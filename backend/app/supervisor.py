"""Single-container process supervisor for ClipFetch.

Starts the loopback filtering CONNECT proxy first, verifies its readiness,
then launches the FastAPI/Uvicorn backend configured to fail closed through
that proxy. Monitors both processes and coordinates graceful shutdown without
leaking child processes.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 3128
DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 10000

PROXY_READY_TIMEOUT = 10.0
PROXY_POLL_INTERVAL = 0.05
MONITOR_POLL_INTERVAL = 0.2
API_SHUTDOWN_TIMEOUT = 15.0
PROXY_SHUTDOWN_TIMEOUT = 5.0

_API_STRIPPED_PROXY_ENV_VARS = (
    "NO_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


@dataclass(frozen=True)
class SupervisorConfig:
    """Validated runtime settings for the single-container supervisor."""

    proxy_host: str
    proxy_port: int
    api_host: str
    api_port: int
    egress_proxy_url: str


def _parse_port(raw_value: str, name: str) -> int:
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid integer port.") from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"{name} must be between 1 and 65535.")
    return port


def load_supervisor_config(
    env: Mapping[str, str] | None = None,
) -> SupervisorConfig:
    """Validate and build supervisor configuration from environment variables."""
    source = env if env is not None else os.environ

    proxy_host = (
        source.get("CLIPFETCH_PROXY_BIND", DEFAULT_PROXY_HOST).strip()
        or DEFAULT_PROXY_HOST
    )
    if proxy_host != DEFAULT_PROXY_HOST:
        raise ValueError(
            "CLIPFETCH_PROXY_BIND must be 127.0.0.1 in single-container mode."
        )

    proxy_port_raw = (
        source.get("CLIPFETCH_PROXY_PORT", str(DEFAULT_PROXY_PORT)).strip()
        or str(DEFAULT_PROXY_PORT)
    )
    proxy_port = _parse_port(proxy_port_raw, "CLIPFETCH_PROXY_PORT")

    api_port_raw = (
        source.get("PORT", str(DEFAULT_API_PORT)).strip()
        or str(DEFAULT_API_PORT)
    )
    api_port = _parse_port(api_port_raw, "PORT")

    if api_port == proxy_port:
        raise ValueError(
            f"PORT ({api_port}) cannot conflict with proxy port ({proxy_port})."
        )

    require_proxy = (
        source.get("CLIPFETCH_REQUIRE_EGRESS_PROXY", "true").strip().lower()
    )
    if require_proxy != "true":
        raise ValueError(
            "CLIPFETCH_REQUIRE_EGRESS_PROXY must be 'true' in single-container mode."
        )

    egress_proxy_url = f"http://{proxy_host}:{proxy_port}"
    configured_proxy = source.get("CLIPFETCH_EGRESS_PROXY", "").strip()
    if configured_proxy and configured_proxy != egress_proxy_url:
        logger.warning(
            "Overriding CLIPFETCH_EGRESS_PROXY=%s with %s for single-container mode.",
            configured_proxy,
            egress_proxy_url,
        )

    return SupervisorConfig(
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        api_host=DEFAULT_API_HOST,
        api_port=api_port,
        egress_proxy_url=egress_proxy_url,
    )


def build_proxy_command() -> list[str]:
    """Return the command to launch the filtering proxy subprocess."""
    return [sys.executable, "-m", "app.services.filtering_proxy"]


def build_api_command(config: SupervisorConfig) -> list[str]:
    """Return the command to launch the FastAPI Uvicorn server."""
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        config.api_host,
        "--port",
        str(config.api_port),
        "--workers",
        "1",
    ]


def build_child_environments(
    config: SupervisorConfig,
    base_env: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build isolated child environments for the proxy and API processes."""
    source = dict(base_env if base_env is not None else os.environ)

    proxy_env = dict(source)
    proxy_env["CLIPFETCH_PROXY_BIND"] = config.proxy_host
    proxy_env["CLIPFETCH_PROXY_PORT"] = str(config.proxy_port)

    api_env = dict(source)
    for var_name in _API_STRIPPED_PROXY_ENV_VARS:
        api_env.pop(var_name, None)
        api_env.pop(var_name.lower(), None)
    api_env["CLIPFETCH_EGRESS_PROXY"] = config.egress_proxy_url
    api_env["CLIPFETCH_REQUIRE_EGRESS_PROXY"] = "true"

    return proxy_env, api_env


def probe_proxy_ready(host: str, port: int, timeout: float = 0.5) -> bool:
    """Verify the CONNECT proxy socket is listening and responding to HTTP."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            # Non-CONNECT probes are rejected locally without DNS or upstream IO.
            sock.sendall(b"OPTIONS * HTTP/1.1\r\n\r\n")
            response = sock.recv(64)
            return response.startswith(b"HTTP/1.1 403 ")
    except (OSError, TimeoutError):
        return False


def wait_for_proxy_ready(
    host: str,
    port: int,
    process: subprocess.Popen[bytes] | subprocess.Popen[str],
    timeout: float = PROXY_READY_TIMEOUT,
    poll_interval: float = PROXY_POLL_INTERVAL,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Block until the proxy passes readiness or exits/times out."""
    deadline = time.monotonic() + timeout
    while True:
        if should_stop is not None and should_stop():
            raise InterruptedError("Startup interrupted by shutdown signal.")

        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(
                f"Filtering proxy exited before becoming ready (exit code {returncode})."
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        if probe_proxy_ready(host, port, timeout=min(0.5, remaining)):
            return

        sleep_for = min(poll_interval, max(0.0, deadline - time.monotonic()))
        if sleep_for > 0:
            time.sleep(sleep_for)

    raise TimeoutError(
        f"Filtering proxy on {host}:{port} did not become ready within {timeout:.1f}s."
    )


def _kill_process_group(pgid: int, sig: int) -> bool:
    """Best-effort signal delivery to a child's process group."""
    if pgid <= 0 or not hasattr(os, "killpg"):
        return False
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _reap_process_group_children(pgid: int) -> None:
    """Reap any exited orphaned descendants in a child's process group."""
    if pgid <= 0 or not hasattr(os, "waitpid"):
        return
    while True:
        try:
            pid, _ = os.waitpid(-pgid, os.WNOHANG)
        except (ChildProcessError, OSError):
            break
        if pid == 0:
            break


def _has_live_process_in_proc(pgid: int) -> bool | None:
    """Inspect /proc to distinguish live group members from unreaped zombies.

    Returns None when /proc process status is unavailable.
    """
    if not os.path.isdir("/proc") or not os.access("/proc/self/stat", os.R_OK):
        return None
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None

    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(
                f"/proc/{entry}/stat", encoding="utf-8", errors="replace"
            ) as handle:
                stat_text = handle.read()
        except OSError:
            continue
        rparen = stat_text.rfind(")")
        if rparen == -1:
            continue
        fields = stat_text[rparen + 2 :].split()
        if len(fields) < 3:
            continue
        state = fields[0]
        try:
            proc_pgid = int(fields[2])
        except ValueError:
            continue
        if proc_pgid == pgid and state not in ("Z", "X"):
            return True
    return False


def _process_group_exists(pgid: int) -> bool:
    """Return True if any live process remains in the given process group."""
    if pgid <= 0 or not hasattr(os, "killpg"):
        return False
    _reap_process_group_children(pgid)
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    live_in_proc = _has_live_process_in_proc(pgid)
    if live_in_proc is not None:
        return live_in_proc
    return True


def _wait_for_process_group_exit(
    pgid: int,
    deadline: float,
    poll_interval: float = 0.05,
) -> bool:
    """Wait until a process group disappears or the deadline expires."""
    while _process_group_exists(pgid):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_interval, remaining))
    return True


def _terminate_process(
    process: subprocess.Popen[bytes] | subprocess.Popen[str] | None,
    name: str,
    timeout: float,
) -> int | None:
    """Gracefully terminate a child process group, escalating to SIGKILL if needed."""
    if process is None:
        return None

    pgid = process.pid
    grace_timeout = max(0.0, timeout)
    deadline = time.monotonic() + grace_timeout

    existing_code = process.poll()
    if existing_code is not None:
        if not _process_group_exists(pgid):
            return existing_code
        logger.info(
            "%s (pid=%d) already exited (code=%d); stopping remaining process group.",
            name,
            pgid,
            existing_code,
        )
        _kill_process_group(pgid, signal.SIGTERM)
        if not _wait_for_process_group_exit(pgid, deadline):
            logger.warning(
                "%s process group (pgid=%d) did not exit within %.1fs; sending SIGKILL.",
                name,
                pgid,
                grace_timeout,
            )
            _kill_process_group(pgid, signal.SIGKILL)
            _wait_for_process_group_exit(pgid, time.monotonic() + 5.0, poll_interval=0.02)
        return existing_code

    logger.info("Stopping %s (pid=%d)...", name, pgid)
    if not _kill_process_group(pgid, signal.SIGTERM):
        try:
            process.terminate()
        except OSError:
            pass

    try:
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        logger.warning(
            "%s (pid=%d) did not exit within %.1fs; sending SIGKILL.",
            name,
            pgid,
            grace_timeout,
        )
        if not _kill_process_group(pgid, signal.SIGKILL):
            try:
                process.kill()
            except OSError:
                pass
        try:
            returncode = process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            logger.error("Timed out waiting for %s (pid=%d) after SIGKILL.", name, pgid)
            return -signal.SIGKILL
        _wait_for_process_group_exit(pgid, time.monotonic() + 5.0, poll_interval=0.02)
        return returncode

    if _process_group_exists(pgid) and not _wait_for_process_group_exit(pgid, deadline):
        logger.warning(
            "%s (pid=%d) exited (code=%d) but process group still active after %.1fs; sending SIGKILL.",
            name,
            pgid,
            returncode,
            grace_timeout,
        )
        _kill_process_group(pgid, signal.SIGKILL)
        _wait_for_process_group_exit(pgid, time.monotonic() + 5.0, poll_interval=0.02)
    return returncode


def _reap_orphaned_children() -> None:
    """Reap any orphaned descendant processes when running as PID 1."""
    if not hasattr(os, "waitpid"):
        return
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            break


def run_supervisor(
    config: SupervisorConfig | None = None,
    *,
    base_env: Mapping[str, str] | None = None,
    proxy_command: list[str] | None = None,
    api_command: list[str] | None = None,
    ready_timeout: float = PROXY_READY_TIMEOUT,
    api_shutdown_timeout: float = API_SHUTDOWN_TIMEOUT,
    proxy_shutdown_timeout: float = PROXY_SHUTDOWN_TIMEOUT,
    monitor_interval: float = MONITOR_POLL_INTERVAL,
    stop_event: threading.Event | None = None,
) -> int:
    """Start filtering_proxy, verify readiness, then run and supervise FastAPI."""
    resolved_config = config or load_supervisor_config(base_env)
    proxy_env, api_env = build_child_environments(resolved_config, base_env)

    shutdown_signal: int | None = None
    previous_handlers: dict[int, object] = {}
    is_main_thread = threading.current_thread() is threading.main_thread()

    if is_main_thread:
        def _handle_signal(signum: int, _frame: object) -> None:
            nonlocal shutdown_signal
            if shutdown_signal is None:
                shutdown_signal = signum

        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _handle_signal)

    def _is_stopping() -> bool:
        return shutdown_signal is not None or (
            stop_event is not None and stop_event.is_set()
        )

    proxy_proc: subprocess.Popen[bytes] | None = None
    api_proc: subprocess.Popen[bytes] | None = None

    try:
        logger.info(
            "Starting filtering proxy on %s:%d",
            resolved_config.proxy_host,
            resolved_config.proxy_port,
        )
        proxy_proc = subprocess.Popen(
            proxy_command or build_proxy_command(),
            env=proxy_env,
            start_new_session=True,
        )

        wait_for_proxy_ready(
            resolved_config.proxy_host,
            resolved_config.proxy_port,
            proxy_proc,
            timeout=ready_timeout,
            should_stop=_is_stopping,
        )
        logger.info(
            "Filtering proxy is ready on %s:%d",
            resolved_config.proxy_host,
            resolved_config.proxy_port,
        )

        if _is_stopping():
            return 0

        logger.info(
            "Starting FastAPI backend on %s:%d (proxy=%s)",
            resolved_config.api_host,
            resolved_config.api_port,
            resolved_config.egress_proxy_url,
        )
        api_proc = subprocess.Popen(
            api_command or build_api_command(resolved_config),
            env=api_env,
            start_new_session=True,
        )

        while True:
            if _is_stopping():
                logger.info(
                    "Shutdown requested (signal=%s); stopping services cleanly.",
                    shutdown_signal,
                )
                return 0

            proxy_rc = proxy_proc.poll()
            if proxy_rc is not None:
                logger.error(
                    "Filtering proxy exited unexpectedly (code=%s); "
                    "stopping FastAPI to prevent unproxied fallback.",
                    proxy_rc,
                )
                return proxy_rc if proxy_rc != 0 else 1

            api_rc = api_proc.poll()
            if api_rc is not None:
                logger.error(
                    "FastAPI exited unexpectedly (code=%s); stopping filtering proxy.",
                    api_rc,
                )
                return api_rc if api_rc != 0 else 1

            if stop_event is not None:
                stop_event.wait(monitor_interval)
            else:
                time.sleep(monitor_interval)

    except InterruptedError:
        logger.info("Supervisor startup interrupted; shutting down.")
        return 0
    except Exception as exc:
        logger.error("Supervisor failure: %s", exc)
        return 1
    finally:
        # Stop API first so no unproxied requests occur while draining.
        _terminate_process(api_proc, "FastAPI", api_shutdown_timeout)
        _terminate_process(proxy_proc, "filtering_proxy", proxy_shutdown_timeout)
        _reap_orphaned_children()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def main() -> int:
    """CLI entrypoint for the single-container supervisor."""
    logging.basicConfig(
        level=os.getenv("CLIPFETCH_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    try:
        config = load_supervisor_config()
    except ValueError as exc:
        logger.error("Invalid supervisor configuration: %s", exc)
        return 1
    return run_supervisor(config)


if __name__ == "__main__":
    raise SystemExit(main())
