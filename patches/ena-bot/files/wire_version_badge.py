"""ENA 대시보드 오른쪽 아래에 '버전 배지'를 붙인다(dashboard.py 2곳 + index.html 1곳). 먼저 ena_bot/version_info.py 가 있어야 한다.
배지: 앱 버전 · 커밋 · 브랜치 · 변경 수(지문) · 서버 시작 시각. 서버가 시작한 뒤 ena_bot/*.py 가 바뀌었으면 빨간 경고(재시작 필요).
Solomon 프로젝트 카드의 버전 줄과 같은 커밋·지문이라 두 화면을 그대로 비교할 수 있다. 배지를 누르면 복사된다.
사용: .venv/bin/python tools/wire_version_badge.py [프로젝트 폴더]   (몇 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
되돌리려면 index.html 의 'VERSION-BADGE-START'~'END' 를 지운다.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
dash, page, mod = root / "ena_bot/dashboard.py", root / "ena_bot/dashboard_static/index.html", root / "ena_bot/version_info.py"
if not mod.exists():
    raise SystemExit("ena_bot/version_info.py 가 없습니다. 먼저 내려받으세요. 아무것도 바꾸지 않았습니다.")

IMP = "from .hayes_holdings import DEFAULT_INTERVAL_MIN\n"
IMP2 = IMP + "from . import version_info\n"
GET = '                elif url.path == "/healthz":\n'
GET2 = ('                elif url.path == "/api/version":\n'
        '                    self._json(version_info.payload())\n') + GET

BADGE = """<!-- VERSION-BADGE-START -->
<script>
(function(){
  const el = document.createElement('div'); el.id = 'ver-badge';
  el.style.cssText = 'position:fixed;right:10px;bottom:8px;z-index:60;max-width:92vw;padding:3px 9px;border-radius:8px;font-size:11px;line-height:1.5;color:#9aa3b5;background:#131722cc;border:1px solid #2a2e39;cursor:copy;overflow-wrap:anywhere';
  document.body.appendChild(el);
  let text = '';
  function hm(s){ try { return new Date(s * 1000).toLocaleTimeString('ko-KR', {timeZone: 'Asia/Seoul', hour12: false, hour: '2-digit', minute: '2-digit'}); } catch (e) { return ''; } }
  async function load(){
    try {
      const r = await fetch('/api/version'); if (!r.ok) throw new Error('v'); const v = await r.json();
      const parts = [v.app_version ? 'v' + v.app_version : null, v.git ? '커밋 ' + v.commit + (v.tag ? ' (' + v.tag + ')' : '') : 'git 아님', v.git ? v.branch : null,
        v.git ? (v.dirty ? '변경 ' + v.dirty + '개' + (v.fingerprint ? ' (지문 ' + v.fingerprint + ')' : '') : '변경 없음') : null, '서버 시작 ' + hm(v.started_at)].filter(Boolean);
      text = parts.join(' · '); el.textContent = text; el.title = '눌러서 복사 — Solomon 프로젝트 카드의 버전 줄과 커밋·지문을 비교하세요';
      if (v.stale_code) { const w = document.createElement('div'); w.textContent = '⚠ 서버가 시작된 뒤 코드가 바뀌었습니다 — 서비스를 재시작해야 반영됩니다'; w.style.cssText = 'color:#ff8a80;font-weight:700'; el.appendChild(w); el.style.borderColor = '#b71c1c'; } else el.style.borderColor = '#2a2e39';
    } catch (e) { el.textContent = '버전 정보를 읽지 못했습니다'; }
  }
  el.onclick = async function(){ try { await navigator.clipboard.writeText(text); const o = el.textContent; el.textContent = '복사했습니다'; setTimeout(load, 1200); } catch (e) {} };
  load(); setInterval(load, 30000);
})();
</script>
<!-- VERSION-BADGE-END -->"""

d, p = dash.read_text(encoding="utf-8"), page.read_text(encoding="utf-8")
nd, np_ = d, p
if "version_info" not in d:
    for name, old in (("import", IMP), ("/healthz 분기", GET)):
        if d.count(old) != 1:
            raise SystemExit(f"dashboard.py 에서 {name} 위치를 찾지 못했습니다({d.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    nd = d.replace(IMP, IMP2, 1).replace(GET, GET2, 1)
pat = re.compile(r"<!-- VERSION-BADGE-START -->.*?<!-- VERSION-BADGE-END -->\n?", re.S)
def put_script(text, block):
    """가장 긴 <script>(메인 스크립트) 앞에 넣어서 메인이 계속 마지막 <script> 로 남게 한다. 이미 있으면 지우고 다시 넣는다(옛 위치에서도 옮겨짐)."""
    text = pat.sub("", text, count=1)
    blocks = [(m.start(), len(m.group(1))) for m in re.finditer(r"<script>(.*?)</script>", text, re.S)]
    if not blocks:
        raise SystemExit("index.html 에서 메인 <script> 를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    pos = max(blocks, key=lambda b: b[1])[0]
    return text[:pos] + block + "\n" + text[pos:]
np_ = put_script(p, BADGE)
if nd == d and np_ == p:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
for path, text in ((dash, nd), (page, np_)):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
print("수정: dashboard.py, index.html (버전 배지)")
