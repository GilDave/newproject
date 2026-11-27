"""차트 패턴 정의 변형의 워크포워드 선택 시험(읽기 전용). 규칙은 ena_round3_prereg.md 의 A 절에 결과를 보기 전에 고정했다.
2024-01 이전 데이터로 변형(피벗 폭 k × 허용도 s) 중 가장 좋은 것을 고르고, 그 선택을 2024-01 이후에서만 평가한다.
실행(ENA 폴더에서):  .venv/bin/python ena_pattern_sweep.py [--out 결과.json]   (ena_pattern_study.py · ena_pattern_study2.py 와 같은 폴더)
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ena_pattern_study as p1  # noqa: E402
import ena_pattern_study2 as p2  # noqa: E402

GRID_K, GRID_S = (3, 4, 6), (0.7, 1.0, 1.5)
TESTS = 24
ALPHA_PCT = 100 * 0.05 / TESTS
MIN_PRE, MIN_POST = 100, 100
PRIMARY = p2.PRIMARY
SIGN = p2.SIGN
_DATA: dict = {}


def combos(pat: str):
    return [(4, s) for s in GRID_S] if pat in ("P9", "P10") else [(k, s) for k in GRID_K for s in GRID_S]


def gross_rows(data: dict, pat: str, tf: str, k: int, s: float) -> np.ndarray:
    """주 판정 선행 구간에서 이벤트마다 (진입 시각, 같은 코인 기준 수익을 뺀 총 초과 수익). 비용은 아직 빼지 않는다."""
    hh, sign, rows = PRIMARY[tf], SIGN[pat], []
    for a in data.values():
        ts, h, l, c = a[:, 0], a[:, 2], a[:, 3], a[:, 4]
        if len(c) <= hh:
            continue
        base = float(np.mean(sign * (c[hh:] / c[:-hh] - 1)))
        for i in p2.events_for(pat, h, l, c, k, s):
            if i + hh < len(c):
                rows.append((ts[i], sign * (c[i + hh] / c[i] - 1) - base))
    return np.array(rows, dtype=float).reshape(-1, 2)


def _task(args):
    tf, pat, k, s = args
    return args, gross_rows(_DATA[tf], pat, tf, k, s)


def evaluate(rows_by_combo: dict, iters: int = 5000, alpha_pct: float = ALPHA_PCT) -> dict:
    """rows_by_combo: {(k,s): rows} 한 패턴·주기의 모든 조합. 이전 구간으로 선택하고 이후 구간으로 평가한다."""
    pre_mean, post_mean, n_pre, n_post = {}, {}, {}, {}
    for key, r in rows_by_combo.items():
        pre, post = r[r[:, 0] < p1.SPLIT_MS, 1] - p1.COST_RT, r[r[:, 0] >= p1.SPLIT_MS, 1] - p1.COST_RT
        n_pre[key], n_post[key] = len(pre), len(post)
        pre_mean[key] = float(pre.mean()) if len(pre) else float("nan")
        post_mean[key] = float(post.mean()) if len(post) else float("nan")
    elig = [k for k in rows_by_combo if n_pre[k] >= MIN_PRE]
    out = {"breadth_pos_pre": int(sum(1 for k in rows_by_combo if np.isfinite(pre_mean[k]) and pre_mean[k] > 0)), "n_combos": len(rows_by_combo),
           "post_mean_all_pct": float(np.nanmean([post_mean[k] for k in rows_by_combo]) * 100) if rows_by_combo else None}
    if not elig:
        return {**out, "pass": False, "verdict": "이전 구간 이벤트 부족"}
    best = max(elig, key=lambda k: pre_mean[k])
    r = rows_by_combo[best]
    post_rows = r[r[:, 0] >= p1.SPLIT_MS]
    out.update({"chosen": {"k": best[0], "s": best[1]}, "pre_n": n_pre[best], "pre_mean_pct": pre_mean[best] * 100,
                "post_n": n_post[best], "post_mean_pct": post_mean[best] * 100 if n_post[best] else None})
    if n_post[best] < MIN_POST:
        return {**out, "pass": False, "verdict": "이후 구간 이벤트 부족"}
    week = (post_rows[:, 0] // (7 * 86_400_000)).astype(int)
    lo, _ = p1.boot_ci(week, post_rows[:, 1] - p1.COST_RT, iters=iters, lo=alpha_pct, hi=99.0)
    out["post_ci_low_pct"] = lo * 100
    ok = bool(post_mean[best] > 0 and lo > 0)
    return {**out, "pass": ok, "verdict": "합격(전진검증 대상)" if ok else "근거 없음 — 폐기"}


def run(data1: dict, iters: int = 5000, pats=None, workers: int = 4, log=print) -> dict:
    global _DATA
    data4 = {s: p2.resample4(v) for s, v in data1.items()}
    _DATA = {"1h": data1, "4h": data4}
    tasks = []
    for tf in ("1h", "4h"):
        plist = pats or (["P0W", "P0M"] + [f"P{i}" for i in range(1, 11)])
        for pat in plist:
            for k, s in combos(pat):
                tasks.append((tf, pat, k, s))
    log(f"조합 {len(tasks)}개 계산 중…")
    if workers > 1 and "fork" in mp.get_all_start_methods():
        with mp.get_context("fork").Pool(workers) as pool:
            done = pool.map(_task, tasks, chunksize=2)
    else:
        done = [_task(t) for t in tasks]
    grouped: dict = {}
    for (tf, pat, k, s), rows in done:
        grouped.setdefault((tf, pat), {})[(k, s)] = rows
    return {f"{tf}:{pat}": evaluate(rows, iters) for (tf, pat), rows in grouped.items()}


def render(res: dict) -> str:
    L = [f"패턴 변형 워크포워드 시험 (2024-01 이전으로 선택 → 이후만 평가 · 비용 0.15% 차감 · 시험 {TESTS}건 보정 하한 {ALPHA_PCT:.2f} 분위)", "",
         f"{'주기·패턴':26s} {'선택(k,s)':>9s} {'이전n':>6s} {'이전%':>7s} {'이후n':>6s} {'이후%':>7s} {'하한%':>7s} {'이전>0 조합':>11s} {'전체조합 이후%':>13s}  판정"]
    passed = []
    names = {**p2.NAMES}
    for key, r in res.items():
        tf, pat = key.split(":")
        label = f"{tf} {names[pat]}"
        if "chosen" not in r:
            L.append(f"{label:26s} {r['verdict']}")
            continue
        c = r["chosen"]
        f = lambda v: "   —  " if v is None else f"{v:+7.2f}"
        L.append(f"{label:26s} {str((c['k'], c['s'])):>9s} {r['pre_n']:6d} {f(r['pre_mean_pct'])} {r['post_n']:6d} {f(r['post_mean_pct'])} {f(r.get('post_ci_low_pct'))} "
                 f"{r['breadth_pos_pre']:>5d}/{r['n_combos']:<5d} {f(r['post_mean_all_pct']):>13s}  {r['verdict']}")
        if r["pass"]:
            passed.append(label)
    L += ["", "합격: " + (", ".join(passed) if passed else "없음 — 모두 폐기"),
          "읽는 법: 선택은 2024년 이전 성적으로만 했고 평가는 이후 구간만 씁니다. 효과가 진짜라면 이웃 조합도 비슷해야 하므로 '이전>0 조합'과 '전체조합 이후%'를 같이 보세요."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default="data/learn_cache")
    ap.add_argument("--out")
    ap.add_argument("--iters", type=int, default=5000)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    data = p1.load(Path(a.cache))
    print(f"코인 {len(data)}개 로드", flush=True)
    res = run(data, a.iters, workers=a.workers)
    print(render(res))
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
