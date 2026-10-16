"""Cross-process WebSocket routing: presence + Redis pub/sub between two managers."""
import asyncio
import uuid

import pytest
import pytest_asyncio
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis

from app.websocket.manager import PRESENCE_PREFIX, ConnectionManager


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, message):
        self.sent.append(message)

    async def close(self, code=1000):
        self.closed = True


@pytest_asyncio.fixture
async def pair():
    server = FakeServer()
    redis = FakeRedis(server=server, decode_responses=True)

    async def factory():
        return redis

    holder = ConnectionManager(redis_factory=factory)  # uvicorn worker that owns the socket
    sender = ConnectionManager(redis_factory=factory)  # another worker / the celery process
    await holder.start()
    await asyncio.sleep(0.05)  # let the subscriber attach
    yield holder, sender, redis
    await holder.stop()
    await redis.flushall()
    await redis.aclose()


async def _eventually(predicate, timeout=2.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


@pytest.mark.asyncio
async def test_message_reaches_socket_held_by_another_process(pair):
    holder, sender, redis = pair
    device, ws = uuid.uuid4(), FakeWS()
    await holder.connect(device, ws)

    assert sender.is_connected(device) is False  # not local to the sender...
    assert await sender.is_online(device) is True  # ...but online cluster-wide
    assert await sender.send_to_device(device, {"type": "command", "data": {"x": 1}}) is True

    assert await _eventually(lambda: ws.sent)
    assert ws.sent == [{"type": "command", "data": {"x": 1}}]


@pytest.mark.asyncio
async def test_unknown_device_is_offline_and_not_sent(pair):
    _, sender, _ = pair
    device = uuid.uuid4()
    assert await sender.is_online(device) is False
    assert await sender.send_to_device(device, {"type": "command"}) is False


@pytest.mark.asyncio
async def test_disconnect_clears_presence_but_not_a_newer_claim(pair):
    holder, sender, redis = pair
    device = uuid.uuid4()
    await holder.connect(device, FakeWS())
    assert await redis.get(PRESENCE_PREFIX + str(device)) == holder._instance_id

    # Device reconnected through another worker before we noticed the old drop.
    await redis.set(PRESENCE_PREFIX + str(device), sender._instance_id)
    await holder.disconnect(device)
    assert await redis.get(PRESENCE_PREFIX + str(device)) == sender._instance_id

    await redis.set(PRESENCE_PREFIX + str(device), holder._instance_id)
    await holder.connect(device, FakeWS())
    await holder.disconnect(device)
    assert await redis.get(PRESENCE_PREFIX + str(device)) is None


@pytest.mark.asyncio
async def test_online_among_and_cluster_count(pair):
    holder, sender, _ = pair
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await holder.connect(a, FakeWS())
    await holder.connect(b, FakeWS())
    assert await sender.online_among([a, b, c]) == {a, b}
    assert await sender.cluster_connection_count() == 2
    assert await holder.cluster_connection_count() == 2


@pytest.mark.asyncio
async def test_local_send_does_not_use_the_bus(pair):
    holder, _, _ = pair
    device, ws = uuid.uuid4(), FakeWS()
    await holder.connect(device, ws)
    assert await holder.send_to_device(device, {"type": "ping"}) is True
    assert ws.sent == [{"type": "ping"}]
