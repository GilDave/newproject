// ---- 가격 종합: 진입 계획 카드(지표) · 거래량 매물대 · 프랙탈 엘리엇(파동)이 내놓는 진입·손절·목표 가격을 같은 규칙·같은 현재가로 한 표에 모은다. ----
// 세 방법은 보는 것이 달라 가격이 같을 수 없다(지표 구간 · 거래량이 쌓인 가격 · 파동의 되돌림 비율). 대신 ① 같은 현재가 ② 같은 현실성 규칙 ③ 같은 모양으로 비교하고
// ④ 가격이 겹치는 곳(합의 구간)을 따로 보여준다. 매매 신호가 아니며 과거 통계로 검증되지 않았다.
const CF_TFS = ['15m', '30m', '1h', '4h', '1d'];
const CF_KO = {'15m': '15분', '30m': '30분', '1h': '1시간', '4h': '4시간', '1d': '1일'};
const CF_RULE = {reachAtr: 3.0, minStopAtr: 0.8, maxStopAtr: 3.5, maxStopPct: 0.10, tpAtr: 4.5, minRR: 1.2, reachPct: 0.08, clusterPct: 0.006};
const CF_MIN = {'15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440};
const CF = {aiDetail: false, aiFit: false, aiFitSeries: null, aiOn: true, aiLines: [], aiSeries: [], ai: null, aiLearn: null, aiAt: 0, aiBusy: false, levels: null, levelsAt: 0, busy: false, collapsed: false, lineOn: false, loaded: false, box: null, lines: [], key: '', last: null};
function cfStore() {
  try {
    if (!CF.loaded) { CF.loaded = true; const v = JSON.parse(localStorage.getItem('ena_confluence_v1') || '{}'); if (typeof v.collapsed === 'boolean') CF.collapsed = v.collapsed; if (typeof v.lineOn === 'boolean') CF.lineOn = v.lineOn; if (typeof v.aiOn === 'boolean') CF.aiOn = v.aiOn; if (typeof v.aiFit === 'boolean') CF.aiFit = v.aiFit; if (typeof v.aiDetail === 'boolean') CF.aiDetail = v.aiDetail; }
    else localStorage.setItem('ena_confluence_v1', JSON.stringify({collapsed: CF.collapsed, lineOn: CF.lineOn, aiOn: CF.aiOn, aiFit: CF.aiFit, aiDetail: CF.aiDetail}));
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
// ---- 종합 판정: 지표(진입 계획)와 엘리엇의 방향 의견을 '한 곳, 한 규칙'으로 합친다. ----
// 같은 시간봉에서 두 방법이 일치하면 2점, 한쪽만 의견이 있으면 1점, 충돌하면 0점에 시간봉 무게(1일 5 · 4시간 4 · 1시간 3 · 30분 2 · 15분 1)를 곱해 합친다.
// 큰 흐름(1일·4시간·1시간)과 단타(30분·15분)를 따로 합산하고, 합이 ±3 미만이면 관망이다. 거래량 매물대는 가격 근거로만 쓰고 방향 투표에는 넣지 않으며 AI 예측은 검증상 우위가 없어 넣지 않는다.
const CF_W = {'1d': 5, '4h': 4, '1h': 3, '30m': 2, '15m': 1}, CF_BIG = ['1d', '4h', '1h'], CF_SMALL = ['30m', '15m'], CF_TH = 3;
function cfSideKo(v) { return v === 'long' ? '롱' : v === 'short' ? '숏' : '관망'; }
function cfTfState(ind, ell) {
  if (ind && ell) return ind === ell ? {state: 'agree', side: ind, unit: 2} : {state: 'conflict', side: null, unit: 0};
  if (ind || ell) return {state: 'single', side: ind || ell, unit: 1};
  return {state: 'none', side: null, unit: 0};
}
function cfGroup(rows, tfs) {
  let sum = 0, n = 0;
  for (const r of rows) if (tfs.indexOf(r.tf) >= 0 && r.side) { sum += (r.side === 'long' ? 1 : -1) * r.unit * (CF_W[r.tf] || 1); n++; }
  return {sum, n, side: Math.abs(sum) >= CF_TH ? (sum > 0 ? 'long' : 'short') : 'wait'};
}
function cfVerdict(items) {
  const rows = items.map(it => Object.assign({tf: it.tf, ind: it.ind || null, ell: it.ell || null}, cfTfState(it.ind || null, it.ell || null)));
  const big = cfGroup(rows, CF_BIG), small = cfGroup(rows, CF_SMALL), conflicts = rows.filter(r => r.state === 'conflict');
  let text;
  if (big.side === 'wait' && small.side === 'wait') text = '뚜렷한 방향이 없습니다 → 관망';
  else if (big.side === 'wait') text = '큰 흐름은 불분명하고 단타만 ' + cfSideKo(small.side) + ' → 짧게, 작은 크기로만 참고';
  else if (small.side === 'wait') text = '큰 흐름 ' + cfSideKo(big.side) + ', 단타는 대기 → 단기 파동이 정리되길 기다림';
  else if (small.side === big.side) text = cfSideKo(big.side) + ' 우세 — 큰 흐름과 단타가 같은 방향(눌림·되돌림 뒤 ' + cfSideKo(big.side) + ' 재개를 관찰)';
  else text = '큰 흐름 ' + cfSideKo(big.side) + ', 단타 ' + cfSideKo(small.side) + '(역추세) → 단타 ' + cfSideKo(small.side) + '은 짧게, 그 움직임이 끝나는 자리에서 ' + cfSideKo(big.side) + ' 재개를 관찰';
  const head = big.side !== 'wait' ? big.side : small.side;
  return {rows, big, small, conflicts, text, head};
}
function cfVerdictFill(el, v) {
  el.textContent = '';
  const mk = (tag, text, css) => { const d = document.createElement(tag); if (text !== undefined) d.textContent = text; if (css) d.style.cssText = css; return d; };
  const col = (sd) => sd === 'long' ? '#1e8e5a' : sd === 'short' ? '#c62828' : '#4a5168';
  const badge = (sd, label, big) => mk('span', label !== undefined ? label : cfSideKo(sd), 'display:inline-block;padding:' + (big ? '1px 12px' : '0 7px') + ';border-radius:6px;font-weight:800;color:#fff;margin:0 5px;background:' + col(sd) + (big ? ';font-size:14px' : ''));
  const l1 = mk('div', undefined, 'display:flex;gap:6px;align-items:center;flex-wrap:wrap');
  l1.appendChild(mk('b', '종합 판정 (지표 + 엘리엇, 한 규칙)')); l1.appendChild(badge(v.head, v.head === 'wait' ? '관망' : cfSideKo(v.head) + ' 우세', true));
  l1.appendChild(document.createTextNode('큰 흐름 1일·4시간·1시간')); l1.appendChild(badge(v.big.side)); l1.appendChild(document.createTextNode('단타 30분·15분')); l1.appendChild(badge(v.small.side));
  el.appendChild(l1);
  el.appendChild(mk('div', v.text, 'margin-top:3px;font-weight:600'));
  const tbl = mk('table', undefined, 'border-collapse:collapse;margin-top:5px;font-size:12px'), th = tbl.insertRow();
  for (const h of ['시간봉', '지표(진입 계획)', '엘리엇', '판정']) th.appendChild(mk('th', h, 'text-align:left;color:#8a8f98;font-weight:600;padding:1px 14px 1px 0'));
  const KO = {'1d': '1일', '4h': '4시간', '1h': '1시간', '30m': '30분', '15m': '15분'}, ST = {agree: '일치', single: '한쪽만', conflict: '충돌', none: '의견 없음'};
  for (const tf of ['1d', '4h', '1h', '30m', '15m']) {
    const r = v.rows.find(x => x.tf === tf); if (!r) continue;
    const tr = tbl.insertRow(); tr.insertCell().textContent = KO[tf];
    for (const sd of [r.ind, r.ell]) { const td = tr.insertCell(); td.appendChild(sd ? badge(sd) : mk('span', '—', 'opacity:.5')); td.firstChild.style.margin = '0'; }
    const jd = tr.insertCell(); jd.textContent = ST[r.state] + (r.side ? ' · ' + cfSideKo(r.side) : ''); jd.style.color = r.state === 'conflict' ? '#ffb74d' : r.state === 'none' ? '#8a8f98' : '';
    for (const td of tr.cells) td.style.padding = '1px 14px 1px 0';
  }
  el.appendChild(tbl);
  if (v.conflicts.length) el.appendChild(mk('div', '충돌: ' + v.conflicts.map(r => KO[r.tf] + ' 지표 ' + cfSideKo(r.ind) + ' ↔ 엘리엇 ' + cfSideKo(r.ell)).join(' · ') + ' — 같은 시간봉에서 반대면 방향 근거가 부족한 것이라 그 시간봉은 0점으로 계산합니다.', 'margin-top:3px;color:#ffb74d'));
  el.appendChild(mk('div', '시간봉이 다른 의견은 충돌이 아닙니다(예: 15분 지표 롱 = 단기 반등, 4시간 엘리엇 숏 = 큰 하락 흐름). 규칙: 같은 시간봉에서 일치 2점·한쪽만 1점·충돌 0점 × 시간봉 무게(1일 5·4시간 4·1시간 3·30분 2·15분 1), 큰 흐름·단타를 따로 합해 ±3 미만이면 관망. 거래량은 가격 근거로만 쓰고 AI 예측은 검증상 우위가 없어 넣지 않았습니다. 매매 신호가 아니며 과거 통계로 검증되지 않았습니다.', 'margin-top:4px;opacity:.6;font-size:11px'));
}
function cfVerdictSlot() {
  if (document.getElementById('analysis-hub')) {      // 분석 허브가 있으면 종합 판정은 허브의 「종합」 탭 한 곳에서만 본다
    const n = document.createElement('div'); n.id = 'unified-verdict-note'; n.style.cssText = 'margin:4px 0;padding:6px 10px;border:1px dashed #2a2e39;border-radius:8px;font-size:12px;opacity:.85;cursor:pointer';
    n.textContent = '※ 이 탭은 지표(RSI·MACD·볼린저)만의 의견입니다. 종합 판정·체크리스트·가격 비교는 「종합」 탭에서 한 곳으로 봅니다. (눌러서 이동)';
    n.onclick = () => { if (typeof hubSetTab === 'function') hubSetTab('sum'); }; return n;
  }
  const d = document.createElement('div'); d.id = 'unified-verdict';
  d.style.cssText = 'margin:6px 0;padding:8px 10px;border:1px solid #2a2e39;border-radius:8px;background:#161b28;color:#d1d4dc;font:12px/1.6 sans-serif';
  if (CF.last && CF.last.verdict) cfVerdictFill(d, CF.last.verdict); else d.textContent = '종합 판정을 계산하는 중…';
  return d;
}
function cfVerdictRender() { const d = document.getElementById('unified-verdict'); if (d && CF.last && CF.last.verdict) cfVerdictFill(d, CF.last.verdict); }
// 지표(진입 계획 카드)가 이 시간봉에 대해 가진 방향 의견: 가장 좋은 계획의 방향(조건이 너무 적은 'weak' 계획은 의견 없음)
function cfEntrySide(tf) {
  const p = typeof ENT !== 'undefined' && ENT && ENT.data && ENT.data.plans && ENT.data.plans.timeframes ? ENT.data.plans.timeframes[tf] : null;
  if (!p || p.status !== 'ok' || !p.best || p.best.state === 'weak') return null;
  return p.best.direction === 'long' || p.best.direction === 'short' ? p.best.direction : null;
}
// 엘리엇의 방향 의견: 4파 끝·5파 진행은 파동 방향, 5파 완성은 반대, 4파 조정 중·후보 없음은 의견 없음. 꺼 둔 시간봉은 의견 없음.
function cfEllSide(tf, ref) {
  if (typeof frcElliott !== 'function' || typeof FRC === 'undefined' || FRC.on[tf] === undefined || !FRC.on[tf]) return null;
  const e = frcElliott(tf); if (!e || e.status !== 'ok') return null;
  const sd = frcSide(e, ref).side; return sd === 'long' || sd === 'short' ? sd : null;
}
function cfEllRef() {
  if (typeof FRC_TFS === 'undefined' || typeof frcElliott !== 'function') return null;
  for (const t of FRC_TFS) { if (FRC.on[t[0]]) { const e = frcElliott(t[0]); if (e && e.status === 'ok') return e; } }
  return null;
}
// ---- /종합 판정 ----
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
// AI 예측(/api/forecast 의 horizons)에서 이 시간봉과 같은 길이(분)의 예측을 꺼낸다. 없으면 null.
function cfAiFor(fc, tf) {
  const hs = fc && fc.forecast && Array.isArray(fc.forecast.horizons) ? fc.forecast.horizons : null; if (!hs) return null;
  const h = hs.find(x => x && x.minutes === CF_MIN[tf]); if (!h) return null;
  if (h.status && h.status !== 'ok') return {why: h.reason || '예측 불가'};
  const side = h.direction === 'up' ? 'long' : h.direction === 'down' ? 'short' : null;
  return {side, label: h.label_ko || (side === 'long' ? '상승' : side === 'short' ? '하락' : '횡보'), ret: h.expected_return, expected: h.expected, score: h.score};
}
// 학습 예측의 과거 검증 요약(/api/learn-forecast): 적중률 · 기준선 대비 오차 개선 · 수수료 포함 모의 매매 누적 수익률. 우위가 없으면 경고 문구를 붙인다.
function cfAiVerdict(lf) {
  const s0 = lf && Array.isArray(lf.series) ? lf.series.find(x => x && x.status === 'ok' && x.current) : null; if (!s0) return null;
  const c = s0.current, t = s0.trading || {};
  const improve = c.base_mae > 0 ? (c.base_mae - c.mae) / c.base_mae * 100 : null;
  const bad = !(c.hit > 0.52) || !(improve > 1) || (t.cum_return != null && t.cum_return < 0);
  let text = '과거 검증(' + (s0.horizon_label || '') + '): 방향 적중 ' + (c.hit * 100).toFixed(1) + '%' + (improve != null ? ' · 기준선 대비 오차 개선 ' + improve.toFixed(2) + '%' : '') + (t.cum_return != null ? ' · 수수료 포함 모의 매매 누적 ' + (t.cum_return * 100).toFixed(1) + '%' : '');
  text += bad ? ' → 우위가 확인되지 않았습니다. 방향 일치의 근거로 쓰지 마세요.' : ' → 기준선보다 낫지만 표본 밖 검증을 계속 확인하세요.';
  return {text, bad};
}
// 차트에 점선으로 그릴 AI 예측. 차트 봉 하나보다 짧은 예측(예: 15분 차트의 1분 예측)은 한 봉 안이라 빼고, 이미 고른 가격과 0.1% 이내로 겹치는 예측은 빼서(보통 가까운 시간대 예측은 거의 같은 가격)
// 서로 다른 가격을 가진 예측만 그린다(5개를 넘으면 가까운 2개 + 가장 먼 3개). k = 마지막 봉에서 몇 봉 뒤인지(예측 길이 ÷ 봉 길이). k 가 미래 여백(futureBars)보다 크면 궤적 선은 생략하고 가격선만 그린다.
function cfAiLines(fc, mainSec, futureBars) {
  const hs = fc && fc.forecast && Array.isArray(fc.forecast.horizons) ? fc.forecast.horizons : []; const all = [];
  for (const h of hs) {
    if (!h || (h.status && h.status !== 'ok') || !(h.expected > 0) || !(h.minutes > 0) || h.minutes * 60 < mainSec * 0.99) continue;
    const side = h.direction === 'up' ? 'long' : h.direction === 'down' ? 'short' : null, k = Math.max(1, Math.round(h.minutes * 60 / mainSec));
    all.push({minutes: h.minutes, label: h.label || h.minutes + '분', side, price: h.expected, base: h.base_price > 0 ? h.base_price : null, k, trail: k <= futureBars - 1, kEnd: Math.max(1, Math.min(k, futureBars - 1)),
      baseTs: h.base_ts > 0 ? h.base_ts / 1000 : null, score: typeof h.score === 'number' ? h.score : null, sigma: typeof h.sigma_h === 'number' ? h.sigma_h : null});
  }
  all.sort((a, b) => a.minutes - b.minutes);
  let out = [];
  for (const x of all) if (out.every(o => Math.abs(x.price / o.price - 1) > 0.001)) out.push(x);
  if (out.length > 5) out = out.slice(0, 2).concat(out.slice(-3));   // 5개를 넘으면 가까운 2개 + 가장 먼 3개(1주·1달처럼 멀리 보는 예측이 빠지지 않게)
  return out;
}
// 그린 AI 예측 가격들의 최저·최고. 가격 축을 이 범위까지 넓힐 때 쓴다(없으면 null).
function cfAiRange(lines, price) {
  if (!lines || !lines.length) return null;
  let lo = price > 0 ? price : Infinity, hi = price > 0 ? price : -Infinity;
  for (const x of lines) { if (x.price < lo) lo = x.price; if (x.price > hi) hi = x.price; }
  return lo < hi ? {lo, hi} : null;
}
// 차트 봉 시각(오름차순) 중 t(초) 이하의 가장 큰 값. t 가 첫 봉보다 앞이면 첫 봉.
function cfSnapBar(t) {
  if (typeof bars === 'undefined' || !bars.length) return null;
  let lo = 0, hi = bars.length - 1, ans = 0;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (bars[mid].time <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
  return bars[ans].time;
}
function cfAiClear() {
  if (CF.aiFitSeries) { try { chart.removeSeries(CF.aiFitSeries); } catch (e) { /* 이미 없음 */ } CF.aiFitSeries = null; }
  for (const l of CF.aiLines) { try { candle.removePriceLine(l); } catch (e) { /* 이미 없음 */ } } CF.aiLines = [];
  for (const sr of CF.aiSeries) { try { chart.removeSeries(sr); } catch (e) { /* 이미 없음 */ } } CF.aiSeries = [];
}
function cfAiDraw() {
  cfAiClear();
  if (!CF.aiOn || !CF.ai || typeof bars === 'undefined' || !bars.length) return;
  const mainSec = typeof barSec === 'function' ? barSec() : (bars.length > 1 ? bars[1].time - bars[0].time : 0); if (!mainSec) return;
  const fut = typeof FUTURE !== 'undefined' ? FUTURE : 0, last = bars[bars.length - 1], drawn = cfAiLines(CF.ai, mainSec, fut), rg = cfAiRange(drawn, last.close);
  if (CF.aiFit && rg) {   // 보이지 않는 보조 선 하나가 가격 축의 자동 범위에 예측 가격들을 포함시킨다
    const fit = chart.addLineSeries({color: 'rgba(0,0,0,0)', lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
      autoscaleInfoProvider: (orig) => { const r = orig(); const pad = (rg.hi - rg.lo) * 0.04; const lo = rg.lo - pad, hi = rg.hi + pad; return r && r.priceRange ? {priceRange: {minValue: Math.min(r.priceRange.minValue, lo), maxValue: Math.max(r.priceRange.maxValue, hi)}, margins: r.margins} : {priceRange: {minValue: lo, maxValue: hi}}; }});
    fit.setData([{time: last.time, value: last.close}]); CF.aiFitSeries = fit;
  }
  for (const x of drawn) {
    const color = x.side === 'long' ? '#66bb6a' : x.side === 'short' ? '#ef5350' : '#9aa3b5', mark = x.side === 'long' ? '▲' : x.side === 'short' ? '▼' : '−';
    CF.aiLines.push(candle.createPriceLine({price: x.price, color, lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: 'AI ' + x.label + ' ' + mark}));
    // 마지막 봉에서 예측 가격까지 이어지는 점선. 예측 시점이 화면 오른쪽 여백보다 멀면(x.trail === false) 오른쪽 끝까지만 그리고 끝에 '→' 를 붙인다(시간 축척이 아님).
    if (fut >= 2 && typeof timeAt === 'function') {
      const t1 = timeAt(bars.length - 1 + x.kEnd); if (!(t1 > last.time)) continue;
      // 출발점 = 예측이 만들어진 '기준가·기준 시각'(마지막 확정 캔들의 종가). 기준 정보가 없으면 마지막 봉.
      const hasBase = x.base > 0 && x.baseTs > 0, t0 = hasBase ? cfSnapBar(x.baseTs) : last.time, v0 = hasBase ? x.base : last.close;
      if (t0 == null || !(t1 > t0)) continue;
      const sr = chart.addLineSeries({color, lineWidth: 2, lineStyle: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false});
      sr.setData([{time: t0, value: v0}, {time: t1, value: x.price}]);
      const pct = hasBase ? (x.price / x.base - 1) * 100 : null;
      // 라벨은 짧게(겹침 방지). 점수·기준가는 「AI 점선 라벨 자세히」를 켤 때만 붙인다.
      const txt = 'AI ' + x.label + ' ' + mark + (pct != null ? ' ' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%' : '') + (CF.aiDetail ? (x.score != null ? ' · 점수 ' + x.score.toFixed(2) : '') + (hasBase ? ' · 기준 ' + cfFmt(x.base) : '') : '') + (x.trail ? '' : ' →');
      sr.setMarkers([{time: t0, position: 'inBar', color, shape: 'circle', text: ''}, {time: t1, position: x.side === 'long' ? 'aboveBar' : x.side === 'short' ? 'belowBar' : 'inBar', color, shape: x.side === 'long' ? 'arrowUp' : x.side === 'short' ? 'arrowDown' : 'circle', text: txt}]);
      CF.aiSeries.push(sr);
    }
  }
}
async function cfLoadAi() {
  if (CF.aiBusy || Date.now() - CF.aiAt < 60000) return;
  CF.aiBusy = true;
  try { const r = await fetch('/api/forecast'); const j = await r.json(); if (r.ok && !j.error) CF.ai = j; } catch (e) { /* AI 열 없이도 나머지는 보인다 */ }
  try { const r = await fetch('/api/learn-forecast'); const j = await r.json(); if (r.ok && !j.error) CF.aiLearn = j; } catch (e) { /* 검증 요약 없이도 동작 */ }
  CF.aiAt = Date.now(); CF.aiBusy = false; CF.key = ''; cfDraw();
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
  if (typeof hubEnsure === 'function') hubEnsure();
  const anchor = document.getElementById('fractal-box') || document.getElementById('chartwrap'); if (!anchor) return null;
  const box = document.createElement('div'); box.id = 'confluence-box';
  box.style.cssText = 'margin:6px 0;padding:8px 10px;border:1px solid #2a2e39;border-radius:8px;background:#131722;color:#d1d4dc;font:12px/1.6 sans-serif';
  const pane = document.getElementById('hub-pane-prc'); if (pane) pane.appendChild(box); else anchor.insertAdjacentElement('afterend', box);
  CF.box = box; return box;
}
function cfClearLines() { for (const l of CF.lines) { try { candle.removePriceLine(l); } catch (e) { /* 이미 없음 */ } } CF.lines = []; }
function cfBuild(price) {
  const rows = [], all = [], ref = cfEllRef();
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
    row.ai = cfAiFor(CF.ai, tf);
    row.op = {tf, ind: cfEntrySide(tf), ell: cfEllSide(tf, ref)};
    row.agree = !sides.length ? null : sides.every(s => s === sides[0]) ? {side: sides[0], n: sides.length} : {mixed: true, long: sides.filter(s => s === 'long').length, short: sides.filter(s => s === 'short').length};
    rows.push(row);
  }
  return {rows, clusters: cfClusters(all, price), verdict: cfVerdict(rows.map(r => r.op))};
}
function cfDraw() {
  try {
    cfStore(); const price = cfPrice(); if (!price) return;
    cfLoadLevels(); cfLoadAi();
    const res = cfBuild(price); CF.last = res; cfVerdictRender();
    const key = JSON.stringify([price.toFixed(5), res.rows.map(r => [r.op.ind, r.op.ell]), CF.collapsed, CF.lineOn, CF.aiOn, CF.aiFit, CF.aiDetail, (CF.ai && CF.ai.forecast && CF.ai.forecast.horizons || []).map(h => h && [h.minutes, h.expected]), bars.length, res.rows.map(r => r.ai ? [r.ai.side, r.ai.score] : null), !!CF.aiLearn, res.rows.map(r => Object.keys(r.cells).map(k => r.cells[k].ok ? r.cells[k].mid.toFixed(5) : r.cells[k].why))]);
    if (key === CF.key) return; CF.key = key;
    cfBox(res, price);
    if (typeof hubRender === 'function') hubRender(res, price);
    cfClearLines(); cfAiDraw();
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
  head.appendChild(mk('b', '가격 종합 — 지표 · 거래량 · 엘리엇의 진입 가격을 같은 규칙으로 비교, AI 예측과 함께 보기 (참고용 가설)'));
  const lb = document.createElement('label'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = CF.lineOn;
  cb.onchange = () => { CF.lineOn = cb.checked; cfStore(); CF.key = ''; cfDraw(); };
  lb.appendChild(cb); lb.appendChild(document.createTextNode(' 합의 구간을 차트에 선으로')); lb.style.cursor = 'pointer'; head.appendChild(lb);
  const la = document.createElement('label'), ca = document.createElement('input'); ca.type = 'checkbox'; ca.checked = CF.aiOn;
  ca.onchange = () => { CF.aiOn = ca.checked; cfStore(); CF.key = ''; cfDraw(); };
  la.appendChild(ca); la.appendChild(document.createTextNode(' AI 예측 점선')); la.style.cursor = 'pointer'; la.title = '/api/forecast 의 예측 가격을 차트에 점선(가격선 + 앞으로의 짧은 궤적)으로 그립니다. 과거 검증에서 우위가 확인되지 않은 규칙 점수입니다.'; head.appendChild(la);
  const lz = document.createElement('label'), cz = document.createElement('input'); cz.type = 'checkbox'; cz.checked = CF.aiFit;
  cz.onchange = () => { CF.aiFit = cz.checked; cfStore(); CF.key = ''; cfDraw(); };
  lz.appendChild(cz); lz.appendChild(document.createTextNode(' 예측선이 모두 보이게 가격 축 넓히기')); lz.style.cursor = 'pointer'; lz.title = '1주·1달 예측처럼 현재가에서 먼 예측선이 화면 밖에 있을 때, 가격 축을 넓혀서 모두 보이게 합니다. 캔들은 상대적으로 작아집니다.'; head.appendChild(lz);
  const ld = document.createElement('label'), cd = document.createElement('input'); cd.type = 'checkbox'; cd.checked = CF.aiDetail;
  cd.onchange = () => { CF.aiDetail = cd.checked; cfStore(); CF.key = ''; cfDraw(); };
  ld.appendChild(cd); ld.appendChild(document.createTextNode(' AI 점선 라벨 자세히(점수·기준가)')); ld.style.cursor = 'pointer'; ld.title = '끄면 차트 라벨이 짧아져 겹치지 않습니다. 점수와 기준가는 아래 표에서도 볼 수 있습니다.'; head.appendChild(ld);
  const fold = mk('span', CF.collapsed ? '▸ 펼치기' : '▾ 접기', 'margin-left:auto;cursor:pointer;opacity:.8'); fold.onclick = () => { CF.collapsed = !CF.collapsed; cfStore(); CF.key = ''; cfDraw(); }; head.appendChild(fold); box.appendChild(head);
  if (CF.collapsed) return;
  box.appendChild(mk('div', '공통 규칙 · 현재가 ' + cfFmt(price) + ' 하나로 거리를 계산 · 진입은 3 ATR(평균 변동폭의 3배) 이내 · 손절 0.8~3.5 ATR(최대 10%) · 목표는 4.5 ATR 이내(↧ 제한됨) · 손익비 1.2 미만은 "(낮음)" · 손절을 넓혔으면 ↔', 'opacity:.7;font-size:11px;margin-top:4px'));
  const vd = cfAiVerdict(CF.aiLearn);
  if (vd) box.appendChild(mk('div', 'AI 예측 열은 모멘텀·EMA 추세·평균회귀를 가중합한 규칙 점수입니다. ' + vd.text, 'margin-top:4px;padding:4px 8px;border-radius:6px;font-size:11px;background:' + (vd.bad ? '#2a1d12' : '#12251b') + ';color:' + (vd.bad ? '#ffb74d' : '#a5d6a7')));
  const tbl = mk('table', undefined, 'border-collapse:collapse;width:100%;margin-top:6px'), th = tbl.insertRow();
  for (const h of ['시간봉', '지표 (진입 계획 카드)', '거래량 (매물대)', '엘리엇 (프랙탈)', 'AI 예측 (규칙 점수)', '방향 일치']) th.appendChild(mk('th', h, 'text-align:left;color:#8a8f98;font-weight:600;padding:3px 8px;white-space:nowrap'));
  for (const r of res.rows) {
    const tr = tbl.insertRow(); tr.style.borderTop = '1px solid #2a2e39';
    cfCell(tr.insertCell(), null); tr.cells[0].textContent = CF_KO[r.tf] + (r.atr ? '' : ''); tr.cells[0].style.cssText = 'padding:3px 8px;vertical-align:top;white-space:nowrap;font-weight:600';
    cfCell(tr.insertCell(), r.cells.entry);
    const lc = tr.insertCell(); cfCell(lc, r.cells.levels); if (r.cells.levels2) { const hr = document.createElement('div'); hr.style.cssText = 'height:3px'; lc.appendChild(hr); const x = document.createElement('td'); cfCell(x, r.cells.levels2); while (x.firstChild) lc.appendChild(x.firstChild); }
    cfCell(tr.insertCell(), r.cells.fractal);
    const ai = tr.insertCell(); ai.style.cssText = 'padding:3px 8px;vertical-align:top;text-align:left';
    if (!r.ai) { ai.textContent = '—'; ai.style.opacity = '.55'; }
    else if (r.ai.why) { ai.textContent = r.ai.why; ai.style.opacity = '.55'; }
    else {
      const ab = document.createElement('span'); ab.textContent = r.ai.side === 'long' ? '▲ 상승' : r.ai.side === 'short' ? '▼ 하락' : '− 횡보';
      ab.style.cssText = 'display:inline-block;padding:0 6px;border-radius:5px;font-weight:700;color:#fff;margin-right:5px;background:' + (r.ai.side === 'long' ? '#1e8e5a' : r.ai.side === 'short' ? '#c62828' : '#4a5168') + ';opacity:.75';
      const dv = document.createElement('div'); dv.appendChild(ab);
      dv.appendChild(document.createTextNode((r.ai.expected != null ? '예상 ' + cfFmt(r.ai.expected) : '') + (r.ai.ret != null ? ' (' + (r.ai.ret >= 0 ? '+' : '') + (r.ai.ret * 100).toFixed(2) + '%)' : '')));
      ai.appendChild(dv);
      if (r.ai.score != null) { const sc = document.createElement('div'); sc.textContent = '점수 ' + r.ai.score.toFixed(2) + ' (모멘텀·EMA·평균회귀의 가중합)'; sc.style.cssText = 'opacity:.55;font-size:11px'; ai.appendChild(sc); }
    }
    const ac = tr.insertCell(); ac.style.cssText = 'padding:3px 8px;vertical-align:top;white-space:nowrap';
    const aiNote = (a) => { if (!r.ai || !r.ai.side || !r.agree || r.agree.mixed) return ''; return r.ai.side === r.agree.side ? ' · AI도 같음' : ' · AI는 반대'; };
    if (!r.agree) ac.textContent = '—'; else if (r.agree.mixed) { ac.textContent = '엇갈림 (롱 ' + r.agree.long + ' · 숏 ' + r.agree.short + ')'; ac.style.color = '#ffb74d'; } else { ac.textContent = (r.agree.side === 'long' ? '롱' : '숏') + ' 일치 ' + r.agree.n + '개' + aiNote(); ac.style.color = r.agree.side === 'long' ? '#66bb6a' : '#ef5350'; }
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
