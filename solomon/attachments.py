"""Screenshots a person attaches to a goal ("this is the error I see"), handed to the AIs that can really look at pictures.

The browser sends pictures as base64; here they are checked (real image bytes, size, count), written under ~/.solomon/attachments/
(outside every project, so they never end up in a diff) and named img-1.png, img-2.jpg ... Nothing from a picture is ever executed.
"""

from __future__ import annotations

import base64
import binascii
import shutil
import time
import uuid
from pathlib import Path

MAX_IMAGES = 4
MAX_BYTES = 4 * 1024 * 1024  # per picture, after decoding (the page shrinks them first)
KEEP_DAYS = 7


class AttachError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def sniff(data: bytes) -> str:
    """The file extension if the bytes really are a PNG, JPEG, WebP or GIF; '' otherwise."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return ""


def base_dir() -> Path:
    return Path.home() / ".solomon" / "attachments"


def decode(items: object) -> list[tuple[str, bytes]]:
    """[(extension, bytes)] from the page's list of {name, data} (data = base64 or a data: URL)."""
    if items in (None, "", []):
        return []
    if not isinstance(items, list):
        raise AttachError(400, "첨부 이미지 형식이 올바르지 않습니다.")
    if len(items) > MAX_IMAGES:
        raise AttachError(400, f"이미지는 한 번에 {MAX_IMAGES}장까지 붙일 수 있습니다.")
    out = []
    for it in items:
        raw = str((it or {}).get("data", "")) if isinstance(it, dict) else ""
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise AttachError(400, "이미지를 읽지 못했습니다(손상됨).") from None
        if len(data) > MAX_BYTES:
            raise AttachError(400, f"이미지 한 장이 너무 큽니다 ({MAX_BYTES // 1024 // 1024}MB 이하).")
        ext = sniff(data)
        if not ext:
            raise AttachError(400, "PNG·JPEG·WebP·GIF 이미지만 붙일 수 있습니다.")
        out.append((ext, data))
    return out


def store(pictures: list[tuple[str, bytes]], root: Path | None = None) -> list[Path]:
    if not pictures:
        return []
    root = Path(root) if root else base_dir()
    batch = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    batch.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (ext, data) in enumerate(pictures, 1):
        p = batch / f"img-{i}.{ext}"
        p.write_bytes(data)
        paths.append(p)
    cleanup(root)
    return paths


def cleanup(root: Path | None = None) -> None:
    root = Path(root) if root else base_dir()
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for d in root.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def validate_files(paths: list[str]) -> list[Path]:
    """For the command line: existing files that really are pictures."""
    out = []
    for p in paths or []:
        f = Path(p).expanduser()
        try:
            head = f.read_bytes()[:16]
        except OSError:
            raise AttachError(400, f"이미지 파일을 읽을 수 없습니다: {f.name}") from None
        if not sniff(head):
            raise AttachError(400, f"이미지 파일이 아닙니다: {f.name}")
        out.append(f.resolve())
    if len(out) > MAX_IMAGES:
        raise AttachError(400, f"이미지는 {MAX_IMAGES}장까지입니다.")
    return out
