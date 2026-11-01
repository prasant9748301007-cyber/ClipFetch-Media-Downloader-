
import pytest

from app.services.platform_detector import detect_platform


@pytest.mark.parametrize(
    "url, expected_platform",
    [
        (
            "https://www.tiktok.com/@user/video/123456",
            "TikTok",
        ),
        (
            "https://www.instagram.com/reel/ABC123/",
            "Instagram",
        ),
        (
            "https://www.facebook.com/watch/?v=123456",
            "Facebook",
        ),
        (
            "https://fb.watch/abc123/",
            "Facebook",
        ),
    ],
)
def test_valid_platform_urls(url, expected_platform):
    result = detect_platform(url)

    assert result["valid"] is True
    assert result["platform"] == expected_platform


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://www.tiktok.com/@user/video/123",
        "https://example.com/video",
        "https://instagram.com.example.org/reel/123",
        "https://www.youtube.com/watch?v=123",
    ],
)
def test_invalid_platform_urls(url):
    result = detect_platform(url)

    assert result["valid"] is False
    assert result["platform"] is None


def test_url_with_credentials_is_rejected():
    result = detect_platform(
        "https://user:password@www.tiktok.com/video/123"
    )

    assert result["valid"] is False


def test_url_without_media_path_is_rejected():
    result = detect_platform("https://www.tiktok.com/")

    assert result["valid"] is False
    assert result["platform"] == "TikTok"
