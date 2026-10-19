import uuid
from datetime import datetime, timezone
from typing import Annotated

from croniter import croniter
from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_org_id, require_admin
from app.models.device import Device
from app.models.scheduled_command import ScheduledCommand
from app.models.user import User
from app.services.audit_service import log_action
from app.services.command_service import ALLOWED_COMMAND_TYPES
from app.services.scoping import get_in_org

router = APIRouter(prefix="/scheduled-commands", tags=["scheduled-commands"])


def _check_cron(value: str) -> str:
    if not isinstance(value, str) or not croniter.is_valid(value.strip()):
        raise ValueError("cron_expression is not a valid cron expression")
    return value.strip()


def _check_command_type(value: str) -> str:
    if value not in ALLOWED_COMMAND_TYPES:
        raise ValueError(f"command_type must be one of {sorted(ALLOWED_COMMAND_TYPES)}")
    return value


class ScheduledCommandCreateRequest(BaseModel):
    device_id: uuid.UUID | None = None
    command_type: str
    payload: dict = {}
    cron_expression: str
    is_enabled: bool = True

    _cron = field_validator("cron_expression")(_check_cron)
    _type = field_validator("command_type")(_check_command_type)


class ScheduledCommandUpdateRequest(BaseModel):
    device_id: uuid.UUID | None = None
    command_type: str | None = None
    payload: dict | None = None
    cron_expression: str | None = None
    is_enabled: bool | None = None

    @field_validator("cron_expression")
    @classmethod
    def _cron(cls, value):
        return None if value is None else _check_cron(value)

    @field_validator("command_type")
    @classmethod
    def _type(cls, value):
        return None if value is None else _check_command_type(value)


async def _require_device_in_org(db: AsyncSession, device_id: uuid.UUID | None, org_id: uuid.UUID) -> None:
    """A schedule may only target a device of the caller's own organization."""
    if device_id is not None:
        await get_in_org(db, Device, device_id, org_id, "Device")


@router.get("")
async def list_scheduled_commands(
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(ScheduledCommand)
        .where(ScheduledCommand.org_id == current_org_id)
        .order_by(ScheduledCommand.created_at.desc())
    )
    return [_fmt_scheduled_command(item) for item in result.scalars().all()]


@router.post("")
async def create_scheduled_command(
    body: ScheduledCommandCreateRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _require_device_in_org(db, body.device_id, current_org_id)
    next_run = _compute_next_run(body.cron_expression)
    scheduled = ScheduledCommand(
        org_id=current_org_id,
        device_id=body.device_id,
        command_type=body.command_type,
        payload=body.payload,
        cron_expression=body.cron_expression,
        is_enabled=body.is_enabled,
        next_run_at=next_run if body.is_enabled else None,
        created_by=current_user.id,
    )
    db.add(scheduled)
    await db.flush()
    await log_action(
        db,
        current_user,
        "scheduled_command_created",
        resource_type="scheduled_command",
        resource_id=str(scheduled.id),
        details={"command_type": body.command_type, "device_id": str(body.device_id) if body.device_id else None},
    )
    await db.commit()
    await db.refresh(scheduled)
    return _fmt_scheduled_command(scheduled)


@router.patch("/{scheduled_command_id}")
async def update_scheduled_command(
    scheduled_command_id: uuid.UUID,
    body: ScheduledCommandUpdateRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    scheduled = await get_in_org(db, ScheduledCommand, scheduled_command_id, current_org_id, "Scheduled command")

    updates = body.model_dump(exclude_unset=True)
    if "device_id" in updates:
        await _require_device_in_org(db, updates["device_id"], current_org_id)
    for field, value in updates.items():
        setattr(scheduled, field, value)

    if scheduled.is_enabled:
        scheduled.next_run_at = _compute_next_run(scheduled.cron_expression)
    else:
        scheduled.next_run_at = None

    await log_action(
        db,
        current_user,
        "scheduled_command_updated",
        resource_type="scheduled_command",
        resource_id=str(scheduled.id),
        details={key: (str(value) if isinstance(value, uuid.UUID) else value) for key, value in updates.items() if key != "payload"},
    )
    await db.commit()
    await db.refresh(scheduled)
    return _fmt_scheduled_command(scheduled)


@router.delete("/{scheduled_command_id}")
async def delete_scheduled_command(
    scheduled_command_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    scheduled = await get_in_org(db, ScheduledCommand, scheduled_command_id, current_org_id, "Scheduled command")
    scheduled_id = str(scheduled.id)
    await db.delete(scheduled)
    await log_action(
        db,
        current_user,
        "scheduled_command_deleted",
        resource_type="scheduled_command",
        resource_id=scheduled_id,
    )
    await db.commit()
    return {"message": "Deleted"}


def _compute_next_run(expression: str) -> datetime:
    return croniter(expression, datetime.now(timezone.utc)).get_next(datetime)


def _fmt_scheduled_command(item: ScheduledCommand) -> dict:
    return {
        "id": str(item.id),
        "device_id": str(item.device_id) if item.device_id else None,
        "command_type": item.command_type,
        "payload": item.payload,
        "cron_expression": item.cron_expression,
        "is_enabled": item.is_enabled,
        "last_run_at": item.last_run_at.isoformat() if item.last_run_at else None,
        "next_run_at": item.next_run_at.isoformat() if item.next_run_at else None,
        "created_by": str(item.created_by) if item.created_by else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
