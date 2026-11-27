"""미국 주식 개장 시간대(9:30 ET)에 코인이 평소보다 더 움직이는가(읽기 전용). 18코인 1시간봉.
ET(미국 동부) 시각별로 '그날 평균 봉 대비 변동 폭·거래량'을 구하고, 미국 장이 열리는 평일과 닫힌 주말을 비교한다(주말은 같은 시각의 대조군).
평일/주말 비율이 1보다 크고 95% 구간이 1 위에 있으면 그 시각에는 미국 장이 열린 날 더 움직였다는 뜻이다(서머타임은 ET 벽시계로 맞춘다).
실행(ENA 폴더에서):  .venv/bin/python ena_open_study.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
ET = ZoneInfo("America/New_York")
DAY = 86_400_000
HOURS = range(5, 15)


def et_fields(ts_ms: np.ndarray):
    """각 봉 시작의 (ET 날짜 번호, ET 시, 요일 0=월). 날짜별 UTC 오프셋을 한 번만 구해 빠르게 계산한다."""
    days = np.unique(ts_ms // DAY)
    off = {int(d): datetime.fromtimestamp(int(d) * 86400 + 43200, ET).utcoffset().total_seconds() * 1000 for d in days}
    shifted = ts_ms + np.array([off[int(t // DAY)] for t in ts_ms])
    dnum = (shifted // DAY).astype(int)
    hour = ((shifted % DAY) // 3_600_000).astype(int)
    wd = ((dnum + 3) % 7).astype(int)                # 1970-01-01 은 목요일(3)
    return dnum, hour, wd


def rel_table(a: np.ndarray):
    """한 코인: {ET 날짜: (요일, 변동 폭 상대값[24], 거래량 상대값[24])}. 24봉이 다 있는 날만."""
    dnum, hour, wd = et_fields(a[:, 0])
    rng = (a[:, 2] - a[:, 3]) / np.where(a[:, 1] > 0, a[:, 1], np.nan)
    vol = a[:, 5].astype(float)
    out = {}
    for d in np.unique(dnum):
        i = np.flatnonzero(dnum == d)
        if len(i) != 24 or len(set(hour[i])) != 24:
            continue
        o = np.argsort(hour[i]); r, v = rng[i][o], vol[i][o]
        if not np.all(np.isfinite(r)) or r.mean() <= 0 or v.mean() <= 0:
            continue
        out[int(d)] = (int(wd[i][0]), r / r.mean(), v / v.mean())
    return out


def boot_mean(x, iters=2000, seed=0):
    x = np.asarray(x, float)
    rng = np.random.default_rng(seed)
    return np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(iters)])


def study(data: dict, only: str | None = None):
    """data: {심볼: 1시간봉 배열}. 반환: {시각: {kind: (평일 평균, 주말 평균, 비율, 하한, 상한, 평일 일수, 주말 일수)}}."""
    per_day: dict = {}
    for sym, a in data.items():
        if only and sym != only:
            continue
        for d, (wd, r, v) in rel_table(a).items():
            per_day.setdefault(d, {"wd": wd, "r": [], "v": []})
            per_day[d]["r"].append(r); per_day[d]["v"].append(v)
    days = {d: (x["wd"], np.mean(x["r"], axis=0), np.mean(x["v"], axis=0)) for d, x in per_day.items()}      # 날짜별로 코인 평균
    wk = [d for d, x in days.items() if x[0] < 5]
    we = [d for d, x in days.items() if x[0] >= 5]
    out = {}
    if len(wk) < 30 or len(we) < 15:
        return out
    for h in HOURS:
        out[h] = {}
        for kind, k in (("range", 1), ("volume", 2)):
            a = np.array([days[d][k][h] for d in wk]); b = np.array([days[d][k][h] for d in we])
            ba, bb = boot_mean(a), boot_mean(b, seed=1)
            ratio = ba / bb
            out[h][kind] = (float(a.mean()), float(b.mean()), float(a.mean() / b.mean()), float(np.percentile(ratio, 2.5)), float(np.percentile(ratio, 97.5)), len(wk), len(we))
    return out


def render(res_all: dict, res_ena: dict) -> str:
    if not res_all:
        return "일수가 모자라 계산하지 않았습니다(평일 30일·주말 15일 필요)."
    L = ["미국 장이 열린 평일 vs 닫힌 주말: 같은 ET 시각의 '그날 평균 봉 대비' 변동 폭·거래량 (비율 1.0 = 차이 없음)", "",
         f"{'ET 시각':8s} {'한국(겨울/여름)':13s} | {'변동 폭 비율(95% 구간)':>24s} | {'거래량 비율(95% 구간)':>24s} | {'ENA 변동 폭':>10s} {'ENA 거래량':>10s}"]
    for h in HOURS:
        r, v = res_all[h]["range"], res_all[h]["volume"]
        e = res_ena.get(h)
        mark = "  ← 개장(9:30) 포함" if h == 9 else ("  ← 지표 8:30 포함" if h == 8 else "")
        kst = f"{(h + 14) % 24:02d}시/{(h + 13) % 24:02d}시"
        L.append(f"{h:02d}:00    {kst:13s} | {r[2]:5.2f}배 ({r[3]:.2f}~{r[4]:.2f})".ljust(52) + f" | {v[2]:5.2f}배 ({v[3]:.2f}~{v[4]:.2f})".ljust(27)
                 + (f" | {e['range'][2]:6.2f}배 {e['volume'][2]:9.2f}배" if e else " |    —") + mark)
    n = res_all[9]["range"]
    L += ["", f"표본: 평일 {n[5]}일 · 주말 {n[6]}일(날짜별로 18코인을 평균). 서머타임 때문에 같은 ET 시각이 한국 시각으로는 계절마다 1시간 다릅니다.",
          "읽는 법: 비율이 1보다 크고 95% 구간이 1 위에 있으면 그 시각은 미국 장이 열린 날 더 많이 움직였다는 뜻입니다.",
          "한계: 움직임의 크기만 봅니다(방향은 아님). 평일 효과에는 미국 장뿐 아니라 지표 발표·유럽 장 등 평일 요인이 섞여 있고, 주말은 대조군일 뿐 완벽한 비교는 아닙니다."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default="data/learn_cache")
    a = ap.parse_args(argv)
    import ena_pattern_study as p1
    data = p1.load(Path(a.cache))
    print(f"코인 {len(data)}개 로드", flush=True)
    print(render(study(data), study(data, "ENAUSDT")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
