import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.services import filtering_proxy
from app.services.filtering_proxy import (
    DestinationRejected,
    parse_connect_request,
    resolve_public_destination,
)


def fake_resolver(addresses):
    def resolve(host, port, type=None):
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET,
             socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in addresses
        ]
    return resolve


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.1", "169.254.1.1", "192.168.1.1",
    "0.0.0.0", "224.0.0.1", "::1", "fc00::1", "fe80::1",
    "2001:db8::1",
    "192.0.0.9", "192.88.99.1", "2001:1::1", "2002::1",
])
def test_proxy_rejects_non_public_ipv4_and_ipv6(ip):
    with pytest.raises(DestinationRejected):
        resolve_public_destination("cdn.example", 443, fake_resolver([ip]))


def test_proxy_rejects_mixed_public_and_private_dns_answers():
    with pytest.raises(DestinationRejected):
        resolve_public_destination(
            "cdn.example", 443, fake_resolver(["8.8.8.8", "10.0.0.1"])
        )


def test_proxy_accepts_and_normalizes_public_dns_answers():
    assert resolve_public_destination(
        "CDN.Example.", 443, fake_resolver(["8.8.8.8"])
    ) == [(socket.AF_INET, "8.8.8.8")]


def test_proxy_checks_ipv6_literal_and_rejects_non_443_port():
    assert resolve_public_destination("2001:4860:4860::8888", 443)
    with pytest.raises(DestinationRejected):
        resolve_public_destination("8.8.8.8", 8443)


def test_proxy_rejects_dns_failure_and_empty_answer():
    def fails(*args, **kwargs):
        raise socket.gaierror("failure")
    with pytest.raises(DestinationRejected):
        resolve_public_destination("cdn.example", 443, fails)
    with pytest.raises(DestinationRejected):
        resolve_public_destination("cdn.example", 443, fake_resolver([]))


def test_connection_uses_validated_ip_without_second_dns_lookup(monkeypatch):
    lookups = []
    connected = []

    def resolve(host, port, type=None):
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    class FakeSocket:
        def __init__(self, family, kind):
            assert family == socket.AF_INET

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            connected.append(address)

        def setblocking(self, value):
            pass

        def close(self):
            pass

    monkeypatch.setattr(filtering_proxy.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(filtering_proxy.socket, "socket", FakeSocket)
    filtering_proxy._connect_validated("cdn.example", 443)

    assert lookups == ["cdn.example"]
    assert connected == [("8.8.8.8", 443)]


def test_redirect_and_cdn_connect_requests_are_separately_validated():
    requests = [
        b"CONNECT redirect.example:443 HTTP/1.1\r\n\r\n",
        b"CONNECT media-cdn.example:443 HTTP/1.1\r\n\r\n",
    ]
    destinations = [parse_connect_request(request) for request in requests]
    assert destinations == [
        ("redirect.example", 443),
        ("media-cdn.example", 443),
    ]

    for host, port in destinations:
        assert resolve_public_destination(
            host, port, fake_resolver(["8.8.8.8"])
        ) == [(socket.AF_INET, "8.8.8.8")]


def test_proxy_server_checks_each_live_connect_tunnel(monkeypatch):
    checked_hosts = []
    upstream_peers = []

    def open_policy_checked_tunnel(host, port):
        checked_hosts.append((host, port))
        proxy_end, peer_end = socket.socketpair()
        upstream_peers.append(peer_end)
        return proxy_end

    monkeypatch.setattr(
        filtering_proxy, "_connect_validated", open_policy_checked_tunnel
    )
    server = filtering_proxy._ThreadedProxy(
        ("127.0.0.1", 0), filtering_proxy._ProxyHandler
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        for host in ("redirect.example", "media-cdn.example"):
            with socket.create_connection(server.server_address) as client:
                client.sendall(f"CONNECT {host}:443 HTTP/1.1\r\n\r\n".encode())
                assert client.recv(256).startswith(b"HTTP/1.1 200")
        assert checked_hosts == [
            ("redirect.example", 443),
            ("media-cdn.example", 443),
        ]
    finally:
        server.shutdown()
        server.server_close()
        for peer in upstream_peers:
            peer.close()


@pytest.mark.parametrize("request_bytes", [
    b"GET https://example.com/ HTTP/1.1\r\n\r\n",
    b"CONNECT example.com:80 HTTP/1.1\r\n\r\n",
    b"CONNECT example.com:0 HTTP/1.1\r\n\r\n",
    b"CONNECT example.com:65536 HTTP/1.1\r\n\r\n",
    b"CONNECT example.com:garbage HTTP/1.1\r\n\r\n",
    b"CONNECT example.com:443 INVALID\r\n\r\n",
    b"CONNECT user@example.com:443 HTTP/1.1\r\n\r\n",
    b"CONNECT [2001:4860:4860::8888%eth0]:443 HTTP/1.1\r\n\r\n",
    b"CONNECT example.com/path:443 HTTP/1.1\r\n\r\n",
])
def test_proxy_rejects_non_connect_or_invalid_connect_requests(request_bytes):
    if request_bytes.endswith(b":80 HTTP/1.1\r\n\r\n"):
        host, port = parse_connect_request(request_bytes)
        with pytest.raises(DestinationRejected):
            resolve_public_destination(host, port, fake_resolver(["8.8.8.8"]))
    else:
        with pytest.raises(DestinationRejected):
            parse_connect_request(request_bytes)


def test_proxy_relays_bidirectional_tunnel_data(monkeypatch):
    upstream_peers = []

    def open_tunnel(host, port):
        proxy_end, peer_end = socket.socketpair()
        upstream_peers.append(peer_end)
        return proxy_end

    monkeypatch.setattr(filtering_proxy, "_connect_validated", open_tunnel)
    server = filtering_proxy._ThreadedProxy(
        ("127.0.0.1", 0), filtering_proxy._ProxyHandler
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with socket.create_connection(server.server_address) as client:
            client.settimeout(2)
            client.sendall(b"CONNECT cdn.example:443 HTTP/1.1\r\n\r\n")
            assert client.recv(256).startswith(b"HTTP/1.1 200")
            client.sendall(b"ping")
            peer = upstream_peers[0]
            peer.settimeout(2)
            assert peer.recv(16) == b"ping"
            peer.sendall(b"pong")
            assert client.recv(16) == b"pong"
    finally:
        server.shutdown()
        server.server_close()
        for peer in upstream_peers:
            peer.close()


def test_proxy_rejects_oversized_connect_headers():
    server = filtering_proxy._ThreadedProxy(
        ("127.0.0.1", 0), filtering_proxy._ProxyHandler
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with socket.create_connection(server.server_address) as client:
            client.settimeout(2)
            oversized = (
                b"CONNECT cdn.example:443 HTTP/1.1\r\nX-Pad: "
                + b"a" * (filtering_proxy.HEADER_LIMIT + 100)
                + b"\r\n\r\n"
            )
            client.sendall(oversized)
            assert client.recv(256).startswith(b"HTTP/1.1 431")
    finally:
        server.shutdown()
        server.server_close()


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_serve_defaults_to_loopback_bind(monkeypatch):
    monkeypatch.delenv("CLIPFETCH_PROXY_BIND", raising=False)
    monkeypatch.setenv("CLIPFETCH_PROXY_PORT", "0")

    bound_addresses = []
    original_proxy = filtering_proxy._ThreadedProxy

    class RecordingProxy(original_proxy):
        def __init__(self, server_address, handler_cls):
            bound_addresses.append(server_address)
            super().__init__(server_address, handler_cls)

    monkeypatch.setattr(filtering_proxy, "_ThreadedProxy", RecordingProxy)
    stop_event = threading.Event()
    stop_event.set()
    filtering_proxy.serve(stop_event=stop_event)

    assert bound_addresses == [("127.0.0.1", 0)]


def test_filtering_proxy_process_shuts_down_gracefully_on_sigterm():
    port = _reserve_loopback_port()
    backend_dir = str(Path(__file__).resolve().parents[1] / "backend")
    env = dict(os.environ)
    env["PYTHONPATH"] = backend_dir
    env["CLIPFETCH_PROXY_BIND"] = "127.0.0.1"
    env["CLIPFETCH_PROXY_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "-m", "app.services.filtering_proxy"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5.0
        ready = False
        while time.monotonic() < deadline:
            assert proc.poll() is None
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2) as sock:
                    sock.sendall(b"OPTIONS * HTTP/1.1\r\n\r\n")
                    if sock.recv(64).startswith(b"HTTP/1.1 403 "):
                        ready = True
                        break
            except OSError:
                time.sleep(0.05)
        assert ready

        proc.terminate()
        assert proc.wait(timeout=5.0) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)
