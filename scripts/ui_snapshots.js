// 화면 연출 확인용: 미리보기 서버를 띄운 뒤 데모를 재생하며 단계별 스크린샷을 찍는다 (검수 AI가 눈으로 확인할 자료).
//   python3 scripts/ui_preview_server.py &   그리고   NODE_PATH=$(npm root -g) node scripts/ui_snapshots.js /tmp/shots
const { chromium } = require('playwright');
(async () => {
  const out = process.argv[2] || '/tmp/shots';
  require('fs').mkdirSync(out, { recursive: true });
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto('http://127.0.0.1:8799/?t=tok'); await p.waitForTimeout(1200);
  await p.click('#fxDemo'); let t = 0;
  for (const m of [1500, 3950, 6000, 10850, 15800, 18000, 21000, 23000, 26500]) { await p.waitForTimeout(m - t); t = m; await p.screenshot({ path: `${out}/demo-${m}.png` }); }
  console.log(errs.length ? 'JS errors: ' + errs.join(' | ') : 'no JS errors'); await b.close();
})();
