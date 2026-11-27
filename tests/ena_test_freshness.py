"""ENA 저장소에서 실행하는 테스트(wire_freshness.py 적용 후 tests/ 로 복사). test_dashboard.py 의 도우미(_setup)를 재사용한다."""
import json
import re
import subprocess
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from test_dashboard import _setup

from ena_bot.dashboard import make_handler

PAGE = Path(__file__).resolve().parents[1] / "ena_bot/dashboard_static/index.html"


def test_state_sources_carry_staleness_and_input_counts(tmp_path):
    cfg, cl, hub, st = _setup(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg, cl, hub, store_factory=lambda: st))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        j = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/state").read())
    finally:
        srv.shutdown()
    s = j["sources"]
    assert s["trend"]["stale_after"] == 300 and s["trend"]["inputs"] == {"ok": 1, "total": 1}
    assert s["news"]["stale_after"] == 21600 and set(s["news"]["inputs"]) == {"ok", "total"}
    assert "stale_after" not in s["calendar"] and "inputs" not in s["calendar"]       # 알 수 없는 칸에는 억지로 붙이지 않는다
    assert {"source", "updated_at"} <= set(s["trend"])                                  # 기존 필드 유지


def test_page_keeps_the_original_source_text_and_adds_freshness():
    html = PAGE.read_text(encoding="utf-8")
    assert "출처 ${m && m.source" in html and "· 갱신 ${m && m.updated_at != null ? kst(m.updated_at) : '확인 불가'}" in html
    for word in ("ageParts", "ageSpan", "inputsSpan", "fresh-badge", "tickFresh", "신선", "지연", "오래됨", "입력 소스"):
        assert word in html


def test_age_classification_in_node(tmp_path):
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r"const AGE_DEFAULT_STALE.*?\nfunction ageSpan", html, re.S)
    js = m.group(0).rsplit("\nfunction ageSpan", 1)[0]
    script = tmp_path / "age.js"
    script.write_text(js + """
const now = Date.now(); const out = [];
out.push(ageParts({updated_at: now - 10_000, stale_after: 300}).cls);        // 10초 → 신선
out.push(ageParts({updated_at: now - 400_000, stale_after: 300}).cls);       // 6.7분 → 지연(기준 초과, 3배 이내)
out.push(ageParts({updated_at: now - 1_000_000, stale_after: 300}).cls);     // 16분 → 오래됨(3배 초과)
out.push(ageParts({updated_at: now - 7_200_000}).text);                      // 기본 기준(600초), 2시간
out.push(String(ageParts({updated_at: null})));
out.push(ageParts({updated_at: now - 5_000}).text);
console.log(JSON.stringify(out));
""")
    res = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out[:3] == ["fresh", "late", "old"] and out[3] == "2.0시간 전 · 오래됨" and out[4] == "null" and out[5].endswith("초 전 · 신선")
