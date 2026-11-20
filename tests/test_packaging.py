import re
import shlex
import tomllib
from html.parser import HTMLParser
from pathlib import Path

import pytest

from solomon import __version__, cli, mdhtml
from solomon.selftest import run_selftest

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "solomon" / "USER_GUIDE.md"


def test_version_is_one_number_everywhere():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert meta["project"]["version"] == __version__ == "1.10.3"
    assert "USER_GUIDE.md" in meta["tool"]["setuptools"]["package-data"]["solomon"]
    assert "solomon.example.toml" in meta["tool"]["setuptools"]["package-data"]["solomon"]


def test_guide_and_example_config_have_one_source_of_truth():
    assert (ROOT / "USER_GUIDE.md").read_text() == GUIDE.read_text()
    assert (ROOT / "docs" / "USER_GUIDE.md").read_text() == GUIDE.read_text()
    assert (ROOT / "solomon.example.toml").read_text() == (ROOT / "solomon" / "solomon.example.toml").read_text()


def _fenced_commands(md: str) -> list[str]:
    cmds = []
    for block in re.findall(r"```\n(.*?)```", md, re.S):
        for line in block.splitlines():
            line = line.strip()
            if re.match(r"^(\.venv/bin/)?solomon\s", line):
                cmds.append(line)
    return cmds


def test_every_solomon_command_in_the_guide_really_exists():
    parser = cli.build_parser()
    subs = next(a for a in parser._actions if a.dest == "command").choices
    cmds = _fenced_commands(GUIDE.read_text())
    assert len(cmds) >= 8
    for line in cmds:
        tokens = shlex.split(line)
        name = tokens[1]
        assert name in subs, f"guide mentions unknown command: {line}"
        known = {opt for act in subs[name]._actions for opt in act.option_strings}
        for tok in tokens[2:]:
            if tok.startswith("--"):
                assert tok in known, f"guide uses unknown option {tok} in: {line}"


def test_every_script_and_file_the_guide_names_exists():
    text = GUIDE.read_text()
    for rel in set(re.findall(r"(?:bash )?(scripts/[\w.-]+\.sh|install\.sh)", text)):
        assert (ROOT / rel).is_file(), rel
    for rel in ("Start-Solomon.command", "Stop-Solomon.command"):
        assert rel in text and (ROOT / rel).is_file()


def test_every_dashboard_label_the_guide_quotes_is_really_on_the_page():
    page = (ROOT / "solomon" / "dashboard.html").read_text()
    for label in ["개선 · 코드 수정", "분석 · 읽기 전용", "신규 프로젝트", "끈질기게 시도", "프로젝트 설정: 테스트 명령", "▶ 실행", "■ 중지",
                  "밤샘 자동 수정", "▶ 폴더 만들고 시작", "작업 폴더 테스트 실행", "사용 가이드", "목록에서 제거", "폴더 삭제 (휴지통)",
                  "한 번에 순서대로 자동 수정", "같은 파일은 한 번에 묶어서", "+ 기존 폴더 추가", "결정권자 성적표", "👑 결정권자", "3개 AI 위에서 최종 채택", "Ollama 연결 시험", "＋ 새 폴더 만들기", "찾아보기", "도구 · 설정", "환경 점검", "대시보드 재시작", "git 초기화", "이름 바꾸기", "별명·메모 저장", "Solomon 업데이트", "yt-dlp 설치·업데이트", "영상 분석", "유튜브 영상 주소", "AI별 영상 직접 시청 시험", "자막을 직접 붙여넣기", "이 아이디어로 개선 시작", "영상 분석 결과", "단계별 AI 참여 도식", "AI · 모델 성능", "+ 신규 프로젝트", "주의 필요한 순", "프로젝트 카드", "AI 1 (담당)", "최근 활동 순", "관리 탭에서 5W1H",
                  "복원", "커밋 취소", "되돌리기", "폐기", "변경 보기", "적용 판단", "단계별 진행 과정", "최대 수정 개수", "최소 심각도", "제한 시간"]:
        assert label in page, label


class _Balance(HTMLParser):
    VOID = {"meta", "br", "hr", "img", "link"}

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> after {self.stack[-3:]}")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def test_guide_renders_to_well_formed_safe_html():
    html = mdhtml.page(GUIDE.read_text())
    p = _Balance()
    p.feed(html)
    assert not p.errors and not p.stack, (p.errors[:3], p.stack)
    assert "<script" not in html.lower() and "javascript:" not in html
    assert html.count("<h2") >= 11 and "<table>" in html and "<pre><code>" in html
    assert 'id="t1-설치-점검' in html or "t1" in html.lower()


def test_markdown_renderer_escapes_and_limits_links():
    body, _ = mdhtml.render("<img src=x onerror=alert(1)> [a](javascript:alert(1)) [b](https://example.com) `<b>`")
    assert "<img" not in body and "javascript:alert(1))" in body.replace("&", "&") and 'href="javascript' not in body
    assert 'href="https://example.com"' in body and "<code>&lt;b&gt;</code>" in body


def test_dashboard_serves_the_guide_with_a_strict_csp():
    import http.client
    from test_dashboard import Env, sleeper
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        env = Env(Path(tmp))
        try:
            st, body, resp = env.call("GET", "/guide", token=None)
            assert st == 200 and b"Solomon" in body
            assert "script-src" not in resp.getheader("Content-Security-Policy")
            assert env.call("GET", "/guide", token=None, host="evil.example.com")[0] == 403
        finally:
            env.close()


def test_selftest_passes_and_costs_nothing(capsys):
    lines = []
    assert run_selftest(out=lines.append) == 0
    assert lines[-1].startswith("결과: 12/12 통과")


def test_init_writes_a_loadable_config_and_refuses_to_overwrite(tmp_path, capsys):
    dest = tmp_path / "s.toml"
    assert cli.main(["init", "--path", str(dest)]) == 0
    assert cli.main(["init", "--path", str(dest)]) == 2
    assert cli.main(["init", "--path", str(dest), "--force"]) == 0
    from solomon.settings import load_settings
    s = load_settings(dest)  # the example file is valid as it stands (all options are the defaults)
    assert s.roles == {"planner": "claude", "builder": "codex", "reviewer": "claude"}
    assert s.max_attempts == 3 and s.include_uncommitted is True


def test_doctor_explains_what_to_do(capsys, monkeypatch):
    monkeypatch.setattr("solomon.providers.base.CliProvider.locate", lambda self: None, raising=False)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "python 3.11+" in out and "결과:" in out
    assert rc in (0, 2)


def test_numbered_steps_keep_their_numbers_in_the_rendered_guide():
    body, _ = mdhtml.render("text\n\n4. four\n5. five\n\n1. one\n2. two\n")
    assert '<ol start="4">' in body and "<ol>" in body


def test_print_version_of_the_guide_has_cover_and_contents():
    html = mdhtml.print_page(GUIDE.read_text(), "1.10.3", "2026-01-01")
    assert 'class="cover"' in html and "버전 1.10.3" in html and 'class="toc"' in html
    assert html.count('<h1') == 1  # the cover's title only; the guide's own h1 is dropped
    assert "@page" in html and "<script" not in html.lower()


def test_a_tool_pip_installed_into_the_same_venv_is_found(tmp_path, monkeypatch):
    from solomon import env
    exe = tmp_path / "bin" / "yt-dlp"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(env.sys, "executable", str(tmp_path / "bin" / "python"))
    monkeypatch.setattr(env, "_posix_candidates", lambda name: [])
    monkeypatch.setenv("PATH", "/nonexistent")
    assert env.resolve_cli("yt-dlp") == str(exe.resolve())
