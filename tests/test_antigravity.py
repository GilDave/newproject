import json
import os
import sys

import pytest

from solomon import bench, cli
from solomon.env import resolve_cli
from solomon.orchestrator import Orchestrator
from solomon.providers import PROVIDER_CLASSES, ProviderError, QuotaExhausted, build_providers
from solomon.providers.antigravity import NO_TOOLS, AntigravityProvider, TransientStreamError
from solomon.settings import ConfigError, Settings
from solomon.tokens import antigravity_usage

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file


def result_line(**result):
    return json.dumps({"event": "result", "result": result}) + "\n"


def test_argv_and_stdin_shape():
    p = AntigravityProvider()
    argv = p.build_argv("agy", False)
    assert argv == ["agy", "--input-format", "stream-json", "--output-format", "stream-json", "--sandbox"]
    assert "--mode" not in argv and "accept-edits" not in argv  # no write mode exists in this adapter
    stdin = p.prepare_stdin("한글 프롬프트 \"quoted\"\nsecond line")
    assert stdin.endswith("\n") and stdin.count("\n") == 1  # exactly one NDJSON line
    msg = json.loads(stdin)
    text = msg["message"]["content"][0]["text"]
    assert msg["event"] == "user" and text.startswith(NO_TOOLS) and "한글 프롬프트" in text and "second line" in text


def test_parse_output_takes_the_last_terminal_result_and_ignores_noise():
    out = "starting...\n" + json.dumps({"event": "progress"}) + "\n" + result_line(status="SUCCESS", response="first") \
        + "{broken json\n" + result_line(status="SUCCESS", response="final")
    assert AntigravityProvider().parse_output(out) == "final"


def test_parse_output_failures_are_classified():
    p = AntigravityProvider()
    with pytest.raises(TransientStreamError):
        p.parse_output(json.dumps({"event": "progress"}) + "\n")  # no result event at all
    with pytest.raises(TransientStreamError):
        p.parse_output(result_line(status="SUCCESS", response="   "))
    with pytest.raises(QuotaExhausted):
        p.parse_output(result_line(status="ERROR", error="RESOURCE_EXHAUSTED: quota exceeded for this account"))
    with pytest.raises(ProviderError) as exc:
        p.parse_output(result_line(status="ERROR", error="invalid request"))
    assert not isinstance(exc.value, (TransientStreamError, QuotaExhausted))


def test_usage_takes_the_largest_prompt_count_and_sums_candidates():
    out = '{"promptTokenCount": 100, "candidatesTokenCount": 5}\n{"promptTokenCount": 140, "candidatesTokenCount": 7}\n'
    assert antigravity_usage(out) == {"in": 140, "out": 12, "total": 152}
    assert antigravity_usage("nothing to see") is None
    assert AntigravityProvider().extract_usage(out, "") == antigravity_usage(out)


def fake_agy(tmp_path, behaviour):
    """A real executable that records what it was given. behaviour: 'ok' | 'flaky' | 'empty'."""
    log = tmp_path / "agy.log"
    counter = tmp_path / "count"
    script = tmp_path / "agy"
    script.write_text(f"""#!{sys.executable}
import json, os, sys
stdin = sys.stdin.read()
n = int(open({str(counter)!r}).read()) if os.path.exists({str(counter)!r}) else 0
open({str(counter)!r}, "w").write(str(n + 1))
open({str(log)!r}, "a").write(json.dumps({{"cwd": os.getcwd(), "listing": os.listdir("."), "args": sys.argv[1:], "stdin": stdin}}) + "\\n")
behaviour = {behaviour!r}
if behaviour == "empty" or (behaviour == "flaky" and n == 0):
    print(json.dumps({{"event": "progress"}}))
else:
    print(json.dumps({{"event": "result", "result": {{"status": "SUCCESS", "response": "pong", "u": {{"promptTokenCount": 50, "candidatesTokenCount": 7}}}}}}))
""")
    script.chmod(0o755)
    return str(script), log


def calls(log):
    return [json.loads(l) for l in log.read_text().splitlines()]


def test_real_process_runs_in_an_empty_folder_with_the_ndjson_prompt(tmp_path):
    exe, log = fake_agy(tmp_path, "ok")
    project = tmp_path / "project"
    project.mkdir()
    (project / "secret.txt").write_text("must not be visible")
    res = AntigravityProvider(executable=exe).run("judge this", cwd=str(project), write=False, timeout=30)
    assert res.text == "pong" and res.tokens == {"in": 50, "out": 7, "total": 57}
    (call,) = calls(log)
    assert call["listing"] == [] and call["cwd"] != str(project)  # an empty temp folder, not the project
    assert "--sandbox" in call["args"] and "judge this" in json.loads(call["stdin"])["message"]["content"][0]["text"]
    assert not os.path.exists(call["cwd"])  # and it is cleaned up afterwards


def test_one_transient_failure_is_retried_but_not_two(tmp_path):
    exe, log = fake_agy(tmp_path, "flaky")
    assert AntigravityProvider(executable=exe).run("x", cwd=str(tmp_path), write=False, timeout=30).text == "pong"
    assert len(calls(log)) == 2
    sub = tmp_path / "again"
    sub.mkdir()
    exe2, log2 = fake_agy(sub, "empty")
    with pytest.raises(TransientStreamError):
        AntigravityProvider(executable=exe2).run("x", cwd=str(tmp_path), write=False, timeout=30)
    assert len(calls(log2)) == 2  # initial attempt plus exactly one retry


def test_write_calls_are_refused_before_any_process_starts(tmp_path):
    exe, log = fake_agy(tmp_path, "ok")
    with pytest.raises(ProviderError, match="cannot be a builder"):
        AntigravityProvider(executable=exe).run("x", cwd=str(tmp_path), write=True, timeout=30)
    assert not log.exists()


def test_antigravity_cannot_be_configured_as_builder():
    s = make_settings()
    s.roles = {"planner": "claude", "builder": "antigravity", "reviewer": "claude"}
    providers = {"claude": ScriptedProvider("claude", []), "codex": ScriptedProvider("codex", []),
                 "antigravity": AntigravityProvider()}
    with pytest.raises(ConfigError, match="read-only roles"):
        Orchestrator(s, providers)


def test_antigravity_as_planner_and_reviewer_in_a_three_ai_run(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings()
    s.roles = {"planner": "antigravity", "builder": "codex", "reviewer": "antigravity"}
    agy = ScriptedProvider("antigravity", [order_json(), review_json()])
    agy.supports_write = False
    codex = ScriptedProvider("codex", [write_file("src/new.py")])
    result = Orchestrator(s, {"claude": ScriptedProvider("claude", []), "codex": codex, "antigravity": agy}).run("add new module", repo)
    assert result.status == "PASSED" and len(agy.calls) == 2 and len(codex.calls) == 1


def test_registry_doctor_and_default_experiment(capsys):
    assert list(PROVIDER_CLASSES) == ["claude", "codex", "antigravity"]
    assert isinstance(build_providers(Settings())["antigravity"], AntigravityProvider)
    cli.main(["doctor"])
    assert "antigravity" in capsys.readouterr().out
    assert len(bench.DEFAULT_CONFIGS) == 4 and not any("antigravity" in c for c in bench.DEFAULT_CONFIGS)
    for name in bench.CONFIGS:
        if "antigravity" in name:
            s = bench.settings_for(Settings(), name)
            assert s.roles["builder"] != "antigravity" and not s.allow_same_provider


def test_agy_is_found_in_its_own_install_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".agy" / "bin").mkdir(parents=True)
    exe = home / ".agy" / "bin" / "agy"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/nonexistent")
    assert resolve_cli("agy") == str(exe.resolve())
