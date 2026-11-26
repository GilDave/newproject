"""ENA 차트에 '가격 종합'(진입 계획 카드 · 거래량 매물대 · 프랙탈 엘리엇의 진입·손절·목표를 같은 규칙으로 비교 + 합의 구간)을 연결한다. index.html 1곳.
먼저 wire_fractal_chart.py(→ wire_structure_chart.py)와 wire_entry_plan.py 가 적용돼 있어야 한다. 이미 적용돼 있으면 새 버전으로 제자리 갱신한다.
프랙탈 표의 '진입 계획'도 같은 공통 규칙을 쓰도록 fractal_chart.js 가 함께 갱신되므로 wire_fractal_chart.py 를 먼저 다시 실행한다.
사용: .venv/bin/python tools/wire_confluence.py [프로젝트 폴더]   (몇 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
page = root / "ena_bot/dashboard_static/index.html"
js = Path(__file__).with_name("confluence.js").read_text(encoding="utf-8").rstrip("\n") + "\n"

START = "// ---- 가격 종합:"
END = "// ---- /가격 종합 ----\n"
F_END = "// ---- /프랙탈 엘리엇 ----\n"

text = page.read_text(encoding="utf-8")
if "function frcDraw" not in text or text.count(F_END) != 1:
    raise SystemExit("먼저 tools/wire_fractal_chart.py 를 최신으로 적용하세요(프랙탈 엘리엇 블록이 없거나 옛 버전입니다). 아무것도 바꾸지 않았습니다.")
if "const ENT " not in text and "const ENT=" not in text:
    raise SystemExit("먼저 tools/wire_entry_plan.py 를 적용하세요(진입 계획 카드가 없습니다). 아무것도 바꾸지 않았습니다.")
if START in text:
    a = text.index(START)
    e = text.find(END, a)
    if e == -1:
        raise SystemExit("기존 가격 종합 블록의 끝 표시를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    new = text[:a] + js + text[e + len(END):]
else:
    new = text.replace(F_END, F_END + js, 1)
if new == text:
    print("이미 최신")
else:
    tmp = page.with_name(page.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(page)
    print("수정: index.html (가격 종합)")
