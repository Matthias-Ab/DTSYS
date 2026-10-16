import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Dict

from fastapi import WebSocket
from redis.asyncio import Redis

from app.core.logging import get_logger

log = get_logger(__name__)

# Agent WebSockets are held in the memory of one uvicorn worker, but API
# requests and Celery tasks (scheduled commands) run in other processes. To make
# "is this device online / send it a message" work cluster-wide:
#   - each connection registers a presence key in Redis (TTL, refreshed), and
#   - messages for a device held by another process go over a Redis channel
#     that every worker listens on; the worker holding the socket delivers it.
PRESENCE_PREFIX = "ws:presence:"
PRESENCE_TTL_SECS = 90
PRESENCE_REFRESH_SECS = 30
BUS_CHANNEL = "dtsys:ws:outbound"


class ConnectionManager:
    """Tracks all active device WebSocket connections."""

    def __init__(self, redis_factory: Callable[[], Awaitable[Redis]] | None = None):
        # device_id (UUID) -> WebSocket
        self._connections: Dict[uuid.UUID, WebSocket] = {}
        self._metadata: Dict[uuid.UUID, dict] = {}
        self._lock = asyncio.Lock()
        self._instance_id = uuid.uuid4().hex
        self._redis_factory = redis_factory
        self._tasks: list[asyncio.Task] = []

    # ---- Redis bus -------------------------------------------------------

    async def _redis(self) -> Redis | None:
        factory = self._redis_factory
        if factory is None:
            from app.core.redis import get_redis

            factory = get_redis
        try:
            return await factory()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("ws_bus_redis_unavailable", error=str(exc))
            return None

    async def start(self) -> None:
        """Start the cross-process listener and presence refresher (call from app lifespan)."""
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._listen(), name="ws-bus-listen"),
            asyncio.create_task(self._refresh_presence(), name="ws-presence-refresh"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        redis = await self._redis()
        if redis is not None:
            for device_id in list(self._connections):
                await self._clear_presence(redis, device_id)

    async def _listen(self) -> None:
        while True:
            redis = await self._redis()
            if redis is None:
                await asyncio.sleep(5)
                continue
            pubsub = redis.pubsub()
            try:
                await pubsub.subscribe(BUS_CHANNEL)
                async for item in pubsub.listen():
                    if item.get("type") != "message":
                        continue
                    try:
                        envelope = json.loads(item["data"])
                        device_id = uuid.UUID(envelope["device_id"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if device_id in self._connections:
                        await self._send_local(device_id, envelope["message"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ws_bus_listen_error", error=str(exc))
                await asyncio.sleep(2)
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass

    async def _refresh_presence(self) -> None:
        while True:
            await asyncio.sleep(PRESENCE_REFRESH_SECS)
            redis = await self._redis()
            if redis is None:
                continue
            try:
                for device_id in list(self._connections):
                    await redis.set(PRESENCE_PREFIX + str(device_id), self._instance_id, ex=PRESENCE_TTL_SECS)
            except Exception as exc:
                log.warning("ws_presence_refresh_failed", error=str(exc))

    async def _set_presence(self, device_id: uuid.UUID) -> None:
        redis = await self._redis()
        if redis is None:
            return
        try:
            await redis.set(PRESENCE_PREFIX + str(device_id), self._instance_id, ex=PRESENCE_TTL_SECS)
        except Exception as exc:
            log.warning("ws_presence_set_failed", error=str(exc))

    async def _clear_presence(self, redis: Redis, device_id: uuid.UUID) -> None:
        key = PRESENCE_PREFIX + str(device_id)
        try:
            # Only clear our own claim: the device may already have reconnected elsewhere.
            if await redis.get(key) == self._instance_id:
                await redis.delete(key)
        except Exception as exc:
            log.warning("ws_presence_clear_failed", error=str(exc))

    async def is_online(self, device_id: uuid.UUID) -> bool:
        """True if the device is connected to any server process."""
        if device_id in self._connections:
            return True
        redis = await self._redis()
        if redis is None:
            return False
        try:
            return bool(await redis.exists(PRESENCE_PREFIX + str(device_id)))
        except Exception:
            return False

    async def online_among(self, device_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Subset of device_ids connected anywhere (one Redis round trip)."""
        online = {d for d in device_ids if d in self._connections}
        remaining = [d for d in device_ids if d not in online]
        if not remaining:
            return online
        redis = await self._redis()
        if redis is None:
            return online
        try:
            values = await redis.mget([PRESENCE_PREFIX + str(d) for d in remaining])
            online.update(d for d, value in zip(remaining, values) if value)
        except Exception:
            pass
        return online

    async def cluster_connection_count(self) -> int:
        redis = await self._redis()
        if redis is None:
            return len(self._connections)
        try:
            count = 0
            async for _ in redis.scan_iter(match=PRESENCE_PREFIX + "*", count=500):
                count += 1
            return max(count, len(self._connections))
        except Exception:
            return len(self._connections)

    async def connect(self, device_id: uuid.UUID, websocket: WebSocket, ip: str | None = None) -> None:
        async with self._lock:
            if device_id in self._connections:
                # Disconnect old connection for same device
                old_ws = self._connections[device_id]
                try:
                    await old_ws.close(code=1000)
                except Exception:
                    pass
            self._connections[device_id] = websocket
            self._metadata[device_id] = {
                "connected_since": datetime.now(timezone.utc),
                "ip": ip,
            }
        await self._set_presence(device_id)
        log.info("device_connected", device_id=str(device_id), total=len(self._connections))

    async def disconnect(self, device_id: uuid.UUID) -> None:
        async with self._lock:
            websocket = self._connections.pop(device_id, None)
            self._metadata.pop(device_id, None)
        if websocket is not None:
            try:
                await websocket.close(code=1000)
            except Exception:
                pass
            redis = await self._redis()
            if redis is not None:
                await self._clear_presence(redis, device_id)
        log.info("device_disconnected", device_id=str(device_id), total=len(self._connections))

    async def _send_local(self, device_id: uuid.UUID, message: dict) -> bool:
        ws = self._connections.get(device_id)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception as e:
            log.warning("send_failed", device_id=str(device_id), error=str(e))
            await self.disconnect(device_id)
            return False

    async def send_to_device(self, device_id: uuid.UUID, message: dict) -> bool:
        """Send a JSON message to a device wherever it is connected.

        Returns True if delivered locally or handed to the process that holds the
        connection, False if the device is not connected anywhere.
        """
        if device_id in self._connections:
            return await self._send_local(device_id, message)

        redis = await self._redis()
        if redis is None:
            return False
        try:
            if not await redis.exists(PRESENCE_PREFIX + str(device_id)):
                return False
            receivers = await redis.publish(
                BUS_CHANNEL, json.dumps({"device_id": str(device_id), "message": message}, default=str)
            )
            return receivers > 0
        except Exception as exc:
            log.warning("ws_bus_publish_failed", device_id=str(device_id), error=str(exc))
            return False

    def is_connected(self, device_id: uuid.UUID) -> bool:
        return device_id in self._connections

    @property
    def online_device_ids(self) -> list[uuid.UUID]:
        return list(self._connections.keys())

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def connection_snapshot(self) -> list[dict]:
        snapshot: list[dict] = []
        for device_id, metadata in self._metadata.items():
            snapshot.append(
                {
                    "device_id": device_id,
                    "connected_since": metadata["connected_since"],
                    "ip": metadata.get("ip"),
                }
            )
        return snapshot


# Singleton shared across the app lifecycle
manager = ConnectionManager()
