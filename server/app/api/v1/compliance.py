"""Device compliance policy endpoints."""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_org_id, require_admin
from app.models.compliance import CompliancePolicy, ComplianceResult
from app.models.device import Device
from app.models.user import User
from app.services.compliance_service import ComplianceService

router = APIRouter(prefix="/compliance", tags=["compliance"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PolicyRule(BaseModel):
    type: str
    value: Any


class PolicyCreate(BaseModel):
    name: str
    description: str | None = None
    rules: list[PolicyRule] = []
    is_active: bool = True


class PolicyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    rules: list[PolicyRule] | None = None
    is_active: bool | None = None


# ── Helper ────────────────────────────────────────────────────────────────────

async def _get_policy(policy_id: uuid.UUID, org_id: uuid.UUID, db: AsyncSession) -> CompliancePolicy:
    result = await db.execute(
        select(CompliancePolicy).where(
            CompliancePolicy.id == policy_id,
            CompliancePolicy.org_id == org_id,
        )
    )
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


# ── Policies ──────────────────────────────────────────────────────────────────

@router.get("/policies")
async def list_policies(
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(CompliancePolicy)
        .where(CompliancePolicy.org_id == current_org_id)
        .order_by(CompliancePolicy.name)
    )
    policies = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "is_active": p.is_active,
            "rules": p.rules,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in policies
    ]


@router.post("/policies", status_code=201)
async def create_policy(
    body: PolicyCreate,
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
):
    policy = CompliancePolicy(
        org_id=current_org_id,
        name=body.name.strip(),
        description=body.description,
        is_active=body.is_active,
        rules=[r.model_dump() for r in body.rules],
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return {"id": str(policy.id), "name": policy.name}


@router.get("/policies/{policy_id}")
async def get_policy(
    policy_id: uuid.UUID,
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    policy = await _get_policy(policy_id, current_org_id, db)
    return {
        "id": str(policy.id),
        "name": policy.name,
        "description": policy.description,
        "is_active": policy.is_active,
        "rules": policy.rules,
        "created_at": policy.created_at.isoformat() if policy.created_at else None,
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
    }


@router.patch("/policies/{policy_id}")
async def update_policy(
    policy_id: uuid.UUID,
    body: PolicyUpdate,
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
):
    policy = await _get_policy(policy_id, current_org_id, db)
    if body.name is not None:
        policy.name = body.name.strip()
    if body.description is not None:
        policy.description = body.description
    if body.rules is not None:
        policy.rules = [r.model_dump() for r in body.rules]
    if body.is_active is not None:
        policy.is_active = body.is_active
    await db.commit()
    return {"id": str(policy.id), "name": policy.name}


@router.delete("/policies/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: uuid.UUID,
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
):
    policy = await _get_policy(policy_id, current_org_id, db)
    await db.delete(policy)
    await db.commit()


# ── Results ───────────────────────────────────────────────────────────────────

@router.get("/results")
async def list_results(
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    device_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
    non_compliant_only: bool = False,
):
    """List compliance results filtered to the org's devices."""

    query = (
        select(ComplianceResult, CompliancePolicy.name, Device.hostname)
        .join(CompliancePolicy, ComplianceResult.policy_id == CompliancePolicy.id)
        .join(Device, ComplianceResult.device_id == Device.id)
        .where(CompliancePolicy.org_id == current_org_id)
    )
    if device_id:
        query = query.where(ComplianceResult.device_id == device_id)
    if policy_id:
        query = query.where(ComplianceResult.policy_id == policy_id)
    if non_compliant_only:
        query = query.where(ComplianceResult.is_compliant.is_(False))

    rows = await db.execute(query.order_by(ComplianceResult.evaluated_at.desc()).limit(500))
    return [
        {
            "id": str(r.id),
            "device_id": str(r.device_id),
            "hostname": hostname,
            "policy_id": str(r.policy_id),
            "policy_name": policy_name,
            "is_compliant": r.is_compliant,
            "violations": r.violations,
            "details": r.details,
            "evaluated_at": r.evaluated_at.isoformat() if r.evaluated_at else None,
        }
        for r, policy_name, hostname in rows.all()
    ]


@router.post("/evaluate/{policy_id}", status_code=202)
async def trigger_evaluation(
    policy_id: uuid.UUID,
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
):
    """Trigger an immediate compliance evaluation for all devices against a policy."""
    policy = await _get_policy(policy_id, current_org_id, db)
    service = ComplianceService(db)

    devices_result = await db.execute(
        select(Device).where(Device.org_id == current_org_id, ~Device.is_revoked)
    )
    devices = devices_result.scalars().all()

    count = 0
    for device in devices:
        await service.evaluate_device(device, policy)
        count += 1

    await db.commit()
    return {"message": f"Evaluated {count} devices", "policy_id": str(policy_id)}
