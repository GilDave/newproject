"""계좌 비교 성적표(읽기 전용): 기준 계좌와 그림자 계좌(BTC 필터 · ML 게이트)를 '같은 기간'으로 맞춰 비교한다.

bot.db 의 trend_cycle / trend_shadow / ml_shadow 이벤트에서 같은 봉(ts)이 있는 사이클만 골라 첫 시점을 100 으로 맞춘다.
기간이 짧으면(기본 90일 미만) 우열을 가리지 않는다: 추세추종은 거래가 적어 몇 주 차이는 우연과 구분되지 않는다.
정지(halted) 사이클이 섞이면 모든 계좌가 같이 멈춰 있으므로 그 개수를 따로 알린다. 매매·설정은 건드리지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

MIN_DAYS = 90
BAR_MS = 4 * 3_600_000
KINDS = {"base": ("trend_cycle", "기준 계좌"), "shadow": ("trend_shadow", "BTC 필터 계좌"), "ml": ("ml_shadow", "ML 게이트 계좌")}


def _load(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("select kind, data from events where kind in ('trend_cycle','trend_shadow','ml_shadow') order by id").fetchall()
    finally:
        con.close()
    out: dict = {k: {} for k in KINDS}
    rev = {v[0]: k for k, v in KINDS.items()}
    for kind, data in rows:
        d = json.loads(data)
        if d.get("ts") is None or d.get("equity") is None:
            continue
        out[rev[kind]][int(d["ts"])] = d  # 같은 봉이 두 번 기록돼도 마지막 값
    return out


def _stats(seq: list[dict]) -> dict:
    eq = [float(d["equity"]) for d in seq]
    peak, mdd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        mdd = min(mdd, (e / peak - 1) * 100)
    return {"return_pct": round((eq[-1] / eq[0] - 1) * 100, 2), "max_dd_pct": round(mdd, 2),
            "rebalances": sum(1 for d in seq[1:] if d.get("delta")),
            "in_market_pct": round(sum(1 for d in seq if d.get("qty")) / len(seq) * 100, 1),
            "curve": [[int(d["ts"]), round(float(d["equity"]) / eq[0] * 100, 3)] for d in seq]}


def payload(db: str | Path, now_ms: int | None = None) -> dict:
    db = Path(db)
    if not db.is_file():
        return {"ok": False, "error": "bot.db 없음"}
    try:
        data = _load(db)
    except Exception as exc:  # noqa: BLE001 - 잠긴 DB 등으로 화면이 깨지면 안 된다
        return {"ok": False, "error": str(exc)[:200]}
    have = [k for k in KINDS if data[k]]
    if "base" not in have or len(have) < 2:
        return {"ok": True, "enough": False, "accounts": [], "notes": ["비교할 그림자 계좌 기록이 아직 없습니다."], "min_days": MIN_DAYS}
    common = sorted(set.intersection(*(set(data[k]) for k in have)))
    if len(common) < 2:
        return {"ok": True, "enough": False, "accounts": [], "notes": ["같은 봉에 기록된 사이클이 2개 미만입니다."], "min_days": MIN_DAYS}
    accounts = []
    for k in have:
        seq = [data[k][t] for t in common]
        accounts.append({"key": k, "label": KINDS[k][1], **_stats(seq)})
    base = data["base"]
    halted = sum(1 for t in common if any(str(w).startswith("halted") for w in (base[t].get("why") or [])))
    p0, p1 = base[common[0]].get("price"), base[common[-1]].get("price")
    days = (common[-1] - common[0]) / 86_400_000
    bh = round((p1 / p0 - 1) * 100, 2) if p0 and p1 else None
    b = next(a for a in accounts if a["key"] == "base")
    for a in accounts:
        a["vs_base_pct"] = None if a["key"] == "base" else round(a["return_pct"] - b["return_pct"], 2)
    enough = days >= MIN_DAYS
    notes = []
    if not enough:
        notes.append(f"비교 기간 {days:.1f}일 — {MIN_DAYS}일 미만이라 수익 차이를 우열로 읽지 마세요(거래가 적어 우연과 구분되지 않습니다).")
    if halted:
        notes.append(f"전면 정지로 모든 계좌가 멈춰 있던 사이클 {halted}개가 기간에 포함돼 있습니다.")
    if "ml" not in have:
        notes.append("ML 게이트 계좌 기록이 없어 제외했습니다(learn-train 후 ml_shadow 설정이 켜져 있어야 합니다).")
    return {"ok": True, "enough": enough, "min_days": MIN_DAYS, "days": round(days, 1), "cycles": len(common),
            "start_ms": common[0], "end_ms": common[-1], "buy_hold_pct": bh, "halted_cycles": halted, "accounts": accounts, "notes": notes,
            "ts": now_ms if now_ms is not None else int(time.time() * 1000)}
