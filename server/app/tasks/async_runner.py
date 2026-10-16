"""Run async code from synchronous Celery tasks without leaking loop-bound connections.

Each Celery task call needs its own event loop (``asyncio.run``), but the SQLAlchemy
engine and the Redis client are process-wide singletons whose pooled connections
are bound to the loop that created them. Reusing them in the next task's new loop
fails with "another operation is in progress" / "Event loop is closed", which
silently broke every periodic task after its first run in a worker process.

``run_async`` runs the coroutine and then disposes the pool and drops the cached
Redis client *inside the same loop*, so the next task starts clean.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


async def _release_loop_bound_resources() -> None:
    from app.core.redis import _redis_client
    from app.db.session import engine

    try:
        if _redis_client.cache_info().currsize:
            client = _redis_client()
            _redis_client.cache_clear()
            await client.aclose()
    except Exception:
        _redis_client.cache_clear()
    await engine.dispose()


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    async def runner() -> T:
        try:
            return await coro
        finally:
            await _release_loop_bound_resources()

    return asyncio.run(runner())
