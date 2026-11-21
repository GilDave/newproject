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
import shutil
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from . import (__version__, joblock, analysis as analysis_mod, alerts as alerts_mod, attachments as attach_mod, autofix as autofix_mod, botlab as botlab_mod, exchange_keys as keys_mod, trading_mode as mode_mod, quota as quota_mod, folders, gitops, manage, policy as policy_mod,
               portfolio, projects as projects_mod, report as report_mod, shadow as shadow_mod, video as video_mod, vision as vision_mod)
from .providers import PROVIDER_CLASSES, build_providers
from .settings import ALL_ROLES, ROLES, Settings
from .status import load_runs

RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
PERSIST_MINUTES = 180  # the dashboard's 'keep trying' stops after this long
PERSIST_ATTEMPTS = 24  # ... or after this many attempts: a backstop behind the repeated-failure detection
MAX_GOAL = 20000  # long enough to paste a stack trace
MAX_BODY = 16 * 1024 * 1024  # a pasted video transcript or up to four screenshots (the page is local-only and token-protected)
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
    env = os.environ.copy()  # jobs must find this very copy of solomon even when it was started from a checkout without pip install
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = pkg_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    popen = subprocess.Popen(
        args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env
    )
    keep_awake(popen.pid)
    return popen


def keep_awake(pid: int) -> None:
    """macOS: stop the Mac from idle-sleeping while the job runs (an overnight batch must not be put to sleep).
    caffeinate exits by itself when the job does. Best effort: no caffeinate, no problem."""
    exe = shutil.which("caffeinate") if sys.platform == "darwin" else None
    if exe:
        try:
            subprocess.Popen([exe, "-i", "-w", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass


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
        self.trash_dir = self.store_path.parent / "trash"
        self._lock = threading.Lock()
        self._projects: dict[str, dict] = {}  # path -> {"test_command": [...]} (key absent = use the config default)
        self.job: Optional[dict] = None
        self._providers_cache: tuple[float, list[dict]] = (0.0, [])
        self._git_cache: dict[str, tuple[float, bool]] = {}
        self._portfolio_cache: Optional[tuple[float, dict]] = None
        self.botlab = botlab_mod.BotLab(self.store_path.parent / "botlab")
        self.alerts = alerts_mod.Alerts(self.botlab.dir)
        self._scheduler_stop = threading.Event()
        self.boot = f"{time.time():.3f}"  # changes when the dashboard restarts, so an open page knows to reload itself
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
        self._portfolio_cache = None  # a project added now must show up on the board immediately
        return key

    # ------------------------------------------------------- project folders
    def projects_root(self) -> Path:
        return Path(self.settings.projects_root).expanduser().resolve()

    def projects_overview(self) -> dict:
        rows = []
        for path in sorted(self._projects):
            cmd, source = self.effective_test_command(path)
            rows.append({"path": path, "name": Path(path).name, "alias": self._projects[path].get("alias", ""), "memo": self._projects[path].get("memo", ""),
                         "managed": projects_mod.is_managed(Path(path), self.projects_root()),
                         "git": self.is_git(path), "test_command": " ".join(cmd) if cmd else "", "busy": self._busy_here(path)})
        return {"projects": rows, "trash": projects_mod.list_trash(self.trash_dir), "root": str(self.projects_root()),
                "stacks": {k: v["label"] for k, v in projects_mod.STACKS.items()}}

    def portfolio(self) -> dict:
        """Every project as a card of measured numbers, plus the per-AI totals. Cached for a few seconds."""
        now = time.time()
        if self._portfolio_cache and now - self._portfolio_cache[0] < 4:
            return self._portfolio_cache[1]
        cards = []
        for path in sorted(self._projects):
            cmd, _ = self.effective_test_command(path)
            try:
                c = portfolio.card(Path(path), self.settings.state_dir, is_git=self.is_git(path),
                                   managed=projects_mod.is_managed(Path(path), self.projects_root()),
                                   test_command=" ".join(cmd) if cmd else "", busy=self._busy_here(path))
            except Exception as exc:  # one broken project folder must not blank the whole board
                c = {"error": f"{type(exc).__name__}: {exc}", "counts": {}, "ais": {}, "recent": [], "next": {"label": "", "action": ""}}
            c.update(path=path, name=Path(path).name, alias=self._projects[path].get("alias", ""), memo=self._projects[path].get("memo", ""))
            cards.append(c)
        ok = [c for c in cards if "error" not in c]
        sc = {"adopt": {"accepted": 0, "discarded": 0, "pending": 0}, "need_human": {"accepted": 0, "discarded": 0, "pending": 0}}
        for c in ok:
            for kind in sc:
                for b in sc[kind]:
                    sc[kind][b] += c["arbiter"][kind][b]
        data = {"projects": cards, "ais": portfolio.merge_ais(ok), "roles": self.settings.roles, "arbiter": sc,
                "totals": {"projects": len(cards), "busy": sum(1 for c in ok if c["busy"]), "pending": sum(c["counts"]["pending"] for c in ok),
                           "runs": sum(c["counts"]["runs"] for c in ok), "passed": sum(c["counts"]["passed"] for c in ok),
                           "tokens": sum(c["tokens"] for c in ok)}}
        self._portfolio_cache = (now, data)
        return data

    # ------------------------------------------------------- folder tools (no terminal needed)
    def browse(self, path: Optional[str]) -> dict:
        try:
            return folders.browse(path, set(self._projects))
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc

    def make_folder(self, parent: str, name: str) -> dict:
        try:
            target = folders.make_folder(parent, name)
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc
        return {"ok": True, "path": str(target), "message": f"새 폴더를 만들었습니다: {target.name}"}

    def _fresh(self, project: Optional[str] = None) -> None:
        self._portfolio_cache = None
        self._git_cache.clear()

    def project_meta(self, path: str, alias: str, memo: str) -> dict:
        path = self._known(path)
        alias, memo = (alias or "").strip(), (memo or "").strip()
        if len(alias) > 40 or len(memo) > 500:
            raise ApiError(400, "별명은 40자, 메모는 500자 이하로 입력하세요.")
        with self._lock:
            cfg = self._projects[path]
            for key, val in (("alias", alias), ("memo", memo)):
                if val:
                    cfg[key] = val
                else:
                    cfg.pop(key, None)
            self._save_store()
        self._fresh()
        return {"ok": True, "message": "저장했습니다. (폴더와 파일은 바뀌지 않습니다)"}

    def rename_project(self, path: str, name: str) -> dict:
        path = self._known(path)
        if self._busy_here(path):
            raise ApiError(409, "이 프로젝트에서 작업이 진행 중입니다. 끝난 뒤 바꾸세요.")
        try:
            target = folders.rename_project(Path(path), self.projects_root(), name)
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc
        with self._lock:
            self._projects[str(target)] = self._projects.pop(path)
            self._save_store()
        self._fresh()
        return {"ok": True, "project": str(target), "message": f"폴더 이름을 바꿨습니다: {target.name}"}

    def git_init_preview(self, path: str) -> dict:
        path = self._known(path)
        try:
            return folders.git_init_preview(Path(path))
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc

    def git_init(self, path: str) -> dict:
        path = self._known(path)
        if self._busy_here(path):
            raise ApiError(409, "이 프로젝트에서 작업이 진행 중입니다.")
        try:
            res = folders.git_init(Path(path))
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc
        except gitops.GitError as exc:
            raise ApiError(500, f"git 초기화에 실패했습니다: {exc}") from exc
        self._fresh()
        return {"ok": True, "message": f"git 저장소를 만들고 파일 {res['files']}개를 첫 커밋으로 기록했습니다. 이제 개선·밤샘 수정을 쓸 수 있습니다."}

    def open_path(self, path: str) -> dict:
        try:
            target = Path(path or "").expanduser().resolve()
        except OSError as exc:
            raise ApiError(400, "경로를 해석할 수 없습니다.") from exc
        ok = False
        for proj in self._projects:
            try:
                target.relative_to(Path(proj).resolve())
                ok = True
                break
            except ValueError:
                continue
        if not ok:
            raise ApiError(403, "등록된 프로젝트 안의 경로만 열 수 있습니다.")
        if not target.exists():
            raise ApiError(404, "경로가 없습니다.")
        try:
            folders.open_path(target)
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc
        return {"ok": True, "message": "열었습니다."}

    # ------------------------------------------------------- tools: checks, installs, update, restart
    def source_root(self) -> Optional[Path]:
        root = Path(__file__).resolve().parent.parent
        return root if (root / ".git").exists() and (root / "scripts" / "update.sh").is_file() else None

    def started_by_script(self) -> bool:
        pid_file = Path(os.environ.get("SOLOMON_HOME", str(Path.home() / ".solomon"))) / "dashboard.pid"
        try:
            return pid_file.read_text().strip() == str(os.getpid())
        except OSError:
            return False

    def _ollama_tools(self) -> dict:
        from .providers import ollama as ollama_mod

        return {"model": self.settings.ollama_model, "alias": ollama_mod.ALIAS_NAME, "alias_active": self.settings.ollama_model == ollama_mod.ALIAS_NAME,
                "loaded": ollama_mod.loaded_models(self.settings.ollama_url)}

    def tools_status(self) -> dict:
        from .env import resolve_cli
        import platform
        return {"version": __version__, "python": platform.python_version(), "git": folders.git_identity(), "ytdlp": resolve_cli("yt-dlp") or "",
                "can_update": self.source_root() is not None, "can_restart": self.source_root() is not None and self.started_by_script(),
                "can_open": folders.open_command() is not None, "projects_root": str(self.projects_root()),
                "ollama": self._ollama_tools(), "vision": vision_mod.load(), "quota": quota_mod.QuotaBoard.persistent().snapshot()}

    _SHOT_RUN = re.compile(r"^[0-9A-Za-z_.-]{1,80}$")
    _SHOT_NAME = re.compile(r"^[0-9A-Za-z_.-]{1,120}\.png$")

    def shot_path(self, project: str, run: str, attempt: str, name: str) -> Path:
        """One screenshot of a run, by validated parts only (no path is ever taken from the request as is)."""
        project = self._known(project)
        if not (self._SHOT_RUN.match(run) and attempt.isdigit() and len(attempt) <= 4 and self._SHOT_NAME.match(name)) or ".." in run:
            raise ApiError(400, "잘못된 요청입니다.")
        root = (Path(project) / self.settings.state_dir / "runs" / run / "shots" / f"attempt-{int(attempt)}").resolve()
        f = (root / name).resolve()
        if root not in f.parents or not f.is_file():
            raise ApiError(404, "이미지를 찾지 못했습니다.")
        return f

    def attachment_path(self, project: str, run: str, name: str) -> Path:
        project = self._known(project)
        if not (self._SHOT_RUN.match(run) and re.match(r"^img-\d{1,2}\.(png|jpg|webp|gif)$", name)) or ".." in run:
            raise ApiError(400, "잘못된 요청입니다.")
        root = (Path(project) / self.settings.state_dir / "runs" / run / "attachments").resolve()
        f = (root / name).resolve()
        if root not in f.parents or not f.is_file():
            raise ApiError(404, "이미지를 찾지 못했습니다.")
        return f

    def project_policy(self, path: str, body: Optional[dict] = None) -> dict:
        path = self._known(path)
        root = Path(path)
        cur = policy_mod.load(root, self.settings.state_dir)
        if body is not None:
            visual = cur["visual"]
            if "visual" in body:
                v = body["visual"]
                if v in (None, "", {}):
                    visual = None
                else:
                    cmd = v.get("command") if isinstance(v, dict) else v
                    cmd = shlex.split(cmd) if isinstance(cmd, str) else cmd
                    if not isinstance(cmd, list) or not cmd:
                        raise ApiError(400, "캡처 명령을 입력하세요 (예: {python} scripts/visual_capture.py {out}).")
                    visual = {"command": [str(c) for c in cmd], "timeout": (v.get("timeout") if isinstance(v, dict) else None) or 180,
                              "max_shots": (v.get("max_shots") if isinstance(v, dict) else None) or 6}
            protect = body.get("protect", cur["protect"])
            if not isinstance(protect, list):
                raise ApiError(400, "보호 경로 형식이 올바르지 않습니다.")
            try:
                cur = policy_mod.save(root, self.settings.state_dir, [str(x) for x in protect], bool(body.get("shadow", cur["shadow"])), visual)
            except ValueError as exc:
                raise ApiError(400, str(exc)) from exc
        suggest = None
        if (root / "scripts" / "visual_capture.py").is_file():
            suggest = ["{python}", "scripts/visual_capture.py", "{out}"]
        return {"policy": cur, "suggest_visual": suggest, "vision": vision_mod.load()}

    def clear_quota(self) -> dict:
        quota_mod.QuotaBoard.persistent().clear(None)
        return {"ok": True, "message": "한도 기록을 지웠습니다. 다음 실행부터 원래 역할 배정대로 시작합니다."}

    def make_ollama_alias(self) -> dict:
        from .providers import ollama as ollama_mod
        from .settings import ollama_override, save_ollama_override

        base = str(ollama_override().get("base") or self.settings.ollama_model)
        if base == ollama_mod.ALIAS_NAME:
            raise ApiError(400, "기준 모델을 알 수 없습니다. solomon.toml [ollama] model 에 원래 모델 이름을 적어주세요.")
        try:
            ollama_mod.create_alias(self.settings.ollama_url, base, ollama_mod.ALIAS_NAME, self.settings.ollama_num_ctx)
        except ollama_mod.ProviderError as exc:
            raise ApiError(502, str(exc)) from exc
        save_ollama_override(ollama_mod.ALIAS_NAME, base)
        self.settings.ollama_model = ollama_mod.ALIAS_NAME
        self._providers_cache = (0.0, [])
        return {"ok": True, "message": f"'{ollama_mod.ALIAS_NAME}' 모델을 만들었습니다({base}와 같은 가중치, 디스크 추가 거의 없음). "
                                       "이제 Solomon은 이 이름만 쓰므로 봇의 Ollama 사용과 서로 밀어내지 않습니다. 새로 시작하는 작업부터 적용됩니다."}

    def set_git_identity(self, name: str, email: str) -> dict:
        try:
            ident = folders.set_git_identity(name, email)
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc
        except gitops.GitError as exc:
            raise ApiError(500, str(exc)) from exc
        return {"ok": True, "git": ident, "message": "git 사용자 정보를 저장했습니다. 이제 대시보드에서 커밋할 수 있습니다."}

    def run_tool(self, tool: str) -> dict:
        py = [sys.executable, "-m"]
        cfg = ["--config", self.config_path] if self.config_path else []
        if tool == "doctor":
            return self._spawn("tool", py + ["solomon", "doctor"] + cfg, None, "환경 점검")
        if tool == "vision":
            return self._spawn("tool", py + ["solomon", "vision-probe"] + cfg, None, "화면 보기 능력 시험 (AI별)")
        if tool == "ollama":
            return self._spawn("tool", py + ["solomon", "ollama"] + cfg, None, "Ollama 연결 시험")
        if tool == "selftest":
            return self._spawn("tool", py + ["solomon", "selftest"], None, "자체 점검 (가짜 AI)")
        if tool == "install-ytdlp":
            return self._spawn("tool", py + ["pip", "install", "--upgrade", "yt-dlp"], None, "yt-dlp 설치 (영상 자막 수집용)")
        if tool == "update":
            root = self.source_root()
            if not root:
                raise ApiError(501, "git으로 받은 폴더에서 시작한 경우에만 자동 업데이트할 수 있습니다.")
            return self._spawn("tool", ["bash", str(root / "scripts" / "update.sh")], None, "Solomon 업데이트 (git pull)")
        raise ApiError(400, "알 수 없는 도구입니다.")

    def restart(self) -> dict:
        root = self.source_root()
        if not root or not self.started_by_script():
            raise ApiError(501, "scripts/start.sh 로 시작한 대시보드만 이 화면에서 재시작할 수 있습니다.")
        if self.job and self.job["popen"].poll() is None:
            raise ApiError(409, "작업이 진행 중입니다. 끝나거나 중지한 뒤 재시작하세요 (재시작하면 진행 상황 연결이 끊깁니다).")
        subprocess.Popen(["bash", str(root / "scripts" / "restart.sh")], start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        return {"ok": True, "message": "재시작합니다. 몇 초 뒤 자동으로 다시 연결됩니다."}

    def create_project(self, body: dict) -> dict:
        self._portfolio_cache = None
        if str(body.get("goal", "")).strip():
            self._role_args(body.get("roles"))  # a bad role choice must fail before a folder is created
        try:
            made = projects_mod.create_project(self.projects_root(), str(body.get("name", "")), str(body.get("stack", "python")),
                                               str(body.get("goal", ""))[:2000])
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc
        path = self.add_project(made["path"])
        with self._lock:
            self._projects[path]["test_command"] = made["test_command"] or []
            self._save_store()
        goal = str(body.get("goal", "")).strip()
        job = None
        if goal:
            job = self.start_run(goal, path, body.get("roles"), bool(body.get("persist")), fresh=True, shadow=bool(body.get("shadow")))
        return {"project": path, "job": job, "message": f"프로젝트 폴더를 만들었습니다: {path}" + (" · 첫 구현을 시작했습니다." if job else "")}

    def remove_project(self, path: str) -> dict:
        self._portfolio_cache = None
        path = self._known(path)
        if self._busy_here(path):
            raise ApiError(409, "이 프로젝트에서 작업이 진행 중입니다.")
        with self._lock:
            self._projects.pop(path, None)
            self._save_store()
        return {"ok": True, "message": "목록에서 제거했습니다. 폴더와 파일은 그대로입니다."}

    def delete_project(self, path: str, confirm: str) -> dict:
        self._portfolio_cache = None
        path = self._known(path)
        if self._busy_here(path):
            raise ApiError(409, "이 프로젝트에서 작업이 진행 중입니다. 끝나거나 중지한 뒤 삭제하세요.")
        try:
            row = projects_mod.trash_project(Path(path), self.projects_root(), self.trash_dir, confirm)
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc
        with self._lock:
            self._projects.pop(path, None)
            self._save_store()
        return {"ok": True, "message": f"휴지통으로 옮겼습니다 ({row['name']}). 휴지통에서 복원할 수 있습니다."}

    def restore_project(self, entry_id: str) -> dict:
        self._portfolio_cache = None
        try:
            path = projects_mod.restore_project(self.trash_dir, entry_id, self.projects_root())
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc
        self.add_project(str(path))
        return {"ok": True, "message": f"복원했습니다: {path}", "project": str(path)}

    def purge_trash(self, entry_id: str, confirm: str) -> dict:
        try:
            projects_mod.purge_trash(self.trash_dir, entry_id, confirm)
        except projects_mod.ProjectError as exc:
            raise ApiError(exc.status, exc.message) from exc
        return {"ok": True, "message": "영구 삭제했습니다."}

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
                 "supports_write": bool(PROVIDER_CLASSES[n].supports_write),
                 "default": bool(getattr(PROVIDER_CLASSES[n], "default_participant", True)),  # asked when no AI is chosen
                 "local": bool(getattr(PROVIDER_CLASSES[n], "local", False)),
                 "model": self.settings.ollama_model if n == "ollama" else ""}
                for n in PROVIDER_CLASSES
            ]
            self._providers_cache = (now, info)
        return self._providers_cache[1]

    def job_info(self) -> dict:
        job = self.job
        if not job or job["popen"].poll() is not None:
            if joblock.is_busy():  # running, but not started by this dashboard (terminal, or before a dashboard restart)
                return {"state": "running", "external": True, "pid": 0, "project": None, "goal": "다른 곳(터미널·이전 대시보드)에서 실행 중인 작업",
                        "exit_code": None, "started": time.time(), "kind": "external"}
        if not job:
            return {"state": "idle"}
        code = job["popen"].poll()
        info = {"state": "running" if code is None else "exited", "pid": job["pid"], "project": job["project"],
                "goal": job["goal"], "exit_code": code, "started": job["started"], "kind": job.get("kind", "run")}
        if code is not None:
            try:
                info["log_tail"] = Path(job["log_path"]).read_text(encoding="utf-8", errors="replace")[-(6000 if job.get("kind") == "tool" else 1500):]
            except OSError:
                info["log_tail"] = ""
        return info

    def build_state(self, project: Optional[str], run_id: Optional[str], analysis_id: Optional[str] = None, video_id: Optional[str] = None) -> dict:
        projects = sorted(self._projects)
        if project not in self._projects:
            project = projects[0] if projects else None
        job = self.job_info()
        state: dict = {
            "version": __version__, "boot": self.boot, "projects": projects, "project": project, "roles": self.settings.roles,
            "providers": self.providers_info(), "job": job, "runs": [], "current": None,
            "apply": {"possible": False, "applied": False}, "analyses": [], "analysis": None, "batches": [],
            "videos": [], "video": None, "video_capability": video_mod.load_capability(),
        }
        if not project:
            return state
        self._add_analysis_state(state, Path(project), project, analysis_id, job)
        self._add_video_state(state, Path(project), project, video_id, job)
        state["project_git"] = self.is_git(project)
        state["batches"] = autofix_mod.list_batches(Path(project), self.settings.state_dir)
        state["protect"] = policy_mod.load(Path(project), self.settings.state_dir)["protect"]  # files no AI may change (auto-fix skips them)
        cmd, source = self.effective_test_command(project)
        state["test_command"] = {"text": " ".join(cmd) if cmd else "없음", "source": source}
        runs = load_runs(Path(project), self.settings.state_dir)
        tracing_runs = job["state"] == "running" and job.get("project") == project and job.get("kind") in {"run", "autofix"}
        latest = runs[-1] if runs else None
        state["runs"] = [
            {"run_id": r.run_id, "status": r.status, "goal": r.goal[:120], "attempts": r.attempts, "finished": r.finished,
             "elapsed_sec": r.elapsed_sec(),
             "live": bool(tracing_runs and r is latest and not r.finished)}
            for r in reversed(runs[-30:])
        ]
        if job["state"] == "exited" and job.get("project") == project and job.get("started") and job.get("kind") == "run":  # only a run is expected to create a run record
            since = datetime.fromtimestamp(job["started"]) - timedelta(seconds=5)
            job["orphan"] = not any(r.started and r.started >= since for r in runs)  # died before creating a run
        state["provider_cards"] = self._provider_cards(runs, state["providers"], project)
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
            running_here = tracing_runs and pick is latest
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

    def _analysis_calls(self, project: str, day: str) -> list[dict]:
        """AI calls made by today's analyses (they live next to the runs, not inside them)."""
        out: list[dict] = []
        root = Path(project) / self.settings.state_dir / "analyses"
        if not root.is_dir():
            return out
        stamp = day.replace("-", "")
        for d in sorted(root.iterdir()):
            if not d.name.startswith(stamp):
                continue
            try:
                lines = (d / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("event") == "ai_end" and ev.get("provider"):
                    ts = str(ev.get("ts", ""))
                    out.append({"provider": str(ev["provider"]), "role": "analysis", "sec": round(float(ev.get("duration_sec") or 0), 1),
                                "day": ts[:10], "at": ts[11:19], "ok": bool(ev.get("ok")), "run_id": d.name})
        return out

    def _provider_cards(self, runs: list, providers: list[dict], project: Optional[str] = None) -> list[dict]:
        """Per-AI facts for the work room: status, today's calls and the most recent call.
        Counted by the day of each call (a run that started yesterday evening still counts its calls after midnight) and including analyses."""
        day = datetime.now().strftime("%Y-%m-%d")
        latest = runs[-1] if runs else None
        extra = self._analysis_calls(project, day) if project else []
        cards = []
        for p in providers:
            name = p["name"]
            calls_today, last, last_key = 0, None, ""
            mine = [dict(c, run_id=r.run_id, day=c.get("day") or (r.started.strftime("%Y-%m-%d") if r.started else ""))
                    for r in runs[-60:] for c in r.calls if c["provider"] == name]
            mine += [c for c in extra if c["provider"] == name]
            for c in mine:
                if c["day"] == day:
                    calls_today += 1
                key = f"{c['day']} {c['at']}"
                if key >= last_key:
                    last_key, last = key, {"role": c["role"], "sec": c["sec"], "at": c["at"], "ok": c["ok"], "run_id": c["run_id"]}
            quota = bool(latest and name in latest.quota_providers)
            cards.append({"name": name, "vendor": VENDORS.get(name, ""), "installed": p["installed"],
                          "supports_write": p["supports_write"],
                          "status": "설치 안 됨" if not p["installed"] else ("한도 소진" if quota else "정상"),
                          "calls_today": calls_today, "last_call": last})
        return cards

    def _add_video_state(self, state: dict, path: Path, project: str, video_run_id: Optional[str], job: dict) -> None:
        listing = video_mod.list_videos(path, self.settings.state_dir)
        state["videos"] = listing
        pick = video_run_id if video_run_id and RUN_ID.match(video_run_id) else (listing[0]["analysis_id"] if listing else None)
        view = video_mod.load_video(path, self.settings.state_dir, pick) if pick else None
        if view is None:
            return
        latest = bool(listing) and listing[0]["analysis_id"] == pick
        running_here = job["state"] == "running" and job.get("kind") == "video" and job.get("project") == project and latest
        view["live"] = bool(running_here and not view["finished"])
        view["interrupted"] = bool(not view["finished"] and not view["live"])
        state["video"] = view

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

    # ------------------------------------------------------------- bot lab (a live bot, evaluated and improved automatically)
    def botlab_state(self) -> dict:
        lab, cfg = self.botlab, self.botlab.config()
        proj = cfg["bot_dir"]
        hist = lab.history()
        out = {"config": cfg, "suggest": botlab_mod.detect(list(self._projects)) if not proj else "", "running": lab.running,
               "message": lab.last_message, "next_eval": (cfg["last_eval"] + cfg["interval_hours"] * 3600) if cfg["enabled"] and cfg["last_eval"] else None,
               "next_analyze": (cfg["last_analyze"] + cfg["analyze_hours"] * 3600) if cfg["auto_analyze"] and cfg["last_analyze"] else None,
               "default_protect": botlab_mod.DEFAULT_PROTECT, "latest": hist[-1] if hist else None, "series": lab.series(hist),
               "services": lab.services(), "health": lab.health(proj) if proj else None, "policy": None, "arbiter_shadow": None,
               "ollama": next((p for p in self.providers_info() if p["name"] == "ollama"), None), "recommended_protect": botlab_mod.PAPER_PROTECT,
               "trading": None, "keys": None, "keys_check": self._keys_check(), "monitor": self.botlab.monitor() if proj else None,
               "alerts": self.alerts.public()}
        if proj:
            out["alerts"]["position"] = self.alerts.position(proj)
            out["keys"] = keys_mod.status(proj)
            out["trading"] = mode_mod.state(proj, out["health"], out["keys_check"], time.time())
        if proj and proj in self._projects:
            out["policy"] = policy_mod.load(Path(proj), self.settings.state_dir)
            try:
                out["arbiter_shadow"] = shadow_mod.evaluate(Path(proj), self.settings.state_dir)
            except Exception:
                out["arbiter_shadow"] = None
        return out

    # ---- exchange keys and the paper/live switch (secrets are written to the bot's .env and never come back)
    def _keys_check(self) -> dict:
        try:
            return json.loads((self.botlab.dir / "keys_check.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_keys_check(self, ex: str, res: dict) -> None:
        data = self._keys_check()
        data[ex] = {k: v for k, v in res.items() if k not in ("key", "secret")}
        self.botlab.dir.mkdir(parents=True, exist_ok=True)
        (self.botlab.dir / "keys_check.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _bot_dir(self) -> str:
        d = self.botlab.config()["bot_dir"]
        if not d:
            raise ApiError(400, "먼저 봇 폴더를 지정하세요.")
        return d

    def botlab_keys(self, body: dict) -> dict:
        d, ex, action = self._bot_dir(), str(body.get("exchange", "")), str(body.get("action", ""))
        try:
            if action == "save":
                st = keys_mod.save(d, ex, str(body.get("key", "")), str(body.get("secret", "")))
                data = self._keys_check()
                data.pop(ex, None)  # a new key has not been checked yet
                (self.botlab.dir).mkdir(parents=True, exist_ok=True)
                (self.botlab.dir / "keys_check.json").write_text(json.dumps(data), encoding="utf-8")
                msg = f"{st['label']} 키를 봇 폴더의 .env에 저장했습니다 (이전 .env는 백업). 화면에는 다시 표시되지 않습니다. 이제 '검증'을 눌러 권한을 확인하세요."
            elif action == "verify":
                pair = keys_mod.load(d, ex)
                if not pair:
                    raise ApiError(400, "저장된 키가 없습니다. 먼저 저장하세요.")
                res = keys_mod.verify(ex, pair[0], pair[1], bool(body.get("testnet")))
                self._save_keys_check(ex, res)
                msg = ("검증했습니다." if res["ok"] else res["error"]) + (" ⚠ 이 키는 출금 권한이 켜져 있습니다. 새 키를 출금 권한 없이 만들어 바꾸세요." if res.get("withdraw") else "")
            elif action == "remove":
                keys_mod.remove(d, ex)
                data = self._keys_check()
                data.pop(ex, None)
                (self.botlab.dir / "keys_check.json").write_text(json.dumps(data), encoding="utf-8")
                msg = "키를 .env에서 지웠습니다."
            else:
                raise ApiError(400, "알 수 없는 동작입니다.")
        except keys_mod.KeyError_ as exc:
            raise ApiError(exc.status, exc.message) from exc
        out = self.botlab_state()
        out["message"] = msg
        return out

    def botlab_alerts(self, body: dict) -> dict:
        action = str(body.get("action", ""))
        try:
            if action == "save":
                self.alerts.update(body)
                msg = "저장했습니다. 키와 암호화 키는 화면에 다시 표시되지 않습니다."
            elif action == "test":
                self.alerts.test()
                msg = "테스트 알림을 보냈습니다. 아이폰을 확인하세요."
            elif action == "forget":
                self.alerts.forget()
                msg = "키를 지우고 알림을 껐습니다."
            else:
                raise ApiError(400, "알 수 없는 동작입니다.")
        except alerts_mod.AlertError as exc:
            raise ApiError(exc.status, exc.message) from exc
        out = self.botlab_state()
        out["message"] = msg
        return out

    def botlab_restart(self, body: dict) -> dict:
        """Restart the ENA bot's launchd services chosen on the page (the trading engine last)."""
        try:
            res = self.botlab.restart_services(body.get("labels"))
        except botlab_mod.BotLabError as exc:
            raise ApiError(400, str(exc)) from exc
        bad = [r["label"] for r in res["results"] if not r["ok"]]
        names = ", ".join(r["label"] for r in res["results"] if r["ok"])
        res["message"] = (f"재시작했습니다: {names}" if not bad else f"일부를 재시작하지 못했습니다: {', '.join(bad)}") + (" · 모니터(8787) 응답 확인" if res["monitor"] else "")
        return res

    def botlab_mode(self, body: dict) -> dict:
        d = self._bot_dir()
        try:
            if str(body.get("mode")) == "paper":
                res = mode_mod.set_paper(d)
            elif str(body.get("mode")) == "live":
                res = mode_mod.set_live(d, str(body.get("confirm", "")), list(body.get("checks") or []), self.botlab.health(d), self._keys_check(), time.time())
            else:
                raise ApiError(400, "알 수 없는 모드입니다.")
        except mode_mod.ModeError as exc:
            raise ApiError(exc.status, exc.message + ((" — " + " / ".join(exc.blockers)) if exc.blockers else "")) from exc
        out = self.botlab_state()
        out["message"] = res["message"]
        return out

    def botlab_config(self, body: dict) -> dict:
        try:
            cfg = self.botlab.update(body)
        except botlab_mod.BotLabError as exc:
            raise ApiError(400, str(exc)) from exc
        if "bot_dir" in body and cfg["bot_dir"]:
            path = self.add_project(cfg["bot_dir"])
            if not policy_mod.path_of(Path(path), self.settings.state_dir).is_file():  # first time: the safe defaults
                policy_mod.save(Path(path), self.settings.state_dir, botlab_mod.DEFAULT_PROTECT, True)
        self._portfolio_cache = None
        return self.botlab_state()

    def botlab_policy(self, body: dict) -> dict:
        proj = self.botlab.config()["bot_dir"]
        if not proj or proj not in self._projects:
            raise ApiError(400, "먼저 봇 폴더를 지정하세요.")
        protect = body.get("protect")
        if not isinstance(protect, list) or len(protect) > 200:
            raise ApiError(400, "보호 경로 형식이 올바르지 않습니다.")
        try:
            policy_mod.save(Path(proj), self.settings.state_dir, [str(x) for x in protect], bool(body.get("shadow")))
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        return self.botlab_state()

    def botlab_run(self) -> dict:
        if not self.botlab.config()["bot_dir"]:
            raise ApiError(400, "먼저 봇 폴더를 지정하세요.")
        if self.botlab.running:
            raise ApiError(409, "평가가 이미 진행 중입니다.")
        threading.Thread(target=self._botlab_eval_safe, daemon=True).start()
        return {"ok": True, "message": "평가를 시작했습니다. 가격을 받는 데 1~2분 걸릴 수 있습니다."}

    def _botlab_eval_safe(self) -> None:
        try:
            self.botlab.evaluate()
        except botlab_mod.BotLabError as exc:
            self.botlab.last_message = str(exc)

    def botlab_tick(self) -> None:
        """One scheduler beat (every minute): evaluate when due, and start the periodic 3-AI analysis when due and nothing else runs."""
        lab = self.botlab
        if lab.due_eval():
            self._botlab_eval_safe()
        if lab.due_analyze() and not (self.job and self.job["popen"].poll() is None):
            hist = lab.history(1)
            try:
                self.start_analysis(lab.config()["bot_dir"], lab.analysis_focus(hist[-1] if hist else None))
                lab.mark_analyze()
            except ApiError as exc:
                if exc.status != 409:  # busy: try again next minute; anything else: record and wait for the next period
                    lab.mark_analyze(exc.message)

    def start_alert_watcher(self) -> None:
        """A light thread of its own (the evaluation thread can be busy for minutes): looks for new bot events every 20 seconds."""
        def loop() -> None:
            while not self._scheduler_stop.wait(20):
                try:
                    d = self.botlab.config()["bot_dir"]
                    if d:
                        self.alerts.tick(d)
                except Exception:
                    pass  # an alert problem must never take the dashboard down

        threading.Thread(target=loop, daemon=True, name="alert-watcher").start()

    def start_scheduler(self) -> None:
        def loop() -> None:
            while not self._scheduler_stop.wait(60):
                try:
                    self.botlab_tick()
                except Exception:
                    pass  # the scheduler must never die

        threading.Thread(target=loop, daemon=True, name="botlab-scheduler").start()

    # ------------------------------------------------------------- commands
    def _role_args(self, roles: Optional[dict]) -> list[str]:
        """Validate the roles chosen on the page and turn them into `solomon run` flags."""
        if not roles:
            return []
        if not isinstance(roles, dict):
            raise ApiError(400, "역할 배정 형식이 올바르지 않습니다.")
        args: list[str] = []
        effective = dict(self.settings.roles)
        for role in ALL_ROLES:
            name = roles.get(role)
            if name is None:
                continue
            if name == "":
                if role in ROLES:
                    raise ApiError(400, f"{role} 역할에는 AI가 하나 필요합니다.")
                effective.pop(role, None)  # an optional slot cleared on the page
                continue
            if name not in PROVIDER_CLASSES:
                raise ApiError(400, f"알 수 없는 AI입니다: {name}")
            effective[role] = name
            args += [f"--{role}", name]
        if not PROVIDER_CLASSES[effective["builder"]].supports_write:
            raise ApiError(400, f"{effective['builder']}은(는) 구현을 맡을 수 없습니다 (계획·검수만 가능). 구현은 다른 AI로 고르세요.")
        if effective["builder"] == effective["reviewer"] and not self.settings.allow_same_provider:
            raise ApiError(400, "구현과 검수는 서로 다른 AI여야 합니다 (같은 AI가 자기 코드를 검수하면 교차 검증이 되지 않습니다).")
        if effective.get("arbiter") and effective["arbiter"] == effective["builder"] and not self.settings.allow_same_provider:
            raise ApiError(400, "결정권자는 구현 AI와 달라야 합니다 (자기 코드를 스스로 채택하지 않도록).")
        reviewers = [effective[r] for r in ("reviewer", "reviewer2", "reviewer3") if effective.get(r)]
        if effective["builder"] in reviewers and not self.settings.allow_same_provider:
            raise ApiError(400, "검수 AI는 구현 AI와 달라야 합니다 (1차·2차·3차 모두).")
        for r in ("builder2", "builder3"):
            w = effective.get(r)
            if w and PROVIDER_CLASSES[w].supports_write and reviewers and all(x == w for x in reviewers) and not self.settings.allow_same_provider:
                raise ApiError(400, f"구현 보조 {w}가 구현할 차례에는 검수할 다른 AI가 없습니다. 검수 AI를 하나 더 지정하세요.")
        return args

    def start_run(self, goal: str, project: str, roles: Optional[dict] = None, persist: bool = False, fresh: bool = False, shadow: bool = False,
                  images: Optional[list] = None) -> dict:
        goal = (goal or "").strip()
        if not goal:
            raise ApiError(400, "목표를 입력하세요.")
        if len(goal) > MAX_GOAL:
            raise ApiError(400, f"목표가 너무 깁니다 ({MAX_GOAL}자 이하).")
        role_args = self._role_args(roles)
        self._ensure_idle()  # before anything is stored or added
        try:
            pictures = attach_mod.decode(images)  # validated before anything is started
        except attach_mod.AttachError as exc:
            raise ApiError(exc.status, exc.message) from exc
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
        for pic in attach_mod.store(pictures):
            args += ["--image", str(pic)]
        if shadow:
            args += ["--shadow"]
        if persist:
            args += ["--persist", "--max-minutes", str(PERSIST_MINUTES), "--max-attempts", str(PERSIST_ATTEMPTS)]
        if fresh:
            args += ["--no-baseline"]  # a brand-new project has nothing to be 'healthy' yet
        cfg = self._projects[project]
        if "test_command" in cfg:
            args += ["--test-command", shlex.join(cfg["test_command"])] if cfg["test_command"] else ["--no-tests"]
        return self._spawn("run", args, project, goal)

    def start_analysis(self, project: str, focus: str = "", ais: Optional[list] = None) -> dict:
        self._ensure_idle()
        focus = (focus or "").strip()
        if len(focus) > MAX_GOAL:
            raise ApiError(400, f"분석 요청이 너무 깁니다 ({MAX_GOAL}자 이하).")
        installed = [p["name"] for p in self.providers_info() if p["installed"]]
        defaults = [p["name"] for p in self.providers_info() if p["installed"] and p.get("default", True)]
        if ais is not None and not isinstance(ais, list):
            raise ApiError(400, "AI 선택 형식이 올바르지 않습니다.")
        chosen = [str(a) for a in (ais or defaults)]
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

    def start_autofix(self, project: str, opts: Optional[dict] = None) -> dict:
        self._ensure_idle()
        opts = opts or {}
        if (project or "") not in self._projects:
            try:
                project = self.add_project(project or "")
            except gitops.GitError as exc:
                raise ApiError(400, str(exc)) from exc
        if not self.is_git(project):
            raise ApiError(400, "자동 수정에는 git 저장소가 필요합니다 (커밋이 1개 이상).")
        sev = str(opts.get("min_severity", "medium"))
        if sev not in ("low", "medium", "high"):
            raise ApiError(400, "심각도 값이 올바르지 않습니다.")
        try:
            fixes = int(opts.get("max_fixes", 5))
            hours = float(opts.get("budget_hours", 6))
        except (TypeError, ValueError):
            raise ApiError(400, "숫자 값이 올바르지 않습니다.") from None
        if not (0 <= fixes <= 100) or not (0.25 <= hours <= 24):
            raise ApiError(400, "수정 개수는 0(전부)~100, 시간은 0.25~24시간이어야 합니다.")
        ids = opts.get("ids")
        if ids is not None:
            if not isinstance(ids, list) or not ids or len(ids) > 300 or not all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in ids):
                raise ApiError(400, "고칠 항목 번호 목록이 올바르지 않습니다.")
        analysis_id = opts.get("analysis_id")
        if analysis_id is not None and not re.match(r"^[0-9a-zA-Z-]{1,60}$", str(analysis_id)):
            raise ApiError(400, "분석 번호가 올바르지 않습니다.")
        cfg = self._projects[project]
        cmd, _ = self.effective_test_command(project)
        if not cmd:
            raise ApiError(400, "테스트 명령이 없으면 밤새 자동 수정을 하지 않습니다. 프로젝트 설정에서 테스트 명령을 정하세요.")
        args = [sys.executable, "-m", "solomon", "autofix", "--project", project, "--min-severity", sev,
                "--max-fixes", str(fixes), "--budget-minutes", str(int(hours * 60)), "--test-command", shlex.join(cmd)]
        if self.config_path:
            args += ["--config", self.config_path]
        if ids:
            args += ["--ids", ",".join(str(i) for i in ids)]
        if analysis_id:
            args += ["--analysis", str(analysis_id)]
        if opts.get("group"):
            args += ["--group-by-file"]
        args += self._role_args(opts.get("roles"))
        label = f"선택한 {len(ids)}건 순서대로 자동 수정" if ids else f"밤샘 자동 수정 ({sev} 이상 · {'전부' if fixes == 0 else '최대 ' + str(fixes) + '건'})"
        return self._spawn("autofix", args, project, label + (" · 파일별 묶음" if opts.get("group") else ""))

    def _busy_here(self, project: str) -> bool:
        return bool(self.job and self.job["popen"].poll() is None and self.job.get("project") == project)

    def act(self, project: str, key: str, action: str, message: str = "") -> dict:
        self._portfolio_cache = None
        project = self._known(project)
        try:
            return manage.perform(Path(project), self.settings.state_dir, key, action, message, self._busy_here(project))
        except manage.ManageError as exc:
            raise ApiError(exc.status, exc.message) from exc
        except gitops.GitError as exc:
            raise ApiError(409, str(exc)) from exc

    def inbox(self, project: str) -> dict:
        project = self._known(project)
        path = Path(project)
        try:
            if self.is_git(project):
                data = manage.list_inbox(path, self.settings.state_dir)
                cmd, _ = self.effective_test_command(project)
                data["test_command"] = " ".join(cmd) if cmd else ""
            else:
                data = {"entries": [], "dirty": [], "branch": "", "pending": 0, "not_git": True}
            data["history"] = manage.list_history(path, self.settings.state_dir)
        except Exception as exc:
            raise ApiError(500, f"결과함을 읽지 못했습니다: {type(exc).__name__}: {exc}") from exc
        return data

    def describe(self, project: str, key: str) -> dict:
        project = self._known(project)
        try:
            return manage.describe(Path(project), self.settings.state_dir, key)
        except manage.ManageError as exc:
            raise ApiError(exc.status, exc.message) from exc
        except Exception as exc:  # a broken record must show an error, not drop the connection
            raise ApiError(500, f"기록을 읽지 못했습니다: {type(exc).__name__}: {exc}") from exc

    def diff(self, project: str, key: str) -> dict:
        project = self._known(project)
        try:
            return manage.read_diff(Path(project), self.settings.state_dir, key)
        except manage.ManageError as exc:
            raise ApiError(exc.status, exc.message) from exc

    def start_tests(self, project: str) -> dict:
        project = self._known(project)
        cmd, _ = self.effective_test_command(project)
        if not cmd:
            raise ApiError(400, "이 프로젝트에는 테스트 명령이 없습니다. 명령 카드의 '프로젝트 설정: 테스트 명령'에서 지정하세요.")
        args = [sys.executable, "-m", "solomon", "runtests", "--project", project, "--test-command", shlex.join(cmd)]
        if self.config_path:
            args += ["--config", self.config_path]
        return self._spawn("tests", args, project, "작업 폴더 테스트")

    def apply_batch(self, project: str, batch_id: str) -> dict:
        return self.act(project, f"batch:{batch_id}", "apply")

    def _check_ais(self, ais: Optional[list]) -> list[str]:
        installed = [p["name"] for p in self.providers_info() if p["installed"]]
        defaults = [p["name"] for p in self.providers_info() if p["installed"] and p.get("default", True)]
        if ais is not None and not isinstance(ais, list):
            raise ApiError(400, "AI 선택 형식이 올바르지 않습니다.")
        chosen = [str(a) for a in (ais or defaults)]
        bad = [a for a in chosen if a not in PROVIDER_CLASSES]
        if bad:
            raise ApiError(400, f"알 수 없는 AI입니다: {', '.join(bad)}")
        missing = [a for a in chosen if a not in installed]
        if missing:
            raise ApiError(400, f"설치되지 않은 AI입니다: {', '.join(missing)}")
        if not chosen:
            raise ApiError(400, "사용할 수 있는 AI가 설치되어 있지 않습니다.")
        return chosen

    def _project_for(self, project: str) -> str:
        if (project or "") not in self._projects:
            try:
                return self.add_project(project or "")
            except gitops.GitError as exc:
                raise ApiError(400, str(exc)) from exc
        return project

    def start_video(self, project: str, body: dict) -> dict:
        url = str(body.get("url", "")).strip()
        try:
            video_mod.parse_video_url(url)
        except video_mod.VideoError as exc:
            raise ApiError(400, str(exc)) from exc
        focus = str(body.get("focus", "")).strip()
        if len(focus) > MAX_GOAL:
            raise ApiError(400, f"분석 요청이 너무 깁니다 ({MAX_GOAL}자 이하).")
        chosen = self._check_ais(body.get("ais"))
        direct = body.get("direct") or []
        if not isinstance(direct, list) or any(d not in chosen for d in direct):
            raise ApiError(400, "직접 시청 AI는 분석에 참여하는 AI 중에서 고르세요.")
        text = str(body.get("transcript", ""))
        if len(text) > 400_000:
            raise ApiError(400, "붙여넣은 자막이 너무 깁니다 (40만 자 이하).")
        project = self._project_for(project)
        args = [sys.executable, "-m", "solomon", "video", url, "--project", project, "--ais", ",".join(chosen)]
        if focus:
            args += ["--focus", focus]
        if direct:
            args += ["--direct", ",".join(direct)]
        if body.get("context"):
            args += ["--context"]
        if text.strip():
            f = Path(project) / self.settings.state_dir / f"video_input-{uuid.uuid4().hex[:10]}.txt"  # long text does not go through argv
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
            args += ["--transcript-file", str(f)]
        if self.config_path:
            args += ["--config", self.config_path]
        return self._spawn("video", args, project, "영상 분석: " + (focus or url))

    def start_report(self, project: str, body: dict) -> dict:
        question = str(body.get("question", "")).strip()
        if not question:
            raise ApiError(400, "질문을 입력하세요.")
        if len(question) > report_mod.MAX_QUESTION:
            raise ApiError(400, f"질문이 너무 깁니다 ({report_mod.MAX_QUESTION}자 이하).")
        chosen = self._check_ais(body.get("ais"))
        merger = str(body.get("merger") or "")
        if merger:
            self._check_ais([merger])
        self._ensure_idle()
        project = self._project_for(project)
        args = [sys.executable, "-m", "solomon", "report", question, "--project", project, "--ais", ",".join(chosen)]
        if merger:
            args += ["--merger", merger]
        if body.get("context"):
            args += ["--context"]
        if self.config_path:
            args += ["--config", self.config_path]
        return self._spawn("report", args, project, "보고서: " + question[:40])

    def list_reports(self, project: str) -> dict:
        project = self._known(project)
        return {"project": project, "reports": report_mod.list_reports(Path(project), self.settings.state_dir)}

    def get_report(self, project: str, rid: str) -> dict:
        project = self._known(project)
        try:
            view = report_mod.load_report(Path(project), self.settings.state_dir, rid)
        except report_mod.ReportError as exc:
            raise ApiError(400, str(exc)) from exc
        if view is None:
            raise ApiError(404, "보고서를 찾을 수 없습니다.")
        from . import mdhtml

        view["html"] = mdhtml.render(view["markdown"])[0] if view.get("markdown") else ""
        tasks = report_mod.load_tasks(Path(project), self.settings.state_dir, rid)
        if tasks:
            target = tasks.get("target") or project
            stat = report_mod.task_statuses(Path(project), self.settings.state_dir, rid, tasks["tasks"], target)
            tasks = dict(tasks, target=target, tasks=[dict(t, **stat[t["id"]]) for t in tasks["tasks"]])
        view["tasks"] = tasks
        view["drafts_html"] = {n: mdhtml.render(t)[0] for n, t in view.get("drafts", {}).items()}
        return view

    def start_report_tasks(self, project: str, body: dict) -> dict:
        project = self._known(project)
        rid = str(body.get("id", ""))
        view = None
        try:
            view = report_mod.load_report(Path(project), self.settings.state_dir, rid)
        except report_mod.ReportError as exc:
            raise ApiError(400, str(exc)) from exc
        if view is None or not view.get("markdown"):
            raise ApiError(400, "나눌 보고서가 없습니다 (아직 작성 중이거나 실패한 보고서입니다).")
        ai = body.get("ai")
        if ai:
            self._check_ais([str(ai)])
        self._ensure_idle()
        args = [sys.executable, "-m", "solomon", "report-tasks", "--project", project, "--id", rid]
        if ai:
            args += ["--ai", str(ai)]
        if self.config_path:
            args += ["--config", self.config_path]
        return self._spawn("reporttasks", args, project, "보고서를 작은 작업으로 나누는 중")

    def start_report_chain(self, project: str, body: dict) -> dict:
        """Build the tasks of a report one after another, unattended, on a new branch (the same machinery as the overnight auto-fix)."""
        project = self._known(project)
        rid = str(body.get("id", ""))
        try:
            data = report_mod.load_tasks(Path(project), self.settings.state_dir, rid)
        except report_mod.ReportError as exc:
            raise ApiError(400, str(exc)) from exc
        if not data:
            raise ApiError(400, "작업 목록이 없습니다. 먼저 '작은 작업으로 나누기'를 하세요.")
        ids = body.get("task_ids")
        if ids is not None:
            if not isinstance(ids, list) or not ids or len(ids) > 20 or not all(isinstance(i, int) and not isinstance(i, bool) and i > 0 for i in ids):
                raise ApiError(400, "실행할 단계 번호 목록이 올바르지 않습니다.")
        try:
            hours = float(body.get("budget_hours", 6))
        except (TypeError, ValueError):
            raise ApiError(400, "시간 값이 올바르지 않습니다.") from None
        if not 0.25 <= hours <= 24:
            raise ApiError(400, "제한 시간은 0.25~24시간이어야 합니다.")
        target = self._task_target(project, rid)
        if not self.is_git(target):
            raise ApiError(400, "자동 실행에는 git 저장소가 필요합니다 (커밋이 1개 이상).")
        cmd, _ = self.effective_test_command(target)
        if not cmd:
            raise ApiError(400, "테스트 명령이 없으면 자동 실행을 하지 않습니다. 프로젝트 설정에서 테스트 명령을 정하세요.")
        role_args = self._role_args(body.get("roles"))
        self._ensure_idle()
        args = [sys.executable, "-m", "solomon", "report-chain", "--project", target, "--id", rid, "--budget-minutes", str(int(hours * 60)),
                "--test-command", shlex.join(cmd)]
        if target != project:
            args += ["--report-project", project]
        if ids:
            args += ["--tasks", ",".join(str(i) for i in ids)]
        if self.config_path:
            args += ["--config", self.config_path]
        args += role_args
        return self._spawn("autofix", args, target, f"보고서 순차 실행 ({len(ids) if ids else len(data['tasks'])}단계)")

    def _task_target(self, project: str, rid: str) -> str:
        """The project a report's tasks are built in: the one chosen for it, else the project the report is stored in. Must still be registered."""
        data = report_mod.load_tasks(Path(project), self.settings.state_dir, rid)
        target = (data or {}).get("target") or project
        if target not in self._projects:
            raise ApiError(400, "작업을 실행할 프로젝트가 등록되어 있지 않습니다. 대상 프로젝트를 다시 지정하세요.")
        return target

    def set_report_target(self, project: str, body: dict) -> dict:
        project = self._known(project)
        target = self._known(str(body.get("target", "")))
        try:
            report_mod.set_task_target(Path(project), self.settings.state_dir, str(body.get("id", "")), target)
        except report_mod.ReportError as exc:
            raise ApiError(400, str(exc)) from exc
        return {"ok": True, "target": target, "message": f"작업을 실행할 프로젝트: {target}"}

    def edit_report_task(self, project: str, body: dict) -> dict:
        project = self._known(project)
        try:
            tid = int(body.get("task_id"))
        except (TypeError, ValueError):
            raise ApiError(400, "작업 번호가 올바르지 않습니다.") from None
        try:
            report_mod.save_task_goal(Path(project), self.settings.state_dir, str(body.get("id", "")), tid, str(body.get("goal", "")))
        except report_mod.ReportError as exc:
            raise ApiError(400, str(exc)) from exc
        return {"ok": True, "message": "목표를 저장했습니다."}

    def run_report_task(self, project: str, body: dict) -> dict:
        project = self._known(project)
        rid = str(body.get("id", ""))
        try:
            tid = int(body.get("task_id"))
        except (TypeError, ValueError):
            raise ApiError(400, "작업 번호가 올바르지 않습니다.") from None
        try:
            goal = report_mod.task_goal_text(Path(project), self.settings.state_dir, rid, tid)
        except report_mod.ReportError as exc:
            raise ApiError(400, str(exc)) from exc
        target = self._task_target(project, rid)
        return self.start_run(goal, target, body.get("roles"), bool(body.get("persist")), shadow=bool(body.get("shadow")))

    def delete_report(self, project: str, rid: str) -> dict:
        project = self._known(project)
        if self.job and self.job["popen"].poll() is None and self.job.get("kind") == "report" and self.job.get("project") == project:
            view = report_mod.load_report(Path(project), self.settings.state_dir, rid) if report_mod.REPORT_ID.match(rid or "") else None
            if view is not None and not view.get("finished"):
                raise ApiError(409, "작성 중인 보고서는 지울 수 없습니다. 끝나거나 중지한 뒤 지우세요.")
        try:
            report_mod.delete_report(Path(project), self.settings.state_dir, rid)
        except report_mod.ReportError as exc:
            raise ApiError(404 if "찾을 수" in str(exc) else 400, str(exc)) from exc
        return {"ok": True, "message": "보고서를 지웠습니다."}

    def export_report(self, project: str, rid: str, folder: str) -> dict:
        project = self._known(project)
        try:
            target = report_mod.export_report(Path(project), self.settings.state_dir, rid, folder)
        except report_mod.ReportError as exc:
            raise ApiError(400, str(exc)) from exc
        return {"ok": True, "path": str(target), "message": f"저장했습니다: {target}"}

    def start_video_probe(self, project: str, body: dict) -> dict:
        url = str(body.get("url", "")).strip()
        try:
            video_mod.parse_video_url(url)
        except video_mod.VideoError as exc:
            raise ApiError(400, str(exc)) from exc
        chosen = self._check_ais(body.get("ais"))
        project = self._project_for(project)
        args = [sys.executable, "-m", "solomon", "video", url, "--probe", "--ais", ",".join(chosen)]
        if self.config_path:
            args += ["--config", self.config_path]
        return self._spawn("videoprobe", args, project, "AI별 영상 직접 시청 시험")

    def _ensure_idle(self) -> None:
        if self.job and self.job["popen"].poll() is None:
            raise ApiError(409, "이미 실행 중인 작업이 있습니다. 끝나거나 중지한 뒤 다시 시도하세요.")
        if joblock.is_busy():  # a job this dashboard does not know: started from a terminal or by an earlier dashboard
            raise ApiError(409, joblock.BUSY)

    def _spawn(self, kind: str, args: list[str], project: str, label: str) -> dict:
        self._portfolio_cache = None
        with self._lock:
            self._ensure_idle()
            log_path = (Path(project) / self.settings.state_dir / "dashboard.log") if project else self.store_path.parent / "tool.log"
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
        return self.act(project, f"run:{run_id}", "apply")


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
                       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'")
                return self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8", {"Content-Security-Policy": csp})
            if url.path == "/guide":
                from .mdhtml import page
                try:
                    body = page((Path(__file__).parent / "USER_GUIDE.md").read_text(encoding="utf-8")).encode("utf-8")
                except OSError:
                    return self._json(404, {"error": "사용 가이드 파일이 없습니다."})
                csp = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
                return self._send(200, body, "text/html; charset=utf-8", {"Content-Security-Policy": csp})
            if url.path == "/api/state":
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다. 터미널에 표시된 주소로 여세요."})
                state = app.build_state((query.get("project") or [None])[0], (query.get("run") or [None])[0],
                                        (query.get("analysis") or [None])[0], (query.get("video") or [None])[0])
                return self._json(200, state)
            if url.path in ("/api/browse", "/api/tools", "/api/projects/git-init-preview"):
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                try:
                    if url.path == "/api/browse":
                        return self._json(200, app.browse((query.get("path") or [""])[0] or None))
                    if url.path == "/api/tools":
                        return self._json(200, app.tools_status())
                    return self._json(200, app.git_init_preview((query.get("path") or [""])[0]))
                except ApiError as exc:
                    return self._json(exc.status, {"error": exc.message})
            if url.path == "/api/portfolio":
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                return self._json(200, app.portfolio())
            if url.path == "/api/shot":
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                try:
                    q = lambda k: (query.get(k) or [""])[0]
                    f = app.shot_path(q("project"), q("run"), q("attempt"), q("name"))
                    return self._send(200, f.read_bytes(), "image/png")
                except ApiError as exc:
                    return self._json(exc.status, {"error": exc.message})
            if url.path == "/api/attach":
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                try:
                    q = lambda k: (query.get(k) or [""])[0]
                    f = app.attachment_path(q("project"), q("run"), q("name"))
                    ctype = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}[f.suffix.lstrip(".")]
                    return self._send(200, f.read_bytes(), ctype)
                except ApiError as exc:
                    return self._json(exc.status, {"error": exc.message})
            if url.path == "/api/projects/policy":
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                try:
                    return self._json(200, app.project_policy((query.get("path") or [""])[0]))
                except ApiError as exc:
                    return self._json(exc.status, {"error": exc.message})
            if url.path == "/api/botlab":
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                return self._json(200, app.botlab_state())
            if url.path in ("/api/reports", "/api/report"):
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                try:
                    proj = (query.get("project") or [""])[0]
                    if url.path == "/api/reports":
                        return self._json(200, app.list_reports(proj))
                    return self._json(200, app.get_report(proj, (query.get("id") or [""])[0]))
                except ApiError as exc:
                    return self._json(exc.status, {"error": exc.message})
            if url.path == "/api/shadow":
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                try:
                    from . import shadow as shadow_mod
                    proj = app._known((query.get("project") or [""])[0])
                    return self._json(200, shadow_mod.evaluate(Path(proj), app.settings.state_dir))
                except ApiError as exc:
                    return self._json(exc.status, {"error": exc.message})
            if url.path == "/api/projects":
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                return self._json(200, app.projects_overview())
            if url.path in ("/api/inbox", "/api/diff", "/api/describe"):
                if not self._token_ok(query):
                    return self._json(401, {"error": "토큰이 없습니다."})
                try:
                    project = (query.get("project") or [""])[0]
                    if url.path == "/api/inbox":
                        return self._json(200, app.inbox(project))
                    if url.path == "/api/describe":
                        return self._json(200, app.describe(project, (query.get("key") or [""])[0]))
                    return self._json(200, app.diff(project, (query.get("key") or [""])[0]))
                except ApiError as exc:
                    return self._json(exc.status, {"error": exc.message})
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
                    return self._json(200, {"job": app.start_run(str(body.get("goal", "")), str(body.get("project", "")), body.get("roles"), bool(body.get("persist")), shadow=bool(body.get("shadow")),
                                                      images=body.get("images"))})
                if path == "/api/analyze":
                    return self._json(200, {"job": app.start_analysis(str(body.get("project", "")), str(body.get("focus", "")), body.get("ais"))})
                if path == "/api/video":
                    return self._json(200, {"job": app.start_video(str(body.get("project", "")), body)})
                if path == "/api/report":
                    return self._json(200, {"job": app.start_report(str(body.get("project", "")), body)})
                if path == "/api/report/tasks":
                    return self._json(200, {"job": app.start_report_tasks(str(body.get("project", "")), body)})
                if path == "/api/report/target":
                    return self._json(200, app.set_report_target(str(body.get("project", "")), body))
                if path == "/api/report/chain":
                    return self._json(200, {"job": app.start_report_chain(str(body.get("project", "")), body)})
                if path == "/api/report/task-run":
                    return self._json(200, {"job": app.run_report_task(str(body.get("project", "")), body)})
                if path == "/api/report/task-edit":
                    return self._json(200, app.edit_report_task(str(body.get("project", "")), body))
                if path == "/api/report/delete":
                    return self._json(200, app.delete_report(str(body.get("project", "")), str(body.get("id", ""))))
                if path == "/api/report/export":
                    return self._json(200, app.export_report(str(body.get("project", "")), str(body.get("id", "")), str(body.get("folder", ""))))
                if path == "/api/video-probe":
                    return self._json(200, {"job": app.start_video_probe(str(body.get("project", "")), body)})
                if path == "/api/botlab/config":
                    return self._json(200, app.botlab_config(body))
                if path == "/api/botlab/keys":
                    return self._json(200, app.botlab_keys(body))
                if path == "/api/botlab/alerts":
                    return self._json(200, app.botlab_alerts(body))
                if path == "/api/botlab/restart":
                    return self._json(200, app.botlab_restart(body))
                if path == "/api/botlab/mode":
                    return self._json(200, app.botlab_mode(body))
                if path == "/api/botlab/policy":
                    return self._json(200, app.botlab_policy(body))
                if path == "/api/botlab/run":
                    return self._json(200, app.botlab_run())
                if path == "/api/autofix":
                    return self._json(200, {"job": app.start_autofix(str(body.get("project", "")), body)})
                if path == "/api/apply-batch":
                    return self._json(200, app.apply_batch(str(body.get("project", "")), str(body.get("batch_id", ""))))
                if path == "/api/inbox/action":
                    return self._json(200, app.act(str(body.get("project", "")), str(body.get("key", "")),
                                                   str(body.get("action", "")), str(body.get("message", ""))))
                if path == "/api/projects/create":
                    return self._json(200, app.create_project(body))
                if path == "/api/folders/mkdir":
                    return self._json(200, app.make_folder(str(body.get("parent", "")), str(body.get("name", ""))))
                if path == "/api/projects/policy":
                    return self._json(200, app.project_policy(str(body.get("path", "")), body))
                if path == "/api/projects/meta":
                    return self._json(200, app.project_meta(str(body.get("path", "")), str(body.get("alias", "")), str(body.get("memo", ""))))
                if path == "/api/projects/rename":
                    return self._json(200, app.rename_project(str(body.get("path", "")), str(body.get("name", ""))))
                if path == "/api/projects/git-init":
                    return self._json(200, app.git_init(str(body.get("path", ""))))
                if path == "/api/open":
                    return self._json(200, app.open_path(str(body.get("path", ""))))
                if path == "/api/tools/run":
                    return self._json(200, {"job": app.run_tool(str(body.get("tool", "")))})
                if path == "/api/tools/git-identity":
                    return self._json(200, app.set_git_identity(str(body.get("name", "")), str(body.get("email", ""))))
                if path == "/api/tools/quota-clear":
                    return self._json(200, app.clear_quota())
                if path == "/api/tools/ollama-alias":
                    return self._json(200, app.make_ollama_alias())
                if path == "/api/tools/restart":
                    return self._json(200, app.restart())
                if path == "/api/projects/remove":
                    return self._json(200, app.remove_project(str(body.get("path", ""))))
                if path == "/api/projects/delete":
                    return self._json(200, app.delete_project(str(body.get("path", "")), str(body.get("confirm", ""))))
                if path == "/api/projects/restore":
                    return self._json(200, app.restore_project(str(body.get("id", ""))))
                if path == "/api/projects/purge":
                    return self._json(200, app.purge_trash(str(body.get("id", "")), str(body.get("confirm", ""))))
                if path == "/api/tests":
                    return self._json(200, {"job": app.start_tests(str(body.get("project", "")))})
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


def _persistent_token() -> str:
    """The same access key across restarts (so the open browser tab reconnects by itself after 'restart'). Stored 0600 beside the other state."""
    path = Path(os.environ.get("SOLOMON_HOME", str(Path.home() / ".solomon"))) / "dashboard.token"
    try:
        tok = path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{24,}", tok):
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tok, encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        pass
    return tok


def serve(
    settings: Settings,
    projects: list[str],
    config_path: Optional[str] = None,
    port: int = 8765,
    open_browser: bool = True,
) -> int:
    token = _persistent_token()
    app = DashboardApp(settings, projects, config_path)
    app.start_scheduler()
    app.start_alert_watcher()
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
