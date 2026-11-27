"""아이디어 실험실: 현재 추세 전략에 덧씌우는 노출 조절 규칙을 같은 기준으로 시험한다(읽기 전용, 매매·설정·DB 는 건드리지 않는다).
후보와 판정 기준은 ena_idea_lab_prereg.md 에 결과를 보기 전에 고정했다. 계산은 data/research/rules_experiment.py 와 같다(18코인 동일가중, 비용 포함).

실행(ENA 폴더에서):  .venv/bin/python ena_idea_lab.py
  --no-macro    거시 지표(FRED) 후보를 건너뛴다.   --out 결과.json   결과를 파일로도 저장
거시 후보(M1~M4)는 같은 폴더의 ena_macro_study.py 로 FRED 를 받는다(없으면 건너뜀).
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
from ena_bot.config import Config  # noqa: E402
from ena_bot.featx import BAR_MS  # noqa: E402
from ena_bot.learn_data import DEFAULT_SYMBOLS, load_coin  # noqa: E402
from ena_bot.trend import target_positions  # noqa: E402

YEAR_MS = 365.2425 * 86_400_000
BASE_EXPECT = {"full": (0.67, 16.0, 25.4), "hold": (0.72, 18.3, 25.4)}  # 기존 결과(rules_experiment_result.md)
HALF = 0.5
CRYPTO = {"C1": "BTC 레짐(전체 ×0.5)", "C2": "BTC 레짐(롱만 ×0.5)", "C3": "변동성 급등", "C4": "펀딩 혼잡", "C5": "롱숏비 혼잡",
          "C6": "미결제약정 급증", "C7": "프리미엄 혼잡", "C8": "분할 익절", "C9": "트레일링"}
MACRO = {"M1": "S&P500 < 200일선", "M2": "나스닥 < 200일선", "M3": "VIX > 25", "M4": "VIX 5일 +30%"}


# ------------------------------------------------------------------ 보조
def asof(ts_src: np.ndarray, val_src: np.ndarray, ts_q: np.ndarray) -> np.ndarray:
    """ts_q 시각 이전(포함)에 알려진 마지막 값. 없으면 nan."""
    if len(ts_src) == 0:
        return np.full(len(ts_q), np.nan)
    i = np.searchsorted(ts_src, ts_q, side="right") - 1
    return np.where(i >= 0, val_src[np.clip(i, 0, None)], np.nan)


def rolling_rank(x: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """각 시점 값이 '직전 window 개 값' 중 몇 분위인지(0~1). 직전 값이 min_periods 미만이면 nan. 현재 값은 포함하지 않는다."""
    out = np.full(len(x), np.nan)
    win: list[float] = []
    q: list[float] = []
    for i, v in enumerate(x):
        if np.isfinite(v) and len(win) >= min_periods:
            out[i] = bisect.bisect_left(win, v) / len(win)
        if np.isfinite(v):
            bisect.insort(win, v)
            q.append(v)
            if len(q) > window:
                old = q.pop(0)
                del win[bisect.bisect_left(win, old)]
    return out


def vol_series(c: np.ndarray, n: int) -> np.ndarray:
    ret = np.r_[0.0, np.diff(c) / c[:-1]]
    out = np.full(len(c), np.nan)
    for i in range(n - 1, len(c)):
        out[i] = ret[i - n + 1 : i + 1].std()
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1 :] = (cs[n:] - cs[:-n]) / n
    return out


def funding_asof(cd, ts: np.ndarray) -> np.ndarray:
    f = cd.funding
    return asof(f[:, 0], f[:, 1], ts + BAR_MS) if len(f) else np.full(len(ts), np.nan)   # 봉 종가 시점까지 정산된 마지막 값


# ------------------------------------------------------------------ 후보별 (롱 배수, 숏 배수) 계산
def overlays_for(sym, cd, base, cfg, ctx):
    """{후보: (롱 배수, 숏 배수)}. 정보가 없는 구간은 1.0(변경 없음)."""
    e = cd.bars
    ts, c = e[:, 0], e[:, 4]
    n = len(c)
    one = np.ones(n)
    out = {}
    btc_flag = asof(ctx["btc_ts"], ctx["btc_below"], ts)          # 같은 4시간 봉의 종가까지 반영된 값(결정 시점에 이미 알려짐)
    f = np.nan_to_num(btc_flag, nan=0.0) > 0
    out["C1"] = (np.where(f, HALF, 1.0), np.where(f, HALF, 1.0))
    out["C2"] = (np.where(f, HALF, 1.0), one)
    vol30 = vol_series(c, 180)                                      # 30일 = 180봉
    pct = rolling_rank(vol30, 2190, 360)
    hot = np.nan_to_num(pct, nan=0.0) >= 0.90
    out["C3"] = (np.where(hot, HALF, 1.0), np.where(hot, HALF, 1.0))
    fr = funding_asof(cd, ts) * 3 * 365                             # 8시간 펀딩률 → 연환산
    out["C4"] = (np.where(np.nan_to_num(fr, nan=0.0) > 0.30, HALF, 1.0), np.where(np.nan_to_num(fr, nan=0.0) < -0.30, HALF, 1.0))
    ls = asof(cd.ls[:, 0], cd.ls[:, 1], ts) if len(cd.ls) else np.full(n, np.nan)   # 한 봉 늦춰 쓴다(봉 시작 시점 기준)
    p = rolling_rank(ls, 1080, 360)
    out["C5"] = (np.where(np.nan_to_num(p, nan=0.5) >= 0.90, HALF, 1.0), np.where(np.nan_to_num(p, nan=0.5) <= 0.10, HALF, 1.0))
    oi = asof(cd.oi[:, 0], cd.oi[:, 1], ts) if len(cd.oi) else np.full(n, np.nan)
    chg = np.full(n, np.nan)
    chg[6:] = oi[6:] / np.where(oi[:-6] > 0, oi[:-6], np.nan) - 1
    p = rolling_rank(chg, 1080, 360)
    sp = np.nan_to_num(p, nan=0.0) >= 0.95
    out["C6"] = (np.where(sp, HALF, 1.0), np.where(sp, HALF, 1.0))
    pr = asof(cd.prem[:, 0], cd.prem[:, 1], ts) if len(cd.prem) else np.full(n, np.nan)   # 시작 ts 기준 → 이미 마감된 봉
    p = rolling_rank(pr, 1080, 360)
    out["C7"] = (np.where(np.nan_to_num(p, nan=0.5) >= 0.90, HALF, 1.0), np.where(np.nan_to_num(p, nan=0.5) <= 0.10, HALF, 1.0))
    vol = vol_series(c, cfg.trend.vol_window)
    m8, cut = np.ones(n), False
    for i in range(12, n):
        s = np.sign(base[i])
        if s == 0 or not np.isfinite(vol[i]) or vol[i] == 0:
            cut = False
            continue
        z = s * (c[i] / c[i - 12] - 1) / (vol[i] * np.sqrt(12))
        if not cut and z >= 3.0:
            cut = True
        elif cut and z <= 0:
            cut = False
        m8[i] = HALF if cut else 1.0
    out["C8"] = (m8, m8)
    m9, side, ext, flat_until = np.ones(n), 0, 0.0, -1
    for i in range(n):
        s = int(np.sign(base[i]))
        if s != side:
            side, ext, flat_until = s, c[i], -1
        if side == 0 or not np.isfinite(vol[i]):
            continue
        ext = max(ext, c[i]) if side > 0 else min(ext, c[i])
        if i < flat_until:
            m9[i] = 0.0
            continue
        if side * (ext - c[i]) / ext >= 3.0 * vol[i] * np.sqrt(6):
            flat_until, m9[i], ext = i + 18, 0.0, c[i]
    out["C9"] = (m9, m9)
    for k, flag in ctx["macro"].items():                            # 거시: 이미 마감한 미국장 종가 기준 플래그
        v = asof(ctx["macro_close_ms"], flag, ts + BAR_MS)
        m = np.where(np.nan_to_num(v, nan=0.0) > 0, HALF, 1.0)
        out[k] = (m, m)
    return out


# ------------------------------------------------------------------ 포트폴리오 · 통계 (rules_experiment.py 와 같은 계산)
def coin_net(c, pos, cost, fund_const):
    r = np.r_[0.0, np.diff(c) / c[:-1]]
    held = np.r_[0.0, pos[:-1]]
    prev = np.r_[0.0, held[:-1]]
    return held * r - cost * np.abs(held - prev) - np.abs(held) * fund_const


def portfolio(series: dict):
    allts = np.array(sorted(set(np.concatenate([v[0] for v in series.values()]))))
    where = {int(x): i for i, x in enumerate(allts)}
    M = np.full((len(series), len(allts)), np.nan)
    for k, (tt, nn) in enumerate(series.values()):
        M[k, [where[int(x)] for x in tt]] = nn
    return allts, np.nan_to_num(np.nanmean(M, 0))


def stats(x, bpy):
    eq = np.cumprod(1 + x)
    return {"sharpe": float(x.mean() / x.std() * np.sqrt(bpy)), "cagr": float((eq[-1] ** (bpy / len(x)) - 1) * 100),
            "mdd": float((1 - eq / np.maximum.accumulate(eq)).max() * 100)}


def boot(allts, a, b, m, bpy, iters=1000):
    wk = (allts[m] // (7 * 86_400_000)).astype(int)
    u = np.unique(wk)
    idx = [np.where(wk == w)[0] for w in u]
    rng = np.random.default_rng(0)
    A, B, d = a[m], b[m], []
    for _ in range(iters):
        sel = np.concatenate([idx[i] for i in rng.integers(0, len(u), len(u))])
        d.append(B[sel].mean() / B[sel].std() * np.sqrt(bpy) - A[sel].mean() / A[sel].std() * np.sqrt(bpy))
    return [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


def judge(base_res, res, ci):
    dS = {p: res[p]["sharpe"] - base_res[p]["sharpe"] for p in res}
    dM = {p: res[p]["mdd"] - base_res[p]["mdd"] for p in res}
    point = all(v >= 0.10 for v in dS.values()) and all(v <= 2.0 for v in dM.values())
    strict = bool(point and all(v[0] >= 0 for v in ci.values()))
    return {"dSharpe": dS, "dMDD": dM, "ci": ci, "point_pass": bool(point), "strict": strict,
            "verdict": "엄격 통과(그림자 전진검증 대상)" if strict else "점추정만 통과(보류)" if point else "폐기"}


def macro_flags(cache: Path):
    """FRED 일 데이터 → 플래그(1=위험 회피 조건)와 각 값이 알려지는 시각(그 날 미국장 종가, ms)."""
    import importlib.util
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("ena_macro_study", here / "ena_macro_study.py")
    if not (here / "ena_macro_study.py").exists():
        raise FileNotFoundError("ena_macro_study.py 가 같은 폴더에 없습니다")
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)
    data = {sid: ms.fetch_fred(sid, cache, start="2019-01-01") for sid in ("SP500", "NASDAQCOM", "VIXCLS")}
    # 세 계열의 날짜 합집합(휴장일 차이)에서 SP500 날짜를 기준으로 맞춘다
    dates = [d for d, _ in data["SP500"]]
    def on(sid):
        m = dict(data[sid]); last, out = np.nan, []
        for d in dates:
            last = m.get(d, last); out.append(last)
        return np.array(out, dtype=float)
    sp, nq, vx = on("SP500"), on("NASDAQCOM"), on("VIXCLS")
    ok = np.isfinite(sp) & np.isfinite(nq) & np.isfinite(vx)
    close_ms = np.array([ms.us_close_ms(d) for d in dates], dtype=np.int64)[ok]
    sp, nq, vx = sp[ok], nq[ok], vx[ok]
    vx5 = np.full(len(vx), np.nan); vx5[5:] = vx[5:] / vx[:-5] - 1
    flags = {"M1": (sp < sma(sp, 200)).astype(float), "M2": (nq < sma(nq, 200)).astype(float), "M3": (vx > 25).astype(float),
             "M4": (vx5 > 0.30).astype(float)}
    for k in ("M1", "M2"):
        flags[k][~np.isfinite(sma(sp if k == "M1" else nq, 200))] = 0.0   # 이동평균을 못 구하는 초기 구간은 무조건 0
    return flags, close_ms


def run(use_macro: bool = True, cache_dir: str = "data/learn_cache", macro_cache: str = "data/macro_cache", log=print) -> dict:
    cfg = Config()
    t = cfg.trend
    bpy = 365 * 24 * 60 / t.trend_interval_min
    cost = cfg.costs.taker_fee + cfg.costs.slippage_bps / 1e4
    fund_const = cfg.costs.funding_rate_per_8h * (t.trend_interval_min / 480.0)
    cache = Path(cache_dir)
    pool = {s: load_coin(s, cache) for s in DEFAULT_SYMBOLS}
    pool = {s: v for s, v in pool.items() if v is not None}
    log(f"코인 {len(pool)}개 로드")
    btc = pool["BTCUSDT"].bars
    ctx = {"btc_ts": btc[:, 0], "btc_below": (btc[:, 4] < sma(btc[:, 4], 1200)).astype(float), "macro": {}, "macro_close_ms": np.zeros(0, dtype=np.int64)}
    ctx["btc_below"][~np.isfinite(sma(btc[:, 4], 1200))] = 0.0
    skipped = {}
    if use_macro:
        try:
            ctx["macro"], ctx["macro_close_ms"] = macro_flags(Path(macro_cache))
            log("거시 지표 로드 완료")
        except Exception as exc:  # noqa: BLE001
            skipped = {k: f"거시 데이터를 받지 못했습니다: {str(exc)[:80]}" for k in MACRO}
            log("거시 후보 건너뜀:", exc)
    else:
        skipped = {k: "--no-macro" for k in MACRO}
    names = ["base"] + list(CRYPTO) + [k for k in MACRO if k not in skipped]
    series = {k: {} for k in names}
    for s, cd in pool.items():
        e = cd.bars
        ts, c = e[:, 0], e[:, 4]
        base = target_positions(c, cfg)
        ov = overlays_for(s, cd, base, cfg, ctx)
        series["base"][s] = (ts, coin_net(c, base, cost, fund_const))
        for k in names[1:]:
            ml, ms_ = ov[k]
            pos = np.where(base > 0, base * ml, np.where(base < 0, base * ms_, 0.0))
            series[k][s] = (ts, coin_net(c, pos, cost, fund_const))
    ports = {k: portfolio(series[k]) for k in names}
    allts = ports["base"][0]
    periods = {"full": allts >= (2021.0 - 1970) * YEAR_MS, "hold": allts >= (2025.5 - 1970) * YEAR_MS}
    res = {k: {p: stats(ports[k][1][m], bpy) for p, m in periods.items()} for k in names}
    # 기준이 기존 결과와 같은지 확인(다르면 중단)
    chk = {p: (round(res["base"][p]["sharpe"], 2), round(res["base"][p]["cagr"], 1), round(res["base"][p]["mdd"], 1)) for p in periods}
    ok = all(chk[p] == BASE_EXPECT[p] for p in periods)
    out = {"baseline_check": {"got": chk, "expected": BASE_EXPECT, "ok": ok}, "res": res, "verdict": {}, "skipped": skipped, "coins": len(pool)}
    if not ok:
        return out
    for k in names[1:]:
        ci = {p: boot(allts, ports["base"][1], ports[k][1], periods[p], bpy) for p in periods}
        out["verdict"][k] = judge(res["base"], res[k], ci)
    return out


def render(out: dict) -> str:
    chk = out["baseline_check"]
    L = [f"기준 재현 확인: 내 계산 {chk['got']} / 기존 {chk['expected']} → {'일치' if chk['ok'] else '불일치 — 실험 중단'}"]
    if not chk["ok"]:
        return "\n".join(L)
    r = out["res"]
    L.append(f"{'후보':26s} {'전체 샤프':>9s} {'ΔS':>6s} {'ΔMDD':>6s} | {'홀드 샤프':>9s} {'ΔS':>6s} {'ΔMDD':>6s} | 판정")
    b = r["base"]
    L.append(f"{'기준(현재 전략)':26s} {b['full']['sharpe']:9.2f} {'':6s} {'':6s} | {b['hold']['sharpe']:9.2f} {'':6s} {'':6s} |")
    names = {**CRYPTO, **MACRO}
    for k, v in out["verdict"].items():
        f, h = r[k]["full"], r[k]["hold"]
        L.append(f"{k + ' ' + names[k]:26s} {f['sharpe']:9.2f} {v['dSharpe']['full']:+6.2f} {v['dMDD']['full']:+6.1f} | {h['sharpe']:9.2f} {v['dSharpe']['hold']:+6.2f} {v['dMDD']['hold']:+6.1f} | {v['verdict']}")
    for k, why in out["skipped"].items():
        L.append(f"{k + ' ' + names[k]:26s} 건너뜀: {why}")
    keep = [k for k, v in out["verdict"].items() if v["point_pass"]]
    L += ["", "통과한 후보: " + (", ".join(keep) if keep else "없음 — 모두 폐기"),
          "주의: 후보 13개를 동시에 시험했으니 통과한 것이 있어도 우연일 수 있습니다. 그림자 계좌로 전진검증하기 전에는 본 계좌에 연결하지 않습니다."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-macro", action="store_true")
    ap.add_argument("--cache", default="data/learn_cache")
    ap.add_argument("--macro-cache", default="data/macro_cache")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    out = run(not a.no_macro, a.cache, a.macro_cache)
    print(render(out))
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if out["baseline_check"]["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
