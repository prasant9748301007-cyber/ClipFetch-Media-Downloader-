"""Small CONNECT-only HTTPS proxy with DNS-rebinding-safe destination checks.

Run as a separate process/service. The proxy resolves each requested hostname,
rejects the whole answer set if any address is unsafe, and connects to one of
those exact validated IPs (never resolving the hostname again at connect time).
"""

import ipaddress
import logging
import os
import selectors
import socket
import socketserver
import threading
import time
from typing import Callable

from app.services.network_security import is_public_ip

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
HEADER_LIMIT = 16 * 1024
RELAY_BUFFER = 64 * 1024
MAX_PROXY_CONNECTIONS = 64


class DestinationRejected(ValueError):
    """Raised when a CONNECT destination is outside the public HTTPS space."""


def resolve_public_destination(
    host: str,
    port: int,
    resolver: Callable[..., list] | None = None,
) -> list[tuple[int, str]]:
    """Return validated (family, IP) pairs; reject mixed or special answers."""

    if not host or port != 443:
        raise DestinationRejected("Only HTTPS destinations on port 443 are allowed.")

    if "%" in host or any(ord(c) <= 32 or ord(c) == 127 for c in host):
        raise DestinationRejected("Invalid destination hostname.")

    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        answers = [(socket.AF_INET6 if literal.version == 6 else socket.AF_INET,
                    str(literal))]
    else:
        try:
            normalized = host.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise DestinationRejected("Invalid destination hostname.") from exc
        if not normalized:
            raise DestinationRejected("Invalid destination hostname.")
        lookup = resolver or socket.getaddrinfo
        try:
            records = lookup(normalized, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as exc:
            raise DestinationRejected("Destination DNS resolution failed.") from exc
        answers = []
        for record in records:
            family, _, _, _, sockaddr = record
            raw = sockaddr[0].split("%", 1)[0]
            try:
                address = ipaddress.ip_address(raw)
            except ValueError as exc:
                raise DestinationRejected("DNS returned an invalid address.") from exc
            answers.append((family, str(address)))

    if not answers:
        raise DestinationRejected("Destination did not resolve.")

    validated = set()
    for family, raw in answers:
        address = ipaddress.ip_address(raw)
        if not is_public_ip(address):
            raise DestinationRejected("Destination resolves to a non-public address.")
        validated.add((family, str(address)))
    return sorted(validated)


def _connect_validated(host: str, port: int) -> socket.socket:
    """Connect directly to a previously validated address, avoiding a second DNS lookup."""
    candidates = resolve_public_destination(host, port)
    last_error = None
    for family, address in candidates:
        connection = socket.socket(family, socket.SOCK_STREAM)
        connection.settimeout(CONNECT_TIMEOUT)
        try:
            endpoint = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
            connection.connect(endpoint)
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
    raise OSError("Could not connect to a validated destination.") from last_error


def parse_connect_request(request: bytes) -> tuple[str, int]:
    """Parse the authority from one bounded CONNECT request header."""
    first_line = request.split(b"\r\n", 1)[0].decode("ascii", "strict")
    parts = first_line.split()
    if (
        len(parts) != 3
        or parts[0].upper() != "CONNECT"
        or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}
    ):
        raise DestinationRejected("CONNECT method required.")
    authority = parts[1]
    if authority.startswith("["):
        close = authority.find("]")
        if close < 0 or authority[close + 1:close + 2] != ":":
            raise DestinationRejected("Invalid CONNECT authority.")
        host, port_text = authority[1:close], authority[close + 2:]
    else:
        host, separator, port_text = authority.rpartition(":")
        if not separator:
            raise DestinationRejected("Invalid CONNECT authority.")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise DestinationRejected("Invalid CONNECT port.") from exc
    if not (1 <= port <= 65535):
        raise DestinationRejected("Invalid CONNECT port.")
    if (
        not host
        or any(ch in host for ch in ("@", "%", "/", "\\", "?", "#"))
        or any(ord(ch) <= 32 or ord(ch) == 127 for ch in host)
    ):
        raise DestinationRejected("Invalid CONNECT hostname.")
    return host, port


class _ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        deadline = time.monotonic() + CONNECT_TIMEOUT
        self.request.settimeout(CONNECT_TIMEOUT)
        try:
            request = bytearray()
            while b"\r\n\r\n" not in request and len(request) <= HEADER_LIMIT:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out reading CONNECT header.")
                self.request.settimeout(remaining)
                chunk = self.request.recv(2048)
                if not chunk:
                    return
                request.extend(chunk)
            if len(request) > HEADER_LIMIT:
                self._respond(b"431 Request Header Fields Too Large\r\n\r\n")
                return
            sep_idx = request.find(b"\r\n\r\n")
            if sep_idx < 0:
                return
            header_bytes = bytes(request[:sep_idx + 4])
            extra_bytes = bytes(request[sep_idx + 4:])
            host, port = parse_connect_request(header_bytes)
            upstream = _connect_validated(host, port)
        except (DestinationRejected, ValueError, UnicodeError):
            self._respond(b"403 Destination Rejected\r\n\r\n")
            return
        except (OSError, TimeoutError):
            self._respond(b"502 Upstream Unavailable\r\n\r\n")
            return

        try:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.request.settimeout(60)
            upstream.settimeout(60)
            if extra_bytes:
                upstream.sendall(extra_bytes)
            self._relay(self.request, upstream)
        except OSError:
            pass
        finally:
            upstream.close()

    def _respond(self, response: bytes) -> None:
        try:
            self.request.sendall(b"HTTP/1.1 " + response)
        except OSError:
            pass

    @staticmethod
    def _relay(client: socket.socket, upstream: socket.socket) -> None:
        with selectors.DefaultSelector() as selector:
            selector.register(client, selectors.EVENT_READ, upstream)
            selector.register(upstream, selectors.EVENT_READ, client)
            while True:
                ready = selector.select(timeout=60)
                if not ready:
                    return
                for key, _ in ready:
                    try:
                        data = key.fileobj.recv(RELAY_BUFFER)
                        if not data:
                            return
                        key.data.sendall(data)
                    except OSError:
                        return


class _ThreadedProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        self._connection_slots = threading.BoundedSemaphore(MAX_PROXY_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._connection_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


def serve(
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Serve the filtering proxy. Intended for a private Render service."""
    host = host or os.getenv("CLIPFETCH_PROXY_BIND", "0.0.0.0")
    port = port or int(os.getenv("CLIPFETCH_PROXY_PORT", "3128"))
    with _ThreadedProxy((host, port), _ProxyHandler) as server:
        logger.info("Filtering CONNECT proxy listening on %s:%d", host, port)
        server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()
