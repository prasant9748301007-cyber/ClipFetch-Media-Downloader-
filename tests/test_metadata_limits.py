
import asyncio
import threading

import pytest

from app.services import media_service as service


TEST_URL = "https://www.tiktok.com/@user/video/123456"


def test_successful_metadata_releases_semaphore(monkeypatch):
    monkeypatch.setattr(
        service,
        "_METADATA_SEMAPHORE",
        asyncio.Semaphore(1),
    )

    def fake_extract(url):
        return {
            "title": "Test Video",
            "thumbnail": None,
            "duration": 10,
            "uploader": "Test User",
            "formats": [],
        }

    monkeypatch.setattr(
        service,
        "_extract_metadata",
        fake_extract,
    )

    async def run_test():
        result = await service.get_media_metadata(TEST_URL)

        assert result["title"] == "Test Video"

        await asyncio.wait_for(
            service._METADATA_SEMAPHORE.acquire(),
            timeout=0.2,
        )

        service._METADATA_SEMAPHORE.release()

    asyncio.run(run_test())


def test_timeout_keeps_slot_until_worker_finishes(monkeypatch):
    monkeypatch.setattr(
        service,
        "METADATA_TIMEOUT",
        0.05,
    )
    monkeypatch.setattr(
        service,
        "_METADATA_SEMAPHORE",
        asyncio.Semaphore(1),
    )

    worker_started = threading.Event()
    finish_worker = threading.Event()

    def slow_extract(url):
        worker_started.set()
        finish_worker.wait(timeout=2)

        return {
            "title": "Slow Test",
            "thumbnail": None,
            "duration": 10,
            "uploader": "Test User",
            "formats": [],
        }

    monkeypatch.setattr(
        service,
        "_extract_metadata",
        slow_extract,
    )

    async def run_test():
        with pytest.raises(
            service.MediaServiceError,
            match="timed out",
        ):
            await service.get_media_metadata(TEST_URL)

        assert worker_started.is_set()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                service._METADATA_SEMAPHORE.acquire(),
                timeout=0.05,
            )

        finish_worker.set()

        await asyncio.wait_for(
            service._METADATA_SEMAPHORE.acquire(),
            timeout=1,
        )

        service._METADATA_SEMAPHORE.release()

    asyncio.run(run_test())


def test_maximum_concurrent_metadata_requests(monkeypatch):
    monkeypatch.setattr(
        service,
        "METADATA_QUEUE_TIMEOUT",
        0.1,
    )
    monkeypatch.setattr(
        service,
        "_METADATA_SEMAPHORE",
        asyncio.Semaphore(2),
    )

    lock = threading.Lock()
    two_workers_started = threading.Event()
    finish_workers = threading.Event()

    active_workers = 0
    maximum_active_workers = 0

    def fake_extract(url):
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

            return {
                "title": "Concurrent Test",
                "thumbnail": None,
                "duration": 10,
                "uploader": "Test User",
                "formats": [],
            }

        finally:
            with lock:
                active_workers -= 1

    monkeypatch.setattr(
        service,
        "_extract_metadata",
        fake_extract,
    )

    async def run_test():
        first = asyncio.create_task(
            service.get_media_metadata(TEST_URL)
        )
        second = asyncio.create_task(
            service.get_media_metadata(TEST_URL)
        )

        await asyncio.wait_for(
            asyncio.to_thread(two_workers_started.wait),
            timeout=1,
        )

        third = asyncio.create_task(
            service.get_media_metadata(TEST_URL)
        )

        with pytest.raises(
            service.MediaServiceError,
            match="service is busy",
        ):
            await third

        finish_workers.set()

        results = await asyncio.gather(first, second)

        assert maximum_active_workers == 2

        assert all(
            result["title"] == "Concurrent Test"
            for result in results
        )

    asyncio.run(run_test())
def test_metadata_worker_creation_failure_releases_semaphore(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "_METADATA_SEMAPHORE",
        asyncio.Semaphore(1),
    )

    def fail_worker(url):
        raise RuntimeError("Metadata worker creation failed")

    monkeypatch.setattr(
        service,
        "_create_metadata_worker",
        fail_worker,
    )

    async def run_test():
        with pytest.raises(RuntimeError):
            await service.get_media_metadata(TEST_URL)

        await asyncio.wait_for(
            service._METADATA_SEMAPHORE.acquire(),
            timeout=0.2,
        )

        service._METADATA_SEMAPHORE.release()

    asyncio.run(run_test())
