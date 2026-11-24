"""대시보드의 '진입 계획' 카드·차트 선이 읽는 JSON. 시세는 차트와 같은 공개 API 함수로 받고(키 필요 없음), 계산은 entry_plan, 검증 요약은 entry_study."""

from __future__ import annotations

import math
import threading
import time

from . import entry_plan, entry_study

TTL_SEC = 60
_LOCK = threading.Lock()
_CACHE: dict = {}


def clean(x):
    """JSON으로 보낼 수 있게 NaN·무한대를 None 으로(서버는 allow_nan=False 로 직렬화한다)."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


def payload(fetch, data_path, symbol: str = "", now_ms: int | None = None, ttl: float = TTL_SEC, cache: dict | None = None) -> dict:
    """fetch(interval, limit) → 캔들 행. 60초 캐시(여러 화면이 열려 있어도 시세 요청이 늘지 않도록)."""
    cache = _CACHE if cache is None else cache
    now = time.time()
    with _LOCK:
        hit = cache.get(symbol)
        if hit and now - hit[0] < ttl:
            return {**hit[1], "study": entry_study.load_saved(data_path), "cached": True}
        plans = clean(entry_plan.plans(fetch, int(now * 1000) if now_ms is None else now_ms))
        body = {"ok": True, "symbol": symbol, "plans": plans, "generated": int(now * 1000), "cached": False}
        cache[symbol] = (now, body)
    return {**body, "study": entry_study.load_saved(data_path)}
