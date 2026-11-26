import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
MAIN = "<script>\n" + "const mainApp = 1;\n" * 40 + "function calendarHtml(){}\n</script>"
PAGE = "<html><head><style>x{}</style></head><body><div id=\"vp-chart\"><div class=\"chartbox\"><div id=\"tf\"></div><div class=\"chartfooter\"></div></div></div>" + MAIN + "</body></html>"
WIRES = {"wire_hayes_fold.py": "HAYES-FOLD-SCRIPT-START", "wire_chart_grid.py": "CHART-GRID-SCRIPT-START", "wire_chart_focus.py": "CHART-FOCUS-SCRIPT-START"}


def scripts(html):
    return re.findall(r"<script>(.*?)</script>", html, re.S)


def run(name, root):
    return subprocess.run([sys.executable, str(TOOLS / name), str(root)], capture_output=True, text=True)


def test_wire_scripts_keep_the_main_script_as_the_last_script_block(tmp_path):
    """저장소 테스트(test_dashboard)는 페이지의 '마지막 <script>' 를 메인 스크립트로 읽는다. 적용 스크립트는 그 앞에 넣어야 한다."""
    root = tmp_path / "p"
    (root / "ena_bot/dashboard_static").mkdir(parents=True)
    (root / "ena_bot").joinpath("dashboard.py").write_text("from .hayes_holdings import DEFAULT_INTERVAL_MIN\n                elif url.path == \"/healthz\":\n")
    (root / "ena_bot" / "version_info.py").write_text("")
    page = root / "ena_bot/dashboard_static/index.html"
    page.write_text(PAGE)
    for name, marker in list(WIRES.items()) + [("wire_version_badge.py", "VERSION-BADGE-START")]:
        r = run(name, root)
        assert r.returncode == 0, (name, r.stderr, r.stdout)
        t = page.read_text()
        assert marker in t and "function calendarHtml" in scripts(t)[-1], name                # 메인(가장 긴) 스크립트가 마지막
        assert t.index(marker) < t.index("const mainApp"), name
        assert "이미 적용" in run(name, root).stdout and page.read_text() == t, name           # 두 번째 실행은 아무것도 바꾸지 않는다
    assert len(scripts(page.read_text())) == 5


def test_a_block_left_after_the_main_script_by_an_older_version_is_moved_in_front(tmp_path):
    root = tmp_path / "p"
    (root / "ena_bot/dashboard_static").mkdir(parents=True)
    page = root / "ena_bot/dashboard_static/index.html"
    old = PAGE.replace("</body>", "<!-- HAYES-FOLD-SCRIPT-START -->\n<script>\n/* old */\n</script>\n<!-- HAYES-FOLD-SCRIPT-END -->\n</body>")
    page.write_text(old)
    assert "/* old */" in scripts(old)[-1]
    r = run("wire_hayes_fold.py", root)
    t = page.read_text()
    assert "수정" in r.stdout and "/* old */" not in t and t.count("HAYES-FOLD-SCRIPT-START") == 1 and "function calendarHtml" in scripts(t)[-1]
