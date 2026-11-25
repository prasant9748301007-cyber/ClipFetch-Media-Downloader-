import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

from app import supervisor


BACKEND_DIR = str(Path(__file__).resolve().parents[1] / "backend")


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _port_accepts_connections(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _wait_for_health(port: int, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
            try:
                conn.request("GET", "/api/health")
                resp = conn.getresponse()
                body = resp.read()
                if resp.status == 200:
                    return json.loads(body.decode("utf-8"))
            finally:
                conn.close()
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"FastAPI did not become healthy on port {port}.")


def test_load_supervisor_config_defaults():
    config = supervisor.load_supervisor_config({})
    assert config.proxy_host == "127.0.0.1"
    assert config.proxy_port == 3128
    assert config.api_host == "0.0.0.0"
    assert config.api_port == 10000
    assert config.egress_proxy_url == "http://127.0.0.1:3128"


def test_load_supervisor_config_supports_render_port():
    config = supervisor.load_supervisor_config({"PORT": "8080"})
    assert config.api_port == 8080


@pytest.mark.parametrize("bind_host", ["0.0.0.0", "10.0.0.2", "::1", "proxy"])
def test_load_supervisor_config_rejects_non_loopback_proxy_bind(bind_host):
    with pytest.raises(ValueError, match="127.0.0.1"):
        supervisor.load_supervisor_config({"CLIPFETCH_PROXY_BIND": bind_host})


@pytest.mark.parametrize("required", ["false", "0", "no", "invalid"])
def test_load_supervisor_config_rejects_disabled_proxy_requirement(required):
    with pytest.raises(ValueError, match="CLIPFETCH_REQUIRE_EGRESS_PROXY"):
        supervisor.load_supervisor_config({"CLIPFETCH_REQUIRE_EGRESS_PROXY": required})


@pytest.mark.parametrize(
    "env",
    [
        {"PORT": "3128"},
        {"PORT": "0"},
        {"PORT": "65536"},
        {"PORT": "not-a-port"},
        {"CLIPFETCH_PROXY_PORT": "0"},
        {"CLIPFETCH_PROXY_PORT": "70000"},
    ],
)
def test_load_supervisor_config_rejects_invalid_or_conflicting_ports(env):
    with pytest.raises(ValueError):
        supervisor.load_supervisor_config(env)


def test_load_supervisor_config_overrides_external_proxy_url():
    config = supervisor.load_supervisor_config(
        {
            "CLIPFETCH_EGRESS_PROXY": "http://legacy-private-service:3128",
            "CLIPFETCH_REQUIRE_EGRESS_PROXY": "true",
        }
    )
    assert config.egress_proxy_url == "http://127.0.0.1:3128"


def test_build_child_environments_enforces_proxy_settings():
    config = supervisor.SupervisorConfig(
        proxy_host="127.0.0.1",
        proxy_port=3128,
        api_host="0.0.0.0",
        api_port=10000,
        egress_proxy_url="http://127.0.0.1:3128",
    )
    proxy_env, api_env = supervisor.build_child_environments(config, base_env={})

    assert proxy_env["CLIPFETCH_PROXY_BIND"] == "127.0.0.1"
    assert proxy_env["CLIPFETCH_PROXY_PORT"] == "3128"
    assert api_env["CLIPFETCH_EGRESS_PROXY"] == "http://127.0.0.1:3128"
    assert api_env["CLIPFETCH_REQUIRE_EGRESS_PROXY"] == "true"


def test_build_child_environments_strips_proxy_bypass_vars_from_api_env():
    config = supervisor.SupervisorConfig(
        proxy_host="127.0.0.1",
        proxy_port=4128,
        api_host="0.0.0.0",
        api_port=10000,
        egress_proxy_url="http://127.0.0.1:4128",
    )
    bypass_vars = {
        "NO_PROXY": "*",
        "no_proxy": "localhost,127.0.0.1",
        "HTTP_PROXY": "http://10.0.0.1:8080",
        "http_proxy": "http://10.0.0.1:8080",
        "HTTPS_PROXY": "http://10.0.0.1:8443",
        "https_proxy": "http://10.0.0.1:8443",
        "ALL_PROXY": "socks5://10.0.0.1:1080",
        "all_proxy": "socks5://10.0.0.1:1080",
    }
    base_env = {
        **bypass_vars,
        "CLIPFETCH_EGRESS_PROXY": "http://untrusted:9999",
        "CLIPFETCH_REQUIRE_EGRESS_PROXY": "false",
        "CLIPFETCH_FRONTEND_ORIGINS": "http://127.0.0.1:5500",
    }

    _proxy_env, api_env = supervisor.build_child_environments(config, base_env=base_env)

    for key in bypass_vars:
        assert key not in api_env
    assert api_env["CLIPFETCH_EGRESS_PROXY"] == "http://127.0.0.1:4128"
    assert api_env["CLIPFETCH_REQUIRE_EGRESS_PROXY"] == "true"
    assert api_env["CLIPFETCH_FRONTEND_ORIGINS"] == "http://127.0.0.1:5500"


def test_run_supervisor_blocks_inherited_proxy_bypass_vars_in_api_subprocess(tmp_path):
    proxy_port = _reserve_loopback_port()
    api_port = _reserve_loopback_port()
    env_dump_path = tmp_path / "api_env.json"

    config = supervisor.SupervisorConfig(
        proxy_host="127.0.0.1",
        proxy_port=proxy_port,
        api_host="127.0.0.1",
        api_port=api_port,
        egress_proxy_url=f"http://127.0.0.1:{proxy_port}",
    )
    bypass_keys = (
        "NO_PROXY",
        "no_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    )
    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = BACKEND_DIR
    for key in bypass_keys:
        base_env[key] = "http://bypass.invalid:8888"

    api_script = (
        "import json, os, time; from pathlib import Path; "
        f"Path({str(env_dump_path)!r}).write_text(json.dumps(dict(os.environ))); "
        "time.sleep(30)"
    )

    stop_event = threading.Event()
    results: list[int] = []

    def _run():
        results.append(
            supervisor.run_supervisor(
                config,
                base_env=base_env,
                api_command=[sys.executable, "-c", api_script],
                monitor_interval=0.05,
                stop_event=stop_event,
            )
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while not env_dump_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert env_dump_path.exists(), "API stub did not record its child environment."
    finally:
        stop_event.set()
        thread.join(timeout=10.0)

    assert not thread.is_alive()
    assert results == [0]

    captured_env = json.loads(env_dump_path.read_text())
    for key in bypass_keys:
        assert key not in captured_env
    assert captured_env["CLIPFETCH_EGRESS_PROXY"] == f"http://127.0.0.1:{proxy_port}"
    assert captured_env["CLIPFETCH_REQUIRE_EGRESS_PROXY"] == "true"


def test_wait_for_proxy_ready_fails_fast_if_proxy_exits_early():
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
    try:
        port = _reserve_loopback_port()
        with pytest.raises(RuntimeError, match="exit code 3"):
            supervisor.wait_for_proxy_ready("127.0.0.1", port, proc, timeout=5.0)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5.0)


def test_wait_for_proxy_ready_times_out_when_unresponsive():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        port = _reserve_loopback_port()
        with pytest.raises(TimeoutError, match="did not become ready"):
            supervisor.wait_for_proxy_ready(
                "127.0.0.1", port, proc, timeout=0.25, poll_interval=0.05
            )
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)


def test_supervisor_starts_proxy_before_api_and_shuts_down_cleanly():
    proxy_port = _reserve_loopback_port()
    api_port = _reserve_loopback_port()
    config = supervisor.SupervisorConfig(
        proxy_host="127.0.0.1",
        proxy_port=proxy_port,
        api_host="127.0.0.1",
        api_port=api_port,
        egress_proxy_url=f"http://127.0.0.1:{proxy_port}",
    )
    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = BACKEND_DIR
    base_env["CLIPFETCH_FRONTEND_ORIGINS"] = "http://127.0.0.1:5500"

    stop_event = threading.Event()
    results: list[int] = []

    def _run():
        results.append(
            supervisor.run_supervisor(
                config,
                base_env=base_env,
                monitor_interval=0.05,
                stop_event=stop_event,
            )
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        health = _wait_for_health(api_port)
        assert health["status"] == "healthy"
        assert supervisor.probe_proxy_ready("127.0.0.1", proxy_port) is True

        # Verify filtering proxy rejects local loopback CONNECT targets.
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=2.0) as client:
            client.sendall(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")
            assert client.recv(128).startswith(b"HTTP/1.1 403 ")
    finally:
        stop_event.set()
        thread.join(timeout=10.0)

    assert not thread.is_alive()
    assert results == [0]
    assert not _port_accepts_connections("127.0.0.1", api_port)
    assert not _port_accepts_connections("127.0.0.1", proxy_port)


def test_supervisor_does_not_start_api_if_proxy_crashes_at_startup(tmp_path):
    proxy_port = _reserve_loopback_port()
    api_port = _reserve_loopback_port()
    marker = tmp_path / "api_started.txt"
    config = supervisor.SupervisorConfig(
        proxy_host="127.0.0.1",
        proxy_port=proxy_port,
        api_host="127.0.0.1",
        api_port=api_port,
        egress_proxy_url=f"http://127.0.0.1:{proxy_port}",
    )

    rc = supervisor.run_supervisor(
        config,
        base_env=os.environ,
        proxy_command=[sys.executable, "-c", "import sys; sys.exit(7)"],
        api_command=[
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('started')",
        ],
        ready_timeout=2.0,
    )

    assert rc != 0
    assert not marker.exists()


def test_supervisor_stops_api_when_proxy_crashes_mid_run(tmp_path):
    proxy_port = _reserve_loopback_port()
    api_port = _reserve_loopback_port()
    pid_file = tmp_path / "proxy.pid"

    config = supervisor.SupervisorConfig(
        proxy_host="127.0.0.1",
        proxy_port=proxy_port,
        api_host="127.0.0.1",
        api_port=api_port,
        egress_proxy_url=f"http://127.0.0.1:{proxy_port}",
    )
    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = BACKEND_DIR
    base_env["CLIPFETCH_FRONTEND_ORIGINS"] = "http://127.0.0.1:5500"

    proxy_wrapper = (
        "import os; from pathlib import Path; "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        "from app.services.filtering_proxy import serve; serve()"
    )

    results: list[int] = []
    stop_event = threading.Event()

    def _run():
        results.append(
            supervisor.run_supervisor(
                config,
                base_env=base_env,
                proxy_command=[sys.executable, "-c", proxy_wrapper],
                monitor_interval=0.05,
                stop_event=stop_event,
            )
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        health = _wait_for_health(api_port)
        assert health["status"] == "healthy"
        proxy_pid = int(pid_file.read_text().strip())
        os.kill(proxy_pid, signal.SIGKILL)
        thread.join(timeout=10.0)
    finally:
        stop_event.set()
        thread.join(timeout=5.0)

    assert not thread.is_alive()
    assert len(results) == 1 and results[0] != 0
    assert not _port_accepts_connections("127.0.0.1", api_port)
    assert not _port_accepts_connections("127.0.0.1", proxy_port)


def test_supervisor_cli_handles_sigterm_gracefully():
    proxy_port = _reserve_loopback_port()
    api_port = _reserve_loopback_port()
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND_DIR
    env["PORT"] = str(api_port)
    env["CLIPFETCH_PROXY_BIND"] = "127.0.0.1"
    env["CLIPFETCH_PROXY_PORT"] = str(proxy_port)
    env["CLIPFETCH_EGRESS_PROXY"] = f"http://127.0.0.1:{proxy_port}"
    env["CLIPFETCH_REQUIRE_EGRESS_PROXY"] = "true"
    env["CLIPFETCH_FRONTEND_ORIGINS"] = "http://127.0.0.1:5500"

    proc = subprocess.Popen(
        [sys.executable, "-m", "app.supervisor"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        health = _wait_for_health(api_port)
        assert health["status"] == "healthy"
        assert supervisor.probe_proxy_ready("127.0.0.1", proxy_port) is True

        proc.terminate()
        assert proc.wait(timeout=10.0) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)

    assert not _port_accepts_connections("127.0.0.1", api_port)
    assert not _port_accepts_connections("127.0.0.1", proxy_port)


def _is_process_gone_or_zombie(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.is_file():
        stat_text = stat_path.read_text()
        rparen = stat_text.rfind(")")
        if rparen != -1:
            state = stat_text[rparen + 2 : rparen + 3]
            return state in {"Z", "X"}
    return False


def test_terminate_process_kills_stubborn_process_group(tmp_path):
    child_pid_file = tmp_path / "grandchild.pid"
    script = (
        "import os, signal, subprocess, sys, time; "
        "from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )
    proc = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    try:
        deadline = time.monotonic() + 5.0
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        grandchild_pid = int(child_pid_file.read_text().strip())

        supervisor._terminate_process(proc, "stubborn", timeout=0.2)
        assert proc.poll() is not None

        # Grandchild in the same process group must also be terminated.
        kill_deadline = time.monotonic() + 2.0
        terminated = False
        while time.monotonic() < kill_deadline:
            if _is_process_gone_or_zombie(grandchild_pid):
                terminated = True
                break
            time.sleep(0.05)
        assert terminated
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)


def test_supervisor_shutdown_terminates_child_and_descendant_when_leader_exits_first(
    tmp_path,
):
    proxy_port = _reserve_loopback_port()
    api_port = _reserve_loopback_port()
    leader_pid_file = tmp_path / "leader.pid"
    descendant_pid_file = tmp_path / "descendant.pid"

    config = supervisor.SupervisorConfig(
        proxy_host="127.0.0.1",
        proxy_port=proxy_port,
        api_host="127.0.0.1",
        api_port=api_port,
        egress_proxy_url=f"http://127.0.0.1:{proxy_port}",
    )
    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = BACKEND_DIR

    descendant_script = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(30)"
    )
    api_script = (
        "import os, signal, subprocess, sys, time; "
        "from pathlib import Path; "
        "signal.signal(signal.SIGTERM, lambda *_args: sys.exit(0)); "
        f"child = subprocess.Popen([sys.executable, '-c', {descendant_script!r}]); "
        f"Path({str(descendant_pid_file)!r}).write_text(str(child.pid)); "
        f"Path({str(leader_pid_file)!r}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )

    stop_event = threading.Event()
    results: list[int] = []

    def _run():
        results.append(
            supervisor.run_supervisor(
                config,
                base_env=base_env,
                api_command=[sys.executable, "-c", api_script],
                api_shutdown_timeout=0.3,
                proxy_shutdown_timeout=1.0,
                monitor_interval=0.05,
                stop_event=stop_event,
            )
        )

    leader_pid: int | None = None
    descendant_pid: int | None = None
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while (
            not (leader_pid_file.exists() and descendant_pid_file.exists())
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert leader_pid_file.exists() and descendant_pid_file.exists()
        leader_pid = int(leader_pid_file.read_text().strip())
        descendant_pid = int(descendant_pid_file.read_text().strip())
    finally:
        stop_event.set()
        thread.join(timeout=10.0)
        for pid in (leader_pid, descendant_pid):
            if pid is not None and not _is_process_gone_or_zombie(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    assert not thread.is_alive()
    assert results == [0]
    assert leader_pid is not None and _is_process_gone_or_zombie(leader_pid)
    assert descendant_pid is not None and _is_process_gone_or_zombie(descendant_pid)
    assert not _port_accepts_connections("127.0.0.1", proxy_port)


def test_supervisor_shutdown_sends_sigterm_to_descendant_when_leader_exits_gracefully(
    tmp_path,
):
    proxy_port = _reserve_loopback_port()
    api_port = _reserve_loopback_port()
    ready_file = tmp_path / "ready.txt"
    descendant_pid_file = tmp_path / "descendant.pid"
    descendant_sigterm_file = tmp_path / "descendant_sigterm.txt"

    config = supervisor.SupervisorConfig(
        proxy_host="127.0.0.1",
        proxy_port=proxy_port,
        api_host="127.0.0.1",
        api_port=api_port,
        egress_proxy_url=f"http://127.0.0.1:{proxy_port}",
    )
    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = BACKEND_DIR

    descendant_script = (
        "import signal, sys, time\n"
        "from pathlib import Path\n"
        "def _on_term(_sig, _frame):\n"
        f"    Path({str(descendant_sigterm_file)!r}).write_text('sigterm')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, _on_term)\n"
        f"Path({str(ready_file)!r}).write_text('ready')\n"
        "time.sleep(30)\n"
    )
    api_script = (
        "import signal, subprocess, sys, time; "
        "from pathlib import Path; "
        "signal.signal(signal.SIGTERM, lambda *_args: sys.exit(0)); "
        f"child = subprocess.Popen([sys.executable, '-c', {descendant_script!r}]); "
        f"Path({str(descendant_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )

    stop_event = threading.Event()
    results: list[int] = []

    def _run():
        results.append(
            supervisor.run_supervisor(
                config,
                base_env=base_env,
                api_command=[sys.executable, "-c", api_script],
                api_shutdown_timeout=5.0,
                proxy_shutdown_timeout=1.0,
                monitor_interval=0.05,
                stop_event=stop_event,
            )
        )

    descendant_pid: int | None = None
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while (
            not (ready_file.exists() and descendant_pid_file.exists())
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert ready_file.exists()
        descendant_pid = int(descendant_pid_file.read_text().strip())
        shutdown_start = time.monotonic()
    finally:
        stop_event.set()
        thread.join(timeout=10.0)
        if descendant_pid is not None and not _is_process_gone_or_zombie(descendant_pid):
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except OSError:
                pass

    elapsed = time.monotonic() - shutdown_start
    assert not thread.is_alive()
    assert results == [0]
    assert elapsed < 3.0
    assert descendant_sigterm_file.read_text() == "sigterm"
    assert descendant_pid is not None and _is_process_gone_or_zombie(descendant_pid)


def test_supervisor_shutdown_stops_api_before_proxy(tmp_path):
    proxy_port = _reserve_loopback_port()
    api_port = _reserve_loopback_port()
    order_file = tmp_path / "shutdown_order.log"
    api_ready = tmp_path / "api_ready.txt"

    config = supervisor.SupervisorConfig(
        proxy_host="127.0.0.1",
        proxy_port=proxy_port,
        api_host="127.0.0.1",
        api_port=api_port,
        egress_proxy_url=f"http://127.0.0.1:{proxy_port}",
    )
    base_env = dict(os.environ)
    base_env["PYTHONPATH"] = BACKEND_DIR

    proxy_script = (
        "from pathlib import Path\n"
        "from app.services.filtering_proxy import serve\n"
        "serve()\n"
        f"with Path({str(order_file)!r}).open('a') as f:\n"
        "    f.write('proxy\\n')\n"
    )
    api_script = (
        "import signal, sys, time\n"
        "from pathlib import Path\n"
        "from app.supervisor import probe_proxy_ready\n"
        "def _record_and_exit(signum, frame):\n"
        "    time.sleep(0.05)\n"
        f"    proxy_alive = probe_proxy_ready('127.0.0.1', {proxy_port})\n"
        f"    with Path({str(order_file)!r}).open('a') as f:\n"
        "        f.write(f'api:{proxy_alive}\\n')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, _record_and_exit)\n"
        f"Path({str(api_ready)!r}).write_text('ready')\n"
        "time.sleep(30)\n"
    )

    stop_event = threading.Event()
    results: list[int] = []

    def _run():
        results.append(
            supervisor.run_supervisor(
                config,
                base_env=base_env,
                proxy_command=[sys.executable, "-c", proxy_script],
                api_command=[sys.executable, "-c", api_script],
                monitor_interval=0.05,
                stop_event=stop_event,
            )
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while not api_ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert api_ready.exists()
    finally:
        stop_event.set()
        thread.join(timeout=10.0)

    assert not thread.is_alive()
    assert results == [0]
    assert order_file.read_text().splitlines() == ["api:True", "proxy"]


def test_terminate_process_cleans_up_surviving_descendant_after_leader_already_exited(
    tmp_path,
):
    descendant_pid_file = tmp_path / "descendant.pid"
    descendant_script = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(30)"
    )
    leader_script = (
        "import subprocess, sys; "
        "from pathlib import Path; "
        f"child = subprocess.Popen([sys.executable, '-c', {descendant_script!r}]); "
        f"Path({str(descendant_pid_file)!r}).write_text(str(child.pid)); "
        "sys.exit(0)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", leader_script],
        start_new_session=True,
    )
    descendant_pid: int | None = None
    try:
        deadline = time.monotonic() + 5.0
        while not descendant_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        descendant_pid = int(descendant_pid_file.read_text().strip())

        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert proc.poll() == 0

        rc = supervisor._terminate_process(proc, "early-exit", timeout=0.2)
        assert rc == 0
        assert _is_process_gone_or_zombie(descendant_pid)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)
        if descendant_pid is not None and not _is_process_gone_or_zombie(descendant_pid):
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except OSError:
                pass


def test_terminate_process_handles_none_and_already_exited_process():
    assert supervisor._terminate_process(None, "missing", timeout=0.2) is None

    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(4)"],
        start_new_session=True,
    )
    assert proc.wait(timeout=5.0) == 4
    assert supervisor._terminate_process(proc, "exited", timeout=0.2) == 4


def test_terminate_process_handles_missing_killpg_and_killpg_errors(monkeypatch):
    # Platform without os.killpg falls back safely to process.terminate()/kill().
    proc_no_killpg = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    try:
        monkeypatch.delattr(supervisor.os, "killpg", raising=False)
        rc = supervisor._terminate_process(proc_no_killpg, "no-killpg", timeout=1.0)
        assert rc is not None
        assert proc_no_killpg.poll() is not None
    finally:
        if proc_no_killpg.poll() is None:
            proc_no_killpg.kill()
            proc_no_killpg.wait(timeout=5.0)

    # ProcessLookupError and PermissionError from os.killpg are handled safely.
    for exc in (ProcessLookupError("gone"), PermissionError("denied")):
        proc_err = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            def _raise_killpg(_pgid, _sig, error=exc):
                raise error

            monkeypatch.setattr(supervisor.os, "killpg", _raise_killpg, raising=False)
            rc = supervisor._terminate_process(proc_err, "killpg-error", timeout=1.0)
            assert rc is not None
            assert proc_err.poll() is not None
        finally:
            if proc_err.poll() is None:
                proc_err.kill()
                proc_err.wait(timeout=5.0)
