import pytest

from app.services.egress_proxy import get_egress_proxy_options
from app.services.network_security import NetworkSecurityError


def test_proxy_is_optional_by_default(monkeypatch):
    monkeypatch.delenv("CLIPFETCH_EGRESS_PROXY", raising=False)
    monkeypatch.delenv("CLIPFETCH_REQUIRE_EGRESS_PROXY", raising=False)

    assert get_egress_proxy_options() == {}


def test_required_proxy_missing_is_rejected(monkeypatch):
    monkeypatch.delenv("CLIPFETCH_EGRESS_PROXY", raising=False)
    monkeypatch.setenv("CLIPFETCH_REQUIRE_EGRESS_PROXY", "true")

    with pytest.raises(NetworkSecurityError):
        get_egress_proxy_options()


def test_valid_local_proxy_is_accepted(monkeypatch):
    monkeypatch.setenv(
        "CLIPFETCH_EGRESS_PROXY",
        "http://127.0.0.1:3128",
    )
    monkeypatch.setenv("CLIPFETCH_REQUIRE_EGRESS_PROXY", "true")

    assert get_egress_proxy_options() == {
        "proxy": "http://127.0.0.1:3128"
    }


@pytest.mark.parametrize("value", ["yes", "1", "enabled", "invalid"])
def test_invalid_required_setting_is_rejected(monkeypatch, value):
    monkeypatch.setenv("CLIPFETCH_REQUIRE_EGRESS_PROXY", value)
    monkeypatch.delenv("CLIPFETCH_EGRESS_PROXY", raising=False)

    with pytest.raises(NetworkSecurityError):
        get_egress_proxy_options()


@pytest.mark.parametrize(
    "proxy_url",
    [
        "https://127.0.0.1:3128",
        "http://8.8.8.8:3128",
        "http://192.168.1.10:3128",
        "http://127.0.0.1",
        "http://user:pass@127.0.0.1:3128",
        "http://127.0.0.1:3128/proxy",
        "http://127.0.0.1:3128/?test=1",
        "http://127.0.0.1:3128/#fragment",
        "not-a-valid-proxy",
    ],
)
def test_unsafe_proxy_urls_are_rejected(monkeypatch, proxy_url):
    monkeypatch.setenv("CLIPFETCH_EGRESS_PROXY", proxy_url)
    monkeypatch.setenv("CLIPFETCH_REQUIRE_EGRESS_PROXY", "true")

    with pytest.raises(NetworkSecurityError):
        get_egress_proxy_options()


def test_ipv6_loopback_proxy_is_accepted(monkeypatch):
    monkeypatch.setenv(
        "CLIPFETCH_EGRESS_PROXY",
        "http://[::1]:3128",
    )
    monkeypatch.setenv("CLIPFETCH_REQUIRE_EGRESS_PROXY", "true")

    assert get_egress_proxy_options() == {
        "proxy": "http://[::1]:3128"
    }
