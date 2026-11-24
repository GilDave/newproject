import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
NODE = shutil.which("node")

DASH = ("from .hayes_holdings import DEFAULT_INTERVAL_MIN\nclass H:\n    def do_GET(self):\n        try:\n            if 0:\n                pass\n"
        '                elif url.path == "/healthz":\n                    pass\n        except ValueError:\n            pass\n')
HTML = ('<body>\n  <details class="panel hayes-lookup" id="hayes-lookup">\n</details>\n<script>\nlet TRNC = null;   // makeTrnChart 인스턴스(스크립트 끝에서 생성)\n'
        "function paintMarkers() {\n  const ms = [];\n  if (TRNC) ms.push(...TRNC.markers());\n  ms.sort((a, b) => a.time - b.time); candle.setMarkers(ms);\n}\n</script></body>")


def setup(tmp_path, dash=DASH, html=HTML):
    (tmp_path / "ena_bot/dashboard_static").mkdir(parents=True)
    (tmp_path / "ena_bot/dashboard.py").write_text(dash)
    (tmp_path / "ena_bot/dashboard_static/index.html").write_text(html)
    return lambda: subprocess.run([sys.executable, str(TOOLS / "wire_entry_plan.py"), str(tmp_path)], capture_output=True, text=True)


def test_wire_tool_installs_upgrades_in_place_and_is_atomic(tmp_path):
    run = setup(tmp_path)
    assert run().returncode == 0
    d, p = (tmp_path / "ena_bot/dashboard.py").read_text(), (tmp_path / "ena_bot/dashboard_static/index.html").read_text()
    assert "from . import entry_api" in d and 'url.path == "/api/entry-plan"' in d and "entry_api.payload(" in d and "klines_payload(client, cfg.symbol, iv, n, None)" in d
    assert p.count('id="entry-card"') == 1 and p.count("function entDraw") == 1 and p.count("  entDraw();\n") == 1 and p.index("function entDraw") < p.index("let TRNC = null")
    assert p.index('id="entry-card"') < p.index('id="hayes-lookup"')
    assert "이미 최신" in run().stdout and (tmp_path / "ena_bot/dashboard_static/index.html").read_text() == p
    old = p[:p.index("// ---- 진입 계획:")] + "// ---- 진입 계획: 옛 버전 ----\nfunction entDraw() { return 'old'; }\n// ---- /진입 계획 ----\n" + p[p.index("// ---- /진입 계획 ----\n") + len("// ---- /진입 계획 ----\n"):]
    (tmp_path / "ena_bot/dashboard_static/index.html").write_text(old)
    assert "수정" in run().stdout
    up = (tmp_path / "ena_bot/dashboard_static/index.html").read_text()
    assert "return 'old'" not in up and up.count("function entDraw") == 1 and up.count('id="entry-card"') == 1 and up.count("  entDraw();\n") == 1
    d_before = (tmp_path / "ena_bot/dashboard.py").read_text()
    (tmp_path / "ena_bot/dashboard_static/index.html").write_text(HTML.replace("TRNC.markers()", "TRNC.markers2()"))
    r = run()                                                       # 이미 적용된 dashboard.py + 못 찾는 index.html: 아무것도 바꾸지 않는다
    assert r.returncode != 0 and (tmp_path / "ena_bot/dashboard.py").read_text() == d_before and "TRNC.markers2()" in (tmp_path / "ena_bot/dashboard_static/index.html").read_text()


def test_wire_tool_changes_nothing_when_dashboard_py_does_not_match(tmp_path):
    run = setup(tmp_path, dash="x = 1\n")
    r = run()
    assert r.returncode != 0 and (tmp_path / "ena_bot/dashboard.py").read_text() == "x = 1\n" and "entry-card" not in (tmp_path / "ena_bot/dashboard_static/index.html").read_text()


@pytest.mark.skipif(NODE is None, reason="node 가 없어 JS 문법 확인을 건너뜀")
def test_the_card_script_is_valid_javascript_and_its_pure_helpers_behave(tmp_path):
    src = (TOOLS / "entry_card.js").read_text(encoding="utf-8")
    f = tmp_path / "t.js"
    f.write_text("globalThis.document = {getElementById: () => null, createElement: () => ({}), head: {appendChild() {}}}; globalThis.fetch = () => new Promise(() => {});\n"
                 "globalThis.setTimeout = () => 0; globalThis.setInterval = () => 0; globalThis.localStorage = {getItem: () => null, setItem() {}};\n" + src +
                 "\nENT.data = {ok: true, study: {ok: false}}; const out = {v: entVerdict('4h', 'pullback'), fmt: entFmt(0.1), none: entFmt(null)};"
                 "\nENT.data.study = {ok: true, groups: {'4h|pullback': {verdict: {use: true, why: '통과'}}, '1h|reversion': {verdict: {use: false, why: '기준 미달'}}}};"
                 "\nout.ok = entVerdict('4h', 'pullback'); out.bad = entVerdict('1h', 'reversion'); out.m15 = entVerdict('15m', 'pullback'); out.unk = entVerdict('1d', 'pullback');"
                 "\nout.txt = entPlanText({direction: 'short', setup: 'reversion', aligned_trend: false}); console.log(JSON.stringify(out));", encoding="utf-8")
    import json
    out = json.loads(subprocess.run([NODE, str(f)], capture_output=True, text=True, check=True).stdout)
    assert out["v"]["cls"] == "warn" and "검증 전" in out["v"]["text"] and out["ok"]["cls"] == "ok" and out["bad"]["cls"] == "bad" and "기준 미달" in out["bad"]["text"]
    assert "검증하지 않았습니다" in out["m15"]["text"] and "검증하지 않았습니다" in out["unk"]["text"]
    assert out["txt"] == "숏 밴드 반전 (추세와 반대)" and out["fmt"] == "0.10000" and out["none"] == "-"


def test_unverified_rules_are_never_called_favorable_on_the_card():
    src = (TOOLS / "entry_card.js").read_text(encoding="utf-8")
    assert "조건이 가장 많이 겹친 시간봉(과거 검증 미통과 · 참고용)" in src and "v.cls === 'ok' ? '지금 가장 유리한 시간봉: '" in src      # 통과한 규칙만 '유리한'이라고 부른다
    assert "function entStudyLine" in src and "방향을 맞히는 근거로 쓰지 마세요" in src and "' (참고)'" in src


@pytest.mark.skipif(NODE is None, reason="node 가 없어 JS 확인을 건너뜀")
def test_the_study_summary_line_counts_passed_rules(tmp_path):
    import json
    src = (TOOLS / "entry_card.js").read_text(encoding="utf-8")
    f = tmp_path / "s.js"
    f.write_text("globalThis.document = {getElementById: () => null, createElement: () => ({}), head: {appendChild() {}}}; globalThis.fetch = () => new Promise(() => {});\n"
                 "globalThis.setTimeout = () => 0; globalThis.setInterval = () => 0; globalThis.localStorage = {getItem: () => null, setItem() {}};\n" + src +
                 "\nconst out = {};\nENT.data = {study: {ok: false}}; out.none = entStudyLine();"
                 "\nENT.data = {study: {ok: true, coins: 18, age_days: 2.2, groups: {a: {verdict: {use: false}}, b: {verdict: {use: false}}}}}; out.zero = entStudyLine();"
                 "\nENT.data.study.groups.b.verdict.use = true; out.one = entStudyLine(); console.log(JSON.stringify(out));", encoding="utf-8")
    out = json.loads(subprocess.run([NODE, str(f)], capture_output=True, text=True, check=True).stdout)
    assert "검증 전" in out["none"] and "2개 중 0개 통과" in out["zero"] and "쓰지 마세요" in out["zero"] and "2개 중 1개 통과" in out["one"] and "쓰지 마세요" not in out["one"]
