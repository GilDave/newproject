"""차트 분석 탭의 카드를 격자(카드 형식)로 배치한다: 아서 헤이즈 카드들은 나란히, 긴 표·차트는 전체 폭. index.html 2곳(스타일·스크립트).
사용: .venv/bin/python tools/wire_chart_grid.py [프로젝트 폴더]   (몇 번 실행해도 안전: 표시 사이의 내용만 교체)
데이터·기능은 건드리지 않고 배치만 바꾼다. 되돌리려면 'CHART-GRID-START'~'END' 두 블록을 지우면 된다.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
idx = root / "ena_bot" / "dashboard_static" / "index.html"
if not idx.exists():
    raise SystemExit(f"index.html 을 찾지 못했습니다: {idx}")
src = idx.read_text(encoding="utf-8")

CSS = """<!-- CHART-GRID-START -->
<style id="chart-grid-style">
#vp-chart:not([hidden]){display:grid;grid-template-columns:minmax(0,1fr);gap:10px;align-items:start}
#vp-chart>*{grid-column:1/-1;min-width:0;margin-bottom:0;order:3}
#vp-chart>#trnbox{order:0}
#vp-chart>#entry-card{order:1}
#vp-chart>.hayes,#vp-chart>#hayes-card,#vp-chart>#hayes-ins,#vp-chart>#hayes-lookup{grid-column:auto;order:2;height:100%;box-sizing:border-box;margin-bottom:0}
@media(min-width:760px){#vp-chart:not([hidden]){grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(min-width:1300px){#vp-chart:not([hidden]){grid-template-columns:repeat(3,minmax(0,1fr))}}
#vp-chart .hayes-val{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
</style>
<!-- CHART-GRID-END -->"""
JS = """<!-- CHART-GRID-SCRIPT-START -->
<script>
(function(){
  /* 아서 헤이즈 보유량의 긴 소수점(451672.4369910842…)을 451,672.44 로 줄이고 원래 값은 마우스를 올리면 보이게 한다 */
  const re = /^\\s*(\\d+\\.\\d{3,})\\s*(ENA)\\s*$/;
  function fmt(){
    document.querySelectorAll('#vp-chart .hayes-val').forEach(function(el){
      const m = re.exec(el.textContent); if (!m) return;
      const n = Number(m[1]); if (!isFinite(n)) return;
      const t = n.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' ' + m[2];
      if (t !== el.textContent) { el.title = m[1] + ' ' + m[2]; el.textContent = t; }
    });
  }
  const p = document.getElementById('vp-chart'); if (!p) return;
  new MutationObserver(fmt).observe(p, {childList: true, subtree: true, characterData: true}); fmt();
})();
</script>
<!-- CHART-GRID-SCRIPT-END -->"""

def put(text, start, end, block, anchor):
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if pat.search(text):
        return pat.sub(lambda m: block, text, count=1)
    if text.count(anchor) != 1:
        raise SystemExit(f"{anchor} 를 한 곳에서만 찾지 못했습니다(발견 {text.count(anchor)}곳). 아무것도 바꾸지 않았습니다.")
    return text.replace(anchor, block + "\n" + anchor)

new = put(src, "<!-- CHART-GRID-START -->", "<!-- CHART-GRID-END -->", CSS, "</head>")
new = put(new, "<!-- CHART-GRID-SCRIPT-START -->", "<!-- CHART-GRID-SCRIPT-END -->", JS, "</body>")
if new == src:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
tmp = idx.with_suffix(".html.tmp")
tmp.write_text(new, encoding="utf-8")
tmp.replace(idx)
print("수정: index.html (차트 분석 카드 격자 배치)")
