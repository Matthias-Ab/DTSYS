"""Compliance policy evaluation engine."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import CompliancePolicy, ComplianceResult
from app.models.device import Device
from app.models.metrics import DeviceMetric
from app.models.software import SoftwareInventory
from app.core.logging import get_logger

log = get_logger(__name__)


def _evaluate_rules(rules: list[dict], device: Device, metric: DeviceMetric | None, packages: list[str]) -> tuple[bool, list[dict], int]:
    """Evaluate a list of rule dicts against device state. Returns (is_compliant, details, violation_count)."""
    details: list[dict] = []
    violations = 0

    for rule in rules:
        rule_type = rule.get("type", "")
        value = rule.get("value")
        passed = True
        detail = ""

        match rule_type:
            case "max_offline_hours":
                # Device must have been seen within N hours
                if device.last_seen is None:
                    passed = False
                    detail = "Device has never reported"
                else:
                    last_seen = device.last_seen
                    if last_seen.tzinfo is None:
                        last_seen = last_seen.replace(tzinfo=timezone.utc)
                    hours_offline = (datetime.now(timezone.utc) - last_seen).total_seconds() / 3600
                    if hours_offline > float(value):
                        passed = False
                        detail = f"Last seen {hours_offline:.1f}h ago (max {value}h)"
                    else:
                        detail = f"Last seen {hours_offline:.1f}h ago"

            case "disk_free_min_gb":
                if metric is None:
                    passed = False
                    detail = "No metric data available"
                elif metric.disk_total_gb is None or metric.disk_used_gb is None:
                    passed = False
                    detail = "Disk metrics unavailable"
                else:
                    free_gb = metric.disk_total_gb - metric.disk_used_gb
                    if free_gb < float(value):
                        passed = False
                        detail = f"Only {free_gb:.1f} GB free (min {value} GB)"
                    else:
                        detail = f"{free_gb:.1f} GB free"

            case "max_disk_percent":
                if metric is None or metric.disk_percent is None:
                    passed = False
                    detail = "Disk metrics unavailable"
                elif metric.disk_percent > float(value):
                    passed = False
                    detail = f"Disk usage {metric.disk_percent:.0f}% (max {value}%)"
                else:
                    detail = f"Disk usage {metric.disk_percent:.0f}%"

            case "required_software":
                # value: package name substring to match
                needle = str(value).lower()
                found = any(needle in pkg.lower() for pkg in packages)
                if not found:
                    passed = False
                    detail = f"Required software not found: {value}"
                else:
                    detail = f"Found: {value}"

            case "forbidden_software":
                needle = str(value).lower()
                found = any(needle in pkg.lower() for pkg in packages)
                if found:
                    passed = False
                    detail = f"Forbidden software detected: {value}"
                else:
                    detail = f"Not installed: {value}"

            case "os_type":
                expected = str(value).lower()
                actual = (device.os_type or "").lower()
                if actual != expected:
                    passed = False
                    detail = f"OS is {device.os_type} (expected {value})"
                else:
                    detail = f"OS: {device.os_type}"

            case _:
                detail = f"Unknown rule type: {rule_type}"
                # Unknown rules don't fail compliance
                passed = True

        if not passed:
            violations += 1
        details.append({"type": rule_type, "passed": passed, "detail": detail})

    is_compliant = violations == 0
    return is_compliant, details, violations


class ComplianceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def evaluate_device(self, device: Device, policy: CompliancePolicy) -> ComplianceResult:
        """Run policy rules against a device and upsert the result."""
        # Fetch latest metric
        metric_result = await self.db.execute(
            select(DeviceMetric)
            .where(DeviceMetric.device_id == device.id)
            .order_by(DeviceMetric.timestamp.desc())
            .limit(1)
        )
        metric = metric_result.scalar_one_or_none()

        # Fetch installed package names
        sw_result = await self.db.execute(
            select(SoftwareInventory.name).where(SoftwareInventory.device_id == device.id)
        )
        packages = [row[0] for row in sw_result.all()]

        is_compliant, details, violations = _evaluate_rules(policy.rules or [], device, metric, packages)

        # Upsert result
        existing_result = await self.db.execute(
            select(ComplianceResult).where(
                ComplianceResult.device_id == device.id,
                ComplianceResult.policy_id == policy.id,
            )
        )
        result = existing_result.scalar_one_or_none()

        if result is None:
            result = ComplianceResult(
                device_id=device.id,
                policy_id=policy.id,
                is_compliant=is_compliant,
                details=details,
                violations=violations,
                evaluated_at=datetime.now(timezone.utc),
            )
            self.db.add(result)
        else:
            result.is_compliant = is_compliant
            result.details = details
            result.violations = violations
            result.evaluated_at = datetime.now(timezone.utc)

        await self.db.flush()
        log.info("compliance_evaluated", device_id=str(device.id), policy_id=str(policy.id), compliant=is_compliant, violations=violations)
        return result

    async def evaluate_org(self, org_id: uuid.UUID) -> None:
        """Evaluate all active policies for all devices in an org."""
        from app.models.device import Device

        policies_result = await self.db.execute(
            select(CompliancePolicy).where(
                CompliancePolicy.org_id == org_id,
                CompliancePolicy.is_active.is_(True),
            )
        )
        policies = policies_result.scalars().all()
        if not policies:
            return

        devices_result = await self.db.execute(
            select(Device).where(Device.org_id == org_id, ~Device.is_revoked)
        )
        devices = devices_result.scalars().all()

        for device in devices:
            for policy in policies:
                try:
                    await self.evaluate_device(device, policy)
                except Exception as exc:
                    log.warning("compliance_eval_failed", device_id=str(device.id), policy_id=str(policy.id), error=str(exc))

        await self.db.commit()
