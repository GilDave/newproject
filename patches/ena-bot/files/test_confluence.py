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
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8') + ';this.X = {cfNorm, cfClusters, CF_RULE, hooked: !!structDraw.__cf};', sb);
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
