"""GitHub hookup from the dashboard: save a login, create a private repository, push the current branch.

Credentials:
- The access token is NEVER written to a Solomon file, a log, a remote URL or an error message. It is handed to git's own
  credential helper (macOS Keychain on a Mac) with `git credential approve`, exactly as `git push` would store it.
  Solomon only keeps the user name (no secret) in ~/.solomon/github.json so the page can say who is logged in.
- To call the GitHub API (create a repository) the token is read back from the helper with `git credential fill`, used for
  that one request, and dropped.
Safety of pushing: only the current branch to the remote called `origin`, never `--force`, never when a secret-looking
file is tracked, and only after the page showed the commits and the user confirmed.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import gitops, gitwatch

API = "https://api.github.com"
HOST = "github.com"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,255}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
PUSH_TIMEOUT = 120.0
HTTP_TIMEOUT = 15.0


class GithubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _home() -> Path:
    return Path(os.environ.get("SOLOMON_HOME", str(Path.home() / ".solomon")))


def _marker() -> Path:
    return _home() / "github.json"


def _env() -> dict:
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    return env


def _scrub(text: str, secret: str = "") -> str:
    text = text or ""
    if secret:
        text = text.replace(secret, "***")
    return re.sub(r"(gh[pousr]_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,})", "***", text)


def _cred(action: str, fields: dict, timeout: float = 20.0) -> subprocess.CompletedProcess:
    data = "".join(f"{k}={v}\n" for k, v in fields.items()) + "\n"
    return subprocess.run(["git", "credential", action], input=data, capture_output=True, text=True, timeout=timeout, env=_env())


def helper() -> str:
    """The configured credential helper ('' when there is none)."""
    out = gitops.git(["config", "--global", "--get-all", "credential.helper"], ".", check=False).stdout
    sysout = gitops.git(["config", "--system", "--get-all", "credential.helper"], ".", check=False).stdout
    return ((out or "") + (sysout or "")).strip()


def saved_login() -> str:
    try:
        return str(json.loads(_marker().read_text(encoding="utf-8")).get("login", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def status() -> dict:
    from . import folders
    return {"identity": folders.git_identity(), "login": saved_login(), "helper": helper(), "platform": platform.system()}


def _request(method: str, path: str, token: str, body: Optional[dict] = None) -> tuple[int, dict, dict]:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "Solomon")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except (ValueError, OSError):
            payload = {}
        return exc.code, payload if isinstance(payload, dict) else {}, dict(exc.headers or {})
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise GithubError(502, "GitHub에 연결하지 못했습니다. 인터넷 연결을 확인하세요.") from exc


def save_login(token: str) -> dict:
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise GithubError(400, "토큰 형식이 올바르지 않습니다. GitHub에서 만든 토큰 전체를 붙여넣으세요(공백·줄바꿈 없이).")
    code, data, headers = _request("GET", "/user", token)
    if code == 401:
        raise GithubError(401, "GitHub가 이 토큰을 받아주지 않았습니다(만료되었거나 잘못 복사됨).")
    if code != 200 or not data.get("login"):
        raise GithubError(502, f"GitHub 확인에 실패했습니다(응답 {code}).")
    login = str(data["login"])
    if not helper():
        if platform.system() == "Darwin":
            gitops.git(["config", "--global", "credential.helper", "osxkeychain"], ".")          # 맥 기본 키체인
        else:
            raise GithubError(501, "이 컴퓨터에는 git 자격 증명 저장소가 설정돼 있지 않아 토큰을 안전하게 저장할 수 없습니다. "
                                   "터미널에서 `gh auth login` 을 쓰거나 자격 증명 도우미를 설정하세요.")
    proc = _cred("approve", {"protocol": "https", "host": HOST, "username": login, "password": token})
    if proc.returncode != 0:
        raise GithubError(500, "토큰을 자격 증명 저장소에 저장하지 못했습니다: " + _scrub(proc.stderr.strip()[:160], token))
    _home().mkdir(parents=True, exist_ok=True)
    _marker().write_text(json.dumps({"login": login, "saved": int(time.time())}), encoding="utf-8")
    try:
        os.chmod(_marker(), 0o600)
    except OSError:
        pass
    scopes = headers.get("X-OAuth-Scopes") or headers.get("x-oauth-scopes") or ""
    return {"ok": True, "login": login, "scopes": scopes, "message": f"GitHub 계정 {login} 로 로그인했습니다. 토큰은 Solomon이 아니라 컴퓨터의 자격 증명 저장소에 저장됩니다."}


def logout() -> dict:
    login = saved_login()
    if login:
        _cred("reject", {"protocol": "https", "host": HOST, "username": login})
    try:
        _marker().unlink()
    except OSError:
        pass
    return {"ok": True, "message": "GitHub 로그인을 해제했습니다(저장된 토큰을 지웠습니다). GitHub 사이트의 토큰도 만료·삭제하면 더 안전합니다."}


def _token() -> tuple[str, str]:
    login = saved_login()
    if not login:
        raise GithubError(401, "먼저 GitHub에 로그인(토큰 저장)하세요.")
    proc = _cred("fill", {"protocol": "https", "host": HOST, "username": login})
    pw = re.search(r"^password=(.+)$", proc.stdout or "", re.M)
    if proc.returncode != 0 or not pw:
        raise GithubError(401, "저장된 GitHub 토큰을 찾지 못했습니다. 다시 로그인하세요.")
    return login, pw.group(1).strip()


def create_repo(path, name: str, description: str = "") -> dict:
    """Create a PRIVATE repository under the logged-in account and set it as `origin`. Never public."""
    p = Path(path)
    name, description = (name or "").strip(), (description or "").strip()
    if not REPO_RE.match(name) or name.endswith(".git"):
        raise GithubError(400, "저장소 이름은 영문·숫자·점·밑줄·하이픈만, 100자 이하로 입력하세요.")
    if len(description) > 200 or re.search(r"[\x00-\x1f]", description):
        raise GithubError(400, "설명은 200자 이하의 한 줄로 입력하세요.")
    if any(r["name"] == "origin" for r in gitwatch._remotes(p)):
        raise GithubError(409, "이 프로젝트에는 이미 origin 원격이 있습니다. 새로 만들지 않았습니다.")
    login, token = _token()
    try:
        code, data, _ = _request("POST", "/user/repos", token, {"name": name, "description": description, "private": True, "auto_init": False})
    except GithubError:
        raise
    if code == 422:
        raise GithubError(409, "이미 같은 이름의 저장소가 이 계정에 있습니다. 다른 이름을 쓰세요.")
    if code in (401, 403):
        raise GithubError(403, "GitHub가 저장소 만들기를 허용하지 않았습니다. 토큰에 'repo' 권한(클래식) 또는 저장소 관리 권한이 있어야 합니다.")
    if code not in (200, 201) or not data.get("clone_url"):
        raise GithubError(502, f"저장소를 만들지 못했습니다(응답 {code}).")
    url = str(data["clone_url"])
    res = gitwatch.set_remote(p, "origin", url)
    return {"ok": True, "url": url, "html_url": data.get("html_url", ""), "private": bool(data.get("private", True)),
            "message": f"GitHub에 비공개 저장소를 만들고 origin 으로 연결했습니다: {url}. 이제 '올리기'로 코드를 올리세요."}


def push_preview(path) -> dict:
    """What a push would send, without sending anything."""
    p = Path(path)
    info = gitwatch.inspect(p)
    out = {"state": info.get("state"), "branch": info.get("branch"), "blockers": [], "commits": [], "count": 0, "remote": ""}
    if info.get("state") != "ready":
        out["blockers"].append("커밋이 있는 git 저장소가 아닙니다.")
        return out
    origin = next((r for r in info.get("remotes", []) if r["name"] == "origin"), None)
    if not origin:
        out["blockers"].append("origin 원격이 없습니다. 먼저 저장소를 만들거나 원격 주소를 설정하세요.")
    else:
        out["remote"] = origin["push"] or origin["fetch"]
    if info.get("detached"):
        out["blockers"].append("특정 커밋을 보고 있는 상태(detached HEAD)라 올릴 수 없습니다. 브랜치로 돌아간 뒤 하세요.")
    if info.get("conflicts"):
        out["blockers"].append("충돌 중인 파일이 있습니다.")
    if info.get("tracked_secrets"):
        out["blockers"].append("비밀이 들어 있을 수 있는 파일이 git에 추적되고 있습니다: " + ", ".join(info["tracked_secrets"][:3])
                               + " — 올리면 GitHub에 그대로 공개 기록이 남습니다. 추적을 끊고 키를 새로 발급한 뒤 올리세요.")
    if info.get("behind"):
        out["blockers"].append(f"원격에 내가 받지 않은 커밋이 {info['behind']}개 있어 그냥 올리면 거절됩니다. 먼저 받아 합친 뒤 올리세요(터미널: git pull --rebase).")
    if info.get("upstream") and info.get("ahead") == 0:
        out["blockers"].append("올릴 새 커밋이 없습니다(이미 원격과 같음).")
    rng = "@{u}..HEAD" if info.get("upstream") else "HEAD"
    log = gitwatch._git(["log", "--format=%h\x1f%s", "-20", rng], p) or ""
    out["commits"] = [{"hash": a, "subject": b[:100]} for a, b in (line.split("\x1f", 1) for line in log.splitlines() if "\x1f" in line)]
    total = (gitwatch._git(["rev-list", "--count", rng], p) or "").strip()
    out["count"] = int(total) if total.isdigit() else len(out["commits"])
    out["first_push"] = not info.get("upstream")
    return out


def push(path, confirm: bool) -> dict:
    if confirm is not True:
        raise GithubError(400, "확인 없이는 올리지 않습니다. 미리보기를 확인한 뒤 '올리기'를 누르세요.")
    p = Path(path)
    pre = push_preview(p)
    if pre["blockers"]:
        raise GithubError(409, pre["blockers"][0])
    branch = pre["branch"]
    try:
        proc = gitops.git(["push", "-u", "origin", branch], p, check=False, timeout=PUSH_TIMEOUT, env=_env())
    except Exception as exc:
        raise GithubError(504, f"올리는 중 시간이 초과되었거나 실패했습니다: {type(exc).__name__}") from exc
    if proc.returncode != 0:
        msg = _scrub((proc.stderr or proc.stdout).strip().splitlines()[-1] if (proc.stderr or proc.stdout).strip() else "알 수 없는 오류")
        hint = " (GitHub 로그인이 안 되어 있으면 위의 'GitHub 로그인'을 먼저 하세요)" if re.search(r"authent|denied|403|401|could not read", msg, re.I) else ""
        raise GithubError(502, "올리지 못했습니다: " + msg + hint)
    return {"ok": True, "message": f"브랜치 {branch} 의 커밋 {pre['count']}개를 origin 에 올렸습니다."}
