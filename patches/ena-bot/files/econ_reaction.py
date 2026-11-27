"""지표 발표 직후 ENA(와 BTC)의 가격 반응 기록(읽기 전용). 발표 시각부터 5·15·30·60분 뒤 수익률, 60분 동안의 최대 상승·하락, 고저 폭을
같은 시각의 직전 평일 5일(평소)과 비교한 배율로 남긴다. 몇 달 쌓이면 지표별로 '실제로 얼마나 움직였나'를 데이터로 볼 수 있다.
방향을 예측하지 않는다. 기록만 한다. 시세는 Bybit 공개 1분봉(키 불필요)을 쓴다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

WINDOWS = (5, 15, 30, 60)
SYMBOLS = ("ENAUSDT", "BTCUSDT")
MIN = 60_000
DELAY_MIN = 62                  # 발표 후 이만큼 지나야 60분 창이 닫힌다
MAX_AGE_DAYS = 7                # 이보다 오래된 발표는 새로 기록하지 않는다(1분봉으로 되짚기 어렵다)


def reaction_from_rows(rows, t_ms: int, windows=WINDOWS):
    """rows: 오래된 것부터의 1분봉 [ts,o,h,l,c,v]. t_ms 에 시작하는 봉의 시가를 발표 시점 가격으로 본다. 창이 비면 None."""
    t0 = t_ms // MIN * MIN
    by = {int(r[0]): r for r in rows}
    if t0 not in by or by[t0][1] <= 0:
        return None
    p0 = float(by[t0][1])
    out = {"p0": p0}
    for w in windows:
        bar = by.get(t0 + (w - 1) * MIN)
        if bar is None:
            return None
        out[f"ret{w}"] = float(bar[4]) / p0 - 1
    seg = [by[t] for t in range(t0, t0 + 60 * MIN, MIN) if t in by]
    if len(seg) < 55:
        return None
    hi, lo = max(float(r[2]) for r in seg), min(float(r[3]) for r in seg)
    out.update({"maxup": hi / p0 - 1, "maxdn": lo / p0 - 1, "range": (hi - lo) / p0})
    return out


def baseline_times(t_ms: int, n: int = 5, max_back: int = 14) -> list[int]:
    """같은 시각의 직전 평일 n일(토·일 제외). 암호화폐도 주말엔 변동이 달라 평일끼리 비교한다."""
    out, k = [], 1
    while len(out) < n and k <= max_back:
        t = t_ms - k * 86_400_000
        if datetime.fromtimestamp(t / 1000, timezone.utc).weekday() < 5:
            out.append(t)
        k += 1
    return out


def compute(fetch_rows, symbol: str, t_ms: int):
    """fetch_rows(symbol, end_ms, limit) -> 1분봉(오래된 것부터). 실패하거나 데이터가 모자라면 None."""
    rows = fetch_rows(symbol, t_ms + 61 * MIN, 80)
    r = reaction_from_rows(rows, t_ms)
    if r is None:
        return None
    bases = []
    for tb in baseline_times(t_ms):
        try:
            b = reaction_from_rows(fetch_rows(symbol, tb + 61 * MIN, 80), tb)
        except Exception:  # noqa: BLE001 - 평소 비교 하나가 실패해도 나머지로 계산한다
            b = None
        if b:
            bases.append(b)
    r["n_base"] = len(bases)
    if len(bases) >= 3:
        a15 = sum(abs(b["ret15"]) for b in bases) / len(bases)
        rg = sum(b["range"] for b in bases) / len(bases)
        r["ratio15"] = abs(r["ret15"]) / a15 if a15 > 0 else None
        r["ratio_range"] = r["range"] / rg if rg > 0 else None
    else:
        r["ratio15"] = r["ratio_range"] = None
    return r


_UNIT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def parse_num(s) -> float | None:
    """'0.5%', '-1.2K', '215K', '$1.3B' 같은 표기를 숫자로. 못 읽으면 None."""
    m = re.fullmatch(r"\s*[$€£]?\s*([-+]?\d+(?:[.,]\d+)?)\s*([KMBT%]?)\s*", str(s or ""), re.I)
    if not m:
        return None
    return float(m.group(1).replace(",", ".")) * _UNIT.get(m.group(2).upper(), 1.0)


def surprise(actual, forecast):
    a, f = parse_num(actual), parse_num(forecast)
    return None if a is None or f is None else a - f


class ReactionStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> dict:
        out = {}
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    out[(int(r["ts"]), r["name_en"])] = r
                except (ValueError, KeyError):
                    continue
        except OSError:
            pass
        return out

    def append(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pending(events, now_ms: int, store: ReactionStore, delay_min: int = DELAY_MIN, max_age_days: int = MAX_AGE_DAYS):
    have = store.load()
    return [e for e in events if e["ts"] + delay_min * MIN <= now_ms <= e["ts"] + max_age_days * 86_400_000 and (e["ts"], e["name_en"]) not in have]


def record_new(events, now_ms: int, fetch_rows, store: ReactionStore, symbols=SYMBOLS, max_per_run: int = 4) -> list[dict]:
    """아직 기록하지 않은, 60분 창이 닫힌 발표의 반응을 계산해 덧붙인다. ENA 를 못 구하면 그 발표는 다음에 다시 시도한다."""
    done = []
    for e in pending(events, now_ms, store)[:max_per_run]:
        res = {}
        for sym in symbols:
            try:
                r = compute(fetch_rows, sym, e["ts"])
            except Exception:  # noqa: BLE001
                r = None
            if r:
                res[sym] = r
        if "ENAUSDT" not in res:
            continue
        rec = {"ts": e["ts"], "name": e["name"], "name_en": e["name_en"], "impact": e["impact"], "previous": e["previous"], "forecast": e["forecast"],
               "actual": e["actual"], "surprise": surprise(e["actual"], e["forecast"]), "recorded_at": now_ms,
               "ena": res["ENAUSDT"], "btc": res.get("BTCUSDT")}
        store.append(rec)
        done.append(rec)
    return done


def brief(rec: dict | None):
    """화면용 요약(필요한 값만)."""
    if not rec:
        return None
    pick = lambda r: None if not r else {k: r.get(k) for k in ("ret5", "ret15", "ret30", "ret60", "maxup", "maxdn", "range", "ratio_range", "ratio15")}
    return {"ena": pick(rec.get("ena")), "btc": pick(rec.get("btc")), "surprise": rec.get("surprise")}
