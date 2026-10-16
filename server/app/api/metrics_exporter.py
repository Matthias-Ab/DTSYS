"""Prometheus text-format exposition for operational metrics."""

import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.alert import Alert
from app.models.device import Device
from app.version import VERSION
from app.websocket.manager import manager

router = APIRouter()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_metrics(device_counts: dict[str, int], alert_counts: dict[str, int], ws_connections: int) -> str:
    lines = [
        "# HELP dtsys_info DTSYS server build information.",
        "# TYPE dtsys_info gauge",
        f'dtsys_info{{version="{_escape(VERSION)}"}} 1',
        "# HELP dtsys_devices Non-revoked devices by status.",
        "# TYPE dtsys_devices gauge",
    ]
    for status, count in sorted(device_counts.items()):
        lines.append(f'dtsys_devices{{status="{_escape(status)}"}} {count}')
    lines += [
        "# HELP dtsys_alerts_unresolved Unresolved alerts by severity.",
        "# TYPE dtsys_alerts_unresolved gauge",
    ]
    for severity, count in sorted(alert_counts.items()):
        lines.append(f'dtsys_alerts_unresolved{{severity="{_escape(severity)}"}} {count}')
    lines += [
        "# HELP dtsys_websocket_connections Connected agent WebSockets.",
        "# TYPE dtsys_websocket_connections gauge",
        f"dtsys_websocket_connections {ws_connections}",
    ]
    return "\n".join(lines) + "\n"


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    token = get_settings().METRICS_TOKEN
    if not token:
        raise HTTPException(status_code=404, detail="Not found")
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied, f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="Unauthorized")

    async with AsyncSessionLocal() as db:
        device_rows = await db.execute(
            select(Device.status, func.count()).where(~Device.is_revoked).group_by(Device.status)
        )
        alert_rows = await db.execute(
            select(Alert.severity, func.count()).where(~Alert.is_resolved).group_by(Alert.severity)
        )
        devices = {str(status): int(count) for status, count in device_rows.all()}
        alerts = {str(severity): int(count) for severity, count in alert_rows.all()}

    return PlainTextResponse(
        render_metrics(devices, alerts, await manager.cluster_connection_count()),
        media_type="text/plain; version=0.0.4",
    )
