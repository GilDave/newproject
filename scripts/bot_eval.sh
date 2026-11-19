#!/usr/bin/env bash
# ena-bot 신호 평가 (읽기 전용): 최신 가격을 받아 봇의 그림자 신호를 채점하고 결과를 날짜별로 저장한다.
# 봇의 코드·설정·DB·서비스는 건드리지 않는다 (DB는 읽기 전용으로 연다, 가격은 별도 폴더에 저장).
set -euo pipefail
BOT="${ENA_BOT_DIR:-/Users/dave_gil/00.Claud/01.ena-bot}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/eval"
mkdir -p "$OUT/history"
PY="$BOT/.venv/bin/python"
[ -x "$PY" ] || { echo "봇의 파이썬을 찾지 못했습니다: $PY"; exit 1; }
(cd "$BOT" && "$PY" -W ignore -m ena_bot fetch --days 30 --out "$OUT/ENAUSDT_3m.csv")
STAMP="$(date +%Y%m%d-%H%M)"
"$HERE/.venv/bin/solomon" shadow --bot-db "$BOT/data/bot.db" --candles "$OUT/ENAUSDT_3m.csv" > "$OUT/history/$STAMP.json"
cp "$OUT/history/$STAMP.json" "$OUT/latest.json"
python3 - "$OUT/latest.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))["signals"]
m, n = d["ml2"]["overall"], d["news"]
print(f"예측(ml2): 채점 {m['n']}건 적중 {m['hits']}건 (95% 구간 {m['ci'][0]}~{m['ci'][1]}) {'' if m['enough'] else '← 표본 부족, 결론 아님'}")
print(f"뉴스 점수: 높은 위험 {n['high_risk']['n']}건 / 낮은 위험 {n['low_risk']['n']}건 {'' if n['enough'] else '← 표본 부족, 결론 아님'}")
print(f"가격 대기 중: {d['waiting_for_price']}건")
PY
echo "저장: $OUT/latest.json"
