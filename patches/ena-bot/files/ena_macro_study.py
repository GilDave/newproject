"""나스닥 등 거시 지표가 ENA 의 다음 24시간 수익률을 설명하는지 확인하는 읽기 전용 분석(매매·설정·DB 는 건드리지 않는다).

데이터: 지수는 FRED 공개 CSV(키 불필요, 일 단위 종가), ENA 는 로컬 시세 CSV(기본 data/ENAUSDT_60m.csv).
정렬: 미국장 종가(뉴욕 16:00, 서머타임 반영) 시점의 ENA 가격을 쓴다. '같은 날' = 전날 종가 → 당일 종가 구간,
      '선행' = 당일 종가 이후 24시간. 선행 관계가 있어야 예측에 쓸 수 있다(같은 날 상관은 설명일 뿐 예측이 아니다).
판정: 시리즈를 여러 개 동시에 보므로 다중검정 보정(본페로니)을 하고, 전·후반 부호가 같아야 하며 표본 150일 이상이어야 '검토 가치 있음'.
      아니면 '관계를 확인하지 못함'이다(없다는 증명은 아니다).

  .venv/bin/python ena_macro_study.py
  .venv/bin/python ena_macro_study.py --csv data/ENAUSDT_60m.csv --no-cache
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import subprocess
import time
import urllib.request
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    NY = None

HOUR = 3_600_000
SERIES = {  # id: (표시 이름, 변화 계산: pct=퍼센트 수익률, diff=차이)
    "NASDAQCOM": ("나스닥 종합", "pct"), "SP500": ("S&P 500", "pct"), "VIXCLS": ("VIX 변동성", "pct"),
    "DGS10": ("미 10년물 금리(%p 변화)", "diff"), "DTWEXBGS": ("달러 지수(광의)", "pct"),
}
MIN_N = 150


# ------------------------------------------------------------------ 데이터
def parse_fred(text: str) -> list[tuple[str, float]]:
    rows = []
    for r in csv.reader(text.splitlines()):
        if len(r) < 2:
            continue
        try:
            dt.date.fromisoformat(r[0])
            rows.append((r[0], float(r[1])))
        except ValueError:
            continue  # 헤더, 결측('.')
    return rows


def http_get(url: str, timeout: int = 30) -> str:
    """curl 을 먼저 쓴다(이 맥에서 urllib 은 시간 초과). FRED 는 HTTP/2 로 받으면 'stream not closed cleanly'(curl 92)가 나서 HTTP/1.1 로 고정하고, 브라우저인 척하는 식별자(Mozilla/5.0)를 붙이면 응답이 오지 않아 curl 기본 식별자를 쓴다(추정). curl 이 없으면 urllib."""
    try:
        r = subprocess.run(["curl", "--http1.1", "-fsSL", "-m", str(timeout), url], capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        raise OSError(f"{timeout}초 안에 받지 못했습니다") from None
    except FileNotFoundError:
        return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8.0"}), timeout=timeout).read().decode("utf-8")
    if r.returncode != 0:
        raise OSError(f"curl 종료 코드 {r.returncode}: {(r.stderr or '').strip()[:80]}")
    return r.stdout


START = "2024-03-01"  # ENA 상장(2024-04) 직전부터만 받는다: 전체 이력(1971~)은 응답이 커서 이 맥에서 중간에 멈췄다


def fetch_fred(sid: str, cache_dir: Path | None, use_cache: bool = True, fetch=None, ttl: float = 86400.0, now=time.time, start: str = START) -> list[tuple[str, float]]:
    f = cache_dir / f"{sid}.csv" if cache_dir else None
    if use_cache and f and f.exists() and now() - f.stat().st_mtime < ttl:
        return parse_fred(f.read_text(encoding="utf-8"))
    fetch = fetch or http_get
    text = fetch(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}")
    rows = parse_fred(text)
    if not rows:
        raise ValueError(f"{sid}: 읽을 수 있는 데이터가 없습니다")
    if f:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return rows


def load_ena_hourly(path: Path) -> dict[int, float]:
    """{봉 시작 ms: 종가}. 1시간보다 촘촘한 파일이면 시간 단위로 묶어 마지막 종가를 쓴다."""
    rows = []
    with open(path, newline="") as fh:
        for r in csv.reader(fh):
            try:
                rows.append((int(float(r[0])), float(r[4])))
            except (ValueError, IndexError):
                continue
    out: dict[int, float] = {}
    for ts, c in sorted(rows):
        out[ts // HOUR * HOUR] = c
    return out


def us_close_ms(date_str: str) -> int:
    d = dt.datetime.fromisoformat(date_str + "T16:00:00")
    if NY is not None:
        return int(d.replace(tzinfo=NY).timestamp() * 1000)
    return int(d.replace(tzinfo=dt.timezone(dt.timedelta(hours=-5))).timestamp() * 1000)


def price_at(hourly: dict[int, float], t_ms: int) -> float | None:
    """t_ms 시각(정시 기준으로 내림)에 끝나는 1시간봉의 종가."""
    return hourly.get(t_ms // HOUR * HOUR - HOUR)


def build_pairs(index_rows: list[tuple[str, float]], kind: str, hourly: dict[int, float]) -> list[dict]:
    out = []
    for (d0, v0), (d1, v1) in zip(index_rows, index_rows[1:]):
        if v0 == 0:
            continue
        t0, t1 = us_close_ms(d0), us_close_ms(d1)
        p0, p1, pf = price_at(hourly, t0), price_at(hourly, t1), price_at(hourly, t1 + 24 * HOUR)
        if not (p0 and p1 and pf):
            continue
        idx = (v1 / v0 - 1) * 100 if kind == "pct" else (v1 - v0)
        out.append({"date": d1, "idx": idx, "same": (p1 / p0 - 1) * 100, "fwd": (pf / p1 - 1) * 100})
    return out


# ------------------------------------------------------------------ 통계
def pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def p_value(r: float | None, n: int) -> float | None:
    """양측 p 값(정규 근사). 표본 30 미만이면 신뢰하지 않는다."""
    if r is None or n < 30 or abs(r) >= 1:
        return None
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return math.erfc(abs(t) / math.sqrt(2))


def analyze_series(pairs: list[dict], n_tests: int) -> dict:
    n = len(pairs)
    idx, same, fwd = [p["idx"] for p in pairs], [p["same"] for p in pairs], [p["fwd"] for p in pairs]
    r_same, r_fwd = pearson(idx, same), pearson(idx, fwd)
    p = p_value(r_fwd, n)
    half = n // 2
    r1, r2 = (pearson(idx[:half], fwd[:half]), pearson(idx[half:], fwd[half:])) if n >= 60 else (None, None)
    adj = None if p is None else min(1.0, p * n_tests)
    stable = r1 is not None and r2 is not None and r1 * r2 > 0 and r_fwd is not None and r1 * r_fwd > 0
    worth = bool(n >= MIN_N and adj is not None and adj < 0.05 and stable)
    return {"n": n, "r_same": r_same, "r_fwd": r_fwd, "p": p, "p_adj": adj, "r_first": r1, "r_second": r2, "stable": stable, "worth": worth}


def study(hourly: dict[int, float], data: dict[str, list[tuple[str, float]]]) -> dict:
    n_tests = max(1, len(data))
    res = {}
    for sid, rows in data.items():
        pairs = build_pairs(rows, SERIES[sid][1], hourly)
        res[sid] = {"label": SERIES[sid][0], **analyze_series(pairs, n_tests)} if pairs else {"label": SERIES[sid][0], "n": 0}
    return res


def render(res: dict, errors: dict | None = None) -> str:
    f = lambda v: "  —  " if v is None else f"{v:+.3f}"
    L = ["지표별 관계 (같은 날 상관 = 설명, 선행 상관 = 그날 종가 이후 ENA 24시간 수익률과의 상관 → 예측에 쓸 수 있는지)", "",
         f"{'지표':22s} {'표본':>5s} {'같은 날':>8s} {'선행':>8s} {'p(보정)':>8s} {'전반':>8s} {'후반':>8s}  판정"]
    for sid, r in res.items():
        if not r.get("n"):
            L.append(f"{r['label']:22s} {0:5d}  (겹치는 구간 없음)")
            continue
        pa = "  —  " if r["p_adj"] is None else f"{r['p_adj']:.3f}"
        verdict = "검토 가치 있음" if r["worth"] else ("표본 부족" if r["n"] < MIN_N else "관계를 확인하지 못함")
        L.append(f"{r['label']:22s} {r['n']:5d} {f(r['r_same']):>8s} {f(r['r_fwd']):>8s} {pa:>8s} {f(r['r_first']):>8s} {f(r['r_second']):>8s}  {verdict}")
    for sid, e in (errors or {}).items():
        L.append(f"{SERIES[sid][0]:22s} 수집 실패: {e}")
    worth = [r["label"] for r in res.values() if r.get("worth")]
    L += ["", "읽는 법:",
          "- p(보정)는 지표 개수만큼 다중검정 보정을 한 값입니다. 0.05 미만이고, 전반·후반 부호가 같고, 표본이 150일 이상일 때만 '검토 가치'로 봅니다.",
          "- 상관이 ±0.1 안팎이면 실제 값이 있어도 매매에 쓰기엔 너무 약합니다(설명되는 변동이 1% 안팎).",
          "- '확인하지 못함'은 관계가 없다는 증명이 아닙니다. 표본·기간이 한정돼 있습니다."]
    L.append("결론: " + (f"{', '.join(worth)} 에서 다음 단계(그림자 실험)를 검토할 만합니다." if worth else "지금 데이터로는 그림자 실험으로 넘어갈 근거가 없습니다."))
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default="data/ENAUSDT_60m.csv")
    ap.add_argument("--cache", default="data/macro_cache")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args(argv)
    if not Path(a.csv).is_file():
        print(f"ENA 시세 파일이 없습니다: {a.csv}")
        return 1
    hourly = load_ena_hourly(Path(a.csv))
    if not hourly:
        print("시세 파일에서 봉을 읽지 못했습니다.")
        return 1
    ts = sorted(hourly)
    print(f"ENA 시세 {len(hourly)}봉 ({dt.datetime.fromtimestamp(ts[0] / 1000, dt.timezone.utc):%Y-%m-%d} ~ {dt.datetime.fromtimestamp(ts[-1] / 1000, dt.timezone.utc):%Y-%m-%d} UTC)")
    data, errors = {}, {}
    for sid in SERIES:
        print(f"받는 중: {SERIES[sid][0]} …", flush=True)
        try:
            data[sid] = fetch_fred(sid, Path(a.cache), use_cache=not a.no_cache)
        except Exception as exc:  # noqa: BLE001
            errors[sid] = str(exc)[:80]
    if not data:
        print("지표를 하나도 받지 못했습니다:", errors)
        return 1
    print(render(study(hourly, data), errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
