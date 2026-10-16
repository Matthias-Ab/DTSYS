from datetime import datetime, timedelta, timezone

from app.tasks.async_runner import run_async
from app.tasks.celery_app import celery_app
from app.core.logging import get_logger

log = get_logger(__name__)


@celery_app.task(name="app.tasks.alert_tasks.check_offline_devices")
def check_offline_devices():
    """Mark devices as offline if they haven't sent a heartbeat recently."""
    run_async(_check_offline_devices_async())


async def _check_offline_devices_async():
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal
    from app.models.alert import Alert
    from app.config import get_settings
    from app.models.device import Device
    from app.models.uptime_event import UptimeEvent

    settings = get_settings()
    threshold = datetime.now(timezone.utc) - timedelta(seconds=settings.DEVICE_OFFLINE_THRESHOLD_SECONDS)

    async with AsyncSessionLocal() as db:
        # Find devices that were online but haven't been seen
        result = await db.execute(
            select(Device).where(
                Device.status == "online",
                Device.last_seen < threshold,
                ~Device.is_revoked,
                ~Device.maintenance_mode,
            )
        )
        stale_devices = result.scalars().all()

        for device in stale_devices:
            device.status = "offline"
            db.add(UptimeEvent(device_id=device.id, event_type="offline"))
            # Check if offline alert already exists
            existing = await db.execute(
                select(Alert).where(
                    Alert.device_id == device.id,
                    Alert.alert_type == "offline",
                    ~Alert.is_resolved,
                )
            )
            if not existing.scalar_one_or_none():
                alert = Alert(
                    device_id=device.id,
                    alert_type="offline",
                    severity="warning",
                    message=f"Device {device.hostname} has not reported since {device.last_seen}",
                )
                db.add(alert)
            log.info("device_marked_offline", device_id=str(device.id), hostname=device.hostname)

        await db.commit()
