import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
JS = TOOLS / "fractal_chart.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node 가 필요합니다")

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const code = fs.readFileSync(process.argv[1], 'utf8');
const sb = {console, document: {}, localStorage: {getItem() { return null; }, setItem() {}}, structDraw: function () {}, ST_CIRCLED: ['0', '①', '②', '③', '④', '⑤']};
vm.createContext(sb);
vm.runInContext(code + ';this.X = {frcSnapTime, frcPhase, frcHint, frcSeriesData, frcMergeMarkers, frcMergeLevels, hooked: !!structDraw.__fr};', sb);
const X = sb.X;
const P = (i, price, kind, label, prov) => ({i, price, kind, label: String(label), provisional: !!prov});
const e = (o) => Object.assign({status: 'ok', direction: 'up', complete: false, forming5: false,
  pivots: [P(0, 1, 'L', 0), P(1, 2, 'H', 1), P(2, 1.5, 'L', 2), P(3, 3, 'H', 3), P(4, 2.4, 'L', 4)], invalidation: {level: 2, side: 'below'}, targets: []}, o);
const out = {};
out.hooked = X.hooked;
out.snap = [X.frcSnapTime(105, [0, 100, 200]), X.frcSnapTime(100, [0, 100, 200]), X.frcSnapTime(-1, [0, 100, 200]), X.frcSnapTime(999, [0, 100, 200]), X.frcSnapTime(5, [])];
out.phase = [X.frcPhase(null).key, X.frcPhase({status: 'none'}).key, X.frcPhase(e({})).key, X.frcPhase(e({complete: true})).key, X.frcPhase(e({forming5: true})).key,
  X.frcPhase(e({pivots: e({}).pivots.map((p, i) => i === 4 ? Object.assign({}, p, {provisional: true}) : p)})).key];
const ref = e({});
out.hint = [X.frcHint(ref, ref), X.frcHint(ref, e({direction: 'down', complete: true})), X.frcHint(ref, e({direction: 'down'})), X.frcHint(ref, e({complete: true})), X.frcHint(ref, e({})), X.frcHint(null, ref), X.frcHint(ref, {status: 'none'})];
const bars = [0, 1, 2, 3, 4].map(k => ({time: k * 900}));
const main = [0, 900, 1800, 2700, 3600, 4500];
out.series_ok = X.frcSeriesData(bars, e({}), main, 900, 900).map(p => [p.time, p.value]);
out.series_shorter = X.frcSeriesData(bars, e({}), main, 900, 3600);
out.series_none = X.frcSeriesData(bars, {status: 'none'}, main, 900, 900);
out.series_before = X.frcSeriesData(bars, e({}), [1000, 1800, 2700, 3600], 900, 900).map(p => p.time);
// 같은 차트 봉에 겹치는 두 극점은 나중 것만
const e2 = e({pivots: [P(0, 1, 'L', 0), P(1, 2, 'H', 1), P(2, 1.5, 'L', 2), P(4, 3, 'H', 3)]});
out.collapse = X.frcSeriesData([0, 900, 1800, 2700, 3600].map(t => ({time: t})), e2, [0, 3600], 3600, 3600).map(p => [p.time, p.value]);
// 마커 합치기: 큰 시간봉의 극점과 같은 자리(시각·가격이 가깝다)의 작은 시간봉 극점은 하나로
const big = {name: '4h', sec: 14400, pts: [{time: 0, value: 1, kind: 'L', label: 0, provisional: false}, {time: 10000, value: 2, kind: 'H', label: 1, provisional: true}]};
const small = {name: '1h', sec: 3600, pts: [{time: 3600, value: 1.001, kind: 'L', label: 0, provisional: false}, {time: 10800, value: 2.004, kind: 'H', label: 1, provisional: false}, {time: 11000, value: 2.0, kind: 'L', label: 2, provisional: false}]};
const m = X.frcMergeMarkers([big, small]);
out.merge = {keys: Object.keys(m), b: m['4h'].map(x => [x.time, x.text, x.position]), s: (m['1h'] || []).map(x => [x.time, x.text, x.position])};
out.levels = X.frcMergeLevels([{name: '4h', level: 1.0}, {name: '1h', level: 1.0003}, {name: '30m', level: 1.2}, {name: '15m', level: 1.2001}]).map(l => [l.names.join(','), l.owner]);
console.log(JSON.stringify(out));
"""


def run():
    r = subprocess.run([NODE, "-e", HARNESS, str(JS)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_fractal_helpers():
    o = run()
    assert o["hooked"] is True
    assert o["snap"] == [100, 100, None, 200, None]
    assert o["phase"] == ["none", "none", "w4end", "done", "w5", "w4"]
    h = o["hint"]
    assert h[0] == "기준 시간봉" and "되돌림 마무리" in h[1] and "대기" in h[2] and "조정 뒤 재개" in h[3] and "추세 진행" in h[4] and h[5] == "—" and h[6] == "—"
    assert o["series_ok"] == [[0, 1], [900, 2], [1800, 1.5], [2700, 3], [3600, 2.4]]
    assert o["series_shorter"] is None and o["series_none"] is None          # 차트보다 짧은 시간봉·후보 없음은 그리지 않는다
    assert o["series_before"] == [1800, 2700, 3600]                            # 차트의 첫 봉(1000)보다 앞선 극점(시각 0·900)은 그릴 곳이 없어 건너뛴다
    assert o["collapse"] == [[0, 1.5], [3600, 3]]                            # 0·900·1800 → 같은 봉(0)에 겹치면 나중 것(1.5)만
    mg = o["merge"]
    assert mg["keys"] == ["4h", "1h"]
    assert mg["b"][0][1] == "4h 0 · 1h 0" and mg["b"][0][2] == "belowBar"
    assert mg["b"][1][1].startswith("4h ①?") and "1h ①" in mg["b"][1][1]    # 시각 차이가 큰 쪽 봉 길이 이내 + 가격 1% 이내 → 같은 자리
    assert mg["s"] == [[11000, "1h ②", "belowBar"]]                            # 가격이 다른 극점은 따로 표시
    assert o["levels"] == [["4h,1h", "4h"], ["30m,15m", "30m"]]


def test_wire_script_inserts_updates_and_refuses_without_structure(tmp_path):
    root = tmp_path / "p"
    (root / "ena_bot/dashboard_static").mkdir(parents=True)
    (root / "tools").mkdir()
    shutil.copy(JS, root / "tools" / "fractal_chart.js")
    shutil.copy(TOOLS / "wire_fractal_chart.py", root / "tools" / "wire_fractal_chart.py")
    page = root / "ena_bot/dashboard_static/index.html"
    wire = root / "tools" / "wire_fractal_chart.py"
    run_wire = lambda: subprocess.run(["python3", str(wire), str(root)], capture_output=True, text=True)
    page.write_text("<script>\nfunction stAnalyze(){}\n// ---- 차트 구조 자동 작도: x ----\nfunction structDraw(){}\n// ---- /차트 구조 자동 작도 ----\nlet TRNC = null;\n</script>")
    r = run_wire()
    assert "수정" in r.stdout and "function frcDraw" in page.read_text() and page.read_text().count("// ---- /프랙탈 엘리엇 ----") == 1
    assert page.read_text().index("프랙탈 엘리엇:") > page.read_text().index("/차트 구조 자동 작도")
    assert "이미 최신" in run_wire().stdout
    (root / "tools" / "fractal_chart.js").write_text(JS.read_text().replace("프랙탈 엘리엇 —", "프랙탈 엘리엇(새) —"))
    assert "수정" in run_wire().stdout and "프랙탈 엘리엇(새)" in page.read_text() and page.read_text().count("function frcDraw") == 1
    page.write_text("<script>let TRNC = null;</script>")
    r = run_wire()
    assert r.returncode != 0 and "wire_structure_chart" in (r.stderr + r.stdout) and "frcDraw" not in page.read_text()
