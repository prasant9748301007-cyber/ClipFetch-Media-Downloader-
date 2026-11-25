import pytest

from app.services import media_service as service


TEST_URL = "https://www.tiktok.com/@user/video/123456"


def test_metadata_uses_configured_proxy(monkeypatch):
    instances = []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {
                "title": "clip",
                "duration": 3,
                "formats": [],
            }

    monkeypatch.setattr(service.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(service, "validate_public_host", lambda url: ["8.8.8.8"])
    monkeypatch.setattr(
        service,
        "get_egress_proxy_options",
        lambda: {"proxy": "http://clipfetch-proxy-abc123:3128"},
    )

    service._extract_metadata(TEST_URL)
    assert instances[0].options["proxy"] == "http://clipfetch-proxy-abc123:3128"


def test_metadata_fails_closed_if_proxy_is_missing(monkeypatch):
    monkeypatch.delenv("CLIPFETCH_EGRESS_PROXY", raising=False)
    monkeypatch.delenv("CLIPFETCH_REQUIRE_EGRESS_PROXY", raising=False)
    monkeypatch.setattr(service, "validate_public_host", lambda url: pytest.fail("DNS must not run"))
    monkeypatch.setattr(
        service.yt_dlp,
        "YoutubeDL",
        lambda options: pytest.fail("extractor must not run"),
    )

    with pytest.raises(service.MediaServiceError, match="outbound proxy"):
        service._extract_metadata(TEST_URL)


def test_proxy_failure_does_not_retry_metadata_directly(monkeypatch):
    calls = []

    class FailedProxy:
        def __init__(self, options):
            calls.append(options["proxy"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            raise OSError("proxy unavailable")

    monkeypatch.setattr(service.yt_dlp, "YoutubeDL", FailedProxy)
    monkeypatch.setattr(service, "validate_public_host", lambda url: ["8.8.8.8"])
    monkeypatch.setattr(
        service,
        "get_egress_proxy_options",
        lambda: {"proxy": "http://clipfetch-proxy-abc123:3128"},
    )

    with pytest.raises(service.MediaServiceError):
        service._extract_metadata(TEST_URL)
    assert calls == ["http://clipfetch-proxy-abc123:3128"]


def test_metadata_keeps_highest_quality_formats_and_tbr(monkeypatch):
    class ManyFormatsYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {
                "title": "clip",
                "duration": 10,
                "formats": [
                    {
                        "format_id": f"fmt_{i}",
                        "ext": "mp4",
                        "resolution": f"{360 + i * 10}x{640 + i * 10}",
                        "tbr": 100 + i,
                        "vcodec": "h264",
                        "acodec": "aac",
                    }
                    for i in range(service.MAX_FORMATS + 5)
                ],
            }

    monkeypatch.setattr(service.yt_dlp, "YoutubeDL", ManyFormatsYoutubeDL)
    monkeypatch.setattr(service, "validate_public_host", lambda url: ["8.8.8.8"])
    monkeypatch.setattr(service, "get_egress_proxy_options", lambda: {})

    metadata = service._extract_metadata(TEST_URL)
    format_ids = [fmt["format_id"] for fmt in metadata["formats"]]
    assert len(format_ids) == service.MAX_FORMATS
    assert f"fmt_{service.MAX_FORMATS + 4}" in format_ids
    assert metadata["formats"][-1]["tbr"] == 100 + service.MAX_FORMATS + 4


def test_metadata_rejects_live_streams(monkeypatch):
    class LiveYoutubeDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {"title": "live", "is_live": True, "formats": []}

    monkeypatch.setattr(service.yt_dlp, "YoutubeDL", LiveYoutubeDL)
    monkeypatch.setattr(service, "validate_public_host", lambda url: ["8.8.8.8"])
    monkeypatch.setattr(service, "get_egress_proxy_options", lambda: {})

    with pytest.raises(service.MediaServiceError, match="Live streams"):
        service._extract_metadata(TEST_URL)
