
import os
import ipaddress
import re
from urllib.parse import urlsplit

from app.services.network_security import NetworkSecurityError


def get_egress_proxy_options() -> dict[str, str]:
    """
    Return yt-dlp proxy options.

    Proxy use is required by default. A local developer may explicitly set
    CLIPFETCH_REQUIRE_EGRESS_PROXY=false when running without the proxy.
    """

    proxy_url = os.getenv("CLIPFETCH_EGRESS_PROXY", "").strip()
    required_value = os.getenv(
        "CLIPFETCH_REQUIRE_EGRESS_PROXY", "true"
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
        proxy_host = parsed.hostname or ""
        try:
            proxy_ip = ipaddress.ip_address(proxy_host)
        except ValueError:
            proxy_ip = None
        proxy_port = parsed.port
    except ValueError:
        raise NetworkSecurityError(
            "Invalid outbound proxy configuration."
        ) from None

    # Accept loopback for a same-container sidecar, or a single-label DNS
    # name used by Render's private network. Never accept public proxy URLs
    # or literal non-loopback proxy IPs from configuration.
    hostname_valid = bool(
        proxy_host
        and re.fullmatch(r"[A-Za-z0-9-]+", proxy_host)
        and not proxy_host.startswith("-")
        and not proxy_host.endswith("-")
    )
    if (
        parsed.scheme != "http"
        or (proxy_ip is not None and not proxy_ip.is_loopback)
        or (proxy_ip is None and not hostname_valid)
        or proxy_port is None
        or not (1 <= proxy_port <= 65535)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise NetworkSecurityError(
            "Outbound proxy must be a loopback or private-network HTTP proxy "
            "with an explicit port."
        )

    return {"proxy": proxy_url}
