import ipaddress
import socket
from urllib.parse import urlsplit
from typing import Callable, Any


# IANA special-purpose registries. Reject these prefixes even where a
# particular entry is marked globally reachable; they are not general
# internet destinations suitable for untrusted SSRF/CONNECT targets.
# https://www.iana.org/assignments/iana-ipv4-special-registry
# https://www.iana.org/assignments/iana-ipv6-special-registry
SPECIAL_PURPOSE_NETWORKS = tuple(map(ipaddress.ip_network, (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.31.196.0/24", "192.52.193.0/24", "192.88.99.0/24",
    "192.168.0.0/16", "192.175.48.0/24", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "240.0.0.0/4",
    "255.255.255.255/32", "::/128", "::1/128", "::ffff:0:0/96",
    "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "100:0:0:1::/64",
    "2001::/23", "2001:2::/48", "2001:3::/32", "2001:4:112::/48",
    "2001:10::/28", "2001:20::/28", "2001:30::/28", "2001:db8::/32",
    "2002::/16", "2620:4f:8000::/48", "3fff::/20", "5f00::/16",
    "fc00::/7", "fe80::/10",
)))


def is_public_ip(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Return True only if an IP is globally routable and not special-purpose."""
    return (
        address.is_global
        and getattr(address, "ipv4_mapped", None) is None
        and not any(address in network for network in SPECIAL_PURPOSE_NETWORKS)
        and not any((
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        ))
    )


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

    if (
        not isinstance(url, str)
        or not url
        or any(ord(char) <= 32 or ord(char) == 127 for char in url)
        or "\\" in url
    ):
        raise NetworkSecurityError("Invalid URL.")

    try:
        parsed = urlsplit(url)

        if parsed.scheme.lower() != "https":
            raise NetworkSecurityError("Only HTTPS URLs are allowed.")

        if not parsed.hostname:
            raise NetworkSecurityError("URL hostname is missing.")

        if parsed.username is not None or parsed.password is not None:
            raise NetworkSecurityError("URL credentials are not allowed.")

        port = parsed.port or 443

        if port != 443:
            raise NetworkSecurityError("Only port 443 is allowed.")

        try:
            hostname = (
                parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
            )
        except UnicodeError as exc:
            raise NetworkSecurityError("Invalid hostname.") from exc

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

            if not is_public_ip(ip):
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
