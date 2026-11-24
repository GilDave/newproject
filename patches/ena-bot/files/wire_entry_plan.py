"""ENA 봇 대시보드에 '진입 계획' 카드와 차트 선을 연결한다(dashboard.py 2곳 + index.html 3곳). 이미 적용돼 있으면 JS를 새 버전으로 제자리 갱신한다.
여러 번 실행해도 안전하고, 위치를 하나라도 못 찾으면 아무 파일도 바꾸지 않는다.
사용: .venv/bin/python tools/wire_entry_plan.py [프로젝트 폴더]
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
dash, page = root / "ena_bot/dashboard.py", root / "ena_bot/dashboard_static/index.html"
js = Path(__file__).with_name("entry_card.js").read_text(encoding="utf-8").rstrip("\n") + "\n"

IMP = "from .hayes_holdings import DEFAULT_INTERVAL_MIN\n"
IMP2 = IMP + "from . import entry_api\n"
GET = '                elif url.path == "/healthz":\n'
GET2 = ('                elif url.path == "/api/entry-plan":\n'
        '                    self._json(entry_api.payload(lambda iv, n: klines_payload(client, cfg.symbol, iv, n, None), cfg.data_path, cfg.symbol))\n') + GET
CARD_ANCHOR = '  <details class="panel hayes-lookup" id="hayes-lookup">'
CARD = '  <section class="panel entc" id="entry-card">진입 계획을 불러오는 중…</section>\n'
HOOK = "  if (TRNC) ms.push(...TRNC.markers());\n"
HOOK2 = HOOK + "  entDraw();\n"
DECL = "let TRNC = null;   // makeTrnChart 인스턴스(스크립트 끝에서 생성)\n"
START, END = "// ---- 진입 계획:", "// ---- /진입 계획 ----\n"

d, p = dash.read_text(encoding="utf-8"), page.read_text(encoding="utf-8")
nd, np_ = d, p
for name, text, old in (("import", d, IMP), ("/healthz 분기", d, GET)):
    if "entry_api" not in text and text.count(old) != 1:
        raise SystemExit(f"{name} 위치를 찾지 못했습니다({text.count(old)}곳). 아무 파일도 바꾸지 않았습니다.")
if "entry_api" not in nd:
    nd = nd.replace(IMP, IMP2, 1).replace(GET, GET2, 1)
if 'id="entry-card"' not in np_:
    if np_.count(CARD_ANCHOR) != 1:
        raise SystemExit(f"카드를 놓을 위치(조회·등록 패널)를 찾지 못했습니다({np_.count(CARD_ANCHOR)}곳). 아무 파일도 바꾸지 않았습니다.")
    np_ = np_.replace(CARD_ANCHOR, CARD + CARD_ANCHOR, 1)
if "function entDraw" in np_:                               # 이미 있음: JS 블록만 새 것으로
    a = np_.index(START)
    e = np_.find(END, a)
    np_ = np_[:a] + js + np_[e + len(END):]
else:
    for name, old in (("마커 위치", HOOK), ("선언 위치", DECL)):
        if np_.count(old) != 1:
            raise SystemExit(f"{name}를 찾지 못했습니다({np_.count(old)}곳). 아무 파일도 바꾸지 않았습니다.")
    np_ = np_.replace(HOOK, HOOK2, 1).replace(DECL, js + DECL, 1)
if (nd, np_) == (d, p):
    print("이미 최신")
else:
    dash.write_text(nd, encoding="utf-8")
    page.write_text(np_, encoding="utf-8")
    print("수정: dashboard.py, index.html")
