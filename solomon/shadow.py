"""Shadow mode scoring: what the local model WOULD have decided, against what really happened.

Two sources, both read-only:
  1. Solomon's own runs (`arbiter_shadow` events): the model judged every attempt but was never obeyed.
  2. A live bot's own filter log (`events` table of its SQLite file), opened with mode=ro and never written.

Truth labels are only what was measured or what a person did: failed gates => REJECT was right; a person applying a result =>
ADOPT was right; a person discarding it => REJECT was right; anything undecided is "unknown" and is never counted as right or wrong.
Every combination (tests x reviewer votes x model decision x truth) is listed, with counts and honest intervals.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from . import manage

MIN_SAMPLES = 30  # below this a rate is shown as "insufficient data", never as a verdict
DANGER_CEILING = 0.10  # upper bound allowed for "said ADOPT but the truth was REJECT" before promotion is even discussed


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1  # ties share the average rank
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> Optional[tuple[float, Optional[tuple[float, float]]]]:
    """Rank correlation of two lists (None when it cannot be computed) with a 95% interval from Fisher's z (needs n > 3)."""
    n = len(xs)
    if n < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    if den == 0:
        return None
    r = max(-0.999999, min(0.999999, cov / den))
    if n <= 3:
        return r, None
    z, se = math.atanh(r), 1 / math.sqrt(n - 3)
    return r, (math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se))


def _vote_pattern(votes: list[dict]) -> str:
    ok = sum(1 for v in votes if v.get("verdict") == "PASS")
    return f"{ok}/{len(votes)} PASS" if votes else "no reviewers"


def _events(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def collect(project: Path, state_dir: str = ".solomon") -> list[dict]:
    """One case per shadow-judged attempt, with its truth label when one exists."""
    project = Path(project)
    root = project / state_dir / "runs"
    marks = manage._load_marks(project, state_dir)
    cases: list[dict] = []
    if not root.is_dir():
        return cases
    for d in sorted(root.iterdir()):
        evs = _events(d / "events.jsonl")
        shadows = [e for e in evs if e.get("event") == "arbiter_shadow"]
        if not shadows:
            continue
        end = next((e for e in reversed(evs) if e.get("event") == "run_end"), {})
        passed_attempt = int(end.get("attempts") or 0) if end.get("status") == "PASSED" else None
        state = manage._state_of(f"run:{d.name}", marks, (d / "applied.json").is_file()) if passed_attempt else ""
        for e in shadows:
            gates_green = e.get("tests") == "pass" and e.get("double") is not False and all(v.get("verdict") == "PASS" for v in e.get("votes", []))
            if not gates_green:
                truth, basis = "REJECT", "gates failed"
            elif passed_attempt == e.get("attempt") and state in ("applied", "committed"):
                truth, basis = "ADOPT", "person applied it"
            elif passed_attempt == e.get("attempt") and state == "discarded":
                truth, basis = "REJECT", "person discarded it"
            else:
                truth, basis = "unknown", "not decided yet" if gates_green else ""
            cases.append({"run": d.name, "attempt": e.get("attempt"), "provider": e.get("provider"), "decision": e.get("decision"),
                          "confidence": float(e.get("confidence") or 0), "tests": e.get("tests"), "votes": _vote_pattern(e.get("votes", [])),
                          "risky": bool(e.get("risky")), "truth": truth, "basis": basis})
    return cases


def _verdict(dec: str, truth: str) -> str:
    """right / dangerous (said ADOPT, truth REJECT) / overcautious (said REJECT, truth ADOPT) / unknown. NEED_HUMAN is its own bucket."""
    if truth == "unknown":
        return "unknown"
    if dec == "NEED_HUMAN":
        return "asked_human"
    if dec == "NONE":
        return "no_answer"
    return "right" if dec == truth else "dangerous" if dec == "ADOPT" else "overcautious"


def matrix(cases: list[dict]) -> dict:
    """Every combination that occurred, plus totals, error rates with intervals and the promotion check."""
    cells: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))
    tot: dict[str, int] = defaultdict(int)
    for c in cases:
        v = _verdict(c["decision"], c["truth"])
        cells[(c["tests"], c["votes"], c["decision"])][c["truth"]] += 1
        cells[(c["tests"], c["votes"], c["decision"])]["_" + v] += 1
        tot[v] += 1
    rows = [{"tests": k[0], "votes": k[1], "decision": k[2], "truth": {t: n for t, n in v.items() if not t.startswith("_")},
             "n": sum(n for t, n in v.items() if not t.startswith("_"))} for k, v in sorted(cells.items())]
    labelled = tot["right"] + tot["dangerous"] + tot["overcautious"] + tot["asked_human"]
    said_adopt = tot["dangerous"] + sum(1 for c in cases if c["decision"] == "ADOPT" and c["truth"] == "ADOPT")
    lo, hi = wilson(tot["dangerous"], said_adopt)
    decided = tot["right"] + tot["dangerous"] + tot["overcautious"]
    r_lo, r_hi = wilson(tot["right"], decided)
    enough = labelled >= MIN_SAMPLES and said_adopt >= 10
    ready = enough and hi <= DANGER_CEILING
    return {"cases": len(cases), "labelled": labelled, "totals": dict(tot), "cells": rows,
            "accuracy": {"right": tot["right"], "of": decided, "ci": [round(r_lo, 3), round(r_hi, 3)]},
            "danger": {"wrong_adopt": tot["dangerous"], "of_adopts": said_adopt, "ci_upper": round(hi, 3)},
            "enough_data": enough, "ready_to_discuss_promotion": ready,
            "note": ("데이터가 부족합니다(판정 %d건 중 라벨 %d건, 최소 %d건·ADOPT 10건). 지금은 결론을 내지 않습니다." % (len(cases), labelled, MIN_SAMPLES)
                     if not enough else "위험 ADOPT 비율의 95%% 상한이 %.0f%%입니다(기준 %.0f%%)." % (hi * 100, DANGER_CEILING * 100))}


def evaluate(project: Path, state_dir: str = ".solomon") -> dict:
    return matrix(collect(project, state_dir))


# ---------------------------------------------------------------- a live bot's own filter log (strictly read-only)

def _open_ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True, timeout=5)


def _load_candles(csv_path: Optional[Path]) -> list[tuple[int, float]]:
    if not csv_path:
        return []
    out = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        cols = {c.lower(): c for c in (rd.fieldnames or [])}
        tcol = next((cols[k] for k in ("ts_ms", "timestamp", "open_time", "time", "ts") if k in cols), None)
        ccol = next((cols[k] for k in ("close", "c") if k in cols), None)
        if not tcol or not ccol:
            raise ValueError("캔들 CSV에서 시간·종가 열을 찾지 못했습니다 (ts_ms/timestamp/open_time, close)")
        for row in rd:
            try:
                t = int(float(row[tcol]))
                out.append((t if t > 10**11 else t * 1000, float(row[ccol])))
            except (ValueError, TypeError):
                pass
    out.sort()
    return out


def _forward_return(candles: list[tuple[int, float]], ts_ms: int, side: str, bars: int) -> Optional[float]:
    """Signed return of entering `side` at the first bar at/after ts and closing `bars` bars later. An approximation, not the bot's real exit."""
    import bisect

    i = bisect.bisect_left(candles, (ts_ms, -1.0))
    if i + bars >= len(candles):
        return None
    a, b = candles[i][1], candles[i + bars][1]
    if a <= 0:
        return None
    r = (b - a) / a
    return r if str(side).lower() in ("long", "buy") else -r


def evaluate_bot_filter(db: Path, candles_csv: Optional[Path] = None, bars: int = 10, fee: float = 0.0008,
                        event_kind: str = "llm") -> dict:
    """How good was the bot's own LLM filter? Reads the `events` table (kind=llm) read-only.

    Counterfactual: for each decision, the signed forward return of the signal over `bars` candles minus `fee`. 'win' = positive.
    A block is GOOD when the blocked entry would have lost; an allow is GOOD when it would have won."""
    con = _open_ro(Path(db))
    try:
        rows = con.execute("select ts_ms, data from events where kind=? order by ts_ms", (event_kind,)).fetchall()
    finally:
        con.close()
    candles = _load_candles(Path(candles_csv)) if candles_csv else []
    n_off, decided = 0, []
    for ts, raw in rows:
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            continue
        reason = str(d.get("reason", ""))
        if "unavailable" in reason or "invalid" in reason:
            n_off += 1
            continue
        side = d.get("side") or d.get("signal") or ""
        blocked = bool(d.get("block"))
        r = _forward_return(candles, int(ts), side, bars) if candles else None
        net = None if r is None else r - fee
        decided.append({"ts": int(ts), "side": side, "blocked": blocked, "confidence": float(d.get("confidence") or 0), "net": net})
    labelled = [x for x in decided if x["net"] is not None]

    def stat(items: list[dict], good_if_loss: bool) -> dict:
        n = len(items)
        k = sum(1 for x in items if (x["net"] < 0) == good_if_loss)
        lo, hi = wilson(k, n)
        return {"n": n, "good": k, "rate": round(k / n, 3) if n else None, "ci": [round(lo, 3), round(hi, 3)], "enough": n >= MIN_SAMPLES}

    blocked = [x for x in labelled if x["blocked"]]
    allowed = [x for x in labelled if not x["blocked"]]
    bins = {}
    for lo_b in (0.0, 0.5, 0.6, 0.7, 0.8, 0.9):
        grp = [x for x in blocked if lo_b <= x["confidence"] < (lo_b + 0.1 if lo_b >= 0.5 else 0.5)]
        if grp:
            bins[f"{lo_b:.1f}+"] = stat(grp, True)
    base_win = sum(1 for x in labelled if x["net"] > 0) / len(labelled) if labelled else None
    return {"events": len(rows), "filter_off": n_off, "filter_off_rate": round(n_off / len(rows), 3) if rows else None,
            "decided": len(decided), "with_outcome": len(labelled), "bars": bars, "fee": fee,
            "blocked": stat(blocked, True), "allowed": stat(allowed, False), "blocked_by_confidence": bins,
            "baseline_win_rate_unfiltered": round(base_win, 3) if base_win is not None else None,
            "blocked_avoided_loss_sum": round(-sum(x["net"] for x in blocked if x["net"] < 0), 4),
            "blocked_missed_gain_sum": round(sum(x["net"] for x in blocked if x["net"] > 0), 4),
            "caveat": "종가 기준 근사 결과입니다(봇의 실제 청산 규칙·체결가가 아님). 표본이 %d건 미만인 칸은 결론이 아닙니다." % MIN_SAMPLES}


def evaluate_bot_signals(db: Path, candles_csv: Optional[Path], horizon_bars: int = 480, now_ms: Optional[int] = None) -> dict:
    """Score the trend bot's own shadow signals against what the price then did (read-only).

    ml2_pred  : direction `dir` (+1/-1) with probability `p` that the trend is right after `horizon_bars` (480 x 3 min = 24 h).
                Scored: hit rate, and hit rate by probability bin, against the base rate of 'trend direction was right'.
    news_score: risk 0..1 from the LLM. Scored: does high risk (>=0.5) precede a larger move / drawdown than low risk?
    Events whose horizon is not covered by the candle file yet are listed as waiting, never counted."""
    con = _open_ro(Path(db))
    try:
        rows = con.execute("select ts_ms, kind, data from events where kind in ('ml2_pred','news_score') order by ts_ms").fetchall()
    finally:
        con.close()
    candles = _load_candles(Path(candles_csv)) if candles_csv else []
    last_ts = candles[-1][0] if candles else 0
    ml, news, waiting, waiting_items = [], [], 0, []
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    for ts, kind, raw in rows:
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            continue
        t = int(d.get("ts") or ts)
        r = _forward_return(candles, t, "long", horizon_bars) if candles else None
        if r is None:
            waiting += 1
            ready = t + horizon_bars * 180_000  # the prices must reach this far before the event can be scored
            waiting_items.append({"kind": kind, "ts_ms": t, "ready_ms": ready, "hours_left": round(max(0.0, (ready - now_ms) / 3_600_000), 1)})
            continue
        if kind == "ml2_pred" and d.get("dir") not in (None, 0, 0.0) and d.get("p") is not None:
            ml.append({"p": float(d["p"]), "hit": (r > 0) == (float(d["dir"]) > 0)})
        elif kind == "news_score" and d.get("risk") is not None:
            import bisect
            i = bisect.bisect_left(candles, (t, -1.0))
            window = [c for _, c in candles[i:i + horizon_bars + 1]]
            dd = min(window) / window[0] - 1.0 if window and window[0] > 0 else 0.0
            news.append({"risk": float(d["risk"]), "abs_ret": abs(r), "drawdown": dd})

    def hit(items: list[dict]) -> dict:
        n = len(items)
        k = sum(1 for x in items if x["hit"])
        lo, hi = wilson(k, n)
        return {"n": n, "hits": k, "rate": round(k / n, 3) if n else None, "ci": [round(lo, 3), round(hi, 3)], "enough": n >= MIN_SAMPLES}

    bins = {}
    for lo_b, hi_b in ((0.0, 0.45), (0.45, 0.55), (0.55, 1.01)):
        grp = [x for x in ml if lo_b <= x["p"] < hi_b]
        if grp:
            bins[f"p {lo_b:.2f}~{min(hi_b, 1.0):.2f}"] = hit(grp)

    def mean(xs: list[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 5) if xs else None

    hi_r = [x for x in news if x["risk"] >= 0.5]
    lo_r = [x for x in news if x["risk"] < 0.5]
    # every scored score counts: rank correlation between the risk score and how deep the following 24 hours dipped
    sp = spearman([x["risk"] for x in news], [-x["drawdown"] for x in news])
    corr = {"n": len(news), "rho": round(sp[0], 3) if sp else None, "ci": [round(sp[1][0], 3), round(sp[1][1], 3)] if sp and sp[1] else None,
            "enough": len(news) >= MIN_SAMPLES}
    waiting_items.sort(key=lambda w: w["ready_ms"])
    return {"horizon_bars": horizon_bars, "candles_end_ts_ms": last_ts, "waiting_for_price": waiting, "waiting_items": waiting_items[:40],
            "ml2": {"overall": hit(ml), "by_probability": bins,
                    "note": "p가 높을수록 적중률이 높아지지 않으면 이 예측은 쓸모가 없습니다(보정 실패)."},
            "news": {"high_risk": {"n": len(hi_r), "mean_abs_return": mean([x["abs_ret"] for x in hi_r]), "mean_drawdown": mean([x["drawdown"] for x in hi_r])},
                     "low_risk": {"n": len(lo_r), "mean_abs_return": mean([x["abs_ret"] for x in lo_r]), "mean_drawdown": mean([x["drawdown"] for x in lo_r]),},
                     "corr": corr, "enough": corr["enough"],
                     "note": "위험 점수가 높을수록 이후 24시간의 낙폭이 깊어지면(상관 +) 뉴스 점수가 쓸모 있습니다. 전체 채점이 30건 미만이면 결론이 아닙니다."},
            "caveat": "캔들 파일이 이벤트 시각 +24시간까지 있어야 채점됩니다. 부족한 건은 waiting_for_price로 셉니다."}
