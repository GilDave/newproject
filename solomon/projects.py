"""Creating, trashing and restoring project folders that Solomon itself made.

Rules, because this folder lives next to real projects (a live trading bot, for one):
- Only folders Solomon created (they carry .solomon/project.json) and that sit directly inside the projects root
  can be deleted from the dashboard. Any other project can only be removed from the list; its files are never touched.
- "Delete" moves the folder to the trash (~/.solomon/trash); it can be restored there. Only an explicit "empty" removes it for good.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from . import gitops

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
MARKER = Path(".solomon") / "project.json"

STACKS = {
    "python": {"label": "Python (pytest)", "test": ["python3", "-m", "pytest", "-q"]},
    "node": {"label": "Node.js (npm test)", "test": ["npm", "test"]},
    "blank": {"label": "빈 프로젝트 (테스트 없음)", "test": None},
}


class ProjectError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _gitignore(stack: str) -> str:
    base = [".env", ".env.*", "*.pem", "*.key", ".DS_Store"]
    extra = {"python": ["__pycache__/", ".venv/", ".pytest_cache/", "*.pyc"], "node": ["node_modules/"], "blank": []}[stack]
    return "\n".join(extra + base) + "\n"


def create_project(root: Path, name: str, stack: str = "python", description: str = "") -> dict:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise ProjectError(400, "프로젝트 이름은 영문·숫자·점·밑줄·하이픈만, 60자 이하로 입력하세요 (예: my-bot).")
    if stack not in STACKS:
        raise ProjectError(400, "알 수 없는 프로젝트 종류입니다.")
    root = Path(root).expanduser().resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise ProjectError(400, "프로젝트 위치가 올바르지 않습니다.")
    if path.exists():
        raise ProjectError(409, f"이미 있는 폴더입니다: {path}")
    root.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    try:
        (path / "README.md").write_text(f"# {name}\n\n{description.strip()}\n", encoding="utf-8")
        (path / ".gitignore").write_text(_gitignore(stack), encoding="utf-8")
        if stack == "python":
            (path / "tests").mkdir()
            (path / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")
        elif stack == "node":
            (path / "package.json").write_text(json.dumps({"name": name.lower(), "version": "0.1.0", "private": True,
                                                           "scripts": {"test": "node --test"}}, indent=2) + "\n", encoding="utf-8")
            (path / "tests").mkdir()
            (path / "tests" / "smoke.test.js").write_text(
                "const test = require('node:test');\nconst assert = require('node:assert');\n"
                "test('smoke', () => { assert.ok(true); });\n", encoding="utf-8")
        gitops.git(["init", "-q"], path)
        gitops.git(["symbolic-ref", "HEAD", "refs/heads/main"], path)
        gitops.git(["add", "-A"], path)
        gitops.git(["-c", "user.name=Solomon", "-c", "user.email=solomon@localhost", "commit", "-q", "-m",
                    "Initial scaffold by Solomon"], path)
        gitops.exclude_from_git(path, ".solomon")
        (path / ".solomon").mkdir(exist_ok=True)
        (path / MARKER).write_text(json.dumps({"created_by": "solomon", "stack": stack, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}),
                                   encoding="utf-8")
    except Exception:
        shutil.rmtree(path, ignore_errors=True)  # a half-made folder we just created ourselves
        raise
    return {"path": str(path), "name": name, "stack": stack, "test_command": STACKS[stack]["test"]}


def is_managed(path: Path, root: Path) -> bool:
    try:
        p, r = Path(path).resolve(), Path(root).expanduser().resolve()
        if p.parent != r:
            return False
        data = json.loads((p / MARKER).read_text(encoding="utf-8"))
        return data.get("created_by") == "solomon"
    except (OSError, ValueError):
        return False


# ------------------------------------------------------------------ trash
def _index(trash: Path) -> list[dict]:
    try:
        return json.loads((trash / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_index(trash: Path, rows: list[dict]) -> None:
    trash.mkdir(parents=True, exist_ok=True)
    (trash / "index.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def trash_project(path: Path, root: Path, trash: Path, confirm_name: str) -> dict:
    path = Path(path).resolve()
    if not is_managed(path, root):
        raise ProjectError(403, "Solomon이 만든 프로젝트만 대시보드에서 삭제할 수 있습니다. 다른 프로젝트는 '목록에서 제거'만 가능하며 파일은 그대로 둡니다.")
    if (confirm_name or "").strip() != path.name:
        raise ProjectError(400, f"확인을 위해 프로젝트 이름({path.name})을 정확히 입력하세요.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(trash) / f"{path.name}__{stamp}"
    Path(trash).mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    rows = _index(trash)
    row = {"id": dest.name, "name": path.name, "original": str(path), "deleted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    rows.append(row)
    _save_index(trash, rows)
    return row


def list_trash(trash: Path) -> list[dict]:
    rows = [r for r in _index(trash) if (Path(trash) / r["id"]).is_dir()]
    return sorted(rows, key=lambda r: r["deleted_at"], reverse=True)


def _find(trash: Path, entry_id: str) -> dict:
    row = next((r for r in _index(trash) if r["id"] == entry_id), None)
    if not row or not (Path(trash) / row["id"]).is_dir():
        raise ProjectError(404, "휴지통에 그 항목이 없습니다.")
    return row


def restore_project(trash: Path, entry_id: str, root: Path) -> Path:
    row = _find(trash, entry_id)
    target = Path(row["original"])
    if target.parent != Path(root).expanduser().resolve():
        raise ProjectError(400, "원래 위치가 프로젝트 폴더 안이 아니어서 복원하지 않습니다.")
    if target.exists():
        raise ProjectError(409, f"원래 자리에 같은 이름의 폴더가 있습니다: {target}")
    shutil.move(str(Path(trash) / row["id"]), str(target))
    _save_index(trash, [r for r in _index(trash) if r["id"] != entry_id])
    return target


def purge_trash(trash: Path, entry_id: str, confirm_name: str) -> None:
    row = _find(trash, entry_id)
    if (confirm_name or "").strip() != row["name"]:
        raise ProjectError(400, f"영구 삭제하려면 프로젝트 이름({row['name']})을 정확히 입력하세요. 복원할 수 없게 됩니다.")
    shutil.rmtree(Path(trash) / row["id"])
    _save_index(trash, [r for r in _index(trash) if r["id"] != entry_id])
