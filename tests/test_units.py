import os
import sys
import time

import pytest

from solomon import scope_guard
from solomon.env import FORBIDDEN_API_KEYS, sanitized_env
from solomon.jsonutil import extract_json
from solomon.providers.base import looks_like_quota
from solomon.providers.claude import ClaudeProvider
from solomon.providers.codex import CodexProvider
from solomon.runner import run_process
from solomon.schemas import Order, ReviewResult
from solomon.scope_guard import Change
from solomon.settings import ConfigError, load_settings


def make_order(**kw):
    base = dict(goal="g", allowed_paths=["src/", "tests/test_*.py"], protected_paths=["src/secret.py"])
    base.update(kw)
    return Order(**base)


# ---- scope guard
def test_scope_guard_accepts_in_scope_changes():
    assert scope_guard.check(make_order(), [Change("src/a.py", "A", 10, 0), Change("tests/test_a.py", "A", 5, 0)]) == []


def test_scope_guard_blocks_outside_protected_and_deletions():
    v = scope_guard.check(
        make_order(),
        [Change("README.md", "M"), Change("src/secret.py", "M"), Change("src/a.py", "D")],
    )
    assert any("README.md" in x and "outside" in x for x in v)
    assert any("secret.py" in x and "protected" in x for x in v)
    assert any("a.py" in x and "deletion" in x for x in v)


def test_scope_guard_limits_files_and_lines():
    order = make_order(max_files_changed=1, max_diff_lines=10)
    v = scope_guard.check(order, [Change("src/a.py", "M", 8, 4), Change("src/b.py", "M", 1, 0)])
    assert any("files changed" in x for x in v) and any("diff lines" in x for x in v)


def test_path_matching_is_not_a_prefix_trick():
    assert scope_guard.path_matches("src/a.py", "src/")
    assert not scope_guard.path_matches("src2/a.py", "src")


# ---- schemas
@pytest.mark.parametrize("bad", ["*", ".", "/etc", "../x", "src/../../x", ""])
def test_order_rejects_dangerous_paths(bad):
    with pytest.raises(ValueError):
        Order.from_dict({"allowed_paths": [bad]}, "g", 15, 1500)


def test_review_requires_a_real_verdict():
    assert ReviewResult.from_dict({"verdict": "pass"}).verdict == "PASS"
    with pytest.raises(ValueError):
        ReviewResult.from_dict({"verdict": "maybe"})


# ---- json
def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('here you go\n```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('text {"a": 3} more') == {"a": 3}
    with pytest.raises(ValueError):
        extract_json("no json here")


# ---- env and providers
def test_api_keys_are_stripped(monkeypatch):
    for key in FORBIDDEN_API_KEYS:
        monkeypatch.setenv(key, "secret")
    env = sanitized_env()
    assert not any(k in env for k in FORBIDDEN_API_KEYS)
    assert os.environ["OPENAI_API_KEY"] == "secret"  # only the copy is cleaned


def test_cli_argv_shapes():
    assert ClaudeProvider().build_argv("claude", False) == ["claude", "--print", "--output-format", "json"]
    assert "acceptEdits" in ClaudeProvider().build_argv("claude", True)
    assert CodexProvider().build_argv("codex", False) == ["codex", "exec", "--sandbox", "read-only", "-"]
    assert CodexProvider().build_argv("codex", True)[3] == "workspace-write"


def test_quota_phrases():
    assert looks_like_quota("Error: usage limit reached, resets at 5pm")
    assert not looks_like_quota("SyntaxError: invalid syntax")


def test_claude_envelope_error_is_classified():
    from solomon.providers import ProviderError, QuotaExhausted

    p = ClaudeProvider()
    assert p.parse_output('{"result": "hello", "is_error": false}') == "hello"
    with pytest.raises(QuotaExhausted):
        p.parse_output('{"result": "5-hour usage limit reached", "is_error": true}')
    with pytest.raises(ProviderError):
        p.parse_output('{"result": "invalid request", "is_error": true}')


# ---- runner
def test_runner_captures_output_and_stdin():
    res = run_process([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], stdin_data="abc")
    assert res.exit_code == 0 and "ABC" in res.stdout and not res.timed_out


def test_runner_hard_timeout_kills_the_process():
    start = time.time()
    res = run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert res.timed_out and "HARD_TIMEOUT" in res.timeout_reason
    assert time.time() - start < 10


def test_runner_no_output_timeout():
    res = run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=20, no_output_timeout=0.5)
    assert res.timed_out and "NO_OUTPUT_TIMEOUT" in res.timeout_reason


# ---- settings
def test_load_settings_from_toml(tmp_path):
    cfg = tmp_path / "solomon.toml"
    cfg.write_text(
        '[roles]\nbuilder = "claude"\nreviewer = "codex"\n'
        '[loop]\non_quota = "wait"\nmax_attempts = 5\n'
        '[verification]\ntest_command = ["pytest", "-q"]\n',
        encoding="utf-8",
    )
    s = load_settings(cfg)
    assert s.roles["builder"] == "claude" and s.roles["reviewer"] == "codex" and s.roles["planner"] == "claude"
    assert s.on_quota == "wait" and s.max_attempts == 5 and s.test_command == ["pytest", "-q"]


def test_bad_config_is_rejected(tmp_path):
    cfg = tmp_path / "bad.toml"
    cfg.write_text('[loop]\non_quota = "ignore"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(cfg)
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "missing.toml")
