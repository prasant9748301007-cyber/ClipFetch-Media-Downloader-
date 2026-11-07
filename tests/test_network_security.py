import socket

import pytest

from app.services.network_security import (
    NetworkSecurityError,
    validate_public_host,
)


def fake_resolver(addresses):
    def resolver(host, port, type=None):
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port),
            )
            for address in addresses
        ]

    return resolver


def test_public_ipv4_is_allowed():
    result = validate_public_host(
        "https://www.tiktok.com/video/123",
        resolver=fake_resolver(["8.8.8.8"]),
    )

    assert result == ["8.8.8.8"]


def test_public_ipv6_is_allowed():
    result = validate_public_host(
        "https://www.tiktok.com/video/123",
        resolver=fake_resolver(["2001:4860:4860::8888"]),
    )

    assert result == ["2001:4860:4860::8888"]


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_private_or_non_public_ips_are_rejected(ip):
    with pytest.raises(NetworkSecurityError):
        validate_public_host(
            "https://www.tiktok.com/video/123",
            resolver=fake_resolver([ip]),
        )


def test_mixed_public_and_private_ips_are_rejected():
    with pytest.raises(NetworkSecurityError):
        validate_public_host(
            "https://www.tiktok.com/video/123",
            resolver=fake_resolver(
                ["8.8.8.8", "192.168.1.1"]
            ),
        )


def test_empty_dns_results_are_rejected():
    with pytest.raises(NetworkSecurityError):
        validate_public_host(
            "https://www.tiktok.com/video/123",
            resolver=fake_resolver([]),
        )


def test_dns_resolution_failure_is_rejected():
    def failing_resolver(host, port, type=None):
        raise socket.gaierror("DNS resolution failed")

    with pytest.raises(NetworkSecurityError):
        validate_public_host(
            "https://www.tiktok.com/video/123",
            resolver=failing_resolver,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.tiktok.com/video/123",
        "https://www.tiktok.com:444/video/123",
        "https://user:password@www.tiktok.com/video/123",
        "https:///video/123",
    ],
)
def test_unsafe_url_structure_is_rejected(url):
    with pytest.raises(NetworkSecurityError):
        validate_public_host(
            url,
            resolver=fake_resolver(["8.8.8.8"]),
        )
