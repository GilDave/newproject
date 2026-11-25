"""차트를 열 때·시간봉을 바꿀 때·범위 버튼·'최신' 버튼을 누를 때 마지막(현재) 봉이 가로 중앙에 오게 한다. index.html.
v2: 모든 시간봉에서 유지되도록, 불러오기 직후 다른 코드가 범위를 바꿔도 잠시 동안(약 2.5초) 중앙으로 다시 맞춘다.
    사용자가 차트를 직접 휠·드래그·키 입력으로 움직이면 그 즉시 멈춘다.
사용: .venv/bin/python tools/wire_chart_center.py [프로젝트 폴더]   (몇 번 실행해도 안전, v1이 적용돼 있으면 v2로 올린다)
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
idx = root / "ena_bot" / "dashboard_static" / "index.html"
if not idx.exists():
    raise SystemExit(f"index.html 을 찾지 못했습니다: {idx}")
src = idx.read_text(encoding="utf-8")

V1 = [
    ("latestChart",
     "ts.setVisibleLogicalRange({from: bars.length - w + 6, to: bars.length + 6}); }",
     "const c = bars.length - 1; ts.setVisibleLogicalRange({from: c - w / 2, to: c + w / 2}); }   /* CHART-CENTER: 마지막 봉을 가로 중앙에 */"),
    ("showBarWindow",
     "const range = {from: Math.max(0, bars.length - count), to: bars.length + 5};",
     "const c = bars.length - 1, range = {from: c - count / 2, to: c + count / 2};   /* CHART-CENTER: 마지막 봉을 가로 중앙에 */"),
]
V2_ANCHOR = "  if (wantRange) chart.timeScale().setVisibleLogicalRange(range);\n}\n"
V2 = V2_ANCHOR + """/* CHART-CENTER2-START: 불러온 직후 다른 코드가 범위를 바꿔도 잠시 동안 중앙으로 다시 맞춘다(사용자가 움직이면 중단) */
(function () {
  const base = showBarWindow, wrap = document.getElementById('chartwrap'); let moved = false, timers = [];
  if (wrap) ['wheel', 'pointerdown', 'keydown', 'touchstart'].forEach(ev => wrap.addEventListener(ev, () => { moved = true; }, {passive: true}));
  showBarWindow = function (count) {
    base(count); moved = false; timers.forEach(clearTimeout);
    timers = [120, 400, 900, 1800, 3000].map(ms => setTimeout(() => {
      if (moved || !bars.length || !lastW) return;
      const c = bars.length - 1; chart.timeScale().setVisibleLogicalRange({from: c - count / 2, to: c + count / 2});
    }, ms));
  };
})();
/* CHART-CENTER2-END */
"""

if "CHART-CENTER2-START" in src:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
for name, old, new in V1:
    if new in src:
        continue
    if src.count(old) != 1:
        raise SystemExit(f"{name}: 바꿀 줄을 한 곳에서만 찾지 못했습니다(발견 {src.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    src = src.replace(old, new)
if src.count(V2_ANCHOR) != 1:
    raise SystemExit(f"showBarWindow 끝 위치를 한 곳에서만 찾지 못했습니다(발견 {src.count(V2_ANCHOR)}곳). 아무것도 바꾸지 않았습니다.")
src = src.replace(V2_ANCHOR, V2)
tmp = idx.with_suffix(".html.tmp")
tmp.write_text(src, encoding="utf-8")
tmp.replace(idx)
print("수정: index.html (모든 시간봉 현재 봉 중앙 배치 v2)")
