"""추세 전략을 '거래 단위 Win/Lose'로 과거 검증한다(읽기 전용). 대시보드 '승률' 탭과 같은 거래 정의를 쓴다.
18개 코인 1시간봉을 4시간으로 합쳐 현재 봇과 같은 규칙(lookbacks·변동성 목표·비용)으로 돌리고, 포지션이 생겼다가 없어지거나 뒤집힐 때까지를 한 건으로 센다.
'Win 건수가 Lose 건수보다 많은가'와 '거래당 기대값이 플러스인가'를 같이 본다. 실행(ENA 폴더에서):  .venv/bin/python ena_winloss_backtest.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
SPLIT_MS = 1_704_067_200_000        # 2024-01-01 UTC


def run(a: np.ndarray, cfg, win_loss, trend):
    """a: [ts,o,h,l,c,v] 1시간봉. 반환: (거래 목록, 열린 거래, 봉 수)."""
    from ena_bot.data import Candles
    t = cfg.trend
    c = trend.resample_candles(Candles(*(a[:, i] for i in range(6))), t.trend_interval_min // t.base_interval_min, t.base_interval_min)
    close = c.close
    pos = trend._apply_threshold(trend.target_positions(close, cfg), t.rebalance_threshold)
    ret = np.r_[0.0, np.diff(close) / close[:-1]]
    held = np.r_[0.0, pos[:-1]]
    side = cfg.costs.taker_fee + cfg.costs.slippage_bps / 1e4
    fpb = cfg.costs.funding_rate_per_8h * (t.trend_interval_min / 480.0)
    eq, qty, evs = 1.0, 0.0, []
    for i in range(len(close)):
        turn = 0.0
        if i > 0:
            tn = held[i] * eq
            turn = abs(tn - qty * close[i - 1]) / eq
            qty = tn / close[i - 1]
        net = max(held[i] * ret[i] - side * turn - abs(held[i]) * fpb, -0.999)
        eq *= 1 + net
        evs.append({"ts": int(c.ts[i]), "qty": float(held[i]), "equity": eq, "price": float(close[i])})
    done, open_ = win_loss.episodes(evs)
    return done, open_, len(close)


def pool(trades):
    from ena_bot import win_loss
    s = win_loss.summarize([{"pnl": x["pct"]} for x in trades])        # 거래별 수익률(%)로 합산해 코인 간 크기를 맞춘다
    return s


def render(rows, pooled, ena, label):
    f = lambda v, d=2: "—" if v is None else f"{v:.{d}f}"
    out = [f"## {label}", "", "| 구분 | 거래 | Win | Lose | 승률 (95%) | 이길 때 평균% | 질 때 평균% | 손익비 | 본전 승률 | 거래당 기대값% | Win>Lose |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in [("18코인 합산", pooled), ("ENA 단독", ena)] + rows:
        ci = f"{s['win_ci95'][0]:.0f}~{s['win_ci95'][1]:.0f}" if s["win_ci95"] else "—"
        out.append(f"| {name} | {s['n']} | {s['wins']} | {s['losses']} | {f(s['win_rate'], 1)}% ({ci}) | {f(s['avg_win'])} | {f(s['avg_loss'])} | {f(s['payoff'])} | {f(s['breakeven_win_rate'], 1)}% | {f(s['expectancy'], 3)} | {'예' if s['wins'] > s['losses'] else '아니오'} |")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="data/learn_cache")
    ap.add_argument("--per-coin", action="store_true")
    args = ap.parse_args(argv)
    from ena_bot import trend, win_loss
    from ena_bot.config import Config
    import ena_pattern_study as p1
    cfg = Config()
    data = p1.load(Path(args.cache))
    allt, per = [], {}
    for sym, a in data.items():
        done, _, _ = run(a, cfg, win_loss, trend)
        per[sym] = done
        allt += [dict(t, sym=sym) for t in done]
    parts = {"전체 기간": allt, "2024 이전": [t for t in allt if t["close_ts"] < SPLIT_MS], "2024 이후": [t for t in allt if t["close_ts"] >= SPLIT_MS]}
    for label, ts in parts.items():
        ena = [t for t in ts if t["sym"] == "ENAUSDT"]
        rows = [(s, pool([t for t in ts if t["sym"] == s])) for s in sorted(per)] if args.per_coin else []
        print(render(rows, pool(ts), pool(ena), label), "\n")
    hold = [np.mean([t["bars"] for t in allt])] if allt else [0]
    print(f"평균 보유 {hold[0]:.0f}봉(4시간봉 기준) · 코인 {len(per)}개 · 거래 합계 {len(allt)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
