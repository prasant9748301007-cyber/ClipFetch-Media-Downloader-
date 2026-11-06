
import asyncio
import logging
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

from app.services.platform_detector import detect_platform


logger = logging.getLogger(__name__)

# Metadata resource limits.
MAX_URL_LENGTH = 2048
METADATA_TIMEOUT = 60
SOCKET_TIMEOUT = 10
MAX_FORMATS = 20

MAX_CONCURRENT_METADATA = 3
METADATA_QUEUE_TIMEOUT = 5

_METADATA_SEMAPHORE = asyncio.Semaphore(
    MAX_CONCURRENT_METADATA
)


class MediaServiceError(Exception):
    """Raised when metadata cannot be retrieved safely."""


def _extract_metadata(url: str) -> dict:
    """
    Extract metadata without downloading the media.

    This function must only be used for URLs that pass
    application validation and safety checks.
    """

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 0,
        "extractor_retries": 0,
        "ignoreerrors": False,
    }

    # TikTok requires browser impersonation.
    if detect_platform(url).get("platform") == "TikTok":
        options["impersonate"] = ImpersonateTarget.from_str(
            "chrome-136"
        )

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise MediaServiceError(
                "No media information was returned."
            )

        if info.get("_type") in {"playlist", "multi_video"}:
            raise MediaServiceError(
                "Playlist URLs are not supported."
            )

        formats = []

        for item in info.get("formats", [])[:MAX_FORMATS]:
            formats.append({
                "format_id": item.get("format_id"),
                "extension": item.get("ext"),
                "resolution": item.get("resolution"),
                "filesize": (
                    item.get("filesize")
                    or item.get("filesize_approx")
                ),
                "has_video": item.get("vcodec") not in (
                    None,
                    "none",
                ),
                "has_audio": item.get("acodec") not in (
                    None,
                    "none",
                ),
            })

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
            "formats": formats,
        }

    except MediaServiceError:
        raise

    except Exception as exc:
        logger.warning(
            "Metadata extraction failed (%s).",
            type(exc).__name__,
        )

        raise MediaServiceError(
            "Metadata could not be retrieved. The media may be "
            "unavailable, unsupported, or restricted by the platform."
        ) from exc

def _create_metadata_worker(url):
    """Create the background metadata worker safely."""

    worker_coroutine = asyncio.to_thread(
        _extract_metadata,
        url,
    )

    try:
        return asyncio.create_task(worker_coroutine)
    except BaseException:
        worker_coroutine.close()
        raise

async def get_media_metadata(url: str) -> dict:
    """
    Validate URL and retrieve metadata with resource limits.
    """

    if not isinstance(url, str) or not url.strip():
        raise MediaServiceError("Please provide a URL.")

    url = url.strip()

    if len(url) > MAX_URL_LENGTH:
        raise MediaServiceError("URL is too long.")

    detection = detect_platform(url)

    if not detection["valid"]:
        raise MediaServiceError(detection["message"])

    parsed = urlsplit(url)

    if parsed.scheme != "https" or not parsed.hostname:
        raise MediaServiceError("Only valid HTTPS URLs are supported.")

    if parsed.username is not None or parsed.password is not None:
        raise MediaServiceError(
            "URLs containing credentials are not allowed."
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise MediaServiceError("Invalid URL port.") from exc

    if port not in (None, 443):
        raise MediaServiceError("Custom URL ports are not supported.")

    try:
        await asyncio.wait_for(
            _METADATA_SEMAPHORE.acquire(),
            timeout=METADATA_QUEUE_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise MediaServiceError(
            "The metadata service is busy. Please try again shortly."
        ) from exc

    try:
        worker = _create_metadata_worker(url)
    except BaseException:
        _METADATA_SEMAPHORE.release()
        raise

    def release_after_worker(finished_task):
        try:
            finished_task.result()
        except BaseException:
            pass
        finally:
            _METADATA_SEMAPHORE.release()

    try:
        metadata = await asyncio.wait_for(
            asyncio.shield(worker),
            timeout=METADATA_TIMEOUT,
        )
        _METADATA_SEMAPHORE.release()

    except asyncio.TimeoutError as exc:
        worker.add_done_callback(release_after_worker)
        raise MediaServiceError(
            "The metadata request timed out. Please try again later."
        ) from exc

    except asyncio.CancelledError:
        worker.add_done_callback(release_after_worker)
        raise

    except Exception:
        _METADATA_SEMAPHORE.release()
        raise

    return {
        "platform": detection["platform"],
        **metadata,
    }
