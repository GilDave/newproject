"""지표 발표 시각에 코인 가격이 평소보다 더 움직이는가(읽기 전용). events.py 의 FOMC·CPI·고용보고서 일정(2021~)과 18코인 1시간봉으로 확인한다.
각 발표가 들어 있는 1시간봉의 변동(시가 대비 절대 수익률, 고저 폭)을 같은 미국 동부시각의 평소 평일 봉(±20일, 발표일 제외)과 비교한 배율이다.
실행(ENA 폴더에서):  .venv/bin/python ena_econ_study.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ena_pattern_study as p1  # noqa: E402
from ena_bot.events import event_times_ms  # noqa: E402

ET, UTC = ZoneInfo("America/New_York"), ZoneInfo("UTC")
H = 3_600_000
KINDS = ("FOMC", "CPI", "NFP")


def bar_of(ts_ms: int, offset_h: int = 0) -> datetime:
    """발표 시각이 들어 있는 1시간봉의 시작(미국 동부 벽시계). offset_h 시간 뒤의 봉도 줄 수 있다."""
    et = datetime.fromtimestamp(ts_ms / 1000, ET).replace(minute=0, second=0, microsecond=0)
    return et + timedelta(hours=offset_h)


def to_ms(et: datetime) -> int:
    return int(et.astimezone(UTC).timestamp() * 1000)


def metrics(a: np.ndarray, start_ms: int):
    """1시간봉 배열 a([ts,o,h,l,c,v])에서 start_ms 에 시작하는 봉의 (|수익률|, 고저 폭). 없으면 None."""
    i = np.searchsorted(a[:, 0], start_ms)
    if i >= len(a) or int(a[i, 0]) != start_ms or a[i, 1] <= 0:
        return None
    o, h, l, c = a[i, 1], a[i, 2], a[i, 3], a[i, 4]
    return abs(c / o - 1), (h - l) / o


def event_ratios(a: np.ndarray, ev_ms: list[int], offset_h: int = 0, span: int = 20) -> list[tuple[float, float, int]]:
    """각 발표의 (|수익률| 배율, 고저 폭 배율, 발표 시각). 평소 비교 봉이 5개 미만이면 건너뛴다."""
    ev_days = {datetime.fromtimestamp(t / 1000, ET).date() for t in ev_ms}
    out = []
    for t in ev_ms:
        start = bar_of(t, offset_h)
        m = metrics(a, to_ms(start))
        if m is None:
            continue
        base = []
        for d in range(-span, span + 1):
            c = start + timedelta(days=d)
            if d == 0 or c.weekday() >= 5 or c.date() in ev_days:
                continue
            x = metrics(a, to_ms(c))                           # 같은 미국 동부 벽시계 시각(서머타임 이동을 반영)
            if x is not None:
                base.append(x)
        if len(base) >= 5:
            b = np.mean(base, axis=0)
            if b[0] > 0 and b[1] > 0:
                out.append((m[0] / b[0], m[1] / b[1], t))
    return out


def boot_median(x: np.ndarray, iters: int = 3000, seed: int = 0):
    rng = np.random.default_rng(seed)
    m = [np.median(x[rng.integers(0, len(x), len(x))]) for _ in range(iters)]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def study(data: dict, kinds=KINDS) -> dict:
    res = {}
    for kind in kinds:
        ev = [int(t) for t in event_times_ms((kind,))]
        for off, label in ((0, "발표가 든 1시간"), (1, "그 다음 1시간")):
            rows = {s: event_ratios(a, ev, off) for s, a in data.items()}
            per_event: dict[int, list[float]] = {}
            for s, lst in rows.items():
                for ra, rr, t in lst:
                    per_event.setdefault(t, []).append(rr)
            pooled = np.array([np.mean(v) for v in per_event.values()]) if per_event else np.array([])
            def one(sym):
                lst = rows.get(sym, [])
                return None if len(lst) < 5 else (len(lst), float(np.median([x[1] for x in lst])), boot_median(np.array([x[1] for x in lst])),
                                                  float(np.median([x[0] for x in lst])))
            res[(kind, label)] = {"events": len(pooled), "all": None if len(pooled) < 5 else (float(np.median(pooled)), boot_median(pooled), float((pooled > 1).mean())),
                                   "BTC": one("BTCUSDT"), "ETH": one("ETHUSDT"), "ENA": one("ENAUSDT")}
    return res


def render(res: dict) -> str:
    L = ["지표 발표 시각의 가격 변동 배율 (고저 폭 기준, 같은 미국 동부 시각의 평소 평일 봉 = 1.0배)", "",
         f"{'지표':6s} {'구간':14s} {'발표수':>5s} | {'18코인 중앙값(95% 구간)':>26s} {'배율>1 비율':>10s} | {'BTC':>14s} | {'ETH':>14s} | {'ENA':>14s}"]
    for (kind, label), r in res.items():
        f = lambda x: "       —      " if x is None else f"{x[1]:.2f}배 (n={x[0]:3d})"
        a = r["all"]
        L.append(f"{kind:6s} {label:14s} {r['events']:5d} | " + ("          —          " if a is None else f"{a[0]:5.2f}배 ({a[1][0]:.2f}~{a[1][1]:.2f})").rjust(26)
                 + f" {('—' if a is None else format(a[2] * 100, '.0f') + '%'):>10s} | {f(r['BTC'])} | {f(r['ETH'])} | {f(r['ENA'])}")
    L += ["", "읽는 법: 1.0배 = 평소와 같은 움직임, 2.0배 = 평소의 두 배. 구간(95%)이 1.0을 포함하지 않고 위에 있으면 그 시각에 평소보다 더 움직였다고 볼 수 있습니다.",
          "방향(오를지 내릴지)은 이 표가 말하지 않습니다. 움직임의 크기만 봅니다."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default="data/learn_cache")
    a = ap.parse_args(argv)
    data = p1.load(Path(a.cache))
    print(f"코인 {len(data)}개 로드", flush=True)
    print(render(study(data)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
