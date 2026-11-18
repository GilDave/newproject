"""Dashboard side of analysis and of the work room: API, state and the per-attempt details."""
import json
import time
from pathlib import Path

import pytest

from solomon.analysis import analysis_view, list_analyses

from test_dashboard import Env, sleeper

ALL = [{"name": "claude", "installed": True, "supports_write": True},
       {"name": "codex", "installed": True, "supports_write": True},
       {"name": "antigravity", "installed": True, "supports_write": False}]


@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    e = Env(tmp_path)
    e.app._providers_cache = (time.time() + 10**6, ALL)  # do not depend on which CLIs this machine has
    yield e
    e.close()


def write_events(env, aid, events):
    d = Path(env.project) / ".solomon" / "analyses" / aid
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return d


def start(aid="20261118-100000-aaaaaa", **extra):
    return [{"ts": "2026-11-18T10:00:00", "event": "analysis_start", "project": "p", "focus": "f", "ais": ["claude", "codex"]}, *extra.get("more", [])]


# ------------------------------------------------------------------ API
def test_analyze_starts_a_separate_process_with_the_chosen_ais_and_focus(env):
    status, body, _ = env.call("POST", "/api/analyze", {"project": env.project, "focus": "에러 위주", "ais": ["claude", "codex"]})
    assert status == 200 and body["job"]["kind"] == "analyze" and body["job"]["state"] == "running"
    args = sleeper.calls[-1]
    assert args[1:4] == ["-m", "solomon", "analyze"] and env.project in args
    assert args[args.index("--ais") + 1] == "claude,codex" and args[args.index("--focus") + 1] == "에러 위주"


def test_analyze_defaults_to_every_installed_ai_and_may_have_no_focus(env):
    assert env.call("POST", "/api/analyze", {"project": env.project})[0] == 200
    args = sleeper.calls[-1]
    assert args[args.index("--ais") + 1] == "claude,codex,antigravity" and "--focus" not in args


@pytest.mark.parametrize("body,fragment", [
    ({"ais": ["nope"]}, "알 수 없는 AI"),
    ({"ais": "claude"}, "형식"),
    ({"focus": "x" * 20001}, "너무 깁니다"),
])
def test_analyze_rejects_bad_requests_before_launching(env, body, fragment):
    status, resp, _ = env.call("POST", "/api/analyze", {"project": env.project, **body})
    assert status == 400 and fragment in resp["error"] and not sleeper.calls


def test_analyze_refuses_a_missing_ai_a_non_repository_and_a_second_job(env, tmp_path):
    env.app._providers_cache = (time.time() + 10**6, [{**ALL[0]}, {**ALL[1], "installed": False}, {**ALL[2], "installed": False}])
    assert env.call("POST", "/api/analyze", {"project": env.project, "ais": ["codex"]})[1]["error"].startswith("설치되지 않은 AI")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert env.call("POST", "/api/analyze", {"project": str(plain)})[0] == 400
    assert env.call("POST", "/api/analyze", {"project": env.project})[0] == 200
    assert env.call("POST", "/api/analyze", {"project": env.project})[0] == 409  # one job at a time
    assert env.call("POST", "/api/run", {"goal": "x", "project": env.project})[0] == 409  # also across kinds


def test_stop_works_for_an_analysis_job_too(env):
    env.call("POST", "/api/analyze", {"project": env.project})
    assert env.call("POST", "/api/stop", {})[0] == 200
    deadline = time.time() + 8
    while time.time() < deadline and env.state()[1]["job"]["state"] == "running":
        time.sleep(0.2)
    assert env.state()[1]["job"]["state"] == "exited"


# ---------------------------------------------------------------- state
def test_state_shows_a_running_analysis_with_per_ai_progress(env):
    env.call("POST", "/api/analyze", {"project": env.project})
    write_events(env, "20261118-100000-aaaaaa", start(more=[
        {"ts": "2026-11-18T10:00:01", "event": "collect_end", "included": 5, "total": 9, "excluded": 4, "redactions": 2},
        {"ts": "2026-11-18T10:00:02", "event": "ai_start", "provider": "claude"},
        {"ts": "2026-11-18T10:00:02", "event": "ai_start", "provider": "codex"},
        {"ts": "2026-11-18T10:00:30", "event": "ai_end", "provider": "claude", "ok": True, "state": "done", "duration_sec": 28.0,
         "tokens": {"total": 9000}, "findings": 2, "verified": 2}]))
    an = env.state()[1]["analysis"]
    assert an["live"] is True and an["interrupted"] is False and an["stage_key"] == "ai"
    assert an["ais"]["claude"]["state"] == "done" and an["ais"]["codex"]["state"] == "running" and an["active_ais"] == ["codex"]
    assert an["coverage"]["included"] == 5 and an["coverage"]["redactions"] == 2
    assert an["providers"]["claude"]["tokens"] == 9000 and an["calls"][0]["role"] == "analysis"


def test_an_unfinished_analysis_without_a_job_is_interrupted_not_live(env):
    write_events(env, "20261118-100000-aaaaaa", start())
    an = env.state()[1]["analysis"]
    assert an["live"] is False and an["interrupted"] is True


def test_finished_analysis_carries_clusters_and_coverage_for_the_page(env):
    aid = "20261118-100000-aaaaaa"
    d = write_events(env, aid, start(more=[{"ts": "2026-11-18T10:01:00", "event": "analysis_end", "status": "DONE", "summary": "끝"}]))
    (d / "analysis.json").write_text(json.dumps({
        "clusters": [{"id": 1, "file": "a.py", "severity": "high", "ais": ["claude"], "agreement": 1, "verified": True, "members": []}],
        "coverage": {"included": 3, "total": 4, "excluded": 1, "excluded_list": [["x.png", "바이너리"]]},
        "ais": {"claude": {"ok": True, "summary": "요약", "architecture": ["a"], "questions": ["q"], "findings": 1, "verified": 1}}}))
    (d / "report.md").write_text("# r")
    st = env.state()[1]
    assert st["analysis"]["finished"] and st["analysis"]["status"] == "DONE" and st["analysis"]["clusters"][0]["file"] == "a.py"
    assert st["analysis"]["ais"]["claude"]["summary"] == "요약" and st["analysis"]["report_path"].endswith("report.md")
    assert st["analysis"]["coverage"]["excluded_list"] == [["x.png", "바이너리"]]
    assert [a["analysis_id"] for a in st["analyses"]] == [aid] and st["analyses"][0]["finished"] is True


def test_an_analysis_can_be_selected_and_a_bad_id_is_ignored(env):
    for aid in ("20261118-100000-aaaaaa", "20261118-110000-bbbbbb"):
        write_events(env, aid, start())
    assert env.state()[1]["analysis"]["analysis_id"] == "20261118-110000-bbbbbb"  # the newest by default
    assert env.state(analysis="20261118-100000-aaaaaa")[1]["analysis"]["analysis_id"] == "20261118-100000-aaaaaa"
    assert env.state(analysis="../../etc")[1]["analysis"]["analysis_id"] == "20261118-110000-bbbbbb"


def test_analysis_view_reads_a_half_written_log_and_quota_failures():
    events = [{"ts": "2026-11-18T10:00:00", "event": "analysis_start", "focus": "", "ais": ["claude", "codex"]},
              {"ts": "2026-11-18T10:00:05", "event": "ai_end", "provider": "codex", "ok": False, "state": "quota", "error": "limit", "tokens": None},
              {"ts": "2026-11-18T10:00:06", "event": "verify_start"}]
    v = analysis_view(events)
    assert v["ais"]["codex"]["state"] == "quota" and v["quota_providers"] == ["codex"] and v["stage_key"] == "verify"
    assert v["ais"]["claude"]["state"] == "waiting" and v["finished"] is False
    assert any("실패" in line for line in v["timeline"])


def test_list_analyses_is_newest_first_and_skips_empty_folders(env):
    write_events(env, "20261118-100000-aaaaaa", start())
    write_events(env, "20261118-120000-cccccc", start())
    (Path(env.project) / ".solomon" / "analyses" / "20261118-130000-empty").mkdir()
    assert [a["analysis_id"] for a in list_analyses(Path(env.project), ".solomon")] == ["20261118-120000-cccccc", "20261118-100000-aaaaaa"]


# --------------------------------------------------------- work room data
def test_current_run_carries_per_attempt_details_for_the_work_room(env):
    env.finish_a_run()
    cur = env.state()[1]["current"]
    (att,) = cur["attempt_details"]
    assert att["attempt"] == 1 and att["builder"]["provider"] == "codex"
    assert [c["path"] for c in att["changes"]] == ["src/new.py"] and att["changes"][0]["status"] == "A"
    assert att["verify"]["passed"] is True and att["verify"]["command"].startswith("python3") and att["violations"] == []
    assert att["review"] == {"verdict": "PASS", "issues": []}
    assert cur["paths"]["run_dir"].endswith(cur["run_id"]) and cur["paths"]["patch"].endswith("result.patch")


def test_provider_cards_and_pending_applies(env):
    env.finish_a_run()
    st = env.state()[1]
    cards = {c["name"]: c for c in st["provider_cards"]}
    assert cards["claude"]["vendor"] == "Anthropic" and cards["codex"]["vendor"] == "OpenAI" and cards["antigravity"]["vendor"] == "Google"
    assert cards["claude"]["status"] == "정상" and cards["claude"]["calls_today"] == 2 and cards["codex"]["calls_today"] == 1
    assert cards["codex"]["last_call"]["role"] == "builder" and cards["antigravity"]["last_call"] is None
    assert cards["antigravity"]["supports_write"] is False
    assert st["apply_pending"] == 1
    env.call("POST", "/api/apply", {"project": env.project, "run_id": st["current"]["run_id"]})
    assert env.state()[1]["apply_pending"] == 0


def test_provider_card_reports_a_missing_cli(env):
    env.app._providers_cache = (time.time() + 10**6, [{**ALL[0], "installed": False}, ALL[1], ALL[2]])
    assert {c["name"]: c["status"] for c in env.state()[1]["provider_cards"]}["claude"] == "설치 안 됨"
