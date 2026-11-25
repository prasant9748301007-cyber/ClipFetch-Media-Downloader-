# ClipFetch

ClipFetch is a FastAPI service and static web interface for downloading public media that the user owns or is authorized to save. The backend uses `yt-dlp` (with `curl-cffi` impersonation where required) for extraction and FFmpeg for MP4/MP3 output, routed through a CONNECT-only filtering egress proxy.

## Features

- URL validation and platform detection for supported TikTok, Instagram, and Facebook hostnames.
- Metadata lookup (title, thumbnail, duration, creator, and available video formats).
- Video download in MP4 (`clipfetch_media.mp4`) with stream-copy remuxing when H.264/AAC compatible and `+faststart` moov placement, falling back to H.264/AAC encoding only when needed.
- Audio-only extraction converted to MP3 (`clipfetch_audio.mp3`) at 192 kbps and 44.1 kHz.
- Responsive static HTML/CSS/JavaScript frontend with light/dark theme toggle, Terms of Service, and Privacy Policy pages.
- Fail-closed egress proxy enforcement, HTTPS-only URLs, platform-host allowlisting, IANA special-purpose and non-public IP rejection, 8 KiB request-body limit, 100 MiB media size limit, bounded concurrency, security headers, and private per-request temporary directories.

## Supported platforms

Supported hostnames are explicitly allowlisted in [platform_detector.py](backend/app/services/platform_detector.py):

- **TikTok**: `tiktok.com`, `www.tiktok.com`, `m.tiktok.com`, `vm.tiktok.com`, `vt.tiktok.com`
- **Instagram**: `instagram.com`, `www.instagram.com`
- **Facebook**: `facebook.com`, `www.facebook.com`, `m.facebook.com`, `web.facebook.com`, `fb.watch`

Live streams, playlists, private/authenticated media, and other platforms are not supported.

## Tech stack

- **Backend**: Python 3.12, FastAPI, Uvicorn, Pydantic, yt-dlp, curl-cffi
- **Media processing**: FFmpeg and `ffprobe`
- **Frontend**: Vanilla HTML, CSS, and JavaScript
- **Testing & CI**: Pytest, Node.js syntax check (`node --check`), GitHub Actions, Docker

## Architecture overview

- `frontend/`: static browser interface (`index.html`, `terms.html`, `privacy.html`, `css/style.css`, `js/config.js`, `js/app.js`).
- `backend/app/main.py`: FastAPI application, CORS configuration, lifespan FFmpeg check, health endpoints.
- `backend/app/supervisor.py`: single-container process supervisor that starts `filtering_proxy` on loopback first, verifies readiness, starts Uvicorn, and coordinates graceful shutdown and crash handling.
- `backend/app/middleware.py`: `RequestBodyLimitMiddleware` (8 KiB cap) and `SecurityHeadersMiddleware`.
- `backend/app/api/routes.py`: `/api/validate-url`, `/api/metadata`, and `/api/download` endpoints with automatic temp-directory cleanup after response streaming.
- `backend/app/services/`:
  - `platform_detector.py`: strict HTTPS URL validation and platform hostname allowlist.
  - `network_security.py`: DNS precheck and IANA special-purpose/non-public IPv4/IPv6 rejection.
  - `egress_proxy.py`: fail-closed outbound proxy URL configuration.
  - `filtering_proxy.py`: CONNECT-only HTTPS proxy with DNS-rebinding-safe IP pinning (loopback `127.0.0.1:3128`).
  - `media_service.py`: bounded-concurrency metadata extraction.
  - `download_service.py`: bounded-concurrency download, size enforcement, FFmpeg remux/transcoding, and temp cleanup.
  - `retry_policy.py`: finite retry policy with bounded exponential backoff.
- `backend/app/database/` and `backend/app/schemas/`: empty placeholders. The application does not use a database, store download history, or persist user data.
- `tests/`: backend unit, security, API, concurrency, and local FFmpeg integration tests.

## Requirements

- Python 3.12
- FFmpeg and `ffprobe` on the backend host
- Node.js 20 (optional, for running `node --check` on frontend JavaScript)
- A modern browser for the static frontend

## Local installation and setup

### 1. Install FFmpeg (Ubuntu / WSL2)

```bash
sudo apt update
sudo apt install -y ffmpeg
ffmpeg -version
ffprobe -version
```

### 2. Create virtual environment and install backend dependencies

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 3. Run the backend

For local development with the filtering proxy (recommended to match production behavior):

```bash
# Terminal 1 (from backend/ with venv activated)
python -m app.services.filtering_proxy
```

```bash
# Terminal 2 (from backend/ with venv activated)
export CLIPFETCH_FRONTEND_ORIGINS="http://127.0.0.1:5500,http://localhost:5500"
export CLIPFETCH_EGRESS_PROXY="http://127.0.0.1:3128"
export CLIPFETCH_REQUIRE_EGRESS_PROXY="true"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

For isolated local development without running the filtering proxy:

```bash
export CLIPFETCH_FRONTEND_ORIGINS="http://127.0.0.1:5500,http://localhost:5500"
export CLIPFETCH_REQUIRE_EGRESS_PROXY="false"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 4. Run the frontend

In a separate terminal:

```bash
cd frontend
python3 -m http.server 5500
```

Open `http://127.0.0.1:5500`. Note that the checked-in `frontend/js/config.js` points to the deployed Render backend URL (`https://clipfetch-media-downloader.onrender.com`); point `API_BASE_URL` to `http://127.0.0.1:8000` temporarily when testing against a local backend.

The values in `backend/.env.example` are documentation examples only. The backend reads process environment variables directly and does not load a `.env` file automatically.

## Environment variables

| Variable | Purpose |
|---|---|
| `CLIPFETCH_FRONTEND_ORIGINS` | Comma-separated exact origins allowed by CORS (e.g. `https://your-frontend.onrender.com`). No trailing slash or path. |
| `CLIPFETCH_TEMP_DIR` | Absolute parent directory for private per-request temporary download directories. Defaults to `<system-temp>/clipfetch-downloads`. |
| `CLIPFETCH_EGRESS_PROXY` | Required filtering proxy URL (`http://127.0.0.1:3128` in single-container production and local proxy dev). |
| `CLIPFETCH_REQUIRE_EGRESS_PROXY` | Defaults to `true` (fail closed unless a valid proxy is configured). Set `false` only for isolated local development without the supervisor/proxy. |
| `CLIPFETCH_PROXY_BIND` | Bind address for `filtering_proxy` (default `127.0.0.1`; single-container mode requires `127.0.0.1`). |
| `CLIPFETCH_PROXY_PORT` | Listening port for `filtering_proxy` (default `3128`). |

## Usage instructions

1. Open the ClipFetch web interface.
2. Paste a public `https://` media URL from TikTok, Instagram, or Facebook.
3. Click **Fetch Media** to retrieve metadata and available qualities.
4. Select **Best Quality**, a specific video resolution (MP4), or **Audio Only · MP3**.
5. Click **Download Media** and wait for server preparation and file download to complete.

## API endpoints

All `POST` endpoints accept JSON bodies up to 8 KiB. Interactive documentation is available at `/docs` while running.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/` | Basic service metadata. |
| `GET` | `/api/health` | Liveness check (`status`, `application`, `version`). |
| `POST` | `/api/validate-url` | Check HTTPS URL syntax and supported platform hostname. |
| `POST` | `/api/metadata` | Extract title, duration, thumbnail, creator, and available formats. |
| `POST` | `/api/download` | Download the selected format and stream `clipfetch_media.mp4` (`video/mp4`) or `clipfetch_audio.mp3` (`audio/mpeg`). |

Example request:

```json
{"url": "https://www.tiktok.com/@creator/video/123456"}
```

For `/api/download`, pass `format_id` as `"best"`, `"audio_mp3"`, or one of the `format_id` values returned by `/api/metadata`. The API returns `400` for invalid or unavailable media, `413` for bodies over 8 KiB, and `422` for malformed request payloads.

## Testing

From the repository root:

```bash
cd backend
python -m pip install -r requirements-dev.txt
cd ..
PYTHONPATH=backend python -m pytest tests -v
```

Syntax and compile checks:

```bash
python -m compileall -q backend/app
node --check frontend/js/app.js
node --check frontend/js/config.js
```

The test suite covers URL validation, network security and IANA special-purpose IP checks, egress proxy configuration, filtering proxy CONNECT/relay behavior, metadata and download services (mocked yt-dlp), concurrency/queue/timeout limits, API endpoints and headers, and local FFmpeg integration with generated media.

Mocked tests do not verify live TikTok, Instagram, or Facebook availability; platform extraction depends on upstream site structure and public accessibility.

## Deployment guide (Docker & Render)

Build and run the single-container backend locally (runs `filtering_proxy` on `127.0.0.1:3128` and FastAPI on `$PORT`, default `10000`):

```bash
docker build -t clipfetch-backend ./backend
docker run --rm -p 10000:10000 \
  -e CLIPFETCH_FRONTEND_ORIGINS="https://your-frontend.example" \
  clipfetch-backend
```

The container installs `ffmpeg`/`ffprobe`, runs as non-root user `clipfetch`, uses `python -m app.supervisor` to verify `filtering_proxy` readiness on `127.0.0.1:3128` before launching one Uvicorn worker (`--workers 1`) so in-process concurrency limits remain exact, and honors `$PORT` (default `10000`). Only port `10000` (or `$PORT`) is exposed; proxy port `3128` is bound strictly to `127.0.0.1` and never exposed.

### Render deployment (Single Web Service)

1. **Backend API + Filtering Proxy (Render Web Service)**:
   - Runtime: **Docker** using `backend/Dockerfile` (no separate Render Private Service needed).
   - Docker Command: leave empty (uses `CMD ["python", "-m", "app.supervisor"]` from `backend/Dockerfile`).
   - Health Check Path: `/api/health`.
   - Environment variables:
     - `CLIPFETCH_FRONTEND_ORIGINS=https://<your-frontend-service>.onrender.com`
     - `CLIPFETCH_EGRESS_PROXY=http://127.0.0.1:3128`
     - `CLIPFETCH_REQUIRE_EGRESS_PROXY=true`
     - `CLIPFETCH_PROXY_BIND=127.0.0.1`
     - `CLIPFETCH_PROXY_PORT=3128`
     - `CLIPFETCH_TEMP_DIR=/tmp/clipfetch-downloads`
2. **Frontend (Render Static Site)**:
   - Publish directory: `frontend`.
   - Ensure `frontend/js/config.js` `API_BASE_URL` matches your deployed backend HTTPS URL.

Important: Binding `filtering_proxy` to `127.0.0.1:3128` inside the Web Service container ensures port `3128` is unreachable from outside the container, and application-level proxy enforcement fails closed if the proxy stops or is unreachable. However, running both processes in one container does **not** enforce OS/kernel-level outbound firewalling against direct sockets from unrelated binaries; strict container-wide egress isolation requires a network-level outbound firewall/egress policy in your hosting environment.

The repository does not manage Render service plans, Cloudflare DNS/WAF, or edge per-IP rate limiting. Configure edge rate limiting at Cloudflare or your reverse proxy before exposing the API publicly.

## Security architecture

- **Strict URL & platform allowlisting**: Only `https://` URLs on port `443` without credentials, whitespace, backslashes, or control characters on explicitly allowlisted TikTok/Instagram/Facebook hostnames are accepted.
- **DNS precheck & IANA special-purpose IP blocking**: Submitted hostnames are resolved and rejected if any IP is non-global, private, loopback, link-local, multicast, reserved, unspecified, IPv4-mapped IPv6, or in IANA special-purpose ranges (`100.64.0.0/10`, `192.0.0.0/24`, `198.18.0.0/15`, `2001:db8::/32`, `2002::/16`, etc.).
- **Fail-closed filtering proxy**: `CLIPFETCH_REQUIRE_EGRESS_PROXY` defaults to `true`. `filtering_proxy.py` accepts `CONNECT` on port `443` only, re-resolves and validates every initial, redirect, and CDN hostname, and opens the upstream socket directly to the validated IP (preventing DNS rebinding). Proxy failures do not fall back to direct outbound requests.
- **Subprocess & filesystem isolation**: `yt-dlp` and `FFmpeg` receive argument lists (no shell interpolation); HLS uses `hls_prefer_native` so `yt-dlp` handles segments through the configured proxy rather than spawning `FFmpegFD` against remote URLs. Each download uses a private `0700` temporary directory cleaned up after streaming.
- **Resource limits**: 8 KiB max JSON body, 2048-char max URL, 100 MiB max media size, 3 concurrent metadata workers, 2 concurrent download workers, 60s metadata timeout, 180s download timeout, 90s FFmpeg timeout, finite retries.
- **No stored credentials**: No cookies, tokens, user accounts, or persistent media storage are used.

## Limitations

- Single Uvicorn worker means concurrency limits are process-local; scaling horizontally multiplies concurrent FFmpeg memory/CPU usage.
- Free/low-memory hosting tiers (e.g., 512 MiB RAM) may struggle with long or high-bitrate videos requiring full re-encoding (`libx264`).
- Platforms frequently change anti-bot protections, endpoints, and authentication requirements; private, age-gated, region-locked, or login-walled posts may fail upstream.
- Without TLS interception, the filtering proxy validates destination hostnames, ports, and resolved IPs for every `CONNECT` tunnel, not encrypted HTTP paths inside the TLS tunnel.

## Troubleshooting

- **"The required outbound proxy is unavailable or misconfigured"**: Ensure `filtering_proxy` is running and `CLIPFETCH_EGRESS_PROXY` is set (e.g. `http://127.0.0.1:3128`), or set `CLIPFETCH_REQUIRE_EGRESS_PROXY=false` for local development only.
- **CORS errors in the browser**: Check `CLIPFETCH_FRONTEND_ORIGINS` matches your frontend origin exactly (scheme + host + port, no trailing slash) and `frontend/js/config.js` points to the right backend URL.
- **FFmpeg not found**: Install `ffmpeg` (includes `ffprobe`) and verify `ffmpeg -version` and `ffprobe -version` succeed in the backend environment.
- **Metadata or download fails for a public URL**: Confirm the URL opens publicly in a logged-out browser, check that it is not a live stream or playlist, and update `yt-dlp` if an upstream platform changed its extractor layout.

## Legal and ethical usage

ClipFetch is intended strictly for downloading media that you own or have explicit permission to save. Do not use ClipFetch to bypass authentication, DRM, paywalls, or platform access controls, or to infringe copyright or violate the terms of service of third-party platforms.
