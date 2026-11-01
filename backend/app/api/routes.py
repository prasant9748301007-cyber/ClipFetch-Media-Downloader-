from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.services.download_service import (
    download_authorized_media,
    DownloadServiceError,
)

from pydantic import BaseModel, Field

from app.services.platform_detector import detect_platform
from app.services.media_service import (
    get_media_metadata,
    MediaServiceError,
)
import logging

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api", tags=["Media"])


class URLRequest(BaseModel):
    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="The media URL to validate.",
    )
class DownloadRequest(BaseModel):
    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="The authorized media URL.",
    )

    format_id: str = Field(
        default="best",
        min_length=1,
        max_length=40,
        description="A format identifier returned for this media.",
    )


@router.post("/validate-url")
async def validate_url(request: URLRequest):
    """Validate a URL and identify its platform."""

    return detect_platform(request.url)


@router.post("/metadata")
async def get_metadata(request: URLRequest):
    """Retrieve metadata for supported, accessible media."""

    try:
        metadata = await get_media_metadata(request.url)

        return {
            "success": True,
            "message": "Media metadata retrieved successfully.",
            "media": metadata,
        }

    except MediaServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="An unexpected server error occurred.",
        ) from exc
@router.post("/download")
async def download_media_endpoint(request: DownloadRequest):
    """Download media and return it as an MP4 file."""

    temporary_directory = None

    try:
        file_path, temporary_directory = (
            await download_authorized_media(
                request.url,
                request.format_id,
            )
        )

        is_audio = request.format_id == "audio_mp3"

        return FileResponse(
            path=file_path,
            filename=(
                "clipfetch_audio.mp3"
                if is_audio
                else "clipfetch_media.mp4"
            ),
            media_type=(
                "audio/mpeg"
                if is_audio
                else "video/mp4"
            ),
            background=BackgroundTask(
                temporary_directory.cleanup
            ),
        )

    except DownloadServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        if temporary_directory is not None:
            temporary_directory.cleanup()

        logger.exception("Unexpected download endpoint error.")

        raise HTTPException(
            status_code=500,
            detail="An unexpected download error occurred.",
        ) from exc
