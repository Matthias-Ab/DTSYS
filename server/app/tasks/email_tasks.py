import uuid

from app.db.session import AsyncSessionLocal
from app.models.alert import Alert
from app.models.device import Device
from app.services.email_service import send_alert_email
from app.tasks.async_runner import run_async
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.email_tasks.send_alert_email_task")
def send_alert_email_task(to: str, alert_id: str, device_id: str) -> None:
    run_async(_send_alert_email_async(to, alert_id, device_id))


async def _send_alert_email_async(to: str, alert_id: str, device_id: str) -> None:
    async with AsyncSessionLocal() as db:
        alert = await db.get(Alert, uuid.UUID(alert_id))
        device = await db.get(Device, uuid.UUID(device_id))
        if alert is None or device is None:
            return
        send_alert_email(to, alert, device)
