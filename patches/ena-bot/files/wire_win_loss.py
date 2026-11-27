"""ENA 대시보드에 '승률(Win vs Lose)' 탭을 추가한다(dashboard.py 2곳 + index.html 2곳). 먼저 ena_bot/win_loss.py 가 있어야 한다.
기준 계좌와 그림자 계좌(BTC 필터·ML 게이트)의 거래를 Win/Lose 로 세어 승률(95% 구간)·이길 때/질 때 평균·본전 승률·최근 결과를 보여 준다.
읽기 전용이며 매매에는 영향이 없다.
사용: .venv/bin/python wire_win_loss.py [프로젝트 폴더]   (여러 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
되돌리려면 index.html 의 'WINLOSS-START'~'END' 를 지우고 TABS 에서 'wl' 항목을 뺀다.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
dash, page, mod = root / "ena_bot/dashboard.py", root / "ena_bot/dashboard_static/index.html", root / "ena_bot/win_loss.py"
if not mod.exists():
    raise SystemExit("ena_bot/win_loss.py 가 없습니다. 먼저 내려받으세요. 아무것도 바꾸지 않았습니다.")

D_EDITS = [
    ("import", "from .translate import TranslationCache, Translator, has_hangul\n", "from .translate import TranslationCache, Translator, has_hangul\nfrom . import win_loss\n"),
    ("/api/news 분기", '                elif url.path == "/api/news":\n',
     '                elif url.path == "/api/win-loss":\n'
     '                    self._json(_nan_to_none(win_loss.payload(cfg.data_path / "bot.db")))\n'
     '                elif url.path == "/api/news":\n'),
]
TAB_ANY = re.compile(r"const TABS = \[(.*?)\];", re.S)
BODY_OLD = "  } else if (tab === 'news') {\n"
BODY_NEW = "  } else if (tab === 'wl') {\n    el.innerHTML = '<div id=\"wlbox\">승률을 불러오는 중…</div>'; setTimeout(() => window.wlInit && window.wlInit(), 0);\n  } else if (tab === 'news') {\n"

BLOCK = """<!-- WINLOSS-START -->
<script>
(function(){
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const G = '#26a69a', R = '#ef5350';
  const f = (v, d) => v == null ? '—' : Number(v).toFixed(d == null ? 2 : d);
  const sg = (v, d) => v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(d == null ? 2 : d);
  const kd = ms => { try { return new Intl.DateTimeFormat('ko-KR', {timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(ms); } catch (e) { return ''; } };
  function card(a, title){
    const n = a.n || 0, w = a.wins || 0, l = a.losses || 0, wp = n ? w / n * 100 : 0;
    const bar = n ? '<div style="display:flex;height:30px;border-radius:6px;overflow:hidden;font-weight:700;font-size:13px;margin:8px 0">'
        + '<div style="width:' + wp + '%;background:' + G + ';display:flex;align-items:center;justify-content:center;min-width:' + (w ? 46 : 0) + 'px">Win ' + w + '</div>'
        + '<div style="flex:1;background:' + R + ';display:flex;align-items:center;justify-content:center;min-width:' + (l ? 46 : 0) + 'px">Lose ' + l + '</div></div>'
      : '<div style="margin:8px 0;opacity:.8">아직 끝난 거래가 없습니다.</div>';
    const ci = a.win_ci95 ? ' (95% 구간 ' + a.win_ci95[0] + '~' + a.win_ci95[1] + '%)' : '';
    const chips = (a.recent || []).map(x => '<span style="display:inline-block;width:18px;height:18px;line-height:18px;text-align:center;border-radius:3px;margin:1px;font-size:11px;font-weight:700;background:' + (x === 'W' ? G : R) + '">' + x + '</span>').join('');
    let h = '<div style="border:1px solid var(--line,#2a2e39);border-radius:8px;padding:10px 12px;margin:10px 0" data-role="wlcard">'
      + '<div style="font-weight:700">' + esc(title || a.label) + '</div>' + bar
      + '<div>승률 <b>' + (n ? f(a.win_rate, 1) + '%' : '—') + '</b>' + ci + ' · 거래 ' + n + '건' + (a.since ? ' · 기록 시작 ' + kd(a.since) : '') + '</div>'
      + (n ? '<div class="ms" style="margin-top:4px">이길 때 평균 ' + sg(a.avg_win, 2) + ' USDT · 질 때 평균 ' + sg(a.avg_loss, 2) + ' USDT · 손익비 ' + f(a.payoff, 2)
        + ' · 본전 승률 ' + (a.breakeven_win_rate == null ? '—' : a.breakeven_win_rate + '%') + ' · 기대값(거래당) ' + sg(a.expectancy, 3) + ' USDT · 누적 ' + sg(a.total_pnl, 2) + ' USDT · 최장 연패 ' + a.max_losing_streak + '</div>' : '')
      + (chips ? '<div style="margin-top:6px">최근 ' + (a.recent || []).length + '건(오래된 것 → 최신) ' + chips + '</div>' : '');
    if (a.open) h += '<div class="ms" style="margin-top:6px">지금 열려 있는 거래: ' + (a.open.side === 'long' ? '롱' : '숏') + ' · ' + kd(a.open.open_ts) + ' 진입 · 평가손익 ' + sg(a.open.pct, 2) + '% (끝나기 전이라 승패에 넣지 않음)</div>';
    if (n && !a.enough) h += '<div class="ms" style="margin-top:6px;color:#f5a623">거래가 ' + 30 + '건 미만이라 승률이 우연인지 구분할 수 없습니다. 구간이 넓은 이유입니다.</div>';
    if (a.trades && a.trades.length) h += '<details style="margin-top:6px"><summary style="cursor:pointer;color:#9ec3ff">최근 거래 내역 ' + a.trades.length + '건</summary>'
      + a.trades.slice().reverse().map(t => '<div class="ms" style="margin:2px 0 0 12px">' + kd(t.open_ts) + ' → ' + kd(t.close_ts) + ' · ' + (t.side === 'long' ? '롱' : '숏') + ' · ' + t.bars + '봉 · <b style="color:' + (t.pnl > 0 ? G : R) + '">' + (t.pnl > 0 ? 'Win' : 'Lose') + ' ' + sg(t.pct, 2) + '% (' + sg(t.pnl, 3) + ' USDT)</b></div>').join('') + '</details>';
    return h + '</div>';
  }
  function draw(box, r){
    if (!r || !r.ok) { box.textContent = '승률을 불러오지 못했습니다' + (r && r.error ? ': ' + r.error : ''); return; }
    let h = '<div style="font-weight:700;font-size:15px">자동매매 승률 — Win vs Lose</div>';
    for (const a of r.accounts) h += card(a);
    if (r.legacy) h += card(r.legacy, r.legacy.label);
    if (!r.accounts.length && !r.legacy) h += '<div style="margin:8px 0;opacity:.8">아직 기록된 사이클이 없습니다.</div>';
    h += '<div class="ms" style="margin-top:10px;opacity:.8">' + (r.notes || []).map(esc).join('<br>') + '</div>';
    box.innerHTML = h;
  }
  window.wlInit = async function(){
    const box = document.getElementById('wlbox'); if (!box) return;
    try { const r = await fetch('/api/win-loss'); draw(box, await r.json()); } catch (e) { box.textContent = '승률을 불러오지 못했습니다.'; }
  };
})();
</script>
<!-- WINLOSS-END -->"""

d, p = dash.read_text(encoding="utf-8"), page.read_text(encoding="utf-8")
nd, np_ = d, p
for name, old, new in D_EDITS:
    if new in nd:
        continue
    if nd.count(old) != 1:
        raise SystemExit(f"dashboard.py 에서 {name} 위치를 찾지 못했습니다({nd.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    nd = nd.replace(old, new, 1)
if "['wl'," not in np_:
    m = TAB_ANY.search(np_)
    if not m or np_.count("const TABS = [") != 1:
        raise SystemExit("index.html 에서 TABS 위치를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    inner = m.group(1)
    anchor = "['cmp', '계좌 비교']"
    new_inner = inner.replace(anchor, anchor + ", ['wl', '승률 Win vs Lose']", 1) if anchor in inner else inner + ", ['wl', '승률 Win vs Lose']"
    np_ = np_[:m.start(1)] + new_inner + np_[m.end(1):]
if "tab === 'wl'" not in np_:
    if np_.count(BODY_OLD) != 1:
        raise SystemExit(f"index.html 에서 탭 본문 위치를 찾지 못했습니다({np_.count(BODY_OLD)}곳). 아무것도 바꾸지 않았습니다.")
    np_ = np_.replace(BODY_OLD, BODY_NEW, 1)
pat = re.compile(r"<!-- WINLOSS-START -->.*?<!-- WINLOSS-END -->\n?", re.S)
def put_script(text, block):
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
print("수정: dashboard.py, index.html (승률 Win vs Lose 탭)")
