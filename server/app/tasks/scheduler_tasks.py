from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.device import Device
from app.models.scheduled_command import ScheduledCommand
from app.services.command_service import CommandService
from app.tasks.async_runner import run_async
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="app.tasks.scheduler_tasks.run_scheduled_commands")
def run_scheduled_commands() -> None:
    run_async(_run_scheduled_commands())


async def _run_scheduled_commands() -> None:
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScheduledCommand).where(
                ScheduledCommand.is_enabled,
                ScheduledCommand.next_run_at.is_not(None),
                ScheduledCommand.next_run_at <= now,
            )
        )
        scheduled_commands = result.scalars().all()
        command_service = CommandService(db)

        for scheduled in scheduled_commands:
            device_ids: list = []
            if scheduled.device_id:
                # Defence in depth for rows created before org checks existed (or by
                # any future bug): never run a schedule on a device of another org.
                target = await db.execute(select(Device).where(Device.id == scheduled.device_id))
                device = target.scalar_one_or_none()
                if device is None or device.is_revoked or device.org_id != scheduled.org_id:
                    log.warning(
                        "scheduled_command_blocked_foreign_target",
                        scheduled_command_id=str(scheduled.id),
                        device_id=str(scheduled.device_id),
                    )
                    scheduled.is_enabled = False
                    scheduled.next_run_at = None
                    continue
                device_ids = [scheduled.device_id]
            else:
                # "All devices" means all devices of the schedule's own organization.
                device_result = await db.execute(
                    select(Device.id).where(~Device.is_revoked, Device.org_id == scheduled.org_id)
                )
                device_ids = [getattr(item, "id", item) for item in device_result.scalars().all()]

            failures = 0
            for device_id in device_ids:
                # One bad device (offline queue full, revoked, ...) must not abort the
                # whole run and leave every schedule stuck re-firing each minute.
                try:
                    await command_service.dispatch_command(
                        device_id=device_id,
                        command_type=scheduled.command_type,
                        payload=scheduled.payload or {},
                        issued_by=scheduled.created_by,
                    )
                except Exception as exc:
                    failures += 1
                    log.warning(
                        "scheduled_command_dispatch_failed",
                        scheduled_command_id=str(scheduled.id),
                        device_id=str(device_id),
                        error=str(exc),
                    )

            scheduled.last_run_at = now
            scheduled.next_run_at = croniter(scheduled.cron_expression, now).get_next(datetime)
            log.info(
                "scheduled_command_dispatched",
                scheduled_command_id=str(scheduled.id),
                target_count=len(device_ids),
                failures=failures,
            )

        await db.commit()
