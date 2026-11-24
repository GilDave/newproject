"""차트 구조 자동 작도: 스윙(지그재그) · 추세선 · 엘리엇 5파 후보. 순수 계산(파일·네트워크 접근 없음).

이 모듈의 결과는 '작도 보조'다. 파동을 세는 방식은 사람마다 달라서 같은 차트에 여러 카운트가 규칙을 통과할 수 있고, 어느 쪽도 앞으로의 가격을
보장하지 않는다. 그래서 규칙을 통과한 후보 하나와 그 무효화 가격만 내놓고, 통과하는 구조가 없으면 '없음'이라고 답한다.
tools/structure_chart.js 가 같은 규칙의 JavaScript 구현이고, tests 에서 같은 데이터로 결과를 대조한다(규칙을 바꾸면 둘 다 바꾼다).
"""

from __future__ import annotations

ATR_N = 14
MIN_PCT = 0.004
SCALES = (2.0, 3.0, 4.5)             # 엘리엇은 크기(스케일)가 겹쳐 나타나므로 여러 스케일에서 찾는다
RETRACE2 = (0.382, 0.786)
RETRACE4 = (0.236, 0.5)
EXT3 = 1.5
W5_REL = (0.5, 1.2)
ALTERNATION = 0.15
MIN_WAVE_ATR = 1.5                    # 1파가 이보다 작으면 잡음으로 보고 건너뜀
MAX_LINE_DIST_ATR = 8.0               # 추세선이 현재가에서 이보다(ATR 배수) 멀면 그리지 않는다


def atr(h: list, l: list, c: list, n: int = ATR_N) -> list:
    """Wilder 평활 ATR. 앞쪽 값도 있는 만큼으로 채운다."""
    out = []
    prev = c[0]
    for i in range(len(c)):
        tr = max(h[i] - l[i], abs(h[i] - prev), abs(l[i] - prev))
        out.append(tr if i == 0 else (out[-1] * (n - 1) + tr) / n)
        prev = c[i]
    return out


def zigzag(h: list, l: list, c: list, atr_mult: float = 3.0, min_pct: float = MIN_PCT, atr_n: int = ATR_N) -> list:
    """ATR의 atr_mult배(또는 가격의 min_pct) 이상 되돌린 지점에서 스윙을 확정한다. 마지막 미확정 극점도 provisional=True 로 돌려준다."""
    n = len(c)
    a = atr(h, l, c, atr_n)
    piv: list = []
    direction = 0
    hi, hi_i, lo, lo_i = h[0], 0, l[0], 0
    for i in range(1, n):
        thr = max(atr_mult * a[i], min_pct * c[i])
        if direction == 0:
            if h[i] > hi:
                hi, hi_i = h[i], i
            if l[i] < lo:
                lo, lo_i = l[i], i
            if h[i] - lo >= thr and lo_i < i:
                piv.append({"i": lo_i, "price": lo, "kind": "L", "provisional": False})
                direction, hi, hi_i = 1, h[i], i
            elif hi - l[i] >= thr and hi_i < i:
                piv.append({"i": hi_i, "price": hi, "kind": "H", "provisional": False})
                direction, lo, lo_i = -1, l[i], i
        elif direction == 1:
            if h[i] > hi:
                hi, hi_i = h[i], i
            elif hi - l[i] >= thr:
                piv.append({"i": hi_i, "price": hi, "kind": "H", "provisional": False})
                direction, lo, lo_i = -1, l[i], i
        else:
            if l[i] < lo:
                lo, lo_i = l[i], i
            elif h[i] - lo >= thr:
                piv.append({"i": lo_i, "price": lo, "kind": "L", "provisional": False})
                direction, hi, hi_i = 1, h[i], i
    if direction == 1:
        piv.append({"i": hi_i, "price": hi, "kind": "H", "provisional": True})
    elif direction == -1:
        piv.append({"i": lo_i, "price": lo, "kind": "L", "provisional": True})
    return piv


def trendlines(pivots: list, h: list, l: list, c: list, a: list, lookback: int = 8, tol_mult: float = 0.5, min_gap: int = 5) -> dict:
    """확정 스윙 저점(지지)·고점(저항)을 두 개씩 이은 선 중 종가가 선을 tol 이상 벗어나지 않은 것에서, 닿은 횟수가 많고 최근인 것 하나씩."""
    n = len(c)
    out = {"support": None, "resistance": None}
    for name, kind in (("support", "L"), ("resistance", "H")):
        pts = [p for p in pivots if p["kind"] == kind and not p["provisional"]][-lookback:]
        best = None
        for x in range(len(pts)):
            for y in range(x + 1, len(pts)):
                p, q = pts[x], pts[y]
                if q["i"] - p["i"] < min_gap:
                    continue
                slope = (q["price"] - p["price"]) / (q["i"] - p["i"])
                total = 0.0
                for j in range(p["i"], q["i"] + 1):      # 합계는 JavaScript 구현과 같은 순서의 반복문(파이썬 3.12의 보정 합계와 어긋나지 않게)
                    total += a[j]
                tol = tol_mult * (total / (q["i"] - p["i"] + 1))
                ok = True
                for j in range(p["i"], n):
                    v = p["price"] + slope * (j - p["i"])
                    if (kind == "L" and c[j] < v - tol) or (kind == "H" and c[j] > v + tol):
                        ok = False
                        break
                if not ok:
                    continue
                last = p["price"] + slope * (n - 1 - p["i"])
                if abs(last - c[-1]) > MAX_LINE_DIST_ATR * a[-1]:
                    continue
                touches = sum(1 for r in pts if r["i"] >= p["i"] and abs(r["price"] - (p["price"] + slope * (r["i"] - p["i"]))) <= tol)
                key = (touches, q["i"], q["i"] - p["i"])
                if best is None or key > best[0]:
                    best = (key, {"a": {"i": p["i"], "price": p["price"]}, "b": {"i": q["i"], "price": q["price"]}, "slope": slope, "touches": touches, "last": last})
        out[name] = best[1] if best else None
    return out


def _check_up(q: list) -> dict | None:
    """위로 향하는 5파(q = [p0 저, p1 고, p2 저, p3 고, p4 저(, p5 고)] 가격). 아래 방향은 가격에 -1을 곱해 같은 함수를 쓴다."""
    w1, w2, w3, w4 = q[1] - q[0], q[1] - q[2], q[3] - q[2], q[3] - q[4]
    if min(w1, w2, w3, w4) <= 0:
        return None
    complete = len(q) == 6
    rules = [("2파가 1파 시작점을 넘지 않음", q[2] > q[0]), ("3파가 1파 끝을 넘음", q[3] > q[1]), ("4파가 1파 영역과 겹치지 않음", q[4] > q[1])]
    w5 = 0.0
    if complete:
        w5 = q[5] - q[4]
        if w5 <= 0:
            return None
        rules.append(("3파가 가장 짧지 않음", not (w3 < w1 and w3 < w5)))
    else:
        rules.append(("3파가 1파보다 길다", w3 >= w1))
    if not all(ok for _, ok in rules):
        return None
    r2, r4 = w2 / w1, w4 / w3
    guides = [("2파 되돌림 38~79%", RETRACE2[0] <= r2 <= RETRACE2[1]), ("3파가 1파의 1.5배 이상", w3 >= EXT3 * w1),
              ("4파 되돌림 24~50%", RETRACE4[0] <= r4 <= RETRACE4[1]), ("2파·4파 깊이가 다름", abs(r2 - r4) >= ALTERNATION)]
    if complete:
        guides.append(("5파가 1파의 50~120%", W5_REL[0] <= w5 / w1 <= W5_REL[1]))
    return {"w1": w1, "rules": rules, "guides": guides, "score": sum(1 for _, ok in guides if ok) / len(guides), "complete": complete}


def elliott(pivots_by_scale: dict, a: list, n: int) -> dict:
    """스케일별 스윙에서 마지막 극점 근처(마지막 3개 중 하나)에서 끝나는 5파(진행 중 4파까지 포함)를 찾고, 필수 규칙을 모두 통과한 것 중 가장 좋은 하나."""
    best = None
    found = 0
    for scale, pivots in pivots_by_scale.items():
        for offset in (0, 1, 2):
            for length in (6, 5):
                end = len(pivots) - offset
                start = end - length
                if start < 0:
                    continue
                seq = pivots[start:end]
                if any(seq[k]["kind"] == seq[k + 1]["kind"] for k in range(len(seq) - 1)):
                    continue
                up = seq[0]["kind"] == "L"
                q = [p["price"] if up else -p["price"] for p in seq]
                res = _check_up(q)
                if res is None or res["w1"] < MIN_WAVE_ATR * a[seq[1]["i"]]:
                    continue
                found += 1
                key = (-offset, res["score"], res["complete"], scale)
                if best is None or key > best[0]:
                    best = (key, scale, seq, up, q, res)
    if best is None:
        return {"status": "none", "reason": "필수 규칙을 통과하는 5파 구조가 없음", "alternatives": 0}
    _, scale, seq, up, q, res = best
    sgn = 1.0 if up else -1.0
    w1 = res["w1"]
    forming5 = res["complete"] and seq[5]["provisional"]        # 5번째 극점이 아직 확정 전(현재 봉이 계속 극점을 갱신 중) = 5파 진행 중
    complete = res["complete"] and not forming5
    if complete:
        inval = {"level": seq[5]["price"], "side": "above" if up else "below", "why": "5파가 연장돼 현재 카운트가 틀림(새 " + ("고점" if up else "저점") + ")"}
        targets = []
    elif forming5:
        # 마지막 극점은 지금 계속 움직이므로 그것을 무효화 가격으로 삼지 않는다: 5파의 시작점(4파 끝)으로 되돌아가면 5파 카운트가 무효
        inval = {"level": seq[4]["price"], "side": "below" if up else "above", "why": "5파 시작점(4파 끝)으로 되돌아가 5파 카운트가 무효"}
        targets = [sgn * (q[4] + 0.618 * w1), sgn * (q[4] + 1.0 * w1)]
    else:
        inval = {"level": seq[1]["price"], "side": "below" if up else "above", "why": "4파가 1파 영역을 침범해 5파 카운트가 무효"}
        targets = [sgn * (q[4] + 0.618 * w1), sgn * (q[4] + 1.0 * w1)]
    return {"status": "ok", "direction": "up" if up else "down", "scale": scale, "complete": complete, "forming5": forming5, "score": res["score"],
            "rules": [name for name, _ in res["rules"]], "hits": [name for name, ok in res["guides"] if ok], "misses": [name for name, ok in res["guides"] if not ok],
            "pivots": [{"i": p["i"], "price": p["price"], "kind": p["kind"], "provisional": p["provisional"], "label": str(k)} for k, p in enumerate(seq)],
            "invalidation": inval, "targets": targets, "alternatives": found - 1}


def analyze(o: list, h: list, l: list, c: list, scales: tuple = SCALES) -> dict:
    """한 번에: 스윙(스케일 3.0), 추세선, 엘리엇 후보. 입력은 파이썬 리스트(길이 같음). 봉이 너무 적으면 빈 결과."""
    n = len(c)
    if n < 60:
        return {"pivots": [], "trendlines": {"support": None, "resistance": None}, "elliott": {"status": "none", "reason": "봉이 너무 적음", "alternatives": 0}}
    a = atr(h, l, c)
    by_scale = {s: zigzag(h, l, c, s) for s in scales}
    main = by_scale[3.0] if 3.0 in by_scale else by_scale[scales[len(scales) // 2]]
    return {"pivots": main, "trendlines": trendlines(main, h, l, c, a), "elliott": elliott(by_scale, a, n)}
