// ---- 가격 종합: 진입 계획 카드(지표) · 거래량 매물대 · 프랙탈 엘리엇(파동)이 내놓는 진입·손절·목표 가격을 같은 규칙·같은 현재가로 한 표에 모은다. ----
// 세 방법은 보는 것이 달라 가격이 같을 수 없다(지표 구간 · 거래량이 쌓인 가격 · 파동의 되돌림 비율). 대신 ① 같은 현재가 ② 같은 현실성 규칙 ③ 같은 모양으로 비교하고
// ④ 가격이 겹치는 곳(합의 구간)을 따로 보여준다. 매매 신호가 아니며 과거 통계로 검증되지 않았다.
const CF_TFS = ['15m', '30m', '1h', '4h', '1d'];
const CF_KO = {'15m': '15분', '30m': '30분', '1h': '1시간', '4h': '4시간', '1d': '1일'};
const CF_RULE = {reachAtr: 3.0, minStopAtr: 0.8, maxStopAtr: 3.5, maxStopPct: 0.10, tpAtr: 4.5, minRR: 1.2, reachPct: 0.08, clusterPct: 0.006};
const CF = {levels: null, levelsAt: 0, busy: false, collapsed: false, lineOn: false, loaded: false, box: null, lines: [], key: '', last: null};
function cfStore() {
  try {
    if (!CF.loaded) { CF.loaded = true; const v = JSON.parse(localStorage.getItem('ena_confluence_v1') || '{}'); if (typeof v.collapsed === 'boolean') CF.collapsed = v.collapsed; if (typeof v.lineOn === 'boolean') CF.lineOn = v.lineOn; }
    else localStorage.setItem('ena_confluence_v1', JSON.stringify({collapsed: CF.collapsed, lineOn: CF.lineOn}));
  } catch (e) { /* 기억하지 못해도 동작한다 */ }
}
// 공통 현실성 규칙. raw = {side, lo, hi, stop, targets[]} → {ok, ...} 또는 {ok:false, why}. atr 이 없으면(1일봉 등) 퍼센트 한도만 쓴다.
function cfNorm(raw, price, atr) {
  if (!raw || !(price > 0)) return {ok: false, why: '—'};
  const long = raw.side === 'long', lo = Math.min(raw.lo, raw.hi), hi = Math.max(raw.lo, raw.hi), mid = (lo + hi) / 2;
  const dist = price >= lo && price <= hi ? 0 : (price < lo ? lo - price : price - hi), distPct = dist / price;
  if (atr ? dist > CF_RULE.reachAtr * atr : distPct > CF_RULE.reachPct) return {ok: false, why: '진입 구간이 현재가에서 ' + (distPct * 100).toFixed(1) + '% 멀어 지금은 무효'};
  let stop = raw.stop, risk = Math.abs(mid - stop), widened = false;
  if ((long && stop >= lo) || (!long && stop <= hi)) return {ok: false, why: '손절이 진입 구간 안쪽이라 무효'};
  if (atr && risk < CF_RULE.minStopAtr * atr) { risk = CF_RULE.minStopAtr * atr; stop = long ? mid - risk : mid + risk; widened = true; }
  if ((atr && risk > CF_RULE.maxStopAtr * atr) || risk > CF_RULE.maxStopPct * mid) return {ok: false, why: '손절 폭이 ' + (risk / mid * 100).toFixed(1) + '%로 너무 넓어 무효'};
  let ts = (raw.targets || []).filter(t => long ? t > hi : t < lo).sort((a, b) => long ? a - b : b - a), capped = false;
  if (!ts.length) return {ok: false, why: '이익 쪽 목표가 없음(이미 넘었거나 계산 불가)'};
  if (atr) { const cap = long ? mid + CF_RULE.tpAtr * atr : mid - CF_RULE.tpAtr * atr; ts = ts.map(t => { const o = long ? t > cap : t < cap; if (o) capped = true; return o ? cap : t; }); }
  const rr = Math.abs(ts[0] - mid) / risk;
  return {ok: true, side: raw.side, lo, hi, mid, stop, t1: ts[0], t2: ts[1] != null ? ts[1] : null, rr, poor: rr < CF_RULE.minRR, widened, capped, distPct, inZone: dist === 0};
}
function cfAtr(name) {
  try { const d = FRC.data[name]; if (!d || !d.bars || d.bars.length < 30) return null; const a = stAtr(d.bars.map(b => b.high), d.bars.map(b => b.low), d.bars.map(b => b.close)); return a[a.length - 1]; } catch (e) { return null; }
}
function cfPrice() { return typeof bars !== 'undefined' && bars.length ? bars[bars.length - 1].close : null; }
// 세 방법의 원자료를 같은 모양으로: {side, lo, hi, stop, targets, tag}
function cfFromEntry(tf) {
  const p = ENT && ENT.data && ENT.data.plans && ENT.data.plans.timeframes ? ENT.data.plans.timeframes[tf] : null;
  if (!p || p.status !== 'ok' || !p.best) return {none: true};
  const b = p.best;
  if (!['in_zone', 'approaching', 'waiting'].includes(b.state)) return {none: true, why: (typeof ENT_STATE !== 'undefined' && ENT_STATE[b.state]) || b.state};
  return {side: b.direction, lo: b.zone[0], hi: b.zone[1], stop: b.stop, targets: b.targets.slice(), tag: (typeof ENT_SETUP !== 'undefined' ? ENT_SETUP[b.setup] : b.setup) + (b.aligned_trend ? '' : ' · 추세 반대')};
}
function cfFromLevels(tf, side) {
  const t = CF.levels && CF.levels.timeframes ? CF.levels.timeframes[tf] : null;
  if (!t) return {none: true};
  const p = t[side];
  if (!p) return {none: true, why: t.why && t.why[side]};
  return {side, lo: p.entry, hi: p.entry, stop: p.stop, targets: [p.take_profit], tag: '거래량 구간'};
}
function cfFromFractal(tf, price) {
  if (typeof frcElliott !== 'function') return {none: true};
  const e = frcElliott(tf); if (!e || e.status !== 'ok') return {none: true, why: '후보 없음'};
  const pl = frcPlan(e, price); if (!pl) return {none: true, why: '계획을 만들 수 없음(손절이 진입 구간 안쪽)'};
  const ML = {with: '4파 끝', pullback: '5파 눌림', correction: '5파 뒤 조정', watch: '조건부(4파 끝 확인 후)'};
  return {side: pl.side, lo: pl.zone[0], hi: pl.zone[1], stop: pl.stop, targets: pl.targets.slice(), tag: ML[pl.mode], cond: pl.mode === 'watch'};
}
// 모든 방법·시간봉의 통과한 계획을 모아 가격이 겹치는 곳(합의 구간)을 찾는다.
function cfClusters(items, price) {
  const out = [];
  for (const side of ['long', 'short']) {
    const xs = items.filter(x => x.n.ok && x.n.side === side).sort((a, b) => a.n.mid - b.n.mid);
    let cur = null;
    for (const x of xs) {
      if (cur && Math.abs(x.n.mid - cur.mid) <= CF_RULE.clusterPct * price) { cur.members.push(x); cur.mid = cur.members.reduce((s, m) => s + m.n.mid, 0) / cur.members.length; cur.lo = Math.min(cur.lo, x.n.lo); cur.hi = Math.max(cur.hi, x.n.hi); }
      else { cur = {side, mid: x.n.mid, lo: x.n.lo, hi: x.n.hi, members: [x]}; out.push(cur); }
    }
  }
  for (const c of out) c.kinds = Array.from(new Set(c.members.map(m => m.method)));
  return out.filter(c => c.members.length >= 2).sort((a, b) => b.kinds.length - a.kinds.length || b.members.length - a.members.length || Math.abs(a.mid - price) - Math.abs(b.mid - price));
}
async function cfLoadLevels() {
  if (CF.busy || Date.now() - CF.levelsAt < 30000) return;
  CF.busy = true;
  try { const r = await fetch('/api/levels'); const j = await r.json(); if (r.ok && !j.error) { CF.levels = j; CF.levelsAt = Date.now(); } } catch (e) { /* 매물대 없이도 나머지는 보인다 */ }
  CF.busy = false; CF.key = ''; cfDraw();
}
function cfFmt(v) { return typeof fmt === 'function' ? fmt(v, 5) : Number(v).toFixed(5); }
function cfBoxEl() {
  if (CF.box && CF.box.isConnected) return CF.box;
  const anchor = document.getElementById('fractal-box') || document.getElementById('chartwrap'); if (!anchor) return null;
  const box = document.createElement('div'); box.id = 'confluence-box';
  box.style.cssText = 'margin:6px 0;padding:8px 10px;border:1px solid #2a2e39;border-radius:8px;background:#131722;color:#d1d4dc;font:12px/1.6 sans-serif';
  anchor.insertAdjacentElement('afterend', box); CF.box = box; return box;
}
function cfClearLines() { for (const l of CF.lines) { try { candle.removePriceLine(l); } catch (e) { /* 이미 없음 */ } } CF.lines = []; }
function cfBuild(price) {
  const rows = [], all = [];
  for (const tf of CF_TFS) {
    const atr = cfAtr(tf), row = {tf, atr, cells: {}};
    const none = (raw) => ({ok: false, why: raw.why || (tf === '30m' ? '이 방법은 30분봉을 계산하지 않음' : '계획 없음')});
    const ent = cfFromEntry(tf), fra = cfFromFractal(tf, price);
    row.cells.entry = ent.none ? none(ent) : Object.assign({tag: ent.tag}, cfNorm(ent, price, atr));
    row.cells.fractal = fra.none ? {ok: false, why: fra.why || '후보 없음'} : Object.assign({tag: fra.tag, cond: fra.cond}, cfNorm(fra, price, atr));
    const lvs = ['long', 'short'].map(sd => cfFromLevels(tf, sd)).map(raw => raw.none ? none(raw) : Object.assign({tag: raw.tag}, cfNorm(raw, price, atr)));
    const okL = lvs.filter(c => c.ok);
    row.cells.levels = okL[0] || lvs.find(c => c.why && c.why !== '계획 없음') || lvs[0];
    if (okL[1]) row.cells.levels2 = okL[1];
    for (const m of ['entry', 'fractal']) if (row.cells[m].ok) all.push({tf, method: m, n: row.cells[m]});
    for (const c of okL) all.push({tf, method: 'levels', n: c});
    const sides = ['entry', 'levels', 'levels2', 'fractal'].map(k => row.cells[k]).filter(c => c && c.ok).map(c => c.side);
    row.agree = !sides.length ? null : sides.every(s => s === sides[0]) ? {side: sides[0], n: sides.length} : {mixed: true, long: sides.filter(s => s === 'long').length, short: sides.filter(s => s === 'short').length};
    rows.push(row);
  }
  return {rows, clusters: cfClusters(all, price)};
}
function cfDraw() {
  try {
    cfStore(); const price = cfPrice(); if (!price) return;
    cfLoadLevels();
    const res = cfBuild(price); CF.last = res;
    const key = JSON.stringify([price.toFixed(5), CF.collapsed, CF.lineOn, res.rows.map(r => Object.keys(r.cells).map(k => r.cells[k].ok ? r.cells[k].mid.toFixed(5) : r.cells[k].why))]);
    if (key === CF.key) return; CF.key = key;
    cfBox(res, price);
    cfClearLines();
    if (CF.lineOn && typeof candle !== 'undefined') for (const c of res.clusters.slice(0, 4)) CF.lines.push(candle.createPriceLine({price: c.mid, color: c.side === 'long' ? '#26a69a' : '#ef5350', lineWidth: 2, lineStyle: 0, axisLabelVisible: true, title: '합의 ' + c.members.length + ' ' + (c.side === 'long' ? '롱' : '숏')}));
  } catch (err) { /* 종합 표 실패가 차트를 깨뜨리지 않게 한다 */ }
}
function cfCell(td, c) {
  td.style.cssText = 'padding:3px 8px;vertical-align:top;text-align:left';
  if (!c || !c.ok) { const d = document.createElement('div'); d.textContent = !c ? '—' : c.why || '—'; d.style.opacity = '.55'; td.appendChild(d); return; }
  const b = document.createElement('span'); b.textContent = c.side === 'long' ? '롱' : '숏';
  b.style.cssText = 'display:inline-block;min-width:26px;text-align:center;padding:0 6px;border-radius:5px;font-weight:700;color:#fff;margin-right:5px;background:' + (c.side === 'long' ? '#1e8e5a' : '#c62828') + (c.poor ? ';opacity:.6' : '');
  const l1 = document.createElement('div'); l1.appendChild(b);
  l1.appendChild(document.createTextNode('진입 ' + (Math.abs(c.hi - c.lo) < 1e-12 ? cfFmt(c.mid) : cfFmt(c.lo) + '~' + cfFmt(c.hi)) + (c.inZone ? ' · 지금 구간 안' : ' (' + (c.distPct * 100).toFixed(1) + '%)')));
  if (c.inZone) l1.style.fontWeight = '700';
  const l2 = document.createElement('div'); l2.style.opacity = '.85';
  l2.textContent = '손절 ' + cfFmt(c.stop) + (c.widened ? '↔' : '') + ' · 목표 ' + cfFmt(c.t1) + (c.capped ? '↧' : '') + (c.t2 != null ? ' / ' + cfFmt(c.t2) : '') + ' · 손익비 ' + c.rr.toFixed(2) + (c.poor ? ' (낮음)' : '');
  td.appendChild(l1); td.appendChild(l2);
  if (c.tag || c.cond) { const l3 = document.createElement('div'); l3.textContent = (c.tag || '') + (c.cond ? ' · 조건부' : ''); l3.style.cssText = 'opacity:.55;font-size:11px'; td.appendChild(l3); }
}
function cfBox(res, price) {
  const box = cfBoxEl(); if (!box) return; box.textContent = '';
  const mk = (tag, text, css) => { const d = document.createElement(tag); if (text !== undefined) d.textContent = text; if (css) d.style.cssText = css; return d; };
  const head = mk('div', undefined, 'display:flex;gap:12px;align-items:center;flex-wrap:wrap');
  head.appendChild(mk('b', '가격 종합 — 지표 · 거래량 · 엘리엇의 진입 가격을 같은 규칙으로 비교 (참고용 가설)'));
  const lb = document.createElement('label'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = CF.lineOn;
  cb.onchange = () => { CF.lineOn = cb.checked; cfStore(); CF.key = ''; cfDraw(); };
  lb.appendChild(cb); lb.appendChild(document.createTextNode(' 합의 구간을 차트에 선으로')); lb.style.cursor = 'pointer'; head.appendChild(lb);
  const fold = mk('span', CF.collapsed ? '▸ 펼치기' : '▾ 접기', 'margin-left:auto;cursor:pointer;opacity:.8'); fold.onclick = () => { CF.collapsed = !CF.collapsed; cfStore(); CF.key = ''; cfDraw(); }; head.appendChild(fold); box.appendChild(head);
  if (CF.collapsed) return;
  box.appendChild(mk('div', '공통 규칙 · 현재가 ' + cfFmt(price) + ' 하나로 거리를 계산 · 진입은 3 ATR(평균 변동폭의 3배) 이내 · 손절 0.8~3.5 ATR(최대 10%) · 목표는 4.5 ATR 이내(↧ 제한됨) · 손익비 1.2 미만은 "(낮음)" · 손절을 넓혔으면 ↔', 'opacity:.7;font-size:11px;margin-top:4px'));
  const tbl = mk('table', undefined, 'border-collapse:collapse;width:100%;margin-top:6px'), th = tbl.insertRow();
  for (const h of ['시간봉', '지표 (진입 계획 카드)', '거래량 (매물대)', '엘리엇 (프랙탈)', '방향 일치']) th.appendChild(mk('th', h, 'text-align:left;color:#8a8f98;font-weight:600;padding:3px 8px;white-space:nowrap'));
  for (const r of res.rows) {
    const tr = tbl.insertRow(); tr.style.borderTop = '1px solid #2a2e39';
    cfCell(tr.insertCell(), null); tr.cells[0].textContent = CF_KO[r.tf] + (r.atr ? '' : ''); tr.cells[0].style.cssText = 'padding:3px 8px;vertical-align:top;white-space:nowrap;font-weight:600';
    cfCell(tr.insertCell(), r.cells.entry);
    const lc = tr.insertCell(); cfCell(lc, r.cells.levels); if (r.cells.levels2) { const hr = document.createElement('div'); hr.style.cssText = 'height:3px'; lc.appendChild(hr); const x = document.createElement('td'); cfCell(x, r.cells.levels2); while (x.firstChild) lc.appendChild(x.firstChild); }
    cfCell(tr.insertCell(), r.cells.fractal);
    const ac = tr.insertCell(); ac.style.cssText = 'padding:3px 8px;vertical-align:top;white-space:nowrap';
    if (!r.agree) ac.textContent = '—'; else if (r.agree.mixed) { ac.textContent = '엇갈림 (롱 ' + r.agree.long + ' · 숏 ' + r.agree.short + ')'; ac.style.color = '#ffb74d'; } else { ac.textContent = (r.agree.side === 'long' ? '롱' : '숏') + ' 일치 ' + r.agree.n + '개'; ac.style.color = r.agree.side === 'long' ? '#66bb6a' : '#ef5350'; }
  }
  box.appendChild(tbl);
  const cl = mk('div', undefined, 'margin-top:6px;padding:6px 8px;border-radius:6px;background:#1a1f2e');
  cl.appendChild(mk('b', '합의 구간 (가격이 0.6% 이내로 겹치는 계획 2개 이상)'));
  if (!res.clusters.length) cl.appendChild(mk('div', '지금은 겹치는 구간이 없습니다 — 세 방법이 서로 다른 가격을 가리키고 있습니다.', 'opacity:.75'));
  for (const c of res.clusters.slice(0, 5)) {
    const d = mk('div', undefined, 'margin-top:2px'), b = mk('span', c.side === 'long' ? '롱' : '숏', 'display:inline-block;min-width:26px;text-align:center;padding:0 6px;border-radius:5px;font-weight:700;color:#fff;margin-right:6px;background:' + (c.side === 'long' ? '#1e8e5a' : '#c62828'));
    d.appendChild(b); const M = {entry: '지표', levels: '매물대', fractal: '엘리엇'};
    d.appendChild(document.createTextNode(cfFmt(c.lo) + (c.hi - c.lo > 1e-12 ? ' ~ ' + cfFmt(c.hi) : '') + ' (현재가 ' + ((c.mid / price - 1) * 100 >= 0 ? '+' : '') + ((c.mid / price - 1) * 100).toFixed(1) + '%) · ' + c.members.length + '개 겹침 [' + c.members.map(m => CF_KO[m.tf] + ' ' + M[m.method]).join(', ') + ']' + (c.kinds.length >= 2 ? ' — 서로 다른 방법 ' + c.kinds.length + '가지' : ' — 같은 방법끼리')));
    cl.appendChild(d);
  }
  box.appendChild(cl);
  box.appendChild(mk('div', '세 방법은 보는 기준이 달라 가격이 같을 수 없습니다(지표 구간 · 거래량이 쌓인 가격 · 파동의 되돌림 비율). 가격이 겹치는 곳은 여러 근거가 같은 자리를 가리킨다는 뜻일 뿐 반등·하락을 보장하지 않으며, 과거 통계로 검증되지 않았습니다.', 'opacity:.65;font-size:11px;margin-top:4px'));
}
setInterval(function () { cfDraw(); }, 5000);
if (typeof structDraw === 'function' && !structDraw.__cf) { const cfBase = structDraw; structDraw = function () { cfBase(); cfDraw(); }; structDraw.__cf = true; }
// ---- /가격 종합 ----
