from app.services.retry_policy import (
    exponential_retry_delay,
    get_retry_policy,
)


def test_retry_delay_is_exponential_and_bounded():
    assert exponential_retry_delay(0) == 0.5
    assert exponential_retry_delay(1) == 1.0
    assert exponential_retry_delay(2) == 2.0
    assert exponential_retry_delay(3) == 2.0


def test_retry_policy_uses_finite_attempt_counts():
    policy = get_retry_policy(
        retries=2,
        fragment_retries=1,
        extractor_retries=1,
        file_access_retries=1,
    )

    assert policy["retries"] == 2
    assert policy["fragment_retries"] == 1
    assert policy["extractor_retries"] == 1
    assert policy["file_access_retries"] == 1
    assert set(policy["retry_sleep_functions"]) == {
        "http",
        "fragment",
        "extractor",
        "file_access",
    }
