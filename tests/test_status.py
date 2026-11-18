import json
from pathlib import Path

from solomon import cli
from solomon.orchestrator import Orchestrator
from solomon.providers.claude import ClaudeProvider
from solomon.providers.codex import CodexProvider
from solomon.status import load_runs, render, summarize_run
from solomon.tokens import claude_usage, codex_usage, estimate_tokens

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file


def ev(kind, ts="2026-11-18T20:15:28", **kw):
    return {"ts": ts, "event": kind, **kw}


def test_claude_usage_sums_cache_tokens():
    out = json.dumps({"result": "x", "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5}})
    expected = {"in": 100, "out": 5, "total": 105, "cache_read": 90, "cache_create": 0}
    assert claude_usage(out) == expected
    assert ClaudeProvider().extract_usage(out, "") == expected
    assert claude_usage("not json") is None
    assert claude_usage('{"result": "x"}') is None


def test_codex_usage_reads_total_line():
    assert codex_usage("", "tokens used\n12,345\nresult") == {"in": None, "out": None, "total": 12345}
    assert CodexProvider().extract_usage("tokens used: 7", "") == {"in": None, "out": None, "total": 7}
    assert codex_usage("nothing here", "") is None


def test_estimate_is_larger_for_hangul_than_ascii():
    assert estimate_tokens("가나다라마바사아") > estimate_tokens("abcdefgh")
    assert estimate_tokens("") == 0


def test_summarize_mixes_exact_and_estimated_tokens():
    events = [
        ev("run_start", goal="g", warnings=["w1"]),
        ev("attempt_start", attempt=1),
        ev("call_start", role="planner", provider="claude", prompt_chars=100),
        ev("call_end", role="planner", provider="claude", duration_sec=10, tokens={"total": 500}, tokens_est={"in": 1, "out": 1}),
        ev("call_start", role="builder", provider="codex", prompt_chars=100),
        ev("call_end", role="builder", provider="codex", duration_sec=20, tokens=None, tokens_est={"in": 30, "out": 70}),
        ev("quota", provider="codex"),
    ]
    run = summarize_run("r1", events)
    assert run.goal == "g" and run.attempts == 1 and run.stage == "한도 소진"
    assert run.providers["claude"].tokens == 500 and run.providers["claude"].estimated_calls == 0
    assert run.providers["codex"].tokens == 100 and run.providers["codex"].estimated_calls == 1
    assert not run.finished and "진행 중" in run.status


def test_old_logs_without_token_fields_still_summarize():
    events = [
        ev("run_start", goal="old"),
        ev("call_start", role="builder", provider="codex", prompt_chars=300),
        ev("call_end", role="builder", provider="codex", duration_sec=5, output_chars=300),
        ev("run_end", ts="2026-11-18T20:16:28", status="PASSED", summary="ok", attempts=1),
    ]
    run = summarize_run("old", events)
    assert run.finished and run.status == "PASSED"
    assert run.providers["codex"].tokens == 200 and run.providers["codex"].estimated_calls == 1
    assert run.elapsed_sec() == 60


def test_status_for_a_real_pipeline_run_and_cli(tmp_path, capsys):
    repo = make_repo(tmp_path)
    settings = make_settings()
    claude = ScriptedProvider("claude", [order_json(), review_json()])
    codex = ScriptedProvider("codex", [write_file("src/new.py")])
    result = Orchestrator(settings, {"claude": claude, "codex": codex}).run("add new module", repo)
    assert result.status == "PASSED"

    runs = load_runs(repo)
    assert len(runs) == 1 and runs[0].status == "PASSED" and runs[0].attempts == 1
    assert set(runs[0].providers) == {"claude", "codex"}
    assert runs[0].providers["claude"].calls == 2 and runs[0].providers["codex"].calls == 1
    text = render(runs, repo)
    assert "AI별 작업량" in text and "PASSED" in text and "진행 기록" in text

    assert cli.main(["status", "--project", str(repo)]) == 0
    assert "add new module" in capsys.readouterr().out
    assert cli.main(["status", "--project", str(repo), "--run", "nope"]) == 2


def test_status_with_no_runs(tmp_path):
    assert "아직 실행 기록이 없습니다" in render([], Path(tmp_path))


def test_partial_last_line_is_ignored(tmp_path):
    d = tmp_path / ".solomon" / "runs" / "r1"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(json.dumps(ev("run_start", goal="g")) + '\n{"event": "call_st', encoding="utf-8")
    runs = load_runs(tmp_path)
    assert runs[0].goal == "g"


def test_status_shows_cache_share_and_per_call_tokens():
    events = [
        ev("run_start", goal="g"),
        ev("call_start", role="reviewer", provider="claude", prompt_chars=10),
        ev("call_end", role="reviewer", provider="claude", duration_sec=9,
           tokens={"total": 61000, "cache_read": 50000}, tokens_est={"in": 1, "out": 1}),
    ]
    run = summarize_run("r", events)
    text = render([run], Path("."))
    assert "캐시 읽기 50,000 포함" in text
    assert "reviewer · claude · 9초 · 토큰 61,000" in text
