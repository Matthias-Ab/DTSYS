import httpx

from app.core.logging import get_logger
from app.core.url_safety import validate_public_http_url
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(
    name="app.tasks.notification_tasks.deliver_webhook_notification",
    bind=True,
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def deliver_webhook_notification(self, webhook_url: str, payload: dict) -> None:
    # Re-check at send time: DNS can change after the rule was saved, and rules
    # created before this validation existed may still point at internal hosts.
    try:
        validate_public_http_url(webhook_url)
    except ValueError as exc:
        log.warning("webhook_blocked", error=str(exc))
        return
    try:
        with httpx.Client(timeout=5.0, follow_redirects=False) as client:
            response = client.post(webhook_url, json=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning(
            "webhook_delivery_failed",
            webhook_url=webhook_url,
            attempt=self.request.retries + 1,
            max_retries=self.max_retries,
            error=str(exc),
        )
        raise
