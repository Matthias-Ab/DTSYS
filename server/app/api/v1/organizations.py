"""Organization management endpoints."""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.organization import Organization, OrganizationMember
from app.models.user import User
from app.models.device import Device
from app.services.auth_service import AuthService
from app.core.rate_limit import limiter
from app.core.security import set_refresh_cookie

router = APIRouter(prefix="/organizations", tags=["organizations"])

SLUG_RE = re.compile(r"^[a-z0-9-]{3,64}$")


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=3, max_length=64)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)


class MemberInvite(BaseModel):
    username: str
    role: str = "member"


class MemberUpdate(BaseModel):
    role: str


def _validate_slug(slug: str) -> str:
    normalized = slug.strip().lower()
    if not SLUG_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Invalid slug")
    return normalized


async def _require_membership(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMember:
    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.org_id == org_id,
            OrganizationMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return member


@router.post("")
async def create_org(
    body: OrganizationCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    slug = _validate_slug(body.slug)
    existing = await db.execute(select(Organization).where(Organization.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Organization slug already exists")

    org = Organization(name=body.name, slug=slug, owner_id=current_user.id)
    db.add(org)
    await db.flush()

    member = OrganizationMember(org_id=org.id, user_id=current_user.id, role="owner")
    db.add(member)

    if current_user.active_org_id is None:
        current_user.active_org_id = org.id

    await db.commit()
    return {"id": str(org.id), "name": org.name, "slug": org.slug}


@router.get("")
async def list_orgs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(Organization, OrganizationMember.role)
        .join(OrganizationMember, OrganizationMember.org_id == Organization.id)
        .where(OrganizationMember.user_id == current_user.id)
        .order_by(Organization.created_at.asc())
    )
    orgs = []
    for org, role in result.all():
        orgs.append(
            {
                "id": str(org.id),
                "name": org.name,
                "slug": org.slug,
                "role": role,
                "active": current_user.active_org_id == org.id,
            }
        )
    return orgs


@router.get("/{org_id}")
async def get_org(
    org_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _require_membership(db, org_id, current_user.id)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"id": str(org.id), "name": org.name, "slug": org.slug}


@router.patch("/{org_id}")
async def update_org(
    org_id: uuid.UUID,
    body: OrganizationUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    member = await _require_membership(db, org_id, current_user.id)
    if member.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if body.name:
        org.name = body.name
    await db.commit()
    return {"id": str(org.id), "name": org.name}


@router.delete("/{org_id}")
async def delete_org(
    org_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    member = await _require_membership(db, org_id, current_user.id)
    if member.role != "owner":
        raise HTTPException(status_code=403, detail="Owner role required")
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.delete(org)
    await db.commit()
    return {"message": "Organization deleted"}


@router.get("/{org_id}/members")
async def list_members(
    org_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _require_membership(db, org_id, current_user.id)
    result = await db.execute(
        select(OrganizationMember, User.username)
        .join(User, User.id == OrganizationMember.user_id)
        .where(OrganizationMember.org_id == org_id)
        .order_by(OrganizationMember.joined_at.asc())
    )
    return [
        {"user_id": str(m.user_id), "username": username, "role": m.role}
        for m, username in result.all()
    ]


@router.post("/{org_id}/members")
async def invite_member(
    org_id: uuid.UUID,
    body: MemberInvite,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    member = await _require_membership(db, org_id, current_user.id)
    if member.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    result = await db.execute(select(User).where(User.username == body.username, User.is_active))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    role = body.role if body.role in {"owner", "admin", "member"} else "member"
    existing = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.org_id == org_id,
            OrganizationMember.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="User already a member")

    db.add(OrganizationMember(org_id=org_id, user_id=user.id, role=role))
    await db.commit()
    return {"message": "Member invited"}


@router.delete("/{org_id}/members/{user_id}")
async def remove_member(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    member = await _require_membership(db, org_id, current_user.id)
    if member.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.org_id == org_id,
            OrganizationMember.user_id == user_id,
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.role == "owner":
        raise HTTPException(status_code=400, detail="Cannot remove org owner")
    await db.delete(target)
    await db.commit()
    return {"message": "Member removed"}


@router.patch("/{org_id}/members/{user_id}")
async def update_member(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    member = await _require_membership(db, org_id, current_user.id)
    if member.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.org_id == org_id,
            OrganizationMember.user_id == user_id,
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.role == "owner" and body.role != "owner":
        raise HTTPException(status_code=400, detail="Cannot demote org owner")
    if body.role not in {"owner", "admin", "member"}:
        raise HTTPException(status_code=400, detail="Invalid role")
    target.role = body.role
    await db.commit()
    return {"message": "Member updated"}


@router.get("/{org_id}/devices")
async def list_org_devices(
    org_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _require_membership(db, org_id, current_user.id)
    result = await db.execute(
        select(Device).where(Device.org_id == org_id, ~Device.is_revoked).order_by(Device.hostname.asc())
    )
    devices = result.scalars().all()
    return [{"id": str(device.id), "hostname": device.hostname, "status": device.status} for device in devices]


@router.post("/{org_id}/switch")
@limiter.limit("20/minute")
async def switch_org(
    request: Request,
    response: Response,
    org_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _require_membership(db, org_id, current_user.id)
    current_user.active_org_id = org_id
    await db.commit()
    tokens = AuthService(db).issue_tokens(current_user)
    set_refresh_cookie(response, tokens["refresh_token"])
    return tokens
