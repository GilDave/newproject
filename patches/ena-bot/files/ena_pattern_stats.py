"""차트 패턴별 과거 통계표(pattern_stats.json) 생성(읽기 전용). 대시보드의 패턴 표시가 이 표를 읽어 '이 패턴은 과거에 이랬다'를 같이 보여 준다.
정의·판정 기준은 ena_pattern_study*_prereg.md 와 같다: 18코인 1·4시간봉, 진입 후 24시간 뒤 수익을 같은 코인 기준 수익과 왕복 비용 0.15%를 빼고 평균.
실행(ENA 폴더에서, 같은 폴더의 ena_pattern_study.py · ena_pattern_study2.py 필요):  .venv/bin/python ena_pattern_stats.py [--out ena_bot/pattern_stats.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ena_pattern_study as p1  # noqa: E402
import ena_pattern_study2 as p2  # noqa: E402

ALL = ["P0W", "P0M"] + [f"P{i}" for i in range(1, 11)]
TESTS = 24
ALPHA_PCT = 100 * 0.05 / TESTS
HLABEL = {"1h": "24시간(1시간봉 24개) 뒤", "4h": "24시간(4시간봉 6개) 뒤"}


def collect(data: dict, pat: str, tf: str):
    """(이벤트 시각, 방향 조정 수익, 같은 코인 모든 봉 평균 수익, 모든 봉 중 이익 비율, 모든 봉 수) 를 코인을 합쳐 돌려준다."""
    hh, sign = p2.PRIMARY[tf], p2.SIGN[pat]
    ts_l, r_l, base_w, hit_w, cnt_w = [], [], [], [], []
    for a in data.values():
        ts, h, l, c = a[:, 0], a[:, 2], a[:, 3], a[:, 4]
        if len(c) <= hh:
            continue
        allr = sign * (c[hh:] / c[:-hh] - 1)
        base = float(allr.mean())
        base_hit = float((allr > 0).mean())
        for i in p2.events_for(pat, h, l, c):
            if i + hh < len(c):
                ts_l.append(ts[i])
                r_l.append(sign * (c[i + hh] / c[i] - 1) - base)      # 같은 코인 기준 수익을 뺀 총 초과 수익
        base_w.append(base)
        hit_w.append(base_hit)
        cnt_w.append(len(allr))
    return np.array(ts_l), np.array(r_l), hit_w, cnt_w


def stat_for(data: dict, pat: str, tf: str, iters: int = 5000) -> dict:
    ts, gross, hit_w, cnt_w = collect(data, pat, tf)
    n = len(gross)
    if n < 2:
        return {"events": int(n), "verdict": "이벤트 부족"}
    net = gross - p1.COST_RT
    week = (ts // (7 * 86_400_000)).astype(int)
    lo95, hi95 = p1.boot_ci(week, net, iters=iters, lo=2.5, hi=97.5)
    lo_b, _ = p1.boot_ci(week, net, iters=iters, lo=ALPHA_PCT, hi=99.0)
    pre, post = net[ts < p1.SPLIT_MS], net[ts >= p1.SPLIT_MS]
    ok = bool(n >= 100 and lo_b > 0 and len(pre) and len(post) and pre.mean() > 0 and post.mean() > 0)
    return {"events": int(n), "mean_net_pct": float(net.mean() * 100), "gross_pct": float(gross.mean() * 100), "ci95_pct": [lo95 * 100, hi95 * 100],
            "win_pct": float((gross > 0).mean() * 100),      # 비용 전, 같은 코인 기준 수익보다 나았던 이벤트의 비율
            "pre2024_pct": float(pre.mean() * 100) if len(pre) else None, "post2024_pct": float(post.mean() * 100) if len(post) else None,
            "n_pre": int(len(pre)), "n_post": int(len(post)),
            "verdict": "합격(전진검증 대상)" if ok else "근거 없음" if n >= 100 else "이벤트 부족"}


def build(data1: dict, iters: int = 5000, log=print) -> dict:
    data4 = {s: p2.resample4(v) for s, v in data1.items()}
    out = {"generated": dt.date.today().isoformat(), "coins": len(data1), "cost_round_trip_pct": p1.COST_RT * 100,
           "note": "비용 후 초과 수익 = 같은 코인의 같은 방향 평균 수익을 뺀 뒤 왕복 비용을 뺀 값. 판정은 사전 등록 기준(이벤트 100개 이상, 보정된 신뢰구간 하한 > 0, 2024년 전·후 모두 양수).", "tf": {}}
    for tf, data in (("1h", data1), ("4h", data4)):
        out["tf"][tf] = {"horizon": HLABEL[tf], "patterns": {}}
        for pat in ALL:
            log(f"{tf} {p2.NAMES[pat]} …")
            out["tf"][tf]["patterns"][pat] = stat_for(data, pat, tf, iters)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default="data/learn_cache")
    ap.add_argument("--out", default="ena_bot/pattern_stats.json")
    ap.add_argument("--iters", type=int, default=5000)
    a = ap.parse_args(argv)
    data = p1.load(Path(a.cache))
    res = build(data, a.iters)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {a.out} (코인 {res['coins']}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
