"""차트 분석 탭의 아서 헤이즈 카드(보유량·변화 모니터링·주소 조회)를 한 번에 접고 펼치는 막대를 추가한다. 기본은 접힘.
사용: .venv/bin/python tools/wire_hayes_fold.py [프로젝트 폴더]   (몇 번 실행해도 안전: 표시 사이의 내용만 교체)
접은 상태는 이 브라우저에 기억된다. 데이터 수집·갱신은 그대로이고 화면에서만 숨긴다.
되돌리려면 index.html 에서 'HAYES-FOLD-START'~'END' 두 블록을 지운다.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
idx = root / "ena_bot" / "dashboard_static" / "index.html"
if not idx.exists():
    raise SystemExit(f"index.html 을 찾지 못했습니다: {idx}")
src = idx.read_text(encoding="utf-8")

CSS = """<!-- HAYES-FOLD-START -->
<style id="hayes-fold-style">
body.hayes-folded #vp-chart #hayes-card,body.hayes-folded #vp-chart #hayes-ins,body.hayes-folded #vp-chart #hayes-lookup{display:none!important}
#vp-chart>*{order:4}
#vp-chart>#trnbox{order:0}
#vp-chart>#entry-card{order:1}
#vp-chart>.hayes-fold{order:2;grid-column:1/-1;display:flex;align-items:center;gap:8px;padding:6px 12px;margin-bottom:10px;cursor:pointer;font-size:12px;color:var(--mute,#8a8f98);background:transparent;border:1px solid #2a2e39;border-radius:8px;text-align:left;width:100%}
#vp-chart>.hayes-fold:hover{color:inherit;border-color:#4a5168}
#vp-chart>#hayes-card,#vp-chart>#hayes-ins,#vp-chart>#hayes-lookup{order:3}
</style>
<!-- HAYES-FOLD-END -->"""
JS = """<!-- HAYES-FOLD-SCRIPT-START -->
<script>
(function(){
  const KEY = 'hayes_fold', vp = document.getElementById('vp-chart'); if (!vp) return;
  let folded = true; try { folded = localStorage.getItem(KEY) !== '0'; } catch (e) {}
  const bar = document.createElement('button'); bar.type = 'button'; bar.className = 'hayes-fold';
  function paint(){ document.body.classList.toggle('hayes-folded', folded); bar.setAttribute('aria-expanded', String(!folded)); bar.textContent = (folded ? '▸ ' : '▾ ') + '아서 헤이즈 ENA 카드 ' + (folded ? '(접힘 — 눌러서 펼치기)' : '(펼침 — 눌러서 접기)'); }
  bar.onclick = function(){ folded = !folded; try { localStorage.setItem(KEY, folded ? '1' : '0'); } catch (e) {} paint(); };
  const anchor = document.getElementById('hayes-ins') || document.getElementById('hayes-card');
  if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(bar, anchor); else vp.appendChild(bar);
  paint();
})();
</script>
<!-- HAYES-FOLD-SCRIPT-END -->"""

def put_script(text, start, end, block):
    """<script> 블록을 넣는다. 가장 긴 <script>(= 메인 스크립트) 바로 앞에 넣어서, 메인 스크립트가 계속 페이지의 마지막 <script> 로 남게 한다
    (저장소 테스트가 마지막 <script> 를 메인으로 읽는다). 이미 있으면 지우고 같은 자리에 다시 넣으므로 옛 위치(맨 뒤)에서도 자동으로 옮겨진다."""
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.S)
    text = pat.sub("", text, count=1)
    blocks = [(m.start(), len(m.group(1))) for m in re.finditer(r"<script>(.*?)</script>", text, re.S)]
    if not blocks:
        raise SystemExit("index.html 에서 메인 <script> 를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    pos = max(blocks, key=lambda b: b[1])[0]
    return text[:pos] + block + "\n" + text[pos:]

def put(text, start, end, block, anchor):
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if pat.search(text):
        return pat.sub(lambda m: block, text, count=1)
    if text.count(anchor) != 1:
        raise SystemExit(f"{anchor} 를 한 곳에서만 찾지 못했습니다(발견 {text.count(anchor)}곳). 아무것도 바꾸지 않았습니다.")
    return text.replace(anchor, block + "\n" + anchor)

new = put(src, "<!-- HAYES-FOLD-START -->", "<!-- HAYES-FOLD-END -->", CSS, "</head>")
new = put_script(new, "<!-- HAYES-FOLD-SCRIPT-START -->", "<!-- HAYES-FOLD-SCRIPT-END -->", JS)
if new == src:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
tmp = idx.with_suffix(".html.tmp")
tmp.write_text(new, encoding="utf-8")
tmp.replace(idx)
print("수정: index.html (아서 헤이즈 카드 접기/펼치기, 기본 접힘)")
