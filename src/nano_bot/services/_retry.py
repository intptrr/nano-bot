"""Shared retry helpers for service clients."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_BACKOFF = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0


def _log_retry(
    logger: logging.Logger,
    description: str,
    exc: BaseException,
    attempt: int,
    max_attempts: int,
) -> None:
    logger.warning(
        "%s failed (%s); retry %d/%d",
        description,
        exc,
        attempt,
        max_attempts,
    )


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    logger: logging.Logger,
    description: str,
    retry_exceptions: tuple[type[BaseException], ...],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> T:
    """Run an async callable with exponential-backoff retries."""
    backoff = initial_backoff
    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except retry_exceptions as exc:
            if attempt >= max_attempts:
                raise
            _log_retry(logger, description, exc, attempt, max_attempts)
            await asyncio.sleep(backoff)
            backoff *= backoff_factor
    raise RuntimeError("unreachable")  # pragma: no cover


def retry_sync(
    func: Callable[[], T],
    *,
    logger: logging.Logger,
    description: str,
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> T:
    """Run a sync callable with exponential-backoff retries."""
    backoff = initial_backoff
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except retry_exceptions as exc:
            if attempt >= max_attempts:
                raise
            _log_retry(logger, description, exc, attempt, max_attempts)
            time.sleep(backoff)
            backoff *= backoff_factor
    raise RuntimeError("unreachable")  # pragma: no cover
