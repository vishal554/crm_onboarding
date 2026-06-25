"""Attachment storage behind a small interface (local volume, MinIO-ready).

Files are addressed by content hash so identical bytes are physically stored
once, which complements the DB-level ``Document.sha256`` uniqueness.
"""

import base64
import hashlib
import re
from pathlib import Path

from django.conf import settings

_DATA_URI_RE = re.compile(r"^data:[^;,]*;base64,", re.IGNORECASE)


def decode_base64(value: str) -> bytes:
    """Tolerantly decode attachment base64.

    Handles real-world inputs: an optional ``data:...;base64,`` prefix and
    embedded whitespace/newlines (MIME wraps base64 at 76 chars). Raises
    ``binascii.Error`` only when the content is genuinely not base64.
    """
    cleaned = _DATA_URI_RE.sub("", value.strip())
    cleaned = re.sub(r"\s+", "", cleaned)
    return base64.b64decode(cleaned)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_format(data: bytes):
    """Return the real file type from magic bytes, or None if unrecognised."""
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    return None


class LocalStorage:
    def __init__(self, base_dir=None):
        self.base = Path(base_dir or settings.ATTACHMENT_STORAGE_DIR)

    def path_for(self, sha256: str, ext: str) -> Path:
        # Shard by first two hex chars to avoid huge flat directories.
        return self.base / sha256[:2] / f"{sha256}.{ext}"

    def exists(self, sha256: str, ext: str) -> bool:
        return self.path_for(sha256, ext).exists()

    def save(self, sha256: str, ext: str, data: bytes) -> str:
        path = self.path_for(sha256, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        return str(path)


storage = LocalStorage()
