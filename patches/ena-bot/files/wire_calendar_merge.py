"""ENA 대시보드의 '경제 캘린더' 카드 2장을 1장으로 합친다.
- 위험 카드 묶음의 '경제 캘린더' 카드(다음 발표까지 N시간 · 게이트)는 목록에서 뺀다(내용은 그대로 계산되고 종합 점수에도 반영된다).
- 그 한 줄 요약은 아래 시장 정보의 월간 캘린더 카드 맨 위(다음 일정 예정 시각 아래)로 옮긴다.
사용: .venv/bin/python wire_calendar_merge.py [프로젝트 폴더]   (여러 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
되돌리려면 ORDER 에 'calendar' 를 다시 넣는다.
"""
import sys
from pathlib import Path

page = Path(sys.argv[1] if len(sys.argv) > 1 else ".") / "ena_bot/dashboard_static/index.html"
EDITS = [
    ("카드 목록", "const ORDER = ['risk', 'health', 'trend', 'reversal', 'volatility', 'btc_regime', 'news', 'funding', 'calendar'];",
     "const ORDER = ['risk', 'health', 'trend', 'reversal', 'volatility', 'btc_regime', 'news', 'funding'];"),
    ("월간 캘린더 머리줄",
     "      + (k === 'calendar' && c.as_of != null ? `<div class=\"ms\">다음 일정 예정 시각 ${kst(c.as_of)}</div>` : '')\n",
     "      + (k === 'calendar' && c.as_of != null ? `<div class=\"ms\">다음 일정 예정 시각 ${kst(c.as_of)}</div>` : '')\n"
     "      + (k === 'calendar' && typeof STATE !== 'undefined' && STATE && STATE.cards && STATE.cards.calendar && STATE.cards.calendar.summary ? `<div class=\"ms\" data-role=\"calrisk\">${escH(STATE.cards.calendar.summary)}</div>` : '')\n"),
]
t = page.read_text(encoding="utf-8")
n = t
for name, old, new in EDITS:
    if new in n:
        continue
    if n.count(old) != 1:
        raise SystemExit(f"index.html 에서 {name} 위치를 찾지 못했습니다({n.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    n = n.replace(old, new, 1)
if n == t:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
tmp = page.with_name(page.name + ".tmp")
tmp.write_text(n, encoding="utf-8")
tmp.replace(page)
print("수정: index.html (경제 캘린더 카드 합치기)")
