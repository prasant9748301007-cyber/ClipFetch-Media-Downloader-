import asyncio
import threading
from pathlib import Path
import pytest

from app.services import download_service as service


TEST_URL = "https://www.tiktok.com/@user/video/123456"


def test_successful_download_releases_semaphore(monkeypatch):
    monkeypatch.setattr(service, "_validate_url", lambda url: None)
    monkeypatch.setattr(
        service,
        "_DOWNLOAD_SEMAPHORE",
        asyncio.Semaphore(1),
    )

    def fake_download(url, format_id, output_dir):
        output = output_dir / "test.mp4"
        output.write_bytes(b"test media")
        return output

    monkeypatch.setattr(service, "_download_media", fake_download)

    async def run_test():
        path, temporary_directory = (
            await service.download_authorized_media(TEST_URL, "best")
        )

        assert path.is_file()

        await asyncio.wait_for(
            service._DOWNLOAD_SEMAPHORE.acquire(),
            timeout=0.2,
        )

        service._DOWNLOAD_SEMAPHORE.release()
        temporary_directory.cleanup()

    asyncio.run(run_test())


def test_timeout_keeps_slot_until_worker_finishes(monkeypatch):
    monkeypatch.setattr(service, "_validate_url", lambda url: None)
    monkeypatch.setattr(service, "DOWNLOAD_TIMEOUT", 0.05)
    monkeypatch.setattr(
        service,
        "_DOWNLOAD_SEMAPHORE",
        asyncio.Semaphore(1),
    )

    worker_started = threading.Event()
    finish_worker = threading.Event()

    def slow_download(url, format_id, output_dir):
        worker_started.set()
        finish_worker.wait(timeout=2)

        output = output_dir / "test.mp4"
        output.write_bytes(b"test media")
        return output

    monkeypatch.setattr(service, "_download_media", slow_download)

    async def run_test():
        with pytest.raises(
            service.DownloadServiceError,
            match="timed out",
        ):
            await service.download_authorized_media(TEST_URL, "best")

        assert worker_started.is_set()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                service._DOWNLOAD_SEMAPHORE.acquire(),
                timeout=0.05,
            )

        finish_worker.set()

        await asyncio.wait_for(
            service._DOWNLOAD_SEMAPHORE.acquire(),
            timeout=1,
        )

        service._DOWNLOAD_SEMAPHORE.release()

    asyncio.run(run_test())


def test_maximum_two_concurrent_downloads(monkeypatch):
    monkeypatch.setattr(service, "_validate_url", lambda url: None)
    monkeypatch.setattr(service, "DOWNLOAD_QUEUE_TIMEOUT", 0.1)
    monkeypatch.setattr(
        service,
        "_DOWNLOAD_SEMAPHORE",
        asyncio.Semaphore(2),
    )

    lock = threading.Lock()
    two_workers_started = threading.Event()
    finish_workers = threading.Event()

    active_workers = 0
    maximum_active_workers = 0

    def fake_download(url, format_id, output_dir):
        nonlocal active_workers, maximum_active_workers

        with lock:
            active_workers += 1
            maximum_active_workers = max(
                maximum_active_workers,
                active_workers,
            )

            if active_workers == 2:
                two_workers_started.set()

        try:
            finish_workers.wait(timeout=2)

            output = output_dir / "test.mp4"
            output.write_bytes(b"test media")
            return output

        finally:
            with lock:
                active_workers -= 1

    monkeypatch.setattr(service, "_download_media", fake_download)

    async def run_test():
        first = asyncio.create_task(
            service.download_authorized_media(TEST_URL, "best")
        )
        second = asyncio.create_task(
            service.download_authorized_media(TEST_URL, "best")
        )

        await asyncio.wait_for(
            asyncio.to_thread(two_workers_started.wait),
            timeout=1,
        )

        third = asyncio.create_task(
            service.download_authorized_media(TEST_URL, "best")
        )

        with pytest.raises(
            service.DownloadServiceError,
            match="server is busy",
        ):
            await third

        finish_workers.set()

        results = await asyncio.gather(first, second)

        assert maximum_active_workers == 2

        for path, temporary_directory in results:
            assert path.is_file()
            temporary_directory.cleanup()

    asyncio.run(run_test())

def test_temporary_directory_failure_releases_semaphore(monkeypatch):
    monkeypatch.setattr(
        service,
        "_validate_url",
        lambda url: None,
    )
    monkeypatch.setattr(
        service,
        "_DOWNLOAD_SEMAPHORE",
        asyncio.Semaphore(1),
    )

    def fail_directory():
        raise OSError("Temporary directory creation failed")

    monkeypatch.setattr(
        service,
        "_create_temporary_directory",
        fail_directory,
    )

    async def run_test():
        with pytest.raises(OSError):
            await service.download_authorized_media(TEST_URL, "best")

        await asyncio.wait_for(
            service._DOWNLOAD_SEMAPHORE.acquire(),
            timeout=0.2,
        )

        service._DOWNLOAD_SEMAPHORE.release()

    asyncio.run(run_test())


def test_download_worker_failure_cleans_directory_and_releases_slot(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "_validate_url",
        lambda url: None,
    )
    monkeypatch.setattr(
        service,
        "_DOWNLOAD_SEMAPHORE",
        asyncio.Semaphore(1),
    )

    created_directories = []

    original_create_directory = service._create_temporary_directory

    def tracked_directory():
        directory = original_create_directory()
        created_directories.append(directory)
        return directory

    def fail_worker(url, format_id, output_dir):
        raise RuntimeError("Worker creation failed")

    monkeypatch.setattr(
        service,
        "_create_temporary_directory",
        tracked_directory,
    )
    monkeypatch.setattr(
        service,
        "_create_download_worker",
        fail_worker,
    )

    async def run_test():
        with pytest.raises(RuntimeError):
            await service.download_authorized_media(TEST_URL, "best")

        assert len(created_directories) == 1
        assert not Path(created_directories[0].name).exists()

        await asyncio.wait_for(
            service._DOWNLOAD_SEMAPHORE.acquire(),
            timeout=0.2,
        )

        service._DOWNLOAD_SEMAPHORE.release()

    asyncio.run(run_test())
