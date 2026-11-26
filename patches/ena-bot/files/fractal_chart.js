// ---- 프랙탈 엘리엇: 4시간·1시간·30분·15분의 엘리엇 후보를 한 차트에 겹쳐 그린다(참고용 가설). structure_chart.js 의 계산(stAnalyze)을 그대로 쓴다. ----
const FRC_TFS = [['4h', '240', 14400, '#ffd54f', 3], ['1h', '60', 3600, '#4fc3f7', 2], ['30m', '30', 1800, '#ba68c8', 1], ['15m', '15', 900, '#81c784', 1]];
const FRC_NAME = {'4h': '4시간', '1h': '1시간', '30m': '30분', '15m': '15분'};
const FRC_FINE_SCALES = [1.5, 2.0, 3.0], FRC_FINE_TFS = ['30m', '15m'];   // 단타 민감 모드: 30분·15분에서 더 작은 스윙(1.5 ATR)도 인정
const FRC = {on: {'4h': true, '1h': true, '30m': true, '15m': true}, fine: false, inval: true, collapsed: false, loaded: false, data: {}, busy: {}, rev: 0, key: '', series: [], box: null, drawn: {}};
function frcStore() {
  try {
    if (!FRC.loaded) {
      FRC.loaded = true; const v = JSON.parse(localStorage.getItem('ena_fractal_v1') || '{}');
      for (const k of Object.keys(FRC.on)) if (v.on && typeof v.on[k] === 'boolean') FRC.on[k] = v.on[k];
      if (typeof v.fine === 'boolean') FRC.fine = v.fine; if (typeof v.inval === 'boolean') FRC.inval = v.inval; if (typeof v.collapsed === 'boolean') FRC.collapsed = v.collapsed;
    } else localStorage.setItem('ena_fractal_v1', JSON.stringify({on: FRC.on, fine: FRC.fine, inval: FRC.inval, collapsed: FRC.collapsed}));
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
// 한 시간봉의 '롱/숏 관점'. 그 시간봉의 엘리엇 위치에서 다음에 기대되는 방향이다(매매 신호가 아니라 가설).
//  4파 끝 → 5파 가능: 파동 방향 · 5파 진행 중: 같은 방향(늦은 구간, 추격 주의) · 4파 조정 중: 관망 · 5파 완성: 조정 가능 → 반대 방향 · 후보 없음: 관망
// ref(가장 큰 시간봉의 후보)가 있으면 큰 흐름과 반대인지(역추세 단타)도 알려준다.
function frcSide(e, ref) {
  const none = {side: 'wait', badge: '관망', why: '후보 없음', counter: false};
  if (!e || e.status !== 'ok') return none;
  const up = e.direction === 'up', k = frcPhase(e).key, own = up ? 'long' : 'short', opp = up ? 'short' : 'long';
  let r;
  if (k === 'w4end') r = {side: own, why: '4파가 끝나 5파가 이어질 수 있는 자리'};
  else if (k === 'w5') r = {side: own, why: '5파 진행 중 — 늦은 구간이라 추격 주의'};
  else if (k === 'w4') r = {side: 'wait', why: '4파 조정 중 — 조정이 끝나는 자리를 기다림'};
  else if (k === 'done') r = {side: opp, why: '5파 완성 — 조정(되돌림)이 나올 수 있음'};
  else return none;
  r.badge = r.side === 'long' ? '롱' : r.side === 'short' ? '숏' : '관망';
  const refSide = ref && ref !== e ? frcSideRaw(ref) : null;
  r.counter = !!(refSide && r.side !== 'wait' && refSide !== 'wait' && r.side !== refSide);
  return r;
}
function frcSideRaw(e) {
  if (!e || e.status !== 'ok') return 'wait';
  const up = e.direction === 'up', k = frcPhase(e).key;
  return k === 'w4end' || k === 'w5' ? (up ? 'long' : 'short') : k === 'done' ? (up ? 'short' : 'long') : 'wait';
}
// 여러 시간봉의 관점을 한 줄로: 큰 흐름(4h·1h)과 단타(30m·15m)를 따로 합산(큰 시간봉일수록 무게). items: [{name, side}]
const FRC_W = {'4h': 4, '1h': 3, '30m': 2, '15m': 1};
function frcVote(items) {
  let l = 0, sh = 0; for (const it of items) { const w = FRC_W[it.name] || 1; if (it.side === 'long') l += w; else if (it.side === 'short') sh += w; }
  const side = l === sh ? 'wait' : (Math.abs(l - sh) <= 1 && l && sh) ? 'wait' : l > sh ? 'long' : 'short';
  return {side, long: l, short: sh};
}
function frcOverall(items) {
  const big = frcVote(items.filter(x => x.name === '4h' || x.name === '1h')), small = frcVote(items.filter(x => x.name === '30m' || x.name === '15m'));
  const nm = (v) => v === 'long' ? '롱' : v === 'short' ? '숏' : '관망';
  let note;
  if (big.side === 'wait' && small.side === 'wait') note = '뚜렷한 방향이 없습니다 → 관망';
  else if (big.side === 'wait') note = '큰 흐름은 불분명하고 단타 쪽만 ' + nm(small.side) + ' → 짧게, 작은 크기로만 참고';
  else if (small.side === 'wait' || small.side === big.side) note = '큰 흐름 ' + nm(big.side) + (small.side === big.side ? ' · 단타도 같은 방향 → 눌림(되돌림) 뒤 ' + nm(big.side) + ' 재개를 관찰' : ' · 단타는 대기 → 단기 파동이 정리되길 기다림');
  else note = '큰 흐름은 ' + nm(big.side) + ', 단타는 ' + nm(small.side) + ' → 단타 ' + nm(small.side) + '은 큰 흐름과 반대(역추세)라 짧게. 그 움직임이 끝나는 자리에서 ' + nm(big.side) + ' 재개를 관찰';
  return {big, small, note};
}
// 한 시간봉의 '가설 진입 계획'(진입 구간 · 손절 · 목표 · 손익비). 파동 위치에서 정석적으로 쓰는 되돌림 비율로 만든 참고 가격이다(매매 신호가 아님).
//  4파 끝(w4end): 4파 저점(고점) 근처 진입, 손절은 그 바깥 · 5파 진행(w5): 지금까지의 5파를 38~50% 되돌린 자리(눌림) · 5파 완성(done): 반대 방향, 고점(저점) 근처에서 전체 상승(하락)의 38.2%·61.8% 되돌림을 목표
//  4파 조정 중(w4): 관망이지만 '4파가 끝날 수 있는 구간'(3파의 23.6~50% 되돌림)을 조건부 관찰 구간으로 보여준다
function frcPlan(e, price) {
  if (!e || e.status !== 'ok') return null;
  const ph = frcPhase(e), k = ph.key, s = e.direction === 'up' ? 1 : -1, q = e.pivots.map(p => p.price);
  let mode, zone, stop, targets, side;
  if (k === 'w4end') {
    const w = Math.abs(q[3] - q[4]); mode = 'with'; side = s > 0 ? 'long' : 'short';
    zone = [q[4], q[4] + s * 0.3 * w]; stop = q[4] - s * 0.1 * w; targets = e.targets.slice();
  } else if (k === 'w5') {
    const w5 = Math.abs(q[5] - q[4]); mode = 'pullback'; side = s > 0 ? 'long' : 'short';
    zone = [q[5] - s * 0.5 * w5, q[5] - s * 0.382 * w5]; stop = e.invalidation.level; targets = e.targets.slice();
  } else if (k === 'done') {
    const w5 = Math.abs(q[5] - q[4]), tot = Math.abs(q[5] - q[0]); mode = 'correction'; side = s > 0 ? 'short' : 'long';
    zone = [q[5] - s * 0.15 * w5, q[5]]; stop = e.invalidation.level + s * 0.1 * w5; targets = [q[5] - s * 0.382 * tot, q[5] - s * 0.618 * tot];
  } else if (k === 'w4') {
    const w3 = Math.abs(q[3] - q[2]); mode = 'watch'; side = s > 0 ? 'long' : 'short';
    zone = [q[3] - s * 0.5 * w3, q[3] - s * 0.236 * w3]; stop = e.invalidation.level; targets = e.targets.slice();
  } else return null;
  const lo = Math.min(zone[0], zone[1]), hi = Math.max(zone[0], zone[1]), long = side === 'long', mid = (lo + hi) / 2;
  if (long ? !(stop < lo) : !(stop > hi)) return null;                                   // 손절이 진입 구간의 바깥이 아니면 만들 수 없음
  targets = targets.filter(t => long ? t > hi : t < lo).sort((a, b) => long ? a - b : b - a);   // 이익 쪽에 있는 목표만, 가까운 것부터
  const risk = Math.abs(mid - stop), rr = targets.length && risk > 0 ? Math.abs(targets[0] - mid) / risk : null;
  let state = 'wait';
  if (price != null) state = price >= lo && price <= hi ? 'in' : (long ? (price > hi ? 'above' : (price < stop ? 'broken' : 'below')) : (price < lo ? 'above' : (price > stop ? 'broken' : 'below')));
  return {mode, side, zone: [lo, hi], stop, targets, rr, state};
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
// 이 시간봉에 쓸 스윙 크기(ATR 배수). 민감 모드는 30분·15분만 더 작게. null 이면 기본값(2·3·4.5).
function frcScalesFor(name, fine) { return fine && FRC_FINE_TFS.indexOf(name) >= 0 ? FRC_FINE_SCALES : null; }
// 한 시간봉의 지금 모드에 맞는 엘리엇 결과(없으면 null)
function frcElliott(name) {
  const d = FRC.data[name]; if (!d || !d.res) return null;
  return (frcScalesFor(name, FRC.fine) && d.resFine ? d.resFine : d.res).elliott;
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
    const arr = [tb.map(b => b.open), tb.map(b => b.high), tb.map(b => b.low), tb.map(b => b.close)];
    FRC.data[name] = {at: Date.now(), bars: tb, res, resFine: FRC_FINE_TFS.indexOf(name) >= 0 ? stAnalyze(arr[0], arr[1], arr[2], arr[3], FRC_FINE_SCALES) : null};
  } catch (err) { FRC.data[name] = {at: Date.now(), bars: [], res: null, error: true}; }
  FRC.busy[name] = false; FRC.rev++; FRC.key = ''; frcDraw();
}
function frcFmt(v) { return typeof fmt === 'function' ? fmt(v, 5) : Number(v).toFixed(5); }
function frcBoxEl() {
  if (FRC.box && FRC.box.isConnected) return FRC.box;
  if (typeof hubEnsure === 'function') hubEnsure();
  const wrap = document.getElementById('chartwrap') || document.getElementById('chart'); if (!wrap) return null;
  const box = document.createElement('div'); box.id = 'fractal-box';
  box.style.cssText = 'margin:6px 0;padding:8px 10px;border:1px solid #2a2e39;border-radius:8px;background:#131722;color:#d1d4dc;font:12px/1.6 sans-serif';
  const pane = document.getElementById('hub-pane-ell'); if (pane) pane.appendChild(box); else wrap.insertAdjacentElement('afterend', box);
  FRC.box = box; return box;
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
  const lf = document.createElement('label'), cf = document.createElement('input'); cf.type = 'checkbox'; cf.checked = FRC.fine;
  cf.onchange = () => { FRC.fine = cf.checked; frcStore(); FRC.key = ''; frcDraw(); frcBox(); };
  lf.appendChild(cf); lf.appendChild(document.createTextNode(' 단타 민감 모드(30분·15분)')); lf.style.cursor = 'pointer'; lf.title = '30분·15분에서 더 작은 스윙(평균 변동폭의 1.5배)도 파동으로 인정합니다. 후보가 자주 나오지만 의미가 약한 잡음 파동도 늘어납니다.'; head.appendChild(lf);
  const fold = mk('span', FRC.collapsed ? '▸ 펼치기' : '▾ 접기', 'margin-left:auto;cursor:pointer;opacity:.8');
  fold.onclick = () => { FRC.collapsed = !FRC.collapsed; frcStore(); frcBox(); }; head.appendChild(fold); box.appendChild(head);
  if (FRC.collapsed) return;
  const mainSec = frcMainSec(), price = bars.length ? bars[bars.length - 1].close : null;
  const tbl = mk('table', undefined, 'border-collapse:collapse;width:100%;margin-top:6px'), th = tbl.insertRow();
  for (const h of ['시간봉', '현재 위치', '관점 (롱/숏)', '진입 계획 (가설)', '무효화 가격 (현재가 대비)', '차트']) th.appendChild(mk('th', h, 'text-align:left;color:#8a8f98;font-weight:600;padding:3px 8px;white-space:nowrap'));
  let ref = null; const sides = [];
  for (const tf0 of FRC_TFS) { const name = tf0[0], e0 = frcElliott(name); if (FRC.on[name] && e0 && e0.status === 'ok') { ref = e0; break; } }
  for (const [name, iv, sec, color] of FRC_TFS) {
    const d = FRC.data[name], e = frcElliott(name), tr = tbl.insertRow(); tr.style.borderTop = '1px solid #2a2e39';
    const c0 = tr.insertCell(), c = document.createElement('input'); c.type = 'checkbox'; c.checked = FRC.on[name];
    c.onchange = () => { FRC.on[name] = c.checked; frcStore(); FRC.key = ''; frcDraw(); frcBox(); };
    c0.appendChild(c); const chip = mk('span', ' ■ ', 'color:' + color + ';font-weight:700'); c0.appendChild(chip); c0.appendChild(document.createTextNode(FRC_NAME[name]));
    const ph = !d ? {text: '불러오는 중…'} : d.error ? {text: '시세를 받지 못함'} : frcPhase(e);
    tr.insertCell().textContent = ph.text + (FRC.fine && frcScalesFor(name, true) ? ' · 민감' : '');
    const vc = tr.insertCell(), v = e && e.status === 'ok' ? frcSide(e, ref) : {side: 'wait', badge: '관망', why: !d ? '' : '후보 없음', counter: false};
    if (FRC.on[name]) sides.push({name, side: v.side});
    const bd = mk('span', v.badge, 'display:inline-block;min-width:34px;text-align:center;padding:0 7px;border-radius:5px;font-weight:700;color:#fff;margin-right:6px;background:' + (v.side === 'long' ? '#1e8e5a' : v.side === 'short' ? '#c62828' : '#4a5168'));
    vc.appendChild(bd); vc.appendChild(document.createTextNode(v.why + (v.counter ? ' · 큰 흐름과 반대(역추세 단타, 짧게)' : '')));
    const pc = tr.insertCell(), pl = e && e.status === 'ok' ? frcPlan(e, price) : null;
    const nm = pl && typeof cfNorm === 'function' ? cfNorm({side: pl.side, lo: pl.zone[0], hi: pl.zone[1], stop: pl.stop, targets: pl.targets}, price, typeof cfAtr === 'function' ? cfAtr(name) : null) : null;
    if (pl && nm && !nm.ok) {      // 가격 종합과 같은 공통 규칙을 통과하지 못한 가설은 이유와 함께 흐리게만 보여준다
      pc.appendChild(mk('div', '공통 규칙 미통과 — ' + nm.why, 'opacity:.6'));
      pc.appendChild(mk('div', '(원안) 진입 ' + frcFmt(pl.zone[0]) + ' ~ ' + frcFmt(pl.zone[1]) + ' · 손절 ' + frcFmt(pl.stop), 'opacity:.4;font-size:11px'));
    } else if (pl) {
      const q = nm && nm.ok ? {stop: nm.stop, targets: [nm.t1].concat(nm.t2 != null ? [nm.t2] : []), rr: nm.rr} : pl;
      const ML = {with: '4파 끝 진입', pullback: '눌림 진입(늦은 구간)', correction: '조정 진입(역방향)', watch: '조건부 관찰(4파 끝 확인 후)'}, STT = {in: '지금 구간 안', above: '구간 밖(' + (pl.side === 'long' ? '위' : '아래') + ') — 눌림·되돌림 대기', below: '구간 밖(' + (pl.side === 'long' ? '아래' : '위') + ')', broken: '손절 기준을 이미 벗어남', wait: ''};
      pc.appendChild(mk('div', ML[pl.mode] + ' · 진입 ' + frcFmt(pl.zone[0]) + ' ~ ' + frcFmt(pl.zone[1]) + (STT[pl.state] ? ' · ' + STT[pl.state] : ''), pl.state === 'in' ? 'font-weight:700' : ''));
      pc.appendChild(mk('div', '손절 ' + frcFmt(q.stop) + ' · 목표 ' + (q.targets.length ? q.targets.map(frcFmt).join(' / ') : '이미 넘음') + (q.rr != null ? ' · 손익비 ' + q.rr.toFixed(2) : ''), 'opacity:.85'));
    } else pc.textContent = '—';
    tr.insertCell().textContent = e && e.status === 'ok' ? frcFmt(e.invalidation.level) + (price ? ' (' + ((e.invalidation.level / price - 1) * 100).toFixed(1) + '%)' : '') + ' ' + (e.invalidation.side === 'above' ? '위로' : '아래로') + ' 벗어나면 무효' : '—';
    const drawn = FRC.drawn[name]; tr.insertCell().textContent = !FRC.on[name] ? '꺼짐' : sec < mainSec ? '차트보다 짧아 생략' : drawn ? '그림' : '—';
    for (const td of tr.cells) td.style.cssText = 'padding:3px 8px;vertical-align:top;text-align:left';
  }
  box.appendChild(tbl);
  const ov = frcOverall(sides), nmS = (v) => v === 'long' ? '롱' : v === 'short' ? '숏' : '관망', colS = (v) => v === 'long' ? '#1e8e5a' : v === 'short' ? '#c62828' : '#4a5168';
  const sum = mk('div', undefined, 'margin-top:6px;padding:6px 8px;border-radius:6px;background:#1a1f2e;display:flex;gap:10px;align-items:center;flex-wrap:wrap');
  sum.appendChild(mk('b', '종합 관점(가설)'));
  for (const [label, v] of [['큰 흐름 4시간·1시간', ov.big], ['단타 30분·15분', ov.small]]) {
    const c = mk('span', undefined, 'display:inline-flex;gap:5px;align-items:center'); c.appendChild(document.createTextNode(label));
    c.appendChild(mk('span', nmS(v.side), 'padding:0 8px;border-radius:5px;font-weight:700;color:#fff;background:' + colS(v.side))); sum.appendChild(c);
  }
  sum.appendChild(mk('span', ov.note, 'opacity:.9'));
  if (typeof cfVerdictSlot === 'function') box.insertBefore(mk('div', '이 표는 엘리엇만의 의견입니다. 지표·엘리엇을 한 규칙으로 합친 종합 판정은 분석 허브의 「종합」 탭 한 곳에서 보세요.', 'margin-top:6px;padding:4px 8px;border-radius:6px;background:#1a1f2e;opacity:.8;font-size:11px'), tbl);
  else box.insertBefore(sum, tbl);
  box.appendChild(mk('div', '차트의 시간봉(' + Math.round(mainSec / 60) + '분)보다 짧은 시간봉은 선이 겹쳐 그릴 수 없어 표에만 나옵니다. 15분 차트에서 보면 네 시간봉이 모두 겹쳐 그려집니다. 굵은 선일수록 큰 시간봉입니다. 「단타 민감 모드」는 30분·15분에서 더 작은 스윙(1.5 ATR)도 인정해 후보가 늘지만 잡음도 늘어납니다.', 'opacity:.65;font-size:11px;margin-top:4px'));
  box.appendChild(mk('div', '엘리엇 파동 세기는 주관적이고 앞으로의 가격을 보장하지 않습니다. 이 규칙의 신호는 과거 통계로 검증되지 않았습니다. "관점"은 각 시간봉의 파동 위치에서 기대되는 방향일 뿐 매매 신호가 아닙니다. "진입 계획"은 정석적인 되돌림 비율(23.6·38.2·50·61.8%)로 만든 가설 가격이며 체결을 보장하지 않습니다. 「진입 계획 (지표)」 탭(RSI·MACD·볼린저 기준)과는 계산이 다릅니다. 손절 기준은 무효화 가격이며, 단타는 작은 시간봉의 무효화 가격을 짧은 손절 기준으로 참고하세요.', 'opacity:.65;font-size:11px'));
}
function frcDraw() {
  try {
    frcStore();
    const L = bars.length; if (L < 2) return;
    for (const tf of FRC_TFS) if (FRC.on[tf[0]]) frcFetch(tf);
    const key = L + ':' + bars[0].time + ':' + bars[L - 1].time + ':' + JSON.stringify(FRC.on) + FRC.inval + FRC.fine + ':' + FRC.rev;
    if (key === FRC.key) return;
    FRC.key = key; frcClear(); FRC.drawn = {};
    const mainTimes = bars.map(b => b.time), mainSec = frcMainSec(), plan = [];
    for (const [name, , sec, color, width] of FRC_TFS) {
      const d = FRC.data[name], e = frcElliott(name); if (!FRC.on[name] || !d || !e) continue;
      const pts = frcSeriesData(d.bars, e, mainTimes, sec, mainSec); if (!pts) continue;
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
