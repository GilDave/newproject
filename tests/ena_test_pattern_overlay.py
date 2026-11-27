"""ENA 저장소에서 실행하는 테스트(wire_pattern_overlay.py 적용 후 tests/ 로 복사). 감지기 정확성은 이 저장소의 tests/test_ena_patterns.py 가 확인한다."""
import json
import re
import subprocess
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

from test_dashboard import _setup

from ena_bot.dashboard import make_handler

PAGE = Path(__file__).resolve().parents[1] / "ena_bot/dashboard_static/index.html"
H1 = 3_600_000


class KlineClient:
    """Bybit 형식(최신이 먼저)으로 쌍바닥이 들어 있는 1시간봉을 돌려준다."""
    def __init__(self):
        c = np.interp(np.arange(0, 240), [0, 150, 165, 180, 200, 230, 239], [100, 90, 100, 90, 95, 108, 108])
        self.c = np.r_[c, np.full(20, 108.0)]
        self.now = 1_800_000_000_000 // H1 * H1

    def _request(self, method, path, params, signed=False):
        assert path == "/v5/market/kline"
        n = len(self.c)
        rows = [[str(self.now - (n - i) * H1), str(self.c[i] - 0.1), str(self.c[i] + 0.3), str(self.c[i] - 0.3), str(self.c[i]), "1", "x"] for i in range(n)]
        return {"list": list(reversed(rows))}


def _get(cfg, client, hub, st, path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg, client, hub, store_factory=lambda: st))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}{path}").read())
    finally:
        srv.shutdown()


def test_route_returns_supported_payload_for_hourly_and_explains_other_intervals(tmp_path, monkeypatch):
    cfg, cl, hub, st = _setup(tmp_path)
    import ena_bot.pattern_api as pa
    real = pa.payload
    monkeypatch.setattr(pa, "payload", lambda fetch, sym, iv, now_ms, **k: real(fetch, sym, iv, KlineClient().now + 10_000, **k))   # 가짜 시세의 시각에 맞춘다
    j = _get(cfg, KlineClient(), hub, st, "/api/patterns?symbol=ENAUSDT&interval=60")
    assert j["supported"] is True and j["tf"] == "1h" and any(g["pat"] == "P0W" for g in j["patterns"])
    j = _get(cfg, KlineClient(), hub, st, "/api/patterns?symbol=ENAUSDT&interval=D")
    assert j["supported"] is False and "1시간·4시간" in j["reason"]


def test_page_wires_the_overlay_after_the_other_chart_blocks_and_stays_neutral():
    html = PAGE.read_text(encoding="utf-8")
    assert html.count("// ---- 차트 패턴 감지(참고용)") == 1 and "structDraw.__pt" in html
    for word in ("patDraw", "patFetch", "patBox", "매매 신호 아님", "교과서상 방향", "확인되지 않았습니다", "이(가) 뭔가요?", "교과서 해석", "롱·숏이 무슨 뜻인가요?", "PAT.open"):
        assert word in html


def test_stat_text_and_neutral_colors_in_node(tmp_path):
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r"function patStatText\(s\) \{.*?\n\}\n", html, re.S)
    script = tmp_path / "pat.js"
    script.write_text(m.group(0) + """
const a = patStatText({events: 3902, mean_net_pct: -0.33, ci95_pct: [-0.58, -0.08], verdict: '근거 없음'});
const b = patStatText({events: 12});
const c = patStatText(undefined);
const d = patStatText({events: 150, mean_net_pct: 0.5, ci95_pct: [0.1, 0.9], verdict: '합격(전진검증 대상)'});
console.log(JSON.stringify([a, b, c, d]));
""")
    res = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    a, b, c, d = json.loads(res.stdout)
    assert "3,902건" in a["text"] and "-0.33%" in a["text"] and "-0.58%" in a["text"] and a["bad"] is True
    assert "표본 부족" in b["text"] and c["text"] == "과거 통계 없음"
    assert d["bad"] is False                                    # 합격한 패턴만 경고색을 쓰지 않는다


def test_points_are_sorted_and_deduplicated_before_reaching_the_chart(tmp_path):
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r"function patSortedPts\(points, tmin\) \{.*?\n\}\n", html, re.S)
    script = tmp_path / "sort.js"
    script.write_text(m.group(0) + """
const out = patSortedPts([[3000, 3], [1000, 1], [2000, 2], [2000, 9], [500, 7], [4000, NaN]], 0.8);   // 시간(ms): 순서 뒤섞임, 중복, 범위 밖(0.5초), NaN
console.log(JSON.stringify(out));
""")
    res = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert [p["time"] for p in out] == [1, 2, 3]                       # 엄격히 오름차순, 중복·범위 밖·NaN 제거
    assert out[1]["value"] == 2                                          # 같은 시각이면 먼저 나온 값만 남긴다


def test_remove_flag_takes_the_overlay_out_and_leaves_a_valid_page(tmp_path):
    import shutil
    root = Path(__file__).resolve().parents[1]
    work = tmp_path / "ena"
    (work / "ena_bot/dashboard_static").mkdir(parents=True)
    shutil.copy(root / "ena_bot/dashboard_static/index.html", work / "ena_bot/dashboard_static/index.html")
    wire = root / "wire_pattern_overlay.py"
    before = (work / "ena_bot/dashboard_static/index.html").read_text(encoding="utf-8")
    assert "// ---- 차트 패턴 감지(참고용)" in before
    r = subprocess.run(["python3", str(wire), str(work), "--remove"], capture_output=True, text=True)
    assert r.returncode == 0 and "제거" in r.stdout, r.stdout + r.stderr
    after = (work / "ena_bot/dashboard_static/index.html").read_text(encoding="utf-8")
    assert "// ---- 차트 패턴 감지(참고용)" not in after and "patDraw" not in after
    main = max((x.group(1) for x in re.finditer(r"<script>(.*?)</script>", after, re.S)), key=len)
    (tmp_path / "m.js").write_text(main)
    assert subprocess.run(["node", "--check", str(tmp_path / "m.js")]).returncode == 0      # 제거 뒤에도 페이지 스크립트가 유효하다
    r2 = subprocess.run(["python3", str(wire), str(work), "--remove"], capture_output=True, text=True)
    assert "없어" in r2.stdout                                                                 # 다시 실행해도 안전
