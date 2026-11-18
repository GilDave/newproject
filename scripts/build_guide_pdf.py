#!/usr/bin/env python3
"""Build the user guide PDF from solomon/USER_GUIDE.md (cover, table of contents, page numbers).

    python3 scripts/build_guide_pdf.py [output.pdf]

Needs Node.js with Playwright's Chromium (only for building the PDF; using Solomon does not need it).
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from solomon import __version__, mdhtml  # noqa: E402

JS = r"""
const { chromium } = require('playwright');
(async () => {
  const [src, out] = process.argv.slice(2);
  const b = await chromium.launch(); const p = await b.newPage();
  await p.goto('file://' + src, { waitUntil: 'load' });
  await p.pdf({ path: out, format: 'A4', printBackground: true, displayHeaderFooter: true,
    headerTemplate: '<span></span>',
    footerTemplate: '<div style="width:100%;font-size:8px;color:#7a8799;display:flex;justify-content:space-between;padding:0 16mm"><span>Solomon 사용 가이드</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
    margin: { top: '18mm', bottom: '20mm', left: '16mm', right: '16mm' } });
  await b.close();
})();
"""


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "release" / f"Solomon-사용가이드-v{__version__}.pdf").resolve()
    md = (ROOT / "solomon" / "USER_GUIDE.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "guide.html"
        page.write_text(mdhtml.print_page(md, __version__, time.strftime("%Y-%m-%d")), encoding="utf-8")
        script = Path(tmp) / "pdf.js"
        script.write_text(JS, encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(["node", str(script), str(page), str(out)], capture_output=True, text=True)
    if res.returncode != 0:
        print("PDF 생성 실패 (Node.js와 Playwright가 필요합니다):\n" + res.stderr, file=sys.stderr)
        return 1
    print(f"만들었습니다: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
