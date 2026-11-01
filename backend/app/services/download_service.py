import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp

from yt_dlp.networking.impersonate import ImpersonateTarget
from app.services.platform_detector import detect_platform


logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 180
FFMPEG_TIMEOUT = 90
MAX_URL_LENGTH = 2048
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "clipfetch-downloads"


class DownloadServiceError(Exception):
    """Raised when a media download or conversion fails."""


def _validate_url(url: str) -> None:
    """Validate supported HTTPS media URLs."""

    if not isinstance(url, str) or not url.strip():
        raise DownloadServiceError("Please provide a media URL.")

    if len(url) > MAX_URL_LENGTH:
        raise DownloadServiceError("The URL is too long.")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise DownloadServiceError("Invalid media URL.") from exc

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise DownloadServiceError("This URL is not permitted.")

    result = detect_platform(url)

    if not result.get("valid"):
        raise DownloadServiceError(
            "This media URL is not supported."
        )


def _get_format_selector(
    url: str,
    format_id: str,
) -> str:
    """Select the requested video quality and include audio when available."""

    info_options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 10,
        "retries": 0,
        "extractor_retries": 0,
    }
    if detect_platform(url).get("platform") == "TikTok":
        info_options["impersonate"] = ImpersonateTarget.from_str("chrome-136")
    with yt_dlp.YoutubeDL(info_options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info or info.get("_type") in {"playlist", "multi_video"}:
        raise DownloadServiceError(
            "This media is unavailable or playlist URLs are not supported."
        )

    if format_id == "best":
        return "bestvideo+bestaudio/best"

    selected = next(
        (
            item
            for item in info.get("formats", [])
            if str(item.get("format_id")) == format_id
        ),
        None,
    )

    if not selected:
        raise DownloadServiceError(
            "The selected video quality is no longer available. "
            "Please fetch the media again."
        )

    has_video = selected.get("vcodec") not in (None, "none")
    has_audio = selected.get("acodec") not in (None, "none")

    if not has_video:
        raise DownloadServiceError(
            "The selected format does not contain a video stream."
        )

    if has_audio:
        return format_id

    # Video-only format: combine it with the best available audio.
    return f"{format_id}+bestaudio/best"


def _convert_to_mp4(input_path: Path, output_path: Path) -> None:
    """Convert media to a broadly compatible H.264/AAC MP4 file."""

    ffmpeg_path = shutil.which("ffmpeg")

    if not ffmpeg_path:
        raise DownloadServiceError(
            "FFmpeg is not installed or is not available in PATH."
        )

    command = [
        ffmpeg_path,
        "-y",
        "-i", str(input_path),

        # Select the first video stream and optional first audio stream.
        "-map", "0:v:0",
        "-map", "0:a:0?",

        # Broadly compatible video and audio codecs.
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-profile:v", "main",

        "-c:a", "aac",
        "-b:a", "128k",

        # Improve playback compatibility.
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "1024",

        str(output_path),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT,
            check=False,
        )

        if result.returncode != 0:
            logger.warning(
                "FFmpeg conversion failed: %s",
                result.stderr[-1500:],
            )
            raise DownloadServiceError(
                "Video conversion failed. The source may not contain "
                "a supported video stream."
            )

        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise DownloadServiceError(
                "FFmpeg did not produce a valid MP4 file."
            )

    except subprocess.TimeoutExpired as exc:
        raise DownloadServiceError(
            "Video conversion took too long. Please try again."
        ) from exc

def _convert_to_mp3(input_path: Path, output_path: Path) -> None:
    """Convert an audio stream to MP3."""

    ffmpeg_path = shutil.which("ffmpeg")

    if not ffmpeg_path:
        raise DownloadServiceError(
            "FFmpeg is not installed or is not available in PATH."
        )

    command = [
        ffmpeg_path,
        "-y",
        "-i", str(input_path),
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-ar", "44100",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT,
            check=False,
        )

        if result.returncode != 0:
            logger.warning(
                "FFmpeg MP3 conversion failed: %s",
                result.stderr[-1500:],
            )
            raise DownloadServiceError(
                "Audio conversion failed. The source may not contain audio."
            )

        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise DownloadServiceError(
                "FFmpeg did not produce a valid MP3 file."
            )

    except subprocess.TimeoutExpired as exc:
        raise DownloadServiceError(
            "Audio conversion took too long. Please try again."
        ) from exc

def _download_media(
    url: str,
    format_id: str,
    output_dir: Path,
) -> Path:
    """Download selected media and convert it to MP4 or MP3."""

    is_audio = format_id == "audio_mp3"

    format_selector = (
        "bestaudio/best"
        if is_audio
        else _get_format_selector(url, format_id)
    )

    options = {
        "format": format_selector,
        "outtmpl": str(output_dir / "clipfetch_source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 10,
        "retries": 0,
        "extractor_retries": 0,
        "max_filesize": MAX_FILE_SIZE,
        "overwrites": True,
    }
    if detect_platform(url).get("platform") == "TikTok":
       options["impersonate"] = ImpersonateTarget.from_str("chrome-136")
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])

        candidates = [
            item
            for item in output_dir.iterdir()
            if item.is_file()
            and item.name.startswith("clipfetch_source.")
            and item.suffix not in {".part", ".ytdl"}
        ]

        if len(candidates) != 1:
            raise DownloadServiceError(
                "The download did not produce one complete media file."
            )

        source_path = candidates[0]

        if source_path.stat().st_size > MAX_FILE_SIZE:
            raise DownloadServiceError(
                "The downloaded file exceeds the size limit."
            )

        if is_audio:
            final_path = output_dir / "clipfetch_audio.mp3"
            converted_path = output_dir / "clipfetch_converted.mp3"

            _convert_to_mp3(source_path, converted_path)

            if converted_path.stat().st_size > MAX_FILE_SIZE:
                raise DownloadServiceError(
                    "The converted audio exceeds the 100 MB size limit."
                )

            converted_path.replace(final_path)

        else:
            final_path = output_dir / "clipfetch_media.mp4"
            converted_path = output_dir / "clipfetch_converted.mp4"

            _convert_to_mp4(source_path, converted_path)

            if converted_path.stat().st_size > MAX_FILE_SIZE:
                raise DownloadServiceError(
                    "The converted video exceeds the 100 MB size limit."
                )

            converted_path.replace(final_path)

        return final_path

    except DownloadServiceError:
        raise

    except Exception as exc:
        logger.warning(
            "Media download failed: %s",
            type(exc).__name__,
        )
        raise DownloadServiceError(
            "Download failed. The media may be unavailable, "
            "unsupported, or restricted."
        ) from exc
async def download_authorized_media(
    url: str,
    format_id: str,
):
    """Validate and download authorized media."""

    from tempfile import TemporaryDirectory

    _validate_url(url)
    temporary_directory = TemporaryDirectory(
        prefix="clipfetch_"
    )

    output_dir = Path(temporary_directory.name)

    try:
        file_path = await asyncio.to_thread(
            _download_media,
            url,
            format_id,
            output_dir,
        )

        return file_path, temporary_directory

    except Exception:
        temporary_directory.cleanup()
        raise
