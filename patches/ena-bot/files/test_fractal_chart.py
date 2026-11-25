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
vm.runInContext(code + ';this.X = {frcSnapTime, frcPhase, frcSide, frcOverall, frcVote, frcSeriesData, frcMergeMarkers, frcMergeLevels, hooked: !!structDraw.__fr};', sb);
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
const dn = (o) => e(Object.assign({direction: 'down'}, o));
out.side = {
  w4end_up: X.frcSide(e({}), null), w5_up: X.frcSide(e({forming5: true}), null), done_up: X.frcSide(e({complete: true}), null),
  w4end_dn: X.frcSide(dn({}), null), done_dn: X.frcSide(dn({complete: true}), null), none: X.frcSide({status: 'none'}, null), nul: X.frcSide(null, null),
  w4_wait: X.frcSide(e({pivots: e({}).pivots.map((p, i) => i === 4 ? Object.assign({}, p, {provisional: true}) : p)}), null),
  with_ref: X.frcSide(e({}), ref), counter: X.frcSide(dn({}), ref), self: X.frcSide(ref, ref), counter_done: X.frcSide(dn({complete: true}), ref),
};
out.vote = [X.frcVote([{name: '4h', side: 'long'}, {name: '1h', side: 'long'}]), X.frcVote([{name: '4h', side: 'short'}, {name: '1h', side: 'long'}]),
  X.frcVote([{name: '30m', side: 'long'}, {name: '15m', side: 'short'}]), X.frcVote([{name: '4h', side: 'wait'}]), X.frcVote([{name: '1h', side: 'long'}, {name: '15m', side: 'short'}])].map(v => v.side);
const o1 = X.frcOverall([{name: '4h', side: 'short'}, {name: '1h', side: 'short'}, {name: '30m', side: 'long'}, {name: '15m', side: 'wait'}]);
const o2 = X.frcOverall([{name: '4h', side: 'wait'}, {name: '1h', side: 'wait'}, {name: '30m', side: 'wait'}, {name: '15m', side: 'wait'}]);
const o3 = X.frcOverall([{name: '4h', side: 'long'}, {name: '1h', side: 'long'}, {name: '30m', side: 'long'}, {name: '15m', side: 'long'}]);
const o4 = X.frcOverall([{name: '4h', side: 'long'}, {name: '1h', side: 'long'}, {name: '30m', side: 'short'}, {name: '15m', side: 'short'}]);
out.overall = [o1, o2, o3, o4].map(o => [o.big.side, o.small.side, o.note]);
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
    sd = o["side"]
    assert [sd["w4end_up"]["side"], sd["w5_up"]["side"], sd["done_up"]["side"]] == ["long", "long", "short"]          # 4파 끝·5파 진행 → 같은 방향, 5파 완성 → 조정(반대)
    assert [sd["w4end_dn"]["side"], sd["done_dn"]["side"]] == ["short", "long"]
    assert sd["none"]["side"] == "wait" and sd["nul"]["side"] == "wait" and sd["w4_wait"]["side"] == "wait" and "4파 조정 중" in sd["w4_wait"]["why"]
    assert sd["w4end_up"]["badge"] == "롱" and sd["done_up"]["badge"] == "숏" and sd["none"]["badge"] == "관망" and "추격 주의" in sd["w5_up"]["why"]
    assert sd["with_ref"]["counter"] is False and sd["self"]["counter"] is False
    assert sd["counter"]["side"] == "short" and sd["counter"]["counter"] is True                  # 큰 흐름(롱)과 반대 관점이면 역추세 표시
    assert sd["counter_done"]["side"] == "long" and sd["counter_done"]["counter"] is False        # 반대 방향 5파 완성 → 큰 흐름 쪽(롱) 관점이라 역추세 아님
    assert o["vote"] == ["long", "wait", "wait", "wait", "long"]                                  # 4h 숏4 vs 1h 롱3 → 차이 1 이하라 관망, 1h 롱3 vs 15m 숏1 → 롱
    assert o["overall"][0][:2] == ["short", "long"] and "역추세" in o["overall"][0][2] and "숏 재개" in o["overall"][0][2]
    assert o["overall"][1] == ["wait", "wait", "뚜렷한 방향이 없습니다 → 관망"]
    assert o["overall"][2][:2] == ["long", "long"] and "롱 재개" in o["overall"][2][2]
    assert o["overall"][3][:2] == ["long", "short"] and "역추세" in o["overall"][3][2]
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


# ------------------------------------------------------------------ 단타 민감 모드
HARNESS2 = r"""
const fs = require('fs'), vm = require('vm');
const sb = {console, document: {}, localStorage: {getItem() { return null; }, setItem() {}}, structDraw: function () {}};
vm.createContext(sb);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8') + '\n' + fs.readFileSync(process.argv[2], 'utf8') + '\n' +
  ';this.X = {stAnalyze, frcScalesFor, frcElliott, FRC, FRC_FINE_SCALES};', sb);
const X = sb.X, d = JSON.parse(fs.readFileSync(process.argv[3], 'utf8')), out = {};
const norm = X.stAnalyze(d.o, d.h, d.l, d.c), fine = X.stAnalyze(d.o, d.h, d.l, d.c, X.FRC_FINE_SCALES);
out.status = [norm.elliott.status, fine.elliott.status];
out.scales = [X.frcScalesFor('15m', true), X.frcScalesFor('30m', true), X.frcScalesFor('1h', true), X.frcScalesFor('4h', true), X.frcScalesFor('15m', false)];
X.FRC.data['15m'] = {res: norm, resFine: fine}; X.FRC.data['1h'] = {res: norm, resFine: null};
X.FRC.fine = false; out.off = [X.frcElliott('15m').status, X.frcElliott('1h').status, X.frcElliott('4h')];
X.FRC.fine = true; out.on = [X.frcElliott('15m').status, X.frcElliott('1h').status];
console.log(JSON.stringify(out));
"""


def _walk(seed, n=700, vol=0.003):
    import math
    import random
    rnd = random.Random(seed)
    c = [1.0]
    for _ in range(n):
        c.append(c[-1] * math.exp(rnd.gauss(0, vol)))
    o = [c[0]] + c[:-1]
    h = [max(a, b) * (1 + abs(rnd.gauss(0, 0.001))) for a, b in zip(o, c)]
    l = [min(a, b) * (1 - abs(rnd.gauss(0, 0.001))) for a, b in zip(o, c)]
    return o, h, l, c


def test_fine_mode_finds_candidates_the_default_scales_miss_and_only_for_30m_and_15m(tmp_path):
    from ena_bot import structure as st
    found = None
    for seed in range(1, 80):
        o, h, l, c = _walk(seed)
        a = st.analyze(o, h, l, c)["elliott"]["status"]
        b = st.analyze(o, h, l, c, (1.5, 2.0, 3.0))["elliott"]["status"]
        if a == "none" and b == "ok":
            found = (seed, o, h, l, c)
            break
    assert found, "기본 크기로는 후보가 없고 민감 크기로는 있는 시세를 찾지 못했습니다"
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"o": found[1], "h": found[2], "l": found[3], "c": found[4]}))
    r = subprocess.run([NODE, "-e", HARNESS2, str(TOOLS / "structure_chart.js"), str(JS), str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["status"] == ["none", "ok"]                                   # 파이썬 계산과 같은 결과
    assert o["scales"] == [[1.5, 2.0, 3.0], [1.5, 2.0, 3.0], None, None, None]
    assert o["off"] == ["none", "none", None]                              # 꺼짐: 기본 크기
    assert o["on"] == ["ok", "none"]                                       # 켜짐: 15분만 민감(1시간은 그대로)
