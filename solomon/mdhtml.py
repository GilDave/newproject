"""A small, safe Markdown -> HTML converter for the built-in user guide (headings, lists, tables, code, quotes).

Everything is HTML-escaped first; only the constructs below produce tags. Links are limited to http(s), relative and #anchors."""

from __future__ import annotations

import html
import re

_SLUG = re.compile(r"[^\w가-힣-]+", re.U)


def slug(text: str) -> str:
    return _SLUG.sub("-", text.strip().lower()).strip("-")


def _inline(text: str) -> str:
    t = html.escape(text, quote=False)
    codes: list[str] = []

    def keep(m: re.Match) -> str:
        codes.append(f"<code>{m.group(1)}</code>")
        return f"\0{len(codes) - 1}\0"

    t = re.sub(r"`([^`]+)`", keep, t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)

    def link(m: re.Match) -> str:
        label, url = m.group(1), html.unescape(m.group(2))
        if not re.match(r"^(https?://|#|[\w./-]+$)", url):
            return m.group(0)
        return f'<a href="{html.escape(url, quote=True)}"' + (' target="_blank" rel="noopener"' if url.startswith("http") else "") + f">{label}</a>"

    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, t)
    return re.sub(r"\0(\d+)\0", lambda m: codes[int(m.group(1))], t)


def render(md: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Returns (body html, table of contents [(level, text, anchor)])."""
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []
    list_stack: list[str] = []  # "ul" / "ol" per open nesting level (indent // 2)

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def close_lists(to: int = 0) -> None:
        while len(list_stack) > to:
            out.append(f"</li></{list_stack.pop()}>")

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush_para(); close_lists()
            lang, buf = line[3:].strip(), []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i]); i += 1
            out.append('<pre><code>' + html.escape("\n".join(buf)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush_para(); close_lists()
            lvl, text = len(m.group(1)), m.group(2).strip()
            anchor = slug(text)
            toc.append((lvl, text, anchor))
            out.append(f'<h{lvl} id="{html.escape(anchor, quote=True)}">{_inline(text)}</h{lvl}>')
            i += 1
            continue
        if re.match(r"^-{3,}\s*$", line):
            flush_para(); close_lists(); out.append("<hr>"); i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|\s*$", lines[i + 1]):
            flush_para(); close_lists()
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")
            continue
        if line.startswith(">"):
            flush_para(); close_lists()
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i][1:].strip()); i += 1
            out.append("<blockquote>" + _inline(" ".join(buf)) + "</blockquote>")
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if m:
            flush_para()
            depth = len(m.group(1)) // 2 + 1
            kind = "ol" if m.group(2)[0].isdigit() else "ul"
            if depth > len(list_stack):
                while len(list_stack) < depth:
                    start = f' start="{int(m.group(2)[:-1])}"' if kind == "ol" and len(list_stack) == depth - 1 and int(m.group(2)[:-1]) != 1 else ""
                    out.append(f"<{kind}{start}><li>"); list_stack.append(kind)
            else:
                close_lists(depth)
                out.append("</li><li>")
            out[-1] = out[-1] + _inline(m.group(3)) if out[-1].endswith("<li>") else out[-1]
            if not out[-1].endswith(_inline(m.group(3))):
                out.append(_inline(m.group(3)))
            i += 1
            continue
        if not line.strip():
            flush_para(); close_lists(); i += 1
            continue
        if list_stack and line.startswith("  "):  # continuation of a list item
            out.append(" " + _inline(line.strip())); i += 1
            continue
        close_lists()
        para.append(line.strip()); i += 1
    flush_para(); close_lists()
    return "\n".join(out), toc


PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Solomon 사용 가이드</title><style>
:root{--bg:#070b12;--panel:#0e1622;--line:#1d2a3b;--text:#dbe4f0;--dim:#8696ab;--acc:#6ea8ff;--code:#08101a}
@media (prefers-color-scheme: light){:root{--bg:#f6f8fb;--panel:#fff;--line:#d9e1ec;--text:#1c2533;--dim:#5b6b80;--acc:#1c5fd1;--code:#eef2f8}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font:15px/1.75 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",system-ui,sans-serif}
.wrap{display:grid;grid-template-columns:260px minmax(0,860px);gap:32px;max-width:1200px;margin:0 auto;padding:24px 16px}
nav{position:sticky;top:16px;align-self:start;max-height:calc(100vh - 32px);overflow:auto;border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:var(--panel);font-size:13px}
nav a{display:block;color:var(--dim);text-decoration:none;padding:2px 0}nav a:hover{color:var(--acc)}nav a.l3{padding-left:14px}nav a.l4{padding-left:28px}
main{min-width:0}h1{font-size:28px}h2{margin-top:2.2em;padding-top:.6em;border-top:1px solid var(--line);font-size:22px}h3{margin-top:1.8em;font-size:18px}h4{font-size:15px}
code{background:var(--code);border:1px solid var(--line);border-radius:5px;padding:1px 5px;font:13px ui-monospace,Menlo,monospace}
pre{background:var(--code);border:1px solid var(--line);border-radius:10px;padding:12px 14px;overflow:auto}pre code{border:0;padding:0;background:none}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}th{background:var(--panel)}
blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid var(--acc);background:var(--panel);border-radius:0 8px 8px 0}
a{color:var(--acc)}hr{border:0;border-top:1px solid var(--line)}
@media (max-width:860px){.wrap{grid-template-columns:1fr}nav{position:static}}
</style></head><body><div class="wrap"><nav>%NAV%</nav><main>%BODY%</main></div></body></html>"""


def page(md: str) -> str:
    body, toc = render(md)
    nav = "".join(f'<a class="l{lvl}" href="#{html.escape(a, quote=True)}">{html.escape(t)}</a>' for lvl, t, a in toc if lvl in (2, 3))
    return PAGE.replace("%NAV%", nav).replace("%BODY%", body)


PRINT_CSS = """
@page{size:A4;margin:18mm 16mm 20mm 16mm}
:root{--text:#1c2533;--dim:#5b6b80;--line:#d9e1ec;--acc:#1c5fd1;--code:#f1f4f9;--panel:#f6f8fb}
*{box-sizing:border-box}html,body{background:#fff;color:var(--text);margin:0;
font:10.5pt/1.65 "Apple SD Gothic Neo","Noto Sans KR","WenQuanYi Zen Hei",-apple-system,system-ui,sans-serif}
h1{font-size:22pt;margin:0 0 6pt}h2{font-size:15pt;margin:20pt 0 6pt;padding-top:8pt;border-top:1.5pt solid var(--line);break-after:avoid}
h3{font-size:12pt;margin:14pt 0 4pt;break-after:avoid}h4{font-size:10.5pt;break-after:avoid}
p,li{orphans:3;widows:3}code{font:9pt "DejaVu Sans Mono",Menlo,"WenQuanYi Zen Hei",monospace;background:var(--code);border:.5pt solid var(--line);border-radius:3pt;padding:0 3pt}
pre{background:var(--code);border:.5pt solid var(--line);border-radius:5pt;padding:7pt 9pt;white-space:pre-wrap;word-break:break-word;break-inside:avoid}
pre code{border:0;padding:0;background:none;font-size:8.8pt}
table{border-collapse:collapse;width:100%;margin:8pt 0;font-size:9.2pt}tr{break-inside:avoid}th,td{border:.5pt solid var(--line);padding:3.5pt 6pt;text-align:left;vertical-align:top}th{background:var(--panel)}
blockquote{margin:8pt 0;padding:5pt 10pt;border-left:2.5pt solid var(--acc);background:var(--panel)}
a{color:var(--acc);text-decoration:none}hr{border:0;border-top:.5pt solid var(--line)}
.cover{min-height:235mm;display:flex;flex-direction:column;justify-content:center;break-after:page}
.cover h1{font-size:34pt;letter-spacing:.04em}.cover .sub{font-size:14pt;color:var(--dim);margin:6pt 0 24pt}.cover .meta{color:var(--dim);font-size:10pt}
.toc{break-after:page}.toc h2{border:0;margin-top:0}.toc a{display:block;padding:1.5pt 0}.toc a.l3{padding-left:14pt;color:var(--dim)}
"""


def print_page(md: str, version: str, date: str, doc_title: str = "사용 가이드", subtitle: str = "사용 가이드 · 설치부터 시험까지") -> str:
    """The guide as a print-ready document: cover, table of contents, body. Light theme, A4."""
    body, toc = render(md)
    body = re.sub(r"^<h1[^>]*>.*?</h1>\n?", "", body, count=1, flags=re.S)  # the cover carries the title
    items = "".join(f'<a class="l{lvl}" href="#{html.escape(a, quote=True)}">{html.escape(t)}</a>' for lvl, t, a in toc if lvl in (2, 3))
    cover = (f'<section class="cover"><h1>Solomon</h1><div class="sub">{html.escape(subtitle)}</div>'
             f'<div class="meta">버전 {html.escape(version)} · {html.escape(date)}<br>여러 AI가 서로 검증하며 만드는 개발 도구<br>'
             f'구독 로그인 CLI만 사용 · 원본은 사람이 적용할 때만 변경</div></section>')
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>Solomon {html.escape(doc_title)} v{html.escape(version)}</title>'
            f"<style>{PRINT_CSS}</style></head><body>{cover}<section class=\"toc\"><h2>목차</h2>{items}</section><main>{body}</main></body></html>")
