"""Which AIs can really LOOK at an image? Measured, never assumed.

A tiny test picture (three coloured stripes, random order) is saved and the AI is asked which colours it sees, left to right.
Only an AI that names them correctly is trusted to review screenshots. The result is remembered in ~/.solomon/vision.json.
"""

from __future__ import annotations

import json
import random
import struct
import tempfile
import time
import zlib
from pathlib import Path
from typing import Callable, Optional

COLORS = {"red": (220, 40, 40), "green": (40, 180, 70), "blue": (40, 90, 220), "yellow": (235, 200, 30)}


def write_png(path: Path, width: int, height: int, pixel: Callable[[int, int], tuple[int, int, int]]) -> None:
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def make_probe(path: Path, rng: Optional[random.Random] = None) -> list[str]:
    """Writes a 300x120 picture of three vertical stripes and returns their colour names, left to right."""
    names = (rng or random).sample(list(COLORS), 3)
    write_png(path, 300, 120, lambda x, y: COLORS[names[min(2, x // 100)]])
    return names


def probe_prompt(image: Path) -> str:
    return (f"Open the image file at {image} and look at it. It shows three vertical stripes of flat colour.\n"
            "Reply with ONLY one JSON object: {\"colors\": [left, middle, right]} using lowercase English names from red, green, blue, yellow.")


def check(answer: str, expected: list[str]) -> bool:
    from .jsonutil import extract_json

    try:
        got = [str(c).strip().lower() for c in extract_json(answer).get("colors", [])]
    except (ValueError, AttributeError):
        return False
    return got == expected


def store_path() -> Path:
    return Path.home() / ".solomon" / "vision.json"


def load() -> dict:
    try:
        return json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(results: dict) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = load()
    cur.update(results)
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")


def verified(name: str, provider: object = None) -> bool:
    """A provider object may state it directly (tests, future CLIs); otherwise the saved probe result decides."""
    if provider is not None and hasattr(provider, "vision_verified"):
        return bool(getattr(provider, "vision_verified"))
    return bool((load().get(name) or {}).get("ok"))


def probe(providers: dict, names: list[str], timeout: float = 240.0) -> dict:
    """Ask each named provider about a fresh test picture. Returns {name: {ok, error, at}} and saves it."""
    results: dict = {}
    for name in names:
        prov = providers.get(name)
        out = {"ok": False, "error": "", "at": time.time()}
        results[name] = out
        if prov is None or not getattr(prov, "image_capable", False):
            out["error"] = "이 AI의 CLI는 이미지 입력을 지원하도록 연결되어 있지 않습니다"
            continue
        if not prov.locate():
            out["error"] = "설치되지 않았습니다"
            continue
        with tempfile.TemporaryDirectory(prefix="solomon-vision-") as tmp:
            img = Path(tmp) / "probe.png"
            expected = make_probe(img)
            try:
                res = prov.run(probe_prompt(img), cwd=tmp, write=False, timeout=timeout, images=[str(img)])
            except Exception as exc:
                out["error"] = str(exc)[:200]
                continue
            out["ok"] = check(res.text, expected)
            if not out["ok"]:
                out["error"] = f"색을 맞히지 못했습니다 (정답 {expected}, 응답 {res.text.strip()[:80]!r})"
    save(results)
    return results
