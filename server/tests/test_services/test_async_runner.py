import asyncio
from types import SimpleNamespace

import pytest

from app.core import redis as redis_module
from app.db import session as session_module
from app.tasks.async_runner import run_async


def test_run_async_returns_value_and_releases_resources(monkeypatch):
    disposed = []

    async def fake_dispose():
        disposed.append(asyncio.get_running_loop())

    monkeypatch.setattr(session_module, "engine", SimpleNamespace(dispose=fake_dispose))
    redis_module._redis_client.cache_clear()
    client = redis_module._redis_client()  # populate the lru_cache like a real task would

    async def work():
        return asyncio.get_running_loop()

    loop = run_async(work())
    assert disposed == [loop]  # disposed inside the loop that used the pool
    assert redis_module._redis_client.cache_info().currsize == 0
    assert redis_module._redis_client() is not client  # next task gets a fresh client
    redis_module._redis_client.cache_clear()


def test_run_async_releases_resources_when_task_fails(monkeypatch):
    disposed = []

    async def fake_dispose():
        disposed.append(True)

    monkeypatch.setattr(session_module, "engine", SimpleNamespace(dispose=fake_dispose))

    async def boom():
        raise RuntimeError("task failed")

    with pytest.raises(RuntimeError, match="task failed"):
        run_async(boom())
    assert disposed == [True]


def test_each_call_gets_its_own_loop(monkeypatch):
    async def fake_dispose():
        return None

    monkeypatch.setattr(session_module, "engine", SimpleNamespace(dispose=fake_dispose))

    async def loop_id():
        return id(asyncio.get_running_loop())

    first, second = run_async(loop_id()), run_async(loop_id())
    assert isinstance(first, int) and isinstance(second, int)
