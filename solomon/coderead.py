"""Let a report read the code it is asked about.

A report written only from a README and a file list can describe nothing the code really does. This picks the files that matter
for the QUESTION (path and definition matches, then local imports), shows them line-numbered with secrets removed, and afterwards
checks every `path:line` the AIs cite against what was actually shown, so an invented citation is caught instead of trusted."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .analysis import SourceFile, _list_files, eligible_files, redact

BUDGET_CHARS = 90_000
MAX_FILE_CHARS = 16_000
MAX_FILES = 25
SCAN_LIMIT = 2500  # files opened to score them
STOP = {"the", "and", "for", "with", "how", "what", "that", "this", "from", "are", "was", "bot", "코드", "방법", "설명", "해주세요", "현재", "관련", "대해", "위한"}

# Korean words people ask with -> the identifiers they usually appear as in code
KO_HINTS = {
    "학습": ["learn", "train", "fit"], "훈련": ["train", "fit"], "모델": ["model"], "인공지능": ["model", "ml", "learn"], "머신러닝": ["ml", "learn", "model"],
    "예측": ["predict", "forecast"], "확률": ["prob", "predict"], "백테스트": ["backtest"], "검증": ["verify", "valid", "eval", "walk"], "평가": ["eval", "score"],
    "전략": ["strategy", "signal"], "신호": ["signal"], "추세": ["trend"], "펀딩": ["fund"], "손절": ["stop", "sl"], "청산": ["liquid", "close", "exit"],
    "주문": ["order"], "체결": ["fill", "order"], "포지션": ["position"], "리스크": ["risk"], "위험": ["risk"], "레버리지": ["leverage"], "노출": ["exposure"],
    "대시보드": ["dashboard"], "화면": ["dashboard", "html"], "뉴스": ["news"], "알림": ["alert", "notify", "telegram"], "텔레그램": ["telegram"],
    "선물": ["futures", "perp", "bybit"], "실거래": ["live", "broker", "order"], "페이퍼": ["paper"], "시세": ["price", "kline", "candle"], "캔들": ["candle", "kline"],
    "미결제": ["oi", "open_interest"], "롱숏": ["ls", "long_short"], "프리미엄": ["prem"], "변동성": ["vol"], "수익": ["pnl", "profit", "return"], "낙폭": ["drawdown"],
    "설정": ["config"], "저장": ["store", "save"], "재시작": ["restart"], "스케줄": ["schedule", "cron"], "지표": ["indicator", "feature"], "피처": ["feat"], "특징": ["feat"],
    "재학습": ["retrain", "refit", "train"], "라벨": ["label"], "분할": ["split", "fold"], "엘리엇": ["elliott"], "파동": ["wave", "pivot", "zigzag"],
}
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_KO = re.compile(r"[가-힣]{2,}")
_PY_IMPORT = re.compile(r"(?m)^\s*(?:from\s+\.?([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))")
_CITE = re.compile(r"`?([\w./-]+\.(?:py|js|ts|tsx|html|css|md|sh|json|toml|ya?ml|sql|go|rs|java|c|cpp|h))(?::|\s*#L|\s*:\s*L)(\d+)(?:\s*[-–~]\s*L?(\d+))?`?")


@dataclass
class CodeContext:
    files: list[SourceFile] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    skipped: list[tuple[str, float]] = field(default_factory=list)  # relevant files that did not fit the budget
    keywords: list[str] = field(default_factory=list)
    chars: int = 0
    redactions: int = 0
    total_files: int = 0

    def by_path(self) -> dict[str, SourceFile]:
        return {f.path: f for f in self.files}


def keywords(question: str) -> list[str]:
    out: list[str] = []
    for w in _WORD.findall(question or ""):
        w = w.lower()
        if w not in STOP and w not in out:
            out.append(w)
    for ko in _KO.findall(question or ""):
        for key, hints in KO_HINTS.items():
            if key in ko:
                out += [h for h in hints if h not in out]
    return out[:40]


def _is_test(rel: str) -> bool:
    parts = rel.lower().split("/")
    stem = parts[-1].split(".")[0]
    return "tests" in parts or "test" in parts or stem.startswith("test") or stem.endswith("_test")


def select(project: Path, question: str, budget_chars: int = BUDGET_CHARS, max_file_chars: int = MAX_FILE_CHARS) -> CodeContext:
    project = Path(project)
    ctx = CodeContext(keywords=keywords(question))
    tracked, _mode, _skipped = _list_files(project)
    ctx.total_files = len(tracked)
    excluded: list[tuple[str, str]] = []
    cand = eligible_files(project, tracked, excluded)[:SCAN_LIMIT]
    texts: dict[str, str] = {}
    stats: dict[str, dict[str, tuple[int, int, bool]]] = {}
    df: dict[str, int] = {k: 0 for k in ctx.keywords}
    for rel, _size in cand:
        try:
            raw = (project / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        texts[rel] = raw
        low, plow = raw.lower(), rel.lower()
        per: dict[str, tuple[int, int, bool]] = {}
        for k in ctx.keywords:
            hits = low.count(k)
            in_path = k in plow
            if not hits and not in_path:
                continue
            defs = len(re.findall(r"(?m)^\s*(?:async\s+def|def|class|function|const|let|var)\s+\w*" + re.escape(k), low)) if hits else 0
            per[k] = (hits, defs, in_path)
            if hits:
                df[k] += 1
        if per:
            stats[rel] = per
    n = max(1, len(texts))
    score: dict[str, float] = {}
    for rel, per in stats.items():
        total = 0.0
        for k, (hits, defs, in_path) in per.items():
            w = 1.0 if df[k] <= 0.15 * n else 0.25  # a word found in most files says little about which file matters
            total += (12 if in_path else 0) + 4 * defs * w + min(hits, 10) * w
        score[rel] = total * (0.5 if _is_test(rel) else 1.0)
    ranked = sorted(score, key=lambda r: (-score[r], r))
    picked: list[str] = [r for r in ranked if score[r] >= 3][:MAX_FILES]

    # one level of local imports of the best files: the code they lean on is part of the answer
    by_stem: dict[str, list[str]] = {}
    for rel in texts:
        by_stem.setdefault(os.path.splitext(os.path.basename(rel))[0], []).append(rel)
    for rel in list(picked[:4]):
        if not rel.endswith(".py"):
            continue
        for m in _PY_IMPORT.finditer(texts[rel]):
            name = (m.group(1) or m.group(2)).split(".")[-1]
            for dep in by_stem.get(name, [])[:1]:
                if dep not in picked and len(picked) < MAX_FILES:
                    picked.append(dep)
                    score.setdefault(dep, 1.0)
    if not picked:  # nothing matched the question: fall back to the README and entry points so the AI still sees the project's shape
        from .analysis import _rank
        picked = [r for r, _ in sorted(((r, _rank(r, 0)) for r in texts), key=lambda x: x[1])][:6]
        for r in picked:
            score.setdefault(r, 0.0)
    used = 0
    for rel in picked:
        text, n_red = redact(texts[rel], rel)
        truncated = len(text) > max_file_chars
        if truncated:
            text = text[:max_file_chars]
        if used + len(text) > budget_chars:
            ctx.skipped.append((rel, round(score.get(rel, 0.0), 1)))
            continue
        used += len(text)
        ctx.redactions += n_red
        ctx.files.append(SourceFile(path=rel, text=text, lines=text.count("\n") + 1, truncated=truncated))
        ctx.scores[rel] = round(score.get(rel, 0.0), 1)
    ctx.chars = used
    ctx.skipped += [(r, round(score[r], 1)) for r in ranked[MAX_FILES:MAX_FILES + 10] if score[r] >= 3 and r not in picked]
    return ctx


def render(ctx: CodeContext) -> str:
    chunks = []
    for f in ctx.files:
        numbered = "\n".join(f"{i:>4}| {line}" for i, line in enumerate(f.text.split("\n"), 1))
        chunks.append(f"=== {f.path}{' (only the first part is shown)' if f.truncated else ''} ===\n{numbered}")
    notseen = ""
    if ctx.skipped:
        notseen = "\nRELEVANT FILES NOT SHOWN (they exist but did not fit; do not describe their contents): " + ", ".join(p for p, _ in ctx.skipped[:15]) + "\n"
    return "\n\n".join(chunks) + notseen


def summary(ctx: CodeContext) -> dict:
    return {"files": [{"path": f.path, "lines": f.lines, "truncated": f.truncated, "score": ctx.scores.get(f.path, 0)} for f in ctx.files],
            "skipped": [p for p, _ in ctx.skipped[:15]], "keywords": ctx.keywords[:15], "chars": ctx.chars, "redactions": ctx.redactions, "total_files": ctx.total_files}


def verify_citations(text: str, ctx: CodeContext) -> dict:
    """Every `file:line` in the report must point at a file that was shown and at a line that exists in what was shown."""
    shown = ctx.by_path()
    seen: set[tuple[str, int]] = set()
    bad: list[str] = []
    ok = 0
    for m in _CITE.finditer(text or ""):
        path, line = m.group(1), int(m.group(2))
        f = shown.get(path) or next((v for k, v in shown.items() if k.endswith("/" + path)), None)
        key = (f.path if f is not None else path, line)  # `learn2.py:4` and `ena_bot/learn2.py:4` are one citation
        if key in seen:
            continue
        seen.add(key)
        if f is not None and 1 <= line <= f.lines:
            ok += 1
        else:
            bad.append(f"{path}:{line}")
    return {"total": len(seen), "valid": ok, "invalid": bad[:12]}
