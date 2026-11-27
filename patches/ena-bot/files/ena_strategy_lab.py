"""전략 10개를 같은 조건으로 비교한다(읽기 전용). 정의·합격 기준은 ena_strategy_lab_prereg.md 에 결과 전에 고정했다.
실행(ENA 폴더에서):  .venv/bin/python ena_strategy_lab.py [--out 결과.md]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
SPLIT_MS = 1_704_067_200_000
H4 = 4 * 3_600_000
N_TESTS = 9


# ------------------------------------------------------------------ 지표
def sma(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def ema(x, n):
    out = np.empty(len(x)); k = 2 / (n + 1); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + k * (x[i] - out[i - 1])
    return out


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0]); up, dn = np.maximum(d, 0), np.maximum(-d, 0)
    out = np.full(len(c), np.nan)
    au, ad = up[1:n + 1].mean(), dn[1:n + 1].mean()
    for i in range(n, len(c)):
        if i > n:
            au, ad = (au * (n - 1) + up[i]) / n, (ad * (n - 1) + dn[i]) / n
        out[i] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def rolling_std(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = x[i - n + 1:i + 1].std()
    return out


# ------------------------------------------------------------------ 전략: (종가, 고가, 저가) -> 방향 {-1,0,1} (크기는 공통 변동성 목표로 나중에 곱한다)
def d_trend(c, h, l, cfg, **_):
    sig = np.zeros(len(c))
    for lag in cfg.trend.lookbacks:
        r = np.full(len(c), np.nan); r[lag:] = c[lag:] / c[:-lag] - 1
        sig += np.sign(np.nan_to_num(r))
    return sig / len(cfg.trend.lookbacks)


def d_long_only(c, h, l, cfg, **_):
    return np.maximum(d_trend(c, h, l, cfg), 0.0)


def d_donchian(c, h, l, cfg, **_):
    pos, cur = np.zeros(len(c)), 0
    for i in range(120, len(c)):
        hi, lo = h[i - 120:i].max(), l[i - 120:i].min()
        ex_lo, ex_hi = l[i - 60:i].min(), h[i - 60:i].max()
        if cur == 0:
            cur = 1 if c[i] > hi else (-1 if c[i] < lo else 0)
        elif cur == 1 and c[i] < ex_lo:
            cur = -1 if c[i] < lo else 0
        elif cur == -1 and c[i] > ex_hi:
            cur = 1 if c[i] > hi else 0
        pos[i] = cur
    return pos


def d_ema_cross(c, h, l, cfg, **_):
    a, b = ema(c, 50), ema(c, 200)
    out = np.sign(a - b); out[:200] = 0
    return out


def _mr(c, entry, exit_, maxhold=30, shorts=False):
    pos, cur, held = np.zeros(len(c)), 0, 0
    for i in range(len(c)):
        if cur == 0:
            cur = entry(i)
            held = 0
        else:
            held += 1
            if exit_(i, cur) or held >= maxhold:
                cur = 0
        pos[i] = cur
    return pos


def d_rsi(c, h, l, cfg, **_):
    r = rsi(c)
    return _mr(c, lambda i: 1 if r[i] < 30 else 0, lambda i, s: r[i] > 50)


def d_boll(c, h, l, cfg, **_):
    m, s = sma(c, 20), rolling_std(c, 20)
    z = (c - m) / np.where(s > 0, s, np.nan)
    return _mr(c, lambda i: 1 if z[i] < -2 else (-1 if z[i] > 2 else 0), lambda i, side: (z[i] > 0 if side == 1 else z[i] < 0), shorts=True)


def d_dip(c, h, l, cfg, **_):
    r, m = rsi(c), sma(c, 200)
    return _mr(c, lambda i: 1 if (c[i] > m[i] and r[i] < 35) else 0, lambda i, s: r[i] > 55)


def d_trend_tp(c, h, l, cfg, **_):
    """S1 의 방향 전환 때 진입, 진입가 대비 +4% 익절 / -4% 손절(종가 기준). 청산 뒤에는 방향이 바뀔 때까지 재진입하지 않는다."""
    base = np.sign(d_trend(c, h, l, cfg))
    pos, cur, entry, block = np.zeros(len(c)), 0, 0.0, 0
    for i in range(len(c)):
        s = int(base[i])
        if cur == 0:
            if s != 0 and s != block:
                cur, entry = s, c[i]
        else:
            gain = cur * (c[i] / entry - 1)
            if gain >= 0.04 or gain <= -0.04 or s != cur:
                block, cur = cur, 0
                if s != 0 and s != block:                  # 신호가 뒤집혀서 청산된 경우: 바로 반대 방향으로 진입
                    cur, entry = s, c[i]
        pos[i] = cur
    return pos


def btc_mult(ts, btc):
    """BTC 일봉 종가가 200일선 아래면 0.5. 봉 종료 시각까지 '완성된' 일봉만 쓴다."""
    day = 86_400_000
    d_ts = (btc[:, 0] // day * day).astype(np.int64)
    days = np.unique(d_ts)
    dclose = np.array([btc[d_ts == d][-1, 4] for d in days])
    ma = sma(dclose, 200)
    out = np.ones(len(ts))
    for i, t in enumerate(ts):
        k = np.searchsorted(days + day, t + H4, side="right") - 1        # 봉 종료 시각까지 끝난 마지막 일봉
        if k >= 199 and np.isfinite(ma[k]) and dclose[k] < ma[k]:
            out[i] = 0.5
    return out


STRATS = {
    "S0": ("기준선: 단순 보유", None), "S1": ("추세(현재 봇)", d_trend), "S2": ("추세 롱 전용", d_long_only), "S3": ("추세 + BTC 200일선 필터", d_trend),
    "S4": ("채널 돌파(120/60봉)", d_donchian), "S5": ("EMA 50/200 교차", d_ema_cross), "S6": ("RSI 과매도 반등(롱)", d_rsi),
    "S7": ("볼린저 평균회귀", d_boll), "S8": ("추세 진입 + 4% 익절·손절", d_trend_tp), "S9": ("상승장 눌림매수(RSI)", d_dip),
}


# ------------------------------------------------------------------ 시뮬레이션
def simulate(ts, close, pos, cfg):
    """pos: 봉 종가에 정한 노출(자본 배수). 다음 봉부터 적용. 반환: (봉 수익률, 이벤트 목록)."""
    held = np.r_[0.0, pos[:-1]]
    ret = np.r_[0.0, np.diff(close) / close[:-1]]
    side = cfg.costs.taker_fee + cfg.costs.slippage_bps / 1e4
    fpb = cfg.costs.funding_rate_per_8h * (cfg.trend.trend_interval_min / 480.0)
    eq, qty, net_all, evs = 1.0, 0.0, np.zeros(len(close)), []
    for i in range(len(close)):
        turn = 0.0
        if i > 0:
            tn = held[i] * eq
            turn = abs(tn - qty * close[i - 1]) / eq
            qty = tn / close[i - 1]
        net = max(held[i] * ret[i] - side * turn - abs(held[i]) * fpb, -0.999)
        eq *= 1 + net
        net_all[i] = net
        evs.append({"ts": int(ts[i]), "qty": float(held[i]), "equity": eq, "price": float(close[i])})
    return net_all, evs


def sized(direction, close, cfg, mult=None):
    t = cfg.trend
    ret = np.r_[0.0, np.diff(close) / close[:-1]]
    vol = rolling_std(ret, t.vol_window)
    bpy = 365 * 24 * 60 / t.trend_interval_min
    scale = np.where(np.isfinite(vol) & (vol > 0), np.clip(t.target_annual_vol / np.sqrt(bpy) / np.where(vol > 0, vol, np.nan), 0, t.max_exposure), 0.0)
    pos = np.clip(direction * np.nan_to_num(scale), -t.max_exposure, t.max_exposure)
    return pos * mult if mult is not None else pos


def boot_ci(x, level=1 - 0.05 / N_TESTS, iters=2000, seed=0):
    x = np.asarray(x, float)
    if len(x) < 10:
        return None
    rng = np.random.default_rng(seed)
    m = np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(iters)])
    a = (1 - level) / 2 * 100
    return float(np.percentile(m, a)), float(np.percentile(m, 100 - a))


def port_stats(series: dict, lo_ms, hi_ms):
    """코인별 (ts, net) 를 시각 기준으로 단순 평균한 포트폴리오의 Sharpe·CAGR·MDD."""
    acc: dict = {}
    for ts, net in series.values():
        for t, r in zip(ts, net):
            if lo_ms <= t < hi_ms:
                acc.setdefault(int(t), []).append(r)
    if len(acc) < 100:
        return None
    r = np.array([np.mean(acc[t]) for t in sorted(acc)])
    bpy = 365 * 6
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(eq)
    yrs = len(r) / bpy
    return {"sharpe": float(r.mean() / r.std() * np.sqrt(bpy)) if r.std() > 0 else 0.0, "cagr": float((eq[-1] ** (1 / yrs) - 1) * 100), "mdd": float(((peak - eq) / peak).max() * 100)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="data/learn_cache")
    ap.add_argument("--out")
    ap.add_argument("--coins", type=int, default=0, help="시험용: 앞의 N개 코인만")
    args = ap.parse_args(argv)
    from ena_bot import trend, win_loss
    from ena_bot.config import Config
    from ena_bot.data import Candles
    import ena_pattern_study as p1
    cfg = Config()
    data = p1.load(Path(args.cache))
    syms = sorted(data)[:args.coins] if args.coins else sorted(data)
    btc = data.get("BTCUSDT")
    t = cfg.trend
    res = {k: {"trades": [], "series": {}} for k in STRATS}
    for sym in syms:
        a = data[sym]
        c = trend.resample_candles(Candles(*(a[:, i] for i in range(6))), t.trend_interval_min // t.base_interval_min, t.base_interval_min)
        ts, close, hi, lo = c.ts, c.close, c.high, c.low
        bm = btc_mult(ts, btc) if btc is not None else None
        for k, (_, fn) in STRATS.items():
            if k == "S0":
                pos = np.ones(len(close)); pos[:t.vol_window] = 0.0
            else:
                direction = fn(close, hi, lo, cfg)
                pos = sized(direction, close, cfg, bm if k == "S3" else None)
            net, evs = simulate(ts, close, pos, cfg)
            done, _ = win_loss.episodes(evs)
            res[k]["trades"] += [dict(x, sym=sym) for x in done]
            res[k]["series"][sym] = (ts, net)
    out = ["# 전략 비교 결과 (사전 등록: ena_strategy_lab_prereg.md)", "", f"코인 {len(syms)}개 · 4시간봉 · 판정은 2024 이후 기준", ""]
    verdict = {}
    base_post = port_stats(res["S0"]["series"], SPLIT_MS, 9e18)
    hdr = "| ID | 전략 | 기간 | 거래 | 승률 | Win>Lose | 이길 때% | 질 때% | 손익비 | 거래당 기대값% (99.4% 구간) | 포트 Sharpe | CAGR% | MDD% |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    out.append(hdr)
    f = lambda v, d=2: "—" if v is None else f"{v:.{d}f}"
    for k, (name, _) in STRATS.items():
        exp = {}
        for lab, lo_, hi_ in (("2024 이전", 0, SPLIT_MS), ("2024 이후", SPLIT_MS, 9e18)):
            tr = [x for x in res[k]["trades"] if lo_ <= x["close_ts"] < hi_]
            s = win_loss.summarize([{"pnl": x["pct"]} for x in tr])
            ci = boot_ci([x["pct"] for x in tr]) if tr else None
            ps = port_stats(res[k]["series"], lo_, hi_)
            exp[lab] = (s, ci, ps)
            cis = f"{f(s['expectancy'], 3)} ({f(ci[0], 2)}~{f(ci[1], 2)})" if ci else (f(s["expectancy"], 3) if s["expectancy"] is not None else "—")
            out.append(f"| {k} | {name} | {lab} | {s['n']} | {f(s['win_rate'], 1)}% | {'예' if s['wins'] > s['losses'] else '아니오'} | {f(s['avg_win'])} | {f(s['avg_loss'])} | {f(s['payoff'])} | {cis} | {f(ps and ps['sharpe'])} | {f(ps and ps['cagr'], 1)} | {f(ps and ps['mdd'], 1)} |")
        post, pre = exp["2024 이후"], exp["2024 이전"]
        if k == "S0":
            verdict[k] = "기준선"
        else:
            c1 = bool(post[2] and base_post and post[2]["sharpe"] > base_post["sharpe"])
            c2 = bool(post[1] and post[1][0] > 0)
            c3 = bool(pre[0]["expectancy"] is not None and pre[0]["expectancy"] > 0)
            verdict[k] = ("합격" if c1 and c2 and c3 else "불합격") + f" (Sharpe>보유 {'O' if c1 else 'X'} · 기대값 구간 하한>0 {'O' if c2 else 'X'} · 2024 이전 기대값>0 {'O' if c3 else 'X'})"
    out += ["", "## 판정 (사전 합격 기준)", ""] + [f"- **{k} {STRATS[k][0]}**: {v}" for k, v in verdict.items()]
    ena = [x for x in res["S1"]["trades"] if x["sym"] == "ENAUSDT"]
    out += ["", f"참고: ENA 는 2024 이후 구간만 있다(S1 거래 {len(ena)}건). 코인 간 상관 때문에 풀링 거래 구간은 실제보다 좁게 나온다."]
    text = "\n".join(out)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
