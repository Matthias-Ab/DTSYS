from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.models.alert import Alert
from app.models.device import Device
from app.models.notification_rule import NotificationRule
from app.services.event_stream import alert_event_stream
from app.tasks.email_tasks import send_alert_email_task
from app.tasks.notification_tasks import deliver_webhook_notification

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def notify(self, alert: Alert, device: Device) -> None:
        from sqlalchemy import select

        result = await self.db.execute(
            # Only the alert's own organization's rules: without this filter every
            # tenant's webhook/email received every other tenant's alerts.
            select(NotificationRule).where(
                NotificationRule.is_enabled,
                NotificationRule.org_id == device.org_id,
            )
        )
        rules = result.scalars().all()
        matching_rules = [rule for rule in rules if self._matches(rule, alert)]

        payload = {
            "id": str(alert.id),
            "device_id": str(alert.device_id),
            "device_hostname": device.label or device.hostname,
            "org_id": str(device.org_id) if device.org_id else None,
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "message": alert.message,
            "is_resolved": alert.is_resolved,
            "created_at": alert.created_at.isoformat(),
            "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        }

        browser_sent = False
        for rule in matching_rules:
            if rule.channel == "browser" and not browser_sent:
                await alert_event_stream.publish(payload)
                browser_sent = True
            if rule.channel == "webhook" and rule.webhook_url:
                deliver_webhook_notification.delay(rule.webhook_url, payload)
            if rule.channel == "email" and rule.email_address:
                send_alert_email_task.delay(rule.email_address, str(alert.id), str(device.id))

        if not browser_sent:
            await alert_event_stream.publish(payload)

        # Send Expo push notifications for critical and high-severity alerts
        if alert.severity in {"critical", "warning"} and device.org_id:
            await self._send_push_notifications(
                org_id=device.org_id,
                title=f"[{alert.severity.upper()}] {device.label or device.hostname}",
                body=alert.message,
            )

    async def _send_push_notifications(self, org_id: uuid.UUID, title: str, body: str) -> None:
        """Send Expo push notifications to all users in this org."""
        import httpx
        from datetime import datetime, timezone
        from sqlalchemy import select
        from app.models.push_token import PushToken
        from app.models.organization import OrganizationMember

        # Fetch push tokens for users in this org
        result = await self.db.execute(
            select(PushToken)
            .join(OrganizationMember, OrganizationMember.user_id == PushToken.user_id)
            .where(OrganizationMember.org_id == org_id)
        )
        tokens = result.scalars().all()
        if not tokens:
            return

        messages = [
            {"to": pt.token, "title": title, "body": body, "sound": "default"}
            for pt in tokens
        ]

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://exp.host/--/api/v2/push/send",
                    json=messages,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    results = resp.json().get("data", [])
                    invalid_tokens = [
                        tokens[i].token
                        for i, r in enumerate(results)
                        if isinstance(r, dict) and r.get("status") == "error" and "InvalidCredentials" in r.get("details", {}).get("error", "")
                    ]
                    if invalid_tokens:
                        from sqlalchemy import delete
                        from app.models.push_token import PushToken as PT
                        await self.db.execute(delete(PT).where(PT.token.in_(invalid_tokens)))

                    now = datetime.now(timezone.utc)
                    for pt in tokens:
                        pt.last_used = now
        except Exception:
            pass  # Push notifications are best-effort

    def _matches(self, rule: NotificationRule, alert: Alert) -> bool:
        if rule.alert_type not in {"*", alert.alert_type}:
            return False
        return SEVERITY_ORDER.get(alert.severity, 0) >= SEVERITY_ORDER.get(rule.severity_min, 0)
