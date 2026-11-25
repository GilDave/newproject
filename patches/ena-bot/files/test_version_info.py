import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ena_bot import version_info as vi


def sh(cwd, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@x", *args], cwd=cwd, check=True, capture_output=True)


def repo(path, files):
    path.mkdir(parents=True, exist_ok=True)
    sh(path, "init", "-q", "-b", "main")
    for n, c in files.items():
        (path / n).parent.mkdir(parents=True, exist_ok=True)
        (path / n).write_text(c)
    sh(path, "add", "-A")
    sh(path, "commit", "-qm", "first")
    return path


def test_app_version_sources(tmp_path):
    a = tmp_path / "a"; a.mkdir(); (a / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    b = tmp_path / "b"; b.mkdir(); (b / "package.json").write_text('{"version":"4.5.6"}')
    c = tmp_path / "c"; c.mkdir(); (c / "VERSION").write_text("7.8.9\n")
    d = tmp_path / "d"; (d / "ena_bot").mkdir(parents=True); (d / "ena_bot" / "__init__.py").write_text('__version__ = "0.3.1"\n')
    e = tmp_path / "e"; e.mkdir()
    assert [vi.app_version(x) for x in (a, b, c, d, e)] == ["1.2.3", "4.5.6", "7.8.9", "0.3.1", ""]


def test_info_reads_git_and_a_fingerprint_only_when_dirty(tmp_path):
    r = repo(tmp_path / "r", {"a.py": "1\n", "ena_bot/x.py": "2\n"})
    i = vi.info(root=r, pkg=r / "ena_bot", started_stamp=vi.code_stamp(r / "ena_bot"))
    assert i["git"] and len(i["commit"]) == 7 and i["branch"] == "main" and i["dirty"] == 0 and i["fingerprint"] is None and i["stale_code"] is False
    (r / "a.py").write_text("changed\n")
    (r / "new.py").write_text("n\n")
    j = vi.info(root=r, pkg=r / "ena_bot", started_stamp=vi.code_stamp(r / "ena_bot"))
    assert j["dirty"] == 2 and len(j["fingerprint"]) == 7


def test_not_a_git_folder_still_reports_without_crashing(tmp_path):
    i = vi.info(root=tmp_path, pkg=tmp_path)
    assert i["git"] is False and "commit" not in i


def test_stale_code_is_detected_when_a_python_file_changes_after_start(tmp_path):
    pkg = tmp_path / "ena_bot"; pkg.mkdir()
    (pkg / "m.py").write_text("x = 1\n")
    stamp = vi.code_stamp(pkg)
    assert vi.info(root=tmp_path, pkg=pkg, started_stamp=stamp)["stale_code"] is False
    time.sleep(0.01)
    (pkg / "m.py").write_text("x = 22\n")
    assert vi.info(root=tmp_path, pkg=pkg, started_stamp=stamp)["stale_code"] is True
    (pkg / "n.py").write_text("y = 1\n")                       # 새 파일이 생겨도 바뀐 것
    assert vi.code_stamp(pkg) != stamp


def test_fingerprint_matches_solomons_so_the_two_screens_can_be_compared(tmp_path):
    sys.path.insert(0, os.environ.get("SOLOMON_SRC", "/home/user/newproject"))
    gitwatch = pytest.importorskip("solomon.gitwatch")
    r = repo(tmp_path / "r", {"a.py": "1\n"})
    (r / "a.py").write_text("edit\n")
    (r / "n.py").write_text("new\n")
    head = vi._git(["rev-parse", "HEAD"], r).strip()
    assert vi.fingerprint(r, head) == gitwatch.fingerprint(r, head) and vi.fingerprint(r, head)


def test_payload_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(vi, "info", lambda *a, **k: calls.append(1) or {"x": 1})
    vi._CACHE.clear()
    vi.payload(ttl=60)
    vi.payload(ttl=60)
    assert len(calls) == 1
    vi._CACHE.clear()
