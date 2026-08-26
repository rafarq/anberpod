from __future__ import annotations

import hashlib
import ipaddress
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.domain.errors import HttpPolicyError
from anberpod.domain.models import RequestPolicy
from anberpod.domain.ports import HttpTransport


ARTWORK_POLICY = RequestPolicy(
    connect_timeout_seconds=5.0,
    read_timeout_seconds=10.0,
    total_timeout_seconds=15.0,
    max_bytes=2 * 1024 * 1024,
)
ARTWORK_ACCEPT = "image/png, image/jpeg, image/webp"
FORMAT_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
MAX_ARTWORK_PIXELS = 16_000_000
MAX_ARTWORK_EDGE = 8_192


class ArtworkCache:
    """Fetch validated cover bytes outside rendering and expose only local paths."""

    def __init__(self, files: AtomicFiles, transport: HttpTransport) -> None:
        self.files = files
        self.transport = transport

    def ensure_cached(self, url: str | None, *, online: bool) -> Path | None:
        key = self._key(url)
        if key is None:
            return None
        existing = self._existing(key)
        if existing is not None or not online:
            return existing
        try:
            response = self.transport.request(ARTWORK_POLICY, url or "", {"Accept": ARTWORK_ACCEPT})
        except (HttpPolicyError, OSError, TimeoutError, ValueError):
            return None
        if response.status != 200 or len(response.body) > ARTWORK_POLICY.max_bytes:
            return None
        extension = self._validated_extension(response.body)
        if extension is None:
            return None
        relative = f"cache/artwork/{key}.{extension}"
        try:
            self.files.write_atomic(relative, response.body)
            return self.files.path(relative)
        except OSError:
            return None

    @staticmethod
    def _key(url: str | None) -> str | None:
        if not url or len(url) > 2048:
            return None
        try:
            parts = urlsplit(url)
            _ = parts.port
        except ValueError:
            return None
        if (
            parts.scheme.lower() != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            return None
        try:
            address = ipaddress.ip_address(parts.hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                return None
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _existing(self, key: str) -> Path | None:
        for extension in FORMAT_EXTENSIONS.values():
            relative = f"cache/artwork/{key}.{extension}"
            if not self.files.exists(relative):
                continue
            try:
                if self.files.size(relative) > ARTWORK_POLICY.max_bytes:
                    self.files.unlink(relative)
                    continue
                data = self.files.read_bytes(relative)
            except OSError:
                continue
            if self._validated_extension(data) == extension:
                return self.files.path(relative)
            self.files.unlink(relative)
        return None

    @staticmethod
    def _validated_extension(data: bytes) -> str | None:
        try:
            with Image.open(BytesIO(data)) as image:
                extension = FORMAT_EXTENSIONS.get(image.format or "")
                width, height = image.size
                if (
                    extension is None
                    or width < 1
                    or height < 1
                    or width > MAX_ARTWORK_EDGE
                    or height > MAX_ARTWORK_EDGE
                    or width * height > MAX_ARTWORK_PIXELS
                    or getattr(image, "n_frames", 1) != 1
                ):
                    return None
                image.load()
                if image.width != width or image.height != height:
                    return None
                return extension
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            return None
