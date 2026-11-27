"""패턴 특징을 v2 학습기(learn2)의 입력에 추가했을 때 표본 외 예측이 좋아지는지 시험한다(읽기 전용, 학습기·설정·DB 는 건드리지 않는다).
규칙·판정은 ena_round5_prereg.md 에 결과를 보기 전에 고정했다. 같은 폴더의 ena_pattern_study.py · ena_pattern_study2.py 가 필요하다.
실행(ENA 폴더에서):  .venv/bin/python ena_pattern_learn.py [--out 결과.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ena_pattern_study2 as p2  # noqa: E402
from ena_bot import learn2  # noqa: E402
from ena_bot.featx import BAR_MS  # noqa: E402
from ena_bot.learn_data import DEFAULT_SYMBOLS, load_coin  # noqa: E402

PATS = ["P0W", "P0M"] + [f"P{i}" for i in range(1, 11)]
DIRS = np.array([p2.SIGN[p] for p in PATS], dtype=float)
WIN_A, WIN_B = 6, 18
FEAT_NAMES = [f"pat_{p}" for p in PATS] + ["pat_net6", "pat_net18"]
MIN_D_AUC, MIN_FOLD_SHARE, BOOT_ITERS = 0.003, 0.60, 500


def window_max(a: np.ndarray, w: int) -> np.ndarray:
    """a[i-w+1..i] 중 하나라도 1 이면 1 (과거 w봉, 현재 봉 포함). 미래 값은 쓰지 않는다."""
    cs = np.cumsum(a)
    prev = np.r_[np.zeros(w), cs[:-w]][:len(a)] if len(a) > w else np.zeros(len(a))
    return ((cs - prev) > 0).astype(float)


def pattern_features(bars: np.ndarray, d: np.ndarray) -> np.ndarray:
    """4시간봉 배열 [ts,o,h,l,c,v] 과 추세 방향 d(−1/0/+1) → (봉 수 × 14) 특징."""
    h, l, c = bars[:, 2], bars[:, 3], bars[:, 4]
    n = len(c)
    fired = np.zeros((n, len(PATS)))
    for k, p in enumerate(PATS):
        for i in p2.events_for(p, h, l, c):
            fired[i, k] = 1.0
    recent6 = np.column_stack([window_max(fired[:, k], WIN_A) for k in range(len(PATS))])
    net6 = (np.column_stack([np.convolve(fired[:, k], np.ones(WIN_A), "full")[:n] for k in range(len(PATS))]) * DIRS).sum(1)
    net18 = (np.column_stack([np.convolve(fired[:, k], np.ones(WIN_B), "full")[:n] for k in range(len(PATS))]) * DIRS).sum(1)
    X = np.column_stack([recent6 * DIRS, net6, net18])
    return X * d[:, None]


def dataset_with_patterns(pool: dict, ds: dict) -> np.ndarray:
    """ds 의 각 행(코인 j, 봉 i)에 해당하는 패턴 특징 행렬."""
    PX = np.zeros((len(ds["y"]), len(FEAT_NAMES)))
    for j, s in enumerate(ds["syms"]):
        m = ds["coin"] == j
        if not m.any():
            continue
        F = pattern_features(pool[s].bars, ds["per"][s]["d"])
        PX[m] = F[ds["idx"][m]]
    return PX


def paired_auc_boot(y, p_a, p_b, week, iters=BOOT_ITERS, seed=0):
    u = np.unique(week)
    idx = [np.where(week == w)[0] for w in u]
    rng = np.random.default_rng(seed)
    d = []
    for _ in range(iters):
        sel = np.concatenate([idx[i] for i in rng.integers(0, len(u), len(u))])
        a, b = learn2.auc_score(y[sel], p_a[sel]), learn2.auc_score(y[sel], p_b[sel])
        if np.isfinite(a) and np.isfinite(b):
            d.append(b - a)
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def compare(ds: dict, PX: np.ndarray, seed: int = 0, iters: int = BOOT_ITERS) -> dict:
    base = learn2.walk_forward(ds)
    ds_p = dict(ds, X=np.hstack([ds["X"], PX]))
    pat = learn2.walk_forward(ds_p)
    rng = np.random.default_rng(seed)
    ds_c = dict(ds, X=np.hstack([ds["X"], PX[rng.permutation(len(PX))]]))
    ctl = learn2.walk_forward(ds_c)
    t = base["tested"] & pat["tested"]
    y, week = ds["y"][t], ds["week"][t]
    auc_b, auc_p, auc_c = (learn2.auc_score(y, r["preds"][t]) for r in (base, pat, ctl))
    lo, hi = paired_auc_boot(y, base["preds"][t], pat["preds"][t], week, iters, seed)
    fb = {f["year"]: f for f in base["folds"]}
    folds = [{"year": f["year"], "n": f["n"], "auc_base": fb[f["year"]]["auc"], "auc_pat": f["auc"], "d": f["auc"] - fb[f["year"]]["auc"]}
             for f in pat["folds"] if f["year"] in fb]
    share = float(np.mean([f["d"] > 0 for f in folds])) if folds else 0.0
    d_auc, d_ctl = auc_p - auc_b, auc_c - auc_b
    checks = {"dAUC>=+0.003": d_auc >= MIN_D_AUC, "CI하한>0": lo > 0, "폴드60%이상양수": share >= MIN_FOLD_SHARE, "대조군|ΔAUC|<실제": abs(d_ctl) < d_auc}
    ok = all(checks.values())
    return {"n_test": int(t.sum()), "auc_base": auc_b, "auc_pat": auc_p, "auc_ctl": auc_c, "d_auc": d_auc, "d_auc_ctl": d_ctl, "d_auc_ci": [lo, hi],
            "edge_base": (base.get("pooled") or {}).get("edge"), "edge_pat": (pat.get("pooled") or {}).get("edge"),
            "edge_ci_base": (base.get("pooled") or {}).get("edge_ci"), "edge_ci_pat": (pat.get("pooled") or {}).get("edge_ci"),
            "folds": folds, "fold_positive_share": share, "checks": {k: bool(v) for k, v in checks.items()}, "pass": bool(ok),
            "verdict": "패턴 특징이 유효(그림자 전진검증 대상)" if ok else "근거 없음 — 학습기에 넣지 않는다",
            "pattern_rows_with_event_pct": float((np.abs(PX[:, :12]).sum(1) > 0).mean() * 100)}


def render(r: dict) -> str:
    f = lambda v: "  —  " if v is None else f"{v:+.4f}"
    L = [f"패턴 특징 14개 추가 시험 (표본 외 {r['n_test']:,}행 · 패턴 이벤트가 있는 행 {r['pattern_rows_with_event_pct']:.1f}%)", "",
         f"  AUC  기준 {r['auc_base']:.4f} → 패턴 {r['auc_pat']:.4f}  (ΔAUC {r['d_auc']:+.4f}, 95% 구간 [{r['d_auc_ci'][0]:+.4f}, {r['d_auc_ci'][1]:+.4f}])",
         f"  음성 대조군(패턴 열을 섞음): AUC {r['auc_ctl']:.4f} (ΔAUC {r['d_auc_ctl']:+.4f})",
         f"  정확도 우위(edge) 기준 {f(r['edge_base'])} → 패턴 {f(r['edge_pat'])}", "", "  연도별 AUC (기준 → 패턴):"]
    for x in r["folds"]:
        L.append(f"    {x['year']}  n={x['n']:6d}  {x['auc_base']:.4f} → {x['auc_pat']:.4f}  ({x['d']:+.4f})")
    L.append(f"  ΔAUC > 0 인 연도 비율 {r['fold_positive_share'] * 100:.0f}% (기준 60%)")
    L.append("  판정 항목: " + " · ".join(f"{k} {'통과' if v else '실패'}" for k, v in r["checks"].items()))
    L += ["", f"결론: {r['verdict']}"]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default="data/learn_cache")
    ap.add_argument("--out")
    ap.add_argument("--iters", type=int, default=BOOT_ITERS)
    a = ap.parse_args(argv)
    pool = {s: load_coin(s, Path(a.cache)) for s in DEFAULT_SYMBOLS}
    pool = {s: v for s, v in pool.items() if v is not None}
    print(f"코인 {len(pool)}개 · 데이터셋 만드는 중…", flush=True)
    ds = learn2.build_dataset(pool)
    PX = dataset_with_patterns(pool, ds)
    print(f"행 {len(ds['y']):,}개 · 비교 중…", flush=True)
    res = compare(ds, PX, iters=a.iters)
    print(render(res))
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
