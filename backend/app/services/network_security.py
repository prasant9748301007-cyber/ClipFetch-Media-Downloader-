import ipaddress
import socket
from urllib.parse import urlsplit
from typing import Callable, Any


class NetworkSecurityError(ValueError):
    """Raised when a URL resolves to an unsafe network address."""


def validate_public_host(
    url: str,
    resolver: Callable[..., Any] | None = None,
) -> list[str]:
    """
    Resolve a URL hostname and reject non-public IP addresses.
    Every resolved IPv4/IPv6 address must be globally routable.
    """

    if not isinstance(url, str) or not url:
        raise NetworkSecurityError("Invalid URL.")

    try:
        parsed = urlsplit(url)

        if parsed.scheme.lower() != "https":
            raise NetworkSecurityError("Only HTTPS URLs are allowed.")

        if not parsed.hostname:
            raise NetworkSecurityError("URL hostname is missing.")

        if parsed.username or parsed.password:
            raise NetworkSecurityError("URL credentials are not allowed.")

        port = parsed.port or 443

        if port != 443:
            raise NetworkSecurityError("Only port 443 is allowed.")

        hostname = parsed.hostname.rstrip(".").lower()

        if not hostname:
            raise NetworkSecurityError("Invalid hostname.")

        dns_resolver = resolver or socket.getaddrinfo

        results = dns_resolver(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )

        if not results:
            raise NetworkSecurityError("Hostname did not resolve.")

        resolved_ips = set()

        for result in results:
            address = result[4][0].split("%", 1)[0]

            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise NetworkSecurityError(
                    "DNS returned an invalid IP address."
                ) from exc

            if (
                not ip.is_global
                or ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise NetworkSecurityError(
                    "URL resolves to a non-public IP address."
                )

            resolved_ips.add(str(ip))

        return sorted(resolved_ips)

    except NetworkSecurityError:
        raise

    except (socket.gaierror, OSError) as exc:
        raise NetworkSecurityError(
            "Unable to resolve URL hostname."
        ) from exc

    except ValueError as exc:
        raise NetworkSecurityError(
            "Invalid URL or port."
        ) from exc
