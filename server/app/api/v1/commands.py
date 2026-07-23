import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.models.command import Command
from app.models.device import Device
from app.dependencies import get_current_user, get_current_org_id
from app.core.exceptions import ForbiddenError
from app.services.audit_service import log_action
from app.services.command_service import CommandService
from app.core.logging import get_logger
from app.websocket.manager import manager
from app.core.rate_limit import limiter

router = APIRouter(prefix="/devices/{device_id}/commands", tags=["commands"])
log = get_logger(__name__)

# Command types that execute arbitrary code on the device — restricted to org admins
# and fully captured in the audit log so a compromised non-admin account can't be used
# to run ad-hoc shell commands across the fleet.
HIGH_RISK_COMMAND_TYPES = {"shell", "script"}


class CommandRequest(BaseModel):
    command_type: str  # shell|script|update_check|reboot
    payload: dict = {}

    @field_validator("payload")
    @classmethod
    def validate_shell_payload(cls, payload: dict) -> dict:
        command = payload.get("command")
        if isinstance(command, str) and len(command) > 10000:
            raise ValueError("shell command payload must be 10000 characters or less")
        return payload


@router.post("")
@limiter.limit("60/minute")
async def dispatch_command(
    device_id: uuid.UUID,
    body: CommandRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.command_type in HIGH_RISK_COMMAND_TYPES and current_user.role != "admin":
        raise ForbiddenError(
            f"'{body.command_type}' commands can only be dispatched by an org admin"
        )

    device = await _get_device_for_org(db, device_id, current_org_id)
    service = CommandService(db)
    cmd, sent = await service.dispatch_command(
        device_id=device_id,
        command_type=body.command_type,
        payload=body.payload,
        issued_by=current_user.id,
        fail_if_offline=True,
    )
    log.info(
        "command_dispatch",
        command_id=str(cmd.id),
        device_id=str(device_id),
        command_type=body.command_type,
        payload=body.payload,
        connected=manager.is_connected(device.id),
        sent=sent,
    )
    audit_details = {"command_type": body.command_type}
    if body.command_type in HIGH_RISK_COMMAND_TYPES:
        command_text = body.payload.get("command") if isinstance(body.payload, dict) else None
        if isinstance(command_text, str):
            audit_details["command"] = command_text[:2000]
    await log_action(
        db,
        current_user,
        "command_dispatched",
        resource_type="device",
        resource_id=str(device_id),
        details=audit_details,
    )
    await db.commit()
    return {
        "command_id": str(cmd.id),
        "status": cmd.status,
        "message": "Command dispatched",
    }


@router.get("")
async def list_commands(
    device_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    skip: int = 0,
    limit: int = 50,
):
    await _get_device_for_org(db, device_id, current_org_id)
    total = int(
        await db.scalar(
            select(func.count()).select_from(Command).where(Command.device_id == device_id)
        )
        or 0
    )
    response.headers["X-Total-Count"] = str(total)
    result = await db.execute(
        select(Command)
        .where(Command.device_id == device_id)
        .order_by(Command.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    cmds = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "command_type": c.command_type,
            "payload": c.payload,
            "status": c.status,
            "exit_code": c.exit_code,
            "created_at": c.created_at.isoformat(),
            "completed_at": c.completed_at.isoformat() if c.completed_at else None,
            "output": c.output,
        }
        for c in cmds
    ]


@router.get("/{command_id}")
async def get_command(
    device_id: uuid.UUID,
    command_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _get_device_for_org(db, device_id, current_org_id)
    service = CommandService(db)
    cmd = await service.get_command(command_id)
    if cmd.device_id != device_id:
        raise HTTPException(status_code=404, detail="Command not found")
    return {
        "id": str(cmd.id),
        "command_type": cmd.command_type,
        "payload": cmd.payload,
        "status": cmd.status,
        "exit_code": cmd.exit_code,
        "output": cmd.output,
        "created_at": cmd.created_at.isoformat(),
        "started_at": cmd.started_at.isoformat() if cmd.started_at else None,
        "completed_at": cmd.completed_at.isoformat() if cmd.completed_at else None,
    }


async def _get_device_for_org(db: AsyncSession, device_id: uuid.UUID, org_id: uuid.UUID) -> Device:
    result = await db.execute(
        select(Device).where(Device.id == device_id, Device.org_id == org_id, ~Device.is_revoked)
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device
