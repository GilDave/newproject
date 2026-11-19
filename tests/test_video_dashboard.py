import json
from pathlib import Path
from urllib.parse import quote

import pytest

from solomon import cli
from solomon import video as V
from solomon.dashboard import MAX_GOAL
from solomon.settings import Settings

from helpers import ScriptedProvider
from test_dashboard import Env, sleeper
from test_video import SEGS, URL, VID, fake_http, good, OEMBED

TEXT = "\n".join(f"[{int(t) // 60}:{int(t) % 60:02d}] {x}" for t, x in SEGS)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLOMON_HOME", str(tmp_path / "home"))
    sleeper.calls.clear()
    e = Env(tmp_path)
    yield e
    e.close()


def seeded(env):
    ps = {"claude": ScriptedProvider("claude", [good()]), "codex": ScriptedProvider("codex", [good()])}
    return V.run_video(Settings(), env.repo, URL, "손절", ["claude", "codex"], transcript_text=TEXT, providers=ps, http=fake_http({"oembed": OEMBED}))


def test_video_job_arguments_and_pasted_transcript_goes_through_a_file(env, monkeypatch):
    monkeypatch.setattr(env.app, "providers_info", lambda: [{"name": n, "installed": True, "supports_write": n != "antigravity"} for n in ("claude", "codex", "antigravity")])
    st, body, _ = env.call("POST", "/api/video", {"project": env.project, "url": URL, "focus": "손절 위주", "ais": ["claude", "antigravity"],
                                                  "direct": ["antigravity"], "context": True, "transcript": TEXT})
    assert st == 200 and body["job"]["kind"] == "video"
    a = sleeper.calls[-1]
    assert a[3:5] == ["video", URL] and a[a.index("--ais") + 1] == "claude,antigravity" and a[a.index("--direct") + 1] == "antigravity"
    assert "--context" in a and a[a.index("--focus") + 1] == "손절 위주"
    f = Path(a[a.index("--transcript-file") + 1])
    assert f.name.startswith("video_input-") and f.read_text(encoding="utf-8") == TEXT and ".solomon" in str(f)
    assert TEXT not in " ".join(a)  # long text never travels through the command line


@pytest.mark.parametrize("opts", [{"url": ""}, {"url": "https://evil.example/watch?v=" + VID}, {"url": "https://www.youtube.com/playlist?list=PL1"},
                                  {"ais": ["nope"]}, {"ais": "claude"}, {"direct": ["codex"], "ais": ["claude"]}, {"transcript": "x" * 400_001},
                                  {"focus": "x" * (MAX_GOAL + 1)}])
def test_video_requests_are_validated_before_anything_runs(env, monkeypatch, opts):
    monkeypatch.setattr(env.app, "providers_info", lambda: [{"name": n, "installed": True, "supports_write": True} for n in ("claude", "codex")])
    sleeper.calls.clear()
    st, body, _ = env.call("POST", "/api/video", {"project": env.project, "url": URL, **opts})
    assert st == 400 and not sleeper.calls, body


def test_a_not_installed_ai_is_refused(env, monkeypatch):
    monkeypatch.setattr(env.app, "providers_info", lambda: [{"name": "claude", "installed": False, "supports_write": True}])
    assert env.call("POST", "/api/video", {"project": env.project, "url": URL, "ais": ["claude"]})[0] == 400


def test_state_carries_the_finished_video_and_the_list(env):
    r = seeded(env)
    s = env.state()[1]
    assert s["videos"][0]["analysis_id"] == r["analysis_id"] and s["videos"][0]["focus"] == "자동매매 손절 규칙"
    assert s["video"]["kind"] == "video" and s["video"]["finished"] and s["video"]["clusters"] and s["video"]["video"]["id"] == VID
    assert env.call("GET", f"/api/state?video={r['analysis_id']}")[1]["video"]["analysis_id"] == r["analysis_id"]
    assert env.call("GET", "/api/state?video=../x")[1]["video"]["analysis_id"] == r["analysis_id"]  # a bad id falls back to the latest


def test_probe_job_and_remembered_capability(env, monkeypatch):
    monkeypatch.setattr(env.app, "providers_info", lambda: [{"name": "antigravity", "installed": True, "supports_write": False}])
    st, body, _ = env.call("POST", "/api/video-probe", {"project": env.project, "url": URL, "ais": ["antigravity"]})
    assert st == 200 and body["job"]["kind"] == "videoprobe" and "--probe" in sleeper.calls[-1]
    assert env.call("POST", "/api/video-probe", {"project": env.project, "url": "https://evil.example/x"})[0] == 400
    V.save_capability({"video_id": VID, "at": "2026-01-01 00:00:00", "ais": {"antigravity": {"status": "confirmed", "title": "t"}}})
    assert env.state()[1]["video_capability"]["antigravity"]["status"] == "confirmed"


def test_five_w_one_h_history_and_portfolio_know_about_videos(env):
    r = seeded(env)
    d = env.call("GET", f"/api/describe?project={quote(env.project)}&key=video:{r['analysis_id']}")[1]
    assert d["kind"] == "video" and list(d["sections"]) == ["who", "what", "when", "where", "why", "how"]
    flat = json.dumps(d, ensure_ascii=False)
    assert "자동매매 손절 규칙" in flat and "claude" in flat and "근거 확인됨" in flat and "손절" in flat
    inbox = env.call("GET", "/api/inbox?project=" + quote(env.project))[1]
    assert any(h["key"] == f"video:{r['analysis_id']}" and h["kind"] == "video" for h in inbox["history"])
    card = next(c for c in env.call("GET", "/api/portfolio")[1]["projects"] if c["path"] == env.project)
    assert card["counts"]["videos"] == 1 and card["ais"]["claude"]["roles"]["video"] == 1
    assert env.call("GET", f"/api/describe?project={quote(env.project)}&key=video:../x")[0] == 400


# ------------------------------------------------------------------ CLI
def test_cli_video_reads_and_removes_the_dashboards_temporary_transcript(tmp_path, monkeypatch, capsys):
    from helpers import make_repo
    repo = make_repo(tmp_path)
    ps = {"claude": ScriptedProvider("claude", [good()])}
    monkeypatch.setattr("solomon.providers.build_providers", lambda s: ps)
    f = repo / "video_input-abc123.txt"
    f.write_text(TEXT, encoding="utf-8")
    monkeypatch.setattr(V, "fetch_oembed", lambda vid, http=None: {"title": "자동매매 손절 규칙", "author": "채널"})
    rc = cli.main(["video", URL, "--project", str(repo), "--ais", "claude", "--transcript-file", str(f), "--focus", "손절"])
    out = capsys.readouterr().out
    assert rc == 0 and "status:   DONE" in out and "자동매매 손절 규칙" in out and "verified" in out and "report:" in out
    assert not f.exists()


def test_cli_video_errors_are_messages_not_tracebacks(capsys, tmp_path):
    assert cli.main(["video", "https://example.com/x", "--project", str(tmp_path)]) == 2
    assert "유튜브 주소" in capsys.readouterr().err


def test_cli_probe_prints_and_saves(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SOLOMON_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(V, "probe_all", lambda s, url, ais=None, providers=None, http=None: {
        "video_id": VID, "truth_title": "진짜 제목", "at": "2026-01-01 00:00:00",
        "ais": {"claude": {"status": "unavailable"}, "antigravity": {"status": "confirmed", "title": "진짜 제목"}, "codex": {"status": "guessed", "title": "엉뚱한 제목"}}})
    assert cli.main(["video", URL, "--probe"]) == 0
    out = capsys.readouterr().out
    assert "직접 시청 확인됨" in out and "추측" in out and "직접 볼 수 없음" in out and "guessed는 영상을 보지 않고" in out
    assert V.load_capability()["antigravity"]["status"] == "confirmed"
