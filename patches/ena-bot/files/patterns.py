"""차트 패턴 감지(참고용 표시). 정의는 ena_pattern_study.py · ena_pattern_study2.py 에서 과거 통계를 낸 감지기와 같고(같은 이벤트를 찾는지 테스트로 확인),
여기서는 차트에 그릴 수 있게 점·선(도형)과 '형성 중' 상태까지 돌려준다. 신호가 아니다: 통계상 비용을 넘는 초과 수익이 확인되지 않았다.
우측 4봉이 지난 확정 피벗만 쓰고 종가 기준으로 돌파를 판정하므로, 마지막 봉 이후의 정보를 쓰지 않는다. 마감된 봉만 넣어야 한다."""
from __future__ import annotations

import numpy as np

K = 4
COOLDOWN = 24
NAMES = {"P0W": "쌍바닥(W)", "P0M": "쌍봉(M)", "P1": "역헤드앤숄더", "P2": "헤드앤숄더", "P3": "삼각 수렴 상향 돌파", "P4": "삼각 수렴 하향 이탈",
         "P5": "하락 쐐기 상향 돌파", "P6": "상승 쐐기 하향 이탈", "P7": "상승 깃발", "P8": "하락 깃발", "P9": "박스권 상향 돌파", "P10": "박스권 하향 이탈"}
DIR = {"P0W": 1, "P0M": -1, "P1": 1, "P2": -1, "P3": 1, "P4": -1, "P5": 1, "P6": -1, "P7": 1, "P8": -1, "P9": 1, "P10": -1}


def atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(c), np.nan)
    if len(c) >= n:
        cs = np.cumsum(np.insert(tr, 0, 0.0))
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def confirmed_pivots(h, l, k=K):
    out = []
    for p in range(k, len(h) - k):
        if l[p] < l[p - k:p].min() and l[p] <= l[p + 1:p + k + 1].min():
            out.append((p + k, p, "L"))
        if h[p] > h[p - k:p].max() and h[p] >= h[p + 1:p + k + 1].max():
            out.append((p + k, p, "H"))
    out.sort()
    return out


def _g(pat, state, idx, points=(), lines=(), level=None, extra=None):
    return {"pat": pat, "state": state, "idx": idx, "points": [[int(i), float(v)] for i, v in points],
            "lines": [[[int(a), float(b)], [int(c_), float(d)]] for (a, b), (c_, d) in lines], "level": None if level is None else float(level), **(extra or {})}


# ------------------------------------------------------------------ 쌍바닥/쌍봉 (상승형 W 기준. M 은 뒤집어서)
def _double(h, l, c, k=K, s=1.0):
    a, pv = atr(h, l, c), confirmed_pivots(h, l, k)
    events, armed, last_ext, seen_opp, ptr = [], [], None, [], 0
    for i in range(len(c)):
        while ptr < len(pv) and pv[ptr][0] <= i:
            conf, p, t = pv[ptr]
            ptr += 1
            if t == "H":
                seen_opp.append((p, h[p]))
            else:
                if last_ext is not None and np.isfinite(a[conf]):
                    p1 = last_ext
                    gap = p - p1
                    mids = [(q, v) for q, v in seen_opp if p1 < q < p]
                    if 8 <= gap <= 72 and mids:
                        neck = max(v for _, v in mids)
                        q = next(q for q, v in mids if v == neck)
                        e1, e2 = l[p1], l[p]
                        near = max(e1, e2)
                        if abs(e1 - e2) <= 0.6 * s * a[conf] and (neck - near) >= 2 / s * a[conf]:
                            armed.append({"neck": neck, "bad": min(e1, e2) - 0.5 * a[conf], "until": conf + 48, "from": conf,
                                          "pts": [(p1, e1), (q, neck), (p, e2)]})
                last_ext = p
        keep = []
        for ar in armed:
            if i > ar["until"] or (c[i] - ar["bad"]) < 0:
                continue
            if i > ar["from"] and (c[i] - ar["neck"]) > 0:
                events.append(_g("", "confirmed", i, ar["pts"], [((ar["pts"][0][0], ar["neck"]), (i, ar["neck"]))], ar["neck"]))
                continue
            keep.append(ar)
        armed = keep
    forming = [_g("", "forming", None, ar["pts"], [((ar["pts"][0][0], ar["neck"]), (len(c) - 1, ar["neck"]))], ar["neck"]) for ar in armed]
    return events, forming


# ------------------------------------------------------------------ 역헤드앤숄더 (상승형 기준)
def _inv_hs(h, l, c, k=K, s=1.0):
    a, pv = atr(h, l, c), confirmed_pivots(h, l, k)
    lows, highs, armed, events, last, ptr = [], [], [], [], -10**9, 0
    for i in range(len(c)):
        while ptr < len(pv) and pv[ptr][0] <= i:
            conf, p, t = pv[ptr]
            ptr += 1
            if t == "H":
                highs.append((p, h[p]))
            else:
                lows.append((p, l[p]))
                if len(lows) >= 3 and np.isfinite(a[conf]):
                    (pa, va), (pb, vb), (pc, vc) = lows[-3:]
                    A = a[conf]
                    if pb - pa >= 5 and pc - pb >= 5 and pc - pa <= 150 and min(va, vc) - vb >= 1.5 / s * A and abs(va - vc) <= 1.0 * s * A:
                        c1 = [x for x in highs if pa < x[0] < pb]
                        c2 = [x for x in highs if pb < x[0] < pc]
                        if c1 and c2:
                            q1, v1 = max(c1, key=lambda x: x[1])
                            q2, v2 = max(c2, key=lambda x: x[1])
                            if q2 > q1:
                                armed.append({"q1": q1, "v1": v1, "q2": q2, "v2": v2, "bad": vb - 0.5 * A, "until": conf + 60, "from": conf,
                                              "pts": [(pa, va), (q1, v1), (pb, vb), (q2, v2), (pc, vc)]})
        keep = []
        for ar in armed:
            if i > ar["until"] or c[i] < ar["bad"]:
                continue
            neck = ar["v1"] + (ar["v2"] - ar["v1"]) * (i - ar["q1"]) / (ar["q2"] - ar["q1"])
            if i > ar["from"] and c[i] > neck:
                if i - last > COOLDOWN:
                    events.append(_g("", "confirmed", i, ar["pts"], [((ar["q1"], ar["v1"]), (i, neck))], neck))
                    last = i
                continue
            keep.append(ar)
        armed = keep
    n = len(c) - 1
    forming = [_g("", "forming", None, ar["pts"], [((ar["q1"], ar["v1"]), (n, ar["v1"] + (ar["v2"] - ar["v1"]) * (n - ar["q1"]) / (ar["q2"] - ar["q1"])))],
                  ar["v1"] + (ar["v2"] - ar["v1"]) * (n - ar["q1"]) / (ar["q2"] - ar["q1"])) for ar in armed]
    return events, forming


# ------------------------------------------------------------------ 삼각 수렴 · 하락 쐐기 (상향 돌파 기준)
def _lines_state(highs, lows, i, A, mode, s):
    (p1_, v1), (p2_, v2) = highs[-2:]
    (q1, w1), (q2, w2) = lows[-2:]
    start, end = min(p1_, q1), max(p2_, q2)
    if end - start > 150 or i - end > 80:
        return None
    rs, ss = (v2 - v1) / (p2_ - p1_), (w2 - w1) / (q2 - q1)
    if not rs < ss:
        return None
    if mode == "tri" and not (rs <= 0 <= ss):
        return None
    if mode == "wedge" and not (rs < 0 and ss <= -0.03 / s * A):
        return None
    res = lambda t_: v2 + rs * (t_ - p2_)
    sup = lambda t_: w2 + ss * (t_ - q2)
    width_i = res(i) - sup(i)
    width_s = (v1 + rs * (start - p1_)) - (w1 + ss * (start - q1))
    if width_i < 1.5 / s * A or width_s < 1.6 * width_i:
        return None
    return {"res": res, "sup": sup, "pts": [(p1_, v1), (p2_, v2), (q1, w1), (q2, w2)], "start": start}


def _lines(h, l, c, mode, k=K, s=1.0):
    a, pv = atr(h, l, c), confirmed_pivots(h, l, k)
    highs, lows, events, last, ptr, lastconf = [], [], [], -10**9, 0, -10**9
    for i in range(1, len(c)):
        while ptr < len(pv) and pv[ptr][0] <= i:
            conf, p, t = pv[ptr]
            ptr += 1
            lastconf = conf
            (highs if t == "H" else lows).append((p, h[p] if t == "H" else l[p]))
        if len(highs) < 2 or len(lows) < 2 or i - lastconf > 60 or not np.isfinite(a[i]) or i - last <= COOLDOWN:
            continue
        st = _lines_state(highs, lows, i, a[i], mode, s)
        if st is None:
            continue
        if c[i] > st["res"](i) and c[i - 1] <= st["res"](i - 1) and c[i] >= st["sup"](i):
            events.append(_g("", "confirmed", i, st["pts"], [((st["start"], st["res"](st["start"])), (i, st["res"](i))), ((st["start"], st["sup"](st["start"])), (i, st["sup"](i)))], st["res"](i)))
            last = i
    forming, n = [], len(c) - 1
    if len(highs) >= 2 and len(lows) >= 2 and n - lastconf <= 60 and n >= 1 and np.isfinite(a[n]):
        st = _lines_state(highs, lows, n, a[n], mode, s)
        if st is not None and c[n] <= st["res"](n):
            forming.append(_g("", "forming", None, st["pts"], [((st["start"], st["res"](st["start"])), (n, st["res"](n))), ((st["start"], st["sup"](st["start"])), (n, st["sup"](n)))], st["res"](n)))
    return events, forming


# ------------------------------------------------------------------ 상승 깃발 (롱 기준)
def _flag(h, l, c, k=K, s=1.0):
    a, pv = atr(h, l, c), confirmed_pivots(h, l, k)
    highs, events, last, ptr = [], [], -10**9, 0
    def check(i, p):
        if not 8 <= i - p <= 40 or not np.isfinite(a[p]):
            return None
        lo0 = max(0, p - 12)
        impulse = h[p] - l[lo0:p + 1].min()
        if impulse < 4 / s * a[p]:
            return None
        retrace = (h[p] - l[p:i].min()) / impulse
        return impulse, retrace, lo0 + int(np.argmin(l[lo0:p + 1])) if retrace <= min(0.8, 0.5 * s) else None
    for i in range(1, len(c)):
        while ptr < len(pv) and pv[ptr][0] <= i:
            conf, p, t = pv[ptr]
            ptr += 1
            if t == "H":
                highs.append(p)
        if i - last <= COOLDOWN or not highs:
            continue
        for p in reversed(highs[-3:]):
            r = check(i, p)
            if r and r[2] is not None and c[i] > h[p] and c[i - 1] <= h[p]:
                events.append(_g("", "confirmed", i, [(r[2], l[r[2]]), (p, h[p])], [((p, h[p]), (i, h[p]))], h[p]))
                last = i
                break
    forming, n = [], len(c) - 1
    for p in reversed(highs[-3:]):
        r = check(n, p)
        if r and r[2] is not None and c[n] <= h[p]:
            forming.append(_g("", "forming", None, [(r[2], l[r[2]]), (p, h[p])], [((p, h[p]), (n, h[p]))], h[p]))
            break
    return events, forming


# ------------------------------------------------------------------ 박스권 (상향 돌파 기준)
def _range(h, l, c, n=48, s=1.0):
    a = atr(h, l, c)
    events, last = [], -10**9
    for i in range(n + 1, len(c)):
        if i - last <= COOLDOWN or not np.isfinite(a[i - 1]):
            continue
        hi, lo = h[i - n:i].max(), l[i - n:i].min()
        if hi - lo <= 5 * s * a[i - 1] and c[i] > hi and c[i - 1] <= hi:
            events.append(_g("", "confirmed", i, [], [((i - n, hi), (i, hi)), ((i - n, lo), (i, lo))], hi))
            last = i
    forming = []
    m = len(c)
    if m > n and np.isfinite(a[m - 1]):
        hi, lo = h[m - n:m].max(), l[m - n:m].min()
        if hi - lo <= 5 * s * a[m - 1] and c[m - 1] <= hi:
            forming.append(_g("", "forming", None, [], [((m - n, hi), (m - 1, hi)), ((m - n, lo), (m - 1, lo))], hi))
    return events, forming


def _bullish(code, h, l, c, k, s):
    return {"P0W": lambda: _double(h, l, c, k, s), "P1": lambda: _inv_hs(h, l, c, k, s), "P3": lambda: _lines(h, l, c, "tri", k, s),
            "P5": lambda: _lines(h, l, c, "wedge", k, s), "P7": lambda: _flag(h, l, c, k, s), "P9": lambda: _range(h, l, c, 48, s)}[code]()


def detect(pat: str, h, l, c, k: int = K, s: float = 1.0):
    """(확정 이벤트 목록, 형성 중 목록). 각 항목은 _g 도형 사전. 하락형은 가격을 뒤집어 같은 감지기를 쓴 뒤 가격을 되돌린다."""
    h, l, c = np.asarray(h, float), np.asarray(l, float), np.asarray(c, float)
    n = int(pat[1:]) if pat[1:].isdigit() else None
    if pat in ("P0W",) or (n is not None and n % 2 == 1):
        ev, fm = _bullish(pat, h, l, c, k, s)
        sign = 1
    else:
        base = "P0W" if pat == "P0M" else f"P{n - 1}"
        ev, fm = _bullish(base, -l, -h, -c, k, s)
        sign = -1
    out = []
    for lst in (ev, fm):
        for g in lst:
            g["pat"] = pat
            if sign < 0:
                g["points"] = [[i, -v] for i, v in g["points"]]
                g["lines"] = [[[a_, -b_], [c_, -d_]] for (a_, b_), (c_, d_) in g["lines"]]
                g["level"] = None if g["level"] is None else -g["level"]
            out.append(g)
    return [g for g in out if g["state"] == "confirmed"], [g for g in out if g["state"] == "forming"]


def event_indices(pat: str, h, l, c, k: int = K, s: float = 1.0) -> list[int]:
    return sorted(set(g["idx"] for g in detect(pat, h, l, c, k, s)[0]))


def scan(ts, h, l, c, recent: int = 100, pats=None):
    """차트 표시용: 최근 recent 봉 안의 확정 이벤트와 지금 형성 중인 패턴. ts 는 봉 시작 시각(ms)."""
    n = len(c)
    out = []
    for pat in pats or NAMES:
        ev, fm = detect(pat, h, l, c)
        for g in ev:
            if n - 1 - g["idx"] <= recent:
                out.append({**g, "bars_ago": n - 1 - g["idx"]})
        for g in fm:
            out.append({**g, "bars_ago": 0})
    for g in out:
        g["name"] = NAMES[g["pat"]]
        g["dir"] = DIR[g["pat"]]
        g["points"] = [[int(ts[i]), v] for i, v in g["points"]]
        g["lines"] = [[[int(ts[a]), b], [int(ts[c_]), d]] for (a, b), (c_, d) in g["lines"]]
        g["time"] = int(ts[g["idx"]]) if g["idx"] is not None else None
    out.sort(key=lambda g: (g["bars_ago"], g["pat"]))
    return out
