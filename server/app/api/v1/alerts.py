import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, Query, Response, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.models.alert import Alert
from app.models.device import Device
from app.dependencies import get_current_user, get_current_org_id, require_admin
from app.services.alert_service import AlertService
from app.services.audit_service import log_action

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    device_id: uuid.UUID | None = Query(None),
    resolved: bool | None = Query(None),
    severity: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    filters = []
    if device_id:
        filters.append(Alert.device_id == device_id)
    if resolved is not None:
        filters.append(Alert.is_resolved == resolved)
    if severity:
        filters.append(Alert.severity == severity)
    filters.append(Device.org_id == current_org_id)

    total = int(
        await db.scalar(
            select(func.count())
            .select_from(Alert)
            .join(Device, Alert.device_id == Device.id)
            .where(*filters)
        )
        or 0
    )
    response.headers["X-Total-Count"] = str(total)

    query = (
        select(Alert)
        .join(Device, Alert.device_id == Device.id)
        .where(*filters)
        .order_by(Alert.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(query)
    alerts = result.scalars().all()
    return [_fmt(a) for a in alerts]


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Authorize against the alert's device BEFORE mutating anything.
    found = await db.execute(select(Alert).where(Alert.id == alert_id))
    existing = found.scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    owner = await db.execute(
        select(Device).where(Device.id == existing.device_id, Device.org_id == current_org_id)
    )
    if owner.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert = await AlertService(db).resolve_alert(alert_id)
    await log_action(
        db, current_user, "alert_resolved", resource_type="alert", resource_id=str(alert.id),
        details={"alert_type": alert.alert_type, "device_id": str(alert.device_id)},
    )
    await db.commit()
    return _fmt(alert)


def _fmt(a: Alert) -> dict:
    return {
        "id": str(a.id),
        "device_id": str(a.device_id),
        "alert_type": a.alert_type,
        "severity": a.severity,
        "message": a.message,
        "is_resolved": a.is_resolved,
        "created_at": a.created_at.isoformat(),
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
    }
