from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.config import get_settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.alert import Alert
from app.models.command import Command
from app.models.device import Device
from app.models.event import Event
from app.models.metrics import DeviceMetric
from app.tasks.async_runner import run_async
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="app.tasks.cleanup_tasks.cleanup_old_metrics")
def cleanup_old_metrics() -> None:
    run_async(run_cleanup())


@celery_app.task(name="app.tasks.cleanup_tasks.cleanup_stale_commands")
def cleanup_stale_commands_task() -> None:
    run_async(cleanup_stale_commands())


async def run_cleanup() -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    metric_cutoff = now - timedelta(days=settings.METRIC_RETENTION_DAYS)
    event_cutoff = now - timedelta(days=settings.EVENT_RETENTION_DAYS)
    command_cutoff = now - timedelta(days=settings.COMMAND_RETENTION_DAYS)
    alert_cutoff = now - timedelta(days=settings.ALERT_RETENTION_DAYS)

    async with AsyncSessionLocal() as db:
        metrics_deleted = await _delete_and_count(db, delete(DeviceMetric).where(DeviceMetric.time < metric_cutoff))
        events_deleted = await _delete_and_count(db, delete(Event).where(Event.time < event_cutoff))
        commands_deleted = await _delete_and_count(db, delete(Command).where(Command.created_at < command_cutoff))
        alerts_deleted = await _delete_and_count(db, delete(Alert).where(Alert.created_at < alert_cutoff, Alert.is_resolved))
        await db.commit()

    deleted = {
        "metrics": metrics_deleted,
        "events": events_deleted,
        "commands": commands_deleted,
        "alerts": alerts_deleted,
    }
    log.info("cleanup_complete", **deleted)
    return deleted


async def cleanup_stale_commands() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            Command.__table__.update()
            .where(Command.status == "sent", Command.created_at < cutoff)
            .values(
                status="failed",
                output="Connection lost before result received",
                completed_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        return int(result.rowcount or 0)


async def collect_storage_stats() -> dict:
    settings = get_settings()
    async with AsyncSessionLocal() as db:
        devices = await _scalar_count(db, select(func.count()).select_from(Device).where(~Device.is_revoked))
        metrics_rows = await _scalar_count(db, select(func.count()).select_from(DeviceMetric))
        events_rows = await _scalar_count(db, select(func.count()).select_from(Event))
        commands_rows = await _scalar_count(db, select(func.count()).select_from(Command))
        alerts_rows = await _scalar_count(db, select(func.count()).select_from(Alert))
        oldest_metric = await db.scalar(select(func.min(DeviceMetric.time)))

    disk_estimate_mb = round(
        ((metrics_rows * 160) + (events_rows * 220) + (commands_rows * 320) + (alerts_rows * 180)) / 1024 / 1024,
        2,
    )
    return {
        "devices": devices,
        "metrics_rows": metrics_rows,
        "events_rows": events_rows,
        "commands_rows": commands_rows,
        "alerts_rows": alerts_rows,
        "oldest_metric": oldest_metric.isoformat() if oldest_metric else None,
        "disk_estimate_mb": disk_estimate_mb,
        "retention_days": {
            "metrics": settings.METRIC_RETENTION_DAYS,
            "events": settings.EVENT_RETENTION_DAYS,
            "commands": settings.COMMAND_RETENTION_DAYS,
            "alerts": settings.ALERT_RETENTION_DAYS,
        },
    }


async def _delete_and_count(db, stmt) -> int:
    result = await db.execute(stmt)
    return int(result.rowcount or 0)


async def _scalar_count(db, stmt) -> int:
    value = await db.scalar(stmt)
    return int(value or 0)
