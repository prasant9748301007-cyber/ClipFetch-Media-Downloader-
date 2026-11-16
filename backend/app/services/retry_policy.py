"""Shared, bounded retry policy for transient yt-dlp network failures."""

from __future__ import annotations

from collections.abc import Callable

MAX_RETRY_DELAY_SECONDS = 2.0


def exponential_retry_delay(attempt: int) -> float:
    """Return a short exponential delay capped at two seconds."""

    attempt = max(0, int(attempt))
    return min(0.5 * (2**attempt), MAX_RETRY_DELAY_SECONDS)


def get_retry_policy(
    *,
    retries: int,
    fragment_retries: int,
    extractor_retries: int,
    file_access_retries: int,
) -> dict[str, object]:
    """Build yt-dlp API options with finite retries and bounded backoff."""

    sleep: Callable[[int], float] = exponential_retry_delay
    return {
        "retries": retries,
        "fragment_retries": fragment_retries,
        "extractor_retries": extractor_retries,
        "file_access_retries": file_access_retries,
        "retry_sleep_functions": {
            "http": sleep,
            "fragment": sleep,
            "extractor": sleep,
            "file_access": sleep,
        },
    }
