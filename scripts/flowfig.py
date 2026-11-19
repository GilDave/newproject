"""Tiny SVG flowchart helper (no dependencies): boxes, diamonds, arrows with labels. Used by build_flow_pdf.py."""
import html

COL = {"main": ("#e8f1ff", "#2f6fdb"), "ai": ("#efe8ff", "#7b5cd6"), "gate": ("#fff4d6", "#c98a00"), "ok": ("#dff5e8", "#1f9d6b"),
       "bad": ("#fde4e4", "#d33f49"), "io": ("#eef1f5", "#64748b"), "bot": ("#e1f6f3", "#0f8f84")}


class Fig:
    def __init__(self, w, h, title=""):
        self.w, self.h, self.items, self.title, self.n = w, h, [], title, {}

    def box(self, key, x, y, w, h, text, kind="main", shape="box"):
        self.n[key] = (x, y, w, h, shape)
        fill, stroke = COL[kind]
        lines = text.split("\n")
        if shape == "diamond":
            pts = f"{x + w / 2},{y} {x + w},{y + h / 2} {x + w / 2},{y + h} {x},{y + h / 2}"
            shp = f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>'
        else:
            shp = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>'
        ty = y + h / 2 - (len(lines) - 1) * 7 + 4
        tx = "".join(f'<text x="{x + w / 2}" y="{ty + i * 14}" text-anchor="middle" font-size="11.5" fill="#1b2433">{html.escape(l)}</text>' for i, l in enumerate(lines))
        self.items.append(shp + tx)

    def _edge_point(self, key, tx, ty):
        x, y, w, h, shape = self.n[key]
        cx, cy = x + w / 2, y + h / 2
        dx, dy = tx - cx, ty - cy
        if dx == 0 and dy == 0:
            return cx, cy
        if shape == "diamond":
            k = 1 / (abs(dx) / (w / 2) + abs(dy) / (h / 2))
            return cx + dx * k, cy + dy * k
        k = min((w / 2) / abs(dx) if dx else 1e9, (h / 2) / abs(dy) if dy else 1e9)
        return cx + dx * k, cy + dy * k

    def arrow(self, a, b, label="", dash=False, via=None, color="#475569", lx=0, ly=0):
        ax, ay, aw, ah, _ = self.n[a]
        bx, by, bw, bh, _ = self.n[b]
        pts = [(ax + aw / 2, ay + ah / 2)] + (via or []) + [(bx + bw / 2, by + bh / 2)]
        p0 = self._edge_point(a, *pts[1])
        p1 = self._edge_point(b, *pts[-2])
        path = [p0] + pts[1:-1] + [p1]
        d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in path)
        da = ' stroke-dasharray="5 4"' if dash else ""
        s = f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.3"{da} marker-end="url(#ah)"/>'
        if label:
            mx, my = path[len(path) // 2] if len(path) % 2 else ((path[len(path) // 2 - 1][0] + path[len(path) // 2][0]) / 2, (path[len(path) // 2 - 1][1] + path[len(path) // 2][1]) / 2)
            s += f'<text x="{mx + lx}" y="{my + ly - 3}" text-anchor="middle" font-size="10.5" fill="{color}" paint-order="stroke" stroke="#fff" stroke-width="3">{html.escape(label)}</text>'
        self.items.append(s)

    def note(self, x, y, text, size=11, color="#334155", anchor="start", weight="400"):
        self.items.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}">{html.escape(text)}</text>')

    def svg(self):
        head = (f'<svg viewBox="0 0 {self.w} {self.h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system,Helvetica,Arial,sans-serif" '
                f'style="background:#fff;border:1px solid #d6dde8;border-radius:8px;margin:6pt 0;break-inside:avoid">'
                '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                '<path d="M0 0 L10 5 L0 10 z" fill="#475569"/></marker></defs>')
        t = f'<text x="12" y="20" font-size="13" font-weight="700" fill="#0f172a">{html.escape(self.title)}</text>' if self.title else ""
        return head + t + "".join(self.items) + "</svg>"
