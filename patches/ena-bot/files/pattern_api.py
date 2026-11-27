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


def payload(fetch_rows, symbol: str, interval: str, now_ms: int, stats_path: Path = STATS_PATH, recent: int = 100) -> dict:
    """fetch_rows(interval, limit) -> [[ts_ms, o, h, l, c, v], ...] (오래된 것부터)."""
    base = {"symbol": symbol, "interval": interval, "disclaimer": "참고용입니다. 패턴의 과거 통계에서 비용을 넘는 초과 수익은 확인되지 않았습니다. 매매 신호로 쓰지 마세요."}
    if interval not in IV_TF:
        return {**base, "supported": False, "reason": "패턴 통계를 검증한 시간봉은 1시간·4시간뿐입니다. 그 시간봉으로 바꾸면 표시됩니다.", "patterns": [], "stats": {}}
    tf, step = IV_TF[interval]
    rows = [r for r in fetch_rows(interval, KLINES) if r[0] + step <= now_ms]      # 아직 닫히지 않은 마지막 봉은 쓰지 않는다
    if len(rows) < 120:
        return {**base, "supported": True, "tf": tf, "bars": len(rows), "patterns": [], "stats": {}, "reason": "봉이 부족합니다"}
    ts = [int(r[0]) for r in rows]
    h, l, c = [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows]
    found = _patterns.scan(ts, h, l, c, recent=recent)
    st = load_stats(stats_path)
    tfs = (st.get("tf") or {}).get(tf) or {}
    return {**base, "supported": True, "tf": tf, "bars": len(rows), "last_ts": ts[-1], "patterns": found, "stats": tfs.get("patterns", {}),
            "horizon": tfs.get("horizon"), "stats_meta": {"generated": st.get("generated"), "coins": st.get("coins"), "cost_round_trip_pct": st.get("cost_round_trip_pct")} if st else None}
