// ---- 진입 계획: 카드 + 차트 가격선. ena_bot/entry_plan.py 의 계산을 /api/entry-plan 에서 받아 그린다. 수동 매매 보조이며 자동 매매와 연결되지 않는다. ----
const ENT = {data: null, lines: [], key: '', lineOn: true, loaded: false, started: false};
const ENT_TF = {'15': '15m', '60': '1h', '240': '4h', 'D': '1d'};
const ENT_TF_KO = {'15m': '15분', '1h': '1시간', '4h': '4시간', '1d': '1일'};
const ENT_STATE = {in_zone: '진입 구간 안', approaching: '다가오는 중', waiting: '대기(아직 멀음)', broken: '구간 이탈', poor_rr: '손익비 부족'};
const ENT_SETUP = {pullback: '추세 눌림목', reversion: '밴드 반전'};
function entFmt(v) { return v == null ? '-' : (typeof fmt === 'function' ? fmt(v, 5) : Number(v).toFixed(5)); }
function entEl(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function entStore() {
  try {
    if (!ENT.loaded) { ENT.loaded = true; const v = JSON.parse(localStorage.getItem('ena_entry_v1') || '{}'); if (typeof v.lines === 'boolean') ENT.lineOn = v.lines; }
    else localStorage.setItem('ena_entry_v1', JSON.stringify({lines: ENT.lineOn}));
  } catch (e) { /* 기억하지 못해도 동작한다 */ }
}
function entStyle() {
  if (document.getElementById('ent-style')) return;
  const s = document.createElement('style'); s.id = 'ent-style';
  s.textContent = '.entc{margin-bottom:10px;padding:10px 12px;font-size:12px}.entc .t{font-weight:600;color:var(--mute,#8a8f98);margin-bottom:6px}'
    + '.entc .fav{font-size:14px;font-weight:700;margin:4px 0}.entc .sub{color:var(--mute,#8a8f98);margin:2px 0 6px}.entc .badge{display:inline-block;padding:2px 8px;border-radius:10px;margin:2px 0;font-weight:600}'
    + '.entc .badge.ok{background:#1b5e2040;color:#66bb6a}.entc .badge.warn{background:#ff8f0030;color:#ffb74d}.entc .badge.bad{background:#b71c1c30;color:#ef5350}'
    + '.entc .wrap{overflow-x:auto}.entc table{border-collapse:collapse;width:100%;min-width:760px}.entc th,.entc td{padding:4px 8px;border-top:1px solid #8884;text-align:left;vertical-align:top;white-space:nowrap}'
    + '.entc th{color:var(--mute,#8a8f98);font-weight:600}.entc .long{color:#26a69a}.entc .short{color:#ef5350}.entc .dim{opacity:.65}.entc .chk{margin-top:2px}.entc details{margin-top:6px}';
  document.head.appendChild(s);
}
function entVerdict(tf, setup) {
  const st = ENT.data && ENT.data.study;
  if (!st || !st.ok) return {cls: 'warn', text: '과거 검증 전 — 터미널에서 python -m ena_bot.entry_study 를 실행하세요'};
  if (tf === '15m') return {cls: 'warn', text: '15분봉은 이력이 없어 검증하지 않았습니다'};
  const g = st.groups[tf + '|' + setup];
  if (!g || !g.verdict) return {cls: 'warn', text: '이 계획은 검증하지 않았습니다'};
  return g.verdict.use ? {cls: 'ok', text: '과거 검증 통과: ' + g.verdict.why} : {cls: 'bad', text: '과거 검증: ' + g.verdict.why};
}
function entDist(b) { return b.dist_pct == null ? '-' : (b.dist_pct >= 0 ? '+' : '') + (b.dist_pct * 100).toFixed(1) + '%'; }
function entPlanText(b) {
  return (b.direction === 'long' ? '롱' : '숏') + ' ' + ENT_SETUP[b.setup] + (b.aligned_trend ? '' : ' (추세와 반대)');
}
function entCard() {
  const box = document.getElementById('entry-card'); if (!box) return;
  entStyle(); box.className = 'panel entc'; box.textContent = '';
  const d = ENT.data;
  box.appendChild(entEl('div', 't', '진입 계획 (수동 매매 보조 · 자동 매매와 연결되지 않음)'));
  if (!d || !d.ok) { box.appendChild(entEl('div', 'sub', '불러오는 중이거나 시세를 받지 못했습니다')); return; }
  const tfs = d.plans.timeframes, fav = d.plans.favorable;
  const pr = ['1h', '4h', '15m', '1d'].map(k => tfs[k]).find(p => p && p.status === 'ok');
  if (pr) box.appendChild(entEl('div', 'sub', '현재가 ' + entFmt(pr.price) + ' (진입가까지의 거리는 현재가 기준: + 위, - 아래)'));
  if (fav && tfs[fav] && tfs[fav].best) {
    const b = tfs[fav].best, v = entVerdict(fav, b.setup);
    box.appendChild(entEl('div', 'fav', '지금 가장 유리한 시간봉: ' + ENT_TF_KO[fav] + ' — ' + entPlanText(b)));
    box.appendChild(entEl('div', null, '권장 진입 ' + entFmt(b.entry) + ' (구간 ' + entFmt(b.zone[0]) + ' ~ ' + entFmt(b.zone[1]) + ') · 손절 ' + entFmt(b.stop) + ' · 목표 ' + entFmt(b.targets[0]) + ' / ' + entFmt(b.targets[1])
      + ' · 진입까지 ' + entDist(b) + ' · 첫 목표 손익비 ' + b.rr.toFixed(2) + ' · 손절 폭 ' + (b.risk_pct * 100).toFixed(1) + '% · 조건 일치 ' + b.score + '/3' + (b.htf_aligned ? ' · 상위 시간봉 추세와 같은 방향' : '')));
    box.appendChild(entEl('div', 'sub', '왕복 비용은 위험의 ' + (b.cost_r * 100).toFixed(0) + '% · 비용을 빼고 본전이 되려면 첫 목표 기준 승률 ' + (b.be_win_rate * 100).toFixed(0) + '% 이상 필요'));
    for (const w of (b.warnings || [])) box.appendChild(entEl('div', 'badge warn', '⚠ ' + w));
    const bd = entEl('span', 'badge ' + v.cls, v.text); box.appendChild(bd);
  } else box.appendChild(entEl('div', 'fav', d.plans.note || '지금은 조건이 충분히 겹친 시간봉이 없습니다(대기)'));
  const wrap = entEl('div', 'wrap'), tb = entEl('table'), hd = entEl('tr');
  for (const h of ['시간봉', '계획', '상태', '진입까지', '권장 진입 (구간)', '손절', '목표 1 / 2', '손익비', '조건', '지표 현황']) hd.appendChild(entEl('th', null, h));
  tb.appendChild(hd);
  for (const tf of ['15m', '1h', '4h', '1d']) {
    const p = tfs[tf], tr = entEl('tr', tf === fav ? '' : 'dim'); tr.appendChild(entEl('td', null, ENT_TF_KO[tf] + (tf === fav ? ' ★' : '')));
    if (!p || p.status !== 'ok') { const td = entEl('td', null, p ? (p.reason || p.status) : '-'); td.colSpan = 9; tr.appendChild(td); tb.appendChild(tr); continue; }
    const b = p.best, s = p.snapshot;
    const snap = 'RSI ' + (s.rsi == null ? '-' : s.rsi.toFixed(0)) + ' · MACD ' + (s.macd_hist == null ? '-' : (s.macd_hist >= 0 ? '+' : '') + s.macd_hist.toPrecision(2)) + ' · %B ' + (s.pct_b == null ? '-' : s.pct_b.toFixed(2)) + ' · 추세 ' + (s.trend > 0 ? '상승' : '하락');
    if (!b) { tr.appendChild(entEl('td', null, '계획 없음')); const td = entEl('td', null, '조건 미충족'); td.colSpan = 7; tr.appendChild(td); tr.appendChild(entEl('td', null, snap)); tb.appendChild(tr); continue; }
    const pc = entEl('td', b.direction, entPlanText(b) + ((b.warnings || []).length ? ' ⚠' : '')); if ((b.warnings || []).length) pc.title = b.warnings.join('\n'); tr.appendChild(pc);
    tr.appendChild(entEl('td', null, ENT_STATE[b.state] || b.state));
    tr.appendChild(entEl('td', null, entDist(b)));
    tr.appendChild(entEl('td', null, entFmt(b.entry) + ' (' + entFmt(b.zone[0]) + '~' + entFmt(b.zone[1]) + ')'));
    tr.appendChild(entEl('td', null, entFmt(b.stop)));
    tr.appendChild(entEl('td', null, entFmt(b.targets[0]) + ' / ' + entFmt(b.targets[1])));
    tr.appendChild(entEl('td', null, b.rr.toFixed(2)));
    tr.appendChild(entEl('td', null, b.score + '/3 ' + b.checks.map(c => (c.ok ? '✓' : '✗')).join('')));
    tr.appendChild(entEl('td', null, snap));
    tb.appendChild(tr);
  }
  wrap.appendChild(tb); box.appendChild(wrap);
  const det = entEl('details'); det.appendChild(entEl('summary', null, '조건 상세와 검증'));
  for (const tf of ['15m', '1h', '4h', '1d']) {
    const p = tfs[tf]; if (!p || p.status !== 'ok' || !p.best) continue;
    const b = p.best, v = entVerdict(tf, b.setup);
    det.appendChild(entEl('div', 'sub', ENT_TF_KO[tf] + ' · ' + entPlanText(b) + ' — ' + b.notes.join(' · ')));
    for (const c of b.checks) det.appendChild(entEl('div', 'chk', (c.ok ? '✓ ' : '✗ ') + c.name + ' — ' + c.detail));
    det.appendChild(entEl('div', 'chk', '왕복 비용은 위험의 ' + (b.cost_r * 100).toFixed(0) + '% · 본전 승률 ' + (b.be_win_rate * 100).toFixed(0) + '% · 손절 폭 ' + (b.risk_pct * 100).toFixed(1) + '%'));
    for (const w of (b.warnings || [])) det.appendChild(entEl('div', 'chk', '⚠ ' + w));
    det.appendChild(entEl('div', 'badge ' + v.cls, v.text));
  }
  box.appendChild(det);
  const lab = entEl('label'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = ENT.lineOn;
  cb.onchange = () => { ENT.lineOn = cb.checked; entStore(); ENT.key = ''; entDraw(); };
  lab.appendChild(cb); lab.appendChild(document.createTextNode(' 차트에 진입·손절·목표 선 표시 (15분·1시간·4시간·1일 차트에서)')); lab.style.cssText = 'display:block;margin-top:6px;cursor:pointer';
  box.appendChild(lab);
  box.appendChild(entEl('div', 'sub dim', '조건이 겹쳤다는 뜻이며 이길 확률이 아닙니다. 위 "과거 검증"을 함께 보세요. 마지막 계산 ' + new Date(d.generated).toLocaleTimeString('ko-KR', {hour12: false}) + ' · 1분마다 갱신'));
}
function entDraw() {
  try {
    entStore();
    const iv = typeof apiIv === 'function' ? apiIv() : '', tf = ENT_TF[iv], d = ENT.data;
    const key = [tf, d && d.generated, ENT.lineOn].join('|');
    if (key === ENT.key) return;
    ENT.key = key;
    for (const ln of ENT.lines) { try { candle.removePriceLine(ln); } catch (e) { /* 이미 없음 */ } }
    ENT.lines = [];
    if (!ENT.lineOn || !tf || !d || !d.ok) return;
    const p = d.plans.timeframes[tf], b = p && p.status === 'ok' ? p.best : null;
    if (!b || !['in_zone', 'approaching', 'waiting'].includes(b.state)) return;
    const a = b.state === 'waiting' ? '88' : '', side = b.direction === 'long' ? '롱' : '숏';
    const add = (price, color, style, title, label) => ENT.lines.push(candle.createPriceLine({price, color: color + a, lineWidth: 1, lineStyle: style, axisLabelVisible: label, title}));
    add(b.zone[0], '#64b5f6', 3, '', false); add(b.zone[1], '#64b5f6', 3, '', false);
    add(b.entry, '#42a5f5', 2, side + ' 진입', true); add(b.stop, '#ef5350', 2, '손절', true);
    add(b.targets[0], '#66bb6a', 2, '목표1', true); add(b.targets[1], '#66bb6a', 3, '목표2', true);
  } catch (err) { /* 선을 못 그려도 차트는 그대로 */ }
}
async function entLoad() {
  try { const r = await fetch('/api/entry-plan'); if (!r.ok) return; ENT.data = await r.json(); entCard(); ENT.key = ''; entDraw(); } catch (e) { /* 다음 주기에 다시 */ }
}
if (!ENT.started) { ENT.started = true; setTimeout(entLoad, 800); setInterval(entLoad, 60000); }
// ---- /진입 계획 ----
