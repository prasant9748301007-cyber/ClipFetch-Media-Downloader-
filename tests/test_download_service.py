
from pathlib import Path

import pytest

from app.services import download_service as service


class FakeYoutubeDL:
    """Simulate yt-dlp without downloading real media."""

    instances = []

    def __init__(self, options):
        self.options = options
        self.params = dict(options)
        self.extract_count = 0
        self.process_count = 0
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def extract_info(self, url, download=False, process=True):
        self.extract_count += 1
        return {
            "_type": "video",
            "formats": [
                {
                    "format_id": "720p",
                    "vcodec": "h264",
                    "acodec": "none",
                }
            ],
        }

    def process_ie_result(self, info, download=True):
        self.process_count += 1
        assert download is True
        assert self.params["format"] == (
            "bestvideo[vcodec~='^(avc1|h264)']"
            "+bestaudio[acodec~='^(mp4a|aac)']/"
            "best[vcodec~='^(avc1|h264)'][acodec~='^(mp4a|aac)']/"
            "bestvideo+bestaudio/best"
        )
        self._write_source_file()

    def download(self, urls):
        self._write_source_file()

    def _write_source_file(self):
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
        "validate_public_host",
        lambda url: None,
    )
    monkeypatch.setattr(
        service,
        "get_egress_proxy_options",
        lambda: {},
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

    # The video URL is extracted once, then the result is processed directly.
    fake_ydl = FakeYoutubeDL.instances[-1]
    assert fake_ydl.extract_count == 1
    assert fake_ydl.process_count == 1


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


def test_best_quality_prefers_h264_aac(monkeypatch):
    real_youtube_dl = service.yt_dlp.YoutubeDL

    monkeypatch.setattr(
        service,
        "validate_public_host",
        lambda url: None,
    )
    monkeypatch.setattr(
        service,
        "get_egress_proxy_options",
        lambda: {},
    )
    monkeypatch.setattr(
        service,
        "detect_platform",
        lambda url: {"platform": "Instagram"},
    )

    class FakeMetadataYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def extract_info(self, url, download=False):
            return {
                "formats": [
                    {
                        "format_id": "avc1",
                        "vcodec": "avc1.640028",
                        "acodec": "none",
                    },
                    {
                        "format_id": "aac",
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                    },
                ]
            }

    monkeypatch.setattr(
        service.yt_dlp,
        "YoutubeDL",
        FakeMetadataYoutubeDL,
    )

    info = {
        "_type": "video",
        "formats": [
            {
                "format_id": "avc1",
                "vcodec": "avc1.640028",
                "acodec": "none",
            },
            {
                "format_id": "aac",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
            },
        ],
    }

    selector = service._get_format_selector(info, "best")

    assert "bestvideo[vcodec~=" in selector
    assert "bestaudio[acodec~=" in selector
    assert "bestvideo+bestaudio/best" in selector

    with real_youtube_dl({"quiet": True}) as ydl:
        ydl.build_format_selector(selector)


def test_selected_video_format_is_validated_from_existing_info():
    info = {
        "_type": "video",
        "formats": [
            {
                "format_id": "720p",
                "vcodec": "avc1",
                "acodec": "none",
            },
            {
                "format_id": "audio",
                "vcodec": "none",
                "acodec": "mp4a",
            },
        ],
    }

    assert service._get_format_selector(info, "720p") == (
        "720p+bestaudio/best"
    )

    with pytest.raises(
        service.DownloadServiceError,
        match="does not contain a video",
    ):
        service._get_format_selector(info, "audio")

    with pytest.raises(
        service.DownloadServiceError,
        match="no longer available",
    ):
        service._get_format_selector(info, "missing")


def test_mp4_copies_h264_video_when_audio_needs_transcoding(
    monkeypatch,
    tmp_path,
):
    import json
    from types import SimpleNamespace

    input_path = tmp_path / "source.mkv"
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
                        {"codec_type": "audio", "codec_name": "opus"},
                    ]
                }),
                stderr="",
            )

        output_path.write_bytes(b"selectively converted mp4")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    service._convert_to_mp4(input_path, output_path)

    ffmpeg_command = next(
        command for command in commands
        if command[0].endswith("ffmpeg")
    )
    assert output_path.is_file()
    assert ffmpeg_command[ffmpeg_command.index("-c:v") + 1] == "copy"
    assert ffmpeg_command[ffmpeg_command.index("-c:a") + 1] == "aac"
    assert "libx264" not in ffmpeg_command


def test_download_removes_source_after_conversion(monkeypatch, tmp_path):
    monkeypatch.setattr(service.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        service,
        "validate_public_host",
        lambda url: None,
    )
    monkeypatch.setattr(
        service,
        "get_egress_proxy_options",
        lambda: {},
    )
    monkeypatch.setattr(
        service,
        "_convert_to_mp4",
        lambda source, output: output.write_bytes(b"fake mp4"),
    )

    result = service._download_media(
        "https://www.tiktok.com/@user/video/123456",
        "best",
        tmp_path,
    )

    assert result.is_file()
    assert not (tmp_path / "clipfetch_source.webm").exists()



def test_empty_temp_directory_setting_uses_system_temp(monkeypatch):
    monkeypatch.setenv("CLIPFETCH_TEMP_DIR", "")

    assert service._get_download_dir() == (
        Path(service.tempfile.gettempdir()) / "clipfetch-downloads"
    )


def test_temp_directory_setting_must_be_absolute():
    with pytest.raises(ValueError, match="absolute path"):
        service._get_download_dir("relative/temp")
