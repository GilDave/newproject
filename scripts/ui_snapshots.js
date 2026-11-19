// 화면 연출 확인용: 데모를 재생하며 단계별 스크린샷을 OUT 폴더에 저장한다 (검수 AI가 눈으로 보는 자료).
// 사용: BASE=http://127.0.0.1:PORT node scripts/ui_snapshots.js OUT    종료 코드: 0 정상, 3 화면이 깨짐(JS 오류·무대 없음)
const { chromium } = require('playwright');
(async () => {
  const out = process.argv[2] || '/tmp/shots', base = process.env.BASE || 'http://127.0.0.1:8799';
  require('fs').mkdirSync(out, { recursive: true });
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(base + '/?t=tok'); await p.waitForTimeout(1200);
  if (!(await p.$('#stage canvas#fx'))) errs.push('무대(canvas#fx)가 화면에 없습니다');
  await p.click('#fxDemo'); let t = 0; let i = 1;
  for (const m of [1500, 3950, 6000, 10850, 18000, 23000]) {
    await p.waitForTimeout(m - t); t = m;
    await (await p.$('#stage')).screenshot({ path: `${out}/${String(i++).padStart(2, '0')}-demo-${m}ms.png` });
  }
  await b.close();
  if (errs.length) { console.log('JS errors: ' + errs.join(' | ')); process.exit(3); }
  console.log('ok');
})().catch(e => { console.log('capture failed: ' + e); process.exit(2); });
