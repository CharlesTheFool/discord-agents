"""
Retry Logic - Exponential backoff for API calls

Handles transient failures with intelligent retry strategies.
"""

import asyncio
import logging
from typing import Callable, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class RetryConfig:
    """Configuration for retry behavior"""
    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


class RetryableError(Exception):
    """Marks an error as retryable"""
    pass


class NonRetryableError(Exception):
    """Marks an error as non-retryable"""
    pass


async def retry_with_backoff(
    func: Callable,
    *args,
    config: Optional[RetryConfig] = None,
    error_context: str = "",
    **kwargs
) -> Any:
    """
    Execute function with exponential backoff retry.

    Raises the last exception if all retries fail.
    """
    if config is None:
        config = RetryConfig()

    last_exception = None
    delay = config.initial_delay

    for attempt in range(1, config.max_attempts + 1):
        try:
            result = await func(*args, **kwargs)
            if attempt > 1:
                logger.info(
                    f"Retry succeeded on attempt {attempt}/{config.max_attempts} "
                    f"for {error_context}"
                )
            return result

        except NonRetryableError as e:
            logger.error(f"Non-retryable error in {error_context}: {e}")
            raise

        except Exception as e:
            last_exception = e

            if attempt == config.max_attempts:
                logger.error(
                    f"All {config.max_attempts} attempts failed for {error_context}: {e}"
                )
                break

            # Calculate next delay with optional jitter
            current_delay = min(delay, config.max_delay)
            if config.jitter:
                import random
                current_delay *= (0.5 + random.random())

            logger.warning(
                f"Attempt {attempt}/{config.max_attempts} failed for {error_context}: {e}. "
                f"Retrying in {current_delay:.1f}s..."
            )

            await asyncio.sleep(current_delay)
            delay *= config.exponential_base

    raise last_exception


def is_retryable_error(error: Exception) -> bool:
    """
    Determine if an error should be retried based on type and message.
    Default: retry unless explicitly marked non-retryable.
    """
    if isinstance(error, RetryableError):
        return True
    if isinstance(error, NonRetryableError):
        return False

    error_str = str(error).lower()

    # Network/connection errors - retryable
    retryable_patterns = [
        "timeout", "connection", "network", "temporary",
        "rate limit", "503", "502", "500",
    ]

    # Permanent errors - not retryable
    non_retryable_patterns = [
        "invalid api key", "authentication", "forbidden",
        "401", "403", "404",
    ]

    for pattern in non_retryable_patterns:
        if pattern in error_str:
            return False

    for pattern in retryable_patterns:
        if pattern in error_str:
            return True

    # Default: retry most errors
    return True


async def retry_with_circuit_breaker(
    func: Callable,
    *args,
    config: Optional[RetryConfig] = None,
    circuit_breaker: Optional['CircuitBreaker'] = None,
    error_context: str = "",
    **kwargs
) -> Any:
    """Execute function with retry and optional circuit breaker"""
    # Check circuit breaker first
    if circuit_breaker and not circuit_breaker.can_proceed():
        raise NonRetryableError(f"Circuit breaker open for {error_context}")

    try:
        result = await retry_with_backoff(
            func, *args, config=config, error_context=error_context, **kwargs
        )

        if circuit_breaker:
            circuit_breaker.record_success()

        return result

    except Exception as e:
        if circuit_breaker:
            circuit_breaker.record_failure()
        raise


class CircuitBreaker:
    """
    Simple circuit breaker pattern.

    Opens circuit after threshold failures, closes after timeout.
    States: closed -> open -> half_open -> closed
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout_seconds: float = 60.0,
        success_threshold: int = 2
    ):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.success_threshold = success_threshold

        self.failure_count = 0
        self.success_count = 0
        self.state = "closed"
        self.opened_at = None

    def can_proceed(self) -> bool:
        """Check if requests should proceed"""
        if self.state == "closed":
            return True

        if self.state == "open":
            # Check if timeout elapsed - transition to half_open
            if self.opened_at:
                elapsed = (datetime.utcnow() - self.opened_at).total_seconds()
                if elapsed >= self.timeout_seconds:
                    logger.info("Circuit breaker entering half-open state")
                    self.state = "half_open"
                    self.success_count = 0
                    return True
            return False

        if self.state == "half_open":
            return True

        return False

    def record_success(self):
        """Record successful request"""
        if self.state == "half_open":
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                logger.info("Circuit breaker closing (threshold reached)")
                self.state = "closed"
                self.failure_count = 0
                self.success_count = 0

        elif self.state == "closed":
            # Gradually reduce failure count on success
            self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self):
        """Record failed request"""
        if self.state == "closed":
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                logger.warning(
                    f"Circuit breaker opening (threshold: {self.failure_count})"
                )
                self.state = "open"
                self.opened_at = datetime.utcnow()

        elif self.state == "half_open":
            logger.warning("Circuit breaker reopening (failure in half-open)")
            self.state = "open"
            self.opened_at = datetime.utcnow()
            self.success_count = 0
