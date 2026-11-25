"""ENA 차트에 '프랙탈 엘리엇'(4시간·1시간·30분·15분을 한 차트에 겹쳐 그리기 + 비교 표)을 연결한다. index.html 1곳.
먼저 wire_structure_chart.py 가 적용돼 있어야 한다(같은 계산·표시 틀을 쓴다). 이미 적용돼 있으면 새 버전으로 제자리 갱신한다.
사용: .venv/bin/python tools/wire_fractal_chart.py [프로젝트 폴더]   (몇 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
page = root / "ena_bot/dashboard_static/index.html"
js = Path(__file__).with_name("fractal_chart.js").read_text(encoding="utf-8").rstrip("\n") + "\n"

START = "// ---- 프랙탈 엘리엇:"
END = "// ---- /프랙탈 엘리엇 ----\n"
S_END = "// ---- /차트 구조 자동 작도 ----\n"

text = page.read_text(encoding="utf-8")
if "function stAnalyze" not in text or text.count(S_END) != 1:
    raise SystemExit("먼저 tools/wire_structure_chart.py 를 최신으로 적용하세요(차트 구조 자동 작도 블록이 없거나 옛 버전입니다). 아무것도 바꾸지 않았습니다.")
if START in text:
    a = text.index(START)
    e = text.find(END, a)
    if e == -1:
        raise SystemExit("기존 프랙탈 엘리엇 블록의 끝 표시를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    new = text[:a] + js + text[e + len(END):]
else:
    new = text.replace(S_END, S_END + js, 1)
if new == text:
    print("이미 최신")
else:
    tmp = page.with_name(page.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(page)
    print("수정: index.html (프랙탈 엘리엇)")
