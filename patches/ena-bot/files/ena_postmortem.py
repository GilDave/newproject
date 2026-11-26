"""ENA 봇 손실 사후분석 (읽기 전용).

bot.db 의 trend_cycle 이벤트로 왕복 거래를 복원해 통계를 내고,
선택적으로 Ollama 에 "패턴 서술/가설"만 요청한다. 숫자는 전부 코드가 계산한다.
봇 설정/DB 는 수정하지 않는다(sqlite 읽기 전용 열기).

  .venv/bin/python ena_postmortem.py                       # 통계만
  .venv/bin/python ena_postmortem.py --since 11-21 --llm   # + 올라마 해설
  .venv/bin/python ena_postmortem.py --llm --model qwen3:14b
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
import sys
import time
import urllib.request
from collections import defaultdict


def load_cycles(db: str, since_ms: int = 0) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("select ts_ms, data from events where kind='trend_cycle' order by id").fetchall()
    finally:
        con.close()
    out = []
    for ts_ms, data in rows:
        d = json.loads(data)
        if d.get("equity") is None or d.get("price") is None:
            continue
        d["ts_ms"] = int(d.get("ts") or ts_ms)
        out.append(d)
    return [d for d in out if d["ts_ms"] >= since_ms]


def _side(q: float) -> int:
    return 1 if q > 1e-12 else -1 if q < -1e-12 else 0


def round_trips(cycles: list[dict]) -> list[dict]:
    """qty 부호 변화로 왕복 거래(진입→청산 또는 반전)를 복원한다. 손익은 자본 변화(수수료·펀딩 포함)."""
    trades, cur, prev_eq, prev_side = [], None, None, 0
    for c in cycles:
        s = _side(c.get("qty", 0.0))
        eq = float(c["equity"])
        if cur is not None and s != prev_side:
            cur.update(exit_ts=c["ts_ms"], exit_price=c["price"], pnl=eq - cur["eq0"])
            cur["pnl_pct"] = cur["pnl"] / cur["eq0"] * 100 if cur["eq0"] else 0.0
            cur["ret_pct"] = (c["price"] / cur["entry_price"] - 1) * 100 * cur["side"]
            trades.append(cur)
            cur = None
        if cur is None and s != 0:
            cur = {"side": s, "entry_ts": c["ts_ms"], "entry_price": c["price"],
                   "eq0": prev_eq if (prev_eq is not None and prev_side == 0) else eq if prev_eq is None else prev_eq}
            if prev_side != 0:  # 반전: 이전 거래 청산 시점 자본이 기준
                cur["eq0"] = eq
        prev_eq, prev_side = eq, s
    open_trade = cur
    return trades if open_trade is None else trades + [dict(open_trade, open=True, exit_ts=cycles[-1]["ts_ms"],
            exit_price=cycles[-1]["price"], pnl=float(cycles[-1]["equity"]) - open_trade["eq0"],
            pnl_pct=(float(cycles[-1]["equity"]) / open_trade["eq0"] - 1) * 100 if open_trade["eq0"] else 0.0,
            ret_pct=(cycles[-1]["price"] / open_trade["entry_price"] - 1) * 100 * open_trade["side"])]


def _lt(ms: int):
    return time.localtime(ms / 1000)


def fmt(ms: int) -> str:
    return time.strftime("%m-%d %H:%M", _lt(ms))


def _grp(trades: list[dict], key) -> dict:
    g = defaultdict(list)
    for t in trades:
        g[key(t)].append(t)
    return {k: {"n": len(v), "win": sum(x["pnl"] > 0 for x in v), "pnl_pct": round(sum(x["pnl_pct"] for x in v), 2),
                "avg_pct": round(st.mean(x["pnl_pct"] for x in v), 3)} for k, v in sorted(g.items())}


def analyze(cycles: list[dict]) -> dict:
    trades = round_trips(cycles)
    eq = [float(c["equity"]) for c in cycles]
    peak, mdd, mdd_at = eq[0] if eq else 0, 0.0, None
    for c, e in zip(cycles, eq):
        peak = max(peak, e)
        dd = (e / peak - 1) * 100 if peak else 0
        if dd < mdd:
            mdd, mdd_at = dd, c["ts_ms"]
    halted = sum(1 for c in cycles if any(str(w).startswith("halted") for w in (c.get("why") or [])))
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    dur_h = lambda t: (t["exit_ts"] - t["entry_ts"]) / 3.6e6
    out = {
        "cycles": len(cycles), "halted_cycles": halted,
        "period": [fmt(cycles[0]["ts_ms"]), fmt(cycles[-1]["ts_ms"])] if cycles else None,
        "equity_start": round(eq[0], 2) if eq else None, "equity_end": round(eq[-1], 2) if eq else None,
        "max_drawdown_pct": round(mdd, 2), "max_drawdown_at": fmt(mdd_at) if mdd_at else None,
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "avg_win_pct": round(st.mean(t["pnl_pct"] for t in wins), 3) if wins else None,
        "avg_loss_pct": round(st.mean(t["pnl_pct"] for t in losses), 3) if losses else None,
        "median_hold_h": round(st.median(dur_h(t) for t in trades), 2) if trades else None,
        "by_side": _grp(trades, lambda t: "long" if t["side"] > 0 else "short"),
        "by_hold": _grp(trades, lambda t: "<1h" if dur_h(t) < 1 else "1~4h" if dur_h(t) < 4 else ">=4h"),
        "by_hour_block": _grp(trades, lambda t: f"{_lt(t['entry_ts']).tm_hour // 6 * 6:02d}-{_lt(t['entry_ts']).tm_hour // 6 * 6 + 6:02d}시 진입"),
        "by_day": _grp(trades, lambda t: time.strftime("%m-%d", _lt(t["entry_ts"]))),
        "worst": [{"in": fmt(t["entry_ts"]), "out": fmt(t["exit_ts"]), "side": "L" if t["side"] > 0 else "S",
                   "hold_h": round(dur_h(t), 2), "pnl_pct": round(t["pnl_pct"], 2)}
                  for t in sorted(trades, key=lambda x: x["pnl_pct"])[:5]],
    }
    return out


PROMPT = ("당신은 퀀트 트레이딩 리뷰어입니다. 아래는 ENAUSDT 페이퍼 봇의 거래 통계(JSON)입니다. "
          "숫자는 코드가 계산한 사실입니다. 규칙: ①통계에 없는 숫자를 만들지 말 것 ②원인을 단정하지 말고 '가설'로 표시 "
          "③매매 지시, 전략 변경 권고, 가격 예측은 하지 말 것 ④한국어로, 첫 문장은 반드시 표본 크기(거래 건수·기간)와 통계적 신뢰도의 한계 "
          "⑤같은 사실을 다른 이름으로 두 번 세지 말 것(reading_notes 참고) ⑥reading_notes 의 읽기 규칙을 반드시 따를 것 "
          "⑦관찰 3~5개, 가설 2~3개(근거가 통계에 있는 것만), 추가로 확인할 데이터 1~2개만.\n\n")


def ask_ollama(stats: dict, model: str, host: str, timeout: int = 300) -> str:
    body = json.dumps({"model": model, "prompt": PROMPT + json.dumps(stats, ensure_ascii=False, indent=1),
                       "stream": False, "think": False, "options": {"temperature": 0.2, "num_ctx": 8192}}).encode()
    req = urllib.request.Request(host.rstrip("/") + "/api/generate", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["response"].strip()


def render(s: dict) -> str:
    if not s["cycles"]:
        return "분석할 trend_cycle 기록이 없습니다."
    L = [f"기간 {s['period'][0]} ~ {s['period'][1]} · 사이클 {s['cycles']}개 (정지 상태 {s['halted_cycles']}개)",
         f"자본 {s['equity_start']} → {s['equity_end']} · 최대 낙폭 {s['max_drawdown_pct']}% ({s['max_drawdown_at']})",
         f"왕복 거래 {s['trades']}건 · 승 {s['wins']} / 패 {s['losses']} (승률 {s['win_rate_pct']}%)",
         f"평균 이익 {s['avg_win_pct']}% · 평균 손실 {s['avg_loss_pct']}% · 보유 중앙값 {s['median_hold_h']}시간", ""]
    for title, k in (("방향별", "by_side"), ("보유시간별", "by_hold"), ("진입 시간대별", "by_hour_block"), ("진입 날짜별", "by_day")):
        L.append(f"[{title}]  (건수 · 승 · 합계% · 평균%)")
        for name, v in s[k].items():
            L.append(f"  {name:12s} n={v['n']:3d} 승={v['win']:3d} 합계={v['pnl_pct']:+7.2f}% 평균={v['avg_pct']:+.3f}%")
        L.append("")
    L.append("[최악 거래 5건]")
    for w in s["worst"]:
        L.append(f"  {w['in']} → {w['out']} {w['side']} {w['hold_h']}h {w['pnl_pct']:+.2f}%")
    return "\n".join(L)


# ---- v1(3분봉 엔진) 거래: entry / trade_closed 이벤트 ----
def load_v1(db: str, since_ms: int = 0) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("select ts_ms, kind, data from events where kind in ('entry','trade_closed') order by id").fetchall()
    finally:
        con.close()
    out, pend = [], None
    for ts, kind, data in rows:
        d = json.loads(data)
        if kind == "entry":
            pend = dict(d, ts_ms=ts)
        elif pend is not None:
            notional = (pend.get("qty") or 0) * (pend.get("price") or 0)
            stop = pend.get("stop")
            t = {"ts_ms": pend["ts_ms"], "exit_ts": ts, "side": pend.get("side"), "signal": str(pend.get("signal", "")),
                 "bars": d.get("bars") or 0, "pnl": float(d.get("pnl") or 0.0),
                 "ret_pct": float(d.get("pnl") or 0.0) / notional * 100 if notional else 0.0,
                 "stop_pct": abs(pend["price"] - stop) / pend["price"] * 100 if stop and pend.get("price") else None}
            pend = None
            if t["ts_ms"] >= since_ms:
                out.append(t)
    return out


def _rsi(sig: str):
    import re
    m = re.search(r"rsi=([0-9.]+)", sig)
    return float(m.group(1)) if m else None


def _grp_v1(trades, key) -> dict:
    g = defaultdict(list)
    for t in trades:
        g[key(t)].append(t)
    return {k: {"n": len(v), "win": sum(x["pnl"] > 0 for x in v), "pnl": round(sum(x["pnl"] for x in v), 3),
                "avg_ret_pct": round(st.mean(x["ret_pct"] for x in v), 3)} for k, v in sorted(g.items())}


def analyze_v1(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    streak = best = 0
    for t in trades:
        streak = streak + 1 if t["pnl"] <= 0 else 0
        best = max(best, streak)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    def rb(t):
        r = _rsi(t["signal"])
        return "rsi 없음" if r is None else "rsi<40" if r < 40 else "rsi 40~60" if r <= 60 else "rsi>60"
    return {
        "trades": len(trades), "period": [fmt(trades[0]["ts_ms"]), fmt(trades[-1]["exit_ts"])],
        "wins": len(wins), "losses": len(losses), "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(sum(t["pnl"] for t in trades), 3),
        "avg_win_pnl": round(st.mean(t["pnl"] for t in wins), 3) if wins else None,
        "avg_loss_pnl": round(st.mean(t["pnl"] for t in losses), 3) if losses else None,
        "longest_loss_streak": best,
        "breakeven_win_rate_pct": round(abs(st.mean(t["pnl"] for t in losses)) / (st.mean(t["pnl"] for t in wins) + abs(st.mean(t["pnl"] for t in losses))) * 100, 1) if wins and losses else None,
        "reading_notes": [
            "by_bars_held: 손절에 빨리 걸린 거래는 정의상 짧은 구간에 몰린다. '오래 보유하면 좋다'는 인과로 읽지 말 것(생존 편향).",
            "by_side 와 by_signal 은 같은 거래를 다른 이름으로 센 것이다(롱=ema_cross_up, 숏=ema_cross_down). 하나의 발견으로 합칠 것.",
            "한 구간이 전체 거래의 대부분을 차지하면 그 구간의 효과가 아니라 전체 표본의 성격이다.",
            "win_rate_pct 와 breakeven_win_rate_pct 의 차이가 손익을 결정한다. 기간이 짧고 시장 국면이 하나라 방향별 차이는 국면 영향일 수 있다.",
        ],
        "median_stop_pct": round(st.median(t["stop_pct"] for t in trades if t["stop_pct"]), 3) if any(t["stop_pct"] for t in trades) else None,
        "by_side": _grp_v1(trades, lambda t: "롱(Buy)" if t["side"] == "Buy" else "숏(Sell)"),
        "by_signal": _grp_v1(trades, lambda t: t["signal"].split(" ")[0] or "?"),
        "by_rsi": _grp_v1(trades, rb),
        "by_bars_held": _grp_v1(trades, lambda t: "0~2봉" if t["bars"] <= 2 else "3~9봉" if t["bars"] < 10 else "10봉 이상"),
        "by_hour_block": _grp_v1(trades, lambda t: f"{_lt(t['ts_ms']).tm_hour // 6 * 6:02d}시대 진입"),
        "by_day": _grp_v1(trades, lambda t: time.strftime("%m-%d", _lt(t["ts_ms"]))),
        "worst": [{"in": fmt(t["ts_ms"]), "side": t["side"], "signal": t["signal"][:24], "bars": t["bars"], "pnl": round(t["pnl"], 3)}
                  for t in sorted(trades, key=lambda x: x["pnl"])[:5]],
    }


def render_v1(s: dict) -> str:
    if not s.get("trades"):
        return "분석할 entry/trade_closed 기록이 없습니다."
    L = [f"[3분봉 엔진 v1] 기간 {s['period'][0]} ~ {s['period'][1]} · 거래 {s['trades']}건 · 승 {s['wins']} / 패 {s['losses']} (승률 {s['win_rate_pct']}%)",
         f"손익분기 승률 {s['breakeven_win_rate_pct']}% (실제 {s['win_rate_pct']}%)",
         f"손익 합계 {s['total_pnl']} USDT · 평균 이익 {s['avg_win_pnl']} · 평균 손실 {s['avg_loss_pnl']} · 최장 연패 {s['longest_loss_streak']} · 손절폭 중앙값 {s['median_stop_pct']}%", ""]
    for title, k in (("방향별", "by_side"), ("신호별", "by_signal"), ("진입 시 RSI별", "by_rsi"), ("보유 봉수별", "by_bars_held"),
                     ("진입 시간대별", "by_hour_block"), ("날짜별", "by_day")):
        L.append(f"[{title}]  (건수 · 승 · 손익합 · 평균수익률%)")
        for name, v in s[k].items():
            L.append(f"  {name:14s} n={v['n']:3d} 승={v['win']:3d} 손익={v['pnl']:+8.3f} 평균={v['avg_ret_pct']:+.3f}%")
        L.append("")
    L.append("[최악 거래 5건]")
    for w in s["worst"]:
        L.append(f"  {w['in']} {w['side']} {w['signal']} {w['bars']}봉 {w['pnl']:+.3f}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/bot.db")
    ap.add_argument("--since", help="MM-DD 이후만 (현재 연도, 로컬 시간)")
    ap.add_argument("--source", choices=("trend", "v1"), default="trend", help="trend: 4시간 추세 봇 사이클 / v1: 3분봉 엔진 거래")
    ap.add_argument("--llm", action="store_true", help="올라마 해설 추가")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    a = ap.parse_args(argv)
    since = 0
    if a.since:
        since = int(time.mktime(time.strptime(f"{time.localtime().tm_year}-{a.since}", "%Y-%m-%d")) * 1000)
    if a.source == "v1":
        s = analyze_v1(load_v1(a.db, since))
        print(render_v1(s))
        has = bool(s.get("trades"))
    else:
        s = analyze(load_cycles(a.db, since))
        print(render(s))
        has = bool(s["cycles"])
    if a.llm and has:
        print(f"\n[올라마 해설 · {a.model}] (가설일 뿐 사실이 아닙니다)")
        try:
            print(ask_ollama(s, a.model, a.host))
        except Exception as exc:  # noqa: BLE001
            print(f"올라마 호출 실패: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
