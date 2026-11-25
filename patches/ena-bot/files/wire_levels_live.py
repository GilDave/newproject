"""'거래량 매물대' 표를 실시간 시세 기준으로 바꾼다(dashboard.py 2곳 + index.html 2곳). 먼저 ena_bot/levels_live.py 가 있어야 한다.
사용: .venv/bin/python tools/wire_levels_live.py [프로젝트 폴더]   (몇 번 실행해도 안전, 위치를 하나라도 못 찾으면 아무것도 바꾸지 않음)
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
dash, page, mod = root / "ena_bot/dashboard.py", root / "ena_bot/dashboard_static/index.html", root / "ena_bot/levels_live.py"
if not mod.exists():
    raise SystemExit("ena_bot/levels_live.py 가 없습니다. 먼저 내려받으세요. 아무것도 바꾸지 않았습니다.")

IMP = "from .dashboard_levels import build_level_plan\n"
IMP2 = IMP + "from . import levels_live\n"
ROUTE = "                    self._json(levels_payload(cfg.data_path, cfg.symbol))\n"
ROUTE2 = ("                    self._json(levels_live.payload(lambda iv, n: klines_payload(client, cfg.symbol, iv, n, None),\n"
          "                                                   lambda: levels_payload(cfg.data_path, cfg.symbol), cfg.symbol))\n")
PRICES = "const prices = plan ? [plan.entry, plan.take_profit, plan.stop].map(p => fmt(p, 6)) : ['산출 불가', '—', '—'];"
PRICES2 = ("const prices = plan ? [fmt(plan.entry, 6), fmt(plan.take_profit, 6) + (plan.capped ? ' ↧' : ''), fmt(plan.stop, 6)] "
           ": [(j.timeframes[key] && j.timeframes[key].why && j.timeframes[key].why[side]) || '산출 불가', '—', '—'];")
STATUS = "`저장된 봉 기준가 ${fmt(j.current_price, 6)} USDT · ${tstr(j.as_of)} KST`"
STATUS2 = "(j.stale ? '⚠ 실시간 시세를 못 받아 저장된 봉 기준(오래됐을 수 있음) ' : '현재가 ') + `${fmt(j.current_price, 6)} USDT · ${tstr(j.as_of)} KST 기준`"

LOOP = "for (const value of [label, direction, ...prices]) row.insertCell().textContent = value;"
LOOP2 = ("if (plan) { for (const value of [label, direction, ...prices]) row.insertCell().textContent = value; }\n"
         "        else { row.insertCell().textContent = label; row.insertCell().textContent = direction; const c = row.insertCell(); c.colSpan = 3; c.style.textAlign = 'left'; c.style.opacity = '.75'; c.textContent = prices[0]; }")
NOTE = "note.textContent = j.disclaimer;"
NOTE2 = NOTE + """
    const rf = $('#levels-refs') || (() => { const d = document.createElement('div'); d.id = 'levels-refs'; d.style.cssText = 'margin:8px 0;font-size:12px;line-height:1.7'; note.parentNode.insertBefore(d, note); return d; })();
    rf.replaceChildren(); const t0 = document.createElement('div'); t0.textContent = '참고 구간 — 계획이 무효여도 보이는 가까운 거래량 매물대(현재가 대비 %)'; t0.style.opacity = '.7'; rf.appendChild(t0);
    for (const [key, label] of labels) {
      const r = j.timeframes[key] && j.timeframes[key].refs; if (!r) continue;
      const fz = z => fmt(z.price, 6) + ' (' + (z.dist_pct >= 0 ? '+' : '') + (z.dist_pct * 100).toFixed(1) + '%)';
      const up = (r.resistance || []).map(fz).join(' · ') || '없음', dn = (r.support || []).map(fz).join(' · ') || '없음';
      const dv = document.createElement('div'); dv.textContent = label + ' — 위 저항: ' + up + '  /  아래 지지: ' + dn; rf.appendChild(dv);
    }"""

d, p = dash.read_text(encoding="utf-8"), page.read_text(encoding="utf-8")
nd, np_ = d, p
if "levels_live" not in d:
    for name, old in (("import", IMP), ("/api/levels 분기", ROUTE)):
        if d.count(old) != 1:
            raise SystemExit(f"dashboard.py 에서 {name} 위치를 찾지 못했습니다({d.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    nd = d.replace(IMP, IMP2, 1).replace(ROUTE, ROUTE2, 1)
if PRICES2 not in p:
    for name, old in (("가격 표시 줄", PRICES), ("기준가 문구", STATUS)):
        if p.count(old) != 1:
            raise SystemExit(f"index.html 에서 {name} 위치를 찾지 못했습니다({p.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    np_ = p.replace(PRICES, PRICES2, 1).replace(STATUS, STATUS2, 1)
if "levels-refs" not in np_:
    for name, old in (("표 행 만들기", LOOP), ("설명 문구", NOTE)):
        if np_.count(old) != 1:
            raise SystemExit(f"index.html 에서 {name} 위치를 찾지 못했습니다({np_.count(old)}곳). 아무것도 바꾸지 않았습니다.")
    np_ = np_.replace(LOOP, LOOP2, 1).replace(NOTE, NOTE2, 1)
if nd == d and np_ == p:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
for path, text in ((dash, nd), (page, np_)):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
print("수정: dashboard.py, index.html (거래량 매물대를 실시간 시세 기준으로)")
