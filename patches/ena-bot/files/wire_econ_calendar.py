"""ENA 경제 캘린더에 외부 지표 일정(도매·소매재고, PCE, 실업수당, GDP 등)을 합친다: dashboard_market_context.py 2곳 + dashboard.py 1곳 + index.html 2곳.
먼저 ena_bot/econ_calendar.py 가 있어야 하고, wire_calendar_merge.py 가 적용돼 있으면 더 보기 좋다(필수는 아니다).
캘린더 카드 맨 위에 '오늘의 지표 발표(한국 시간)'(이전·예상·발표값)과 '이번 주 남은 일정', 수집 상태(성공 시각·최근 실패)가 나온다.
수집은 대시보드가 필요할 때 백그라운드로 1시간에 한 번(발표 직전·직후는 10분에 한 번) 하고, 실패하면 캐시를 보여 주며 실패를 표시한다. 읽기 전용이다.
사용: .venv/bin/python wire_econ_calendar.py [프로젝트 폴더]   (여러 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
되돌리려면 index.html 의 'ECON-TODAY-START'~'END' 와 호출부를 원래대로 하고, dashboard_market_context.py 의 'calendar' 조회 함수를 fetch_calendar 로 되돌린다.
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
ctx, dash, page, mod = (root / "ena_bot/dashboard_market_context.py", root / "ena_bot/dashboard.py", root / "ena_bot/dashboard_static/index.html", root / "ena_bot/econ_calendar.py")
if not mod.exists():
    raise SystemExit("ena_bot/econ_calendar.py 가 없습니다. 먼저 내려받으세요. 아무것도 바꾸지 않았습니다.")

PLUS = '''def _calendar_plus(now_ms: int) -> dict:
    """events.py 일정에 외부 지표 피드(도매·소매재고 등)를 합친다. 외부 수집이 실패해도 기본 일정은 그대로 돌려준다."""
    base = fetch_calendar(now_ms)
    try:
        from . import econ_calendar
        return econ_calendar.fetch_calendar_plus(now_ms, base)
    except Exception as exc:  # noqa: BLE001
        log.warning("econ calendar merge failed: %s", exc)
        return base


class MarketContext:
'''
C_EDITS = [("MarketContext 클래스 앞", "class MarketContext:\n", PLUS), ("기본 조회 함수", '            "calendar": fetch_calendar,\n', '            "calendar": _calendar_plus,\n')]
D_OLD = "        market = MarketContext(client, news)\n"
D_NEW = D_OLD + "    from . import econ_calendar as _ec\n    _ec.configure(cfg.data_path)\n"

JS = r"""/* ECON-TODAY-START: 오늘의 지표 발표·이번 주 남은 일정·수집 상태 */
const ECON_DT = new Intl.DateTimeFormat('ko-KR', {timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false});
function econRow(x, withDate) {
  const imp = {high: ['#ef5350', '높음'], medium: ['#f5a623', '보통'], low: ['#7d8597', '낮음']}[x.impact] || ['#7d8597', ''];
  const when = withDate ? ECON_DT.format(x.ts) : kstHM(x.ts);
  const actual = x.actual ? '<b>' + escH(x.actual) + '</b>' : (x.ts <= Date.now() ? '<i>발표 대기</i>' : '대기');
  return `<div class="mi" data-role="econrow">${when} <span style="color:${imp[0]}" title="중요도 ${imp[1]}">●</span> ${escH(x.name)}${x.manual ? ' <span class="ms">(수동)</span>' : ''} `
    + `<span class="ms">이전 ${escH(x.previous || '—')} · 예상 ${escH(x.forecast || '—')} · 발표 ${actual}</span></div>`;
}
function econTodayHtml(c) {
  const e = c.econ; if (!e) return '';
  const today = c.today || [], up = c.upcoming_econ || [];
  let h = '<div class="ms" data-role="econhead" style="margin:6px 0 2px;font-weight:600">오늘의 지표 발표 (한국 시간)</div>';
  h += today.length ? today.map(x => econRow(x, false)).join('') : '<div class="ms">오늘 수집된 발표 일정이 없습니다.</div>';
  if (up.length) h += '<div class="ms" style="margin:6px 0 2px;font-weight:600">이번 주 남은 일정</div>' + up.map(x => econRow(x, true)).join('');
  h += e.fetched_at
    ? `<div class="ms" data-role="econstatus">지표 피드 수집 ${kst(e.fetched_at)} · ${e.count}건${e.manual ? ' · 수동 ' + e.manual + '건' : ''}${e.error ? ' · <span style="color:#ff8a80">최근 갱신 실패: ' + escH(e.error) + '</span>' : ''}</div>`
    : `<div class="ms" data-role="econstatus" style="color:#ff8a80">${escH(e.note || e.error || '지표 피드를 아직 받지 못했습니다')}</div>`;
  return h + '<div class="ms" style="opacity:.7">피드에 없는 지표는 data/econ_manual.json 에 직접 추가할 수 있습니다.</div>';
}
/* ECON-TODAY-END */
"""
CALL_OLD = "(k === 'calendar' ? calendarHtml(c) :"
CALL_NEW = "(k === 'calendar' ? econTodayHtml(c) + calendarHtml(c) :"
CAL_FN = "function calendarHtml(c) {"

cx, dx, px = ctx.read_text(encoding="utf-8"), dash.read_text(encoding="utf-8"), page.read_text(encoding="utf-8")
ncx, ndx, npx = cx, dx, px
if "_calendar_plus" not in ncx:
    for name, old, new in C_EDITS:
        if ncx.count(old) != 1:
            raise SystemExit(f"dashboard_market_context.py 에서 {name} 위치를 찾지 못했습니다({ncx.count(old)}곳). 아무것도 바꾸지 않았습니다.")
        ncx = ncx.replace(old, new, 1)
if "econ_calendar as _ec" not in ndx:
    if ndx.count(D_OLD) != 1:
        raise SystemExit(f"dashboard.py 에서 MarketContext 생성 위치를 찾지 못했습니다({ndx.count(D_OLD)}곳). 아무것도 바꾸지 않았습니다.")
    ndx = ndx.replace(D_OLD, D_NEW, 1)
if "ECON-TODAY-START" in npx:                                   # 이미 있으면 최신 블록으로 교체
    a = npx.index("/* ECON-TODAY-START")
    e = npx.index("/* ECON-TODAY-END */\n", a) + len("/* ECON-TODAY-END */\n")
    npx = npx[:a] + JS + npx[e:]
else:
    if npx.count(CAL_FN) != 1:
        raise SystemExit("index.html 에서 calendarHtml 정의 위치를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    npx = npx.replace(CAL_FN, JS + CAL_FN, 1)
if CALL_NEW not in npx:
    if npx.count(CALL_OLD) != 1:
        raise SystemExit(f"index.html 에서 캘린더 표시 호출 위치를 찾지 못했습니다({npx.count(CALL_OLD)}곳). 아무것도 바꾸지 않았습니다.")
    npx = npx.replace(CALL_OLD, CALL_NEW, 1)
if (ncx, ndx, npx) == (cx, dx, px):
    print("이미 최신입니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
for path, text in ((ctx, ncx), (dash, ndx), (page, npx)):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
print("수정: dashboard_market_context.py, dashboard.py, index.html (외부 경제지표 일정)")
