import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
JS = TOOLS / "confluence.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node 가 필요합니다")

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const sb = {console, document: {}, localStorage: {getItem() { return null; }, setItem() {}}, structDraw: function () {}, setInterval() {}};
vm.createContext(sb);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8') + ';this.X = {cfNorm, cfClusters, cfAiFor, cfAiVerdict, cfAiLines, cfAiRange, cfVerdict, cfTfState, CF_RULE, hooked: !!structDraw.__cf};', sb);
const X = sb.X, out = {};
const r = (v) => v === null || v === undefined ? v : Math.round(v * 10000) / 10000;
const n = (raw, price, atr) => { const o = X.cfNorm(raw, price, atr); return o.ok ? {ok: true, side: o.side, mid: r(o.mid), stop: r(o.stop), t1: r(o.t1), t2: r(o.t2), rr: r(o.rr), poor: o.poor, widened: o.widened, capped: o.capped, inZone: o.inZone, distPct: r(o.distPct)} : o;
};
const L = (o) => Object.assign({side: 'long', lo: 99.5, hi: 99.8, stop: 98.8, targets: [101.5, 103]}, o);
out.hooked = X.hooked;
out.ok = n(L({}), 100, 1);
out.far = n(L({lo: 95, hi: 96}), 100, 1);
out.widen = n(L({stop: 99.4}), 100, 1);
out.wide = n(L({stop: 95}), 100, 1);
out.inside = n(L({stop: 99.6}), 100, 1);
out.cap = n(L({targets: [110]}), 100, 1);
out.notgt = n(L({targets: [99]}), 100, 1);
out.pct_ok = n(L({lo: 91, hi: 92, stop: 90.2, targets: [95]}), 100, null);
out.pct_far = n(L({lo: 90, hi: 91, stop: 89.2, targets: [95]}), 100, null);
out.inzone = n(L({}), 99.7, 1);
out.short = n({side: 'short', lo: 100.2, hi: 100.5, stop: 101.4, targets: [98.5, 97]}, 100, 1);
out.poor = n(L({targets: [100.2]}), 100, 1);
out.none = [X.cfNorm(null, 100, 1).ok, X.cfNorm(L({}), 0, 1).ok];
const it = (tf, method, side, mid) => ({tf, method, n: {ok: true, side, mid, lo: mid - 0.05, hi: mid + 0.05}});
const cl = X.cfClusters([it('15m', 'entry', 'long', 100), it('1h', 'levels', 'long', 100.3), it('4h', 'fractal', 'long', 102), it('1h', 'entry', 'short', 100.2), it('4h', 'levels', 'long', 100.4)], 100);
out.cl = cl.map(c => [c.side, c.members.length, c.kinds.sort(), r(c.mid)]);
const cl2 = X.cfClusters([it('15m', 'entry', 'long', 100), it('1h', 'entry', 'short', 100.1)], 100);
out.cl2 = cl2.length;
const fc = {forecast: {horizons: [{minutes: 1, direction: 'down', label_ko: '하락', expected_return: -0.000267, expected: 0.10247, score: -0.545, status: 'ok'},
  {minutes: 15, direction: 'up', label_ko: '상승', expected_return: 0.001, expected: 0.1031, score: 0.4, status: 'ok'}, {minutes: 60, status: 'insufficient', reason: '봉 부족'}, {minutes: 240, direction: 'flat', status: 'ok', expected_return: 0}]}};
out.ai = [X.cfAiFor(fc, '15m'), X.cfAiFor(fc, '1h'), X.cfAiFor(fc, '30m'), X.cfAiFor(null, '15m'), X.cfAiFor({forecast: {}}, '15m'), X.cfAiFor(fc, '4h')];
const lf = (o) => ({series: [Object.assign({status: 'ok', horizon_label: '3분 뒤 종가', current: {hit: 0.5049315619967794, mae: 0.000321401763548984, base_mae: 0.000321533816425121}, trading: {cum_return: -0.9824011901251959}}, o)]});
out.verdict = [X.cfAiVerdict(lf({})), X.cfAiVerdict(lf({current: {hit: 0.56, mae: 0.0003, base_mae: 0.00033}, trading: {cum_return: 0.1}})), X.cfAiVerdict(null), X.cfAiVerdict({series: [{status: 'error'}]})];
const lines = X.cfAiLines({forecast: {horizons: [{minutes: 1, label_ko: '하락', label: '1분', direction: 'down', expected: 0.10247, base_price: 0.1025, status: 'ok'},
  {minutes: 15, label: '15분', direction: 'up', expected: 0.1031, base_price: 0.1025, status: 'ok'}, {minutes: 60, label: '1시간', direction: 'down', expected: 0.1020, status: 'ok'},
  {minutes: 240, label: '4시간', direction: 'flat', expected: 0.1026, status: 'ok'}, {minutes: 1440, label: '1일', direction: 'up', expected: 0.11, status: 'ok'}, {minutes: 30, status: 'insufficient'}]}}, 900, 26);
out.lines = lines.map(l => [l.minutes, l.label, l.side, l.price, l.k, l.trail]);
const real = ['1:1분:down:0.10243014', '3:3분:down:0.10242556', '5:5분:down:0.10242007', '15:15분:down:0.10236401', '30:30분:neutral:0.10243989', '60:1시간:neutral:0.10248999', '240:4시간:neutral:0.10250251', '1440:1일:up:0.10382156', '10080:1주:down:0.09888259', '43200:1달:down:0.09357405']
  .map(s => { const [m, l, d, p] = s.split(':'); return {minutes: +m, label: l, direction: d, expected: +p, status: 'ok'}; });
out.real = X.cfAiLines({forecast: {horizons: real}}, 900, 26).map(l => [l.minutes, l.side, l.k, l.trail]);
out.real_1m = X.cfAiLines({forecast: {horizons: real}}, 60, 26).map(l => l.minutes);
out.many = X.cfAiLines({forecast: {horizons: [15, 60, 240, 1440, 10080, 43200, 129600].map((m, i) => ({minutes: m, label: m + '분', direction: 'up', expected: 0.1 * (1 + 0.01 * (i + 1)), status: 'ok'}))}}, 900, 26).map(l => l.minutes);
const V = (arr) => X.cfVerdict(arr.map(([tf, ind, ell]) => ({tf, ind, ell})));
const vv = (v) => ({head: v.head, big: [v.big.side, v.big.sum], small: [v.small.side, v.small.sum], states: v.rows.map(r => r.tf + ':' + r.state + ':' + (r.side || '-')), conflicts: v.conflicts.map(r => r.tf), text: v.text});
out.v_user = vv(V([['15m', 'long', null], ['30m', null, null], ['1h', 'long', null], ['4h', 'short', 'short'], ['1d', null, null]]));
out.v_conflict = vv(V([['15m', null, null], ['30m', null, null], ['1h', 'long', 'short'], ['4h', null, null]]));
out.v_none = vv(V([['15m', null, null], ['1h', null, null]]));
out.v_same = vv(V([['15m', 'long', 'long'], ['30m', 'long', 'long'], ['1h', 'long', 'long'], ['4h', 'long', null]]));
out.v_opp = vv(V([['15m', 'short', 'short'], ['30m', 'short', 'short'], ['4h', 'long', 'long']]));
out.v_small_only = vv(V([['30m', 'long', 'long'], ['15m', 'long', null], ['4h', null, null]]));
out.range = [X.cfAiRange(X.cfAiLines({forecast: {horizons: real}}, 900, 26), 0.10215), X.cfAiRange([], 0.1), X.cfAiRange(null, 0.1), X.cfAiRange([{price: 0.1}], 0.1)];
out.lines_empty = [X.cfAiLines(null, 900, 26).length, X.cfAiLines({forecast: {horizons: [{minutes: 1, expected: 0.1, status: 'ok'}]}}, 900, 26).length, X.cfAiLines({forecast: {horizons: [{minutes: 15, expected: 0.1, status: 'error'}, {minutes: 15, expected: -1, status: 'ok'}]}}, 900, 26).length];
console.log(JSON.stringify(out));
"""


def run():
    r = subprocess.run([NODE, "-e", HARNESS, str(JS)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_common_rules_apply_the_same_way_to_every_source():
    o = run()
    assert o["hooked"] is True
    ok = o["ok"]
    assert ok["ok"] and ok["mid"] == 99.65 and ok["stop"] == 98.8 and ok["t1"] == 101.5 and ok["t2"] == 103 and ok["rr"] == 2.1765 and not ok["poor"] and not ok["widened"] and not ok["capped"]
    assert not o["far"]["ok"] and "멀어" in o["far"]["why"]                              # 진입 구간이 3 ATR 넘게 멀면 무효
    assert o["widen"]["ok"] and o["widen"]["widened"] and o["widen"]["stop"] == 98.85 and o["widen"]["rr"] == 2.3125   # 손절이 0.8 ATR 보다 좁으면 넓힌다
    assert not o["wide"]["ok"] and "넓어" in o["wide"]["why"]                            # 3.5 ATR 넘으면 무효
    assert not o["inside"]["ok"] and "안쪽" in o["inside"]["why"]
    assert o["cap"]["ok"] and o["cap"]["capped"] and o["cap"]["t1"] == 104.15            # 목표는 4.5 ATR 안으로
    assert not o["notgt"]["ok"] and "목표" in o["notgt"]["why"]
    assert o["pct_ok"]["ok"] and not o["pct_far"]["ok"]                                  # ATR 이 없으면 퍼센트 한도(8%)
    assert o["inzone"]["inZone"] is True and o["inzone"]["distPct"] == 0
    assert o["short"]["ok"] and o["short"]["side"] == "short" and o["short"]["t1"] == 98.5 and o["short"]["rr"] == 1.7619
    assert o["poor"]["ok"] and o["poor"]["poor"] is True                                 # 손익비 1.2 미만은 표시하되 '낮음'
    assert o["none"] == [False, False]


def test_ai_forecast_is_matched_by_horizon_and_its_verdict_warns_when_there_is_no_edge():
    o = run()
    a = o["ai"]
    assert a[0]["side"] == "long" and a[0]["label"] == "상승" and a[0]["expected"] == 0.1031                 # 15분 = 15분 예측
    assert a[1] == {"why": "봉 부족"} and a[2] is None and a[3] is None and a[4] is None                    # 1시간은 예측 불가 이유, 30분·없는 값은 null
    assert a[5]["side"] is None and a[5]["label"] == "횡보"
    v = o["verdict"]
    assert v[0]["bad"] is True and "50.5%" in v[0]["text"] and "0.04%" in v[0]["text"] and "-98.2%" in v[0]["text"] and "우위가 확인되지 않았습니다" in v[0]["text"]
    assert v[1]["bad"] is False and "표본 밖 검증" in v[1]["text"]
    assert v[2] is None and v[3] is None


def test_ai_forecast_lines_skip_horizons_shorter_than_a_bar_and_limit_the_trail_to_the_future_margin():
    o = run()
    # 15분 차트(900초): 1분 예측은 한 봉 안이라 제외, 30분은 상태가 나빠 제외, 가까운 것부터 최대 4개
    assert o["lines"] == [[15, "15분", "long", 0.1031, 1, True], [60, "1시간", "short", 0.102, 4, True], [240, "4시간", None, 0.1026, 16, True], [1440, "1일", "long", 0.11, 96, False]]
    # 실제 ENA 응답(1·3·5·15·30분·1·4시간·1일·1주·1달): 15분 차트에서는 1분·3분·5분(한 봉보다 짧음)과, 이미 고른 가격과 0.1% 이내인 30분·4시간 예측을 빼고 5개를 그린다
    assert o["real"] == [[15, "short", 1, True], [60, None, 4, True], [1440, "long", 96, False], [10080, "short", 672, False], [43200, "short", 2880, False]]
    assert o["real_1m"] == [1, 1440, 10080, 43200]                                         # 1분 차트: 1분 예측부터, 3·5·15·30·60분·4시간은 1분 예측과 0.1% 이내라 제외
    assert o["many"] == [15, 60, 10080, 43200, 129600]                                      # 서로 다른 가격이 5개를 넘으면 가까운 2개 + 가장 먼 3개
    u = o["v_user"]                                                                          # 실제 화면의 상황: 15분 지표 롱·1시간 지표 롱·4시간 지표/엘리엇 숏
    assert u["head"] == "short" and u["big"] == ["short", -5] and u["small"] == ["wait", 1]
    assert u["states"] == ["15m:single:long", "30m:none:-", "1h:single:long", "4h:agree:short", "1d:none:-"] and u["conflicts"] == [] and "큰 흐름 숏, 단타는 대기" in u["text"]
    c = o["v_conflict"]
    assert c["conflicts"] == ["1h"] and c["head"] == "wait" and c["big"] == ["wait", 0] and "관망" in c["text"]           # 같은 시간봉에서 반대면 0점
    assert o["v_none"]["head"] == "wait" and o["v_none"]["text"] == "뚜렷한 방향이 없습니다 → 관망"
    s = o["v_same"]
    assert s["head"] == "long" and s["big"][0] == "long" and s["small"] == ["long", 6] and "롱 우세" in s["text"]       # 30분 일치 4점 + 15분 일치 2점 = 6
    d = o["v_opp"]
    assert d["head"] == "long" and d["big"][0] == "long" and d["small"][0] == "short" and "역추세" in d["text"]
    so = o["v_small_only"]
    assert so["head"] == "long" and so["big"] == ["wait", 0] and so["small"][0] == "long" and "큰 흐름은 불분명" in so["text"]
    r0 = o["range"][0]
    assert r0["lo"] == 0.09357405 and r0["hi"] == 0.10382156 and o["range"][1:] == [None, None, None]        # 축을 넓힐 범위: 가장 낮은(1달) ~ 가장 높은(1일) 예측, 현재가 포함. 선이 없거나 범위가 0이면 없음
    assert o["lines_empty"] == [0, 0, 0]                                                   # 데이터 없음·너무 짧음·오류/잘못된 가격은 그리지 않는다


def test_confluence_clusters_group_nearby_entries_of_the_same_side():
    o = run()
    assert o["cl"] == [["long", 3, ["entry", "levels"], 100.2333]]                     # 100·100.3·100.4(0.6% 이내) 한 묶음, 102 와 숏은 따로
    assert o["cl2"] == 0                                                                 # 방향이 다르면 겹쳐도 합의가 아님


def test_wire_script_requires_fractal_and_entry_blocks_then_inserts_and_updates(tmp_path):
    root = tmp_path / "p"
    (root / "ena_bot/dashboard_static").mkdir(parents=True)
    (root / "tools").mkdir()
    shutil.copy(JS, root / "tools" / "confluence.js")
    shutil.copy(TOOLS / "wire_confluence.py", root / "tools" / "wire_confluence.py")
    page = root / "ena_bot/dashboard_static/index.html"
    run_wire = lambda: subprocess.run(["python3", str(root / "tools" / "wire_confluence.py"), str(root)], capture_output=True, text=True)
    page.write_text("<script>let TRNC = null;</script>")
    r = run_wire()
    assert r.returncode != 0 and "wire_fractal_chart" in (r.stderr + r.stdout) and "cfNorm" not in page.read_text()
    page.write_text("<script>\nfunction frcDraw(){}\n// ---- 프랙탈 엘리엇: x ----\n// ---- /프랙탈 엘리엇 ----\nlet TRNC = null;\n</script>")
    r = run_wire()
    assert r.returncode != 0 and "wire_entry_plan" in (r.stderr + r.stdout)
    page.write_text("<script>\nconst ENT = {};\nfunction frcDraw(){}\n// ---- 프랙탈 엘리엇: x ----\n// ---- /프랙탈 엘리엇 ----\nlet TRNC = null;\n</script>")
    r = run_wire()
    t = page.read_text()
    assert "수정" in r.stdout and t.count("function cfNorm") == 1 and t.index("// ---- 가격 종합:") > t.index("/프랙탈 엘리엇")
    assert "이미 최신" in run_wire().stdout
    (root / "tools" / "confluence.js").write_text(JS.read_text().replace("가격 종합 — 지표", "가격 종합(새) — 지표"))
    assert "수정" in run_wire().stdout and "가격 종합(새)" in page.read_text() and page.read_text().count("function cfNorm") == 1
