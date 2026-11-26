import shutil
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "wire_chart_focus.py"
HTML = "<html><head><style>x{}</style></head><body><div id=\"vp-chart\"><div class=\"chartbox\"><div id=\"tf\"></div><div class=\"chartfooter\"></div></div></div><script>\n" + "const mainApp = 1;\n" * 20 + "</script></body></html>"


def wire(root):
    return subprocess.run([sys.executable, str(TOOL), str(root)], capture_output=True, text=True)


def test_wire_inserts_once_updates_in_place_and_refuses_unknown_pages(tmp_path):
    root = tmp_path / "p"
    (root / "ena_bot/dashboard_static").mkdir(parents=True)
    page = root / "ena_bot/dashboard_static/index.html"
    page.write_text(HTML)
    r = wire(root)
    t = page.read_text()
    assert "수정" in r.stdout and t.count("CHART-FOCUS-START") == 1 and t.count("CHART-FOCUS-SCRIPT-START") == 1 and "chart-focus-btn" in t
    assert t.index("CHART-FOCUS-START") < t.index("</head>") and t.index("CHART-FOCUS-SCRIPT-START") < t.index("</body>")
    assert "이미 적용" in wire(root).stdout and page.read_text() == t
    page.write_text(t.replace("집중 모드 종료", "OLD"))                         # 옛 내용은 같은 자리에서 새 내용으로 교체
    assert "수정" in wire(root).stdout and "OLD" not in page.read_text() and page.read_text().count("CHART-FOCUS-START") == 1
    page.write_text("<html><body>no head, no script</body></html>")
    r = wire(root)
    assert r.returncode != 0 and "CHART-FOCUS" not in page.read_text()
