import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from solomon import cli
from solomon.providers import PROVIDER_CLASSES, ProviderError, ProviderTimeout, ProviderUnavailable, build_providers, default_ais
from solomon.providers import ollama as O
from solomon.settings import ConfigError, Settings, load_settings


class FakeOllama:
    """A tiny local stand-in for the Ollama server: /api/tags and /api/chat."""

    def __init__(self, models=("qwen3:8b",), reply=None, tin=120, tout=30, status=200, delay=0.0):
        self.models, self.reply, self.tin, self.tout, self.status, self.delay = list(models), reply, tin, tout, status, delay
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"models": [{"name": m} for m in outer.models]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(req)
                if outer.delay:
                    import time; time.sleep(outer.delay)
                if outer.status != 200:
                    self.send_response(outer.status); self.end_headers(); self.wfile.write(b'{"error":"model not found"}'); return
                text = outer.reply if outer.reply is not None else json.dumps({"verdict": "PASS", "issues": [], "summary": "ok"})
                body = json.dumps({"message": {"role": "assistant", "content": text}, "prompt_eval_count": outer.tin, "eval_count": outer.tout}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown(); self.server.server_close()


@pytest.fixture
def fake():
    O._cache.clear()
    f = FakeOllama()
    yield f
    f.close()
    O._cache.clear()


def prov(f, **kw):
    return O.OllamaProvider(url=f.url, **kw)


def test_it_is_a_registered_read_only_helper_that_is_not_asked_by_default():
    assert "ollama" in PROVIDER_CLASSES and PROVIDER_CLASSES["ollama"].supports_write is False
    assert PROVIDER_CLASSES["ollama"].default_participant is False and PROVIDER_CLASSES["ollama"].local is True


def test_only_this_computer_is_allowed():
    for bad in ("http://192.168.0.5:11434", "https://127.0.0.1:11434", "http://example.com:11434", "http://127.0.0.1.evil.io:11434"):
        with pytest.raises(ValueError):
            O.check_url(bad)
        s = Settings(ollama_url=bad)
        with pytest.raises(ConfigError):
            s.validate()
    assert O.check_url("http://localhost:11434") == "http://localhost:11434"


def test_available_only_when_the_server_runs_and_the_model_is_installed(fake):
    assert prov(fake).locate() == fake.url
    O._cache.clear()
    assert prov(fake, model="other:1b").locate() is None  # server up, model missing
    O._cache.clear()
    assert prov(fake, model="qwen3").locate() is None  # a bare name means ':latest', which is not what is installed
    dead = O.OllamaProvider(url="http://127.0.0.1:1")
    assert dead.locate() is None and dead.status()["running"] is False


def test_a_call_sends_the_safe_options_and_returns_exact_tokens(fake):
    r = prov(fake).run("Reply with ONLY one JSON object: {\"verdict\": \"PASS\"}. Review this.", "/tmp", False, 30)
    req = fake.requests[0]
    assert req["model"] == "qwen3:8b" and req["stream"] is False and req["think"] is False and req["format"] == "json"
    assert req["options"] == {"temperature": 0, "num_ctx": 16384} and req["keep_alive"] == "30m"
    assert json.loads(r.text)["verdict"] == "PASS" and r.tokens == {"in": 120, "out": 30, "total": 150} and r.provider == "ollama"
    prov(fake).run("Give plain advice in Korean.", "/tmp", False, 30)
    assert "format" not in fake.requests[1]  # plain-text roles are not forced into JSON


def test_thinking_blocks_are_removed(fake):
    fake.reply = "<think>길게 생각하는 중…</think>\n{\"verdict\": \"FAIL\", \"issues\": [\"x\"], \"summary\": \"s\"}"
    assert json.loads(prov(fake).run("x", "/tmp", False, 30).text)["verdict"] == "FAIL"


def test_it_refuses_to_write(fake):
    with pytest.raises(ProviderError, match="읽기 전용"):
        prov(fake).run("x", "/tmp", True, 30)


def test_a_prompt_that_does_not_fit_the_window_is_an_error_not_a_silent_cut(fake):
    p = prov(fake, num_ctx=2048)
    with pytest.raises(ProviderError, match="num_ctx"):
        p.run("가나다라 " * 4000, "/tmp", False, 30)  # clearly too long: refused before sending
    assert fake.requests == []
    fake.tin = 2048  # the server says it filled the whole window: it was cut
    with pytest.raises(ProviderError, match="잘렸을 수"):
        p.run("짧은 질문", "/tmp", False, 30)


def test_errors_say_what_to_do(fake):
    fake.status = 404
    with pytest.raises(ProviderError, match="ollama pull qwen3:8b"):
        prov(fake).run("x", "/tmp", False, 30)
    fake.status = 200
    fake.reply = "   "
    with pytest.raises(ProviderError, match="빈 답"):
        prov(fake).run("x", "/tmp", False, 30)
    with pytest.raises(ProviderUnavailable, match="실행 중인지"):
        O.OllamaProvider(url="http://127.0.0.1:1").run("x", "/tmp", False, 5)
    fake.reply, fake.delay = None, 1.5
    with pytest.raises(ProviderTimeout):
        prov(fake).run("x", "/tmp", False, 0.3)


def test_settings_and_registry_build_it_from_the_toml(tmp_path, fake):
    f = tmp_path / "s.toml"
    f.write_text(f'[ollama]\nurl = "{fake.url}"\nmodel = "llama3.1:8b"\nnum_ctx = 8192\nkeep_alive = "5m"\n')
    s = load_settings(f)
    p = build_providers(s)["ollama"]
    assert (p.url, p.model, p.num_ctx, p.keep_alive) == (fake.url, "llama3.1:8b", 8192, "5m")
    f.write_text('[ollama]\nnum_ctx = 100\n')
    with pytest.raises(ConfigError):
        load_settings(f)


def test_default_selection_leaves_the_local_model_out_unless_picked(fake):
    class Cloud:
        supports_write = True
        def locate(self): return "/bin/x"
    ps = {"claude": Cloud(), "codex": Cloud(), "ollama": prov(fake)}
    assert default_ais(ps) == ["claude", "codex"]


def test_it_works_as_an_analysis_participant_and_reviewer(tmp_path, fake):
    from solomon.analysis import run_analysis
    from helpers import make_repo
    repo = make_repo(tmp_path)
    fake.reply = json.dumps({"summary": "s", "architecture": [], "questions": [], "findings": []})
    r = run_analysis(Settings(), repo, providers={"ollama": prov(fake)}, ais=["ollama"])
    assert r.status == "DONE" and r.ais["ollama"]["ok"]
    assert fake.requests and fake.requests[0]["format"] == "json"


def test_doctor_reports_the_local_model(fake, capsys, tmp_path):
    f = tmp_path / "s.toml"
    f.write_text(f'[ollama]\nurl = "{fake.url}"\n')
    cli.main(["doctor", "--config", str(f)])
    out = capsys.readouterr().out
    assert "[ok] ollama" in out and "qwen3:8b" in out and "비용 없음" in out
    O._cache.clear()
    f.write_text(f'[ollama]\nurl = "{fake.url}"\nmodel = "nope:1b"\n')
    cli.main(["doctor", "--config", str(f)])
    out = capsys.readouterr().out
    assert "모델 'nope:1b'이 없습니다" in out and "설치된 모델: qwen3:8b" in out


def test_dashboard_lists_it_as_a_local_non_default_ai(tmp_path, fake, monkeypatch):
    from test_dashboard import Env, sleeper
    sleeper.calls.clear()
    e = Env(tmp_path, settings=Settings(ollama_url=fake.url))
    try:
        o = next(p for p in e.state()[1]["providers"] if p["name"] == "ollama")
        assert o == {"name": "ollama", "installed": True, "supports_write": False, "default": False, "local": True, "model": "qwen3:8b"}
        # an analysis started without choosing AIs does not include it; choosing it explicitly works
        monkeypatch.setattr(e.app, "providers_info", lambda: [{"name": "claude", "installed": True, "supports_write": True, "default": True},
                                                              {"name": "ollama", "installed": True, "supports_write": False, "default": False}])
        e.call("POST", "/api/analyze", {"project": e.project})
        assert sleeper.calls[-1][sleeper.calls[-1].index("--ais") + 1] == "claude"
        e.app.job["popen"].kill(); e.app.job["popen"].wait()
        e.call("POST", "/api/analyze", {"project": e.project, "ais": ["claude", "ollama"]})
        assert sleeper.calls[-1][sleeper.calls[-1].index("--ais") + 1] == "claude,ollama"
    finally:
        e.close()


# ------------------------------------------------------------------ `solomon ollama` (the connection test)
def cfg(tmp_path, fake, model="qwen3:8b"):
    f = tmp_path / "s.toml"
    f.write_text(f'[ollama]\nurl = "{fake.url}"\nmodel = "{model}"\n')
    return str(f)


def test_connection_test_passes_with_valid_decisions(fake, tmp_path, capsys):
    fake.reply = json.dumps({"decision": "ADOPT", "confidence": 0.8, "reason": "스타일 문제일 뿐"})
    assert cli.main(["ollama", "--config", cfg(tmp_path, fake), "--rounds", "2"]) == 0
    out = capsys.readouterr().out
    assert "유효한 JSON 2/2" in out and "같은 질문에 같은 결정 예" in out and fake.requests[0]["options"]["temperature"] == 0


def test_connection_test_flags_bad_format_and_missing_model_and_dead_server(fake, tmp_path, capsys):
    fake.reply = json.dumps({"verdict": "PASS"})  # valid JSON but not a decision
    assert cli.main(["ollama", "--config", cfg(tmp_path, fake), "--rounds", "1"]) == 1
    assert "형식 이상" in capsys.readouterr().out
    O._cache.clear()
    assert cli.main(["ollama", "--config", cfg(tmp_path, fake, "nope:1b")]) == 2
    assert "ollama pull nope:1b" in capsys.readouterr().out
    O._cache.clear()
    f = tmp_path / "dead.toml"
    f.write_text('[ollama]\nurl = "http://127.0.0.1:1"\n')
    assert cli.main(["ollama", "--config", str(f)]) == 2
    assert "Ollama 앱을 실행" in capsys.readouterr().out


def test_the_dashboard_tool_button_runs_the_connection_test(tmp_path):
    from test_dashboard import Env, sleeper
    sleeper.calls.clear()
    e = Env(tmp_path)
    try:
        assert e.call("POST", "/api/tools/run", {"tool": "ollama"})[0] == 200
        assert sleeper.calls[-1][3:] == ["ollama"] or "ollama" in sleeper.calls[-1]
    finally:
        e.close()


def test_analysis_skips_the_local_model_when_the_input_cannot_fit_instead_of_failing(tmp_path, fake):
    from solomon.analysis import run_analysis
    from helpers import make_repo, ScriptedProvider
    repo = make_repo(tmp_path)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "big.py").write_text("x = 1  # " + "filler " * 4000 + "\n")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "big"], cwd=repo, check=True)
    small = prov(fake)
    small.num_ctx = 2048
    ok = ScriptedProvider("claude", [json.dumps({"summary": "s", "architecture": [], "questions": [], "findings": []})])
    r = run_analysis(Settings(), repo, providers={"claude": ok, "ollama": small}, ais=["claude", "ollama"])
    assert r.ais["ollama"]["state"] == "skipped" and not r.ais["ollama"]["ok"] and "건너뜁니다" in r.ais["ollama"]["error"]
    assert r.ais["claude"]["ok"] and r.status == "DONE" and "1/1" in r.summary and not fake.requests
