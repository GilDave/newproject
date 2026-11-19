import json
from pathlib import Path
from urllib.parse import quote

import pytest

from solomon import arbiter as A
from solomon import cli, manage
from solomon.orchestrator import Orchestrator
from solomon.providers import ProviderError, QuotaExhausted
from solomon.schemas import Order
from solomon.settings import ConfigError, Settings, load_settings

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file


def verdict(decision="ADOPT", confidence=0.9, reason="증거가 깨끗합니다", conditions=()):
    return json.dumps({"decision": decision, "confidence": confidence, "reason": reason, "conditions": list(conditions)}, ensure_ascii=False)


def build(arbiter, claude=(), codex=(), settings=None, builder2=None):
    s = settings or make_settings()
    s.roles["arbiter"] = "agy"
    agy = ScriptedProvider("agy", list(arbiter))
    agy.supports_write = False
    ps = {"claude": ScriptedProvider("claude", list(claude)), "codex": ScriptedProvider("codex", list(codex)), "agy": agy}
    return Orchestrator(s, ps), ps


def events(r):
    return [json.loads(l) for l in (Path(r.run_dir) / "events.jsonl").read_text().splitlines()]


# ------------------------------------------------------------------ the pure parts
@pytest.mark.parametrize("text", ['{"decision": "adopt", "confidence": 0.7, "reason": "ok"}', '{"decision": "need human", "confidence": "0.5", "reason": "x"}',
                                  '```json\n{"decision": "REJECT", "confidence": 2, "reason": "r", "conditions": ["a"]}\n```'])
def test_parse_accepts_sloppy_but_clear_answers(text):
    d = A.parse(text)
    assert d.decision in A.DECISIONS and 0 <= d.confidence <= 1


@pytest.mark.parametrize("text", ['{"decision": "MAYBE", "confidence": 0.5}', '{"confidence": 0.5}', '{"decision": "ADOPT", "confidence": "high"}', "no json"])
def test_parse_rejects_unclear_answers(text):
    with pytest.raises(ValueError):
        A.parse(text)


def test_policy_only_ever_makes_it_stricter():
    P = lambda d, c, rej=0, risky=(): A.apply_policy(A.Decision(d, c, "r"), 0.6, rej, 2, list(risky)).final
    assert P("ADOPT", 0.9) == "ADOPT" and P("ADOPT", 0.59) == "NEED_HUMAN" and P("ADOPT", 0.95, risky=["src/order_exec.py"]) == "NEED_HUMAN"
    assert P("REJECT", 0.9) == "REJECT" and P("REJECT", 0.9, rej=2) == "NEED_HUMAN" and P("NEED_HUMAN", 0.99) == "NEED_HUMAN"
    for d in A.DECISIONS:  # nothing is ever turned INTO an adopt
        for c in (0.0, 0.5, 1.0):
            for rej in (0, 5):
                for risky in ([], ["config.py"]):
                    assert d == "ADOPT" or P(d, c, rej, risky) != "ADOPT"
    assert A.unavailable("x").final == "NEED_HUMAN"


def test_risky_path_names():
    assert A.risk_paths(["src/app.py", "ena_bot/order_executor.py", "ena_bot/risk.py", "settings/.env", "docs/readme.md", "ena_bot/config.py"]) == \
        ["ena_bot/order_executor.py", "ena_bot/risk.py", "settings/.env", "ena_bot/config.py"]


def test_the_evidence_is_measured_facts_not_opinions():
    from solomon.scope_guard import Change
    from solomon.verifier import VerifyResult
    ev = A.build_evidence(Order("g", ["src/"]), 2, [Change("src/a.py", "M", 10, 2), Change("src/risk_limits.py", "M", 1, 1)],
                          VerifyResult(True, False, "pytest", 0, "96 passed in 3s"), True,
                          [{"provider": "claude", "verdict": "PASS", "issues": []}, {"provider": "agy", "verdict": "PASS", "issues": ["nit"]}],
                          ["시도 1: tests failed"], "diff --git a/x b/x\n+" + "y" * 5000, 100)
    for needle in ("96 tests", "2 file(s), +11 -3", "src/risk_limits.py", "claude: PASS", "agy: PASS | issues: nit", "earlier attempts failed", "second run: passed",
                   "diff truncated", "HIGH-RISK PATH NAMES TOUCHED: ['src/risk_limits.py']"):
        assert needle in ev, needle
    assert "untrusted" in A.prompt(ev) and "ONLY one JSON" in A.prompt(ev)


# ------------------------------------------------------------------ in the pipeline
def test_adopt_passes_and_is_recorded_with_its_evidence(tmp_path):
    repo = make_repo(tmp_path)
    o, ps = build([verdict("ADOPT", 0.9)], [order_json(), review_json()], [write_file("src/new.py")])
    r = o.run("add module", repo)
    assert r.status == "PASSED" and r.needs_human is False and r.arbiter["final"] == "ADOPT"
    assert len(ps["agy"].calls) == 1 and ps["agy"].calls[0]["write"] is False
    p = ps["agy"].calls[0]["prompt"]
    assert "TESTS: passed" in p and "src/new.py" in p and "claude: PASS" in p and "scope check" in p.lower()
    ev = [e for e in events(r) if e["event"] == "arbiter"]
    assert ev[0]["final"] == "ADOPT" and ev[0]["provider"] == "agy" and ev[0]["confidence"] == 0.9
    assert json.loads((Path(r.run_dir) / "result.json").read_text())["arbiter"]["decision"] == "ADOPT"


def test_reject_sends_the_work_back_with_the_reasons_and_the_next_attempt_can_pass(tmp_path):
    repo = make_repo(tmp_path)
    o, ps = build([verdict("REJECT", 0.8, "경계값 테스트가 없습니다", ["경계값 테스트 추가"]), verdict("ADOPT", 0.9)],
                  [order_json(), review_json(), review_json()], [write_file("src/new.py"), write_file("src/new.py", "x = 2\n")])
    r = o.run("add module", repo)
    assert r.status == "PASSED" and r.attempts == 2
    assert "REJECTED" in ps["codex"].calls[1]["prompt"] and "경계값 테스트가 없습니다" in ps["codex"].calls[1]["prompt"] and "경계값 테스트 추가" in ps["codex"].calls[1]["prompt"]
    assert [e["final"] for e in events(r) if e["event"] == "arbiter"] == ["REJECT", "ADOPT"]


def test_need_human_still_delivers_the_result_but_flags_it(tmp_path):
    repo = make_repo(tmp_path)
    o, _ = build([verdict("NEED_HUMAN", 0.7, "변경 범위가 목표에 비해 커 보입니다")], [order_json(), review_json()], [write_file("src/new.py")])
    r = o.run("add module", repo)
    assert r.status == "PASSED" and r.needs_human is True and r.patch_path
    assert any(w.startswith("ARBITER_NEEDS_HUMAN") for w in r.warnings)


def test_low_confidence_and_risky_paths_become_need_human_even_when_it_says_adopt(tmp_path):
    repo = make_repo(tmp_path)
    o, _ = build([verdict("ADOPT", 0.4)], [order_json(), review_json()], [write_file("src/new.py")])
    r = o.run("add module", repo)
    assert r.arbiter["decision"] == "ADOPT" and r.arbiter["final"] == "NEED_HUMAN" and "기준" in r.arbiter["note"] and r.needs_human
    repo2 = make_repo(tmp_path / "second")
    s = make_settings()
    s.test_command = ["python3", "-c", "import pathlib,sys; sys.exit(0 if pathlib.Path('src/risk_limits.py').exists() else 1)"]
    o2, _ = build([verdict("ADOPT", 0.99)], [order_json(), review_json()], [write_file("src/risk_limits.py")], settings=s)
    r2 = o2.run("limits", repo2)
    assert r2.status == "PASSED" and r2.arbiter["final"] == "NEED_HUMAN" and "고위험" in r2.arbiter["note"]


def test_a_silent_or_broken_arbiter_never_adopts_and_never_blocks(tmp_path):
    repo = make_repo(tmp_path)
    o, ps = build(["blah", "still blah", "no json at all"], [order_json(), review_json()], [write_file("src/new.py")])
    r = o.run("add module", repo)
    assert r.status == "PASSED" and r.arbiter["final"] == "NEED_HUMAN" and len(ps["agy"].calls) == 3 and r.needs_human
    for err in (ProviderError("boom"), QuotaExhausted("usage limit reached")):
        o2, ps2 = build([err], [order_json(), review_json()], [write_file("src/new.py")])
        r2 = o2.run("add module", make_repo(tmp_path / str(id(err))))
        assert r2.status == "PASSED" and r2.arbiter["final"] == "NEED_HUMAN" and len(ps2["agy"].calls) == 1 and "답하지 못했습니다" in r2.arbiter["reason"]


def test_the_arbiter_cannot_stop_forever(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=0, stagnation_limit=50)
    s.arbiter_max_rejects = 1
    o, ps = build([verdict("REJECT"), verdict("REJECT")], [order_json(), review_json(), review_json()],
                  [write_file("src/new.py"), write_file("src/new.py", "x = 2\n")], settings=s)
    r = o.run("add module", repo)
    assert r.status == "PASSED" and r.attempts == 2 and r.arbiter["final"] == "NEED_HUMAN" and "이어져" in r.arbiter["note"]


def test_it_is_only_asked_after_every_gate_is_green_and_can_never_rescue_a_failure(tmp_path):
    repo = make_repo(tmp_path)
    # tests fail
    o, ps = build([], [order_json(), review_json()], [write_file("src/other.py")], settings=make_settings(max_attempts=1))
    r = o.run("add module", repo)
    assert r.status == "FAILED" and ps["agy"].calls == []
    # a reviewer fails
    o2, ps2 = build([], [order_json(), review_json("FAIL", ["bug"])], [write_file("src/new.py")], settings=make_settings(max_attempts=1))
    r2 = o2.run("add module", make_repo(tmp_path / "b"))
    assert r2.status == "FAILED" and ps2["agy"].calls == []
    # scope violation
    o3, ps3 = build([], [order_json()], [write_file("README.md", "x\n")], settings=make_settings(max_attempts=1))
    assert o3.run("add module", make_repo(tmp_path / "c")).status == "FAILED" and ps3["agy"].calls == []


def test_without_an_arbiter_nothing_changes(tmp_path):
    repo = make_repo(tmp_path)
    ps = {"claude": ScriptedProvider("claude", [order_json(), review_json()]), "codex": ScriptedProvider("codex", [write_file("src/new.py")])}
    r = Orchestrator(make_settings(), ps).run("add module", repo)
    assert r.status == "PASSED" and r.arbiter is None and r.needs_human is False and not any(e["event"] == "arbiter" for e in events(r))


def test_the_builder_cannot_be_its_own_arbiter(tmp_path):
    s = make_settings()
    s.roles["arbiter"] = "codex"  # codex is the builder
    with pytest.raises(ConfigError):
        s.validate()
    s2 = make_settings()
    s2.roles.update(builder2="claude", arbiter="claude", reviewer="agy")  # claude builds on a hand-over: it must not judge itself
    s2.roles["reviewer"] = "agy"
    agy = ScriptedProvider("agy", [review_json()]); agy.supports_write = False
    s2.max_attempts, s2.stagnation_limit = 0, 50
    ps = {"claude": ScriptedProvider("claude", [order_json(), write_file("src/new.py")]), "agy": agy,
          "codex": ScriptedProvider("codex", [write_file("src/other.py", "x=1\n"), write_file("src/other.py", "x=2\n")])}
    r = Orchestrator(s2, ps).run("add module", make_repo(tmp_path / "x"))
    assert r.status == "PASSED" and r.arbiter["final"] == "NEED_HUMAN" and any(e["event"] == "arbiter_skipped_self" for e in events(r))


def test_settings_from_toml_and_validation(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text('[roles]\narbiter = "ollama"\n[loop]\narbiter_min_confidence = 0.8\narbiter_max_rejects = 3\narbiter_diff_chars = 0\n')
    s = load_settings(f)
    assert s.roles["arbiter"] == "ollama" and (s.arbiter_min_confidence, s.arbiter_max_rejects, s.arbiter_diff_chars) == (0.8, 3, 0)
    f.write_text("[loop]\narbiter_min_confidence = 1.5\n")
    with pytest.raises(ConfigError):
        load_settings(f)
    f.write_text('[roles]\narbiter = ""\n')
    assert "arbiter" not in load_settings(f).roles


# ------------------------------------------------------------------ what a person sees
def test_the_process_and_the_apply_judgement_show_the_arbiter(tmp_path):
    from test_dashboard import Env, sleeper
    sleeper.calls.clear()
    e = Env(tmp_path)
    try:
        o, _ = build([verdict("NEED_HUMAN", 0.7, "크기가 큽니다", ["직접 확인"])], [order_json(), review_json()], [write_file("src/new.py")])
        r = o.run("add module", e.repo)
        d = e.call("GET", f"/api/describe?project={quote(e.project)}&key=run:{r.run_id}")[1]
        step = next(s for s in d["process"]["attempts"][0]["steps"] if s["name"].startswith("결정권자"))
        assert step["status"] == "warn" and "사람 확인 필요" in step["text"] and any("이유: 크기가 큽니다" in l for l in step["lines"]) and any("조건: 직접 확인" in l for l in step["lines"])
        a = d["assessment"]
        assert a["recommendation"] == "caution" and any(c["label"].startswith("결정권자") and c["status"] == "warn" for c in a["checks"])
        run = next(x for x in e.call("GET", "/api/state")[1]["runs"] if x["run_id"] == r.run_id)
        assert run is not None
    finally:
        e.close()


def test_the_scorecard_compares_the_arbiter_with_what_the_human_did(tmp_path):
    from test_dashboard import Env, sleeper
    sleeper.calls.clear()
    e = Env(tmp_path)
    try:
        keys = []
        for i, dec in enumerate(["ADOPT", "ADOPT", "ADOPT", "NEED_HUMAN"]):
            o, _ = build([verdict(dec, 0.9)], [order_json(), review_json()], [write_file("src/new.py")])
            r = o.run(f"goal {i}", e.repo)
            keys.append(f"run:{r.run_id}")
            assert r.status == "PASSED"
            import time; time.sleep(1.05)  # distinct run ids
        post = lambda k, a: e.call("POST", "/api/inbox/action", {"project": e.project, "key": k, "action": a})
        assert post(keys[0], "apply")[0] == 200
        assert post(keys[1], "discard")[0] == 200
        card = next(c for c in e.call("GET", "/api/portfolio")[1]["projects"] if c["path"] == e.project)
        assert card["arbiter"]["adopt"] == {"accepted": 1, "discarded": 1, "pending": 1}
        assert card["arbiter"]["need_human"] == {"accepted": 0, "discarded": 0, "pending": 1}
        ai = card["ais"]["agy"]
        assert ai["decisions"] == {"ADOPT": 3, "REJECT": 0, "NEED_HUMAN": 1} and ai["roles"]["arbiter"] == 4
        tot = e.call("GET", "/api/portfolio")[1]["arbiter"]
        assert tot["adopt"]["accepted"] == 1
    finally:
        e.close()


# ------------------------------------------------------------------ command line and page
def test_cli_and_dashboard_accept_an_arbiter(tmp_path):
    from test_roles import Env as _E  # noqa: F401
    parser = cli.build_parser()
    run = next(a for a in parser._actions if a.dest == "command").choices["run"]
    assert "--arbiter" in {o for a in run._actions for o in a.option_strings}


def test_dashboard_arbiter_role_is_validated(tmp_path):
    from test_dashboard import Env, sleeper
    sleeper.calls.clear()
    e = Env(tmp_path)
    try:
        body = lambda **roles: {"goal": "x", "project": e.project, "roles": roles}
        assert e.call("POST", "/api/run", body(arbiter="ollama"))[0] == 200
        assert sleeper.calls[-1][sleeper.calls[-1].index("--arbiter") + 1] == "ollama"
        e.app.job["popen"].kill(); e.app.job["popen"].wait()
        assert e.call("POST", "/api/run", body(arbiter="codex"))[0] == 400  # codex builds by default
        assert e.call("POST", "/api/run", body(arbiter="nope"))[0] == 400
        sleeper.calls.clear()
        assert e.call("POST", "/api/run", body(arbiter=""))[0] == 200 and "--arbiter" not in sleeper.calls[-1]
    finally:
        e.close()
