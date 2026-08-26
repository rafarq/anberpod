from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from anberpod.adapters.filesystem import AtomicFiles
from anberpod.domain.errors import HttpPolicyError
from anberpod.domain.models import HttpResponse
from anberpod.services.artwork import ArtworkCache


class RecordingTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls = []

    def request(self, policy, url, headers):  # type: ignore[no-untyped-def]
        self.calls.append((policy, url, headers))
        return self.response


def image_bytes(format: str = "PNG", size: tuple[int, int] = (320, 320)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "#6f35d5").save(output, format=format)
    return output.getvalue()


def test_artwork_cache_fetches_valid_https_image_with_bounded_policy_and_reuses_local_file(
    tmp_path: Path,
) -> None:
    transport = RecordingTransport(HttpResponse(200, {"Content-Type": "image/png"}, image_bytes()))
    cache = ArtworkCache(AtomicFiles(tmp_path), transport)
    url = "https://images.example.test/private-name.png?token=secret"

    cached = cache.ensure_cached(url, online=True)
    offline = cache.ensure_cached(url, online=False)

    assert cached is not None and cached == offline
    assert cached.is_file()
    assert cached.relative_to(tmp_path).parts[:2] == ("cache", "artwork")
    assert "private-name" not in cached.name and "secret" not in cached.name
    assert len(transport.calls) == 1
    policy, requested_url, headers = transport.calls[0]
    assert policy.connect_timeout_seconds <= 5
    assert policy.read_timeout_seconds <= 10
    assert policy.total_timeout_seconds is not None and policy.total_timeout_seconds <= 15
    assert policy.max_bytes <= 2 * 1024 * 1024
    assert requested_url == url
    assert headers == {"Accept": "image/png, image/jpeg, image/webp"}


def test_artwork_cache_rejects_unsafe_urls_invalid_formats_and_policy_failures(tmp_path: Path) -> None:
    class FailingTransport:
        def request(self, policy, url, headers):  # type: ignore[no-untyped-def]
            raise HttpPolicyError("target rejected", code="non_public_address")

    cache = ArtworkCache(AtomicFiles(tmp_path), FailingTransport())

    assert cache.ensure_cached("http://images.example.test/cover.png", online=True) is None
    assert cache.ensure_cached("https://user:secret@images.example.test/cover.png", online=True) is None
    assert cache.ensure_cached("https://127.0.0.1/cover.png", online=True) is None

    # Network policy failures are an ordinary placeholder condition, not a render failure.
    assert cache.ensure_cached("https://images.example.test/cover.png", online=True) is None


def test_artwork_cache_rejects_oversize_and_corrupt_images_and_removes_bad_local_entry(tmp_path: Path) -> None:
    output = BytesIO()
    Image.effect_noise((1600, 1600), 100).save(output, format="PNG")
    oversized = output.getvalue()
    assert len(oversized) > 2 * 1024 * 1024
    url = "https://images.example.test/large.png"
    cache = ArtworkCache(
        AtomicFiles(tmp_path),
        RecordingTransport(HttpResponse(200, {"Content-Type": "image/png"}, oversized)),
    )

    assert cache.ensure_cached(url, online=True) is None
    assert list((tmp_path / "cache" / "artwork").glob("*")) == []

    valid_transport = RecordingTransport(HttpResponse(200, {}, image_bytes()))
    valid_cache = ArtworkCache(AtomicFiles(tmp_path), valid_transport)
    cached = valid_cache.ensure_cached(url, online=True)
    assert cached is not None
    cached.write_bytes(b"corrupt cached bytes")

    assert valid_cache.ensure_cached(url, online=False) is None
    assert not cached.exists()
