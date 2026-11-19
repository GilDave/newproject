import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

from solomon import cli, policy, vision, visual
from solomon.orchestrator import Orchestrator

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file
from test_dashboard import Env, sleeper


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # vision.json and the like never touch the real home


# ---------------------------------------------------------------- the pieces
def test_probe_png_is_a_real_png_with_the_expected_stripes(tmp_path):
    names = vision.make_probe(tmp_path / "p.png")
    raw = (tmp_path / "p.png").read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n" and len(set(names)) == 3
    w, h = struct.unpack(">II", raw[16:24])
    assert (w, h) == (300, 120)
    idat = raw[raw.index(b"IDAT") + 4:raw.index(b"IEND") - 8]
    rows = zlib.decompress(idat)
    px = lambda x: tuple(rows[1 + x * 3: 4 + x * 3])
    assert [px(10), px(150), px(290)] == [vision.COLORS[n] for n in names]


def test_probe_answer_must_name_the_colours_in_order():
    assert vision.check('{"colors": ["red", "green", "blue"]}', ["red", "green", "blue"])
    assert vision.check('```json\n{"colors": ["Red", "GREEN", "blue"]}\n```', ["red", "green", "blue"])
    for bad in ('{"colors": ["green", "red", "blue"]}', '{"colors": []}', "I see stripes", '{"colors": ["red"]}'):
        assert not vision.check(bad, ["red", "green", "blue"])


class Seer:
    """A provider that answers the probe by reading the real picture's pixels (so the whole path is exercised)."""
    image_capable = True
    name = "seer"
    def locate(self): return "/bin/x"
    def run(self, prompt, cwd, write, timeout, no_output_timeout=None, images=None):
        raw = Path(images[0]).read_bytes()
        rows = zlib.decompress(raw[raw.index(b"IDAT") + 4:raw.index(b"IEND") - 8])
        inv = {v: k for k, v in vision.COLORS.items()}
        from solomon.providers import ProviderResult
        got = [inv[tuple(rows[1 + x * 3: 4 + x * 3])] for x in (10, 150, 290)]
        return ProviderResult(json.dumps({"colors": got}), 0, 0.1, "seer")


class Blind(Seer):
    name = "blind"
    def run(self, prompt, cwd, write, timeout, no_output_timeout=None, images=None):
        from solomon.providers import ProviderResult
        return ProviderResult('{"colors": ["red", "red", "red"]}', 0, 0.1, "blind")


def test_probe_trusts_only_the_ai_that_really_sees_and_remembers_it():
    out = vision.probe({"seer": Seer(), "blind": Blind(), "text_only": object()}, ["seer", "blind", "text_only"])
    assert out["seer"]["ok"] and not out["blind"]["ok"] and "맞히지 못했습니다" in out["blind"]["error"] and not out["text_only"]["ok"]
    assert vision.verified("seer") and not vision.verified("blind") and not vision.verified("nobody")


def script(tmp_path, code, name="cap.py"):
    f = tmp_path / name
    f.write_text(code)
    return {"command": [sys.executable, str(f), "{out}"], "timeout": 20, "max_shots": 2}


def test_capture_exit_codes_mean_ok_broken_or_could_not_run(tmp_path):
    good = script(tmp_path, "import sys, pathlib\nfor i in range(4): pathlib.Path(sys.argv[1], f'{i}.png').write_bytes(b'x')\n", "good.py")
    r = visual.run_capture(good, tmp_path, tmp_path / "o1")
    assert r.ok and len(r.shots) == 2  # capped by max_shots
    r = visual.run_capture(script(tmp_path, "print('JS errors: boom'); raise SystemExit(3)", "b.py"), tmp_path, tmp_path / "o2")
    assert not r.ok and "boom" in r.broken and not r.infra
    r = visual.run_capture(script(tmp_path, "raise SystemExit(2)", "i.py"), tmp_path, tmp_path / "o3")
    assert not r.ok and r.infra and not r.broken
    r = visual.run_capture(script(tmp_path, "pass", "n.py"), tmp_path, tmp_path / "o4")
    assert r.infra and "이미지가 없습니다" in r.infra
    r = visual.run_capture({"command": ["/definitely/not/here"], "timeout": 5}, tmp_path, tmp_path / "o5")
    assert r.infra
    r = visual.run_capture(script(tmp_path, "import time; time.sleep(30)", "t.py") | {"timeout": 1}, tmp_path, tmp_path / "o6")
    assert r.infra and "안에 끝나지" in r.infra


def test_policy_visual_is_validated_and_roundtrips(tmp_path):
    saved = policy.save(tmp_path, ".solomon", [], False, {"command": ["{python}", "cap.py", "{out}"], "timeout": 5000, "max_shots": 99})
    assert saved["visual"] == {"command": ["{python}", "cap.py", "{out}"], "timeout": 900.0, "max_shots": 12}
    assert policy.load(tmp_path, ".solomon")["visual"]["command"][1] == "cap.py"
    assert policy.save(tmp_path, ".solomon", [], False, {"command": []})["visual"] is None
    assert policy.save(tmp_path, ".solomon", [], False, "nonsense")["visual"] is None


# ---------------------------------------------------------------- inside the pipeline
class Eyes(ScriptedProvider):
    image_capable = True
    vision_verified = True


def project_with_capture(tmp_path, code):
    repo = make_repo(tmp_path)
    cap = Path(repo) / "cap.py"
    cap.write_text(code)
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "cap"], cwd=repo, check=True)
    policy.save(Path(repo), ".solomon", [], False, {"command": [sys.executable, "cap.py", "{out}"], "timeout": 20})
    return repo


GOOD = "import sys, pathlib\npathlib.Path(sys.argv[1], 'a.png').write_bytes(b'png')\n"


def events(r):
    return [json.loads(l) for l in (Path(r.run_dir) / "events.jsonl").read_text().splitlines()]


def test_a_reviewer_who_can_see_gets_the_pictures_and_the_result_records_who_looked(tmp_path):
    repo = project_with_capture(tmp_path, GOOD)
    eyes = Eyes("claude", [order_json(), review_json()])
    o = Orchestrator(make_settings(), {"claude": eyes, "codex": ScriptedProvider("codex", [write_file("src/new.py")])})
    r = o.run("add module", repo)
    assert r.status == "PASSED" and not r.needs_human
    call = eyes.calls[-1]
    assert call["images"] and call["images"][0].endswith("a.png") and "LOOK at each one" in call["prompt"]
    assert r.visual["viewed_by"] == ["claude"] and r.visual["shots"] == ["shots/attempt-1/a.png"]
    assert any(e["event"] == "visual" and e["ok"] for e in events(r))
    assert (Path(r.run_dir) / "shots" / "attempt-1" / "a.png").is_file()


def test_nobody_who_can_see_means_a_person_must_look(tmp_path):
    repo = project_with_capture(tmp_path, GOOD)
    blind = ScriptedProvider("claude", [order_json(), review_json()])
    o = Orchestrator(make_settings(), {"claude": blind, "codex": ScriptedProvider("codex", [write_file("src/new.py")])})
    r = o.run("add module", repo)
    assert r.status == "PASSED" and r.needs_human and any(w.startswith("VISUAL_UNVIEWED") for w in r.warnings)
    assert blind.calls[-1]["images"] is None and "cannot view images" in blind.calls[-1]["prompt"]


def test_a_page_that_does_not_render_is_sent_back_without_asking_any_reviewer(tmp_path):
    repo = project_with_capture(tmp_path, "print('JS errors: x is not defined'); raise SystemExit(3)")
    claude = Eyes("claude", [order_json()])  # a review call would exhaust the script and fail the test
    o = Orchestrator(make_settings(), {"claude": claude, "codex": ScriptedProvider("codex", [write_file("src/new.py")] * 3)})
    s = o.s
    s.max_attempts = 2
    r = o.run("add module", repo)
    assert r.status == "FAILED" and len(claude.calls) == 1  # only the planner
    ev = events(r)
    assert any(e["event"] == "visual" and e["broken"] for e in ev)
    assert any(e["event"] == "review" and e["verdict"] == "FAIL" and "did not render" in json.dumps(e) for e in ev)


def test_a_capture_that_cannot_run_is_reported_not_blamed_on_the_code(tmp_path):
    repo = project_with_capture(tmp_path, "raise SystemExit(2)")
    o = Orchestrator(make_settings(), {"claude": Eyes("claude", [order_json(), review_json()]), "codex": ScriptedProvider("codex", [write_file("src/new.py")])})
    r = o.run("add module", repo)
    assert r.status == "PASSED" and r.needs_human and any(w.startswith("VISUAL_SKIPPED") for w in r.warnings)


def test_no_capture_when_the_tests_failed_or_the_gate_is_off(tmp_path):
    repo = make_repo(tmp_path)  # no policy at all
    o = Orchestrator(make_settings(), {"claude": ScriptedProvider("claude", [order_json(), review_json()]), "codex": ScriptedProvider("codex", [write_file("src/new.py")])})
    r = o.run("add module", repo)
    assert r.visual is None and not any(e["event"] == "visual" for e in events(r))


# ---------------------------------------------------------------- the dashboard side
@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    e = Env(tmp_path)
    yield e
    e.close()


def test_screenshots_are_served_only_by_validated_name_and_need_the_token(env):
    shots = Path(env.project) / ".solomon" / "runs" / "r1" / "shots" / "attempt-1"
    shots.mkdir(parents=True)
    (shots / "a.png").write_bytes(b"\x89PNG-fake")
    (Path(env.project) / ".solomon" / "secret.png").write_bytes(b"nope")
    from urllib.parse import quote
    q = lambda **k: "/api/shot?project=" + quote(env.project) + "".join(f"&{a}={quote(str(b))}" for a, b in k.items())
    st, body, resp = env.call("GET", q(run="r1", attempt="1", name="a.png"))
    assert st == 200 and body == b"\x89PNG-fake" and resp.getheader("Content-Type") == "image/png"
    assert env.call("GET", q(run="r1", attempt="1", name="a.png"), token=None)[0] == 401
    for bad in (dict(run="r1", attempt="1", name="../../secret.png"), dict(run="..", attempt="1", name="a.png"), dict(run="r1", attempt="x", name="a.png"),
                dict(run="r1", attempt="1", name="a.txt"), dict(run="r1/..", attempt="1", name="a.png")):
        assert env.call("GET", q(**bad))[0] in (400, 404), bad
    assert env.call("GET", "/api/shot?project=/etc&run=r1&attempt=1&name=a.png")[0] == 400


def test_policy_api_sets_and_clears_the_capture_command_and_suggests_one(env):
    (Path(env.project) / "scripts").mkdir()
    (Path(env.project) / "scripts" / "visual_capture.py").write_text("")
    st, body, _ = env.call("GET", "/api/projects/policy?path=" + env.project)
    assert st == 200 and body["suggest_visual"] == ["{python}", "scripts/visual_capture.py", "{out}"] and body["policy"]["visual"] is None
    st, body, _ = env.call("POST", "/api/projects/policy", {"path": env.project, "visual": {"command": "{python} scripts/visual_capture.py {out}"}})
    assert st == 200 and body["policy"]["visual"]["command"][1] == "scripts/visual_capture.py"
    assert env.call("POST", "/api/projects/policy", {"path": env.project, "visual": "   "})[0] in (200, 400)
    st, body, _ = env.call("POST", "/api/projects/policy", {"path": env.project, "visual": None})
    assert st == 200 and body["policy"]["visual"] is None


def test_page_shows_screenshots_the_gate_pill_and_the_setup(env):
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ("/api/shot?t=", 'visual: a.visual', "화면 캡처 관문", "t-vision", "/api/projects/policy"):
        assert needle in html, needle
    assert "img-src 'self' data:" in (Path(__file__).parent.parent / "solomon" / "dashboard.py").read_text(encoding="utf-8")


def test_cli_has_the_probe_command():
    parser = cli.build_parser()
    assert "vision-probe" in next(a for a in parser._actions if a.dest == "command").choices
