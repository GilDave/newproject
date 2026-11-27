"""/api/patterns: 차트의 시간봉에서 감지한 패턴(도형)과 그 패턴의 과거 통계를 돌려준다. 참고용이며 신호가 아니다.
과거 통계표(pattern_stats.json)는 18코인 이력으로 만든 것(ena_pattern_stats.py)이고, 1시간봉·4시간봉만 검증했다."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from . import patterns as _patterns
except ImportError:  # 패키지 밖(테스트)에서 불러올 때
    import patterns as _patterns

IV_TF = {"60": ("1h", 3_600_000), "240": ("4h", 14_400_000)}
STATS_PATH = Path(__file__).with_name("pattern_stats.json")
KLINES = 1000


def load_stats(path: Path = STATS_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


NEAR_ATR, FAR_ATR = 1.5, 6.0           # 선까지 거리가 이 ATR 배수 이내면 '임박', FAR 를 넘으면 '다가오는 신호'에서 뺀다


def upcoming(found, price: float, atr_v: float) -> list[dict]:
    """형성 중인 패턴이 앞으로 확정되려면 가격이 넘어야 할 선과 현재 거리. 방향을 예측하지 않는다(선까지의 거리만 계산).
    선이 둘(삼각·쐐기·박스)이면 위·아래를 모두 준다. 선이 하나(깃발·쌍바닥·헤드앤숄더)이면 그 패턴이 뜻하는 방향 하나만 준다."""
    out = []
    if not (price and price > 0 and atr_v and atr_v > 0):
        return out
    for g in found:
        if g.get("state") != "forming":
            continue
        ends = [ln[1][1] for ln in g.get("lines", []) if ln and len(ln) == 2]
        if len(ends) >= 2:
            up, dn = max(ends), min(ends)
            if not (dn < up):
                continue
            sides = [{"side": "up", "level": up}, {"side": "down", "level": dn}]
        elif g.get("level") is not None:
            sides = [{"side": "up" if g["dir"] > 0 else "down", "level": g["level"]}]
        else:
            continue
        for sd in sides:
            d = sd["level"] - price
            sd["level"], sd["dist_pct"] = float(sd["level"]), float(d / price * 100)
            sd["dist_atr"] = float(abs(d) / atr_v)
            sd["crossed"] = bool(d <= 0 if sd["side"] == "up" else d >= 0)      # 이미 선을 넘은 상태(아직 마감 확인 전)
        sides.sort(key=lambda x: x["dist_atr"])
        if sides[0]["dist_atr"] > FAR_ATR and not sides[0]["crossed"]:
            continue
        out.append({"pat": g["pat"], "name": g["name"], "dir": g["dir"], "sides": sides, "nearest_atr": float(sides[0]["dist_atr"]),
                    "imminent": bool(sides[0]["dist_atr"] <= NEAR_ATR or sides[0]["crossed"])})
    out.sort(key=lambda u: u["nearest_atr"])
    return out


def payload(fetch_rows, symbol: str, interval: str, now_ms: int, stats_path: Path = STATS_PATH, recent: int = 100) -> dict:
    """fetch_rows(interval, limit) -> [[ts_ms, o, h, l, c, v], ...] (오래된 것부터)."""
    base = {"symbol": symbol, "interval": interval, "disclaimer": "참고용입니다. 패턴의 과거 통계에서 비용을 넘는 초과 수익은 확인되지 않았습니다. 매매 신호로 쓰지 마세요."}
    if interval not in IV_TF:
        return {**base, "supported": False, "reason": "패턴 통계를 검증한 시간봉은 1시간·4시간뿐입니다. 그 시간봉으로 바꾸면 표시됩니다.", "patterns": [], "stats": {}, "explain": {}, "glossary": _patterns.GLOSSARY}
    tf, step = IV_TF[interval]
    rows = [r for r in fetch_rows(interval, KLINES) if r[0] + step <= now_ms]      # 아직 닫히지 않은 마지막 봉은 쓰지 않는다
    if len(rows) < 120:
        return {**base, "supported": True, "tf": tf, "bars": len(rows), "patterns": [], "stats": {}, "reason": "봉이 부족합니다"}
    ts = [int(r[0]) for r in rows]
    h, l, c = [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows]
    found = _patterns.scan(ts, h, l, c, recent=recent)
    st = load_stats(stats_path)
    tfs = (st.get("tf") or {}).get(tf) or {}
    explain = {g["pat"]: _patterns.EXPLAIN[g["pat"]] for g in found}
    import numpy as np
    a = _patterns.atr(np.asarray(h, float), np.asarray(l, float), np.asarray(c, float))[-1]
    soon = upcoming(found, c[-1], None if a != a else float(a))
    return {**base, "supported": True, "tf": tf, "bars": len(rows), "last_ts": ts[-1], "patterns": found, "upcoming": soon, "near_atr": NEAR_ATR, "atr": None if a != a else float(a), "stats": tfs.get("patterns", {}),
            "explain": explain, "glossary": _patterns.GLOSSARY,
            "horizon": tfs.get("horizon"), "stats_meta": {"generated": st.get("generated"), "coins": st.get("coins"), "cost_round_trip_pct": st.get("cost_round_trip_pct")} if st else None}
