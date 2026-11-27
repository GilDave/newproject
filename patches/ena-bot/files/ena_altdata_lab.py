"""다른 정보 원천 시험(읽기 전용): 공포탐욕지수 · 스테이블코인 유동성 · USDe 유통량 · Ethena 예치금을 노출 조절 규칙으로 시험한다.
규칙·판정은 ena_round3_prereg.md 의 B 절에 결과를 보기 전에 고정했다. 같은 폴더의 ena_idea_lab.py · ena_macro_study.py 를 쓴다.
모두 공개 일 단위 데이터(키 불필요)이며 curl 로 받는다. 일 단위 값은 그 날이 끝난 다음 UTC 00:00 이후에만 쓴다.

  .venv/bin/python ena_altdata_lab.py --check     각 출처가 받아지는지·기간·최근 값만 확인
  .venv/bin/python ena_altdata_lab.py             시험 실행 (--out 결과.json 으로 저장 가능)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ena_idea_lab as lab  # noqa: E402
import ena_macro_study as ms  # noqa: E402
from ena_bot.config import Config  # noqa: E402
from ena_bot.featx import BAR_MS  # noqa: E402
from ena_bot.learn_data import DEFAULT_SYMBOLS, load_coin  # noqa: E402
from ena_bot.trend import target_positions  # noqa: E402

DAY = 86_400_000
HALF = 0.5
ENA_START_MS = 1_712_000_000_000          # 2024-04
IDEAS = {"A1": "공포탐욕 ≤ 20 → 숏 ×0.5", "A2": "공포탐욕 ≥ 80 → 롱 ×0.5", "A3": "스테이블코인 시총 30일 감소 → 전체 ×0.5",
         "A4": "USDe 유통량 30일 감소 → ENA ×0.5 (ENA 전용·참고)", "A5": "Ethena 예치금 30일 감소 → ENA ×0.5 (ENA 전용·참고)"}


# ------------------------------------------------------------------ 수집·파싱 (각 출처는 독립적으로 실패해도 된다)
def parse_fng(text: str):
    arr = json.loads(text)["data"]
    rows = sorted((int(x["timestamp"]) * 1000, float(x["value"])) for x in arr)
    return np.array([r[0] for r in rows], dtype=np.int64), np.array([r[1] for r in rows])


def parse_llama_chart(text: str):
    """stablecoincharts: [{date, totalCirculatingUSD:{peggedUSD}, totalCirculating:{peggedUSD}}]"""
    rows = []
    for x in json.loads(text):
        v = (x.get("totalCirculatingUSD") or x.get("totalCirculating") or {}).get("peggedUSD")
        if v is not None:
            rows.append((int(x["date"]) * 1000, float(v)))
    rows.sort()
    return np.array([r[0] for r in rows], dtype=np.int64), np.array([r[1] for r in rows])


def parse_protocol_tvl(text: str):
    """protocol/<slug>: {tvl:[{date, totalLiquidityUSD}]}"""
    rows = sorted((int(x["date"]) * 1000, float(x["totalLiquidityUSD"])) for x in json.loads(text)["tvl"] if x.get("totalLiquidityUSD") is not None)
    return np.array([r[0] for r in rows], dtype=np.int64), np.array([r[1] for r in rows])


def find_usde_id(text: str) -> str:
    for a in json.loads(text)["peggedAssets"]:
        if str(a.get("symbol", "")).lower() == "usde":
            return str(a["id"])
    raise KeyError("USDe 를 스테이블코인 목록에서 찾지 못했습니다")


def get(url: str, cache: Path | None, name: str, use_cache=True, fetch=None):
    f = cache / f"{name}.json" if cache else None
    if use_cache and f and f.exists() and (__import__("time").time() - f.stat().st_mtime) < 86400:
        return f.read_text(encoding="utf-8")
    text = (fetch or ms.http_get)(url)
    if f:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return text


def fetch_all(cache: Path | None, use_cache=True, fetch=None):
    series, errors = {}, {}
    def one(key, fn):
        try:
            series[key] = fn()
        except Exception as exc:  # noqa: BLE001
            errors[key] = str(exc)[:90]
    one("fng", lambda: parse_fng(get("https://api.alternative.me/fng/?limit=0&format=json", cache, "fng", use_cache, fetch)))
    one("stable_total", lambda: parse_llama_chart(get("https://stablecoins.llama.fi/stablecoincharts/all", cache, "stable_total", use_cache, fetch)))
    def usde():
        uid = find_usde_id(get("https://stablecoins.llama.fi/stablecoins?includePrices=false", cache, "stable_list", use_cache, fetch))
        return parse_llama_chart(get(f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={uid}", cache, "usde", use_cache, fetch))
    one("usde", usde)
    def tvl():
        last = None
        for slug in ("ethena", "ethena-usde"):
            try:
                return parse_protocol_tvl(get(f"https://api.llama.fi/protocol/{slug}", cache, f"tvl_{slug}", use_cache, fetch))
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise last
    one("ethena_tvl", tvl)
    return series, errors


# ------------------------------------------------------------------ 일 단위 값 → 봉 단위 플래그 (미래 정보 금지)
def asof_known(dates_ms: np.ndarray, flag: np.ndarray, ts_q: np.ndarray) -> np.ndarray:
    """날짜 d 의 값은 d+1일 00:00 UTC 부터 알려진 것으로 본다. ts_q 시각에 알려진 마지막 값(없으면 nan)."""
    return lab.asof(dates_ms + DAY, flag, ts_q)


def change_30d(dates_ms: np.ndarray, vals: np.ndarray) -> np.ndarray:
    prev = lab.asof(dates_ms, vals, dates_ms - 30 * DAY)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.isfinite(prev) & (prev > 0), vals / prev - 1, np.nan)


def multipliers(idea: str, series: dict, ts: np.ndarray):
    """(롱 배수, 숏 배수). 데이터가 없는 구간은 1.0."""
    n = len(ts)
    one = np.ones(n)
    if idea in ("A1", "A2"):
        d, v = series["fng"]
        on = np.nan_to_num(asof_known(d, (v <= 20).astype(float) if idea == "A1" else (v >= 80).astype(float), ts + BAR_MS), nan=0.0) > 0
        m = np.where(on, HALF, 1.0)
        return (one, m) if idea == "A1" else (m, one)
    key = {"A3": "stable_total", "A4": "usde", "A5": "ethena_tvl"}[idea]
    d, v = series[key]
    ch = change_30d(d, v)
    flag = np.where(np.isfinite(ch), (ch < 0).astype(float), np.nan)
    on = np.nan_to_num(asof_known(d, flag, ts + BAR_MS), nan=0.0) > 0
    m = np.where(on, HALF, 1.0)
    return m, m


# ------------------------------------------------------------------ 시험
def run(cache_dir="data/learn_cache", alt_cache="data/alt_cache", use_cache=True, log=print, fetch=None) -> dict:
    cfg = Config()
    t = cfg.trend
    bpy = 365 * 24 * 60 / t.trend_interval_min
    cost = cfg.costs.taker_fee + cfg.costs.slippage_bps / 1e4
    fund = cfg.costs.funding_rate_per_8h * (t.trend_interval_min / 480.0)
    pool = {s: load_coin(s, Path(cache_dir)) for s in DEFAULT_SYMBOLS}
    pool = {s: v for s, v in pool.items() if v is not None}
    series, errors = fetch_all(Path(alt_cache), use_cache, fetch)
    for k, e in errors.items():
        log(f"수집 실패 {k}: {e}")
    ideas = {"A1": "fng", "A2": "fng", "A3": "stable_total", "A4": "usde", "A5": "ethena_tvl"}
    avail = [k for k, src in ideas.items() if src in series]
    port = [k for k in avail if k in ("A1", "A2", "A3")]
    nets = {k: {} for k in ["base"] + port}
    ena = {}
    for s, cd in pool.items():
        ts, c = cd.bars[:, 0], cd.bars[:, 4]
        base = target_positions(c, cfg)
        nets["base"][s] = (ts, lab.coin_net(c, base, cost, fund))
        for k in port:
            ml, ms_ = multipliers(k, series, ts)
            pos = np.where(base > 0, base * ml, np.where(base < 0, base * ms_, 0.0))
            nets[k][s] = (ts, lab.coin_net(c, pos, cost, fund))
        if s == "ENAUSDT":
            for k in [x for x in avail if x in ("A4", "A5")]:
                ml, ms_ = multipliers(k, series, ts)
                pos = np.where(base > 0, base * ml, np.where(base < 0, base * ms_, 0.0))
                ena[k] = lab.coin_net(c, pos, cost, fund)
            ena["base"] = lab.coin_net(c, base, cost, fund)
            ena["ts"] = ts
    ports = {k: lab.portfolio(nets[k]) for k in nets}
    allts = ports["base"][0]
    periods = {"full": allts >= (2021.0 - 1970) * lab.YEAR_MS, "hold": allts >= (2025.5 - 1970) * lab.YEAR_MS}
    res = {k: {p: lab.stats(ports[k][1][m], bpy) for p, m in periods.items()} for k in nets}
    chk = {p: (round(res["base"][p]["sharpe"], 2), round(res["base"][p]["cagr"], 1), round(res["base"][p]["mdd"], 1)) for p in periods}
    ok = all(chk[p] == lab.BASE_EXPECT[p] for p in periods)
    out = {"baseline_check": {"got": chk, "expected": lab.BASE_EXPECT, "ok": ok}, "res": res, "verdict": {}, "errors": errors,
           "ranges": {k: [int(v[0][0]), int(v[0][-1]), int(len(v[0]))] for k, v in series.items()}}
    if not ok:
        return out
    for k in port:
        ci = {p: lab.boot(allts, ports["base"][1], ports[k][1], periods[p], bpy) for p in periods}
        out["verdict"][k] = lab.judge(res["base"], res[k], ci)
    if ena:
        m = ena["ts"] >= ENA_START_MS
        out["ena_only"] = {k: lab.stats(ena[k][m], bpy) for k in ("base", "A4", "A5") if k in ena}
        out["ena_only"]["n_bars"] = int(m.sum())
    return out


def render(out: dict) -> str:
    chk = out["baseline_check"]
    L = [f"기준 재현 확인: {chk['got']} / 기존 {chk['expected']} → {'일치' if chk['ok'] else '불일치 — 실험 중단'}"]
    if not chk["ok"]:
        return "\n".join(L)
    for k, e in out["errors"].items():
        L.append(f"  수집 실패(해당 시험 건너뜀) {k}: {e}")
    L.append(f"{'후보':36s} {'전체 샤프':>9s} {'ΔS':>6s} {'ΔMDD':>6s} | {'홀드 샤프':>9s} {'ΔS':>6s} {'ΔMDD':>6s} | 판정")
    b = out["res"]["base"]
    L.append(f"{'기준(현재 전략)':36s} {b['full']['sharpe']:9.2f} {'':6s} {'':6s} | {b['hold']['sharpe']:9.2f}")
    for k, v in out["verdict"].items():
        f, h = out["res"][k]["full"], out["res"][k]["hold"]
        L.append(f"{k + ' ' + IDEAS[k]:36s} {f['sharpe']:9.2f} {v['dSharpe']['full']:+6.2f} {v['dMDD']['full']:+6.1f} | {h['sharpe']:9.2f} {v['dSharpe']['hold']:+6.2f} {v['dMDD']['hold']:+6.1f} | {v['verdict']}")
    if "ena_only" in out:
        e = out["ena_only"]
        L += ["", f"[ENA 단독 참고 — 2024-04 이후 {e['n_bars']}봉, 판정에 쓰지 않음]"]
        for k in ("A4", "A5"):
            if k in e:
                L.append(f"  {k} {IDEAS[k]}: 샤프 {e[k]['sharpe']:.2f} (기준 {e['base']['sharpe']:.2f}) · CAGR {e[k]['cagr']:.1f}% (기준 {e['base']['cagr']:.1f}%) · 낙폭 {e[k]['mdd']:.1f}% (기준 {e['base']['mdd']:.1f}%)")
    keep = [k for k, v in out["verdict"].items() if v["point_pass"]]
    L += ["", "통과한 후보(포트폴리오): " + (", ".join(keep) if keep else "없음 — 모두 폐기"),
          "주의: 시험 3건(A1~A3)은 합격 기준이 idea lab 과 같습니다. ENA 단독(A4·A5)은 표본이 작아 참고로만 봅니다."]
    return "\n".join(L)


def check(alt_cache="data/alt_cache") -> int:
    series, errors = fetch_all(Path(alt_cache), use_cache=False)
    import datetime as dt
    for k, (d, v) in series.items():
        f = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        print(f"[OK] {k:14s} {len(d):5d}개 · {f(d[0])} ~ {f(d[-1])} · 최근 값 {v[-1]:,.4g}")
    for k, e in errors.items():
        print(f"[실패] {k}: {e}")
    return 0 if series else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.check:
        return check()
    out = run(use_cache=not a.no_cache)
    print(render(out))
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0 if out["baseline_check"]["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
