// ---- 이상 변동 봉 표시: ena_bot/shock.py 와 같은 규칙(직전 180봉의 변동폭·몸통 대비 6배 이상, 최소 범위 3%). 원인은 추정하지 않는다. ----
const SHK = {win: 180, z: 6, minRange: 0.03, wick: 0.6, mad: 1.4826};
function shkMid(s) { const n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; }
function shkMedMad(x, i, w) {          // 직전 w개(i 자신은 제외)의 중앙값과 절대편차 중앙값: 미래를 쓰지 않는다
  const s = x.slice(i - w, i).sort((a, b) => a - b), med = shkMid(s);
  const d = s.map(v => Math.abs(v - med)).sort((a, b) => a - b);
  return [med, shkMid(d)];
}
function shkScale(med, mad, rel) { return Math.max(SHK.mad * mad, rel * Math.abs(med), 1e-9); }
function detectShocks(b) {
  const n = b.length, out = [];
  const rng = b.map(x => Math.log(Math.max(x.high, 1e-12) / Math.max(x.low, 1e-12)));
  const body = b.map(x => Math.abs(Math.log(Math.max(x.close, 1e-12) / Math.max(x.open, 1e-12))));
  const vol = b.map(x => Math.log1p(Math.max(x.volume || 0, 0)));
  for (let i = SHK.win; i < n; i++) {
    if (!(rng[i] >= SHK.minRange)) continue;                       // 대부분의 봉은 여기서 건너뛴다(범위 3% 미만)
    const [mr, dr] = shkMedMad(rng, i, SHK.win), [mb, db] = shkMedMad(body, i, SHK.win);
    const zr = (rng[i] - mr) / shkScale(mr, dr, 0.25), zb = (body[i] - mb) / shkScale(mb, db, 0.25);
    if (!(zr >= SHK.z || zb >= SHK.z)) continue;
    const x = b[i], full = x.high - x.low;
    const upper = full > 0 ? (x.high - Math.max(x.open, x.close)) / full : 0, lower = full > 0 ? (Math.min(x.open, x.close) - x.low) / full : 0;
    let zv = null;
    if (b.some(y => y.volume > 0)) { const [mv, dv] = shkMedMad(vol, i, SHK.win); zv = (vol[i] - mv) / shkScale(mv, dv, 0); }
    const ev = {i, time: x.time, direction: x.close >= x.open ? 'up' : 'down', ret: x.close / x.open - 1, rangePct: Math.expm1(rng[i]),
      zRange: zr, zBody: zb, zVolume: zv, upper, lower, kind: Math.max(upper, lower) >= SHK.wick ? 'wick_spike' : 'body_run'};
    if (out.length && ev.i - out[out.length - 1].i <= 1) { if (ev.zRange > out[out.length - 1].zRange) out[out.length - 1] = ev; continue; }   // 이어진 봉은 하나의 사건
    out.push(ev);
  }
  return out;
}
function shockText(e) {
  const sgn = e.ret >= 0 ? '+' : '-', pct = sgn + Math.round(Math.abs(e.ret) * 100) + '%';
  let t = (e.kind === 'wick_spike' ? '꼬리 급등락' : e.direction === 'up' ? '장대 양봉' : '장대 음봉') + ' ' + pct + ' · 평소 변동폭의 ' + Math.round(1 + e.zRange) + '배 수준';
  if (e.zVolume != null && e.zVolume >= 3) t += ' · 거래량도 급증';
  if (e.kind === 'wick_spike') t += ' · 체결·데이터 오류 가능성 확인 필요';
  return t + ' · 원인 미확인';
}
const SHK_SHOW_Z = 9;   // 화면에는 평소 변동폭의 10배 이상(z>=9)인 봉만 표시한다(탐지·사후 통계의 기준 6배는 그대로)
let SHK_KEY = '', SHK_CACHE = [];
function shockEvents() {               // 화면의 봉이 바뀔 때만 다시 계산한다
  const L = bars.length, k = L + ':' + (L ? bars[0].time + ':' + bars[L - 1].time + ':' + bars[L - 1].close + ':' + bars[L - 1].high + ':' + bars[L - 1].low : '');
  if (k !== SHK_KEY) { SHK_KEY = k; SHK_CACHE = detectShocks(bars).filter(e => e.zRange >= SHK_SHOW_Z); }
  return SHK_CACHE;
}
function shockMarkers() {
  return shockEvents().map(e => ({time: e.time, position: e.direction === 'up' ? 'aboveBar' : 'belowBar', color: '#f5b942', shape: 'circle',
    text: '⚡' + (e.ret >= 0 ? '+' : '-') + Math.round(Math.abs(e.ret) * 100) + '%'}));
}
function shockTip(t) {
  const e = t == null ? null : shockEvents().find(x => x.time === t);
  return e ? '<div style="margin-top:4px"><b>⚡ ' + shockText(e) + '</b></div>' : '';
}
