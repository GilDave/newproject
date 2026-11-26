"""ENA 차트 아래에 '분석 허브'를 연결한다(index.html 1곳): 종합 판정 · 진입 체크리스트 · 추천 계획 · 포지션 크기 · 시간봉×방법 매트릭스를 한 곳에 두고,
진입 계획 카드(지표)·프랙탈 엘리엇·가격 비교(AI 포함)를 같은 자리의 탭으로 모은다.
먼저 wire_confluence.py(→ wire_fractal_chart.py)가 최신으로 적용돼 있어야 한다. 이미 적용돼 있으면 새 버전으로 제자리 갱신한다.
사용: .venv/bin/python tools/wire_hub.py [프로젝트 폴더]   (몇 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
되돌리려면 index.html 에서 '// ---- 분석 허브:' ~ '// ---- /분석 허브 ----' 블록을 지우고 wire_confluence.py·wire_fractal_chart.py 를 이전 버전으로 되돌린다.
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
page = root / "ena_bot/dashboard_static/index.html"
js = Path(__file__).with_name("hub.js").read_text(encoding="utf-8").rstrip("\n") + "\n"

START = "// ---- 분석 허브:"
END = "// ---- /분석 허브 ----\n"
C_END = "// ---- /가격 종합 ----\n"

text = page.read_text(encoding="utf-8")
if "function cfNorm" not in text or "hubEnsure" not in text.replace("function hubEnsure", "") and "typeof hubEnsure" not in text or text.count(C_END) != 1:
    raise SystemExit("먼저 tools/wire_fractal_chart.py 와 tools/wire_confluence.py 를 최신 버전으로 적용하세요(가격 종합 블록이 없거나 옛 버전입니다). 아무것도 바꾸지 않았습니다.")
if START in text:
    a = text.index(START)
    e = text.find(END, a)
    if e == -1:
        raise SystemExit("기존 분석 허브 블록의 끝 표시를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    new = text[:a] + js + text[e + len(END):]
else:
    new = text.replace(C_END, C_END + js, 1)
if new == text:
    print("이미 최신")
else:
    tmp = page.with_name(page.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(page)
    print("수정: index.html (분석 허브)")
