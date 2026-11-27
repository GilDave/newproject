"""자동매매 승·패 성적표(읽기 전용): 기준 계좌와 그림자 계좌(BTC 필터 · ML 게이트)의 거래를 Win / Lose 로 센다.

추세 봇은 목표 노출을 조금씩 조절하므로 '거래'를 이렇게 정의한다: 포지션이 없다가 생겨서 → 없어지거나 롱↔숏으로 뒤집힐 때까지가 한 건.
손익 = 그 구간 동안 늘어난 자본(수수료·슬리피지·펀딩비 포함). 0 초과면 Win, 이하면 Lose. 아직 열려 있는 건은 따로 보여 주고 세지 않는다.
bot.db 의 trend_cycle / trend_shadow / ml_shadow 이벤트와 3분봉 엔진의 trade_closed 이벤트만 읽는다. 매매·설정은 건드리지 않는다.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

KINDS = {"base": ("trend_cycle", "기준 계좌"), "shadow": ("trend_shadow", "BTC 필터 계좌"), "ml": ("ml_shadow", "ML 게이트 계좌")}
MIN_TRADES = 30                   # 이보다 적으면 승률이 우연인지 구분할 수 없다고 알린다
BAR_MS = 4 * 3_600_000


def wilson(w: int, n: int, z: float = 1.96):
    """승률의 95% 신뢰구간(Wilson). n=0 이면 None."""
    if n <= 0:
        return None
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h) * 100, 1), round(min(1.0, c + h) * 100, 1)]


def episodes(events: list[dict]):
    """events: ts 오름차순의 {ts, qty, equity, price}. -> (끝난 거래 목록, 열려 있는 거래 또는 None).
    첫 이벤트에 이미 포지션이 있으면 시작 자본을 알 수 없어 그 건은 세지 않는다."""
    done, cur, prev = [], None, None
    for e in events:
        q, eq = float(e.get("qty") or 0.0), float(e["equity"])
        pq = float(prev.get("qty") or 0.0) if prev else None
        if prev is None:
            cur = None
        elif cur is None and pq == 0 and q != 0:
            cur = {"open_ts": e["ts"], "side": "long" if q > 0 else "short", "start_eq": float(prev["equity"]), "entry": e.get("price")}
        elif cur is not None and (q == 0 or (q > 0) != (pq > 0)):                       # 청산 또는 방향 반전
            pnl = eq - cur["start_eq"]
            done.append({**cur, "close_ts": e["ts"], "exit": e.get("price"), "pnl": pnl, "pct": pnl / cur["start_eq"] * 100 if cur["start_eq"] else 0.0,
                         "bars": int(round((e["ts"] - cur["open_ts"]) / BAR_MS))})
            cur = {"open_ts": e["ts"], "side": "long" if q > 0 else "short", "start_eq": eq, "entry": e.get("price")} if q != 0 else None
        prev = e
    open_ = None
    if cur is not None and prev is not None:
        open_ = {**cur, "pnl": float(prev["equity"]) - cur["start_eq"], "bars": int(round((prev["ts"] - cur["open_ts"]) / BAR_MS))}
        open_["pct"] = open_["pnl"] / cur["start_eq"] * 100 if cur["start_eq"] else 0.0
    return done, open_


def summarize(trades: list[dict]) -> dict:
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    aw = gw / len(wins) if wins else None
    al = gl / len(losses) if losses else None
    payoff = aw / al if aw is not None and al else None
    out = {"n": n, "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / n * 100, 1) if n else None, "win_ci95": wilson(len(wins), n),
           "avg_win": aw, "avg_loss": -al if al is not None else None, "payoff": payoff, "profit_factor": gw / gl if gl else None,
           "expectancy": sum(t["pnl"] for t in trades) / n if n else None, "total_pnl": sum(t["pnl"] for t in trades),
           "breakeven_win_rate": round(100 / (1 + payoff), 1) if payoff else None,
           "enough": n >= MIN_TRADES, "recent": ["W" if t["pnl"] > 0 else "L" for t in trades[-30:]]}
    run, best = 0, 0
    for t in trades:                                                            # 최장 연패
        run = run + 1 if t["pnl"] <= 0 else 0
        best = max(best, run)
    out["max_losing_streak"] = best
    return out


def _load(db: Path):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("select kind, data from events where kind in ('trend_cycle','trend_shadow','ml_shadow','trade_closed') order by id").fetchall()
    finally:
        con.close()
    series: dict = {k: {} for k in KINDS}
    rev = {v[0]: k for k, v in KINDS.items()}
    legacy = []
    for kind, data in rows:
        try:
            d = json.loads(data)
        except ValueError:
            continue
        if kind == "trade_closed":
            if d.get("pnl") is not None:
                legacy.append({"pnl": float(d["pnl"]), "side": d.get("side"), "bars": d.get("bars")})
        elif d.get("ts") is not None and d.get("equity") is not None:
            series[rev[kind]][int(d["ts"])] = d                                 # 같은 봉이 두 번 기록돼도 마지막 값
    return series, legacy


def payload(db: str | Path) -> dict:
    db = Path(db)
    if not db.is_file():
        return {"ok": False, "error": "bot.db 없음"}
    try:
        series, legacy = _load(db)
    except Exception as exc:  # noqa: BLE001 - 잠긴 DB 등으로 화면이 깨지면 안 된다
        return {"ok": False, "error": str(exc)[:200]}
    accounts = []
    for k, (_, label) in KINDS.items():
        if not series[k]:
            continue
        evs = [series[k][t] for t in sorted(series[k])]
        done, open_ = episodes(evs)
        accounts.append({"key": k, "label": label, "since": evs[0]["ts"], "cycles": len(evs), **summarize(done), "open": open_,
                         "trades": [{"open_ts": t["open_ts"], "close_ts": t["close_ts"], "side": t["side"], "pct": round(t["pct"], 2), "pnl": round(t["pnl"], 4), "bars": t["bars"]} for t in done[-20:]]})
    old = {"label": "3분봉 엔진(과거 기록)", **summarize([{"pnl": x["pnl"]} for x in legacy])} if legacy else None
    return {"ok": True, "accounts": accounts, "legacy": old, "min_trades": MIN_TRADES,
            "notes": ["Win = 한 번의 거래(포지션이 생겼다가 없어지거나 뒤집힐 때까지)가 비용을 뺀 뒤 이익으로 끝난 것, Lose = 손실 또는 본전.",
                      "추세추종은 승률이 40~50%대여도 이긴 거래가 질 때보다 훨씬 크면 이익입니다. 그래서 승률만 보지 말고 '이길 때 평균 / 질 때 평균'과 함께 보세요.",
                      f"거래가 {MIN_TRADES}건 미만이면 승률이 우연인지 구분할 수 없습니다(95% 구간이 넓습니다)."]}
