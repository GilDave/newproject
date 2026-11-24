// ---- 차트 구조 자동 작도: 스윙(지그재그)·추세선·엘리엇 5파 후보. ena_bot/structure.py 와 같은 규칙(규칙을 바꾸면 둘 다 바꾼다). ----
// 이 결과는 작도 보조다: 파동 세기는 주관적이고 같은 차트에 여러 카운트가 가능하며, 앞으로의 가격을 보장하지 않는다.
const ST = {atrN: 14, minPct: 0.004, scales: [2.0, 3.0, 4.5], retrace2: [0.382, 0.786], retrace4: [0.236, 0.5], ext3: 1.5, w5Rel: [0.5, 1.2], alt: 0.15, minWaveAtr: 1.5, maxLineDist: 8.0};
function stAtr(h, l, c, n) {
  n = n || ST.atrN; const out = []; let prev = c[0];
  for (let i = 0; i < c.length; i++) {
    const tr = Math.max(h[i] - l[i], Math.abs(h[i] - prev), Math.abs(l[i] - prev));
    out.push(i === 0 ? tr : (out[out.length - 1] * (n - 1) + tr) / n); prev = c[i];
  }
  return out;
}
function stZigzag(h, l, c, atrMult, minPct, atrN) {
  atrMult = atrMult || 3.0; minPct = minPct === undefined ? ST.minPct : minPct;
  const n = c.length, a = stAtr(h, l, c, atrN), piv = [];
  let direction = 0, hi = h[0], hiI = 0, lo = l[0], loI = 0;
  for (let i = 1; i < n; i++) {
    const thr = Math.max(atrMult * a[i], minPct * c[i]);
    if (direction === 0) {
      if (h[i] > hi) { hi = h[i]; hiI = i; }
      if (l[i] < lo) { lo = l[i]; loI = i; }
      if (h[i] - lo >= thr && loI < i) { piv.push({i: loI, price: lo, kind: 'L', provisional: false}); direction = 1; hi = h[i]; hiI = i; }
      else if (hi - l[i] >= thr && hiI < i) { piv.push({i: hiI, price: hi, kind: 'H', provisional: false}); direction = -1; lo = l[i]; loI = i; }
    } else if (direction === 1) {
      if (h[i] > hi) { hi = h[i]; hiI = i; }
      else if (hi - l[i] >= thr) { piv.push({i: hiI, price: hi, kind: 'H', provisional: false}); direction = -1; lo = l[i]; loI = i; }
    } else {
      if (l[i] < lo) { lo = l[i]; loI = i; }
      else if (h[i] - lo >= thr) { piv.push({i: loI, price: lo, kind: 'L', provisional: false}); direction = 1; hi = h[i]; hiI = i; }
    }
  }
  if (direction === 1) piv.push({i: hiI, price: hi, kind: 'H', provisional: true});
  else if (direction === -1) piv.push({i: loI, price: lo, kind: 'L', provisional: true});
  return piv;
}
function stCmp(a, b) { for (let k = 0; k < a.length; k++) { if (a[k] < b[k]) return -1; if (a[k] > b[k]) return 1; } return 0; }
function stTrendlines(pivots, h, l, c, a, lookback, tolMult, minGap) {
  lookback = lookback || 8; tolMult = tolMult === undefined ? 0.5 : tolMult; minGap = minGap || 5;
  const n = c.length, out = {support: null, resistance: null};
  for (const [name, kind] of [['support', 'L'], ['resistance', 'H']]) {
    const pts = pivots.filter(p => p.kind === kind && !p.provisional).slice(-lookback);
    let best = null;
    for (let x = 0; x < pts.length; x++) for (let y = x + 1; y < pts.length; y++) {
      const p = pts[x], q = pts[y];
      if (q.i - p.i < minGap) continue;
      const slope = (q.price - p.price) / (q.i - p.i);
      let total = 0.0; for (let j = p.i; j <= q.i; j++) total += a[j];
      const tol = tolMult * (total / (q.i - p.i + 1));
      let ok = true;
      for (let j = p.i; j < n; j++) {
        const v = p.price + slope * (j - p.i);
        if ((kind === 'L' && c[j] < v - tol) || (kind === 'H' && c[j] > v + tol)) { ok = false; break; }
      }
      if (!ok) continue;
      const last = p.price + slope * (n - 1 - p.i);
      if (Math.abs(last - c[n - 1]) > ST.maxLineDist * a[n - 1]) continue;
      let touches = 0;
      for (const r of pts) if (r.i >= p.i && Math.abs(r.price - (p.price + slope * (r.i - p.i))) <= tol) touches++;
      const key = [touches, q.i, q.i - p.i];
      if (best === null || stCmp(key, best.key) > 0) best = {key, line: {a: {i: p.i, price: p.price}, b: {i: q.i, price: q.price}, slope, touches, last}};
    }
    out[name] = best ? best.line : null;
  }
  return out;
}
function stCheckUp(q) {
  const w1 = q[1] - q[0], w2 = q[1] - q[2], w3 = q[3] - q[2], w4 = q[3] - q[4];
  if (Math.min(w1, w2, w3, w4) <= 0) return null;
  const complete = q.length === 6;
  const rules = [['2파가 1파 시작점을 넘지 않음', q[2] > q[0]], ['3파가 1파 끝을 넘음', q[3] > q[1]], ['4파가 1파 영역과 겹치지 않음', q[4] > q[1]]];
  let w5 = 0.0;
  if (complete) { w5 = q[5] - q[4]; if (w5 <= 0) return null; rules.push(['3파가 가장 짧지 않음', !(w3 < w1 && w3 < w5)]); }
  else rules.push(['3파가 1파보다 길다', w3 >= w1]);
  if (!rules.every(r => r[1])) return null;
  const r2 = w2 / w1, r4 = w4 / w3;
  const guides = [['2파 되돌림 38~79%', ST.retrace2[0] <= r2 && r2 <= ST.retrace2[1]], ['3파가 1파의 1.5배 이상', w3 >= ST.ext3 * w1],
    ['4파 되돌림 24~50%', ST.retrace4[0] <= r4 && r4 <= ST.retrace4[1]], ['2파·4파 깊이가 다름', Math.abs(r2 - r4) >= ST.alt]];
  if (complete) guides.push(['5파가 1파의 50~120%', ST.w5Rel[0] <= w5 / w1 && w5 / w1 <= ST.w5Rel[1]]);
  return {w1, rules, guides, score: guides.filter(g => g[1]).length / guides.length, complete};
}
function stElliott(byScale, a, n) {
  let best = null, found = 0;
  for (const scale of Object.keys(byScale).map(Number)) {
    const pivots = byScale[scale];
    for (const offset of [0, 1, 2]) for (const length of [6, 5]) {
      const end = pivots.length - offset, start = end - length;
      if (start < 0) continue;
      const seq = pivots.slice(start, end);
      let alternates = true; for (let k = 0; k < seq.length - 1; k++) if (seq[k].kind === seq[k + 1].kind) alternates = false;
      if (!alternates) continue;
      const up = seq[0].kind === 'L', q = seq.map(p => up ? p.price : -p.price), res = stCheckUp(q);
      if (res === null || res.w1 < ST.minWaveAtr * a[seq[1].i]) continue;
      found++;
      const key = [-offset, res.score, res.complete ? 1 : 0, scale];
      if (best === null || stCmp(key, best.key) > 0) best = {key, scale, seq, up, q, res};
    }
  }
  if (best === null) return {status: 'none', reason: '필수 규칙을 통과하는 5파 구조가 없음', alternatives: 0};
  const {scale, seq, up, q, res} = best, sgn = up ? 1.0 : -1.0, w1 = res.w1;
  const forming5 = res.complete && seq[5].provisional, complete = res.complete && !forming5;   // 5번째 극점이 아직 확정 전이면 5파 진행 중
  let inval, targets;
  if (complete) { inval = {level: seq[5].price, side: up ? 'above' : 'below', why: '5파가 연장돼 현재 카운트가 틀림(새 ' + (up ? '고점' : '저점') + ')'}; targets = []; }
  else if (forming5) { inval = {level: seq[4].price, side: up ? 'below' : 'above', why: '5파 시작점(4파 끝)으로 되돌아가 5파 카운트가 무효'}; targets = [sgn * (q[4] + 0.618 * w1), sgn * (q[4] + 1.0 * w1)]; }
  else { inval = {level: seq[1].price, side: up ? 'below' : 'above', why: '4파가 1파 영역을 침범해 5파 카운트가 무효'}; targets = [sgn * (q[4] + 0.618 * w1), sgn * (q[4] + 1.0 * w1)]; }
  return {status: 'ok', direction: up ? 'up' : 'down', scale, complete, forming5, score: res.score, rules: res.rules.map(r => r[0]), hits: res.guides.filter(g => g[1]).map(g => g[0]),
    misses: res.guides.filter(g => !g[1]).map(g => g[0]), pivots: seq.map((p, k) => ({i: p.i, price: p.price, kind: p.kind, provisional: p.provisional, label: String(k)})),
    invalidation: inval, targets, alternatives: found - 1};
}
function stAnalyze(o, h, l, c, scales) {
  scales = scales || ST.scales; const n = c.length;
  if (n < 60) return {pivots: [], trendlines: {support: null, resistance: null}, elliott: {status: 'none', reason: '봉이 너무 적음', alternatives: 0}};
  const a = stAtr(h, l, c), byScale = {}; for (const s of scales) byScale[s] = stZigzag(h, l, c, s);
  const main = byScale[3.0] !== undefined ? byScale[3.0] : byScale[scales[Math.floor(scales.length / 2)]];
  return {pivots: main, trendlines: stTrendlines(main, h, l, c, a), elliott: stElliott(byScale, a, n)};
}

// ---------------------------------------------------------------- 화면: 차트에 선·표시를 그리고 요약 박스를 둔다(켜고 끄기는 이 브라우저에 기억)
const STR = {on: {tl: true, ell: true}, key: '', sup: null, res: null, ell: null, box: null, loaded: false, collapsed: true};   // 처음에는 접어서 한 줄만 보인다(가격 영역을 가리지 않게)
function structStore() {
  try {
    if (!STR.loaded) { STR.loaded = true; const v = JSON.parse(localStorage.getItem('ena_struct_v1') || '{}'); if (typeof v.tl === 'boolean') STR.on.tl = v.tl; if (typeof v.ell === 'boolean') STR.on.ell = v.ell; if (typeof v.collapsed === 'boolean') STR.collapsed = v.collapsed; }
    else localStorage.setItem('ena_struct_v1', JSON.stringify({tl: STR.on.tl, ell: STR.on.ell, collapsed: STR.collapsed}));
  } catch (e) { /* 기억하지 못해도 동작한다 */ }
}
function structClear() { for (const k of ['sup', 'res', 'ell']) { if (STR[k]) { try { chart.removeSeries(STR[k]); } catch (e) { /* 이미 없음 */ } STR[k] = null; } } }
const ST_CIRCLED = ['0', '①', '②', '③', '④', '⑤'];
function structFmt(v) { return typeof fmt === 'function' ? fmt(v, 5) : Number(v).toFixed(5); }
function structBoxEl() {
  if (STR.box && STR.box.isConnected) return STR.box;
  const host = document.getElementById('chart'); if (!host) return null;
  if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
  const box = document.createElement('div');
  box.style.cssText = 'position:absolute;left:8px;top:30px;z-index:6;max-width:380px;padding:6px 8px;border-radius:6px;background:rgba(19,23,34,.86);border:1px solid #2a2e39;color:#d1d4dc;font:12px/1.5 sans-serif;pointer-events:auto';
  host.appendChild(box); STR.box = box; return box;
}
function structSummary(res) {
  const s = res.trendlines.support, r = res.trendlines.resistance, e = res.elliott;
  const parts = [];
  if (STR.on.tl) parts.push('지지 ' + (s ? structFmt(s.last) : '없음') + ' · 저항 ' + (r ? structFmt(r.last) : '없음'));
  if (STR.on.ell) parts.push('엘리엇 ' + (e.status === 'ok' ? (e.direction === 'up' ? '상승' : '하락') + ' 5파 ' + (e.complete ? '완성' : e.forming5 ? '5파 진행 중' : '진행 중') : '후보 없음'));
  return parts.join(' · ');
}
function structBox(res) {
  const box = structBoxEl(); if (!box) return;
  box.textContent = '';
  const row = (txt, dim) => { const d = document.createElement('div'); d.textContent = txt; if (dim) d.style.cssText = 'opacity:.65;font-size:11px'; box.appendChild(d); return d; };
  const head = document.createElement('div'); head.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap';
  const title = document.createElement('b'); title.textContent = '자동 작도 (참고용)'; head.appendChild(title);
  for (const [k, label] of [['tl', '추세선'], ['ell', '엘리엇(가설)']]) {
    const lb = document.createElement('label'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = STR.on[k];
    cb.onchange = () => { STR.on[k] = cb.checked; structStore(); STR.key = ''; structDraw(); };
    lb.appendChild(cb); lb.appendChild(document.createTextNode(' ' + label)); lb.style.cursor = 'pointer'; head.appendChild(lb);
  }
  const fold = document.createElement('span'); fold.textContent = STR.collapsed ? '▸' : '▾'; fold.style.cssText = 'cursor:pointer;margin-left:auto';
  fold.onclick = () => { STR.collapsed = !STR.collapsed; structStore(); structBox(STR.last || null); }; head.appendChild(fold); box.appendChild(head);
  if (!res) return;
  if (STR.collapsed) { row(structSummary(res), true); return; }
  if (STR.on.tl) {
    const s = res.trendlines.support, r = res.trendlines.resistance;
    row('지지선 ' + (s ? structFmt(s.last) + ' (닿은 횟수 ' + s.touches + ')' : '없음') + ' · 저항선 ' + (r ? structFmt(r.last) + ' (닿은 횟수 ' + r.touches + ')' : '없음') + ' — 지금 위치의 값');
  }
  if (STR.on.ell) {
    const e = res.elliott;
    if (e.status !== 'ok') row('엘리엇 후보 없음: ' + e.reason);
    else {
      row('엘리엇 후보: ' + (e.direction === 'up' ? '상승' : '하락') + ' 5파 ' + (e.complete ? '완성(마지막 극점 확정)' : e.forming5 ? '진행 중(5파 극점을 갱신하는 중)' : '진행 중(4파까지)') + ' · 필수 규칙 ' + e.rules.length + '/' + e.rules.length + ' 통과 · 가이드라인 ' + e.hits.length + '/' + (e.hits.length + e.misses.length));
      row('무효화: ' + structFmt(e.invalidation.level) + ' ' + (e.invalidation.side === 'above' ? '위로' : '아래로') + ' 벗어나면 — ' + e.invalidation.why + (e.forming5 ? ' (새 극점이 나와도 이 선은 움직이지 않음)' : ''));
      if (e.targets.length) row('5파가 이어진다면 목표 구간 ' + structFmt(Math.min(...e.targets)) + ' ~ ' + structFmt(Math.max(...e.targets)) + ' (1파 길이의 0.618~1.0배)');
      if (e.alternatives > 0) row('규칙을 통과하는 다른 카운트도 ' + e.alternatives + '개 더 있습니다(이 후보가 유일한 해석이 아닙니다).', true);
    }
  }
  row('파동 세기는 주관적이며 앞으로의 가격을 보장하지 않습니다. 과거 통계로 검증되지 않았습니다.', true);
}
function structDraw() {
  try {
    structStore();
    const L = bars.length, key = L + ':' + (L ? bars[0].time + ':' + bars[L - 1].time : '') + ':' + STR.on.tl + STR.on.ell;
    if (key === STR.key) return;
    STR.key = key; structClear();
    const o = bars.map(b => b.open), h = bars.map(b => b.high), l = bars.map(b => b.low), c = bars.map(b => b.close);
    const res = stAnalyze(o, h, l, c); STR.last = res; structBox(res);
    if (L < 60) return;
    const bs = typeof barSec === 'function' ? barSec() : (bars[1].time - bars[0].time), ext = 40, tEnd = bars[L - 1].time + ext * bs;
    const line = (color, style) => chart.addLineSeries({color, lineWidth: 1, lineStyle: style, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false});
    if (STR.on.tl) {
      for (const [k, name, color] of [['support', 'sup', '#26c6da'], ['resistance', 'res', '#ff7043']]) {
        const t = res.trendlines[k]; if (!t) continue;
        const s = line(color, 2); STR[name] = s;
        s.setData([{time: bars[t.a.i].time, value: t.a.price}, {time: tEnd, value: t.a.price + t.slope * (L - 1 + ext - t.a.i)}]);
      }
    }
    const e = res.elliott;
    if (STR.on.ell && e.status === 'ok') {
      const s = line('#e0e0e0', 0); STR.ell = s;
      s.setData(e.pivots.map(p => ({time: bars[p.i].time, value: p.price})));
      s.setMarkers(e.pivots.map(p => ({time: bars[p.i].time, position: p.kind === 'H' ? 'aboveBar' : 'belowBar', color: '#e0e0e0', shape: 'circle', text: ST_CIRCLED[Number(p.label)] + (p.provisional ? '?' : '')})));
      s.createPriceLine({price: e.invalidation.level, color: '#ff5252', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '무효화'});
      if (e.targets.length) {
        s.createPriceLine({price: Math.min(...e.targets), color: '#9ccc65', lineWidth: 1, lineStyle: 3, axisLabelVisible: true, title: '5파 목표↓'});
        s.createPriceLine({price: Math.max(...e.targets), color: '#9ccc65', lineWidth: 1, lineStyle: 3, axisLabelVisible: true, title: '5파 목표↑'});
      }
    }
  } catch (err) { /* 작도 실패가 차트를 깨뜨리지 않게 한다 */ }
}
// ---- /차트 구조 자동 작도 ----
