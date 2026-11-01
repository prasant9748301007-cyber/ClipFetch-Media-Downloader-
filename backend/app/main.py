import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router


def get_allowed_origins() -> list[str]:
    """Read allowed frontend origins from an environment variable."""

    raw_origins = os.getenv(
        "CLIPFETCH_FRONTEND_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500",
    )

    origins = [
        origin.strip().rstrip("/")
        for origin in raw_origins.split(",")
        if origin.strip()
    ]

    if not origins:
        raise RuntimeError(
            "CLIPFETCH_FRONTEND_ORIGINS must contain at least one origin."
        )

    return origins


app = FastAPI(
    title="ClipFetch API",
    description="Backend API for the ClipFetch media downloader.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/", tags=["General"])
async def root():
    return {
        "message": "Welcome to ClipFetch API",
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "application": "ClipFetch",
        "version": "1.0.0",
        "message": "ClipFetch backend is running successfully.",
    }


app.include_router(router)
