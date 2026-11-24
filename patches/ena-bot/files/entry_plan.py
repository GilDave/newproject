"""지표(RSI·MACD·볼린저 밴드) 기반 진입 계획 계산. 수동 매매 보조용이며 순수 계산이다(파일·네트워크 접근 없음, 미래 데이터를 쓰지 않는다).

규칙을 코드로 고정해서 시간봉마다 '진입 구간 · 손절 · 목표 · 손익비'를 낸다. 이 규칙이 과거에 실제로 나았는지는 entry_study.py 가 따로 검증하고,
화면은 그 결과(통과 / 기준 미달 / 검증 전)를 항상 함께 보여 준다. 규칙이 좋다는 뜻이 아니라 '조건이 겹쳤다'는 뜻일 뿐이다.

두 가지 계획:
- 추세 눌림목: 추세(EMA21 vs EMA50) 방향으로, 가격이 볼린저 중심선·EMA21 부근까지 되돌아오면 진입. 목표는 위험(R)의 1.5배·3배.
- 밴드 반전: 볼린저 밴드 바깥(과매도/과매수)에서 RSI·MACD 히스토그램이 되돌리는 쪽으로 돌 때 진입. 목표는 볼린저 중심선·반대편 밴드.
"""

from __future__ import annotations

import math

from .structure import atr as _atr

RSI_N, MACD_FAST, MACD_SLOW, MACD_SIG, BB_N, BB_K, EMA_FAST, EMA_SLOW = 14, 12, 26, 9, 20, 2.0, 21, 50
MIN_BARS = 120
MIN_STOP_ATR, MAX_STOP_ATR = 0.8, 4.0     # 손절 폭이 이 범위를 벗어나는 계획은 만들지 않는다(너무 촘촘하거나 너무 넓음)
ZONE_REACH_ATR = 1.5                      # 가격이 진입 구간에서 이만큼(ATR) 이내면 '다가오는 중'
COST_FEE, COST_SLIP = 0.00055, 0.0001     # 한쪽 체결당 수수료·슬리피지(entry_study 와 같은 값)
WARN_RISK_PCT, SEVERE_RISK_PCT = 0.08, 0.10   # 손절 폭이 가격의 이 비율을 넘으면 경고 / '가장 유리한 시간봉' 후보에서 제외
WARN_COST_R, SEVERE_COST_R = 0.15, 0.25       # 왕복 비용이 위험(R)의 이 비율을 넘으면 경고 / 후보에서 제외
FAR_TARGET_PCT = 0.30                          # 목표가 진입가에서 이만큼 멀면 비현실적이라고 경고
TIMEFRAMES = {"15m": ("15", 15), "1h": ("60", 60), "4h": ("240", 240), "1d": ("D", 1440)}
NAN = float("nan")


def ema(x: list, n: int) -> list:
    a = 2.0 / (n + 1)
    out = [x[0]]
    for v in x[1:]:
        out.append(out[-1] + a * (v - out[-1]))
    return out


def rsi(c: list, n: int = RSI_N) -> list:
    """Wilder RSI. 앞의 n개는 NaN."""
    out = [NAN] * len(c)
    if len(c) <= n:
        return out
    gain = sum(max(c[i] - c[i - 1], 0.0) for i in range(1, n + 1)) / n
    loss = sum(max(c[i - 1] - c[i], 0.0) for i in range(1, n + 1)) / n
    out[n] = _rsi_value(gain, loss)
    for i in range(n + 1, len(c)):
        gain = (gain * (n - 1) + max(c[i] - c[i - 1], 0.0)) / n
        loss = (loss * (n - 1) + max(c[i - 1] - c[i], 0.0)) / n
        out[i] = _rsi_value(gain, loss)
    return out


def _rsi_value(gain: float, loss: float) -> float:
    if loss == 0:
        return 50.0 if gain == 0 else 100.0          # 전혀 안 움직였으면 50
    return 100.0 - 100.0 / (1.0 + gain / loss)


def macd(c: list) -> tuple[list, list, list]:
    fast, slow = ema(c, MACD_FAST), ema(c, MACD_SLOW)
    line = [a - b for a, b in zip(fast, slow)]
    sig = ema(line, MACD_SIG)
    return line, sig, [a - b for a, b in zip(line, sig)]


def bollinger(c: list, n: int = BB_N, k: float = BB_K) -> tuple[list, list, list]:
    mid, up, lo = [NAN] * len(c), [NAN] * len(c), [NAN] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)
        mid[i], up[i], lo[i] = m, m + k * sd, m - k * sd
    return mid, up, lo


def indicators(o: list, h: list, l: list, c: list) -> dict:
    line, sig, hist = macd(c)
    mid, up, lo = bollinger(c)
    return {"o": o, "h": h, "l": l, "c": c, "rsi": rsi(c), "macd": line, "signal": sig, "hist": hist, "bb_mid": mid, "bb_up": up, "bb_lo": lo,
            "ema_fast": ema(c, EMA_FAST), "ema_slow": ema(c, EMA_SLOW), "atr": _atr(h, l, c)}


def snapshot(ind: dict, t: int) -> dict:
    """카드에 보여 줄 지표 현황(봉 t 기준)."""
    width = ind["bb_up"][t] - ind["bb_lo"][t]
    return {"rsi": ind["rsi"][t], "macd_hist": ind["hist"][t], "macd_above_signal": ind["macd"][t] > ind["signal"][t],
            "pct_b": (ind["c"][t] - ind["bb_lo"][t]) / width if width > 0 else 0.5, "trend": 1 if ind["ema_fast"][t] > ind["ema_slow"][t] else -1,
            "atr": ind["atr"][t], "bb_mid": ind["bb_mid"][t], "bb_up": ind["bb_up"][t], "bb_lo": ind["bb_lo"][t], "ema_fast": ind["ema_fast"][t]}


def _state(price: float, lo: float, hi: float, a: float, direction: int) -> str:
    """진입 구간 대비 가격 위치. 롱은 구간 위에서 내려와 닿는 것, 숏은 아래에서 올라와 닿는 것을 기다린다."""
    if lo <= price <= hi:
        return "in_zone"
    if direction == 1:
        if price > hi:
            return "approaching" if price - hi <= ZONE_REACH_ATR * a else "waiting"
        return "broken"                    # 구간 아래로 이미 내려감
    if price < lo:
        return "approaching" if lo - price <= ZONE_REACH_ATR * a else "waiting"
    return "broken"


def _finish(direction: int, setup: str, entry: float, stop: float, zone: tuple, targets: list, a: float, checks: list, state: str, trend: int, price: float, notes: list) -> dict | None:
    risk = (entry - stop) if direction == 1 else (stop - entry)
    if not (MIN_STOP_ATR * a <= risk <= MAX_STOP_ATR * a) or risk <= 0:
        return None
    reward = (targets[0] - entry) if direction == 1 else (entry - targets[0])
    rr, risk_pct = reward / risk, risk / entry
    cost_r = entry * 2 * (COST_FEE + COST_SLIP) / risk            # 왕복 비용이 위험(R)의 몇 배인지
    warnings, severe = [], False
    if risk_pct > WARN_RISK_PCT:
        warnings.append(f"손절 폭이 큼({risk_pct:.1%}) — 목표도 그만큼 멀어집니다")
        severe = severe or risk_pct > SEVERE_RISK_PCT
    if cost_r > WARN_COST_R:
        warnings.append(f"수수료·슬리피지가 위험의 {cost_r:.0%}(손절 폭이 좁아 비용 부담이 큼)")
        severe = severe or cost_r > SEVERE_COST_R
    far = max(abs(t - entry) / entry for t in targets)
    if far > FAR_TARGET_PCT:
        warnings.append(f"목표가 진입가에서 {far:.0%} 떨어져 있어 비현실적일 수 있음")
    return {"setup": setup, "direction": "long" if direction == 1 else "short", "state": state, "entry": entry, "zone": [zone[0], zone[1]], "stop": stop,
            "targets": targets, "rr": rr, "risk_pct": risk_pct, "cost_r": cost_r, "be_win_rate": (1 + cost_r) / (rr + 1), "dist_pct": (entry - price) / price,
            "warnings": warnings, "severe": severe, "score": sum(1 for _, ok, _ in checks if ok), "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
            "aligned_trend": direction == trend, "price": price, "notes": notes}


def candidates(ind: dict, t: int, price: float | None = None) -> list:
    """봉 t(마감된 봉)까지의 정보로 만든 진입 계획 후보들. price 는 지금 가격(없으면 봉 t의 종가)."""
    c, h, l = ind["c"], ind["h"], ind["l"]
    price = c[t] if price is None else price
    a = ind["atr"][t]
    if t < MIN_BARS or any(math.isnan(ind[k][t]) for k in ("rsi", "bb_mid", "bb_up", "bb_lo")) or not a > 0:
        return []
    r, hist, hist_prev = ind["rsi"][t], ind["hist"][t], ind["hist"][t - 1]
    mid, up, lo_b = ind["bb_mid"][t], ind["bb_up"][t], ind["bb_lo"][t]
    width = up - lo_b
    pctb = (c[t] - lo_b) / width if width > 0 else 0.5
    trend = 1 if ind["ema_fast"][t] > ind["ema_slow"][t] else -1
    e21 = ind["ema_fast"][t]
    out = []
    # 1) 추세 눌림목 (추세 방향)
    d = trend
    if d == 1:
        zhi, zlo = max(mid, e21), min(mid, e21) - 0.25 * a
        entry = zhi
        stop = min(min(l[t - 9:t + 1]), zlo) - 0.3 * a
        stop = min(stop, entry - MIN_STOP_ATR * a)
        checks = [("RSI 35~60(과열·과매도 아님)", 35 <= r <= 60, f"RSI {r:.0f}"), ("MACD 상승 쪽(선>신호 또는 히스토그램 개선)", ind["macd"][t] > ind["signal"][t] or hist > hist_prev, f"히스토그램 {hist:+.5f}(직전 {hist_prev:+.5f}), MACD선은 신호선{'보다 위' if ind['macd'][t] > ind['signal'][t] else '보다 아래'}"),
                  ("볼린저 중하단(%B 0.15~0.65)", 0.15 <= pctb <= 0.65, f"%B {pctb:.2f}")]
    else:
        zlo, zhi = min(mid, e21), max(mid, e21) + 0.25 * a
        entry = zlo
        stop = max(max(h[t - 9:t + 1]), zhi) + 0.3 * a
        stop = max(stop, entry + MIN_STOP_ATR * a)
        checks = [("RSI 40~65(과열·과매도 아님)", 40 <= r <= 65, f"RSI {r:.0f}"), ("MACD 하락 쪽(선<신호 또는 히스토그램 악화)", ind["macd"][t] < ind["signal"][t] or hist < hist_prev, f"히스토그램 {hist:+.5f}(직전 {hist_prev:+.5f}), MACD선은 신호선{'보다 위' if ind['macd'][t] > ind['signal'][t] else '보다 아래'}"),
                  ("볼린저 중상단(%B 0.35~0.85)", 0.35 <= pctb <= 0.85, f"%B {pctb:.2f}")]
    risk = abs(entry - stop)
    targets = [entry + d * 1.5 * risk, entry + d * 3.0 * risk]
    p = _finish(d, "pullback", entry, stop, (zlo, zhi), targets, a, checks, _state(price, zlo, zhi, a, d), trend, price, ["추세 방향 눌림목", "목표는 위험의 1.5배·3배"])
    if p:
        out.append(p)
    # 2) 밴드 반전 (롱: 아래 밴드, 숏: 위 밴드)
    for d, edge, ok_band, ok_rsi, ok_macd in ((1, lo_b, pctb <= 0.1, r <= 32, hist > hist_prev), (-1, up, pctb >= 0.9, r >= 68, hist < hist_prev)):
        zlo, zhi = (edge - 0.5 * a, edge + 0.25 * a) if d == 1 else (edge - 0.25 * a, edge + 0.5 * a)
        entry = edge
        extreme = min(l[t - 4:t + 1]) if d == 1 else max(h[t - 4:t + 1])
        stop = (min(extreme, zlo) - 0.5 * a) if d == 1 else (max(extreme, zhi) + 0.5 * a)
        stop = min(stop, entry - MIN_STOP_ATR * a) if d == 1 else max(stop, entry + MIN_STOP_ATR * a)
        targets = [mid, up if d == 1 else lo_b]
        checks = [("볼린저 밴드 바깥 근처(%B)", ok_band, f"%B {pctb:.2f}"), ("RSI 과매도/과매수" + (" ≤32" if d == 1 else " ≥68"), ok_rsi, f"RSI {r:.0f}"),
                  ("MACD 히스토그램이 되돌리는 방향", ok_macd, f"히스토그램 {hist:+.5f} (직전 {hist_prev:+.5f})")]
        st = _state(price, zlo, zhi, a, d)
        if not (ok_band or st in ("in_zone", "approaching")):
            continue                                              # 밴드 근처가 아니면 반전 계획을 내지 않는다
        p = _finish(d, "reversion", entry, stop, (zlo, zhi), targets, a, checks, st, trend, price, ["밴드 반전" + ("" if d == trend else " · 추세와 반대 방향(주의)"), "목표는 볼린저 중심선·반대편 밴드"])
        if p:
            if p["rr"] < 1.0:
                p["state"] = "poor_rr"                           # 첫 목표까지의 이득이 위험보다 작으면 대기
            out.append(p)
    return out


STATE_RANK = {"in_zone": 3, "approaching": 2, "waiting": 1, "poor_rr": 0, "broken": 0}


def best_for(cands: list) -> dict | None:
    """한 시간봉의 후보 중 지금 가장 쓸 만한 것(상태 → 점수 → 추세 일치 → 손익비 순)."""
    if not cands:
        return None
    return max(cands, key=lambda p: (STATE_RANK[p["state"]], p["score"], p["aligned_trend"], p["rr"]))


def _rows_to_arrays(rows: list) -> tuple:
    rows = sorted((r for r in rows if len(r) >= 5), key=lambda r: r[0])
    if rows and len(rows[0]) >= 6 and rows[0][0] > 1e11:                  # [ts, o, h, l, c, v]
        return [int(r[0]) for r in rows], [float(r[1]) for r in rows], [float(r[2]) for r in rows], [float(r[3]) for r in rows], [float(r[4]) for r in rows]
    raise ValueError("캔들 행의 형식을 확인하지 못했습니다([ts, open, high, low, close, volume]이어야 합니다)")


def plan_timeframe(rows: list, bar_ms: int, now_ms: int) -> dict:
    """한 시간봉의 계획. 신호는 마감된 봉까지로 계산하고, 진행 중인 마지막 봉의 종가를 지금 가격으로 쓴다."""
    ts, o, h, l, c = _rows_to_arrays(rows)
    if len(c) < MIN_BARS + 2:
        return {"status": "insufficient", "reason": f"봉이 부족합니다({len(c)}개, 필요 {MIN_BARS + 2}개)"}
    forming = ts[-1] + bar_ms > now_ms
    last_closed = len(c) - 2 if forming else len(c) - 1
    ind = indicators(o, h, l, c)
    price = c[-1]
    cands = candidates(ind, last_closed, price)
    return {"status": "ok", "price": price, "signal_bar": ts[last_closed], "forming": forming, "snapshot": snapshot(ind, last_closed), "best": best_for(cands), "all": cands}


def plans(fetch, now_ms: int, timeframes: dict | None = None, limit: int = 300) -> dict:
    """모든 시간봉의 계획과, 지금 가장 유리한 시간봉 하나. fetch(interval, limit) → 캔들 행 목록. 한 시간봉이 실패해도 나머지는 계산한다."""
    tfs = timeframes or TIMEFRAMES
    out = {"timeframes": {}, "generated": now_ms}
    for name, (iv, minutes) in tfs.items():
        try:
            out["timeframes"][name] = plan_timeframe(fetch(iv, limit), minutes * 60_000, now_ms)
        except Exception as exc:  # noqa: BLE001 - 시세 조회 실패(요청 한도 등)가 다른 시간봉을 막지 않게
            out["timeframes"][name] = {"status": "error", "reason": str(exc)[:160]}
    # 상위 시간봉(4h·1d)의 추세와 방향이 같으면 가산
    htf = [p["snapshot"]["trend"] for k, p in out["timeframes"].items() if k in ("4h", "1d") and p.get("status") == "ok"]
    best, best_key = None, None
    for name, p in out["timeframes"].items():
        b = p.get("best") if p.get("status") == "ok" else None
        if not b or b["state"] not in ("in_zone", "approaching") or b["score"] < 2 or b["severe"]:
            continue                                  # 손절 폭이 너무 크거나 비용 부담이 큰 계획은 '가장 유리한 시간봉'으로 내세우지 않는다(표에는 경고와 함께 남는다)
        direction = 1 if b["direction"] == "long" else -1
        b["htf_aligned"] = bool(htf) and all(x == direction for x in htf)
        key = (STATE_RANK[b["state"]], b["score"], b["htf_aligned"], b["rr"])
        if best_key is None or key > best_key:
            best, best_key = name, key
    out["favorable"] = best
    out["note"] = None if best else "지금은 조건이 충분히 겹친 시간봉이 없습니다(대기)."
    return out
