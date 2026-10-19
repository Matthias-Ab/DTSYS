import json
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.redis import get_redis
from app.core.security import hash_password
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.device import Device
from app.models.organization import OrganizationMember
from app.models.user import User
from app.dependencies import get_current_org_id, get_current_user, require_admin
from app.services.auth_service import AuthService
from app.services.audit_service import log_action
from app.tasks.cleanup_tasks import collect_storage_stats, run_cleanup
from app.websocket.manager import manager

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=255)


async def _org_member_ids(db: AsyncSession, org_id: uuid.UUID) -> set[uuid.UUID]:
    result = await db.execute(select(OrganizationMember).where(OrganizationMember.org_id == org_id))
    return {member.user_id for member in result.scalars().all()}


async def _get_org_user(db: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> User:
    """A user of the caller's own organization; anyone else is reported as not found.

    The admin role used to be platform-wide: an admin of tenant A could list every
    user, reset tenant B's admin password and take that account over.
    """
    if user_id not in await _org_member_ids(db, org_id):
        raise HTTPException(status_code=404, detail="User not found")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _active_admin_count(db: AsyncSession, org_id: uuid.UUID) -> int:
    member_ids = await _org_member_ids(db, org_id)
    if not member_ids:
        return 0
    result = await db.execute(select(User).where(User.id.in_(member_ids), User.role == "admin", User.is_active))
    return len(result.scalars().all())


@router.post("/users")
async def create_user(
    body: CreateUserRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.role not in {"admin", "viewer"}:
        raise HTTPException(status_code=400, detail="Role must be admin or viewer")
    if len(body.password) < 12:
        raise HTTPException(status_code=400, detail="Password must be at least 12 characters")
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username already exists")
    service = AuthService(db)
    user = await service.create_user(body.username, body.password, body.role)
    # New users belong to the creating admin's organization (and start in it),
    # otherwise they can log in but every org-scoped call fails.
    user.active_org_id = current_org_id
    db.add(OrganizationMember(org_id=current_org_id, user_id=user.id, role="member"))
    await log_action(
        db,
        current_user,
        "user_created",
        resource_type="user",
        resource_id=str(user.id),
        details={"username": user.username, "role": user.role},
    )
    await db.commit()
    return {"id": str(user.id), "username": user.username, "role": user.role}


@router.get("/users")
async def list_users(
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    member_ids = await _org_member_ids(db, current_org_id)
    result = await db.execute(
        select(User).where(User.id.in_(member_ids)).order_by(User.created_at.asc(), User.username.asc())
    )
    users = result.scalars().all()
    return [
        {
            "id": str(user.id),
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }
        for user in users
    ]


@router.patch("/users/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user = await _get_org_user(db, user_id, current_org_id)

    if body.role is not None:
        if body.role not in {"admin", "viewer"}:
            raise HTTPException(status_code=400, detail="Role must be admin or viewer")
        if user.id == current_user.id and body.role != "admin":
            raise HTTPException(status_code=400, detail="Cannot remove your own admin role")
        user.role = body.role

    if body.is_active is not None:
        if user.id == current_user.id and not body.is_active:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        if not body.is_active and user.role == "admin":
            if await _active_admin_count(db, current_org_id) <= 1:
                raise HTTPException(status_code=400, detail="Cannot deactivate the last admin")
        user.is_active = body.is_active

    await log_action(
        db,
        current_user,
        "user_updated",
        resource_type="user",
        resource_id=str(user.id),
        details=body.model_dump(exclude_none=True),
    )
    await db.commit()
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
    }


@router.patch("/users/{user_id}/password")
async def reset_user_password(
    user_id: uuid.UUID,
    body: ResetPasswordRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if current_user.role != "admin" and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Not allowed to change this password")
    user = await _get_org_user(db, user_id, current_org_id)

    user.password_hash = hash_password(body.password)
    await log_action(
        db,
        current_user,
        "user_password_reset",
        resource_type="user",
        resource_id=str(user.id),
        details={"username": user.username},
    )
    await db.commit()
    return {"message": "Password updated"}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    user = await _get_org_user(db, user_id, current_org_id)
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    if user.role == "admin" and user.is_active:
        if await _active_admin_count(db, current_org_id) <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last admin")

    user.is_active = False
    await log_action(
        db,
        current_user,
        "user_deleted",
        resource_type="user",
        resource_id=str(user.id),
        details={"username": user.username},
    )
    await db.commit()
    return {"message": "User deactivated"}


@router.post("/enrollment-tokens")
async def generate_enrollment_token(
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    redis: Annotated[Redis, Depends(get_redis)],
):
    # The membership-verified organization, not users.active_org_id (which could
    # point at an organization the user no longer belongs to).
    token = secrets.token_urlsafe(24)
    await redis.setex(
        f"enrollment:{token}",
        3600,
        json.dumps({"org_id": str(current_org_id)}),
    )
    return {
        "enrollment_token": token,
        "expires_in_minutes": 60,
        "note": "Use this token once when registering a new device agent",
    }


@router.get("/audit-log")
async def get_audit_log(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
):
    filters = []
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if action:
        filters.append(AuditLog.action == action)
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type)

    total = int(await db.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0)
    response.headers["X-Total-Count"] = str(total)

    query = select(AuditLog).where(*filters).order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()
    return [
        {
            "id": str(entry.id),
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            "user_id": str(entry.user_id) if entry.user_id else None,
            "username": entry.username,
            "action": entry.action,
            "resource_type": entry.resource_type,
            "resource_id": entry.resource_id,
            "ip_address": entry.ip_address,
            "details": entry.details,
        }
        for entry in entries
    ]


@router.get("/auth-config")
async def get_auth_config(
    current_user: Annotated[User, Depends(require_admin)],
):
    settings = get_settings()
    return {
        "mode": "LDAP" if settings.LDAP_ENABLED else "Local",
        "ldap_enabled": settings.LDAP_ENABLED,
        "ldap_server": settings.LDAP_SERVER,
        "ldap_port": settings.LDAP_PORT,
        "ldap_use_ssl": settings.LDAP_USE_SSL,
        "ldap_base_dn": settings.LDAP_BASE_DN,
        "ldap_user_filter": settings.LDAP_USER_FILTER,
        "ldap_admin_group_dn": settings.LDAP_ADMIN_GROUP_DN,
    }


@router.get("/connections")
async def get_live_connections(
    current_user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    snapshot = manager.connection_snapshot()
    if not snapshot:
        return {"total": 0, "devices": []}

    device_ids = [entry["device_id"] for entry in snapshot]
    result = await db.execute(select(Device).where(Device.id.in_(device_ids)))
    devices = {device.id: device for device in result.scalars().all()}

    return {
        "total": len(snapshot),
        "devices": [
            {
                "device_id": str(entry["device_id"]),
                "hostname": (devices.get(entry["device_id"]).label or devices.get(entry["device_id"]).hostname)
                if devices.get(entry["device_id"])
                else str(entry["device_id"]),
                "ip": entry["ip"],
                "connected_since": entry["connected_since"].isoformat(),
            }
            for entry in snapshot
        ],
    }


@router.get("/storage-stats")
async def get_storage_stats(
    current_user: Annotated[User, Depends(require_admin)],
):
    return await collect_storage_stats()


@router.post("/cleanup")
async def cleanup_now(
    current_user: Annotated[User, Depends(require_admin)],
):
    deleted = await run_cleanup()
    return {"deleted": deleted}
