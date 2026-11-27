"""온체인 예치금(TVL) 규칙의 교차 코인 재현 시험(읽기 전용). 규칙·판정은 ena_round4_prereg.md 에 결과를 보기 전에 고정했다.
같은 폴더의 ena_altdata_lab.py · ena_idea_lab.py · ena_macro_study.py 를 쓴다. 공개 DefiLlama API(키 불필요)를 curl 로 받는다.

  .venv/bin/python ena_tvl_lab.py --check    체인별로 받아지는지·기간·최근 값만 확인
  .venv/bin/python ena_tvl_lab.py            시험 실행 (--out 결과.json)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ena_altdata_lab as al  # noqa: E402
import ena_idea_lab as lab  # noqa: E402
from ena_bot.config import Config  # noqa: E402
from ena_bot.featx import BAR_MS  # noqa: E402
from ena_bot.learn_data import DEFAULT_SYMBOLS, load_coin  # noqa: E402
from ena_bot.trend import target_positions  # noqa: E402

CHAINS = {"ETHUSDT": "Ethereum", "SOLUSDT": "Solana", "AVAXUSDT": "Avalanche", "BNBUSDT": "BSC", "SUIUSDT": "Sui", "NEARUSDT": "Near",
          "ADAUSDT": "Cardano", "ARBUSDT": "Arbitrum", "OPUSDT": "Optimism", "INJUSDT": "Injective", "DOTUSDT": "Polkadot", "TIAUSDT": "Celestia"}
MIN_BARS = 1000
HALF = 0.5
DAY = al.DAY


def parse_chain_tvl(text: str):
    rows = sorted((int(x["date"]) * 1000, float(x["tvl"])) for x in json.loads(text) if x.get("tvl") is not None)
    return np.array([r[0] for r in rows], dtype=np.int64), np.array([r[1] for r in rows])


def fetch_tvl(cache: Path | None, use_cache=True, fetch=None):
    series, errors = {}, {}
    for sym, chain in CHAINS.items():
        try:
            series[sym] = parse_chain_tvl(al.get(f"https://api.llama.fi/v2/historicalChainTvl/{chain}", cache, f"chain_{chain}", use_cache, fetch))
        except Exception as exc:  # noqa: BLE001
            errors[sym] = f"{chain}: {str(exc)[:70]}"
    try:
        last = None
        for slug in ("ethena", "ethena-usde"):
            try:
                series["ENAUSDT"] = al.parse_protocol_tvl(al.get(f"https://api.llama.fi/protocol/{slug}", cache, f"tvl_{slug}", use_cache, fetch))
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
        else:
            raise last
    except Exception as exc:  # noqa: BLE001
        errors["ENAUSDT"] = f"Ethena: {str(exc)[:70]}"
    return series, errors


def multiplier(series, ts: np.ndarray, window_days: int = 30) -> np.ndarray:
    d, v = series
    prev = lab.asof(d, v, d - window_days * DAY)
    with np.errstate(divide="ignore", invalid="ignore"):
        ch = np.where(np.isfinite(prev) & (prev > 0), v / prev - 1, np.nan)
    flag = np.where(np.isfinite(ch), (ch < 0).astype(float), np.nan)
    on = np.nan_to_num(al.asof_known(d, flag, ts + BAR_MS), nan=0.0) > 0
    return np.where(on, HALF, 1.0)


def run(cache_dir="data/learn_cache", tvl_cache="data/alt_cache", use_cache=True, log=print, fetch=None) -> dict:
    cfg = Config()
    t = cfg.trend
    bpy = 365 * 24 * 60 / t.trend_interval_min
    cost = cfg.costs.taker_fee + cfg.costs.slippage_bps / 1e4
    fund = cfg.costs.funding_rate_per_8h * (t.trend_interval_min / 480.0)
    pool = {s: load_coin(s, Path(cache_dir)) for s in DEFAULT_SYMBOLS}
    pool = {s: v for s, v in pool.items() if v is not None}
    series, errors = fetch_tvl(Path(tvl_cache), use_cache, fetch)
    for k, e in errors.items():
        log(f"수집 실패 {k}: {e}")
    wins = (30, 14, 60)
    nets = {w: {} for w in wins}
    base_nets = {}
    for s, cd in pool.items():
        if s not in series:
            continue
        ts, c = cd.bars[:, 0], cd.bars[:, 4]
        base = target_positions(c, cfg)
        base_nets[s] = (ts, lab.coin_net(c, base, cost, fund))
        for w in wins:
            m = multiplier(series[s], ts, w)
            nets[w][s] = (ts, lab.coin_net(c, base * m, cost, fund))
    used = sorted(base_nets)
    judged = [s for s in used if s != "ENAUSDT"]
    out = {"used": used, "judged": judged, "errors": errors, "per_coin": {}, "windows": {}}
    if len(judged) < 3:
        out["verdict"] = {"verdict": "자료가 있는 코인이 3개 미만이라 판정할 수 없습니다"}
        return out
    def port(selected, nets_):
        return lab.portfolio({s: nets_[s] for s in selected})
    allts, base_p = port(judged, base_nets)
    periods = {"full": allts >= (2021.0 - 1970) * lab.YEAR_MS, "hold": allts >= (2025.5 - 1970) * lab.YEAR_MS}
    base_res = {p: lab.stats(base_p[m], bpy) for p, m in periods.items()}
    for w in wins:
        ov_p = port(judged, nets[w])[1]
        res = {p: lab.stats(ov_p[m], bpy) for p, m in periods.items()}
        out["windows"][w] = {"res": res, "judge": None}
        if w == 30:
            ci = {p: lab.boot(allts, base_p, ov_p, periods[p], bpy) for p in periods}
            out["windows"][w]["judge"] = lab.judge(base_res, res, ci)
    out["base_res"] = base_res
    # 코인별 개선(폭넓음)
    ge = np.array(sorted({int(x) for s in used for x in base_nets[s][0]}))
    better = 0
    for s in used:
        ts, b = base_nets[s]
        _, o = nets[30][s]
        m = (ts >= (2021.0 - 1970) * lab.YEAR_MS)
        if m.sum() < MIN_BARS:
            continue
        sb, so = lab.stats(b[m], bpy)["sharpe"], lab.stats(o[m], bpy)["sharpe"]
        out["per_coin"][s] = {"base": sb, "overlay": so, "d": so - sb, "bars": int(m.sum())}
        if s != "ENAUSDT" and so > sb:
            better += 1
    n_j = len([s for s in judged if s in out["per_coin"]])
    out["breadth"] = {"better": better, "of": n_j, "share": better / n_j if n_j else 0.0}
    j = out["windows"][30]["judge"]
    broad = out["breadth"]["share"] >= 0.60
    ok = bool(j["point_pass"] and broad)
    out["verdict"] = {"point_pass": ok, "strict": bool(ok and j["strict"]),
                      "verdict": "엄격 통과(전진검증 대상)" if ok and j["strict"] else "점추정만 통과(보류)" if ok else "폐기 — ENA 결과는 단일 종목 우연으로 본다"}
    if "ENAUSDT" in used:
        ts_all, base_all = port(used, base_nets)
        _, ov_all = port(used, nets[30])
        out["with_ena"] = {p: {"base": lab.stats(base_all[m], bpy), "overlay": lab.stats(ov_all[m], bpy)} for p, m in
                           {"full": ts_all >= (2021.0 - 1970) * lab.YEAR_MS, "hold": ts_all >= (2025.5 - 1970) * lab.YEAR_MS}.items()}
    return out


def render(out: dict) -> str:
    L = [f"사용 코인 {len(out['used'])}개 (판정 대상 {len(out['judged'])}개, ENA 제외): {', '.join(s.replace('USDT', '') for s in out['used'])}"]
    for k, e in out["errors"].items():
        L.append(f"  수집 실패(제외) {k}: {e}")
    if "base_res" not in out:
        return "\n".join(L + [out["verdict"]["verdict"]])
    b, w30 = out["base_res"], out["windows"][30]
    L.append(f"\n[판정 — ENA 제외 포트폴리오, TVL 30일 감소 → 해당 코인 ×0.5]")
    L.append(f"  {'':10s} {'샤프':>6s} {'ΔS':>6s} {'CAGR%':>7s} {'낙폭%':>7s} {'ΔMDD':>6s} {'95% 하한':>8s}")
    for p, name in (("full", "2021~전체"), ("hold", "홀드아웃")):
        r, j = w30["res"][p], w30["judge"]
        L.append(f"  {name:8s}기준 {b[p]['sharpe']:6.2f} {'':6s} {b[p]['cagr']:7.1f} {b[p]['mdd']:7.1f}")
        L.append(f"  {'':8s}적용 {r['sharpe']:6.2f} {j['dSharpe'][p]:+6.2f} {r['cagr']:7.1f} {r['mdd']:7.1f} {j['dMDD'][p]:+6.1f} {j['ci'][p][0]:+8.2f}")
    bd = out["breadth"]
    L.append(f"  폭넓음: 판정 대상 {bd['of']}개 중 {bd['better']}개 코인에서 개별 샤프 개선 ({bd['share'] * 100:.0f}%, 기준 60%)")
    L.append(f"  → {out['verdict']['verdict']}")
    L.append("\n[코인별 (2021 이후 개별 샤프, 참고)]")
    for s, v in out["per_coin"].items():
        L.append(f"  {s.replace('USDT', ''):6s} 기준 {v['base']:+5.2f} → 적용 {v['overlay']:+5.2f} ({v['d']:+5.2f}){'  ← 동기가 된 ENA(판정 제외)' if s == 'ENAUSDT' else ''}")
    L.append("\n[창 길이 변형 — 탐색, 판정에 쓰지 않음]")
    for w, x in out["windows"].items():
        if w != 30:
            L.append(f"  {w}일: 전체 샤프 {x['res']['full']['sharpe']:.2f} (기준 {b['full']['sharpe']:.2f}) · 홀드 {x['res']['hold']['sharpe']:.2f} (기준 {b['hold']['sharpe']:.2f})")
    if "with_ena" in out:
        e = out["with_ena"]
        L.append(f"\n[ENA 포함 참고] 전체 샤프 {e['full']['base']['sharpe']:.2f} → {e['full']['overlay']['sharpe']:.2f} · 홀드 {e['hold']['base']['sharpe']:.2f} → {e['hold']['overlay']['sharpe']:.2f}")
    return "\n".join(L)


def check(tvl_cache="data/alt_cache") -> int:
    import datetime as dt
    series, errors = fetch_tvl(Path(tvl_cache), use_cache=False)
    f = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    for s, (d, v) in series.items():
        print(f"[OK] {s:9s} {len(d):5d}개 · {f(d[0])} ~ {f(d[-1])} · 최근 {v[-1]:,.4g}")
    for s, e in errors.items():
        print(f"[실패] {s}: {e}")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
