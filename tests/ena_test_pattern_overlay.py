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
    for word in ("patDraw", "patFetch", "patBox", "매매 신호 아님", "교과서상 방향", "확인되지 않았습니다"):
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
