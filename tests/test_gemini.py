import json

import pytest

from solomon import bench, cli
from solomon.env import sanitized_env
from solomon.orchestrator import Orchestrator
from solomon.providers import PROVIDER_CLASSES, ProviderError, QuotaExhausted, build_providers
from solomon.providers.base import looks_like_quota
from solomon.providers.gemini import GeminiProvider
from solomon.settings import Settings, load_settings
from solomon.tokens import gemini_usage

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file


def test_argv_read_only_and_write():
    ro = GeminiProvider().build_argv("gemini", False)
    assert ro[0] == "gemini" and "-p" in ro and "--output-format" in ro and "--approval-mode" not in ro
    rw = GeminiProvider().build_argv("gemini", True)
    assert rw[-2:] == ["--approval-mode", "auto_edit"]
    assert "--yolo" not in rw and "-y" not in rw  # never auto-approve shell commands
    assert GeminiProvider(extra_args=["-m", "gemini-2.5-pro"]).build_argv("gemini", False)[-2:] == ["-m", "gemini-2.5-pro"]


def test_parse_output():
    p = GeminiProvider()
    assert p.parse_output(json.dumps({"response": "hello", "stats": {}})) == "hello"
    assert p.parse_output("plain text answer") == "plain text answer"
    with pytest.raises(QuotaExhausted):
        p.parse_output(json.dumps({"error": {"message": "RESOURCE_EXHAUSTED: quota exceeded"}}))
    with pytest.raises(ProviderError):
        p.parse_output(json.dumps({"error": {"message": "invalid argument"}}))


def test_usage_sums_models_and_never_invents_numbers():
    out = json.dumps({"response": "x", "stats": {"models": {
        "gemini-2.5-pro": {"tokens": {"prompt": 100, "candidates": 20, "total": 150, "cached": 40}},
        "gemini-2.5-flash": {"tokens": {"prompt": 10, "candidates": 5}},
    }}})
    assert gemini_usage(out) == {"in": 110, "out": 25, "total": 165, "cache_read": 40}
    for bad in ["not json", "{}", json.dumps({"stats": {"models": {"m": {"tokens": {}}}}}), json.dumps({"stats": []})]:
        assert gemini_usage(bad) is None  # falls back to a labelled estimate
    assert GeminiProvider().extract_usage(out, "") == gemini_usage(out)


@pytest.mark.parametrize("text", ["RESOURCE_EXHAUSTED", "You have exhausted your capacity on this model", "429 Too Many Requests"])
def test_gemini_quota_phrases(text):
    assert looks_like_quota(text)


def test_registry_and_doctor_know_gemini(capsys):
    assert list(PROVIDER_CLASSES) == ["claude", "codex", "gemini"]
    assert isinstance(build_providers(Settings())["gemini"], GeminiProvider)
    cli.main(["doctor"])
    assert "gemini" in capsys.readouterr().out


def test_billing_switches_are_removed_for_gemini(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
        monkeypatch.setenv(k, "1")
    env = sanitized_env()
    assert not any(k in env for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"))


def test_gemini_can_take_any_role_and_cross_review_still_holds(tmp_path):
    repo = make_repo(tmp_path)
    settings = make_settings()
    settings.roles = {"planner": "claude", "builder": "gemini", "reviewer": "codex"}  # three different AIs
    claude = ScriptedProvider("claude", [order_json()])
    gemini = ScriptedProvider("gemini", [write_file("src/new.py")])
    codex = ScriptedProvider("codex", [review_json()])
    result = Orchestrator(settings, {"claude": claude, "gemini": gemini, "codex": codex}).run("add new module", repo)
    assert result.status == "PASSED"
    assert [len(x.calls) for x in (claude, gemini, codex)] == [1, 1, 1]


def test_builder_and_reviewer_cannot_both_be_gemini_by_default():
    from solomon.settings import ConfigError

    s = make_settings()
    s.roles = {"planner": "claude", "builder": "gemini", "reviewer": "gemini"}
    with pytest.raises(ConfigError):
        Orchestrator(s, {n: ScriptedProvider(n, []) for n in ("claude", "codex", "gemini")})


def test_roles_can_be_set_to_gemini_in_the_config(tmp_path):
    cfg = tmp_path / "solomon.toml"
    cfg.write_text('[roles]\nreviewer = "gemini"\n[extra_args]\ngemini = ["-m", "x"]\n')
    s = load_settings(cfg)
    assert s.roles["reviewer"] == "gemini"
    assert build_providers(s)["gemini"].build_argv("gemini", False)[-2:] == ["-m", "x"]


def test_default_experiment_is_unchanged_and_gemini_configs_are_opt_in():
    assert len(bench.DEFAULT_CONFIGS) == 4 and not any("gemini" in c for c in bench.DEFAULT_CONFIGS)
    cross = bench.settings_for(Settings(), "gemini_build_claude_review")
    assert cross.roles["builder"] == "gemini" and cross.roles["reviewer"] == "claude" and not cross.allow_same_provider
    assert bench.settings_for(Settings(), "gemini_solo").allow_same_provider is True
