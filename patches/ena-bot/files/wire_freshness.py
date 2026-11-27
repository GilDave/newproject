"""ENA 대시보드 카드마다 '데이터가 얼마나 신선한가'를 보여 준다(dashboard.py 1곳 + index.html 3곳).
- 각 카드의 출처 줄 끝에 '3분 전 · 신선 / 지연 / 오래됨'(1초마다 갱신)과 '입력 소스 성공 n/m' 을 붙인다. 기존 '출처 … · 갱신 …' 글자는 그대로다.
- 화면 왼쪽 아래에 전체 요약 배지: 신선한 카드 수 · 입력 소스 성공 수 · 확인 불가 카드 수. 오래된 데이터나 실패한 입력이 있으면 노란/빨간 테두리.
신선 기준(초): 시세 기반 카드 300, 뉴스 점수 21,600(4시간 주기), 그 밖에 알려진 갱신 시각이 있는 칸 600. 기준의 3배를 넘으면 '오래됨'.
사용: .venv/bin/python wire_freshness.py [프로젝트 폴더]   (여러 번 실행해도 안전, 위치를 못 찾으면 아무것도 바꾸지 않음)
되돌리려면 index.html 에서 'FRESHNESS' 표시가 붙은 줄을 지우고 srcLine 을 예전 한 줄 정의로 되돌린다.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
dash, page = root / "ena_bot/dashboard.py", root / "ena_bot/dashboard_static/index.html"

D_OLD = '    return {k: {"source": s, "updated_at": t} for k, (s, t) in spec.items()} | {"display_tz": DISPLAY_TZ}\n'
D_NEW = '''    # FRESHNESS-START
    last_cycle = None            # 봇(추세 엔진)의 마지막 판단 시각: 위험·학습·건강 카드는 판단 때마다 갱신되므로 봇이 멈추면 이 값이 낡는다
    if store is not None:
        try:
            ev = store.events("trend_cycle", 1)
            last_cycle = int(ev[0][0]) if ev else None
        except Exception:  # noqa: BLE001
            last_cycle = None
    for key in ("risk", "learning", "health"):
        if key in spec:
            spec[key] = (spec[key][0] + " · 봇 마지막 판단 시각 기준", last_cycle)
    stale = {"trend": 300, "reversal": 300, "volatility": 300, "btc_regime": 300, "funding": 300, "news": 21600,
             "risk": 15000, "learning": 15000, "health": 15000}  # 이 시간(초)을 넘으면 '지연'(4시간 주기 + 여유)
    inputs = {"trend": [fetched(ena)], "reversal": [fetched(ena)], "volatility": [fetched(ena)], "btc_regime": [fetched(btc)],
              "funding": [fetched(tick)], "news": [spec["news"][1]]}

    def entry(k, s, t):
        d = {"source": s, "updated_at": t}
        if k in stale:
            d["stale_after"] = stale[k]
        if k in inputs:
            d["inputs"] = {"ok": sum(1 for x in inputs[k] if x is not None), "total": len(inputs[k])}
        return d
    return {k: entry(k, s, t) for k, (s, t) in spec.items()} | {"display_tz": DISPLAY_TZ}
    # FRESHNESS-END
'''
CSS_OLD = ".src{display:block;margin-top:auto;padding-top:6px;border-top:1px dashed var(--line);font-size:11px;color:var(--mute);overflow-wrap:anywhere}"
CSS_NEW = CSS_OLD + "\n.age{margin-left:6px;font-weight:600}.age.fresh{color:#26a69a}.age.late{color:#f5a623}.age.old{color:#ef5350}.inp{margin-left:6px}.inp.bad{color:#ef5350;font-weight:600}\nbody{padding-bottom:44px}  /* FRESHNESS: 아래 고정 배지가 마지막 내용을 가리지 않게 */"

JS = r"""/* FRESHNESS: 카드별 신선도(갱신 경과·입력 소스 성공 수)와 전체 요약 배지 */
const AGE_DEFAULT_STALE = 600;
function ageParts(m) {
  if (!m || m.updated_at == null) return null;
  const max = m.stale_after || AGE_DEFAULT_STALE, sec = Math.max(0, Math.round((Date.now() - m.updated_at) / 1000));
  const cls = sec <= max ? 'fresh' : sec <= max * 3 ? 'late' : 'old';
  const t = sec < 60 ? sec + '초 전' : sec < 3600 ? Math.round(sec / 60) + '분 전' : (sec / 3600).toFixed(1) + '시간 전';
  return {sec, cls, text: t + ' · ' + {fresh: '신선', late: '지연', old: '오래됨'}[cls]};
}
function ageSpan(m) { const a = ageParts(m); return a ? ` <span class="age ${a.cls}" data-upd="${m.updated_at}" data-max="${m.stale_after || AGE_DEFAULT_STALE}">${a.text}</span>` : ''; }
function inputsSpan(m) { return m && m.inputs ? ` <span class="inp${m.inputs.ok < m.inputs.total ? ' bad' : ''}">입력 소스 ${m.inputs.ok}/${m.inputs.total}</span>` : ''; }
function srcLine(m) { return `<span class="src" data-role="src">출처 ${m && m.source ? escH(m.source) : '확인 불가'} · 갱신 ${m && m.updated_at != null ? kst(m.updated_at) : '확인 불가'}${ageSpan(m)}${inputsSpan(m)}</span>`; }
function renderFreshBadge() {
  let el = document.getElementById('fresh-badge');
  if (!el) { el = document.createElement('div'); el.id = 'fresh-badge'; el.style.cssText = 'position:fixed;left:10px;bottom:8px;z-index:60;padding:3px 9px;border-radius:8px;font-size:11px;line-height:1.5;color:#9aa3b5;background:#131722cc;border:1px solid #2a2e39'; document.body.appendChild(el); }
  const src = (typeof STATE !== 'undefined' && STATE && STATE.sources) || null;
  if (!src) { el.style.display = 'none'; return; }
  const items = Object.entries(src).filter(([k, v]) => k !== 'display_tz' && v && typeof v === 'object');
  const known = items.filter(([k, v]) => v.updated_at != null).map(([k, v]) => ageParts(v));
  const unknown = items.length - known.length, cnt = {fresh: 0, late: 0, old: 0};
  known.forEach(a => cnt[a.cls]++);
  let ok = 0, tot = 0; items.forEach(([k, v]) => { if (v.inputs) { ok += v.inputs.ok; tot += v.inputs.total; } });
  el.style.display = '';
  el.textContent = `데이터 신선 ${cnt.fresh}/${known.length}` + (cnt.late ? ` · 지연 ${cnt.late}` : '') + (cnt.old ? ` · 오래됨 ${cnt.old}` : '') + (tot ? ` · 입력 소스 ${ok}/${tot}` : '') + (unknown ? ` · 갱신 시각 확인 불가 ${unknown}` : '');
  el.style.borderColor = cnt.old || ok < tot ? '#b71c1c' : cnt.late ? '#8a6d1a' : '#2a2e39';
  el.title = '카드마다 마지막 성공 갱신 시각 기준입니다. 기준(신선): 시세 카드 5분, 뉴스 점수 6시간, 봇 판단 기반 카드(위험·학습·건강) 4시간+여유, 그 밖 10분. 기준의 3배를 넘으면 오래됨. 확인 불가 = 갱신 시각이 없는 정적·수동 데이터(캘린더, v2 학습 리포트).';
}
function tickFresh() {
  document.querySelectorAll('.age[data-upd]').forEach(el => { const a = ageParts({updated_at: +el.dataset.upd, stale_after: +el.dataset.max}); if (a) { el.className = 'age ' + a.cls; el.textContent = a.text; } });
  renderFreshBadge();
}
setInterval(tickFresh, 1000);
"""

d, p = dash.read_text(encoding="utf-8"), page.read_text(encoding="utf-8")
nd, np_ = d, p
SIG_OLD, SIG_NEW = "def state_sources(state: dict, hub: DataHub) -> dict:", "def state_sources(state: dict, hub: DataHub, store=None) -> dict:"
CALL_OLD, CALL_NEW = "state[\"sources\"] = state_sources(state, hub)", "state[\"sources\"] = state_sources(state, hub, store_factory())"
if "# FRESHNESS-START" not in nd:
    if "FRESHNESS" in nd:      # 1차 버전이 이미 적용돼 있으면 그 블록을 새 블록으로 바꾼다
        old_blk = re.search(r"    stale = \{.*?\n    return \{k: entry\(k, s, t\) for k, \(s, t\) in spec\.items\(\)\} \| \{\"display_tz\": DISPLAY_TZ\}\n", nd, re.S)
        if not old_blk:
            raise SystemExit("dashboard.py 의 이전 신선도 블록을 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
        nd = nd[:old_blk.start()] + D_NEW + nd[old_blk.end():]
    else:
        if nd.count(D_OLD) != 1:
            raise SystemExit(f"dashboard.py 에서 state_sources 의 return 줄을 찾지 못했습니다({nd.count(D_OLD)}곳). 아무것도 바꾸지 않았습니다.")
        nd = nd.replace(D_OLD, D_NEW, 1)
if SIG_NEW not in nd:
    if nd.count(SIG_OLD) != 1 or nd.count(CALL_OLD) != 1:
        raise SystemExit("dashboard.py 에서 state_sources 정의 또는 호출 위치를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    nd = nd.replace(SIG_OLD, SIG_NEW, 1).replace(CALL_OLD, CALL_NEW, 1)
PAD = "body{padding-bottom:44px}"
if PAD not in np_:
    if "/* FRESHNESS */" in np_:      # 1차 버전이 이미 적용돼 있으면 여백 규칙만 덧붙인다
        np_ = np_.replace("/* FRESHNESS */", "\n" + PAD + "  /* FRESHNESS: 아래 고정 배지가 마지막 내용을 가리지 않게 */", 1)
    else:
        if np_.count(CSS_OLD) != 1:
            raise SystemExit(f"index.html 에서 .src 스타일을 찾지 못했습니다({np_.count(CSS_OLD)}곳). 아무것도 바꾸지 않았습니다.")
        np_ = np_.replace(CSS_OLD, CSS_NEW, 1)
old_js = re.search(r"/\* FRESHNESS: 카드별 신선도.*?setInterval\(tickFresh, 1000\);\n", np_, re.S)
if old_js:                              # 이미 들어 있는 블록은 최신 내용으로 교체
    np_ = np_[:old_js.start()] + JS + np_[old_js.end():]
else:
    m = re.search(r"^function srcLine\(m\) \{.*\}\n", np_, re.M)
    if not m or np_.count("function srcLine(m)") != 1:
        raise SystemExit("index.html 에서 srcLine 정의를 찾지 못했습니다. 아무것도 바꾸지 않았습니다.")
    np_ = np_[:m.start()] + JS + np_[m.end():]
if nd == d and np_ == p:
    print("이미 적용되어 있습니다. 아무것도 바꾸지 않았습니다.")
    raise SystemExit(0)
for path, text in ((dash, nd), (page, np_)):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
print("수정: dashboard.py, index.html (데이터 신선도 표시)")
