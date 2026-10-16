import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, update
from sqlalchemy.orm.attributes import set_committed_value

from app.models.command import Command
from app.models.device import Device
from app.websocket.manager import manager
from app.websocket.messages import ServerMessageType
from app.core.exceptions import NotFoundError, BadRequestError, TooManyRequestsError
from app.core.logging import get_logger

log = get_logger(__name__)

ALLOWED_COMMAND_TYPES = {"shell", "script", "update_check", "reboot", "sync_time", "request_process_list", "diagnostics", "update"}


class CommandService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def dispatch_command(
        self,
        device_id: uuid.UUID,
        command_type: str,
        payload: dict,
        issued_by: uuid.UUID | None,
        *,
        fail_if_offline: bool = False,
    ) -> tuple[Command, bool]:
        if command_type not in ALLOWED_COMMAND_TYPES:
            raise BadRequestError(f"Command type must be one of: {ALLOWED_COMMAND_TYPES}")

        if command_type == "sync_time" and "target_time" not in payload:
            payload = {**payload, "target_time": datetime.now(timezone.utc).isoformat()}

        pending_count = int(
            await self.db.scalar(
                select(func.count())
                .select_from(Command)
                .where(
                    Command.device_id == device_id,
                    Command.status.in_(["pending", "sent"]),
                )
            )
            or 0
        )
        if pending_count >= 100:
            raise TooManyRequestsError("Too many queued commands for this device")

        # Verify device exists
        result = await self.db.execute(
            select(Device).where(Device.id == device_id, ~Device.is_revoked)
        )
        device = result.scalar_one_or_none()
        if not device:
            raise NotFoundError(f"Device {device_id} not found")

        command = Command(
            device_id=device_id,
            command_type=command_type,
            payload=payload,
            issued_by=issued_by,
            status="pending",
        )
        self.db.add(command)
        await self.db.flush()
        # The row must be durable and visible BEFORE the device can answer: a fast
        # agent replies within milliseconds, and the result handler runs in another
        # session. Dispatching first (as this used to) dropped results for rows not
        # yet committed, or had them overwritten back to "sent" by our later commit.
        await self.db.commit()

        sent = await manager.send_to_device(
            device_id,
            {
                "type": ServerMessageType.COMMAND,
                "data": {
                    "command_id": str(command.id),
                    "command_type": command_type,
                    "payload": payload,
                },
            },
        )

        # Conditional updates (never an ORM flush of a stale status) so a result that
        # already arrived is not clobbered.
        if sent:
            await self.db.execute(
                update(Command).where(Command.id == command.id, Command.status == "pending").values(status="sent")
            )
            set_committed_value(command, "status", "sent")
            log.info("command_sent", command_id=str(command.id), device_id=str(device_id))
        else:
            log.warning("command_queued_device_offline", command_id=str(command.id))
            if fail_if_offline:
                completed_at = datetime.now(timezone.utc)
                await self.db.execute(
                    update(Command)
                    .where(Command.id == command.id, Command.status == "pending")
                    .values(status="failed", output="Device not connected", completed_at=completed_at)
                )
                set_committed_value(command, "status", "failed")
                set_committed_value(command, "output", "Device not connected")
                set_committed_value(command, "completed_at", completed_at)
        await self.db.commit()

        return command, sent

    async def get_command(self, command_id: uuid.UUID) -> Command:
        result = await self.db.execute(select(Command).where(Command.id == command_id))
        cmd = result.scalar_one_or_none()
        if not cmd:
            raise NotFoundError(f"Command {command_id} not found")
        return cmd
