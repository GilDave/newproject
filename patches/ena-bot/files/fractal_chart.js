// ---- 프랙탈 엘리엇: 4시간·1시간·30분·15분의 엘리엇 후보를 한 차트에 겹쳐 그린다(참고용 가설). structure_chart.js 의 계산(stAnalyze)을 그대로 쓴다. ----
const FRC_TFS = [['4h', '240', 14400, '#ffd54f', 3], ['1h', '60', 3600, '#4fc3f7', 2], ['30m', '30', 1800, '#ba68c8', 1], ['15m', '15', 900, '#81c784', 1]];
const FRC_NAME = {'4h': '4시간', '1h': '1시간', '30m': '30분', '15m': '15분'};
const FRC = {on: {'4h': true, '1h': true, '30m': true, '15m': true}, inval: true, collapsed: false, loaded: false, data: {}, busy: {}, rev: 0, key: '', series: [], box: null, drawn: {}};
function frcStore() {
  try {
    if (!FRC.loaded) {
      FRC.loaded = true; const v = JSON.parse(localStorage.getItem('ena_fractal_v1') || '{}');
      for (const k of Object.keys(FRC.on)) if (v.on && typeof v.on[k] === 'boolean') FRC.on[k] = v.on[k];
      if (typeof v.inval === 'boolean') FRC.inval = v.inval; if (typeof v.collapsed === 'boolean') FRC.collapsed = v.collapsed;
    } else localStorage.setItem('ena_fractal_v1', JSON.stringify({on: FRC.on, inval: FRC.inval, collapsed: FRC.collapsed}));
  } catch (e) { /* 기억하지 못해도 동작한다 */ }
}
// times(오름차순) 중 t 이하의 가장 큰 값. 없으면 null. 작은 시간봉의 시각을 차트의 봉 시각에 맞출 때 쓴다.
function frcSnapTime(t, times) {
  let lo = 0, hi = times.length - 1, ans = null;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (times[mid] <= t) { ans = times[mid]; lo = mid + 1; } else hi = mid - 1; }
  return ans;
}
function frcPhase(e) {
  if (!e || e.status !== 'ok') return {key: 'none', text: '후보 없음'};
  const nm = e.direction === 'up' ? '상승' : '하락', last = e.pivots[e.pivots.length - 1];
  if (e.complete) return {key: 'done', text: nm + ' 5파 완성 → 조정 가능'};
  if (e.forming5) return {key: 'w5', text: nm + ' 5파 진행 중'};
  if (last && last.provisional) return {key: 'w4', text: nm + ' 4파 진행 중(저점·고점 갱신 가능)'};
  return {key: 'w4end', text: nm + ' 4파 끝 → 5파 가능'};
}
// 가장 큰 시간봉(ref)의 방향과 비교한 한 줄 해석. 매매 신호가 아니라 '어디를 볼지'에 대한 가설이다.
function frcHint(ref, e) {
  if (!e || e.status !== 'ok') return '—';
  if (!ref) return '—';
  if (ref === e) return '기준 시간봉';
  const k = frcPhase(e).key;
  if (ref.direction === e.direction) return k === 'done' ? '큰 방향과 같음 · 5파 완성 → 짧은 조정 뒤 재개 관찰' : '큰 방향과 같음 · 추세 진행 구간';
  return k === 'done' ? '큰 방향과 반대 · 5파 완성 → 되돌림 마무리 가능(큰 방향 재개 관찰)' : '큰 방향과 반대 · 되돌림 진행 중(대기)';
}
// 한 시간봉의 엘리엇 후보를 차트의 봉 시각(mainTimes)에 맞춘 선·표시 데이터로 바꾼다. 차트보다 짧은 시간봉이면 null(겹쳐서 그릴 수 없음).
function frcSeriesData(tfBars, e, mainTimes, tfSec, mainSec) {
  if (!e || e.status !== 'ok' || !mainTimes.length || tfSec < mainSec) return null;
  const pts = [];
  for (const p of e.pivots) {
    const t = frcSnapTime(tfBars[p.i].time, mainTimes); if (t === null) continue;
    if (pts.length && pts[pts.length - 1].time === t) pts.pop();     // 같은 봉에 겹치면 나중 것만
    pts.push({time: t, value: p.price, kind: p.kind, label: Number(p.label), provisional: p.provisional});
  }
  return pts.length >= 2 ? pts : null;
}
// 큰 시간봉의 극점은 작은 시간봉에서도 같은 자리의 극점이다(프랙탈). 같은 종류(고점/저점)이고, 시각 차이가 큰 쪽의 봉 길이 이내이며 가격이 1% 이내면
// 같은 자리로 보고 표시를 하나로 합친다(가장 큰 시간봉의 선에 붙이고 글자는 '4h ③ · 1h ③ …'). items 는 큰 시간봉부터.
function frcMergeMarkers(items) {
  const groups = [];
  for (const it of items) for (const p of it.pts) {
    const g = groups.find(x => x.kind === p.kind && Math.abs(x.time - p.time) <= x.sec && Math.abs(x.value / p.value - 1) < 0.01);
    if (g) g.members.push({name: it.name, p}); else groups.push({kind: p.kind, time: p.time, value: p.value, sec: it.sec, owner: it.name, members: [{name: it.name, p}]});
  }
  const out = {};
  for (const g of groups) {
    const text = g.members.map(x => x.name + ' ' + ST_CIRCLED[x.p.label] + (x.p.provisional ? '?' : '')).join(' · ');
    (out[g.owner] = out[g.owner] || []).push({time: g.time, position: g.kind === 'H' ? 'aboveBar' : 'belowBar', color: frcColor(g.owner), shape: 'circle', text});
  }
  for (const n of Object.keys(out)) { out[n].sort((a, b) => a.time - b.time); for (let i = out[n].length - 1; i > 0; i--) if (out[n][i].time === out[n][i - 1].time) out[n].splice(i, 1); }
  return out;
}
function frcColor(name) { for (const t of FRC_TFS) if (t[0] === name) return t[3]; return '#e0e0e0'; }
// 무효화 가격이 거의 같으면(0.05% 이내) 한 줄로 합친다. 줄은 가장 큰 시간봉에 붙인다.
function frcMergeLevels(items) {
  const out = [];
  for (const it of items) {
    const hit = out.find(o => Math.abs(o.level / it.level - 1) < 0.0005);
    if (hit) hit.names.push(it.name); else out.push({level: it.level, names: [it.name], owner: it.name});
  }
  return out;
}
function frcMainSec() { return typeof barSec === 'function' ? barSec() : (bars.length > 1 ? bars[1].time - bars[0].time : 0); }
function frcClear() { for (const s of FRC.series) { try { chart.removeSeries(s); } catch (err) { /* 이미 없음 */ } } FRC.series = []; }
async function frcFetch(tf) {
  const [name, iv] = tf; const hit = FRC.data[name];
  if (FRC.busy[name] || (hit && Date.now() - hit.at < 60000)) return;
  FRC.busy[name] = true;
  try {
    const r = await fetch('/api/klines?symbol=' + encodeURIComponent(SYMBOL) + '&interval=' + iv + '&limit=500');
    const j = await r.json(); if (!r.ok || !Array.isArray(j) || j.length < 60) throw new Error('klines');
    const tb = j.map(x => ({time: x[0] / 1000, open: +x[1], high: +x[2], low: +x[3], close: +x[4]}));
    const res = stAnalyze(tb.map(b => b.open), tb.map(b => b.high), tb.map(b => b.low), tb.map(b => b.close));
    FRC.data[name] = {at: Date.now(), bars: tb, res};
  } catch (err) { FRC.data[name] = {at: Date.now(), bars: [], res: null, error: true}; }
  FRC.busy[name] = false; FRC.rev++; FRC.key = ''; frcDraw();
}
function frcFmt(v) { return typeof fmt === 'function' ? fmt(v, 5) : Number(v).toFixed(5); }
function frcBoxEl() {
  if (FRC.box && FRC.box.isConnected) return FRC.box;
  const wrap = document.getElementById('chartwrap') || document.getElementById('chart'); if (!wrap) return null;
  const box = document.createElement('div'); box.id = 'fractal-box';
  box.style.cssText = 'margin:6px 0;padding:8px 10px;border:1px solid #2a2e39;border-radius:8px;background:#131722;color:#d1d4dc;font:12px/1.6 sans-serif';
  wrap.insertAdjacentElement('afterend', box); FRC.box = box; return box;
}
function frcBox() {
  const box = frcBoxEl(); if (!box) return;
  box.textContent = '';
  const mk = (tag, text, css) => { const d = document.createElement(tag); if (text !== undefined) d.textContent = text; if (css) d.style.cssText = css; return d; };
  const head = mk('div', undefined, 'display:flex;gap:12px;align-items:center;flex-wrap:wrap');
  head.appendChild(mk('b', '프랙탈 엘리엇 — 4시간·1시간·30분·15분을 한 차트에 (참고용 가설)'));
  const lb = document.createElement('label'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = FRC.inval;
  cb.onchange = () => { FRC.inval = cb.checked; frcStore(); FRC.key = ''; frcDraw(); };
  lb.appendChild(cb); lb.appendChild(document.createTextNode(' 무효화선')); lb.style.cursor = 'pointer'; head.appendChild(lb);
  const fold = mk('span', FRC.collapsed ? '▸ 펼치기' : '▾ 접기', 'margin-left:auto;cursor:pointer;opacity:.8');
  fold.onclick = () => { FRC.collapsed = !FRC.collapsed; frcStore(); frcBox(); }; head.appendChild(fold); box.appendChild(head);
  if (FRC.collapsed) return;
  const mainSec = frcMainSec(), price = bars.length ? bars[bars.length - 1].close : null;
  const tbl = mk('table', undefined, 'border-collapse:collapse;width:100%;margin-top:6px'), th = tbl.insertRow();
  for (const h of ['시간봉', '현재 위치', '무효화 가격 (현재가 대비)', '5파 목표 구간', '큰 시간봉과 비교', '차트']) th.appendChild(mk('th', h, 'text-align:left;color:#8a8f98;font-weight:600;padding:3px 8px;white-space:nowrap'));
  let ref = null;
  for (const tf0 of FRC_TFS) { const name = tf0[0], d = FRC.data[name]; if (FRC.on[name] && d && d.res && d.res.elliott.status === 'ok') { ref = d.res.elliott; break; } }
  for (const [name, iv, sec, color] of FRC_TFS) {
    const d = FRC.data[name], e = d && d.res ? d.res.elliott : null, tr = tbl.insertRow(); tr.style.borderTop = '1px solid #2a2e39';
    const c0 = tr.insertCell(), c = document.createElement('input'); c.type = 'checkbox'; c.checked = FRC.on[name];
    c.onchange = () => { FRC.on[name] = c.checked; frcStore(); FRC.key = ''; frcDraw(); frcBox(); };
    c0.appendChild(c); const chip = mk('span', ' ■ ', 'color:' + color + ';font-weight:700'); c0.appendChild(chip); c0.appendChild(document.createTextNode(FRC_NAME[name]));
    const ph = !d ? {text: '불러오는 중…'} : d.error ? {text: '시세를 받지 못함'} : frcPhase(e);
    tr.insertCell().textContent = ph.text;
    tr.insertCell().textContent = e && e.status === 'ok' ? frcFmt(e.invalidation.level) + (price ? ' (' + ((e.invalidation.level / price - 1) * 100).toFixed(1) + '%)' : '') + ' ' + (e.invalidation.side === 'above' ? '위로' : '아래로') + ' 벗어나면 무효' : '—';
    tr.insertCell().textContent = e && e.status === 'ok' && e.targets.length ? frcFmt(Math.min(...e.targets)) + ' ~ ' + frcFmt(Math.max(...e.targets)) : '—';
    tr.insertCell().textContent = e && e.status === 'ok' ? frcHint(ref, e) : '—';
    const drawn = FRC.drawn[name]; tr.insertCell().textContent = !FRC.on[name] ? '꺼짐' : sec < mainSec ? '차트보다 짧아 생략' : drawn ? '그림' : '—';
    for (const td of tr.cells) td.style.cssText = 'padding:3px 8px;vertical-align:top';
  }
  box.appendChild(tbl);
  box.appendChild(mk('div', '차트의 시간봉(' + Math.round(mainSec / 60) + '분)보다 짧은 시간봉은 선이 겹쳐 그릴 수 없어 표에만 나옵니다. 15분 차트에서 보면 네 시간봉이 모두 겹쳐 그려집니다. 굵은 선일수록 큰 시간봉입니다.', 'opacity:.65;font-size:11px;margin-top:4px'));
  box.appendChild(mk('div', '엘리엇 파동 세기는 주관적이고 앞으로의 가격을 보장하지 않습니다. 이 규칙의 신호는 과거 통계로 검증되지 않았습니다. "큰 시간봉과 비교"는 매매 신호가 아니라 어디를 살펴볼지에 대한 가설입니다.', 'opacity:.65;font-size:11px'));
}
function frcDraw() {
  try {
    frcStore();
    const L = bars.length; if (L < 2) return;
    for (const tf of FRC_TFS) if (FRC.on[tf[0]]) frcFetch(tf);
    const key = L + ':' + bars[0].time + ':' + bars[L - 1].time + ':' + JSON.stringify(FRC.on) + FRC.inval + ':' + FRC.rev;
    if (key === FRC.key) return;
    FRC.key = key; frcClear(); FRC.drawn = {};
    const mainTimes = bars.map(b => b.time), mainSec = frcMainSec(), plan = [];
    for (const [name, , sec, color, width] of FRC_TFS) {
      const d = FRC.data[name]; if (!FRC.on[name] || !d || !d.res) continue;
      const e = d.res.elliott, pts = frcSeriesData(d.bars, e, mainTimes, sec, mainSec); if (!pts) continue;
      plan.push({name, sec, color, width, e, pts});
    }
    // 큰 시간봉의 극점은 작은 시간봉에서도 같은 자리의 극점이다(프랙탈). 같은 자리의 표시는 하나로 합쳐 겹치지 않게 한다.
    const groups = frcMergeMarkers(plan.map(x => ({name: x.name, sec: x.sec, pts: x.pts})));
    const lines = frcMergeLevels(plan.map(x => ({name: x.name, color: x.color, level: x.e.invalidation.level})));
    for (const x of plan) {
      const s = chart.addLineSeries({color: x.color, lineWidth: x.width, lineStyle: 0, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false});
      s.setData(x.pts.map(p => ({time: p.time, value: p.value})));
      s.setMarkers(groups[x.name] || []);
      FRC.series.push(s); FRC.drawn[x.name] = true;
      if (FRC.inval) for (const ln of lines) if (ln.owner === x.name) s.createPriceLine({price: ln.level, color: x.color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: ln.names.join('·') + ' 무효화'});
    }
    if (plan.length && typeof STR !== 'undefined' && STR.ell) { try { STR.ell.applyOptions({visible: false}); } catch (err) { /* 겹쳐 그린 엘리엇이 있으니 단일 시간봉 엘리엇은 숨긴다 */ } }
    frcBox();
  } catch (err) { /* 겹쳐 그리기에 실패해도 차트는 그대로 */ }
}
if (typeof structDraw === 'function' && !structDraw.__fr) { const frcBase = structDraw; structDraw = function () { frcBase(); frcDraw(); }; structDraw.__fr = true; }
// ---- /프랙탈 엘리엇 ----
