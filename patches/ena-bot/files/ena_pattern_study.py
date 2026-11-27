"""차트 패턴(쌍바닥 W · 쌍봉 M) 목선 돌파의 사후 수익률을 18개 코인 1시간봉으로 검증한다(읽기 전용).
정의·판정 기준은 ena_pattern_study_prereg.md 에 결과를 보기 전에 고정했다.
실행(ENA 폴더에서):  .venv/bin/python ena_pattern_study.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

K = 4                      # 피벗 좌우 봉 수
HORIZONS = (6, 12, 24, 48)
PRIMARY = 24
COST_RT = 2 * (0.00055 + 0.0002)   # 왕복 비용
SPLIT_MS = 1_704_067_200_000       # 2024-01-01 UTC
SYMBOLS = ["ADAUSDT", "ARBUSDT", "AVAXUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT", "DOTUSDT", "ENAUSDT", "ETHUSDT",
           "INJUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SOLUSDT", "SUIUSDT", "TIAUSDT", "XRPUSDT"]


def atr(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
    out = np.full(len(c), np.nan)
    if len(c) >= n:
        cs = np.cumsum(np.insert(tr, 0, 0.0))
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def confirmed_pivots(h, l, k=K):
    """[(확정 인덱스, 피벗 인덱스, 'L'|'H')]. 피벗은 우측 k봉이 지난 뒤 확정된다."""
    out = []
    for p in range(k, len(h) - k):
        if l[p] < l[p - k:p].min() and l[p] <= l[p + 1:p + k + 1].min():
            out.append((p + k, p, "L"))
        if h[p] > h[p - k:p].max() and h[p] >= h[p + 1:p + k + 1].max():
            out.append((p + k, p, "H"))
    out.sort()
    return out


def find_events(h, l, c, kind: str) -> list[int]:
    """kind='W'(롱 신호) 또는 'M'(숏 신호). 돌파가 일어난 봉의 인덱스 목록(그 봉 종가에 진입)."""
    a = atr(h, l, c)
    sign = 1 if kind == "W" else -1
    ext, opp = ("L", "H") if kind == "W" else ("H", "L")
    px = l if kind == "W" else h          # 바닥(꼭대기) 가격
    pv = confirmed_pivots(h, l)
    events, armed, last_ext, seen_opp = [], [], None, []   # armed: dict(neck, lo, until, start)
    ptr = 0
    for i in range(len(c)):
        while ptr < len(pv) and pv[ptr][0] <= i:
            conf, p, t = pv[ptr]
            ptr += 1
            if t == opp:
                seen_opp.append((p, h[p] if kind == "W" else l[p]))
            elif t == ext:
                if last_ext is not None and np.isfinite(a[conf]):
                    p1 = last_ext
                    gap = p - p1
                    mids = [v for q, v in seen_opp if p1 < q < p]
                    if 8 <= gap <= 72 and mids:
                        neck = max(mids) if kind == "W" else min(mids)
                        e1, e2 = px[p1], px[p]
                        near = max(e1, e2) if kind == "W" else min(e1, e2)
                        if abs(e1 - e2) <= 0.6 * a[conf] and sign * (neck - near) >= 2 * a[conf]:
                            armed.append({"neck": neck, "bad": (min(e1, e2) - 0.5 * a[conf]) if kind == "W" else (max(e1, e2) + 0.5 * a[conf]),
                                          "until": conf + 48, "from": conf})
                last_ext = p
        keep = []
        for ar in armed:
            if i > ar["until"] or sign * (c[i] - ar["bad"]) < 0:
                continue                                  # 기간 만료 또는 무효(저점/고점 밖으로 이탈)
            if i > ar["from"] and sign * (c[i] - ar["neck"]) > 0:
                events.append(i)
                continue                                  # 한 번만 발동
            keep.append(ar)
        armed = keep
    return sorted(set(events))


def forward(c, idx, sign):
    out = {h: [] for h in HORIZONS}
    for i in idx:
        for hh in HORIZONS:
            if i + hh < len(c):
                out[hh].append((i, sign * (c[i + hh] / c[i] - 1)))
    return out


def boot_ci(week, x, iters=2000, lo=1.25, hi=98.75, seed=0):
    u = np.unique(week)
    idx = [np.where(week == w)[0] for w in u]
    rng = np.random.default_rng(seed)
    m = [x[np.concatenate([idx[j] for j in rng.integers(0, len(u), len(u))])].mean() for _ in range(iters)]
    return float(np.percentile(m, lo)), float(np.percentile(m, hi))


def study(data: dict, kinds=("W", "M")) -> dict:
    """data: {심볼: 1시간봉 배열[ts,o,h,l,c,v]}"""
    res = {}
    for kind in kinds:
        sign = 1 if kind == "W" else -1
        rows = {hh: [] for hh in HORIZONS}     # (ts, 초과수익_총, 이벤트수익, 코인기준)
        n_events = 0
        for sym, a in data.items():
            ts, h, l, c = a[:, 0], a[:, 2], a[:, 3], a[:, 4]
            idx = find_events(h, l, c, kind)
            n_events += len(idx)
            fw = forward(c, idx, sign)
            for hh in HORIZONS:
                base = float(np.mean(sign * (c[hh:] / c[:-hh] - 1))) if len(c) > hh else 0.0
                for i, r in fw[hh]:
                    rows[hh].append((ts[i], r - base - COST_RT, r, base))
        res[kind] = {"events": n_events, "h": {}}
        for hh in HORIZONS:
            if not rows[hh]:
                continue
            arr = np.array(rows[hh], dtype=float)
            ex = arr[:, 1]
            week = (arr[:, 0] // (7 * 86_400_000)).astype(int)
            lo, hi = boot_ci(week, ex)
            pre, post = ex[arr[:, 0] < SPLIT_MS], ex[arr[:, 0] >= SPLIT_MS]
            res[kind]["h"][hh] = {"n": len(ex), "mean_net_excess_pct": float(ex.mean() * 100), "ci_pct": [lo * 100, hi * 100],
                                  "hit_rate_pct": float((arr[:, 2] > 0).mean() * 100), "base_hit_pct": None,
                                  "pre2024_pct": float(pre.mean() * 100) if len(pre) else None, "post2024_pct": float(post.mean() * 100) if len(post) else None,
                                  "n_pre": int(len(pre)), "n_post": int(len(post))}
        p = res[kind]["h"].get(PRIMARY)
        ok = bool(p and p["n"] >= 100 and p["ci_pct"][0] > 0 and (p["pre2024_pct"] or 0) > 0 and (p["post2024_pct"] or 0) > 0)
        res[kind]["pass"] = ok
        res[kind]["verdict"] = "합격(전진검증 대상)" if ok else ("이벤트 부족" if not p or p["n"] < 100 else "근거 없음 — 폐기")
    return res


def render(res: dict) -> str:
    L = ["패턴 사건 연구 (비용 0.15% 차감 · 같은 코인 기준 수익 제외 · 주 단위 부트스트랩)", ""]
    name = {"W": "쌍바닥 W (돌파 시 롱)", "M": "쌍봉 M (이탈 시 숏)"}
    for kind, r in res.items():
        L.append(f"[{name[kind]}] 이벤트 {r['events']}개 → {r['verdict']}")
        L.append(f"  {'선행':>6s} {'표본':>6s} {'비용후 초과(%)':>14s} {'신뢰구간(%)':>18s} {'승률(%)':>8s} {'2024전':>8s} {'2024후':>8s}")
        for hh, x in r["h"].items():
            mark = " ← 1차 판정" if hh == PRIMARY else ""
            f = lambda v: "   —  " if v is None else f"{v:+7.2f}"
            L.append(f"  {hh:>4d}봉 {x['n']:6d} {x['mean_net_excess_pct']:+14.2f} [{x['ci_pct'][0]:+7.2f},{x['ci_pct'][1]:+7.2f}] {x['hit_rate_pct']:8.1f} {f(x['pre2024_pct']):>8s} {f(x['post2024_pct']):>8s}{mark}")
        L.append("")
    L.append("읽는 법: 1차 판정은 24봉 하나만 씁니다(나머지는 참고). 신뢰구간 하한이 0 위이고 2024년 전·후 모두 양수여야 합격입니다.")
    return "\n".join(L)


def load(cache: Path) -> dict:
    out = {}
    for s in SYMBOLS:
        f = cache / f"{s}_k60.npy"
        if f.exists():
            a = np.load(f)
            if len(a) > 24 * 60:
                out[s] = a
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default="data/learn_cache")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    data = load(Path(a.cache))
    print(f"코인 {len(data)}개 로드")
    res = study(data)
    print(render(res))
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
