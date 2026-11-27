"""차트 패턴 2차 사건 연구: 헤드앤숄더 · 삼각 수렴 · 쐐기 · 깃발 · 박스권 돌파를 1시간봉·4시간봉에서 검증한다(읽기 전용).
정의·판정 기준은 ena_pattern_study2_prereg.md 에 결과를 보기 전에 고정했다. ena_pattern_study.py(1차)와 같은 폴더에 둔다.
실행(ENA 폴더에서):  .venv/bin/python ena_pattern_study2.py [--out 결과.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ena_pattern_study as p1  # noqa: E402

atr, confirmed_pivots, COST_RT, SPLIT_MS = p1.atr, p1.confirmed_pivots, p1.COST_RT, p1.SPLIT_MS
TESTS = 22
ALPHA_PCT = 100 * 0.05 / TESTS          # 보정한 단측 하한 분위(0.227)
COOLDOWN = 24
HORIZONS = {"1h": (6, 12, 24, 48), "4h": (2, 3, 6, 12)}
PRIMARY = {"1h": 24, "4h": 6}
NAMES = {"P0W": "쌍바닥 W", "P0M": "쌍봉 M", "P1": "역헤드앤숄더", "P2": "헤드앤숄더", "P3": "삼각 수렴 상향 돌파", "P4": "삼각 수렴 하향 이탈",
         "P5": "하락 쐐기 상향 돌파", "P6": "상승 쐐기 하향 이탈", "P7": "상승 깃발", "P8": "하락 깃발", "P9": "박스권 상향 돌파", "P10": "박스권 하향 이탈"}
SIGN = {"P0W": 1, "P0M": -1, "P1": 1, "P2": -1, "P3": 1, "P4": -1, "P5": 1, "P6": -1, "P7": 1, "P8": -1, "P9": 1, "P10": -1}


def _flip(h, l, c):
    return -l, -h, -c


# ------------------------------------------------------------------ 상승(롱) 쪽 감지기. 하락 쪽은 가격을 뒤집어서 같은 코드를 쓴다.
def det_inv_hs(h, l, c, k=4, s=1.0):
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
                                armed.append({"q1": q1, "v1": v1, "q2": q2, "v2": v2, "bad": vb - 0.5 * A, "until": conf + 60, "from": conf})
        keep = []
        for ar in armed:
            if i > ar["until"] or c[i] < ar["bad"]:
                continue
            neck = ar["v1"] + (ar["v2"] - ar["v1"]) * (i - ar["q1"]) / (ar["q2"] - ar["q1"])
            if i > ar["from"] and c[i] > neck:
                if i - last > COOLDOWN:
                    events.append(i)
                    last = i
                continue            # 한 번 넘으면 이 패턴은 소진(쿨다운 중이어도 다시 쓰지 않는다)
            keep.append(ar)
        armed = keep
    return events


def det_lines(h, l, c, mode: str, k=4, s=1.0):
    """mode='tri' 삼각 수렴(저항 기울기 ≤ 0 ≤ 지지 기울기), 'wedge' 하락 쐐기(저항·지지 모두 하락, 저항이 더 가파름). 상향 돌파 신호."""
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
        (p1_, v1), (p2_, v2) = highs[-2:]
        (q1, w1), (q2, w2) = lows[-2:]
        start, end = min(p1_, q1), max(p2_, q2)
        if end - start > 150 or i - end > 80:
            continue
        rs, ss, A = (v2 - v1) / (p2_ - p1_), (w2 - w1) / (q2 - q1), a[i]
        if not rs < ss:
            continue
        if mode == "tri" and not (rs <= 0 <= ss):
            continue
        if mode == "wedge" and not (rs < 0 and ss <= -0.03 / s * A):
            continue
        res = lambda t_: v2 + rs * (t_ - p2_)
        sup = lambda t_: w2 + ss * (t_ - q2)
        width_i = res(i) - sup(i)
        width_s = (v1 + rs * (start - p1_)) - (w1 + ss * (start - q1))
        if width_i < 1.5 / s * A or width_s < 1.6 * width_i:
            continue
        if c[i] > res(i) and c[i - 1] <= res(i - 1) and c[i] >= sup(i):
            events.append(i)
            last = i
    return events


def det_flag(h, l, c, k=4, s=1.0):
    a, pv = atr(h, l, c), confirmed_pivots(h, l, k)
    highs, events, last, ptr = [], [], -10**9, 0
    for i in range(1, len(c)):
        while ptr < len(pv) and pv[ptr][0] <= i:
            conf, p, t = pv[ptr]
            ptr += 1
            if t == "H":
                highs.append(p)
        if i - last <= COOLDOWN or not highs:
            continue
        for p in reversed(highs[-3:]):
            if not 8 <= i - p <= 40 or not np.isfinite(a[p]):
                continue
            impulse = h[p] - l[max(0, p - 12):p + 1].min()
            if impulse < 4 / s * a[p]:
                continue
            retrace = (h[p] - l[p:i].min()) / impulse
            if retrace <= min(0.8, 0.5 * s) and c[i] > h[p] and c[i - 1] <= h[p]:
                events.append(i)
                last = i
                break
    return events


def det_range(h, l, c, n=48, s=1.0):
    a = atr(h, l, c)
    events, last = [], -10**9
    for i in range(n + 1, len(c)):
        if i - last <= COOLDOWN or not np.isfinite(a[i - 1]):
            continue
        hi, lo = h[i - n:i].max(), l[i - n:i].min()
        if hi - lo <= 5 * s * a[i - 1] and c[i] > hi and c[i - 1] <= hi:
            events.append(i)
            last = i
    return events


def bullish_events(code: str, h, l, c, k=4, s=1.0):
    return {"P1": lambda: det_inv_hs(h, l, c, k, s), "P3": lambda: det_lines(h, l, c, "tri", k, s), "P5": lambda: det_lines(h, l, c, "wedge", k, s),
            "P7": lambda: det_flag(h, l, c, k, s), "P9": lambda: det_range(h, l, c, 48, s)}[code]()


def events_for(pat: str, h, l, c, k: int = 4, s: float = 1.0) -> list[int]:
    if pat == "P0W":
        return p1.find_events(h, l, c, "W", k, s)
    if pat == "P0M":
        return p1.find_events(h, l, c, "M", k, s)
    n = int(pat[1:])
    if n % 2 == 1:                                  # 홀수 = 상승(롱)
        return bullish_events(pat, h, l, c, k, s)
    fh, fl, fc = _flip(h, l, c)                     # 짝수 = 하락(숏): 뒤집은 가격에서 같은 감지기를 쓴다
    return bullish_events(f"P{n - 1}", fh, fl, fc, k, s)


# ------------------------------------------------------------------ 평가
def resample4(a: np.ndarray) -> np.ndarray:
    ms = 4 * 3_600_000
    g = (a[:, 0] // ms).astype(np.int64)
    out = []
    for key in np.unique(g):
        x = a[g == key]
        if len(x) == 4 and np.array_equal(x[:, 0], key * ms + np.arange(4) * 3_600_000):
            out.append([x[0, 0], x[0, 1], x[:, 2].max(), x[:, 3].min(), x[-1, 4], x[:, 5].sum()])
    return np.array(out, dtype=float)


def patterns_for(tf: str):
    pats = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]
    return (["P0W", "P0M"] + pats) if tf == "4h" else pats


def study_tf(data: dict, tf: str, pats=None, iters=5000, alpha_pct=ALPHA_PCT) -> dict:
    out = {}
    hz = HORIZONS[tf]
    for pat in pats or patterns_for(tf):
        sign = SIGN[pat]
        rows = {hh: [] for hh in hz}
        n_ev = 0
        for sym, a in data.items():
            ts, h, l, c = a[:, 0], a[:, 2], a[:, 3], a[:, 4]
            idx = events_for(pat, h, l, c)
            n_ev += len(idx)
            for hh in hz:
                base = float(np.mean(sign * (c[hh:] / c[:-hh] - 1))) if len(c) > hh else 0.0
                for i in idx:
                    if i + hh < len(c):
                        r = sign * (c[i + hh] / c[i] - 1)
                        rows[hh].append((ts[i], r - base - COST_RT, r - base))
        res = {"events": n_ev, "h": {}}
        for hh in hz:
            if not rows[hh]:
                continue
            arr = np.array(rows[hh], dtype=float)
            res["h"][hh] = {"n": len(arr), "mean_net_pct": float(arr[:, 1].mean() * 100)}
        pr = PRIMARY[tf]
        if pr in res["h"] and res["h"][pr]["n"] >= 2:
            arr = np.array(rows[pr], dtype=float)
            week = (arr[:, 0] // (7 * 86_400_000)).astype(int)
            lo, _ = p1.boot_ci(week, arr[:, 1], iters=iters, lo=alpha_pct, hi=99.0)
            pre, post = arr[arr[:, 0] < SPLIT_MS, 1], arr[arr[:, 0] >= SPLIT_MS, 1]
            p = res["h"][pr]
            p.update({"ci_low_pct": lo * 100, "pre2024_pct": float(pre.mean() * 100) if len(pre) else None,
                      "post2024_pct": float(post.mean() * 100) if len(post) else None, "n_pre": int(len(pre)), "n_post": int(len(post)),
                      "reverse_net_pct": float((-arr[:, 2] - COST_RT).mean() * 100)})
            ok = bool(p["n"] >= 100 and p["ci_low_pct"] > 0 and (p["pre2024_pct"] or 0) > 0 and (p["post2024_pct"] or 0) > 0)
            res["pass"] = ok
            res["verdict"] = "합격(전진검증 대상)" if ok else ("이벤트 부족" if p["n"] < 100 else "근거 없음 — 폐기")
        else:
            res["pass"], res["verdict"] = False, "이벤트 부족"
        out[pat] = res
    return out


def render(results: dict) -> str:
    L = [f"차트 패턴 2차 사건 연구 (비용 0.15% 차감 · 같은 코인 기준 수익 제외 · 시험 {TESTS}건 보정: 신뢰구간 하한 {ALPHA_PCT:.2f} 분위)", ""]
    passed = []
    for tf, res in results.items():
        pr = PRIMARY[tf]
        L.append(f"[{tf}] 1차 판정 = {pr}봉 뒤")
        L.append(f"  {'패턴':18s} {'이벤트':>6s} {'비용후 초과%':>11s} {'하한%':>7s} {'2024전':>7s} {'2024후':>7s} {'역방향(참고)':>12s}  판정")
        for pat, r in res.items():
            p = r["h"].get(pr)
            if not p or "ci_low_pct" not in p:
                L.append(f"  {NAMES[pat]:18s} {r['events']:6d}  (표본 부족)")
                continue
            f = lambda v: "  —  " if v is None else f"{v:+7.2f}"
            L.append(f"  {NAMES[pat]:18s} {r['events']:6d} {p['mean_net_pct']:+11.2f} {p['ci_low_pct']:+7.2f} {f(p['pre2024_pct'])} {f(p['post2024_pct'])} {p['reverse_net_pct']:+12.2f}  {r['verdict']}")
            if r["pass"]:
                passed.append(f"{tf} {NAMES[pat]}")
        L.append("")
    L.append("합격: " + (", ".join(passed) if passed else "없음 — 모두 폐기"))
    L.append("읽는 법: 합격은 이벤트 100개 이상 + 보정된 신뢰구간 하한 > 0 + 2024년 전·후 모두 양수여야 합니다. 역방향 열은 탐색용이며 판정에 쓰지 않습니다.")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default="data/learn_cache")
    ap.add_argument("--out")
    ap.add_argument("--iters", type=int, default=5000)
    a = ap.parse_args(argv)
    data1 = p1.load(Path(a.cache))
    print(f"코인 {len(data1)}개 로드", flush=True)
    data4 = {s: resample4(v) for s, v in data1.items()}
    results = {}
    for tf, data in (("1h", data1), ("4h", data4)):
        print(f"{tf} 시험 중…", flush=True)
        results[tf] = study_tf(data, tf, iters=a.iters)
    print(render(results))
    if a.out:
        Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
