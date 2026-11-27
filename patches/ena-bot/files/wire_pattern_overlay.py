"""ENA 차트에 '차트 패턴 감지(참고용)'을 연결한다: dashboard.py 2곳 + index.html 1곳. 먼저 ena_bot/patterns.py · ena_bot/pattern_api.py 가 있어야 한다.
쌍바닥·쌍봉·(역)헤드앤숄더·삼각 수렴·쐐기·깃발·박스권을 차트에 보라색 점선·주황 기준선으로 겹쳐 그리고, 차트 아래 표에 각 패턴의 과거 통계
(18코인 이력, 24시간 뒤, 비용 후)를 함께 보여 준다. 1시간·4시간 차트에서만 동작한다(통계를 검증한 시간봉). 신호가 아니다.
먼저 wire_structure_chart.py 가 적용돼 있어야 한다. 이미 적용돼 있으면 새 버전으로 제자리 갱신한다.
사용: .venv/bin/python wire_pattern_overlay.py [프로젝트 폴더]   (여러 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
되돌리려면 index.html 의 '// ---- 차트 패턴 감지' ~ '// ---- /차트 패턴 감지 ----' 를 지운다.
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
dash, page = root / "ena_bot/dashboard.py", root / "ena_bot/dashboard_static/index.html"
for need in ("patterns.py", "pattern_api.py"):
    if not (root / "ena_bot" / need).exists():
        raise SystemExit(f"ena_bot/{need} 가 없습니다. 먼저 내려받으세요. 아무것도 바꾸지 않았습니다.")
js = Path(__file__).with_name("pattern_overlay.js").read_text(encoding="utf-8").rstrip("\n") + "\n"

IMP = "from .translate import TranslationCache, Translator, has_hangul\n"
IMP_NEW = IMP + "from . import pattern_api\n"
ROUTE = '                elif url.path == "/api/news":\n'
ROUTE_NEW = ('                elif url.path == "/api/patterns":\n'
             '                    sym = q.get("symbol", cfg.symbol)\n'
             '                    self._json(pattern_api.payload(lambda iv_, n: klines_payload(client, sym, iv_, n, None), sym, q.get("interval", "60"), int(time.time() * 1000)))\n') + ROUTE

START, END = "// ---- 차트 패턴 감지(참고용)", "// ---- /차트 패턴 감지 ----\n"
S_END, F_END = "// ---- /차트 구조 자동 작도 ----\n", "// ---- /프랙탈 엘리엇 ----\n"

d, p = dash.read_text(encoding="utf-8"), page.read_text(encoding="utf-8")
nd, np_ = d, p
if "pattern_api" not in nd:
    for name, old in (("import", IMP), ("/api/news 분기", ROUTE)):
        if nd.count(old) != 1:
            raise SystemExit(f"dashboard.py 에서 {name} 위치를 찾지 못했습니다({nd.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    nd = nd.replace(IMP, IMP_NEW, 1).replace(ROUTE, ROUTE_NEW, 1)
if "function stAnalyze" not in np_ or np_.count(S_END) != 1:
    raise SystemExit("먼저 wire_structure_chart.py 를 적용하세요(차트 구조 자동 작도 블록이 없습니다). 아무것도 바꾸지 않았습니다.")
if START in np_:
    a = np_.index(START)
    e = np_.find(END, a)
    if e == -1:
        raise SystemExit("기존 패턴 블록의 끝 표시를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    np_ = np_[:a] + js + np_[e + len(END):]
else:
    anchor = F_END if np_.count(F_END) == 1 else S_END       # 프랙탈 엘리엇 블록이 있으면 그 뒤, 없으면 구조 작도 블록 뒤
    np_ = np_.replace(anchor, anchor + js, 1)
if nd == d and np_ == p:
    print("이미 최신입니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
for path, text in ((dash, nd), (page, np_)):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
print("수정: dashboard.py, index.html (차트 패턴 감지)")
if not (root / "ena_bot/pattern_stats.json").exists():
    print("참고: ena_bot/pattern_stats.json 이 없어 과거 통계는 표시되지 않습니다(패턴 표시는 동작).")
