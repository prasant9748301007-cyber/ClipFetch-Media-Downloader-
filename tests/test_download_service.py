
from pathlib import Path

import pytest

from app.services import download_service as service


class FakeYoutubeDL:
    """Simulate yt-dlp without downloading real media."""

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def download(self, urls):
        output_template = self.options["outtmpl"]
        source_path = Path(
            output_template.replace("%(ext)s", "webm")
        )
        source_path.write_bytes(b"fake source media")


def test_audio_download_creates_mp3(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service.yt_dlp,
        "YoutubeDL",
        FakeYoutubeDL,
    )

    def fake_mp3_conversion(input_path, output_path):
        output_path.write_bytes(b"fake mp3 audio")

    monkeypatch.setattr(
        service,
        "_convert_to_mp3",
        fake_mp3_conversion,
    )

    result = service._download_media(
        "https://www.tiktok.com/@user/video/123456",
        "audio_mp3",
        tmp_path,
    )

    assert result.name == "clipfetch_audio.mp3"
    assert result.is_file()
    assert result.read_bytes() == b"fake mp3 audio"


def test_video_download_creates_mp4(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service.yt_dlp,
        "YoutubeDL",
        FakeYoutubeDL,
    )

    monkeypatch.setattr(
        service,
        "_get_format_selector",
        lambda url, format_id: "bestvideo+bestaudio/best",
    )

    def fake_mp4_conversion(input_path, output_path):
        output_path.write_bytes(b"fake mp4 video")

    monkeypatch.setattr(
        service,
        "_convert_to_mp4",
        fake_mp4_conversion,
    )

    result = service._download_media(
        "https://www.tiktok.com/@user/video/123456",
        "best",
        tmp_path,
    )

    assert result.name == "clipfetch_media.mp4"
    assert result.is_file()
    assert result.read_bytes() == b"fake mp4 video"


def test_download_rejects_oversized_source(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service.yt_dlp,
        "YoutubeDL",
        FakeYoutubeDL,
    )

    monkeypatch.setattr(
        service,
        "MAX_FILE_SIZE",
        5,
    )

    with pytest.raises(
        service.DownloadServiceError,
        match="size limit",
    ):
        service._download_media(
            "https://www.tiktok.com/@user/video/123456",
            "audio_mp3",
            tmp_path,
        )


def test_audio_conversion_error_is_propagated(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        service.yt_dlp,
        "YoutubeDL",
        FakeYoutubeDL,
    )

    def failed_conversion(input_path, output_path):
        raise service.DownloadServiceError(
            "Audio conversion failed."
        )

    monkeypatch.setattr(
        service,
        "_convert_to_mp3",
        failed_conversion,
    )

    with pytest.raises(
        service.DownloadServiceError,
        match="Audio conversion failed",
    ):
        service._download_media(
            "https://www.tiktok.com/@user/video/123456",
            "audio_mp3",
            tmp_path,
        )


def test_mp4_uses_stream_copy_for_h264_aac(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    input_path = tmp_path / "source.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"fake source")

    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)

        if command[0].endswith("ffprobe"):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "streams": [
                        {"codec_type": "video", "codec_name": "h264"},
                        {"codec_type": "audio", "codec_name": "aac"},
                    ]
                }),
                stderr="",
            )

        output_path.write_bytes(b"fake mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    service._convert_to_mp4(input_path, output_path)

    assert output_path.is_file()
    assert output_path.read_bytes() == b"fake mp4"
    assert any("-c" in command and "copy" in command for command in commands)
    assert not any("libx264" in command for command in commands)


def test_mp4_falls_back_to_encoding_for_incompatible_codecs(
    monkeypatch,
    tmp_path,
):
    import json
    from types import SimpleNamespace

    input_path = tmp_path / "source.webm"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"fake source")

    monkeypatch.setattr(
        service.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)

        if command[0].endswith("ffprobe"):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "streams": [
                        {"codec_type": "video", "codec_name": "vp9"},
                        {"codec_type": "audio", "codec_name": "opus"},
                    ]
                }),
                stderr="",
            )

        output_path.write_bytes(b"converted mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    service._convert_to_mp4(input_path, output_path)

    assert output_path.is_file()
    assert output_path.read_bytes() == b"converted mp4"
    assert any("libx264" in command for command in commands)
    assert any("aac" in command for command in commands)
