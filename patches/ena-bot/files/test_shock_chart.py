import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from ena_bot import shock as sk

TOOLS = Path(__file__).resolve().parents[1] / "tools"
JS = TOOLS / "shock_chart.js"
NODE = shutil.which("node")


def dataset(seed=3, n=900):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.004, n)
    r[400] = 0.29                                           # 장대 양봉
    r[600] = -0.22                                          # 장대 음봉
    r[601] = -0.12                                          # 이어진 봉(하나의 사건으로 합쳐진다)
    c = 0.5 * np.exp(np.cumsum(r))
    o = np.concatenate([[0.5], c[:-1]])
    sp = np.abs(rng.normal(0, 0.003, n))
    h = np.maximum(o, c) * np.exp(sp)
    l = np.minimum(o, c) * np.exp(-sp)
    h[750] = o[750] * 1.4                                   # 꼬리 급등락
    v = np.exp(rng.normal(8, 0.3, n))
    v[400] *= 30
    return o, h, l, c, v


@pytest.mark.skipif(NODE is None, reason="node 가 없어 JS 대조를 건너뜀")
def test_the_chart_javascript_matches_the_python_rules(tmp_path):
    o, h, l, c, v = dataset()
    bars = [{"time": 1_700_000_000 + i * 14400, "open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": v[i]} for i in range(len(c))]
    py = sk.detect_shocks(o, h, l, c, v)
    assert [e["i"] for e in py] == [400, 600, 750]
    script = tmp_path / "run.js"
    script.write_text(JS.read_text(encoding="utf-8") + "\nconst bars_ = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));"
                      "\nconst ev = detectShocks(bars_); console.log(JSON.stringify(ev.map(e => ({...e, text: shockText(e)}))));", encoding="utf-8")
    data = tmp_path / "bars.json"
    data.write_text(json.dumps(bars))
    out = subprocess.run([NODE, str(script), str(data)], capture_output=True, text=True, check=True).stdout
    js = json.loads(out)
    assert [e["i"] for e in js] == [e["i"] for e in py]
    for a, b in zip(js, py):
        assert a["direction"] == b["direction"] and a["kind"] == b["kind"]
        for ja, pk in (("ret", "ret"), ("rangePct", "range_pct"), ("zRange", "z_range"), ("zBody", "z_body"), ("zVolume", "z_volume"), ("upper", "upper_wick"), ("lower", "lower_wick")):
            assert a[ja] == pytest.approx(b[pk], rel=1e-9, abs=1e-9), (a["i"], ja)
        assert a["text"] == sk.describe(b)                  # 화면 문구도 Python 과 같다


@pytest.mark.skipif(NODE is None, reason="node 가 없어 JS 대조를 건너뜀")
def test_markers_and_tip_use_the_loaded_bars_only(tmp_path):
    o, h, l, c, v = dataset()
    bars = [{"time": 1_700_000_000 + i * 14400, "open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": v[i]} for i in range(len(c))]
    script = tmp_path / "run2.js"
    script.write_text(JS.read_text(encoding="utf-8") + "\nconst bars = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));"
                      "\nconst m = shockMarkers(); const t = shockTip(bars[400].time); const none = shockTip(bars[10].time) + shockTip(null);"
                      "\nbars.length = 300; const after = shockMarkers().length;"
                      "\nconsole.log(JSON.stringify({m, t, none, after}));", encoding="utf-8")
    data = tmp_path / "bars2.json"
    data.write_text(json.dumps(bars))
    res = json.loads(subprocess.run([NODE, str(script), str(data)], capture_output=True, text=True, check=True).stdout)
    assert len(res["m"]) == 3 and res["m"][0]["text"] == "⚡+34%" and res["m"][1]["text"].startswith("⚡-") and res["m"][2]["time"] == bars[750]["time"]
    assert res["m"][0]["position"] == "aboveBar" and res["m"][1]["position"] == "belowBar" and res["m"][0]["time"] == bars[400]["time"]
    assert "장대 양봉" in res["t"] and "원인 미확인" in res["t"] and res["none"] == "" and res["after"] == 0     # 300봉만 있으면 400번 봉의 표시는 없다


def test_wire_tool_is_atomic_and_idempotent(tmp_path):
    (tmp_path / "ena_bot/dashboard_static").mkdir(parents=True)
    page = tmp_path / "ena_bot/dashboard_static/index.html"
    html = ("<script>\nfunction trackCrosshair(p) {\n  const tp = $('#trntip'), h = (TRNC && hoverTime != null) ? TRNC.tip(hoverTime) : '';\n}\n"
            "let TRNC = null;   // makeTrnChart 인스턴스(스크립트 끝에서 생성)\nfunction paintMarkers() {\n  const ms = [];\n  if (TRNC) ms.push(...TRNC.markers());\n"
            "  ms.sort((a, b) => a.time - b.time); candle.setMarkers(ms);\n}\n</script>")
    page.write_text(html)
    run = lambda: subprocess.run([sys.executable, str(TOOLS / "wire_shock_chart.py"), str(tmp_path)], capture_output=True, text=True)
    r = run()
    assert r.returncode == 0, r.stderr
    out = page.read_text()
    assert out.count("function detectShocks") == 1 and "shockMarkers()" in out and "+ shockTip(hoverTime)" in out and out.index("function detectShocks") < out.index("let TRNC = null")
    assert "이미 적용됨" in run().stdout and page.read_text() == out
    page.write_text(html.replace("TRNC.tip(hoverTime)", "TRNC.tip2(hoverTime)"))
    before = page.read_text()
    assert run().returncode != 0 and page.read_text() == before


# ---------------------------------------------------------------- 과거 통계 한 줄 + 엔드포인트
def saved(tmp_path, h6_use=True, h1_use=True, generated=None):
    import time as _t
    from ena_bot import shock_study as ss
    hz = {1: {"n": 501, "n_clusters": 210, "n_coins": 18, "mean": -0.0122, "median": -0.01, "continue_rate": 0.4, "ci": [-0.02, -0.004]},
          6: {"n": 496, "n_clusters": 205, "n_coins": 18, "mean": -0.0196, "median": -0.02, "continue_rate": 0.4, "ci": [-0.03, -0.008]},
          18: {"n": 490, "n_clusters": 200, "n_coins": 18, "mean": -0.01, "median": -0.01, "continue_rate": 0.47, "ci": [-0.03, 0.01]}}
    res = {"factor": 4, "coins": [f"C{i}" for i in range(18)], "study": {"events": 675, "alpha": 0.0125, "horizons": hz},
           "verdicts": {1: {"use": h1_use, "why": "x"}, 6: {"use": h6_use, "why": "y"}, 18: {"use": False, "why": "z"}}, "generated": int(generated or _t.time())}
    (tmp_path / "shock_study.json").write_text(json.dumps(res))
    return ss.load_saved(tmp_path)


def test_saved_study_is_summarised_for_the_page_and_broken_files_are_harmless(tmp_path):
    from ena_bot import shock_study as ss
    assert ss.load_saved(tmp_path) == {"ok": False}
    j = saved(tmp_path)
    assert j["ok"] and j["coins"] == 18 and j["events"] == 675 and j["verdicts"]["6"]["use"] is True and j["horizons"]["6"]["n_clusters"] == 205 and j["age_days"] < 1
    (tmp_path / "shock_study.json").write_text("{broken")
    assert ss.load_saved(tmp_path) == {"ok": False}


@pytest.mark.skipif(NODE is None, reason="node 가 없어 JS 대조를 건너뜀")
def test_the_stats_line_only_shows_what_passed_the_bar_and_only_on_4h_charts(tmp_path):
    def line(stats, iv):
        src = ("globalThis.fetch = () => new Promise(() => {});\n" + (TOOLS / "shock_stats.js").read_text(encoding="utf-8")
               + f"\nglobalThis.apiIv = () => '{iv}';\nSHK_STATS = {json.dumps(stats)};\nconsole.log(JSON.stringify(shockStatsLine()));")
        f = tmp_path / "s.js"
        f.write_text(src, encoding="utf-8")
        return json.loads(subprocess.run([NODE, str(f)], capture_output=True, text=True, check=True).stdout)
    both = line(saved(tmp_path), "240")
    assert "1봉 뒤 평균 -1.2%" in both and "6봉 뒤 평균 -2.0%" in both and "되돌리는 경향" in both and "서로 다른 날 205개" in both and "참고용 · 확정 아님" in both
    only6 = line(saved(tmp_path, h1_use=False), "240")
    assert "1봉 뒤" not in only6 and "6봉 뒤 평균 -2.0%" in only6
    none = line(saved(tmp_path, h1_use=False, h6_use=False), "240")
    assert "반영하지 않음" in none and "봉 뒤 평균" not in none
    assert line(saved(tmp_path), "60") == "" and line({"ok": False}, "240") == ""             # 다른 시간 단위·통계 없음: 아무것도 붙이지 않는다


def test_stats_wire_tool_needs_the_chart_wire_first_and_is_atomic(tmp_path):
    (tmp_path / "ena_bot/dashboard_static").mkdir(parents=True)
    dash, page = tmp_path / "ena_bot/dashboard.py", tmp_path / "ena_bot/dashboard_static/index.html"
    dash.write_text("from .hayes_holdings import DEFAULT_INTERVAL_MIN\nfrom . import hayes_lookup\nclass H:\n    def do_GET(self):\n        try:\n            if 0:\n                pass\n"
                    '                elif url.path == "/healthz":\n                pass\n        except ValueError:\n            pass\n')
    html = ("<script>\nfunction trackCrosshair(p) {\n  const tp = $('#trntip'), h = (TRNC && hoverTime != null) ? TRNC.tip(hoverTime) : '';\n}\n"
            "let TRNC = null;   // makeTrnChart 인스턴스(스크립트 끝에서 생성)\nfunction paintMarkers() {\n  const ms = [];\n  if (TRNC) ms.push(...TRNC.markers());\n"
            "  ms.sort((a, b) => a.time - b.time); candle.setMarkers(ms);\n}\n</script>")
    page.write_text(html)
    run = lambda name: subprocess.run([sys.executable, str(TOOLS / name), str(tmp_path)], capture_output=True, text=True)
    first = run("wire_shock_stats.py")
    assert first.returncode != 0 and "wire_shock_chart.py" in first.stderr and page.read_text() == html      # 차트 연결이 먼저
    assert run("wire_shock_chart.py").returncode == 0
    d0, p0 = dash.read_text(), page.read_text()
    r = run("wire_shock_stats.py")
    assert r.returncode == 0, r.stderr
    d1, p1 = dash.read_text(), page.read_text()
    assert "from . import shock_study" in d1 and "shock_study.load_saved(cfg.data_path)" in d1 and "+ shockStatsLine() : ''" in p1 and p1.count("function shockStatsLine") == 1
    assert "이미 적용됨" in run("wire_shock_stats.py").stdout and dash.read_text() == d1 and page.read_text() == p1
    dash.write_text(d0.replace('"/healthz"', '"/health"'))
    page.write_text(p0)
    r2 = run("wire_shock_stats.py")
    assert r2.returncode != 0 and page.read_text() == p0                                                       # 한 곳이라도 못 찾으면 둘 다 그대로
