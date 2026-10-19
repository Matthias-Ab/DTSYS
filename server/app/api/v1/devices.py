import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.redis import check_rate_limit, get_redis
from app.core.security import generate_api_key, hash_api_key
from app.db.session import get_db
from app.models.device import Device
from app.models.device_config import DeviceConfig
from app.models.event import Event
from app.models.network import DeviceNetworkInfo
from app.models.ssh_key import SSHKey
from app.models.uptime_event import UptimeEvent
from app.models.user import User
from app.dependencies import get_current_user, require_admin, get_current_org_id
from app.services.audit_service import log_action
from app.services.device_service import DeviceService
from app.websocket.manager import manager
from app.websocket.messages import ServerMessageType

router = APIRouter(prefix="/devices", tags=["devices"])
TAG_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


class RegisterRequest(BaseModel):
    hostname: str
    os_type: str
    os_version: str | None = None
    arch: str | None = None
    fingerprint: str
    ip_address: str | None = None
    enrollment_token: str


class UpdateDeviceRequest(BaseModel):
    label: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = None
    serial_number: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    model_name: str | None = Field(default=None, max_length=100)
    purchase_date: date | None = None
    warranty_expires: date | None = None
    location: str | None = Field(default=None, max_length=255)
    assigned_to: str | None = Field(default=None, max_length=255)
    asset_tag: str | None = Field(default=None, max_length=100)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: list[str] | None) -> list[str] | None:
        if tags is None:
            return tags
        for tag in tags:
            if not TAG_PATTERN.fullmatch(tag):
                raise ValueError("tags must be alphanumeric and may include dash or underscore, up to 50 characters")
        return tags


class MaintenanceRequest(BaseModel):
    enabled: bool
    until: datetime | None = None
    reason: str | None = Field(default=None, max_length=500)


class DeviceConfigRequest(BaseModel):
    telemetry_interval_secs: int
    software_scan_interval_m: int
    event_poll_interval_secs: int


@router.post("/register")
async def register_device(
    body: RegisterRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
):
    client_ip = _get_client_ip(request)
    if not await check_rate_limit(redis, f"rate_limit:register:{client_ip}", limit=5, window_secs=3600):
        raise HTTPException(status_code=429, detail="Too many registration attempts")

    token_key = f"enrollment:{body.enrollment_token}"
    token_value = await redis.get(token_key)
    if token_value is None:
        raise HTTPException(status_code=400, detail="Invalid or expired enrollment token")
    org_id: uuid.UUID | None = None
    if isinstance(token_value, (bytes, bytearray)):
        token_value = token_value.decode("utf-8", errors="ignore")
    try:
        payload = json.loads(token_value)
        org_raw = payload.get("org_id")
        if org_raw:
            org_id = uuid.UUID(str(org_raw))
    except Exception:
        org_id = None

    service = DeviceService(db)
    device, raw_key = await service.register_device(
        hostname=body.hostname,
        os_type=body.os_type,
        os_version=body.os_version,
        arch=body.arch,
        fingerprint=body.fingerprint,
        ip_address=body.ip_address,
        org_id=org_id,
    )
    await db.commit()
    await redis.delete(token_key)
    return {
        "device_id": str(device.id),
        "api_key": raw_key,
        "message": "Device registered successfully",
    }


@router.get("")
async def list_devices(
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    tag: str | None = Query(None),
    search: str | None = Query(None),
):
    service = DeviceService(db)
    total = await service.count_devices(tag=tag, search=search, org_id=current_org_id)
    response.headers["X-Total-Count"] = str(total)
    devices = await service.list_devices(skip=skip, limit=limit, tag=tag, search=search, org_id=current_org_id)
    online_ids = await manager.online_among([d.id for d in devices])
    return [
        {
            "id": str(d.id),
            "hostname": d.hostname,
            "os_type": d.os_type,
            "os_version": d.os_version,
            "arch": d.arch,
            "ip_address": d.ip_address,
            "status": d.status,
            "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            "enrolled_at": d.enrolled_at.isoformat(),
            "label": d.label,
            "notes": d.notes,
            "tags": d.tags or [],
            "serial_number": d.serial_number,
            "manufacturer": d.manufacturer,
            "model_name": d.model_name,
            "purchase_date": d.purchase_date.isoformat() if d.purchase_date else None,
            "warranty_expires": d.warranty_expires.isoformat() if d.warranty_expires else None,
            "location": d.location,
            "assigned_to": d.assigned_to,
            "asset_tag": d.asset_tag,
            "maintenance_mode": d.maintenance_mode,
            "maintenance_until": d.maintenance_until.isoformat() if d.maintenance_until else None,
            "maintenance_reason": d.maintenance_reason,
            "agent_version": d.agent_version,
            "is_online": d.id in online_ids,
        }
        for d in devices
    ]


@router.get("/by-fingerprint/{fingerprint}")
async def get_device_by_fingerprint(
    fingerprint: str,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(Device).where(
            Device.fingerprint == fingerprint,
            Device.org_id == current_org_id,
            ~Device.is_revoked,
        )
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {
        "id": str(device.id),
        "hostname": device.hostname,
        "label": device.label,
        "fingerprint": device.fingerprint,
        "status": device.status,
    }


@router.get("/{device_id}")
async def get_device(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    service = DeviceService(db)
    d = await service.get_device(device_id, org_id=current_org_id)
    return {
        "id": str(d.id),
        "hostname": d.hostname,
        "os_type": d.os_type,
        "os_version": d.os_version,
        "arch": d.arch,
        "ip_address": d.ip_address,
        "status": d.status,
        "last_seen": d.last_seen.isoformat() if d.last_seen else None,
        "enrolled_at": d.enrolled_at.isoformat(),
        "label": d.label,
        "notes": d.notes,
        "tags": d.tags or [],
        "serial_number": d.serial_number,
        "manufacturer": d.manufacturer,
        "model_name": d.model_name,
        "purchase_date": d.purchase_date.isoformat() if d.purchase_date else None,
        "warranty_expires": d.warranty_expires.isoformat() if d.warranty_expires else None,
        "location": d.location,
        "assigned_to": d.assigned_to,
        "asset_tag": d.asset_tag,
        "maintenance_mode": d.maintenance_mode,
        "maintenance_until": d.maintenance_until.isoformat() if d.maintenance_until else None,
        "maintenance_reason": d.maintenance_reason,
        "agent_version": d.agent_version,
        "is_online": await manager.is_online(d.id),
    }


@router.get("/{device_id}/network")
async def get_device_network(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_device_for_org(db, device_id, current_org_id)

    result = await db.execute(
        select(DeviceNetworkInfo)
        .where(DeviceNetworkInfo.device_id == device_id)
        .order_by(DeviceNetworkInfo.interface_name.asc())
    )
    interfaces = result.scalars().all()
    return {
        "interfaces": [
            {
                "id": str(interface.id),
                "interface_name": interface.interface_name,
                "mac_address": interface.mac_address,
                "ipv4": interface.ipv4,
                "ipv6": interface.ipv6,
                "is_up": interface.is_up,
                "mtu": interface.mtu,
                "updated_at": interface.updated_at.isoformat() if interface.updated_at else None,
            }
            for interface in interfaces
        ]
    }


@router.patch("/{device_id}")
async def update_device(
    device_id: uuid.UUID,
    body: UpdateDeviceRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    device = await _get_device_for_org(db, device_id, current_org_id)
    updates = body.model_dump(exclude_unset=True)
    if "label" in updates:
        device.label = body.label
    if "notes" in updates:
        device.notes = body.notes
    if "tags" in updates:
        device.tags = sorted(set(tag.strip() for tag in body.tags or [] if tag.strip()))
    for field in (
        "serial_number",
        "manufacturer",
        "model_name",
        "purchase_date",
        "warranty_expires",
        "location",
        "assigned_to",
        "asset_tag",
    ):
        if field in updates:
            setattr(device, field, getattr(body, field))
    await db.commit()
    return {"message": "Updated"}


@router.post("/{device_id}/maintenance")
async def set_device_maintenance(
    device_id: uuid.UUID,
    body: MaintenanceRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    device = await _get_device_for_org(db, device_id, current_org_id)
    device.maintenance_mode = body.enabled
    device.maintenance_until = body.until if body.enabled else None
    device.maintenance_reason = body.reason if body.enabled else None
    await db.commit()
    return {
        "device_id": str(device.id),
        "maintenance_mode": device.maintenance_mode,
        "maintenance_until": device.maintenance_until.isoformat() if device.maintenance_until else None,
        "maintenance_reason": device.maintenance_reason,
    }


@router.get("/{device_id}/config")
async def get_device_config(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_device_for_org(db, device_id, current_org_id)
    config_row = await db.get(DeviceConfig, device_id)
    config_data = config_row.config if config_row else _default_device_config()
    return {
        "device_id": str(device_id),
        "config": config_data,
        "updated_at": config_row.updated_at.isoformat() if config_row else None,
    }


@router.post("/{device_id}/config")
async def update_device_config(
    device_id: uuid.UUID,
    body: DeviceConfigRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_device_for_org(db, device_id, current_org_id)

    payload = body.model_dump()
    config_row = await db.get(DeviceConfig, device_id)
    if config_row is None:
        config_row = DeviceConfig(device_id=device_id, config=payload)
        db.add(config_row)
    else:
        config_row.config = payload

    await db.commit()

    sent = False
    if await manager.is_online(device_id):
        sent = await manager.send_to_device(
            device_id,
            {"type": ServerMessageType.CONFIG_UPDATE, "data": payload},
        )

    return {
        "device_id": str(device_id),
        "config": payload,
        "sent": sent,
    }


@router.delete("/{device_id}")
async def revoke_device(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    service = DeviceService(db)
    await service.revoke_device(device_id, org_id=current_org_id)
    await log_action(
        db,
        current_user,
        "device_revoked",
        resource_type="device",
        resource_id=str(device_id),
    )
    await db.commit()
    return {"message": "Device revoked"}


@router.post("/{device_id}/rotate-key")
async def rotate_device_key(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    device = await _get_device_for_org(db, device_id, current_org_id)
    raw_key = generate_api_key()
    device.api_key_hash = hash_api_key(raw_key)
    await log_action(
        db,
        current_user,
        "device_key_rotated",
        resource_type="device",
        resource_id=str(device_id),
    )
    await db.commit()
    return {"device_id": str(device_id), "api_key": raw_key}


@router.post("/{device_id}/screenshot/request")
async def request_screenshot(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    device = await _get_device_for_org(db, device_id, current_org_id)
    if device.is_revoked:
        raise HTTPException(status_code=404, detail="Device not found")
    if not await manager.is_online(device_id):
        raise HTTPException(status_code=409, detail="Device is offline")

    await manager.send_to_device(
        device_id,
        {
            "type": ServerMessageType.SCREENSHOT_REQUEST,
            "data": {"command_id": str(uuid.uuid4())},
        },
    )
    return {"message": "Screenshot requested", "device_id": str(device_id)}


@router.get("/{device_id}/screenshot")
async def get_screenshot(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    redis: Annotated[Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_device_for_org(db, device_id, current_org_id)
    payload = await redis.get(f"screenshot:{device_id}")
    if payload is None:
        return {"image_b64": None, "captured_at": None, "status": "not_captured"}
    data = json.loads(payload)
    data.setdefault("status", "captured")
    return data


@router.get("/{device_id}/processes")
async def get_device_processes(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    redis: Annotated[Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_device_for_org(db, device_id, current_org_id)
    payload = await redis.get(f"process_list:{device_id}")
    if payload is None:
        raise HTTPException(status_code=404, detail="Process list not found")
    return json.loads(payload)


@router.get("/{device_id}/ssh-keys")
async def get_device_ssh_keys(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_device_for_org(db, device_id, current_org_id)
    result = await db.execute(
        select(SSHKey).where(SSHKey.device_id == device_id).order_by(SSHKey.discovered_at.desc())
    )
    keys = result.scalars().all()
    return [
        {
            "id": str(key.id),
            "key_type": key.key_type,
            "public_key": key.public_key,
            "fingerprint": key.fingerprint,
            "comment": key.comment,
            "discovered_at": key.discovered_at.isoformat() if key.discovered_at else None,
        }
        for key in keys
    ]


@router.delete("/{device_id}/ssh-keys/{key_id}")
async def delete_device_ssh_key(
    device_id: uuid.UUID,
    key_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_device_for_org(db, device_id, current_org_id)
    key = await db.get(SSHKey, key_id)
    if key is None or key.device_id != device_id:
        raise HTTPException(status_code=404, detail="SSH key not found")
    await db.delete(key)
    await db.commit()
    return {"message": "SSH key removed from inventory"}


@router.get("/{device_id}/uptime-history")
async def get_uptime_history(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(30, ge=1, le=365),
):
    await _get_device_for_org(db, device_id, current_org_id)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(UptimeEvent)
        .where(UptimeEvent.device_id == device_id, UptimeEvent.timestamp >= since)
        .order_by(UptimeEvent.timestamp.asc())
    )
    events = result.scalars().all()
    total_period_secs = days * 24 * 60 * 60
    total_downtime_secs = sum(event.duration_secs or 0 for event in events if event.event_type == "online")
    uptime_percent = 100.0 if total_period_secs == 0 else max(0.0, 100.0 - (total_downtime_secs / total_period_secs * 100.0))
    return {
        "events": [
            {
                "id": str(event.id),
                "event_type": event.event_type,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                "duration_secs": event.duration_secs,
            }
            for event in events
        ],
        "uptime_percent_30d": round(uptime_percent, 2),
        "total_downtime_secs": total_downtime_secs,
        "outage_count": sum(1 for event in events if event.event_type == "offline"),
    }


@router.get("/{device_id}/agent-logs")
async def get_agent_logs(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(200, ge=1, le=1000),
):
    await _get_device_for_org(db, device_id, current_org_id)
    result = await db.execute(
        select(Event)
        .where(
            Event.device_id == device_id,
            (Event.source.like("agent/%")) | (Event.event_type == "agent_log"),
        )
        .order_by(Event.time.desc())
        .limit(limit)
    )
    events = result.scalars().all()
    return [
        {
            "id": str(event.id),
            "time": event.time.isoformat(),
            "event_type": event.event_type,
            "source": event.source,
            "message": event.message,
        }
        for event in events
    ]


@router.post("/{device_id}/disconnect")
async def disconnect_device(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_device_for_org(db, device_id, current_org_id)
    await manager.disconnect(device_id)
    return {"message": "Disconnected"}


def _default_device_config() -> dict:
    return {
        "telemetry_interval_secs": 60,
        "software_scan_interval_m": 60,
        "event_poll_interval_secs": 120,
    }


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def _get_device_for_org(db: AsyncSession, device_id: uuid.UUID, org_id: uuid.UUID) -> Device:
    result = await db.execute(
        select(Device).where(Device.id == device_id, Device.org_id == org_id, ~Device.is_revoked)
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device
