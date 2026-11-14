import json
import shutil
import subprocess

import pytest

from app.services.download_service import (
    _convert_to_mp3,
    _convert_to_mp4,
)


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE,
    reason="FFmpeg and ffprobe are required for media integration checks.",
)


def _create_sample_video(path):
    subprocess.run(
        [
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-f", "lavfi",
            "-i", "color=c=black:s=64x64:r=25:d=1",
            "-f", "lavfi",
            "-i", "sine=frequency=1000:duration=1",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _probe(path):
    result = subprocess.run(
        [
            FFPROBE,
            "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,sample_rate",
            "-of", "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)["streams"]


def test_ffmpeg_remuxes_h264_aac_to_playable_mp4(tmp_path):
    source = tmp_path / "source.mkv"
    output = tmp_path / "output.mp4"
    _create_sample_video(source)

    _convert_to_mp4(source, output)

    assert output.is_file()
    streams = _probe(output)
    codecs = {stream["codec_name"] for stream in streams}
    assert {"h264", "aac"} <= codecs


def test_ffmpeg_extracts_mp3_with_requested_sample_rate(tmp_path):
    source = tmp_path / "source.mkv"
    output = tmp_path / "output.mp3"
    _create_sample_video(source)

    _convert_to_mp3(source, output)

    assert output.is_file()
    audio = next(
        stream for stream in _probe(output)
        if stream["codec_type"] == "audio"
    )
    assert audio["codec_name"] == "mp3"
    assert audio["sample_rate"] == "44100"
