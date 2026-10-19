import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_user, get_current_org_id, require_admin
from app.models.device import Device
from app.models.device_group import DeviceGroup, DeviceGroupMembership
from app.models.user import User
from app.services.scoping import devices_in_org, get_in_org
from app.websocket.manager import manager

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupRequest(BaseModel):
    name: str
    description: str | None = None
    color: str = "#3b82f6"


class GroupUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None


class GroupDevicesRequest(BaseModel):
    device_ids: list[uuid.UUID]


@router.get("")
async def list_groups(
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(
            DeviceGroup,
            func.count(DeviceGroupMembership.id).label("member_count"),
        )
        .outerjoin(DeviceGroupMembership, DeviceGroupMembership.group_id == DeviceGroup.id)
        .where(DeviceGroup.org_id == current_org_id)
        .group_by(DeviceGroup.id)
        .order_by(DeviceGroup.name.asc())
    )
    return [
        {
            "id": str(group.id),
            "name": group.name,
            "description": group.description,
            "color": group.color,
            "member_count": member_count,
            "created_at": group.created_at.isoformat() if group.created_at else None,
        }
        for group, member_count in result.all()
    ]


@router.post("")
async def create_group(
    body: GroupRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    group = DeviceGroup(
        org_id=current_org_id,
        name=body.name,
        description=body.description,
        color=body.color,
        created_by=current_user.id,
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return {
        "id": str(group.id),
        "name": group.name,
        "description": group.description,
        "color": group.color,
        "member_count": 0,
    }


@router.patch("/{group_id}")
async def update_group(
    group_id: uuid.UUID,
    body: GroupUpdateRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    group = await get_in_org(db, DeviceGroup, group_id, current_org_id, "Group")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    await db.commit()
    return {"message": "Updated"}


@router.delete("/{group_id}")
async def delete_group(
    group_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    group = await get_in_org(db, DeviceGroup, group_id, current_org_id, "Group")
    await db.delete(group)
    await db.commit()
    return {"message": "Deleted"}


@router.post("/{group_id}/devices")
async def add_devices_to_group(
    group_id: uuid.UUID,
    body: GroupDevicesRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await get_in_org(db, DeviceGroup, group_id, current_org_id, "Group")
    # Every device must belong to the caller's organization; a single foreign id
    # fails the whole request instead of silently pulling another tenant's device in.
    requested = set(body.device_ids)
    allowed = {device.id for device in await devices_in_org(db, list(requested), current_org_id)}
    if requested - allowed:
        raise HTTPException(status_code=404, detail="Device not found")

    existing = await db.execute(
        select(DeviceGroupMembership.device_id).where(DeviceGroupMembership.group_id == group_id)
    )
    existing_ids = set(existing.scalars().all())

    for device_id in body.device_ids:
        if device_id not in existing_ids:
            db.add(DeviceGroupMembership(group_id=group_id, device_id=device_id))

    await db.commit()
    return {"message": "Devices added"}


@router.delete("/{group_id}/devices/{device_id}")
async def remove_device_from_group(
    group_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await get_in_org(db, DeviceGroup, group_id, current_org_id, "Group")
    result = await db.execute(
        select(DeviceGroupMembership).where(
            DeviceGroupMembership.group_id == group_id,
            DeviceGroupMembership.device_id == device_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="Membership not found")
    await db.delete(membership)
    await db.commit()
    return {"message": "Removed"}


@router.get("/{group_id}/devices")
async def list_group_devices(
    group_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await get_in_org(db, DeviceGroup, group_id, current_org_id, "Group")
    result = await db.execute(
        select(Device)
        .join(DeviceGroupMembership, DeviceGroupMembership.device_id == Device.id)
        .where(DeviceGroupMembership.group_id == group_id, Device.org_id == current_org_id)
        .order_by(Device.hostname.asc())
    )
    devices = result.scalars().all()
    online_ids = await manager.online_among([device.id for device in devices])
    return [
        {
            "id": str(device.id),
            "hostname": device.hostname,
            "label": device.label,
            "status": device.status,
            "os_type": device.os_type,
            "os_version": device.os_version,
            "arch": device.arch,
            "ip_address": device.ip_address,
            "last_seen": device.last_seen.isoformat() if device.last_seen else None,
            "enrolled_at": device.enrolled_at.isoformat() if device.enrolled_at else None,
            "notes": device.notes,
            "tags": device.tags or [],
            "is_online": device.id in online_ids,
        }
        for device in devices
    ]


@router.get("/device/{device_id}")
async def list_device_groups(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await get_in_org(db, Device, device_id, current_org_id, "Device")
    result = await db.execute(
        select(DeviceGroup)
        .join(DeviceGroupMembership, DeviceGroupMembership.group_id == DeviceGroup.id)
        .where(DeviceGroupMembership.device_id == device_id, DeviceGroup.org_id == current_org_id)
        .order_by(DeviceGroup.name.asc())
    )
    groups = result.scalars().all()
    return [
        {
            "id": str(group.id),
            "name": group.name,
            "description": group.description,
            "color": group.color,
        }
        for group in groups
    ]
