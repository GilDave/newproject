"""ENA 대시보드에 미국 주식(QQQ·나스닥 선물) 시세 수집을 붙인다: dashboard.py 3곳. 먼저 ena_bot/us_market.py 가 있어야 하고, 허브 표시는 새 tools/hub.js 를 wire_hub.py 로 올리면 나온다.
/api/us-market 이 생기고, 대시보드가 필요할 때 백그라운드로 1분에 한 번 받는다. 읽기 전용이며 매매에는 영향이 없다.
사용: .venv/bin/python wire_us_market.py [프로젝트 폴더]   (여러 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
먼저 실제로 받아지는지 확인:  .venv/bin/python -m ena_bot.us_market check
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
dash, mod = root / "ena_bot/dashboard.py", root / "ena_bot/us_market.py"
if not mod.exists():
    raise SystemExit("ena_bot/us_market.py 가 없습니다. 먼저 내려받으세요. 아무것도 바꾸지 않았습니다.")
EDITS = [
    ("import", "from .translate import TranslationCache, Translator, has_hangul\n", "from .translate import TranslationCache, Translator, has_hangul\nfrom . import us_market\n"),
    ("/api/news 분기", '                elif url.path == "/api/news":\n',
     '                elif url.path == "/api/us-market":\n                    self._json(_nan_to_none(us_market.COLLECTOR.payload()))\n                elif url.path == "/api/news":\n'),
    ("저장 폴더 설정", "        market = MarketContext(client, news)\n", "        market = MarketContext(client, news)\n    us_market.configure(cfg.data_path)\n"),
]
d = dash.read_text(encoding="utf-8")
nd = d
for name, old, new in EDITS:
    if new in nd:
        continue
    if nd.count(old) != 1:
        raise SystemExit(f"dashboard.py 에서 {name} 위치를 찾지 못했습니다({nd.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    nd = nd.replace(old, new, 1)
if nd == d:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
tmp = dash.with_name(dash.name + ".tmp")
tmp.write_text(nd, encoding="utf-8")
tmp.replace(dash)
print("수정: dashboard.py (미국 주식 시세 /api/us-market)")
