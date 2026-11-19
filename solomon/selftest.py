"""`solomon selftest`: the whole machine, end to end, with scripted fake AIs in a throw-away folder.

It uses no subscription and no tokens. It proves that this installation can: run the cross-verified pipeline,
reject out-of-scope edits, send review failures back, keep the original untouched, snapshot uncommitted edits,
apply / commit / undo / discard / restore results, and create / trash / restore project folders.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from . import folders, gitops, manage, portfolio, projects, video
from .orchestrator import Orchestrator
from .providers.base import Provider, ProviderResult
from .settings import Settings


class _Fake(Provider):
    def __init__(self, name: str, steps: list):
        self.name, self.steps = name, list(steps)

    def run(self, prompt, cwd, write, timeout, no_output_timeout=None):
        step = self.steps.pop(0)
        out = step(prompt, cwd) if callable(step) else step
        return ProviderResult(text=out, exit_code=0, duration_sec=0.0, provider=self.name)


def _order() -> str:
    return json.dumps({"allowed_paths": ["src/"], "protected_paths": [], "acceptance_criteria": ["src/new.py exists"],
                       "allowed_deletions": [], "max_files_changed": 5, "max_diff_lines": 200, "context": "selftest"})


def _review(verdict: str = "PASS", issues=None) -> str:
    return json.dumps({"verdict": verdict, "issues": issues or [], "summary": "selftest"})


def _write(rel: str, content: str = "x = 1\n"):
    def step(prompt, cwd):
        t = Path(cwd) / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(content, encoding="utf-8")
        return "done"
    return step


def _repo(base: Path, name: str) -> Path:
    repo = base / name
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "selftest"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        gitops.git(args, repo)
    return repo


def _settings() -> Settings:
    s = Settings()
    s.test_command = ["python3", "-c", "import pathlib,sys; sys.exit(0 if pathlib.Path('src/new.py').exists() else 1)"]
    s.baseline_check = False
    s.max_attempts = 3
    return s


def _orch(claude: list, codex: list, s: Settings | None = None) -> Orchestrator:
    return Orchestrator(s or _settings(), {"claude": _Fake("claude", claude), "codex": _Fake("codex", codex)})


def run_selftest(out: Callable[[str], None] = print) -> int:
    results: list[tuple[str, bool, str]] = []

    def check(title: str, fn: Callable[[], None]) -> None:
        t0 = time.time()
        try:
            fn()
            results.append((title, True, ""))
            out(f"  ✔ {title} ({time.time() - t0:.1f}s)")
        except Exception as exc:  # report every failing step, not just the first
            results.append((title, False, f"{type(exc).__name__}: {exc}"))
            out(f"  ✘ {title}\n      {type(exc).__name__}: {exc}")

    with tempfile.TemporaryDirectory(prefix="solomon-selftest-") as tmp:
        base = Path(tmp)
        out("Solomon 자체 점검 (가짜 AI 사용 · 구독/토큰을 쓰지 않습니다)")
        state: dict = {}

        def pipeline():
            repo = _repo(base, "p1")
            r = _orch([_order(), _review()], [_write("src/new.py")]).run("add module", repo)
            assert r.status == "PASSED" and r.attempts == 1, r.summary
            assert not (repo / "src" / "new.py").exists(), "원본이 바뀌었습니다"
            assert "src/new.py" in Path(r.patch_path).read_text()
            state["repo"], state["run"] = repo, r

        def scope_and_retry():
            repo = _repo(base, "p2")
            r = _orch([_order(), _review("FAIL", ["add a docstring"]), _review()],
                      [_write("README.md", "hacked\n"), _write("src/new.py"), _write("src/new.py", '"""doc"""\nx = 1\n')]).run("add module", repo)
            assert r.status == "PASSED" and r.attempts == 3, f"{r.status} {r.attempts}"
            assert "hacked" not in Path(r.patch_path).read_text(), "범위 밖 수정이 결과에 섞였습니다"

        def snapshot():
            repo = _repo(base, "p3")
            (repo / "src" / "app.py").write_text("print('uncommitted edit')\n")
            (repo / ".env").write_text("SECRET=1\n")
            seen = {}

            def spy(prompt, cwd):
                seen["edit"] = "uncommitted edit" in (Path(cwd) / "src" / "app.py").read_text()
                seen["env"] = (Path(cwd) / ".env").exists()
                return _write("src/new.py")(prompt, cwd)
            r = _orch([_order(), _review()], [spy]).run("add module", repo)
            assert r.status == "PASSED" and seen == {"edit": True, "env": False}, seen

        def apply_commit_undo():
            repo, run = state["repo"], state["run"]
            key = f"run:{run.run_id}"
            sd = ".solomon"
            manage.perform(repo, sd, key, "apply")
            assert (repo / "src" / "new.py").exists()
            manage.perform(repo, sd, key, "revert")
            assert not (repo / "src" / "new.py").exists()
            manage.perform(repo, sd, key, "apply")
            manage.perform(repo, sd, key, "commit", "selftest commit")
            assert "selftest commit" in gitops.git(["log", "-1", "--format=%s"], repo).stdout
            manage.perform(repo, sd, key, "revert_commit")
            assert not (repo / "src" / "new.py").exists()
            assert manage.list_inbox(repo, sd)["entries"][0]["state"] == "pending"

        def discard_restore():
            repo, run = state["repo"], state["run"]
            key = f"run:{run.run_id}"
            manage.perform(repo, ".solomon", key, "discard")
            manage.perform(repo, ".solomon", key, "restore")
            assert manage.list_inbox(repo, ".solomon")["entries"][0]["state"] == "pending"

        def five_w_one_h():
            d = manage.describe(state["repo"], ".solomon", f"run:{state['run'].run_id}")
            assert list(d["sections"]) == ["who", "what", "when", "where", "why", "how"]
            assert d["assessment"]["recommendation"] == "recommend", d["assessment"]

        def three_ais_per_stage():
            repo = _repo(base, "p4")
            st = _settings()
            st.roles.update(planner2="agy", builder2="claude", builder3="agy", reviewer2="agy", reviewer3="claude")
            st.roles["reviewer"] = "claude"
            agy = _Fake("agy", [json.dumps({"verdict": "CHANGE", "concerns": ["빈 입력"], "extra_acceptance_criteria": ["빈 입력 처리"]}),
                                "조언: 빈 입력을 먼저 처리하세요", _review(), _review()])
            agy.supports_write = False
            # attempt 1 (codex) is rejected by claude; attempt 2 (codex) too; attempt 3 is handed to claude, so agy must review it
            claude = _Fake("claude", [_order(), _review("FAIL", ["a"]), _review("FAIL", ["b"]), _write("src/new.py")])
            codex = _Fake("codex", [_write("src/other.py", "x = 1\n"), _write("src/other.py", "x = 2\n")])
            st.max_attempts = 0
            st.stagnation_limit = 50
            r = Orchestrator(st, {"claude": claude, "codex": codex, "agy": agy}).run("add module", repo)
            assert r.status == "PASSED", r.summary
            ev = [json.loads(l) for l in (Path(r.run_dir) / "events.jsonl").read_text().splitlines()]
            order = next(e["order"] for e in ev if e["event"] == "order")
            assert "빈 입력 처리" in order["acceptance_criteria"] and order["allowed_paths"] == ["src/"]
            assert [e["provider"] for e in ev if e["event"] == "builder_pick"] == ["codex", "codex", "claude"]
            assert any(e["event"] == "review_skipped_self" for e in ev)
            card = portfolio.card(repo, ".solomon", is_git=True, managed=False, test_command="", busy=False)
            assert set(card["ais"]) == {"claude", "codex", "agy"} and card["counts"]["passed"] == 1 and card["tokens"] > 0

        def video_analysis():
            repo = _repo(base, "p5")
            text = ("[0:01] 안녕하세요 오늘은 자동매매 손절 규칙을 이야기해 보겠습니다\n[0:05] 손절은 진입가 대비 2퍼센트로 고정하는 게 가장 좋습니다\n"
                    "[1:30] 수수료와 슬리피지를 반드시 계산하세요")
            real = {"summary": "손절 규칙 영상", "outline": [], "project_ideas": [], "caveats": [], "claims_to_check": [], "action_items": [],
                    "key_points": [{"point": "손절 2%", "time": "0:05", "quote": "손절은 진입가 대비 2퍼센트로 고정하는 게 가장 좋습니다"}]}
            fake = dict(real, key_points=[{"point": "지어낸 말", "time": "0:30", "quote": "이 문장은 영상에 전혀 나오지 않습니다 지어낸 것입니다"}])
            offline = lambda url: (_ for _ in ()).throw(video.VideoError("offline"))
            ps = {"claude": _Fake("claude", [json.dumps(real, ensure_ascii=False)]), "codex": _Fake("codex", [json.dumps(real, ensure_ascii=False)]),
                  "agy": _Fake("agy", [json.dumps(fake, ensure_ascii=False)])}
            r = video.run_video(_settings(), repo, "https://youtu.be/dQw4w9WgXcQ", "", ["claude", "codex", "agy"], transcript_text=text, providers=ps, http=offline)
            assert r["status"] == "DONE", r["summary"]
            good = next(c for c in r["clusters"] if c["verified"])
            assert good["agreement"] == 2 and abs(good["time"] - 5) < 1  # two AIs, the time comes from the transcript
            assert any(m["evidence"] == "quote_missing" for c in r["clusters"] for m in c["members"])  # the invented quote is caught
            assert video.title_matches("자동매매 손절 규칙", "자동매매 손절 규칙") and not video.title_matches("다른 제목", "자동매매 손절 규칙")

        def local_ai():
            import threading
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            from .providers import ollama as oll

            seen: list[dict] = []

            class H(BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def _send(self, obj):
                    body = json.dumps(obj).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

                def do_GET(self):
                    self._send({"models": [{"name": "qwen3:8b"}]})

                def do_POST(self):
                    req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    seen.append(req)
                    self._send({"message": {"content": "<think>생각</think>" + _review()}, "prompt_eval_count": 50, "eval_count": 20})

            srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                oll._cache.clear()
                p = oll.OllamaProvider(url=f"http://127.0.0.1:{srv.server_address[1]}")
                assert p.locate() and p.supports_write is False and p.default_participant is False
                r = p.run("Reply with ONLY one JSON object: {}. Review.", ".", False, 20)
                assert json.loads(r.text)["verdict"] == "PASS" and r.tokens["total"] == 70  # reasoning removed, exact tokens kept
                assert seen[0]["think"] is False and seen[0]["format"] == "json" and seen[0]["options"]["temperature"] == 0
                try:
                    oll.OllamaProvider(url="http://192.168.1.10:11434")
                except ValueError:
                    pass
                else:
                    raise AssertionError("다른 컴퓨터 주소가 허용됐습니다")
            finally:
                srv.shutdown(); srv.server_close(); oll._cache.clear()

        def folder_tools():
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(base)  # browsing is limited to the home folder: use the throw-away one
            try:
                root = base / "root2"
                made = projects.create_project(root, "rename-me", "python", "x")
                renamed = folders.rename_project(Path(made["path"]), root, "renamed-ok")
                assert renamed.is_dir() and not Path(made["path"]).exists()
                plain = base / "plain-folder"
                plain.mkdir()
                (plain / "a.py").write_text("x = 1\n")
                (plain / ".env").write_text("SECRET=1\n")
                folders.git_init(plain)
                tracked = gitops.git(["ls-files"], plain).stdout.split()
                assert "a.py" in tracked and ".env" not in tracked, tracked  # secrets are never committed
                listing = folders.browse(str(base), set())
                assert "plain-folder" in [d["name"] for d in listing["dirs"]]
                try:
                    folders.browse("/etc", set())
                except projects.ProjectError:
                    pass
                else:
                    raise AssertionError("홈 폴더 밖을 볼 수 있습니다")
                try:
                    folders.rename_project(_repo(base, "not-mine"), root, "x-new")
                except projects.ProjectError:
                    pass
                else:
                    raise AssertionError("Solomon이 만들지 않은 폴더의 이름이 바뀌었습니다")
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

        def project_folders():
            root = base / "root"
            made = projects.create_project(root, "demo-app", "python", "demo")
            path = Path(made["path"])
            assert (path / "tests" / "test_smoke.py").is_file() and gitops.git(["rev-list", "--count", "HEAD"], path).stdout.strip() == "1"
            trash = base / "trash"
            projects.trash_project(path, root, trash, "demo-app")
            assert not path.exists()
            projects.restore_project(trash, projects.list_trash(trash)[0]["id"], root)
            assert path.is_dir()
            outside = _repo(base, "other-project")
            try:
                projects.trash_project(outside, root, trash, "other-project")
            except projects.ProjectError:
                assert outside.is_dir()
            else:
                raise AssertionError("Solomon이 만들지 않은 프로젝트가 삭제 허용됐습니다")

        for title, fn in (
            ("교차 검증 파이프라인 (계획 → 구현 → 범위 검사 → 테스트 → 검수)", pipeline),
            ("범위 밖 수정 차단 · 검수 불합격 시 재시도", scope_and_retry),
            ("커밋 안 한 수정 자동 포함 · 비밀 파일 제외", snapshot),
            ("적용 → 되돌리기 → 커밋 → 커밋 취소", apply_commit_undo),
            ("폐기 → 복원", discard_restore),
            ("5W1H 기록과 적용 판단", five_w_one_h),
            ("단계마다 AI 최대 3개 (계획 검토 · 구현 교대·자문 · 3중 검수) + 프로젝트 카드 집계", three_ais_per_stage),
            ("영상 분석: 자막 인용 검증 · AI 간 교차 확인 · 제목으로 직접 시청 판별", video_analysis),
            ("신규 프로젝트 폴더 · 휴지통 · 복원 · 기존 프로젝트 삭제 거부", project_folders),
            ("폴더 편집: 이름 변경 · git 초기화(비밀 파일 제외) · 찾아보기(홈 밖 차단)", folder_tools),
            ("로컬 AI(Ollama) 연결부: 생각 블록 제거 · 정확한 토큰 · 이 컴퓨터만 허용", local_ai),
        ):
            check(title, fn)
        subprocess.run(["git", "worktree", "prune"], cwd=base, capture_output=True)

    bad = [r for r in results if not r[1]]
    out(f"결과: {len(results) - len(bad)}/{len(results)} 통과" + ("" if not bad else " — 실패 항목이 있으면 이 출력을 그대로 보내주세요."))
    return 0 if not bad else 1
