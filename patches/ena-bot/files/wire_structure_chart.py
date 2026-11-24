"""ENA 봇 대시보드 차트에 추세선·엘리엇 자동 작도를 연결한다(처음 적용). 이미 적용돼 있으면 새 버전으로 제자리 갱신한다(⚡ 표시 기준 조정 포함).
여러 번 실행해도 안전하고, 위치를 못 찾으면 아무것도 바꾸지 않는다.
사용: .venv/bin/python tools/wire_structure_chart.py [프로젝트 폴더]
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
page = root / "ena_bot/dashboard_static/index.html"
js = Path(__file__).with_name("structure_chart.js").read_text(encoding="utf-8").rstrip("\n") + "\n"

START = "// ---- 차트 구조 자동 작도:"
END = "// ---- /차트 구조 자동 작도 ----\n"
HOOK = "  if (TRNC) ms.push(...TRNC.markers());\n"
HOOK2 = HOOK + "  structDraw();\n"
DECL = "let TRNC = null;   // makeTrnChart 인스턴스(스크립트 끝에서 생성)\n"
SHK_A = "let SHK_KEY = '', SHK_CACHE = [];"
SHK_A2 = "const SHK_SHOW_Z = 9;   // 화면에는 평소 변동폭의 10배 이상(z>=9)인 봉만 표시한다(탐지·사후 통계의 기준 6배는 그대로)\n" + SHK_A
SHK_B = "SHK_CACHE = detectShocks(bars); }"
SHK_B2 = "SHK_CACHE = detectShocks(bars).filter(e => e.zRange >= SHK_SHOW_Z); }"

text = page.read_text(encoding="utf-8")
new = text
if "function stAnalyze" in new:                         # 이미 있음: 블록만 새 것으로 바꾼다
    a = new.index(START)
    e = new.find(END, a)
    end = e + len(END) if e != -1 else new.index(DECL, a)    # 옛 버전에는 끝 표시가 없어서 선언 위치까지가 블록이다
    new = new[:a] + js + new[end:]
else:
    for name, old in (("마커 위치", HOOK), ("선언 위치", DECL)):
        if new.count(old) != 1:
            raise SystemExit(f"{name}를 찾지 못했습니다({new.count(old)}곳). index.html 을 수정하지 않았습니다.")
    new = new.replace(HOOK, HOOK2, 1).replace(DECL, js + DECL, 1)
if "const SHK_SHOW_Z" not in new and new.count(SHK_A) == 1 and new.count(SHK_B) == 1:   # ⚡ 표시는 10배 이상만
    new = new.replace(SHK_A, SHK_A2, 1).replace(SHK_B, SHK_B2, 1)
if new == text:
    print("이미 최신")
else:
    page.write_text(new, encoding="utf-8")
    print("수정: index.html")
