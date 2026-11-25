"""'거래량 매물대' 표를 실시간 시세로 계산한다. 저장된 봉 파일이 멈춰 있으면 기준가가 며칠 전 값이 되던 문제를 없애고,
현재가에서 너무 먼 진입가·너무 넓은 손절·비현실적인 목표를 걸러낸다. 계산(거래량 프로파일)은 dashboard_levels 를 그대로 쓴다.

표시 규칙(진입 계획 카드와 같은 생각): ATR = 그 시간봉의 평균 변동폭.
- 진입가가 현재가에서 3 ATR 넘게 멀면 지금은 의미가 없으므로 표시하지 않는다(이유를 표에 적는다).
- 손절 폭은 0.8 ATR 이상으로 넓히고, 3.5 ATR 또는 가격의 10%를 넘으면 표시하지 않는다.
- 목표는 진입가에서 4.5 ATR 안으로 제한한다(제한되면 capped=True).
"""

from __future__ import annotations

import math
import threading
import time

from . import dashboard_levels as dl

TIMEFRAMES = {"15m": ("15", 15), "1h": ("60", 60), "4h": ("240", 240), "1d": ("D", 1440)}
LIMIT = 200
ATR_N = 14
MAX_ENTRY_ATR = 3.0
MIN_STOP_ATR, MAX_STOP_ATR, MAX_STOP_PCT = 0.8, 3.5, 0.10
TP_MAX_ATR = 4.5
TTL_SEC = 30
REF_COUNT = 3
NOTE = (dl.DISCLAIMER + " 진입가가 현재가에서 3 ATR(평균 변동폭의 3배)보다 멀거나 손절 폭이 3.5 ATR·10%를 넘으면 표시하지 않고, "
        "목표는 진입가에서 4.5 ATR 안으로 제한합니다(↧).")
_LOCK = threading.Lock()
_CACHE: dict = {}


def _atr(h: list, l: list, c: list, n: int = ATR_N) -> float:
    prev, out = c[0], None
    for i in range(len(c)):
        tr = max(h[i] - l[i], abs(h[i] - prev), abs(l[i] - prev))
        out = tr if out is None else (out * (n - 1) + tr) / n
        prev = c[i]
    return out if out is not None else math.nan


def _rows(rows) -> list:
    out = []
    for r in rows:
        try:
            ts, o, h, l, c, v = (float(x) for x in r[:6])
        except (TypeError, ValueError, IndexError):
            continue
        if all(map(math.isfinite, (ts, o, h, l, c, v))) and c > 0:
            out.append((ts, o, h, l, c, v))
    return out


def _side(direction: str, price: float, atr: float, near: dict, far: dict) -> tuple:
    """(계획 또는 None, 이유). near = 같은 쪽에서 가장 가까운 구간(롱: 아래 지지, 숏: 위 저항), far = 목표가 될 반대쪽 구간."""
    long = direction == "long"
    entry = near["price"]
    width = near["high"] - near["low"]
    stop = (near["low"] - width) if long else (near["high"] + width)
    tp = far["price"]
    dist = (price - entry) if long else (entry - price)
    if dist > MAX_ENTRY_ATR * atr:
        return None, f"진입가가 현재가에서 {dist / price:.0%} 멀어 지금은 무효"
    risk = (entry - stop) if long else (stop - entry)
    min_risk = MIN_STOP_ATR * atr
    if risk < min_risk:
        stop, risk = (entry - min_risk, min_risk) if long else (entry + min_risk, min_risk)
    if risk > MAX_STOP_ATR * atr or risk > MAX_STOP_PCT * entry:
        return None, f"손절 폭이 {risk / entry:.0%}로 너무 넓어 무효"
    cap = entry + TP_MAX_ATR * atr if long else entry - TP_MAX_ATR * atr
    capped = (tp > cap) if long else (tp < cap)
    if capped:
        tp = cap
    gain = (tp - entry) if long else (entry - tp)
    if gain <= 0:
        return None, "목표가 진입가보다 불리해 무효"
    return {"entry": entry, "take_profit": tp, "stop": stop, "rr": gain / risk, "capped": capped, "dist_pct": (entry - price) / price}, ""


def plan_timeframe(rows, minutes: int, now_ms: float, price: float | None = None) -> dict:
    data = _rows(rows)
    bar_ms = minutes * 60_000
    if data and data[-1][0] + bar_ms > now_ms:                 # 진행 중인 마지막 봉은 프로파일에서 뺀다
        live = data[-1][4]
        data = data[:-1]
    else:
        live = data[-1][4] if data else math.nan
    price = live if price is None else price
    out = {"status": "insufficient_data", "long": None, "short": None, "why": {}}
    if len(data) < dl.MIN_CANDLES:
        return out
    atr = _atr([d[2] for d in data], [d[3] for d in data], [d[4] for d in data])
    prof = dl.compute_liquidity_levels([d[1:] for d in data], price)
    if prof["status"] != "ok" or not math.isfinite(atr) or atr <= 0:
        return out
    sup, res = prof["support_levels"], prof["resistance_levels"]
    out["status"] = "ok"
    out["atr"] = atr
    # 계획이 무효여도 위치 정보는 남긴다: 현재가에서 가까운 순서로 지지·저항 각 3개와 현재가 대비 거리
    out["refs"] = {"support": [{"price": z["price"], "dist_pct": (z["price"] - price) / price} for z in sup[:REF_COUNT]],
                   "resistance": [{"price": z["price"], "dist_pct": (z["price"] - price) / price} for z in res[:REF_COUNT]]}
    if not sup or not res:                                    # 가격이 최근 범위의 맨 아래(또는 위)라서 한쪽에 거래량 구간이 없으면 진입가·목표가를 만들 수 없다
        no_sup = "현재가 아래에 거래량이 많이 쌓인 지지 구간이 없음(최근 하락이 빨라 아래쪽 거래가 적음)"
        no_res = "현재가 위에 거래량이 많이 쌓인 저항 구간이 없음(최근 상승이 빨라 위쪽 거래가 적음)"
        why = no_sup if not sup else no_res
        out["why"] = {"long": why if not sup else no_res + " — 롱 목표가 없음", "short": no_sup + " — 숏 목표가 없음" if not sup else no_res}
        if not sup and not res:
            out["why"] = {"long": "근처에 지지·저항 구간이 없음", "short": "근처에 지지·저항 구간이 없음"}
        return out
    for side, near, far in (("long", sup[0], res[0]), ("short", res[0], sup[0])):
        plan, why = _side(side, price, atr, near, far)
        out[side] = plan
        if why:
            out["why"][side] = why
    return out


def _clean(x):
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def compute(fetch, symbol: str, now_ms: int) -> dict:
    tfs, price, errors = {}, None, 0
    for name, (iv, minutes) in TIMEFRAMES.items():
        try:
            rows = fetch(iv, LIMIT)
            last = _rows(rows)
            if price is None and last:
                price = last[-1][4]                              # 가장 짧은 봉(15분)의 마지막 종가 = 지금 가격. 모든 시간봉이 같은 가격을 쓴다
            tfs[name] = plan_timeframe(rows, minutes, now_ms, price)
        except Exception:  # noqa: BLE001 - 한 시간봉의 시세 실패가 나머지를 막지 않게
            errors += 1
    if errors == len(TIMEFRAMES) or not tfs:
        raise RuntimeError("실시간 시세를 받지 못했습니다")
    if price is None:
        price = math.nan
    return _clean({"current_price": price, "timeframes": tfs, "symbol": symbol, "as_of": now_ms, "disclaimer": NOTE, "live": True})


def payload(fetch, fallback, symbol: str = "", now_ms: int | None = None, ttl: float = TTL_SEC, cache: dict | None = None) -> dict:
    """실시간으로 계산(30초 캐시). 시세를 못 받으면 저장된 봉으로 계산한 fallback() 결과를 stale=True 로 돌려준다."""
    cache = _CACHE if cache is None else cache
    now = time.time()
    with _LOCK:
        hit = cache.get(symbol)
        if hit and now - hit[0] < ttl:
            return hit[1]
        try:
            body = compute(fetch, symbol, int(now * 1000) if now_ms is None else now_ms)
        except Exception:  # noqa: BLE001
            body = {**_clean(fallback()), "stale": True, "live": False}
        cache[symbol] = (now, body)
    return body
