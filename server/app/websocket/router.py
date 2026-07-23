import asyncio
import json
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timedelta, timezone

from app.core.redis import get_redis
from app.db.session import AsyncSessionLocal, get_db
from app.models.device_config import DeviceConfig
from app.models.device import Device
from app.models.command import Command
from app.models.uptime_event import UptimeEvent
from app.core.security import verify_api_key
from app.websocket.manager import manager
from app.websocket.handler import MessageHandler
from app.websocket.messages import ServerMessageType
from app.core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)
_pending_offline: dict[uuid.UUID, asyncio.Task] = {}
_pending_lock = asyncio.Lock()
MAX_WS_MESSAGE_BYTES = 4 * 1024 * 1024  # 4 MB — accommodates large software inventories and screenshots


async def _ping_loop(websocket: WebSocket, device_id: uuid.UUID, interval: int = 30) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await websocket.send_json({"type": "ping"})
        except Exception as exc:
            log.info("ws_ping_failed", device_id=str(device_id), error=str(exc))
            break


async def _schedule_offline(device_id: uuid.UUID) -> None:
    try:
        await asyncio.sleep(15)
        if manager.is_connected(device_id):
            return
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Device).where(Device.id == device_id).values(status="offline")
            )
            await session.commit()
    except asyncio.CancelledError:
        return
    finally:
        async with _pending_lock:
            _pending_offline.pop(device_id, None)


async def _cancel_pending_offline(device_id: uuid.UUID) -> None:
    async with _pending_lock:
        task = _pending_offline.pop(device_id, None)
    if task:
        task.cancel()


@router.websocket("/ws/device/{device_id}")
async def device_websocket(
    device_id: uuid.UUID,
    websocket: WebSocket,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    client_ip = websocket.client.host if websocket.client else "unknown"

    # Skip rate limiting for loopback — agents on the same host legitimately reconnect
    # frequently (e.g. during restarts / updates). Real abuse from localhost is impossible.
    _is_loopback = client_ip in ("127.0.0.1", "::1", "localhost")
    attempts_key = f"ws_attempts:{client_ip}"
    if not _is_loopback:
        attempts = await redis.incr(attempts_key)
        if attempts == 1:
            await redis.expire(attempts_key, 60)
        if attempts > 30:
            await websocket.close(code=4029)
            log.warning("ws_rate_limited", device_id=str(device_id), ip=client_ip, attempts=attempts)
            return

    # Authenticate device
    result = await db.execute(
        select(Device).where(Device.id == device_id, ~Device.is_revoked)
    )
    device = result.scalar_one_or_none()

    if device is None or not verify_api_key(token, device.api_key_hash):
        await websocket.close(code=4001)
        log.warning("ws_auth_failed", device_id=str(device_id))
        return

    await redis.delete(attempts_key)
    await websocket.accept()
    await manager.connect(device_id, websocket, ip=client_ip)
    await _cancel_pending_offline(device_id)

    # Update device status
    now = datetime.now(timezone.utc)
    await db.execute(
        update(Device)
        .where(Device.id == device_id)
        .values(status="online", last_seen=now)
    )
    last_offline_result = await db.execute(
        select(UptimeEvent)
        .where(UptimeEvent.device_id == device_id, UptimeEvent.event_type == "offline")
        .order_by(UptimeEvent.timestamp.desc())
        .limit(1)
    )
    last_offline = last_offline_result.scalar_one_or_none()
    duration_secs = None
    if last_offline and last_offline.timestamp:
        duration_secs = max(0, int((now - last_offline.timestamp).total_seconds()))
    db.add(UptimeEvent(device_id=device_id, event_type="online", duration_secs=duration_secs))
    await db.commit()

    config_row = await db.get(DeviceConfig, device_id)
    if config_row:
        try:
            await manager.send_to_device(
                device_id,
                {"type": ServerMessageType.CONFIG_UPDATE, "data": config_row.config},
            )
        except Exception as exc:
            log.warning("config_send_failed", device_id=str(device_id), error=str(exc))

    pending_cmds = await db.execute(
        select(Command).where(
            Command.device_id == device_id,
            Command.status == "sent",
            Command.created_at >= datetime.now(timezone.utc) - timedelta(hours=1),
        )
    )
    for cmd in pending_cmds.scalars().all():
        await manager.send_to_device(
            device_id,
            {
                "type": ServerMessageType.COMMAND,
                "data": {
                    "command_id": str(cmd.id),
                    "command_type": cmd.command_type,
                    "payload": cmd.payload,
                },
            },
        )

    handler = MessageHandler(db, redis)
    ping_task = asyncio.create_task(_ping_loop(websocket, device_id))

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > MAX_WS_MESSAGE_BYTES:
                await websocket.close(code=1009)
                log.warning("ws_message_too_large", device_id=str(device_id), size=len(raw))
                return
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("ws_invalid_json", device_id=str(device_id))
                continue
            await handler.handle(device, message)
            await db.commit()
            await websocket.send_json({"type": "ack"})
    except WebSocketDisconnect as e:
        log.info("ws_disconnect", device_id=str(device_id), code=e.code, reason=e.reason)
    except Exception as e:
        log.error("ws_error", device_id=str(device_id), error=str(e), type=type(e).__name__)
    finally:
        ping_task.cancel()
        await manager.disconnect(device_id)
        async with _pending_lock:
            if device_id not in _pending_offline:
                _pending_offline[device_id] = asyncio.create_task(_schedule_offline(device_id))
