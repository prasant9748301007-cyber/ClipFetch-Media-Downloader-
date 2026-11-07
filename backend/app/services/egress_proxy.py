
import os
import ipaddress
from urllib.parse import urlsplit

from app.services.network_security import NetworkSecurityError


def get_egress_proxy_options() -> dict[str, str]:
    """
    Return yt-dlp proxy options.

    Local development:
        Proxy is optional.

    Production:
        Set CLIPFETCH_REQUIRE_EGRESS_PROXY=true.
        Requests fail closed if the proxy is missing.
    """

    proxy_url = os.getenv("CLIPFETCH_EGRESS_PROXY", "").strip()
    required_value = os.getenv(
        "CLIPFETCH_REQUIRE_EGRESS_PROXY", "false"
    ).strip().lower()

    if required_value not in {"true", "false"}:
        raise NetworkSecurityError(
            "Invalid egress proxy requirement configuration."
        )

    proxy_required = required_value == "true"

    if not proxy_url:
        if proxy_required:
            raise NetworkSecurityError(
                "A required outbound security proxy is not configured."
            )
        return {}

    try:
        parsed = urlsplit(proxy_url)
        proxy_ip = ipaddress.ip_address(parsed.hostname or "")
        proxy_port = parsed.port
    except ValueError:
        raise NetworkSecurityError(
            "Invalid outbound proxy configuration."
        ) from None

    # Only allow a local HTTP proxy.
    if (
        parsed.scheme != "http"
        or not proxy_ip.is_loopback
        or proxy_port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise NetworkSecurityError(
            "Outbound proxy must be a local HTTP proxy with an explicit port."
        )

    return {"proxy": proxy_url}
