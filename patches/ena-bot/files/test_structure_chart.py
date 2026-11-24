import json
import math
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ena_bot import structure as st
try:
    from test_structure import UP, build
except ImportError:          # tests 폴더가 패키지인 저장소
    from tests.test_structure import UP, build

TOOLS = Path(__file__).resolve().parents[1] / "tools"
JS = TOOLS / "structure_chart.js"
NODE = shutil.which("node")


def walk(seed, n=700, vol=0.006):
    rnd = random.Random(seed)
    closes = [1.0]
    for _ in range(n):
        closes.append(closes[-1] * math.exp(rnd.gauss(0, vol)))
    o = [closes[0]] + closes[:-1]
    h = [max(a, b) * (1 + abs(rnd.gauss(0, 0.002))) for a, b in zip(o, closes)]
    l = [min(a, b) * (1 - abs(rnd.gauss(0, 0.002))) for a, b in zip(o, closes)]
    return o, h, l, closes


def rising_lows(n=300):
    base = [1.0 + 0.002 * i for i in range(n)]
    c = [b * (1 + 0.06 * (1 - abs((i % 30) - 15) / 15)) for i, b in enumerate(base)]
    o = [c[0]] + c[:-1]
    return o, [max(a, b) * 1.002 for a, b in zip(o, c)], [min(a, b) * 0.998 for a, b in zip(o, c)], c


def datasets():
    out = {f"walk{s}": walk(s) for s in range(1, 9)}
    out["walk_calm"] = walk(11, vol=0.002)
    out["up5_complete"] = build(UP)[:4]
    out["up5_forming"] = build(UP[:5])[:4]
    out["up5_wave5_moving"] = build(UP[:5] + [1.62])[:4]
    out["down5_wave5_moving"] = build([3 - x for x in UP[:5] + [1.62]])[:4]
    out["down5"] = build([3 - x for x in UP])[:4]
    out["overlap"] = build([1.00, 1.30, 1.12, 1.65, 1.20, 1.80, 1.55])[:4]
    out["short3"] = build([1.00, 1.30, 1.12, 1.30, 1.15, 1.80, 1.55])[:4]
    out["rising_lows"] = rising_lows()
    out["short"] = tuple([1.0] * 30 for _ in range(4))
    return out


def close(a, b, path="x"):
    """파이썬과 JS 결과를 모든 필드에서 비교한다(숫자는 상대 1e-9, 나머지는 같아야 한다)."""
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), (path, sorted(a), sorted(b))
        for k in a:
            close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), (path, len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            close(x, y, f"{path}[{i}]")
    elif isinstance(a, float) or isinstance(b, float):
        assert a == pytest.approx(b, rel=1e-9, abs=1e-12), (path, a, b)
    else:
        assert a == b, (path, a, b)


@pytest.mark.skipif(NODE is None, reason="node 가 없어 JS 대조를 건너뜀")
def test_the_chart_javascript_gives_the_same_structure_as_the_python_reference(tmp_path):
    ds = datasets()
    py = {k: st.analyze(*v) for k, v in ds.items()}
    data = tmp_path / "d.json"
    data.write_text(json.dumps({k: [list(a) for a in v] for k, v in ds.items()}))
    script = tmp_path / "run.js"
    script.write_text(JS.read_text(encoding="utf-8") + "\nconst D = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8')); const R = {};"
                      "\nfor (const k of Object.keys(D)) R[k] = stAnalyze(D[k][0], D[k][1], D[k][2], D[k][3]);\nconsole.log(JSON.stringify(R));", encoding="utf-8")
    js = json.loads(subprocess.run([NODE, str(script), str(data)], capture_output=True, text=True, check=True).stdout)
    for k in ds:
        close(py[k], js[k], k)
    assert py["up5_complete"]["elliott"]["status"] == "ok" and py["overlap"]["elliott"]["status"] == "none"          # 대조한 데이터가 실제로 다양한 결과를 포함한다
    assert py["rising_lows"]["trendlines"]["support"] is not None and py["short"]["elliott"]["reason"] == "봉이 너무 적음"
    assert sum(1 for v in py.values() if v["elliott"]["status"] == "ok") >= 4


def test_wire_tool_installs_upgrades_in_place_and_is_atomic(tmp_path):
    (tmp_path / "ena_bot/dashboard_static").mkdir(parents=True)
    page = tmp_path / "ena_bot/dashboard_static/index.html"
    html = ("<script>\nlet SHK_KEY = '', SHK_CACHE = [];\nfunction shockEvents() {\n  if (k !== SHK_KEY) { SHK_KEY = k; SHK_CACHE = detectShocks(bars); }\n}\n"
            "let TRNC = null;   // makeTrnChart 인스턴스(스크립트 끝에서 생성)\nfunction paintMarkers() {\n  const ms = [];\n  if (TRNC) ms.push(...TRNC.markers());\n"
            "  ms.sort((a, b) => a.time - b.time); candle.setMarkers(ms);\n}\n</script>")
    page.write_text(html)
    run = lambda: subprocess.run([sys.executable, str(TOOLS / "wire_structure_chart.py"), str(tmp_path)], capture_output=True, text=True)
    assert run().returncode == 0
    out = page.read_text()
    assert out.count("function stAnalyze") == 1 and out.count("  structDraw();\n") == 1 and out.index("function stAnalyze") < out.index("let TRNC = null")
    assert "SHK_CACHE = detectShocks(bars).filter(e => e.zRange >= SHK_SHOW_Z)" in out and out.count("const SHK_SHOW_Z") == 1          # ⚡ 표시 기준도 함께
    assert "이미 최신" in run().stdout and page.read_text() == out
    # 옛 버전(끝 표시가 없고 기본이 펼침)이 이미 적용돼 있으면 그 블록만 새 것으로 바꾼다
    old_block = "// ---- 차트 구조 자동 작도: 옛 버전 ----\nfunction stAnalyze() { return 'old'; }\nconst STR = {collapsed: false};\n"
    start = out.index("// ---- 차트 구조 자동 작도:")
    end = out.index("// ---- /차트 구조 자동 작도 ----\n") + len("// ---- /차트 구조 자동 작도 ----\n")
    page.write_text(out[:start] + old_block + out[end:])
    r = run()
    upgraded = page.read_text()
    assert r.returncode == 0 and "수정" in r.stdout and "return 'old'" not in upgraded and upgraded.count("function stAnalyze") == 1 and "collapsed: true" in upgraded
    assert upgraded.count("  structDraw();\n") == 1 and upgraded.count("let TRNC = null;") == 1
    # 위치를 못 찾으면 그대로
    page.write_text(html.replace("TRNC.markers()", "TRNC.markers2()"))
    before = page.read_text()
    assert run().returncode != 0 and page.read_text() == before


def test_the_summary_line_and_the_display_threshold_are_what_the_page_needs():
    src = JS.read_text(encoding="utf-8")
    assert "collapsed: true" in src and "function structSummary" in src and "maxLineDist: 8.0" in src and src.rstrip().endswith("// ---- /차트 구조 자동 작도 ----")
    assert st.MAX_LINE_DIST_ATR == 8.0                                   # 파이썬 기준 구현과 같은 값
    shock_src = (TOOLS / "shock_chart.js").read_text(encoding="utf-8")
    assert "const SHK_SHOW_Z = 9;" in shock_src and "e.zRange >= SHK_SHOW_Z" in shock_src
