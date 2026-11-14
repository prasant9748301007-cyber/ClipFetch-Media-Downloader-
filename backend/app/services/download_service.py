import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp

from yt_dlp.networking.impersonate import ImpersonateTarget
from app.services.platform_detector import detect_platform
from app.services.egress_proxy import get_egress_proxy_options
from app.services.network_security import (
    NetworkSecurityError,
    validate_public_host,
)
from app.services.retry_policy import get_retry_policy
logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 180
FFMPEG_TIMEOUT = 90
MAX_URL_LENGTH = 2048
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_CONCURRENT_DOWNLOADS = 2
DOWNLOAD_QUEUE_TIMEOUT = 5

_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(
    MAX_CONCURRENT_DOWNLOADS
)

DOWNLOAD_DIR = Path(
    os.getenv(
        "CLIPFETCH_TEMP_DIR",
        str(Path(tempfile.gettempdir()) / "clipfetch-downloads"),
    )
).expanduser()


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
    info: dict,
    format_id: str,
) -> str:
    """Select a format from metadata already extracted by the download worker."""

    if not info or info.get("_type") in {"playlist", "multi_video"}:
        raise DownloadServiceError(
            "This media is unavailable or playlist URLs are not supported."
        )

    if format_id == "best":
        return (
            "bestvideo[vcodec~='^(avc1|h264)']"
            "+bestaudio[acodec~='^(mp4a|aac)']/"
            "best[vcodec~='^(avc1|h264)'][acodec~='^(mp4a|aac)']/"
            "bestvideo+bestaudio/best"
        )

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

    return f"{format_id}+bestaudio/best"

def _convert_to_mp4(input_path: Path, output_path: Path) -> None:
    """Create a compatible MP4, copying compatible streams when possible."""

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    if not ffmpeg_path:
        raise DownloadServiceError(
            "FFmpeg is not installed or is not available in PATH."
        )

    def valid_output() -> bool:
        return output_path.is_file() and output_path.stat().st_size > 0

    copy_video = False
    copy_audio = False

    if ffprobe_path:
        probe_command = [
            ffprobe_path,
            "-v", "error",
            "-show_entries", "stream=codec_type,codec_name",
            "-of", "json",
            str(input_path),
        ]

        try:
            probe = subprocess.run(
                probe_command,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

            if probe.returncode == 0:
                import json

                streams = json.loads(probe.stdout).get("streams", [])
                video_codecs = [
                    stream.get("codec_name")
                    for stream in streams
                    if stream.get("codec_type") == "video"
                ]
                audio_codecs = [
                    stream.get("codec_name")
                    for stream in streams
                    if stream.get("codec_type") == "audio"
                ]

                copy_video = bool(video_codecs) and all(
                    codec == "h264" for codec in video_codecs
                )
                # An absent audio stream is compatible with the optional map.
                copy_audio = all(codec == "aac" for codec in audio_codecs)

                logger.info(
                    "ClipFetch codec diagnostic | video=%s | audio=%s "
                    "| stream_copy_video=%s | stream_copy_audio=%s",
                    video_codecs,
                    audio_codecs,
                    copy_video,
                    copy_audio,
                )

        except (
            subprocess.TimeoutExpired,
            OSError,
            ValueError,
            TypeError,
        ):
            logger.warning(
                "Could not inspect source codecs; using H.264/AAC conversion."
            )

    # Remux already-compatible streams without decoding them.
    if copy_video and copy_audio:
        copy_command = [
            ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(input_path),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c", "copy",
            "-movflags", "+faststart",
            "-fs", str(MAX_FILE_SIZE),
            "-f", "mp4",
            str(output_path),
        ]

        try:
            result = subprocess.run(
                copy_command,
                capture_output=True,
                text=True,
                timeout=FFMPEG_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output_path.unlink(missing_ok=True)
            raise DownloadServiceError(
                "Video remuxing took too long. Please try again."
            ) from exc

        if result.returncode == 0 and valid_output():
            logger.info("MP4 created using fast stream-copy.")
            return

        logger.warning(
            "MP4 stream-copy failed; falling back to encoding: %s",
            result.stderr[-1500:],
        )
        output_path.unlink(missing_ok=True)
        # A failed remux can indicate a container/bitstream issue; force the
        # compatibility path rather than repeating the same copy operation.
        copy_video = False
        copy_audio = False

    def build_encode_command(
        copy_video_stream: bool,
        copy_audio_stream: bool,
    ) -> list[str]:
        command = [
            ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(input_path),
            "-map", "0:v:0",
            "-map", "0:a:0?",
        ]

        if copy_video_stream:
            command.extend(["-c:v", "copy"])
        else:
            command.extend([
                "-threads:v", "1",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-profile:v", "main",
            ])

        if copy_audio_stream:
            command.extend(["-c:a", "copy"])
        else:
            command.extend([
                "-threads:a", "1",
                "-c:a", "aac",
                "-b:a", "128k",
            ])

        command.extend([
            "-movflags", "+faststart",
            "-max_muxing_queue_size", "1024",
            "-fs", str(MAX_FILE_SIZE),
            str(output_path),
        ])
        return command

    def run_encoding(
        copy_video_stream: bool,
        copy_audio_stream: bool,
    ):
        try:
            return subprocess.run(
                build_encode_command(
                    copy_video_stream,
                    copy_audio_stream,
                ),
                capture_output=True,
                text=True,
                timeout=FFMPEG_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output_path.unlink(missing_ok=True)
            raise DownloadServiceError(
                "Video conversion took too long. Please try again."
            ) from exc

    result = run_encoding(copy_video, copy_audio)

    # If a selective copy fails, retry once with fully compatible codecs.
    if result.returncode != 0 and (copy_video or copy_audio):
        logger.warning(
            "Selective MP4 stream processing failed; retrying with full encoding."
        )
        output_path.unlink(missing_ok=True)
        result = run_encoding(False, False)

    if result.returncode != 0:
        logger.warning(
            "FFmpeg conversion failed: %s",
            result.stderr[-1500:],
        )
        output_path.unlink(missing_ok=True)
        raise DownloadServiceError(
            "Video conversion failed. The source may not contain "
            "a supported video stream."
        )

    if not valid_output():
        raise DownloadServiceError(
            "FFmpeg did not produce a valid MP4 file."
        )

def _convert_to_mp3(input_path: Path, output_path: Path) -> None:
    """Convert an audio stream to MP3."""

    ffmpeg_path = shutil.which("ffmpeg")

    if not ffmpeg_path:
        raise DownloadServiceError(
            "FFmpeg is not installed or is not available in PATH."
        )

    command = [
        ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(input_path),
        "-threads:a", "1",
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-ar", "44100",
        "-fs", str(MAX_FILE_SIZE),
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
            output_path.unlink(missing_ok=True)
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
        output_path.unlink(missing_ok=True)
        raise DownloadServiceError(
            "Audio conversion took too long. Please try again."
        ) from exc
def _check_download_size(progress, output_dir: Path) -> None:
    """Stop downloads when their combined on-disk size exceeds 100 MB."""

    if progress.get("status") != "downloading":
        return

    downloaded_bytes = progress.get("downloaded_bytes", 0)

    if downloaded_bytes and downloaded_bytes > MAX_FILE_SIZE:
        raise DownloadServiceError(
            "The download exceeds the 100 MB size limit."
        )

    total_bytes = (
        progress.get("total_bytes")
        or progress.get("total_bytes_estimate")
    )

    if total_bytes and total_bytes > MAX_FILE_SIZE:
        raise DownloadServiceError(
            "The estimated download exceeds the 100 MB size limit."
        )

    current_size = sum(
        item.stat().st_size
        for item in output_dir.rglob("*")
        if item.is_file()
    )

    if current_size > MAX_FILE_SIZE:
        raise DownloadServiceError(
            "The combined download size exceeds the 100 MB limit."
        )
def _download_media(
    url: str,
    format_id: str,
    output_dir: Path,
) -> Path:
    """Download selected media and convert it to MP4 or MP3."""

    is_audio = format_id == "audio_mp3"

    options = {
        "format": "bestaudio/best" if is_audio else "best",
        "outtmpl": str(output_dir / "clipfetch_source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 10,
        **get_retry_policy(
            retries=2,
            fragment_retries=1,
            extractor_retries=1,
            file_access_retries=1,
        ),
        "max_filesize": MAX_FILE_SIZE,
        "progress_hooks": [
            lambda progress: _check_download_size(progress, output_dir)
        ],
        "overwrites": True,
    }

    if detect_platform(url).get("platform") == "TikTok":
        options["impersonate"] = ImpersonateTarget.from_str(
            "chrome-136"
        )

    try:
        # Configure outbound proxy.
        try:
            options.update(get_egress_proxy_options())
        except NetworkSecurityError as exc:
            raise DownloadServiceError(
                "Outbound proxy configuration is invalid."
            ) from exc

        # Recheck the submitted hostname before the actual download.
        validate_public_host(url)

        with yt_dlp.YoutubeDL(options) as ydl:
            if is_audio:
                download_started = time.perf_counter()
                ydl.download([url])
            else:
                metadata_started = time.perf_counter()
                info = ydl.extract_info(
                    url,
                    download=False,
                    process=False,
                )
                logger.warning(
                    "ClipFetch timing | metadata extraction: %.2f seconds",
                    time.perf_counter() - metadata_started,
                )

                # Reuse the extracted result instead of asking yt-dlp to
                # extract the same URL again during the download.
                ydl.params["format"] = _get_format_selector(
                    info,
                    format_id,
                )

                download_started = time.perf_counter()
                ydl.process_ie_result(info, download=True)

        logger.warning(
            "ClipFetch timing | yt-dlp download: %.2f seconds",
            time.perf_counter() - download_started,
        )

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

        conversion_started = time.perf_counter()

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

        # Keep only the final response file while it is streamed to the user.
        try:
            source_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove temporary media source file.")

        logger.warning(
            "ClipFetch timing | media conversion: %.2f seconds",
            time.perf_counter() - conversion_started,
        )

        return final_path

    except DownloadServiceError:
        raise

    except NetworkSecurityError as exc:
        logger.warning(
            "Download blocked by network security (%s).",
            type(exc).__name__,
        )
        raise DownloadServiceError(
            "This URL resolves to a non-public or unsafe network address."
        ) from exc

    except Exception as exc:
        logger.warning(
            "Media download failed: %s",
            type(exc).__name__,
        )
        raise DownloadServiceError(
            "Download failed. The media may be unavailable, "
            "unsupported, or restricted."
        ) from exc
def _create_download_worker(url, format_id, output_dir):
    """Create the background download worker safely."""

    worker_coroutine = asyncio.to_thread(
        _download_media,
        url,
        format_id,
        output_dir,
    )

    try:
        return asyncio.create_task(worker_coroutine)
    except BaseException:
        worker_coroutine.close()
        raise
def _create_temporary_directory():
    """Create an isolated temporary directory for a download."""

    from tempfile import TemporaryDirectory

    DOWNLOAD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    return TemporaryDirectory(
        prefix="clipfetch_",
        dir=DOWNLOAD_DIR,
    )
async def download_authorized_media(
    url: str,
    format_id: str,
):
    """Validate and download authorized media with resource limits."""

    _validate_url(url)

    try:
        await asyncio.wait_for(
            _DOWNLOAD_SEMAPHORE.acquire(),
            timeout=DOWNLOAD_QUEUE_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise DownloadServiceError(
            "The server is busy. Please try again in a moment."
        ) from exc

    temporary_directory = None

    try:
        # Setup resources after acquiring the semaphore.
        temporary_directory = _create_temporary_directory()

        output_dir = Path(temporary_directory.name)

        worker = _create_download_worker(
            url,
            format_id,
            output_dir,
        )

    except BaseException:
        try:
            if temporary_directory is not None:
                temporary_directory.cleanup()
        finally:
            _DOWNLOAD_SEMAPHORE.release()

        raise

    def cleanup_after_worker(finished_task):
        """Cleanup only after the background worker finishes."""

        try:
            finished_task.result()
        except BaseException:
            pass
        finally:
            try:
                temporary_directory.cleanup()
            finally:
                _DOWNLOAD_SEMAPHORE.release()

    try:
        file_path = await asyncio.wait_for(
            asyncio.shield(worker),
            timeout=DOWNLOAD_TIMEOUT,
        )

        _DOWNLOAD_SEMAPHORE.release()

        return file_path, temporary_directory

    except asyncio.TimeoutError as exc:
        worker.add_done_callback(cleanup_after_worker)

        raise DownloadServiceError(
            "Download timed out. Please try again later."
        ) from exc

    except asyncio.CancelledError:
        worker.add_done_callback(cleanup_after_worker)
        raise

    except Exception:
        try:
            temporary_directory.cleanup()
        finally:
            _DOWNLOAD_SEMAPHORE.release()

        raise
