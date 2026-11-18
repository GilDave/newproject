"""Choosing which AI takes which role, from the command line and from the dashboard."""
import pytest

from solomon import cli

from test_dashboard import Env, sleeper


@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    e = Env(tmp_path)
    yield e
    e.close()


def run_body(env, **roles):
    return {"goal": "do it", "project": env.project, "roles": roles}


# ------------------------------------------------------------------ CLI
def test_cli_role_flags_override_the_config(monkeypatch, tmp_path):
    seen = {}

    class FakeOrch:
        def __init__(self, settings):
            seen["roles"] = dict(settings.roles)

        def run(self, goal, project, keep_worktree=False):
            from solomon.orchestrator import RunResult

            return RunResult(run_id="r", status="PASSED", summary="ok", run_dir=str(tmp_path))

    monkeypatch.setattr(cli, "Orchestrator", FakeOrch)
    cli.main(["run", "g", "--project", str(tmp_path), "--planner", "antigravity", "--reviewer", "antigravity"])
    assert seen["roles"] == {"planner": "antigravity", "builder": "codex", "reviewer": "antigravity"}
    cli.main(["run", "g", "--project", str(tmp_path)])
    assert seen["roles"] == {"planner": "claude", "builder": "codex", "reviewer": "claude"}  # no flags: config default


def test_cli_refuses_antigravity_as_builder_and_unknown_ais(capsys, tmp_path):
    assert cli.main(["run", "g", "--project", str(tmp_path), "--builder", "antigravity"]) == 2
    assert "cannot be the builder" in capsys.readouterr().err
    assert cli.main(["run", "g", "--project", str(tmp_path), "--planner", "nope"]) == 2
    assert "unknown provider" in capsys.readouterr().err
    assert cli.main(["run", "g", "--project", str(tmp_path), "--builder", "claude", "--reviewer", "claude"]) == 2
    assert "different providers" in capsys.readouterr().err


# ------------------------------------------------------------ dashboard
def test_state_tells_the_page_which_ais_can_build(env):
    providers = {p["name"]: p for p in env.state()[1]["providers"]}
    assert providers["antigravity"]["supports_write"] is False
    assert providers["claude"]["supports_write"] is True and providers["codex"]["supports_write"] is True


def test_roles_chosen_on_the_page_become_run_flags(env):
    status, _, _ = env.call("POST", "/api/run", run_body(env, planner="claude", builder="codex", reviewer="antigravity"))
    assert status == 200
    args = sleeper.calls[-1]
    pairs = {args[i][2:]: args[i + 1] for i in range(len(args) - 1) if args[i] in ("--planner", "--builder", "--reviewer")}
    assert pairs == {"planner": "claude", "builder": "codex", "reviewer": "antigravity"}


def test_a_partial_choice_keeps_the_config_default_for_the_rest(env):
    assert env.call("POST", "/api/run", run_body(env, reviewer="antigravity"))[0] == 200
    assert "--reviewer" in sleeper.calls[-1] and "--builder" not in sleeper.calls[-1]


@pytest.mark.parametrize(
    "roles,fragment",
    [
        ({"planner": "nope"}, "알 수 없는 AI"),
        ({"builder": "antigravity"}, "구현을 맡을 수 없습니다"),
        ({"builder": "claude", "reviewer": "claude"}, "서로 다른 AI"),
        ({"reviewer": "codex"}, "서로 다른 AI"),  # the config default builder is codex
    ],
)
def test_bad_role_choices_are_rejected_before_anything_is_launched(env, roles, fragment):
    status, body, _ = env.call("POST", "/api/run", run_body(env, **roles))
    assert status == 400 and fragment in body["error"]
    assert not sleeper.calls


def test_roles_must_be_an_object(env):
    assert env.call("POST", "/api/run", {"goal": "x", "project": env.project, "roles": ["claude"]})[0] == 400
