FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin clipfetch \
    && install -d -m 0700 -o clipfetch -g clipfetch /tmp/clipfetch-downloads

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=clipfetch:clipfetch app ./app

ENV CLIPFETCH_TEMP_DIR=/tmp/clipfetch-downloads

USER clipfetch

EXPOSE 10000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
