"""
Minimal test for retry logic
"""

import asyncio
import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.retry_logic import (
    retry_with_backoff,
    RetryConfig,
    RetryableError,
    NonRetryableError,
    CircuitBreaker,
    is_retryable_error
)


class CallCounter:
    """Helper to count function calls"""
    def __init__(self):
        self.count = 0

    async def fail_twice_then_succeed(self):
        """Fails first two attempts, succeeds on third"""
        self.count += 1
        if self.count < 3:
            raise RetryableError(f"Attempt {self.count} failed")
        return "success"

    async def always_fail(self):
        """Always raises error"""
        self.count += 1
        raise RetryableError(f"Attempt {self.count} failed")


@pytest.mark.asyncio
async def test_retry_success():
    """Test successful retry after failures"""
    counter = CallCounter()
    config = RetryConfig(max_attempts=3, initial_delay=0.01)

    result = await retry_with_backoff(
        counter.fail_twice_then_succeed,
        config=config,
        error_context="test"
    )

    assert result == "success"
    assert counter.count == 3
    print("✓ Retry success test passed")


@pytest.mark.asyncio
async def test_retry_exhaustion():
    """Test that retry eventually gives up"""
    counter = CallCounter()
    config = RetryConfig(max_attempts=3, initial_delay=0.01)

    with pytest.raises(RetryableError):
        await retry_with_backoff(
            counter.always_fail,
            config=config,
            error_context="test"
        )

    assert counter.count == 3
    print("✓ Retry exhaustion test passed")


@pytest.mark.asyncio
async def test_non_retryable_error():
    """Test that non-retryable errors fail immediately"""
    async def fail_non_retryable():
        raise NonRetryableError("Auth failed")

    config = RetryConfig(max_attempts=3)

    with pytest.raises(NonRetryableError):
        await retry_with_backoff(
            fail_non_retryable,
            config=config,
            error_context="test"
        )

    print("✓ Non-retryable error test passed")


def test_error_classification():
    """Test error classification logic"""
    # Retryable errors
    assert is_retryable_error(Exception("Connection timeout"))
    assert is_retryable_error(Exception("503 Service Unavailable"))
    assert is_retryable_error(RetryableError("Custom retryable"))

    # Non-retryable errors
    assert not is_retryable_error(Exception("401 Unauthorized"))
    assert not is_retryable_error(Exception("Invalid API key"))
    assert not is_retryable_error(NonRetryableError("Custom non-retryable"))

    print("✓ Error classification test passed")


@pytest.mark.asyncio
async def test_circuit_breaker():
    """Test circuit breaker functionality"""
    breaker = CircuitBreaker(failure_threshold=3, timeout_seconds=0.1, success_threshold=2)

    # Initially closed
    assert breaker.can_proceed()
    assert breaker.state == "closed"

    # Record failures
    for _ in range(3):
        breaker.record_failure()

    # Should be open
    assert not breaker.can_proceed()
    assert breaker.state == "open"

    # Wait for timeout
    await asyncio.sleep(0.15)

    # Should be half-open
    assert breaker.can_proceed()
    assert breaker.state == "half_open"

    # Record successes to close
    breaker.record_success()
    breaker.record_success()

    assert breaker.state == "closed"
    print("✓ Circuit breaker test passed")


if __name__ == "__main__":
    asyncio.run(test_retry_success())
    asyncio.run(test_retry_exhaustion())
    asyncio.run(test_non_retryable_error())
    test_error_classification()
    asyncio.run(test_circuit_breaker())
    print("\n✅ All retry logic tests passed!")
