"""Audit log query and SSE stream endpoints."""
from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.db.session import get_db, AsyncSessionLocal
from app.dependencies import get_current_org_id, get_current_user, require_admin
from app.models.audit_log import AuditLog
from app.models.user import User

router = APIRouter(prefix="/audit", tags=["audit"])

# EventSource (the browser SSE client) can't send an Authorization header, so
# the stream can only be authenticated via the URL. Rather than put the real
# (long-lived, full-API-access) JWT in the URL — which then leaks into proxy
# logs, browser history, and Referer headers — the client first mints a
# single-use, 30-second ticket over a normal Bearer-authenticated request and
# passes only that opaque ticket in the SSE URL.
STREAM_TICKET_PREFIX = "audit_stream_ticket:"
STREAM_TICKET_TTL_SECONDS = 30


@router.post("/stream/ticket")
async def create_stream_ticket(
    current_user: Annotated[User, Depends(require_admin)],
    redis: Annotated[Redis, Depends(get_redis)],
):
    """Issue a short-lived, single-use ticket for authenticating GET /audit/stream."""
    ticket = secrets.token_urlsafe(32)
    await redis.setex(f"{STREAM_TICKET_PREFIX}{ticket}", STREAM_TICKET_TTL_SECONDS, str(current_user.id))
    return {"ticket": ticket, "expires_in": STREAM_TICKET_TTL_SECONDS}


@router.get("")
async def list_audit_logs(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    action: str | None = Query(default=None),
    username: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Paginated audit log with optional filters. Admin only."""
    query = select(AuditLog)

    if action:
        query = query.where(AuditLog.action == action)
    if username:
        query = query.where(AuditLog.username.ilike(f"%{username}%"))
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if resource_id:
        query = query.where(AuditLog.resource_id == resource_id)
    if since:
        query = query.where(AuditLog.timestamp >= since)
    if until:
        query = query.where(AuditLog.timestamp <= until)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar_one()

    rows_result = await db.execute(
        query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit)
    )
    rows = rows_result.scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "id": str(r.id),
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "username": r.username,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "ip_address": r.ip_address,
                "details": r.details,
            }
            for r in rows
        ],
    }


@router.get("/export/csv")
async def export_audit_csv(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    action: str | None = Query(default=None),
):
    """Download audit log as CSV. Admin only."""
    query = select(AuditLog)
    if since:
        query = query.where(AuditLog.timestamp >= since)
    if until:
        query = query.where(AuditLog.timestamp <= until)
    if action:
        query = query.where(AuditLog.action == action)

    rows_result = await db.execute(query.order_by(AuditLog.timestamp.desc()).limit(50000))
    rows = rows_result.scalars().all()

    def csv_escape(val: str | None) -> str:
        if val is None:
            return ""
        val = str(val).replace('"', '""')
        return f'"{val}"'

    lines = ["timestamp,username,action,resource_type,resource_id,ip_address,details"]
    for r in rows:
        lines.append(",".join([
            csv_escape(r.timestamp.isoformat() if r.timestamp else None),
            csv_escape(r.username),
            csv_escape(r.action),
            csv_escape(r.resource_type),
            csv_escape(r.resource_id),
            csv_escape(r.ip_address),
            csv_escape(json.dumps(r.details) if r.details else None),
        ]))

    content = "\n".join(lines)
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit-log.csv"},
    )


async def _audit_sse_generator(request: Request) -> AsyncGenerator[str, None]:
    """Polls the DB every 5 seconds for new audit log entries and streams them as SSE."""
    last_id: uuid.UUID | None = None

    # Bootstrap: get latest entry ID
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(1)
        )
        latest = result.scalar_one_or_none()
        if latest:
            last_id = latest.id

    while True:
        if await request.is_disconnected():
            break

        await asyncio.sleep(5)

        try:
            async with AsyncSessionLocal() as db:
                query = select(AuditLog).order_by(AuditLog.timestamp.asc()).limit(50)
                if last_id is not None:
                    # Fetch entries after the last seen one
                    last_result = await db.execute(
                        select(AuditLog.timestamp).where(AuditLog.id == last_id)
                    )
                    last_ts = last_result.scalar_one_or_none()
                    if last_ts:
                        query = query.where(AuditLog.timestamp > last_ts)

                rows_result = await db.execute(query)
                rows = rows_result.scalars().all()

                for row in rows:
                    last_id = row.id
                    data = json.dumps({
                        "id": str(row.id),
                        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                        "username": row.username,
                        "action": row.action,
                        "resource_type": row.resource_type,
                        "resource_id": row.resource_id,
                        "ip_address": row.ip_address,
                        "details": row.details,
                    })
                    yield f"data: {data}\n\n"
        except Exception:
            pass


async def _require_admin_sse(
    request: Request,
    db: AsyncSession,
    redis: Redis,
    ticket_param: str | None,
) -> None:
    """Auth for SSE: accepts a normal Bearer header, or a single-use ticket
    minted by POST /audit/stream/ticket (for EventSource, which can't send headers)."""
    from app.core.security import decode_token

    user_id: str | None = None

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = decode_token(auth_header[7:])
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token")
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Wrong token type")
        user_id = payload.get("sub")
    elif ticket_param:
        ticket_key = f"{STREAM_TICKET_PREFIX}{ticket_param}"
        raw_user_id = await redis.get(ticket_key)
        if raw_user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired ticket")
        await redis.delete(ticket_key)  # single use
        user_id = raw_user_id.decode() if isinstance(raw_user_id, (bytes, bytearray)) else raw_user_id

    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin required")


@router.get("/stream")
async def audit_stream(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    ticket: str | None = Query(default=None),
):
    """SSE stream of new audit log entries. Admin only.
    Accepts a Bearer Authorization header, or a single-use ?ticket= from
    POST /audit/stream/ticket (EventSource cannot set custom headers).
    """
    await _require_admin_sse(request, db, redis, ticket)
    return StreamingResponse(
        _audit_sse_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
