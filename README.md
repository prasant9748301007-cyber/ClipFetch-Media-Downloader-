# ClipFetch

ClipFetch is a small FastAPI service and static web interface for downloading media that the user owns or is authorized to save. The backend uses yt-dlp for extraction and FFmpeg for MP4/MP3 output.

## Current features

- URL support for TikTok, Instagram, and Facebook hostnames listed in `backend/app/services/platform_detector.py`.
- Metadata lookup and video downloads in MP4.
- Audio-only downloads converted to MP3 at 192 kbps and 44.1 kHz.
- A responsive HTML/CSS/JavaScript interface with dark and light themes.
- HTTPS, platform-host allowlisting, public-address DNS checks, request-size limits, bounded processing concurrency, and private per-request temporary directories.

ClipFetch does not bypass authentication, access controls, DRM, or platform restrictions. Use it only for media you are authorized to download and follow the source platform's terms.

## Architecture and current scope

- `frontend/`: static browser interface.
- `backend/app/api/`: FastAPI endpoints.
- `backend/app/services/`: URL checks, metadata extraction, yt-dlp downloads, and FFmpeg conversion.
- `tests/`: backend unit/API tests and small local FFmpeg integration tests.
- The database and schema packages are empty placeholders. The current application does not use SQLite, store download history, or persist user data.
- The repository does not include a Render or Cloudflare configuration. A Dockerfile is provided for a reproducible backend image.

## Requirements

- Python 3.12.
- FFmpeg and ffprobe on the backend host.
- Node.js 20 only to run the frontend JavaScript syntax check.
- A browser for the static frontend.

The backend uses the pinned packages in `backend/requirements.txt`. yt-dlp's supported platforms and extraction behavior can change as source sites change.

## Local setup

Install FFmpeg on Ubuntu or WSL2:

```bash
sudo apt update
sudo apt install ffmpeg
ffmpeg -version
ffprobe -version
```

Set up and start the backend:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export CLIPFETCH_FRONTEND_ORIGINS="http://127.0.0.1:5500,http://localhost:5500"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

In another terminal, start the static frontend:

```bash
cd frontend
python3 -m http.server 5500
```

Open `http://127.0.0.1:5500`. The local API URL is `http://127.0.0.1:8000`; the checked-in `frontend/js/config.js` currently points to the Render API, so change it to the local API URL when developing locally.

The environment variables in `backend/.env.example` are examples only. The application reads process environment variables and does not load a `.env` file automatically.

## Environment variables

| Variable | Purpose |
|---|---|
| `CLIPFETCH_FRONTEND_ORIGINS` | Comma-separated exact origins allowed by CORS. Set the deployed frontend origin without a path. |
| `CLIPFETCH_TEMP_DIR` | Parent directory for private temporary download directories. Defaults to the system temporary directory plus `clipfetch-downloads`. |
| `CLIPFETCH_EGRESS_PROXY` | Optional loopback HTTP proxy URL, such as `http://127.0.0.1:3128`. |
| `CLIPFETCH_REQUIRE_EGRESS_PROXY` | When `true`, requests fail closed unless a valid loopback proxy is configured. |

The proxy settings do not start a proxy. A local proxy service must be deployed separately if strict egress filtering is required.

## API

All POST endpoints accept JSON.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Basic service information. |
| GET | `/api/health` | Liveness check. |
| POST | `/api/validate-url` | Check HTTPS URL syntax and supported platform. |
| POST | `/api/metadata` | Extract title, duration, thumbnail, uploader, and available formats. |
| POST | `/api/download` | Download the selected format and return a streamed MP4 or MP3 file. |

Example request:

```json
{"url":"https://www.tiktok.com/@creator/video/123456"}
```

Download requests may include `format_id`; use `audio_mp3` for MP3 output. Video format IDs are revalidated against fresh extraction data before download.

The API returns HTTP 413 for request bodies above 8 KiB and HTTP 400 for invalid or unavailable media. The interactive API schema is at `/docs` while the service is running.

## Resource limits and file handling

- One Uvicorn worker is used by the provided Docker image so in-process concurrency limits remain meaningful.
- At most two media workers and three metadata workers run per process. Requests wait up to five seconds for a slot.
- Metadata extraction has a 60-second timeout; download processing has a 180-second timeout.
- Source and final files are limited to 100 MiB. FFmpeg is also given an output size cap.
- Each request uses a private temporary directory. The original source file is removed after conversion; the final response file is removed after the streamed response completes.
- Compatible H.264/AAC streams are remuxed without re-encoding. If only one stream is compatible, the other stream is encoded. Remaining formats are converted to H.264/AAC with one encoder thread.
- yt-dlp uses a small, finite retry count with short exponential delays. Fragment concurrency remains at yt-dlp's default of one.

The concurrency controls are process-local. Adding workers or scaling to multiple instances increases aggregate concurrency and temporary disk use; measure resource usage before changing either.

## Testing

From the repository root:

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest ../tests -v
```

The test suite includes mocked extractor/API unit tests, URL and network-security checks, resource-limit tests, and local FFmpeg integration tests using generated media. The GitHub Actions workflow runs backend tests, JavaScript syntax checks, and a Docker image build.

Mocked tests do not prove that TikTok, Instagram, or Facebook will serve a particular URL. Platform behavior must be checked separately with user-authorized public test media, and failures may be caused by platform policy, availability, regional restrictions, or upstream changes.

## Deployment

Build and run the backend image from the repository root:

```bash
docker build -t clipfetch-backend ./backend
docker run --rm -p 10000:10000 \
  -e CLIPFETCH_FRONTEND_ORIGINS="https://your-frontend.example" \
  clipfetch-backend
```

The container installs FFmpeg/ffprobe, runs as a non-root user, uses one Uvicorn worker, and honors the platform's `PORT` environment variable. Configure the service health check to use `/api/health`. On Render, select the Docker runtime and the backend Dockerfile. Host the frontend separately and set `CLIPFETCH_FRONTEND_ORIGINS` to its exact HTTPS origin.

The repository does not set a Render plan, Cloudflare DNS, proxy, firewall, or edge rate limit. Those deployment settings must be reviewed in the service account. The API has concurrency and request-size limits but no per-IP rate limiter; apply an edge rate limit before exposing it publicly. Do not enable `CLIPFETCH_REQUIRE_EGRESS_PROXY=true` unless a filtering loopback proxy is actually running in the container.

## Security and known limitations

- Only HTTPS URLs on the explicit platform hostname allowlist are accepted.
- DNS results for the submitted hostname are rejected if any address is non-public.
- yt-dlp and FFmpeg receive argument lists; user URLs and format IDs are not interpolated into shell commands.
- CORS allows configured origins only; credentials are disabled.
- No credentials, cookies, or account authentication are accepted by the application.
- DNS is checked before yt-dlp connects, but the application does not pin resolved addresses or inspect every redirect/CDN connection itself. Strict production SSRF protection therefore requires an outbound filtering proxy or network policy.
- No live Render resource metrics or authenticated deployment logs are part of this repository checkout, so memory use and production platform behavior must be checked in the service dashboard.
