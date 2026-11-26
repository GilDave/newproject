"""차트 분석 화면 두 가지 개선. index.html 2곳(스타일·스크립트).
1) 스크롤해도 시간봉 버튼줄과 '종목·최신 봉' 정보 줄이 화면 위에 붙어 있게 한다(위쪽 탭 막대가 고정이면 그 바로 아래에 붙음).
2) 집중 모드(전체화면): 차트·막대·지표·프랙탈 표만 화면 전체를 쓰고 다른 카드와 머리글은 숨긴다. 버튼(차트 아래 줄 '⛶ 집중 모드') · F 키(차트에 포커스) · Esc 로 끝낸다.
사용: .venv/bin/python tools/wire_chart_focus.py [프로젝트 폴더]   (몇 번 실행해도 안전: 표시 사이의 내용만 교체)
되돌리려면 index.html 에서 'CHART-FOCUS-START'~'END' 두 블록을 지운다.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
idx = root / "ena_bot" / "dashboard_static" / "index.html"
if not idx.exists():
    raise SystemExit(f"index.html 을 찾지 못했습니다: {idx}")
src = idx.read_text(encoding="utf-8")

CSS = """<!-- CHART-FOCUS-START -->
<style id="chart-focus-style">
/* 1) 스크롤해도 보이는 막대: 시간봉 버튼줄(#tf)과 종목·최신 봉 줄(.chartreadout). 위의 탭 막대(.views)가 고정이면 그 높이만큼 아래에 붙인다 */
#vp-chart .chartbox>#tf{position:sticky!important;top:var(--vtabs-h,0px)!important;z-index:19!important;background:var(--panel,#131722)!important}
#vp-chart .chartbox>.chartreadout{position:sticky!important;top:calc(var(--vtabs-h,0px) + var(--tf-h,40px))!important;z-index:18!important;background:var(--panel,#131722)!important;padding-top:4px;padding-bottom:4px;border-bottom:1px solid #2a2e39}
#vp-chart,.chartpanel{overflow:visible}
#vp-chart:fullscreen{overflow:auto}
/* 도구 줄 ⛶ 버튼으로 브라우저 전체화면에 들어가면 격자 배치와 겹치지 않게 한 줄 배치로 되돌린다 */
#vp-chart:fullscreen{display:block!important}
#vp-chart:fullscreen>*{margin-bottom:10px}
/* 2) 집중 모드 */
body.chart-focus{overflow:hidden!important}
body.chart-focus>header,body.chart-focus .views{display:none!important}
body.chart-focus #vp-chart{position:fixed!important;inset:0;z-index:1000;display:flex!important;flex-direction:column;padding:6px;background:var(--bg,#0b0e14);overflow:hidden!important}
body.chart-focus #vp-chart>*:not(.chartbox){display:none!important}
body.chart-focus #vp-chart .chartbox{flex:1 1 auto;min-height:0!important;overflow-y:auto;overflow-x:hidden}
body.chart-focus #vp-chart .chartbox>*{flex:none}
body.chart-focus #vp-chart .chartwrap{flex:none;height:max(340px,58vh)!important}
body.chart-focus #vp-chart .chartbox>#tf{top:0!important}
body.chart-focus #vp-chart .chartbox>.chartreadout{top:var(--tf-h,40px)!important}
#chart-focus-btn{margin-left:8px}
</style>
<!-- CHART-FOCUS-END -->"""
JS = """<!-- CHART-FOCUS-SCRIPT-START -->
<script>
(function(){
  const vp = document.getElementById('vp-chart'); if (!vp) return;
  const body = document.body;
  /* 고정 막대가 놓일 높이를 잰다 */
  function measure(){
    const tabs = document.querySelector('.views'), tf = document.getElementById('tf');
    const tabsH = tabs && getComputedStyle(tabs).position === 'sticky' && tabs.offsetParent !== null ? tabs.offsetHeight : 0;
    document.documentElement.style.setProperty('--vtabs-h', tabsH + 'px');
    document.documentElement.style.setProperty('--tf-h', (tf ? tf.offsetHeight : 40) + 'px');
  }
  measure(); window.addEventListener('resize', measure);
  try { const ro = new ResizeObserver(measure); const tf = document.getElementById('tf'); if (tf) ro.observe(tf); const tabs = document.querySelector('.views'); if (tabs) ro.observe(tabs); } catch (e) {}
  setTimeout(measure, 500); setTimeout(measure, 2000);
  /* 집중 모드 */
  const btn = document.createElement('button'); btn.type = 'button'; btn.id = 'chart-focus-btn'; btn.title = '차트에 포커스를 두고 F 키 · 종료는 Esc';
  function paint(){ btn.textContent = body.classList.contains('chart-focus') ? '✕ 집중 모드 종료' : '⛶ 집중 모드'; }
  function set(on){
    body.classList.toggle('chart-focus', on); paint(); measure();
    if (on && document.documentElement.requestFullscreen && !document.fullscreenElement) { try { document.documentElement.requestFullscreen().catch(function(){}); } catch (e) {} }
    if (!on && document.fullscreenElement && document.exitFullscreen) { try { document.exitFullscreen().catch(function(){}); } catch (e) {} }
    setTimeout(function(){ window.dispatchEvent(new Event('resize')); }, 60);
  }
  btn.onclick = function(){ set(!body.classList.contains('chart-focus')); };
  /* 도구 줄의 ⛶ 전체화면 버튼도 같은 집중 모드로 연결한다(메인 스크립트가 chartAction 을 만든 뒤에 바꿔 끼운다) */
  window.addEventListener('load', function(){
    if (typeof window.chartAction === 'function' && !window.chartAction.__focus) {
      const base = window.chartAction;
      window.chartAction = function(a){ if (a === 'full') { set(!body.classList.contains('chart-focus')); return; } return base.apply(this, arguments); };
      window.chartAction.__focus = true;
    }
  });
  const foot = document.querySelector('#vp-chart .chartfooter');
  if (foot) foot.insertBefore(btn, foot.firstChild); else vp.appendChild(btn);
  paint();
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape' && body.classList.contains('chart-focus') && !document.fullscreenElement) set(false);
    if ((e.key === 'f' || e.key === 'F') && !e.metaKey && !e.ctrlKey && !e.altKey && e.target && e.target.id === 'chartwrap') { e.preventDefault(); set(!body.classList.contains('chart-focus')); }
  });
  document.addEventListener('fullscreenchange', function(){ if (!document.fullscreenElement && body.classList.contains('chart-focus')) set(false); });
})();
</script>
<!-- CHART-FOCUS-SCRIPT-END -->"""

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

new = put(src, "<!-- CHART-FOCUS-START -->", "<!-- CHART-FOCUS-END -->", CSS, "</head>")
new = put_script(new, "<!-- CHART-FOCUS-SCRIPT-START -->", "<!-- CHART-FOCUS-SCRIPT-END -->", JS)
if new == src:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
tmp = idx.with_suffix(".html.tmp")
tmp.write_text(new, encoding="utf-8")
tmp.replace(idx)
print("수정: index.html (고정 막대 · 집중 모드)")
