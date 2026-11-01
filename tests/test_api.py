
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import routes


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_validate_tiktok_url():
    response = client.post(
        "/api/validate-url",
        json={
            "url": "https://www.tiktok.com/@user/video/123456"
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["valid"] is True
    assert data["platform"] == "TikTok"


def test_validate_unsupported_url():
    response = client.post(
        "/api/validate-url",
        json={"url": "https://example.com/video"},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is False


def test_validate_empty_url():
    response = client.post(
        "/api/validate-url",
        json={"url": ""},
    )

    assert response.status_code == 422


def test_metadata_success(monkeypatch):
    async def fake_metadata(url):
        return {
            "platform": "TikTok",
            "title": "Test Video",
            "thumbnail": None,
            "duration": 30,
            "uploader": "Test User",
            "formats": [],
        }

    monkeypatch.setattr(
        routes,
        "get_media_metadata",
        fake_metadata,
    )

    response = client.post(
        "/api/metadata",
        json={
            "url": "https://www.tiktok.com/@user/video/123456"
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["success"] is True
    assert data["media"]["title"] == "Test Video"


def test_metadata_service_error(monkeypatch):
    async def fake_metadata(url):
        raise routes.MediaServiceError("Test metadata error")

    monkeypatch.setattr(
        routes,
        "get_media_metadata",
        fake_metadata,
    )

    response = client.post(
        "/api/metadata",
        json={
            "url": "https://www.tiktok.com/@user/video/123456"
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Test metadata error"
def test_download_mp3_success(monkeypatch, tmp_path):
    class FakeTemporaryDirectory:
        def cleanup(self):
            pass

    audio_file = tmp_path / "clipfetch_audio.mp3"
    audio_file.write_bytes(b"fake mp3 audio")

    async def fake_download(url, format_id):
        return audio_file, FakeTemporaryDirectory()

    monkeypatch.setattr(
        routes,
        "download_authorized_media",
        fake_download,
    )

    response = client.post(
        "/api/download",
        json={
            "url": "https://www.tiktok.com/@user/video/123456",
            "format_id": "audio_mp3",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert "clipfetch_audio.mp3" in response.headers["content-disposition"]
    assert response.content == b"fake mp3 audio"


def test_download_mp4_success(monkeypatch, tmp_path):
    class FakeTemporaryDirectory:
        def cleanup(self):
            pass

    video_file = tmp_path / "clipfetch_media.mp4"
    video_file.write_bytes(b"fake mp4 video")

    async def fake_download(url, format_id):
        return video_file, FakeTemporaryDirectory()

    monkeypatch.setattr(
        routes,
        "download_authorized_media",
        fake_download,
    )

    response = client.post(
        "/api/download",
        json={
            "url": "https://www.tiktok.com/@user/video/123456",
            "format_id": "best",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert "clipfetch_media.mp4" in response.headers["content-disposition"]
    assert response.content == b"fake mp4 video"


def test_download_service_error(monkeypatch):
    async def fake_download(url, format_id):
        raise routes.DownloadServiceError("Test download error")

    monkeypatch.setattr(
        routes,
        "download_authorized_media",
        fake_download,
    )

    response = client.post(
        "/api/download",
        json={
            "url": "https://www.tiktok.com/@user/video/123456",
            "format_id": "audio_mp3",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Test download error"
