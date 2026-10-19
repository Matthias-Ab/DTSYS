import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.url_safety import validate_public_http_url
from app.dependencies import get_current_org_id, require_admin
from app.models.notification_rule import NotificationRule
from app.models.user import User
from app.services.audit_service import log_action
from app.services.scoping import get_in_org

router = APIRouter(prefix="/notification-rules", tags=["notification-rules"])


class NotificationRuleRequest(BaseModel):
    alert_type: str = "*"
    severity_min: str = "info"
    channel: str
    webhook_url: str | None = None
    email_address: EmailStr | None = None
    is_enabled: bool = True

    @field_validator("webhook_url")
    @classmethod
    def _public_webhook(cls, value):
        return None if value is None else validate_public_http_url(value)

    @model_validator(mode="after")
    def validate_target(self):
        if self.channel == "webhook" and not self.webhook_url:
            raise ValueError("webhook_url is required for webhook notification rules")
        if self.channel == "email" and not self.email_address:
            raise ValueError("email_address is required for email notification rules")
        return self


class NotificationRuleUpdateRequest(BaseModel):
    alert_type: str | None = None
    severity_min: str | None = None
    channel: str | None = None
    webhook_url: str | None = None
    email_address: EmailStr | None = None
    is_enabled: bool | None = None

    @field_validator("webhook_url")
    @classmethod
    def _public_webhook(cls, value):
        return None if value is None else validate_public_http_url(value)


@router.get("")
async def list_notification_rules(
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(NotificationRule)
        .where(NotificationRule.org_id == current_org_id)
        .order_by(NotificationRule.created_at.desc())
    )
    return [_fmt_rule(rule) for rule in result.scalars().all()]


@router.post("")
async def create_notification_rule(
    body: NotificationRuleRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rule = NotificationRule(org_id=current_org_id, **body.model_dump())
    db.add(rule)
    await db.flush()
    await log_action(
        db, current_user, "notification_rule_created", resource_type="notification_rule",
        resource_id=str(rule.id), details={"channel": rule.channel},
    )
    await db.commit()
    await db.refresh(rule)
    return _fmt_rule(rule)


@router.patch("/{rule_id}")
async def update_notification_rule(
    rule_id: uuid.UUID,
    body: NotificationRuleUpdateRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rule = await get_in_org(db, NotificationRule, rule_id, current_org_id, "Notification rule")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(rule, field, value)

    # The same per-channel requirements as on create, applied to the merged result.
    if rule.channel == "webhook" and not rule.webhook_url:
        raise HTTPException(status_code=422, detail="webhook_url is required for webhook notification rules")
    if rule.channel == "email" and not rule.email_address:
        raise HTTPException(status_code=422, detail="email_address is required for email notification rules")

    await log_action(
        db, current_user, "notification_rule_updated", resource_type="notification_rule",
        resource_id=str(rule.id), details={"fields": sorted(updates)},
    )
    await db.commit()
    await db.refresh(rule)
    return _fmt_rule(rule)


@router.delete("/{rule_id}")
async def delete_notification_rule(
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rule = await get_in_org(db, NotificationRule, rule_id, current_org_id, "Notification rule")
    rule_id_text = str(rule.id)
    await db.delete(rule)
    await log_action(
        db, current_user, "notification_rule_deleted", resource_type="notification_rule", resource_id=rule_id_text,
    )
    await db.commit()
    return {"message": "Deleted"}


def _fmt_rule(rule: NotificationRule) -> dict:
    return {
        "id": str(rule.id),
        "alert_type": rule.alert_type,
        "severity_min": rule.severity_min,
        "channel": rule.channel,
        "webhook_url": rule.webhook_url,
        "email_address": rule.email_address,
        "is_enabled": rule.is_enabled,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
    }
