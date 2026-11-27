"""ENA 대시보드에 '계좌 비교' 탭을 추가한다(dashboard.py 2곳 + index.html 2곳 + 스크립트 1개). 먼저 ena_bot/shadow_scorecard.py 가 있어야 한다.
기준 계좌와 그림자 계좌(BTC 필터·ML 게이트)를 같은 기간으로 맞춰 수익률·최대 낙폭·리밸런싱·시장 참여율을 나란히 보여 준다.
기간이 90일 미만이면 우열을 가리지 않는다는 경고가 맨 위에 나온다. 읽기 전용이며 매매에는 영향이 없다.
사용: .venv/bin/python wire_shadow_scorecard.py [프로젝트 폴더]   (여러 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
되돌리려면 index.html 의 'SHADOW-CMP-START'~'END' 를 지우고 TABS 에서 'cmp' 항목을 뺀다.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
dash, page, mod = root / "ena_bot/dashboard.py", root / "ena_bot/dashboard_static/index.html", root / "ena_bot/shadow_scorecard.py"
if not mod.exists():
    raise SystemExit("ena_bot/shadow_scorecard.py 가 없습니다. 먼저 내려받으세요. 아무것도 바꾸지 않았습니다.")

D_EDITS = [
    ("import", "from .translate import TranslationCache, Translator, has_hangul\n",
     "from .translate import TranslationCache, Translator, has_hangul\nfrom . import shadow_scorecard\n"),
    ("/api/news 분기", '                elif url.path == "/api/news":\n',
     '                elif url.path == "/api/account-compare":\n'
     '                    self._json(_nan_to_none(shadow_scorecard.payload(cfg.data_path / "bot.db")))\n'
     '                elif url.path == "/api/news":\n'),
]
P_EDITS = [
    ("탭 목록", "const TABS = [['acct', '계좌·성과'], ['news', '뉴스 위험']",
     "const TABS = [['acct', '계좌·성과'], ['cmp', '계좌 비교'], ['news', '뉴스 위험']"),
    ("탭 본문", "  } else if (tab === 'news') {\n",
     "  } else if (tab === 'cmp') {\n    el.innerHTML = '<div id=\"cmpbox\">계좌 비교를 불러오는 중…</div>'; setTimeout(() => window.cmpInit && window.cmpInit(), 0);\n  } else if (tab === 'news') {\n"),
]

BLOCK = """<!-- SHADOW-CMP-START -->
<script>
(function(){
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const COL = {base: '#4fa3ff', shadow: '#f5a623', ml: '#e91e63'};
  const f = (v, d) => v == null ? '—' : Number(v).toFixed(d == null ? 2 : d);
  const sg = v => v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%';
  const kd = ms => { try { return new Intl.DateTimeFormat('ko-KR', {timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit'}).format(ms); } catch (e) { return ''; } };
  function chart(r){
    const W = 640, H = 200, PL = 46, PR = 8, PT = 8, PB = 8, all = r.accounts.flatMap(a => a.curve);
    if (all.length < 2) return '';
    const t0 = r.start_ms, t1 = r.end_ms || t0 + 1, ys = all.map(p => p[1]), lo = Math.min(...ys, 100), hi = Math.max(...ys, 100), pad = (hi - lo) * 0.08 || 1;
    const ymin = lo - pad, ymax = hi + pad;
    const X = t => PL + (t - t0) / ((t1 - t0) || 1) * (W - PL - PR), Y = v => H - PB - (v - ymin) / (ymax - ymin) * (H - PT - PB);
    const lines = r.accounts.map(a => '<polyline fill="none" stroke="' + (COL[a.key] || '#9aa3b5') + '" stroke-width="2" points="' + a.curve.map(p => X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1)).join(' ') + '"/>').join('');
    const ticks = [ymax, (ymax + ymin) / 2, ymin].map(v => '<text x="' + (PL - 5) + '" y="' + (Y(v) + 4).toFixed(1) + '" text-anchor="end" font-size="11" fill="#7d8597">' + v.toFixed(1) + '</text>').join('');
    const legend = r.accounts.map(a => '<span style="margin-right:12px"><span style="color:' + (COL[a.key] || '#9aa3b5') + '">●</span> ' + esc(a.label) + '</span>').join('');
    return '<div style="font-size:12px;margin:2px 0 4px">' + legend + '</div>' +
      '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;max-width:760px;height:auto;background:#131722;border:1px solid #2a2e39;border-radius:8px">' + ticks + '<line x1="' + PL + '" x2="' + (W - PR) + '" y1="' + Y(100).toFixed(1) + '" y2="' + Y(100).toFixed(1) + '" stroke="#7d8597" stroke-dasharray="4 4"/>' + lines + '</svg>' +
      '<div style="font-size:11px;color:var(--mute);margin-top:2px">첫 시점 = 100 · ' + kd(r.start_ms) + ' ~ ' + kd(r.end_ms) + ' · 점선은 100(본전) · 선이 겹치면 가장 위 색만 보입니다</div>';
  }
  function draw(box, r){
    if (!r.ok) { box.innerHTML = '<div class="note">계좌 비교를 불러오지 못했습니다: ' + esc(r.error || '') + '</div>'; return; }
    if (!r.accounts || !r.accounts.length) { box.innerHTML = '<div class="note">' + (r.notes || []).map(esc).join('<br>') + '</div>'; return; }
    const warn = (r.notes || []).map(n => '<div style="margin:3px 0">⚠ ' + esc(n) + '</div>').join('');
    const rows = r.accounts.map(a => '<tr><td><span style="color:' + (COL[a.key] || '#9aa3b5') + '">●</span> ' + esc(a.label) + '</td><td style="text-align:right">' + sg(a.return_pct) + '</td><td style="text-align:right">' + (a.vs_base_pct == null ? '—' : sg(a.vs_base_pct)) + '</td><td style="text-align:right">' + f(a.max_dd_pct) + '%</td><td style="text-align:right">' + a.rebalances + '회</td><td style="text-align:right">' + f(a.in_market_pct, 0) + '%</td></tr>').join('');
    box.innerHTML = (warn ? '<div class="note" style="border-color:#8a6d1a">' + warn + '</div>' : '') +
      '<div style="margin:10px 0 6px;color:var(--mute)">같은 기간(' + r.days + '일 · 사이클 ' + r.cycles + '개)으로 맞춘 비교</div>' + chart(r) +
      '<table style="width:100%;max-width:760px;margin-top:10px;border-collapse:collapse;font-size:13px"><thead><tr style="color:var(--mute);text-align:right"><th style="text-align:left">계좌</th><th>수익률</th><th>기준 대비</th><th>최대 낙폭</th><th>리밸런싱</th><th>시장 참여율</th></tr></thead><tbody>' + rows + '</tbody></table>' +
      '<div style="margin-top:8px;font-size:12px;color:var(--mute)">참고: 같은 기간 ENA 가격 변화 ' + sg(r.buy_hold_pct) + ' (단순 보유). 수익률은 펀딩비·수수료 반영 후 자본 기준이고, 기준 대비 = 기준 계좌와의 수익률 차이(%p)입니다. ' +
      (r.enough ? '기간이 ' + r.min_days + '일 이상이라 비교할 수 있지만, 거래 수가 적으면 여전히 우연일 수 있습니다.' : '기간이 짧아 우열을 정하지 않습니다.') + '</div>';
  }
  window.cmpInit = async function(){
    const box = document.getElementById('cmpbox'); if (!box) return;
    try { const r = await fetch('/api/account-compare'); draw(box, await r.json()); } catch (e) { box.textContent = '계좌 비교를 불러오지 못했습니다.'; }
  };
})();
</script>
<!-- SHADOW-CMP-END -->"""

d, p = dash.read_text(encoding="utf-8"), page.read_text(encoding="utf-8")
nd, np_ = d, p
for text_name, edits, cur in (("dashboard.py", D_EDITS, "nd"), ("index.html", P_EDITS, "np_")):
    for name, old, new in edits:
        txt = nd if cur == "nd" else np_
        if new in txt:
            continue
        if txt.count(old) != 1:
            raise SystemExit(f"{text_name} 에서 {name} 위치를 찾지 못했습니다({txt.count(old)}곳). 아무것도 바꾸지 않았습니다.")
        txt = txt.replace(old, new, 1)
        if cur == "nd":
            nd = txt
        else:
            np_ = txt
pat = re.compile(r"<!-- SHADOW-CMP-START -->.*?<!-- SHADOW-CMP-END -->\n?", re.S)
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
print("수정: dashboard.py, index.html (계좌 비교 탭)")
