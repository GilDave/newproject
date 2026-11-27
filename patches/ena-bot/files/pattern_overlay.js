// ---- 차트 패턴 감지(참고용): 쌍바닥·쌍봉·헤드앤숄더·삼각·쐐기·깃발·박스권을 차트에 겹쳐 그리고, 각 패턴의 과거 통계를 함께 보여 준다. 신호가 아니다. ----
const PAT = {on: true, collapsed: false, loaded: false, data: null, dataKey: '', at: 0, busy: false, key: '', series: [], box: null, rev: 0, open: {}};
const PAT_COLOR = '#ce93d8', PAT_UP = '#26a69a', PAT_DOWN = '#ef5350', PAT_NEUTRAL = '#9aa3b5', PAT_WARN = '#d4a24c';
function patStore() {
  try {
    if (!PAT.loaded) { PAT.loaded = true; const v = JSON.parse(localStorage.getItem('ena_pattern_v1') || '{}'); if (typeof v.on === 'boolean') PAT.on = v.on; if (typeof v.collapsed === 'boolean') PAT.collapsed = v.collapsed; }
    else localStorage.setItem('ena_pattern_v1', JSON.stringify({on: PAT.on, collapsed: PAT.collapsed}));
  } catch (e) { /* 기억하지 못해도 동작한다 */ }
}
function patFmt(v) { return typeof fmt === 'function' ? fmt(v, 5) : Number(v).toFixed(5); }
// 차트 라이브러리는 시간이 '엄격히 오름차순'이 아니면 오류를 내 차트 전체가 깨진다. 항상 정렬하고 중복·범위 밖을 걷어낸다.
function patSortedPts(points, tmin) {
  const out = []; let last = -Infinity;
  for (const p of (points || []).slice().sort((a, b) => a[0] - b[0])) {
    const t = p[0] / 1000; if (!(t >= tmin) || !(t > last) || !Number.isFinite(p[1])) continue;
    out.push({time: t, value: p[1]}); last = t;
  }
  return out;
}
function patClear() { for (const s of PAT.series) { try { chart.removeSeries(s); } catch (e) { /* 이미 없음 */ } } PAT.series = []; }
async function patFetch() {
  const key = SYMBOL + ':' + iv;
  if (PAT.busy || (PAT.data && PAT.dataKey === key && Date.now() - PAT.at < 60000)) return;
  PAT.busy = true;
  try {
    const r = await fetch('/api/patterns?symbol=' + encodeURIComponent(SYMBOL) + '&interval=' + encodeURIComponent(iv)); const j = await r.json();
    if (!r.ok || j.error) throw new Error('patterns'); PAT.data = j;
  } catch (e) { PAT.data = {error: true}; }
  PAT.dataKey = key; PAT.at = Date.now(); PAT.busy = false; PAT.rev++; PAT.key = ''; patDraw();
}
function patBoxEl() {
  if (PAT.box && PAT.box.isConnected) return PAT.box;
  const anchor = document.getElementById('fractal-box') || document.getElementById('chartwrap') || document.getElementById('chart'); if (!anchor) return null;
  const box = document.createElement('div'); box.id = 'pattern-box';
  box.style.cssText = 'margin:6px 0;padding:8px 10px;border:1px solid #2a2e39;border-radius:8px;background:#131722;color:#d1d4dc;font:12px/1.6 sans-serif';
  anchor.insertAdjacentElement('afterend', box); PAT.box = box; return box;
}
function patStatText(s) {
  if (!s || s.mean_net_pct === undefined) return {text: s && s.events !== undefined ? '과거 이벤트 ' + s.events + '건 — 표본 부족' : '과거 통계 없음', bad: false};
  const sg = (v) => (v > 0 ? '+' : '') + v.toFixed(2) + '%';
  return {text: '과거 ' + s.events.toLocaleString('ko-KR') + '건 · 24시간 뒤 비용 후 평균 ' + sg(s.mean_net_pct) + ' (95% 구간 ' + sg(s.ci95_pct[0]) + ' ~ ' + sg(s.ci95_pct[1]) + ') · 판정: ' + s.verdict, bad: !/^합격/.test(s.verdict)};
}
function patBox() {
  const box = patBoxEl(); if (!box) return; box.textContent = '';
  const mk = (tag, text, css) => { const d = document.createElement(tag); if (text !== undefined) d.textContent = text; if (css) d.style.cssText = css; return d; };
  const head = mk('div', undefined, 'display:flex;gap:12px;align-items:center;flex-wrap:wrap');
  head.appendChild(mk('b', '차트 패턴 감지 (참고용 · 매매 신호 아님)'));
  const lb = document.createElement('label'), cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = PAT.on;
  cb.onchange = () => { PAT.on = cb.checked; patStore(); PAT.key = ''; patDraw(); }; lb.appendChild(cb); lb.appendChild(document.createTextNode(' 차트에 표시')); lb.style.cursor = 'pointer'; head.appendChild(lb);
  const fold = mk('span', PAT.collapsed ? '▸ 펼치기' : '▾ 접기', 'margin-left:auto;cursor:pointer;opacity:.8'); fold.onclick = () => { PAT.collapsed = !PAT.collapsed; patStore(); patBox(); }; head.appendChild(fold); box.appendChild(head);
  if (PAT.collapsed) return;
  const d = PAT.data;
  if (!d) { box.appendChild(mk('div', '불러오는 중…', 'opacity:.7')); return; }
  if (d.error) { box.appendChild(mk('div', '패턴을 불러오지 못했습니다.', 'opacity:.7')); return; }
  if (!d.supported) { box.appendChild(mk('div', d.reason || '이 시간봉은 지원하지 않습니다.', 'opacity:.8;margin-top:4px')); return; }
  const price = bars.length ? bars[bars.length - 1].close : null, list = (d.patterns || []).slice(0, 8);
  const addDetails = (key, title, fill) => {      // 열림 상태는 PAT.open 에 기억해 1분마다 다시 그려도 접히지 않는다
    const det = mk('details', undefined, 'margin-top:6px'); det.open = !!PAT.open[key];
    det.addEventListener('toggle', () => { PAT.open[key] = det.open; });
    det.appendChild(mk('summary', title, 'cursor:pointer;color:#9ec3ff')); fill(det); box.appendChild(det);
  };
  if (!list.length) box.appendChild(mk('div', '지금 이 시간봉(' + d.tf + ')에서 감지된 패턴이 없습니다.', 'margin-top:4px;opacity:.85'));
  else {
    const tbl = mk('table', undefined, 'border-collapse:collapse;width:100%;margin-top:6px'), th = tbl.insertRow();
    for (const h of ['패턴(교과서상 방향)', '상태', '기준선(목선 등)', '이 패턴의 과거 통계 (' + (d.horizon || '24시간 뒤') + ')']) th.appendChild(mk('th', h, 'text-align:left;color:#8a8f98;font-weight:600;padding:3px 8px'));
    for (const g of list) {
      const tr = tbl.insertRow(); tr.style.borderTop = '1px solid #2a2e39';
      const c0 = tr.insertCell(); const ar = mk('span', g.dir > 0 ? '↗ ' : '↘ ', 'font-weight:700;color:' + PAT_NEUTRAL); ar.title = '교과서에서 이 패턴이 뜻한다고 하는 방향입니다. 실제 과거 통계는 오른쪽 열을 보세요.'; c0.appendChild(ar); c0.appendChild(document.createTextNode(g.name));
      const exg = (d.explain || {})[g.pat]; if (exg) c0.appendChild(mk('div', '교과서: ' + exg.bias, 'font-size:11px;opacity:.7'));
      tr.insertCell().textContent = g.state === 'forming' ? '형성 중(아직 돌파 전)' : '돌파 확정 · ' + g.bars_ago + '봉 전';
      tr.insertCell().textContent = g.level != null ? patFmt(g.level) + (price ? ' (현재가보다 ' + Math.abs((g.level / price - 1) * 100).toFixed(1) + '% ' + (g.level >= price ? '위' : '아래') + ')' : '') : '—';
      const st = patStatText((d.stats || {})[g.pat]), c3 = tr.insertCell(); c3.textContent = st.text; if (st.bad) c3.style.color = PAT_WARN;
      for (const td of tr.cells) td.style.cssText += ';padding:3px 8px;vertical-align:top;text-align:left';
    }
    box.appendChild(tbl);
    const ex = d.explain || {}, seen = new Set();
    for (const g of list) {
      if (seen.has(g.pat) || !ex[g.pat]) continue; seen.add(g.pat); const e = ex[g.pat];
      addDetails('p:' + g.pat, g.name + '이(가) 뭔가요? · ' + e.bias, (det) => {
        for (const [lab, key] of [['모양', 'shape'], ['교과서 해석', 'reads'], ['확정 조건', 'confirm'], ['무효 조건', 'fail'], ['주의', 'caution']]) {
          const row = mk('div', undefined, 'margin:3px 0 0 14px'); row.appendChild(mk('b', lab + ': ', 'color:#8a8f98')); row.appendChild(document.createTextNode(e[key])); det.appendChild(row);
        }
      });
    }
  }
  if (d.glossary) addDetails('glossary', '롱·숏이 무슨 뜻인가요? 이 패턴대로 하면 되나요?', (det) => det.appendChild(mk('div', d.glossary, 'margin:3px 0 0 14px')));
  const meta = d.stats_meta ? ' (통계 생성일 ' + (d.stats_meta.generated || '?') + ', 코인 ' + (d.stats_meta.coins || '?') + '개, 왕복 비용 ' + (d.stats_meta.cost_round_trip_pct || 0).toFixed(2) + '% 차감)' : ' (과거 통계표가 없어 통계는 표시하지 않습니다)';
  box.appendChild(mk('div', '※ 참고용입니다. 이 패턴들은 18개 코인 과거 이력 시험에서 비용을 넘는 초과 수익이 확인되지 않았습니다' + meta + '. 패턴 이름이 방향을 보장하지 않습니다. 보라색 점선 = 패턴 모양, 기준선 = 목선·추세선(마감된 봉만 사용, 돌파는 종가 기준).', 'margin-top:6px;opacity:.75;font-size:11px'));
}
function patDraw() {
  try {
    patStore();
    const L = bars.length; if (L < 2) return;
    patFetch();
    const key = L + ':' + bars[L - 1].time + ':' + iv + ':' + PAT.on + ':' + PAT.rev;
    if (key === PAT.key) return;
    PAT.key = key; patClear();
    if (PAT.on && PAT.data && PAT.data.supported && PAT.data.interval === iv) {
      const tmin = bars[0].time, items = (PAT.data.patterns || []).slice(0, 6);
      for (const g of items) {
        const col = g.dir > 0 ? PAT_UP : PAT_DOWN;
        const pts = patSortedPts(g.points, tmin), made = [];
        try {
        if (pts.length >= 2) {
          const s = chart.addLineSeries({color: PAT_COLOR, lineWidth: 2, lineStyle: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false}); made.push(s); s.setData(pts);
        }
        let marked = false;
        for (const ln of (g.lines || [])) {
          const a = {time: ln[0][0] / 1000, value: ln[0][1]}, b = {time: ln[1][0] / 1000, value: ln[1][1]};
          if (!(b.time > a.time) || b.time < tmin) continue;
          const s = chart.addLineSeries({color: '#f5a623', lineWidth: 2, lineStyle: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false});
          made.push(s); s.setData([a, b]);
          if (!marked && g.state === 'confirmed' && g.time && Math.abs(b.time - g.time / 1000) < 1) {
            s.setMarkers([{time: b.time, position: g.dir > 0 ? 'belowBar' : 'aboveBar', color: col, shape: g.dir > 0 ? 'arrowUp' : 'arrowDown', text: g.name + ' 돌파'}]); marked = true;
          }
          if (g.state === 'forming' && g.level != null && !marked) { s.createPriceLine({price: g.level, color: '#f5a623', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: g.name + ' 기준선'}); marked = true; }
        }
        PAT.series.push(...made);
        } catch (e) {        // 이 패턴을 그리다 실패하면 그 패턴의 선만 걷어낸다. 차트는 그대로 둔다.
          for (const s of made) { try { chart.removeSeries(s); } catch (e2) { /* 이미 없음 */ } }
        }
      }
    }
    try { patBox(); } catch (e) { /* 표를 못 만들어도 차트의 선은 그대로 둔다 */ }
  } catch (e) { try { patClear(); } catch (e2) { /* 패턴 표시에 실패해도 차트는 그대로 */ } }
}
if (typeof structDraw === 'function' && !structDraw.__pt) { const patBase = structDraw; structDraw = function () { patBase(); patDraw(); }; structDraw.__pt = true; }
// ---- /차트 패턴 감지 ----
