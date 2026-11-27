"""ENA 대시보드 '뉴스 위험' 탭에 'AI 뉴스 요약' 카드를 붙인다(dashboard.py 3곳 + index.html 2곳). 먼저 ena_bot/news_brief.py 가 있어야 한다.
버튼을 눌렀을 때만 로컬 LLM(Ollama)으로 최근 헤드라인을 한국어 3~6줄로 요약한다(자동 호출 없음, 30분 캐시).
요약 문장마다 근거 헤드라인 번호가 붙고, 근거 없는 문장·헤드라인에 없는 숫자·매수/매도/전망 표현은 코드가 걸러 낸다. 가격 판단이 아니다.
다른 모델을 쓰려면 환경변수 ENA_BRIEF_MODEL (뉴스 점수가 쓰는 llm.model 은 바뀌지 않는다).
사용: .venv/bin/python wire_news_brief.py [프로젝트 폴더]   (여러 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
되돌리려면 index.html 의 'NEWS-BRIEF-START'~'END' 를 지운다.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
dash, page, mod = root / "ena_bot/dashboard.py", root / "ena_bot/dashboard_static/index.html", root / "ena_bot/news_brief.py"
if not mod.exists():
    raise SystemExit("ena_bot/news_brief.py 가 없습니다. 먼저 내려받으세요. 아무것도 바꾸지 않았습니다.")

D_EDITS = [
    ("import", "from .translate import TranslationCache, Translator, has_hangul\n",
     "from .translate import TranslationCache, Translator, has_hangul\nfrom .news_brief import NewsBrief\n"),
    ("market 생성", "    if market is None:\n        market = MarketContext(client, news)\n",
     "    if market is None:\n        market = MarketContext(client, news)\n    brief = NewsBrief(cfg, news, store_factory)\n"),
    ("/api/news 분기", '                elif url.path == "/api/news":\n',
     '                elif url.path == "/api/news-brief":\n'
     '                    if q.get("refresh") == "1":\n'
     '                        brief.start(force=True)\n'
     '                    self._json(brief.state())\n'
     '                elif url.path == "/api/news":\n'),
]
P_EDITS = [
    ("뉴스 탭 자리", '<div style="margin:10px 0 4px;color:var(--mute)">LLM 판단: ${esc(summaryKo(c))}</div>',
     '<div style="margin:10px 0 4px;color:var(--mute)">LLM 판단: ${esc(summaryKo(c))}</div><div id="newsbrief"></div>'),
    ("뉴스 탭 초기화", "if (mini.news) mini.news.remove();",
     "setTimeout(() => window.newsBriefInit && window.newsBriefInit(), 0); if (mini.news) mini.news.remove();"),
]

BLOCK = """<!-- NEWS-BRIEF-START -->
<script>
(function(){
  let timer = null, tries = 0;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const hm = ts => { try { return new Date(ts * 1000).toLocaleTimeString('ko-KR', {timeZone: 'Asia/Seoul', hour12: false, hour: '2-digit', minute: '2-digit'}); } catch (e) { return ''; } };
  const safeLink = u => /^https?:\\/\\//.test(u || '') ? u : '';
  function draw(box, st){
    const r = st.result, bullets = r && r.bullets ? r.bullets : [];
    const rows = bullets.map(b => '<div style="margin:6px 0;line-height:1.55"><span style="display:inline-block;padding:0 7px;margin-right:6px;border-radius:9px;background:#2a2e39;color:#9aa3b5;font-size:11px">' + esc(b.topic) + '</span>' + esc(b.text) +
      ' <span style="color:var(--mute)">' + (b.refs || []).map(x => { const l = safeLink(x.link); return (l ? '<a href="' + esc(l) + '" target="_blank" rel="noopener" title="' + esc(x.title) + '">[' + x.i + ']</a>' : '<span title="' + esc(x.title) + '">[' + x.i + ']</span>'); }).join('') + '</span></div>').join('');
    const meta = r ? '생성 ' + hm(r.ts) + ' · 모델 ' + esc(r.model) + ' · 헤드라인 ' + r.n_headlines + '개' + (r.dropped ? ' · 검증에서 제외한 문장 ' + r.dropped + '개' : '') + ' · ' + r.elapsed_sec + '초' + (r.meta ? ' · 프롬프트 ' + esc(r.meta.prompt_version) + ' · 모델 응답 ' + (r.meta.latency_ms / 1000).toFixed(1) + '초 · 토큰 ' + (r.meta.tokens_in == null ? '—' : r.meta.tokens_in) + '/' + (r.meta.tokens_out == null ? '—' : r.meta.tokens_out) : '') : '아직 생성하지 않았습니다.';
    box.innerHTML = '<div style="margin:12px 0 4px;color:var(--mute)">AI 뉴스 요약 <span style="font-size:11px">(참고용 · 헤드라인만 근거 · 가격 방향 판단 아님)</span></div>' +
      '<div class="note" style="margin:0">' + (rows || '<div style="color:var(--mute)">' + (st.running ? '요약을 만드는 중… (모델을 처음 올리면 수십 초 걸립니다)' : '요약이 없습니다.') + '</div>') +
      '<div style="margin-top:8px;display:flex;gap:10px;align-items:center;flex-wrap:wrap"><button id="newsbrief-go"' + (st.running ? ' disabled' : '') + '>' + (st.running ? '생성 중…' : (r ? '다시 요약' : '요약 만들기')) + '</button>' +
      '<span style="font-size:11px;color:var(--mute)">' + meta + '</span></div>' +
      (st.error ? '<div style="margin-top:6px;color:#ff8a80;font-size:12px">⚠ ' + esc(st.error) + '</div>' : '') +
      '<div style="margin-top:6px;font-size:11px;color:var(--mute)">문장 끝 [번호]는 근거 헤드라인(누르면 원문)입니다. 근거·숫자 검증을 통과한 문장만 표시합니다. 번역·요약은 틀릴 수 있으니 원문을 확인하세요.</div></div>';
    const go = box.querySelector('#newsbrief-go'); if (go) go.onclick = () => load(box, true);
  }
  async function load(box, refresh){
    try {
      const r = await fetch('/api/news-brief' + (refresh ? '?refresh=1' : '')); const st = await r.json();
      if (!document.body.contains(box)) return; draw(box, st);
      clearTimeout(timer);
      if (st.running && tries++ < 150) timer = setTimeout(() => load(box, false), 2500); else tries = 0;
    } catch (e) { box.textContent = 'AI 뉴스 요약을 불러오지 못했습니다.'; }
  }
  window.newsBriefInit = function(){ const box = document.getElementById('newsbrief'); if (box) { tries = 0; load(box, false); } };
})();
</script>
<!-- NEWS-BRIEF-END -->"""

d, p = dash.read_text(encoding="utf-8"), page.read_text(encoding="utf-8")
nd, np_ = d, p
for name, old, new in D_EDITS:
    if new in nd:
        continue
    if nd.count(old) != 1:
        raise SystemExit(f"dashboard.py 에서 {name} 위치를 찾지 못했습니다({nd.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    nd = nd.replace(old, new, 1)
for name, old, new in P_EDITS:
    if new in np_:
        continue
    if np_.count(old) != 1:
        raise SystemExit(f"index.html 에서 {name} 위치를 찾지 못했습니다({np_.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    np_ = np_.replace(old, new, 1)

pat = re.compile(r"<!-- NEWS-BRIEF-START -->.*?<!-- NEWS-BRIEF-END -->\n?", re.S)
def put_script(text, block):
    """가장 긴 <script>(메인 스크립트) 앞에 넣어서 메인이 계속 마지막 <script> 로 남게 한다. 이미 있으면 지우고 다시 넣는다."""
    text = pat.sub("", text, count=1)
    blocks = [(m.start(), len(m.group(1))) for m in re.finditer(r"<script>(.*?)</script>", text, re.S)]
    if not blocks:
        raise SystemExit("index.html 에서 메인 <script> 를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    pos = max(blocks, key=lambda b: b[1])[0]
    return text[:pos] + block + "\n" + text[pos:]
np_ = put_script(np_, BLOCK)
if nd == d and np_ == p:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
for path, text in ((dash, nd), (page, np_)):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
print("수정: dashboard.py, index.html (AI 뉴스 요약)")
