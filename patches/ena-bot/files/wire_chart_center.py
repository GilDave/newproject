"""차트를 열 때·시간봉을 바꿀 때·'최신' 버튼을 누를 때 마지막(현재) 봉이 가로 중앙에 오게 한다. index.html 2곳.
사용: .venv/bin/python tools/wire_chart_center.py [프로젝트 폴더]   (몇 번 실행해도 안전, 이미 적용돼 있으면 아무것도 바꾸지 않음)
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
idx = root / "ena_bot" / "dashboard_static" / "index.html"
if not idx.exists():
    raise SystemExit(f"index.html 을 찾지 못했습니다: {idx}")
src = idx.read_text(encoding="utf-8")

EDITS = [
    ("latestChart",
     "ts.setVisibleLogicalRange({from: bars.length - w + 6, to: bars.length + 6}); }",
     "const c = bars.length - 1; ts.setVisibleLogicalRange({from: c - w / 2, to: c + w / 2}); }   /* CHART-CENTER: 마지막 봉을 가로 중앙에 */"),
    ("showBarWindow",
     "const range = {from: Math.max(0, bars.length - count), to: bars.length + 5};",
     "const c = bars.length - 1, range = {from: c - count / 2, to: c + count / 2};   /* CHART-CENTER: 마지막 봉을 가로 중앙에 */"),
]
if all(new in src for _, _, new in EDITS):
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
for name, old, new in EDITS:
    if new in src:
        continue
    if src.count(old) != 1:
        raise SystemExit(f"{name}: 바꿀 줄을 한 곳에서만 찾지 못했습니다(발견 {src.count(old)}곳). 아무것도 바꾸지 않았습니다.")
for name, old, new in EDITS:
    if new not in src:
        src = src.replace(old, new)
tmp = idx.with_suffix(".html.tmp")
tmp.write_text(src, encoding="utf-8")
tmp.replace(idx)
print("수정: index.html (차트 현재 봉 중앙 배치)")
