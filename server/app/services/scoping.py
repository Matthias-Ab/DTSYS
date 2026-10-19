"""Tenant scoping helpers.

Every lookup of an org-owned object by id must go through here (or an equivalent
``where(Model.org_id == org_id)``). ``db.get(Model, id)`` ignores the organization
and was the root cause of cross-tenant reads and writes. A foreign object is
reported as 404, never 403, so object ids cannot be probed across tenants.
"""
import uuid
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError

T = TypeVar("T")


async def get_in_org(db: AsyncSession, model: type[T], object_id: uuid.UUID, org_id: uuid.UUID, what: str | None = None) -> T:
    result = await db.execute(select(model).where(model.id == object_id, model.org_id == org_id))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise NotFoundError(f"{what or model.__name__} not found")
    return obj


async def devices_in_org(db: AsyncSession, device_ids: list[uuid.UUID], org_id: uuid.UUID) -> list:
    """Return the Device rows among device_ids that belong to org_id (revoked ones excluded)."""
    from app.models.device import Device

    if not device_ids:
        return []
    result = await db.execute(
        select(Device).where(Device.id.in_(device_ids), Device.org_id == org_id, ~Device.is_revoked)
    )
    return list(result.scalars().all())
