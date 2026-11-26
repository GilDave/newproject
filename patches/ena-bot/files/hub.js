// ---- 분석 허브: 종합 판정 · 진입 계획(지표) · 엘리엇 · 가격 비교 · AI 예측을 차트 아래 한 곳에 모은다. ----
// 한 곳에서 위에서 아래로 읽는다: ① 결론(판정 + 진입 체크리스트) ② 추천 계획과 포지션 크기 ③ 시간봉 × 방법 매트릭스. 자세한 표는 같은 자리의 탭으로 둔다.
// 설계 근거: 큰 시간봉이 방향을 정하고 작은 시간봉이 진입 시점을 정한다(top-down), 시간봉 의견이 엇갈리면 관망, 진입 전 체크리스트로 조건을 하나씩 확인,
// 포지션 크기는 (계좌 × 위험 %) ÷ 손절 거리. 매매 신호가 아니며 이 규칙들은 과거 통계로 검증되지 않았다.
const HUB_TABS = [['sum', '종합'], ['ent', '진입 계획 (지표)'], ['ell', '엘리엇'], ['prc', '가격 비교 · AI']];
const HUB_FEE = 0.00055, HUB_SLIP = 0.0001, HUB_PASS = 7, HUB_WAIT = 5, HUB_MIN_RR = 1.5, HUB_MAX_COST = 0.15;
const HUB = {tab: 'sum', acct: 1000, risk: 1, loaded: false, box: null, key: ''};
function hubStore() {
  try {
    if (!HUB.loaded) { HUB.loaded = true; const v = JSON.parse(localStorage.getItem('ena_hub_v1') || '{}'); if (['sum', 'ent', 'ell', 'prc'].indexOf(v.tab) >= 0) HUB.tab = v.tab; if (v.acct > 0) HUB.acct = v.acct; if (v.risk > 0) HUB.risk = v.risk; }
    else localStorage.setItem('ena_hub_v1', JSON.stringify({tab: HUB.tab, acct: HUB.acct, risk: HUB.risk}));
  } catch (e) { /* 기억하지 못해도 동작한다 */ }
}
// 수수료·슬리피지(왕복)가 위험(손절까지 거리)의 몇 배인가
function hubCostR(n) { const risk = Math.abs(n.mid - n.stop); return risk > 0 ? n.mid * 2 * (HUB_FEE + HUB_SLIP) / risk : Infinity; }
// 통과한 계획들(방향 side): [{tf, method, n}]
function hubPlans(res, side) {
  const out = [];
  for (const r of res.rows) for (const k of ['entry', 'levels', 'levels2', 'fractal']) { const c = r.cells[k]; if (c && c.ok && c.side === side) out.push({tf: r.tf, method: k === 'levels2' ? 'levels' : k, n: c}); }
  return out;
}
// 추천 계획: 여러 방법의 가격이 겹치는 곳에 있고, 지금 구간 안이고, 손익비가 좋은 것
function hubPick(res, side) {
  const plans = hubPlans(res, side); if (!plans.length) return null;
  const inCl = (p) => res.clusters.some(c => c.side === side && c.members.some(m => m.tf === p.tf && m.method === p.method && Math.abs(m.n.mid - p.n.mid) < 1e-12));
  let best = null, bs = -Infinity;
  for (const p of plans) { const sc = (inCl(p) ? 3 : 0) + (p.n.inZone ? 2 : 0) + Math.min(p.n.rr, 3) - (p.n.poor ? 1 : 0) - p.n.distPct * 20; if (sc > bs) { bs = sc; best = p; } }
  return best;
}
// 진입 체크리스트. 판정 방향이 없으면 적용하지 않는다(관망).
function hubChecklist(res, price) {
  const v = res.verdict, side = v.head === 'long' || v.head === 'short' ? v.head : null;
  if (!side) return {side: null, items: [], pass: 0, total: 0, status: 'none', text: '방향 근거가 부족합니다 → 관망 (체크리스트를 적용하지 않음)', best: null};
  const plans = hubPlans(res, side), best = hubPick(res, side);
  const near = plans.some(p => p.n.inZone || p.n.distPct <= 0.005), cl = res.clusters.some(c => c.side === side && c.kinds.length >= 2);
  const items = [
    {key: 'big', label: '큰 흐름(1일·4시간·1시간)이 같은 방향', ok: v.big.side === side, note: v.big.side === 'wait' ? '큰 흐름이 불분명' : v.big.side !== side ? '큰 흐름은 반대' : ''},
    {key: 'small', label: '단타(30분·15분)가 큰 흐름과 반대가 아님', ok: v.small.side === 'wait' || v.small.side === side, note: v.small.side !== 'wait' && v.small.side !== side ? '단타가 역추세' : ''},
    {key: 'agree', label: '같은 시간봉에서 지표·엘리엇이 일치', ok: v.rows.some(r => r.state === 'agree' && r.side === side), note: ''},
    {key: 'noconf', label: '지표·엘리엇이 충돌한 시간봉이 없음', ok: v.conflicts.length === 0, note: v.conflicts.length ? v.conflicts.map(r => r.tf).join('·') + ' 충돌' : ''},
    {key: 'plan', label: '공통 규칙을 통과한 진입 계획이 있음', ok: plans.length > 0, note: ''},
    {key: 'confl', label: '서로 다른 방법의 진입 가격이 겹침(합의 구간)', ok: cl, note: ''},
    {key: 'near', label: '진입 구간이 가까움(구간 안 또는 0.5% 이내)', ok: near, note: ''},
    {key: 'rr', label: '추천 계획의 손익비가 ' + HUB_MIN_RR + ' 이상', ok: !!best && best.n.rr >= HUB_MIN_RR, note: best ? '손익비 ' + best.n.rr.toFixed(2) : '계획 없음'},
    {key: 'cost', label: '수수료·슬리피지가 위험의 ' + (HUB_MAX_COST * 100) + '% 이하', ok: !!best && hubCostR(best.n) <= HUB_MAX_COST, note: best ? (hubCostR(best.n) * 100).toFixed(0) + '%' : ''},
  ];
  let ai = null; if (best) { const row = res.rows.find(r => r.tf === best.tf); ai = row && row.ai && row.ai.side ? (row.ai.side === side ? 'same' : 'opp') : 'none'; }
  const pass = items.filter(i => i.ok).length, total = items.length;
  const must = items[0].ok && items.find(i => i.key === 'rr').ok;      // 큰 흐름과 같은 방향이고 손익비가 기준 이상이어야 '진입 검토 가능'(큰 시간봉의 거부권)
  const status = pass >= HUB_PASS && must ? 'ready' : pass >= HUB_WAIT ? 'wait' : 'no';
  const text = status === 'ready' ? '조건 충족 — 진입 검토 가능 (손절을 정하고 크기를 계산한 뒤)' : status === 'wait' ? '조건 일부 부족 — 대기 (아래 ✗ 항목이 채워지길 기다림)' : '조건 부족 — 관망';
  return {side, items, pass, total, status, text, best, ai};
}
// 포지션 크기: 수량 = (계좌 × 위험%) ÷ |진입 − 손절|
function hubSize(acct, riskPct, n) {
  if (!(acct > 0) || !(riskPct > 0) || !n) return null;
  const riskUsd = acct * riskPct / 100, per = Math.abs(n.mid - n.stop); if (!(per > 0)) return null;
  const qty = riskUsd / per, notional = qty * n.mid;
  return {riskUsd, qty, notional, lev: notional / acct};
}
function hubSetTab(k) {
  HUB.tab = k; hubStore();
  for (const t of HUB_TABS) {
    const pane = document.getElementById('hub-pane-' + t[0]), btn = document.getElementById('hub-tab-' + t[0]);
    if (pane) pane.style.display = t[0] === k ? '' : 'none';
    if (btn) { btn.style.background = t[0] === k ? '#273852' : 'transparent'; btn.style.color = t[0] === k ? '#edf3ff' : '#9aa3b5'; btn.setAttribute('aria-selected', String(t[0] === k)); }
  }
}
// 허브를 한 번만 만든다. 차트 영역 맨 아래에 놓고, 진입 계획 카드를 그 안의 탭으로 옮긴다.
function hubEnsure() {
  if (HUB.box && HUB.box.isConnected) return HUB.box;
  const cw = document.getElementById('chartwrap'), cbox = cw && cw.closest ? cw.closest('.chartbox') : null; if (!cbox) return null;
  hubStore();
  const hub = document.createElement('div'); hub.id = 'analysis-hub';
  hub.style.cssText = 'margin:8px 0;border:1px solid #2a2e39;border-radius:10px;background:#101520;color:#d1d4dc;font:12px/1.6 sans-serif';
  const bar = document.createElement('div'); bar.setAttribute('role', 'tablist'); bar.style.cssText = 'display:flex;gap:4px;flex-wrap:wrap;padding:6px 8px;border-bottom:1px solid #2a2e39;align-items:center';
  const ttl = document.createElement('b'); ttl.textContent = '분석 허브'; ttl.style.marginRight = '8px'; bar.appendChild(ttl);
  for (const t of HUB_TABS) {
    const b = document.createElement('button'); b.type = 'button'; b.id = 'hub-tab-' + t[0]; b.textContent = t[1]; b.setAttribute('role', 'tab');
    b.style.cssText = 'padding:4px 12px;border-radius:7px;border:1px solid #2a2e39;cursor:pointer;font:inherit'; b.onclick = () => hubSetTab(t[0]); bar.appendChild(b);
  }
  hub.appendChild(bar);
  for (const t of HUB_TABS) { const p = document.createElement('div'); p.id = 'hub-pane-' + t[0]; p.style.cssText = 'padding:8px 10px'; hub.appendChild(p); }
  const anchor = cbox.querySelector('.hint');
  if (anchor) anchor.insertAdjacentElement('afterend', hub); else cbox.appendChild(hub);
  const ec = document.getElementById('entry-card'), pe = document.getElementById('hub-pane-ent');
  if (ec && pe && !pe.contains(ec)) pe.appendChild(ec);
  HUB.box = hub; document.getElementById('hub-pane-sum').textContent = '분석을 불러오는 중…'; hubSetTab(HUB.tab);
  return hub;
}
function hubBadge(side, text) {
  const b = document.createElement('span'); b.textContent = text !== undefined ? text : (side === 'long' ? '롱' : side === 'short' ? '숏' : '—');
  b.style.cssText = 'display:inline-block;min-width:30px;text-align:center;padding:0 7px;border-radius:5px;font-weight:700;color:#fff;background:' + (side === 'long' ? '#1e8e5a' : side === 'short' ? '#c62828' : '#4a5168') + (side ? '' : ';opacity:.55');
  return b;
}
function hubFmt(v) { return typeof fmt === 'function' ? fmt(v, 5) : Number(v).toFixed(5); }
function hubRender(res, price) {
  try {
    hubEnsure(); const pane = document.getElementById('hub-pane-sum'); if (!pane || !res || !res.verdict) return;
    const ck = hubChecklist(res, price), v = res.verdict, best = ck.best;
    const key = JSON.stringify([price.toFixed(5), v.head, v.big.sum, v.small.sum, ck.items.map(i => i.ok), best ? [best.tf, best.method, best.n.mid.toFixed(5), best.n.stop.toFixed(5)] : null, res.rows.map(r => [r.op.ind, r.op.ell, r.ai && r.ai.side, r.cells.levels && r.cells.levels.ok && r.cells.levels.side])]);
    if (key === HUB.key && pane.firstChild && pane.firstChild.id === 'hub-sum-root') return;
    HUB.key = key; pane.textContent = '';
    const mk = (tag, text, css) => { const d = document.createElement(tag); if (text !== undefined) d.textContent = text; if (css) d.style.cssText = css; return d; };
    const root = mk('div', undefined, 'display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start'); root.id = 'hub-sum-root';
    // ---- 왼쪽: 결론 · 체크리스트 · 추천 계획 · 크기
    const left = mk('div', undefined, 'flex:1 1 430px;min-width:300px');
    const head = mk('div', undefined, 'display:flex;gap:8px;align-items:center;flex-wrap:wrap');
    const hb = hubBadge(ck.side || 'wait', ck.side ? (ck.side === 'long' ? '롱 우세' : '숏 우세') : '관망'); hb.style.fontSize = '15px'; hb.style.padding = '1px 12px'; head.appendChild(hb);
    head.appendChild(mk('b', '진입 준비도 ' + (ck.total ? ck.pass + ' / ' + ck.total : '—'), 'font-size:14px'));
    const stc = ck.status === 'ready' ? '#66bb6a' : ck.status === 'wait' ? '#ffb74d' : '#9aa3b5'; head.appendChild(mk('span', ck.text, 'font-weight:700;color:' + stc));
    left.appendChild(head);
    left.appendChild(mk('div', v.text, 'margin:4px 0;opacity:.9'));
    if (ck.items.length) {
      const ul = mk('div', undefined, 'margin-top:4px');
      for (const it of ck.items) { const li = mk('div', undefined, 'display:flex;gap:6px'); li.appendChild(mk('span', it.ok ? '✓' : '✗', 'width:14px;font-weight:700;color:' + (it.ok ? '#66bb6a' : '#ef5350'))); li.appendChild(mk('span', it.label + (it.note ? ' — ' + it.note : ''), it.ok ? '' : 'color:#ffb74d')); ul.appendChild(li); }
      if (ck.ai) { const li = mk('div', undefined, 'display:flex;gap:6px;opacity:.7'); li.appendChild(mk('span', ck.ai === 'opp' ? '△' : '·', 'width:14px')); li.appendChild(mk('span', '참고: AI 규칙 점수는 ' + (ck.ai === 'same' ? '같은 방향' : ck.ai === 'opp' ? '반대 방향' : '의견 없음') + ' (과거 검증에서 우위가 없어 판정에는 넣지 않음)')); ul.appendChild(li); }
      left.appendChild(ul);
    }
    const plan = mk('div', undefined, 'margin-top:8px;padding:6px 8px;border-radius:7px;background:#161b28');
    plan.appendChild(mk('b', '추천 계획'));
    if (!best) plan.appendChild(mk('div', ck.side ? '공통 규칙을 통과한 ' + (ck.side === 'long' ? '롱' : '숏') + ' 진입 계획이 없습니다.' : '방향이 없어 계획을 만들지 않았습니다.', 'opacity:.75'));
    else {
      const n = best.n, M = {entry: '지표(진입 계획)', levels: '거래량 구간', fractal: '엘리엇'}, KO = {'1d': '1일', '4h': '4시간', '1h': '1시간', '30m': '30분', '15m': '15분'};
      plan.appendChild(mk('div', KO[best.tf] + ' · ' + M[best.method] + (n.inZone ? ' · 지금 구간 안' : ' · 현재가 대비 ' + (n.distPct * 100).toFixed(1) + '%')));
      plan.appendChild(mk('div', '진입 ' + (n.hi - n.lo > 1e-12 ? hubFmt(n.lo) + ' ~ ' + hubFmt(n.hi) : hubFmt(n.mid)) + ' · 손절 ' + hubFmt(n.stop) + ' · 목표 ' + hubFmt(n.t1) + (n.t2 != null ? ' / ' + hubFmt(n.t2) : '') + ' · 손익비 ' + n.rr.toFixed(2), 'font-weight:700'));
      const sz = mk('div', undefined, 'margin-top:4px;display:flex;gap:6px;align-items:center;flex-wrap:wrap');
      sz.appendChild(document.createTextNode('포지션 크기: 계좌'));
      const ai = mk('input', undefined, 'width:80px'); ai.type = 'number'; ai.min = '1'; ai.value = String(HUB.acct); ai.setAttribute('aria-label', '계좌 USDT'); sz.appendChild(ai);
      sz.appendChild(document.createTextNode('USDT × 위험'));
      const ri = mk('input', undefined, 'width:55px'); ri.type = 'number'; ri.min = '0.1'; ri.step = '0.1'; ri.value = String(HUB.risk); ri.setAttribute('aria-label', '위험 %'); sz.appendChild(ri); sz.appendChild(document.createTextNode('%'));
      const out = mk('div', undefined, 'margin-top:2px;opacity:.95'); plan.appendChild(sz); plan.appendChild(out);
      const upd = () => {
        HUB.acct = Number(ai.value) || HUB.acct; HUB.risk = Number(ri.value) || HUB.risk; hubStore();
        const s = hubSize(Number(ai.value), Number(ri.value), n);
        out.textContent = s ? '손절 시 손실 ≈ ' + s.riskUsd.toFixed(2) + ' USDT · 수량 ≈ ' + s.qty.toFixed(1) + ' ENA · 명목 ≈ ' + s.notional.toFixed(1) + ' USDT · 필요 레버리지 ≈ ' + s.lev.toFixed(2) + '배' + (s.lev > 3 ? ' ⚠ 3배 초과(청산가가 손절보다 가까울 수 있음)' : '') : '계좌와 위험 %를 입력하세요';
      };
      ai.oninput = upd; ri.oninput = upd; upd();
    }
    left.appendChild(plan); root.appendChild(left);
    // ---- 오른쪽: 시간봉 × 방법 매트릭스
    const right = mk('div', undefined, 'flex:1 1 380px;min-width:300px');
    right.appendChild(mk('b', '시간봉 × 방법 한눈에'));
    const tbl = mk('table', undefined, 'border-collapse:collapse;margin-top:4px'), th = tbl.insertRow();
    for (const h of ['시간봉', '지표', '엘리엇', '거래량(참고)', 'AI(참고)', '판정']) th.appendChild(mk('th', h, 'text-align:left;color:#8a8f98;font-weight:600;padding:1px 12px 1px 0;white-space:nowrap'));
    const KO = {'1d': '1일', '4h': '4시간', '1h': '1시간', '30m': '30분', '15m': '15분'}, ST = {agree: '일치', single: '한쪽만', conflict: '충돌', none: '—'};
    for (const tf of ['1d', '4h', '1h', '30m', '15m']) {
      const r = res.rows.find(x => x.tf === tf), vr = v.rows.find(x => x.tf === tf); if (!r || !vr) continue;
      const tr = tbl.insertRow(); tr.insertCell().textContent = KO[tf];
      tr.insertCell().appendChild(hubBadge(r.op.ind)); tr.insertCell().appendChild(hubBadge(r.op.ell));
      const lv = [r.cells.levels, r.cells.levels2].filter(c => c && c.ok).map(c => c.side), lc = tr.insertCell();
      if (lv.length) lv.forEach((sd, i) => { const b = hubBadge(sd); b.style.marginRight = '3px'; b.style.opacity = '.7'; lc.appendChild(b); }); else lc.appendChild(hubBadge(null));
      const ab = hubBadge(r.ai && r.ai.side ? r.ai.side : null); ab.style.opacity = '.7'; tr.insertCell().appendChild(ab);
      const jd = tr.insertCell(); jd.textContent = ST[vr.state] + (vr.side ? ' · ' + (vr.side === 'long' ? '롱' : '숏') : ''); jd.style.color = vr.state === 'conflict' ? '#ffb74d' : vr.state === 'none' ? '#8a8f98' : '';
      for (const td of tr.cells) { td.style.padding = '2px 12px 2px 0'; td.style.whiteSpace = 'nowrap'; td.style.textAlign = 'left'; }
    }
    right.appendChild(tbl);
    right.appendChild(mk('div', '판정에는 지표와 엘리엇만 반영합니다(같은 시간봉에서 일치 2점·한쪽만 1점·충돌 0점 × 시간봉 무게). 거래량 구간은 가격 근거, AI는 검증상 우위가 없어 참고로만 표시합니다. 자세한 가격과 근거는 위 탭에서 봅니다.', 'margin-top:4px;opacity:.6;font-size:11px'));
    root.appendChild(right); pane.appendChild(root);
    pane.appendChild(mk('div', '이 허브는 분석을 한 곳에 모은 것이지 매매 신호가 아닙니다. 판정 규칙·체크리스트 기준·위험 %는 과거 통계로 검증되지 않았습니다. 큰 시간봉이 방향을 정하고 작은 시간봉은 진입 시점만 정하는 top-down 순서로 읽고, 시간봉을 바꿔 가며 내 생각에 맞는 답을 찾는 것(확증 편향)은 피하세요.', 'margin-top:8px;opacity:.55;font-size:11px'));
  } catch (err) { /* 허브 표시 실패가 차트를 깨뜨리지 않게 한다 */ }
}
setTimeout(function () { try { hubEnsure(); } catch (e) { /* 아직 화면이 없으면 첫 그리기에서 만든다 */ } }, 0);
// ---- /분석 허브 ----
