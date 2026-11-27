import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node 가 필요합니다")

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const sb = {console, document: {getElementById() { return null; }}, localStorage: {getItem() { return null; }, setItem() {}}, structDraw: function () {}, setInterval() {}, setTimeout() {}};
vm.createContext(sb);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8') + '\n' + fs.readFileSync(process.argv[2], 'utf8') + ';this.X = {cfVerdict, hubChecklist, hubPick, hubSize, hubCostR, hubPlans};', sb);
const X = sb.X, out = {};
const cell = (side, mid, stop, t1, o) => Object.assign({ok: true, side, lo: mid - 0.2, hi: mid + 0.2, mid, stop, t1, t2: null, rr: Math.abs(t1 - mid) / Math.abs(mid - stop), poor: false, inZone: false, distPct: 0.003}, o);
const row = (tf, ind, ell, cells, ai) => ({tf, op: {ind, ell}, cells: cells || {}, ai: ai || null});
const mkRes = (rows, clusters) => ({rows, clusters: clusters || [], verdict: X.cfVerdict(rows.map(r => r.op).map((o, i) => ({tf: rows[i].tf, ind: o.ind, ell: o.ell})))});
const members = (arr) => arr.map(([tf, method, n]) => ({tf, method, n}));
// A: 모든 조건 충족
const eA = cell('short', 100, 102, 96, {inZone: true}), fA = cell('short', 100.2, 102.2, 96), lA = cell('short', 100.1, 102.1, 95);
const resA = mkRes([row('1d', null, null), row('4h', 'short', 'short', {entry: eA, fractal: fA, levels: lA}, {side: 'short'}), row('1h', 'short', null), row('30m', null, null), row('15m', 'short', 'short')],
  [{side: 'short', mid: 100.1, lo: 99.8, hi: 100.4, members: members([['4h', 'entry', eA], ['4h', 'fractal', fA], ['4h', 'levels', lA]]), kinds: ['entry', 'fractal', 'levels']}]);
const cA = X.hubChecklist(resA, 100);
out.A = {side: cA.side, pass: cA.pass, total: cA.total, status: cA.status, ai: cA.ai, best: cA.best && [cA.best.tf, cA.best.method], fails: cA.items.filter(i => !i.ok).map(i => i.key)};
// B: 방향 없음(관망)
const resB = mkRes([row('4h', 'short', null), row('1h', 'long', null), row('15m', 'long', null)]);
const cB = X.hubChecklist(resB, 100); out.B = {side: cB.side, status: cB.status, n: cB.items.length};
// C: 손익비 부족이면 점수가 높아도 '진입 검토 가능'이 아니다
const eC = cell('short', 100, 104, 103), fC = cell('short', 100.2, 104.2, 103.2);
const resC = mkRes([row('4h', 'short', 'short', {entry: eC, fractal: fC})], [{side: 'short', mid: 100.1, lo: 99.8, hi: 100.4, members: members([['4h', 'entry', eC], ['4h', 'fractal', fC]]), kinds: ['entry', 'fractal']}]);
const cC = X.hubChecklist(resC, 100); out.C = {pass: cC.pass, status: cC.status, fails: cC.items.filter(i => !i.ok).map(i => i.key)};
// D: 큰 흐름이 반대(단타만 롱) → 큰 흐름 항목 ✗, 준비 안 됨
const eD = cell('long', 100, 98, 104, {inZone: true}), fD = cell('long', 100.1, 98.1, 104.1);
const resD = mkRes([row('4h', null, null), row('30m', 'long', 'long', {entry: eD, fractal: fD}), row('15m', 'long', 'long')], [{side: 'long', mid: 100, lo: 99.8, hi: 100.3, members: members([['30m', 'entry', eD], ['30m', 'fractal', fD]]), kinds: ['entry', 'fractal']}]);
const cD = X.hubChecklist(resD, 100); out.D = {side: cD.side, status: cD.status, fails: cD.items.filter(i => !i.ok).map(i => i.key)};
// 충돌한 시간봉이 있으면 noconf ✗
const resE = mkRes([row('4h', 'short', 'short', {entry: eA}), row('1h', 'long', 'short')]);
out.E = X.hubChecklist(resE, 100).items.find(i => i.key === 'noconf');
// 추천: 합의 구간 안 + 지금 구간 안 + 손익비 좋은 것
out.pick = X.hubPick(resA, 'short') && [X.hubPick(resA, 'short').tf, X.hubPick(resA, 'short').method];
out.pick_none = X.hubPick(resA, 'long');
const r2 = (x) => x === null ? null : Math.round(x * 1e6) / 1e6;
const sz = X.hubSize(1000, 1, {mid: 100, stop: 102});
out.size = [r2(sz.riskUsd), r2(sz.qty), r2(sz.notional), r2(sz.lev)];
out.size_bad = [X.hubSize(0, 1, {mid: 1, stop: 2}), X.hubSize(100, 0, {mid: 1, stop: 2}), X.hubSize(100, 1, null), X.hubSize(100, 1, {mid: 1, stop: 1})];
out.cost = [r2(X.hubCostR({mid: 100, stop: 102})), X.hubCostR({mid: 100, stop: 100})];
console.log(JSON.stringify(out));
"""


def run():
    r = subprocess.run([NODE, "-e", HARNESS, str(TOOLS / "confluence.js"), str(TOOLS / "hub.js")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_checklist_ready_wait_and_none():
    o = run()
    assert o["A"] == {"side": "short", "pass": 9, "total": 9, "status": "ready", "ai": "same", "best": ["4h", "entry"], "fails": []}
    assert o["B"] == {"side": None, "status": "none", "n": 0}                                   # 방향이 없으면 체크리스트를 적용하지 않는다
    c = o["C"]
    assert c["status"] != "ready" and "rr" in c["fails"]                                         # 손익비가 모자라면 다른 조건이 많이 맞아도 진입 검토 가능이 아니다
    d = o["D"]
    assert d["side"] == "long" and d["status"] != "ready" and "big" in d["fails"]                # 큰 시간봉의 거부권: 큰 흐름 근거 없이 단타만으로는 준비 안 됨
    assert o["E"]["ok"] is False and "1h" in o["E"]["note"]


def test_pick_and_position_size_and_cost():
    o = run()
    assert o["pick"] == ["4h", "entry"] and o["pick_none"] is None
    assert o["size"] == [10, 5, 500, 0.5]                                                        # 계좌 1000 × 1% = 10 ÷ 손절거리 2 = 수량 5, 명목 500, 레버리지 0.5
    assert o["size_bad"] == [None, None, None, None]
    assert o["cost"][0] == 0.065 and o["cost"][1] is None                                        # 왕복 비용 0.13% × 100 ÷ 2 = 0.065(위험의 6.5%), 손절 거리 0이면 무한대(JSON 에서 null)


def test_wire_script_inserts_after_confluence_updates_and_refuses_without_it(tmp_path):
    root = tmp_path / "p"
    (root / "ena_bot/dashboard_static").mkdir(parents=True)
    (root / "tools").mkdir()
    shutil.copy(TOOLS / "hub.js", root / "tools" / "hub.js")
    shutil.copy(TOOLS / "wire_hub.py", root / "tools" / "wire_hub.py")
    page = root / "ena_bot/dashboard_static/index.html"
    wire = lambda: subprocess.run([sys.executable, str(root / "tools" / "wire_hub.py"), str(root)], capture_output=True, text=True)
    page.write_text("<script>let TRNC = null;</script>")
    r = wire()
    assert r.returncode != 0 and "wire_confluence" in (r.stderr + r.stdout) and "hubChecklist" not in page.read_text()
    page.write_text("<script>\nfunction cfNorm(){}\nif (typeof hubEnsure === 'function') hubEnsure();\n// ---- 가격 종합: x ----\n// ---- /가격 종합 ----\nlet TRNC = null;\n</script>")
    r = wire()
    t = page.read_text()
    assert "수정" in r.stdout and t.count("function hubChecklist") == 1 and t.index("// ---- 분석 허브:") > t.index("/가격 종합")
    assert "이미 최신" in wire().stdout
    (root / "tools" / "hub.js").write_text((TOOLS / "hub.js").read_text().replace("분석 허브'; ttl", "분석 허브(새)'; ttl"))
    assert "수정" in wire().stdout and "분석 허브(새)" in page.read_text() and page.read_text().count("function hubChecklist") == 1


USM_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const sb = {console, document: {getElementById() { return null; }}, localStorage: {getItem() { return null; }, setItem() {}}, structDraw: function () {}, setInterval() {}, setTimeout() {}};
vm.createContext(sb);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8') + '\n' + fs.readFileSync(process.argv[2], 'utf8') + ';this.X = {hubUsmText};', sb);
const t = sb.X.hubUsmText, out = {};
out.none = [t(null), t({items: []}), t({items: [], error: 'curl 22'})];
out.ok = t({items: [{symbol: 'QQQ', label: 'QQQ(나스닥100 ETF)', pct_day: -1.234, pct_1h: -0.4, age_sec: 30}, {symbol: 'NQ=F', label: '나스닥100 선물', pct_day: -1.5, pct_1h: null, age_sec: 60}]});
out.stale = t({items: [{symbol: 'QQQ', label: 'QQQ(x)', pct_day: 0.3, pct_1h: null, age_sec: 3600}]});
console.log(JSON.stringify(out));
"""


def test_us_stock_line_shows_values_staleness_and_errors():
    r = subprocess.run([NODE, "-e", USM_HARNESS, str(TOOLS / "confluence.js"), str(TOOLS / "hub.js")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["none"][0] is None and o["none"][1] is None and "받지 못했습니다" in o["none"][2]
    assert "QQQ 오늘 -1.23%(최근 1시간 -0.40%)" in o["ok"] and "나스닥100 선물 오늘 -1.50%" in o["ok"] and "분 전 값" not in o["ok"]
    assert "60분 전 값" in o["stale"]
