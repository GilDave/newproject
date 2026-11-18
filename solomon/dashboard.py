"""Local web dashboard: watch runs and give commands (planning doc, section 9).

Safety model, because this page can start AI agents that edit code:
- binds to 127.0.0.1 only and checks the Host header (blocks DNS rebinding);
- every /api/ call needs the random token printed at start-up (blocks other web pages);
- the original project is never modified until the user presses "apply";
- one run at a time, started as a separate `solomon run` process so stop is a plain SIGTERM.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__, analysis as analysis_mod, gitops
from .providers import PROVIDER_CLASSES, build_providers
from .settings import Settings
from .status import load_runs

RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
PERSIST_MINUTES = 180  # the dashboard's 'keep trying' stops after this long
MAX_GOAL = 20000  # long enough to paste a stack trace
MAX_BODY = 64 * 1024
HTML_PATH = Path(__file__).with_name("dashboard.html")
VENDORS = {"claude": "Anthropic", "codex": "OpenAI", "antigravity": "Google"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _default_launcher(args: list[str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True
    )


class DashboardApp:
    def __init__(
        self,
        settings: Settings,
        projects: Optional[list[str]] = None,
        config_path: Optional[str] = None,
        store_path: Optional[Path] = None,
        launcher: Optional[Callable[[list[str], Path], subprocess.Popen]] = None,
    ):
        self.settings = settings
        self.config_path = config_path
        self.store_path = store_path if store_path is not None else Path.home() / ".solomon" / "dashboard.json"
        self._launcher = launcher or _default_launcher
        self._lock = threading.Lock()
        self._projects: dict[str, dict] = {}  # path -> {"test_command": [...]} (key absent = use the config default)
        self.job: Optional[dict] = None
        self._providers_cache: tuple[float, list[dict]] = (0.0, [])
        self._git_cache: dict[str, tuple[float, bool]] = {}
        self._load_store()
        for p in list(projects or []) + ([settings.project_path] if settings.project_path else []):
            try:
                self.add_project(p)
            except (gitops.GitError, OSError):
                pass  # a stale path must not stop the dashboard from starting

    # ------------------------------------------------------------- projects
    def _load_store(self) -> None:
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            for path, cfg in (data.get("projects") or {}).items():
                if Path(path).is_dir():
                    self._projects[path] = dict(cfg) if isinstance(cfg, dict) else {}
        except (OSError, ValueError):
            pass

    def _save_store(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self.store_path.write_text(json.dumps({"projects": self._projects}, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass  # remembering projects is a convenience, never a requirement

    def is_git(self, project: str) -> bool:
        """Whether code can be changed there (needs a repository with a commit). Cached for a few seconds."""
        now = time.time()
        hit = self._git_cache.get(project)
        if hit and now - hit[0] < 5:
            return hit[1]
        try:
            gitops.ensure_repo(Path(project))
            ok = True
        except gitops.GitError:
            ok = False
        self._git_cache[project] = (now, ok)
        return ok

    def add_project(self, path: str) -> str:
        """Register a folder. Any folder works for analysis; changing code additionally needs git (checked at run)."""
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise gitops.GitError(f"project folder does not exist: {p}")
        key = str(p)
        with self._lock:
            if key not in self._projects:
                self._projects[key] = {}
                self._save_store()
        return key

    def _known(self, path: Optional[str]) -> str:
        if not path or path not in self._projects:
            raise ApiError(400, "알 수 없는 프로젝트입니다. 먼저 경로를 추가하세요.")
        return path

    def effective_test_command(self, project: str) -> tuple[Optional[list[str]], str]:
        cfg = self._projects.get(project, {})
        if "test_command" in cfg:
            return (cfg["test_command"] or None), "프로젝트별 설정"
        return self.settings.test_command, "기본 설정"

    def set_test_command(self, project: str, text: Optional[str]) -> None:
        self._known(project)
        text = (text or "").strip()
        with self._lock:
            cfg = self._projects[project]
            if not text:
                cfg.pop("test_command", None)  # back to the config default
            elif text in ("없음", "none"):
                cfg["test_command"] = []
            else:
                try:
                    cmd = shlex.split(text)
                except ValueError as exc:
                    raise ApiError(400, f"명령을 해석할 수 없습니다: {exc}") from exc
                if os.path.basename(cmd[0]) not in self.settings.approved_commands:
                    raise ApiError(400, f"'{cmd[0]}'은(는) 허용된 명령이 아닙니다 ({', '.join(self.settings.approved_commands)})")
                cfg["test_command"] = cmd
            self._save_store()

    # --------------------------------------------------------------- state
    def providers_info(self) -> list[dict]:
        now = time.time()
        if now - self._providers_cache[0] > 20:
            providers = build_providers(self.settings)
            info = [
                {"name": n, "installed": bool(providers[n].locate()),  # type: ignore[attr-defined]
                 "supports_write": bool(PROVIDER_CLASSES[n].supports_write)}
                for n in PROVIDER_CLASSES
            ]
            self._providers_cache = (now, info)
        return self._providers_cache[1]

    def job_info(self) -> dict:
        job = self.job
        if not job:
            return {"state": "idle"}
        code = job["popen"].poll()
        info = {"state": "running" if code is None else "exited", "pid": job["pid"], "project": job["project"],
                "goal": job["goal"], "exit_code": code, "started": job["started"], "kind": job.get("kind", "run")}
        if code is not None:
            try:
                info["log_tail"] = Path(job["log_path"]).read_text(encoding="utf-8", errors="replace")[-1500:]
            except OSError:
                info["log_tail"] = ""
        return info

    def build_state(self, project: Optional[str], run_id: Optional[str], analysis_id: Optional[str] = None) -> dict:
        projects = sorted(self._projects)
        if project not in self._projects:
            project = projects[0] if projects else None
        job = self.job_info()
        state: dict = {
            "version": __version__, "projects": projects, "project": project, "roles": self.settings.roles,
            "providers": self.providers_info(), "job": job, "runs": [], "current": None,
            "apply": {"possible": False, "applied": False}, "analyses": [], "analysis": None,
        }
        if not project:
            return state
        self._add_analysis_state(state, Path(project), project, analysis_id, job)
        state["project_git"] = self.is_git(project)
        cmd, source = self.effective_test_command(project)
        state["test_command"] = {"text": " ".join(cmd) if cmd else "없음", "source": source}
        runs = load_runs(Path(project), self.settings.state_dir)
        latest = runs[-1] if runs else None
        state["runs"] = [
            {"run_id": r.run_id, "status": r.status, "goal": r.goal[:120], "attempts": r.attempts, "finished": r.finished,
             "elapsed_sec": r.elapsed_sec(),
             "live": bool(job["state"] == "running" and job.get("project") == project and r is latest and not r.finished)}
            for r in reversed(runs[-30:])
        ]
        if job["state"] == "exited" and job.get("project") == project and job.get("started"):
            since = datetime.fromtimestamp(job["started"]) - timedelta(seconds=5)
            job["orphan"] = not any(r.started and r.started >= since for r in runs)  # died before creating a run
        state["provider_cards"] = self._provider_cards(runs, state["providers"])
        state["apply_pending"] = sum(
            1 for r in runs[-30:]
            if r.status == "PASSED" and (Path(project) / self.settings.state_dir / "runs" / r.run_id / "result.patch").is_file()
            and not (Path(project) / self.settings.state_dir / "runs" / r.run_id / "applied.json").exists()
        )
        pick = None
        if run_id and RUN_ID.match(run_id):
            pick = next((r for r in runs if r.run_id == run_id), None)
        pick = pick or latest
        if pick:
            running_here = job["state"] == "running" and job.get("project") == project and pick is latest
            cur = pick.to_dict()
            cur["live"] = bool(running_here and not pick.finished)
            cur["interrupted"] = bool(not pick.finished and not cur["live"])
            run_dir = Path(project) / self.settings.state_dir / "runs" / pick.run_id
            cur["paths"] = {"project": project, "run_dir": str(run_dir), "events": str(run_dir / "events.jsonl"),
                            "patch": str(run_dir / "result.patch") if (run_dir / "result.patch").is_file() else None}
            state["current"] = cur
            patch = Path(project) / self.settings.state_dir / "runs" / pick.run_id / "result.patch"
            applied = (patch.parent / "applied.json").exists()
            state["apply"] = {"possible": pick.status == "PASSED" and patch.is_file() and not applied, "applied": applied,
                              "patch": str(patch) if patch.is_file() else None}
        return state

    def _provider_cards(self, runs: list, providers: list[dict]) -> list[dict]:
        """Per-AI facts for the work room: status, today's calls and the most recent call."""
        today = datetime.now().date()
        latest = runs[-1] if runs else None
        cards = []
        for p in providers:
            name = p["name"]
            calls_today, last = 0, None
            for r in reversed(runs):
                for c in reversed(r.calls):
                    if c["provider"] != name:
                        continue
                    if last is None:
                        last = {"role": c["role"], "sec": c["sec"], "at": c["at"], "ok": c["ok"], "run_id": r.run_id}
                    if r.started and r.started.date() == today:
                        calls_today += 1
            quota = bool(latest and name in latest.quota_providers)
            cards.append({"name": name, "vendor": VENDORS.get(name, ""), "installed": p["installed"],
                          "supports_write": p["supports_write"],
                          "status": "설치 안 됨" if not p["installed"] else ("한도 소진" if quota else "정상"),
                          "calls_today": calls_today, "last_call": last})
        return cards

    def _add_analysis_state(self, state: dict, path: Path, project: str, analysis_id: Optional[str], job: dict) -> None:
        listing = analysis_mod.list_analyses(path, self.settings.state_dir)
        state["analyses"] = listing
        pick = analysis_id if analysis_id and RUN_ID.match(analysis_id) else (listing[0]["analysis_id"] if listing else None)
        if not pick:
            return
        view = analysis_mod.load_analysis(path, self.settings.state_dir, pick)
        if view is None:
            return
        latest = bool(listing) and listing[0]["analysis_id"] == pick
        running_here = job["state"] == "running" and job.get("kind") == "analyze" and job.get("project") == project and latest
        view["live"] = bool(running_here and not view["finished"])
        view["interrupted"] = bool(not view["finished"] and not view["live"])
        state["analysis"] = view

    # ------------------------------------------------------------- commands
    def _role_args(self, roles: Optional[dict]) -> list[str]:
        """Validate the roles chosen on the page and turn them into `solomon run` flags."""
        if not roles:
            return []
        if not isinstance(roles, dict):
            raise ApiError(400, "역할 배정 형식이 올바르지 않습니다.")
        args: list[str] = []
        effective = dict(self.settings.roles)
        for role in ("planner", "builder", "reviewer", "reviewer2"):
            name = roles.get(role)
            if name is None:
                continue
            if name not in PROVIDER_CLASSES:
                raise ApiError(400, f"알 수 없는 AI입니다: {name}")
            effective[role] = name
            args += [f"--{role}", name]
        if not PROVIDER_CLASSES[effective["builder"]].supports_write:
            raise ApiError(400, f"{effective['builder']}은(는) 구현을 맡을 수 없습니다 (계획·검수만 가능). 구현은 다른 AI로 고르세요.")
        if effective["builder"] == effective["reviewer"] and not self.settings.allow_same_provider:
            raise ApiError(400, "구현과 검수는 서로 다른 AI여야 합니다 (같은 AI가 자기 코드를 검수하면 교차 검증이 되지 않습니다).")
        second = effective.get("reviewer2")
        if second and second == effective["builder"]:
            raise ApiError(400, "2차 검수 AI는 구현 AI와 달라야 합니다.")
        return args

    def start_run(self, goal: str, project: str, roles: Optional[dict] = None, persist: bool = False) -> dict:
        goal = (goal or "").strip()
        if not goal:
            raise ApiError(400, "목표를 입력하세요.")
        if len(goal) > MAX_GOAL:
            raise ApiError(400, f"목표가 너무 깁니다 ({MAX_GOAL}자 이하).")
        role_args = self._role_args(roles)
        if (project or "") not in self._projects:
            try:
                project = self.add_project(project or "")
            except gitops.GitError as exc:
                raise ApiError(400, str(exc)) from exc
        if not self.is_git(project):
            raise ApiError(400, "개선(코드 수정)에는 git 저장소가 필요합니다 (커밋이 1개 이상). 분석은 git 없이도 됩니다. "
                                "고치려면 프로젝트 폴더에서 git init 후 첫 커밋을 만드세요.")
        args = [sys.executable, "-m", "solomon", "run", goal, "--project", project]
        if self.config_path:
            args += ["--config", self.config_path]
        args += role_args
        if persist:
            args += ["--persist", "--max-minutes", str(PERSIST_MINUTES)]
        cfg = self._projects[project]
        if "test_command" in cfg:
            args += ["--test-command", shlex.join(cfg["test_command"])] if cfg["test_command"] else ["--no-tests"]
        return self._spawn("run", args, project, goal)

    def start_analysis(self, project: str, focus: str = "", ais: Optional[list] = None) -> dict:
        focus = (focus or "").strip()
        if len(focus) > MAX_GOAL:
            raise ApiError(400, f"분석 요청이 너무 깁니다 ({MAX_GOAL}자 이하).")
        installed = [p["name"] for p in self.providers_info() if p["installed"]]
        if ais is not None and not isinstance(ais, list):
            raise ApiError(400, "AI 선택 형식이 올바르지 않습니다.")
        chosen = [str(a) for a in (ais or installed)]
        bad = [a for a in chosen if a not in PROVIDER_CLASSES]
        if bad:
            raise ApiError(400, f"알 수 없는 AI입니다: {', '.join(bad)}")
        missing = [a for a in chosen if a not in installed]
        if missing:
            raise ApiError(400, f"설치되지 않은 AI입니다: {', '.join(missing)}")
        if not chosen:
            raise ApiError(400, "분석에 쓸 수 있는 AI가 설치되어 있지 않습니다.")
        if (project or "") not in self._projects:
            try:
                project = self.add_project(project or "")
            except gitops.GitError as exc:
                raise ApiError(400, str(exc)) from exc
        args = [sys.executable, "-m", "solomon", "analyze", "--project", project, "--ais", ",".join(chosen)]
        if focus:
            args += ["--focus", focus]
        if self.config_path:
            args += ["--config", self.config_path]
        return self._spawn("analyze", args, project, focus or "전반 분석")

    def _spawn(self, kind: str, args: list[str], project: str, label: str) -> dict:
        with self._lock:
            if self.job and self.job["popen"].poll() is None:
                raise ApiError(409, "이미 실행 중인 작업이 있습니다. 끝나거나 중지한 뒤 다시 시도하세요.")
            log_path = Path(project) / self.settings.state_dir / "dashboard.log"
            try:
                popen = self._launcher(args, log_path)
            except OSError as exc:
                raise ApiError(500, f"실행을 시작하지 못했습니다: {exc}") from exc
            self.job = {"popen": popen, "pid": popen.pid, "project": project, "goal": label, "started": time.time(),
                        "log_path": str(log_path), "kind": kind}
        return self.job_info()

    def stop_run(self) -> dict:
        job = self.job
        if not job or job["popen"].poll() is not None:
            raise ApiError(409, "실행 중인 작업이 없습니다.")
        popen = job["popen"]
        try:
            os.killpg(os.getpgid(popen.pid), signal.SIGTERM)  # solomon turns SIGTERM into a clean STOPPED run
        except (ProcessLookupError, PermissionError):
            pass

        def hard_kill() -> None:
            if popen.poll() is None:
                try:
                    os.killpg(os.getpgid(popen.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

        timer = threading.Timer(15.0, hard_kill)
        timer.daemon = True
        timer.start()
        return self.job_info()

    def apply_run(self, project: str, run_id: str) -> dict:
        project = self._known(project)
        if not RUN_ID.match(run_id or ""):
            raise ApiError(400, "실행 번호가 올바르지 않습니다.")
        run_dir = Path(project) / self.settings.state_dir / "runs" / run_id
        patch = run_dir / "result.patch"
        if not patch.is_file():
            raise ApiError(404, "이 실행에는 적용할 패치가 없습니다 (PASSED 상태에서만 만들어집니다).")
        if (run_dir / "applied.json").exists():
            raise ApiError(409, "이미 적용한 실행입니다.")
        if self.job and self.job["popen"].poll() is None and self.job.get("project") == project:
            raise ApiError(409, "이 프로젝트에서 작업이 진행 중입니다. 끝난 뒤 적용하세요.")
        try:
            gitops.apply_patch(Path(project), patch)
        except gitops.GitError as exc:
            raise ApiError(409, f"패치를 적용하지 못했습니다 (원본이 그사이 바뀌었을 수 있습니다): {exc}") from exc
        (run_dir / "applied.json").write_text(json.dumps({"applied_at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
        return {"ok": True, "message": "원본 작업 폴더에 적용했습니다. 커밋은 하지 않았습니다. git diff로 확인하세요."}


def make_handler(app: DashboardApp, token: str, allowed_hosts: set[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Solomon"

        def log_message(self, fmt, *args):  # quiet by default
            pass

        # -------------------------------------------------------- helpers
        def _send(self, status: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "") in allowed_hosts

        def _token_ok(self, query: dict) -> bool:
            given = self.headers.get("X-Solomon-Token") or (query.get("t") or [""])[0]
            return hmac.compare_digest(given.encode(), token.encode())

        # ----------------------------------------------------------- GET
        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "허용되지 않은 호스트입니다."})
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                csp = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                       "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'")
                return self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8", {"Content-Security-Policy": csp})
            if url.path == "/api/state":
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다. 터미널에 표시된 주소로 여세요."})
                state = app.build_state((query.get("project") or [None])[0], (query.get("run") or [None])[0],
                                        (query.get("analysis") or [None])[0])
                return self._json(200, state)
            return self._json(404, {"error": "not found"})

        # ---------------------------------------------------------- POST
        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"error": "허용되지 않은 호스트입니다."})
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
                return self._json(403, {"error": "허용되지 않은 출처입니다."})
            if not self._token_ok({}):
                return self._json(401, {"error": "토큰이 없습니다."})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                return self._json(413, {"error": "요청이 너무 큽니다."})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "JSON 객체를 보내야 합니다."})
            try:
                path = urlparse(self.path).path
                if path == "/api/run":
                    return self._json(200, {"job": app.start_run(str(body.get("goal", "")), str(body.get("project", "")), body.get("roles"), bool(body.get("persist")))})
                if path == "/api/analyze":
                    return self._json(200, {"job": app.start_analysis(str(body.get("project", "")), str(body.get("focus", "")), body.get("ais"))})
                if path == "/api/stop":
                    return self._json(200, {"job": app.stop_run()})
                if path == "/api/apply":
                    return self._json(200, app.apply_run(str(body.get("project", "")), str(body.get("run_id", ""))))
                if path == "/api/project":
                    try:
                        return self._json(200, {"project": app.add_project(str(body.get("path", "")))})
                    except gitops.GitError as exc:
                        raise ApiError(400, str(exc)) from exc
                if path == "/api/test-command":
                    app.set_test_command(str(body.get("project", "")), body.get("command"))
                    return self._json(200, {"ok": True})
                return self._json(404, {"error": "not found"})
            except ApiError as exc:
                return self._json(exc.status, {"error": exc.message})

    return Handler


def serve(
    settings: Settings,
    projects: list[str],
    config_path: Optional[str] = None,
    port: int = 8765,
    open_browser: bool = True,
) -> int:
    token = secrets.token_urlsafe(24)
    app = DashboardApp(settings, projects, config_path)
    server = None
    for candidate in range(port, port + 10):
        hosts = {f"127.0.0.1:{candidate}", f"localhost:{candidate}"}
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), make_handler(app, token, hosts))
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        print(f"error: ports {port}-{port + 9} are all in use", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{port}/?t={token}"
    print(f"Solomon 대시보드: {url}", flush=True)
    print("이 주소는 이 컴퓨터에서만 열립니다. 주소의 t=... 값이 접속 키이니 다른 사람에게 공유하지 마세요. 종료: Ctrl+C", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if app.job and app.job["popen"].poll() is None:
            print("실행 중인 작업은 계속 진행됩니다. 멈추려면 대시보드에서 중지하세요.")
    return 0
