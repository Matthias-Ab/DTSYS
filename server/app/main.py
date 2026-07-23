"""DTSYS FastAPI application entrypoint and public health surfaces."""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select, text
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import router as api_router
from app.api.v1.installer import router as installer_router
from app.config import get_settings
from app.core.logging import configure_logging
from app.core.rate_limit import limiter
from app.core.redis import get_redis
from app.db.session import AsyncSessionLocal, Base, engine
from app.models.alert import Alert
from app.models.device import Device
from app.version import VERSION
from app.websocket.manager import manager
from app.websocket.router import router as ws_router
from app.tasks.cleanup_tasks import cleanup_stale_commands

settings = get_settings()
settings.assert_production_safe()
configure_logging()
START_TIME = time.monotonic()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.is_production:
            response.headers["Content-Security-Policy"] = "default-src 'self'"
        else:
            response.headers["Content-Security-Policy"] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: http: https:"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ENVIRONMENT == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    await _seed_admin()
    try:
        await cleanup_stale_commands()
    except Exception:
        pass
    yield
    await engine.dispose()


async def _seed_admin():
    from app.core.security import hash_password
    from app.models.user import User
    from sqlalchemy.exc import IntegrityError

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        if not result.scalar_one_or_none():
            user = User(
                username="admin",
                password_hash=hash_password(settings.FIRST_ADMIN_PASSWORD),
                role="admin",
            )
            db.add(user)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()


app = FastAPI(
    title="DTSYS - Device Management System",
    description="IT device management platform",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(api_router)
app.include_router(ws_router)
app.include_router(installer_router)


@app.get("/health")
async def health():
    db_status = "ok"
    redis_status = "ok"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    try:
        redis = await get_redis()
        await redis.ping()
    except Exception:
        redis_status = "error"

    status = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
    return {
        "status": status,
        "app": settings.APP_NAME,
        "version": VERSION,
        "db": db_status,
        "redis": redis_status,
        "devices_online": manager.connection_count,
        "uptime_secs": int(time.monotonic() - START_TIME),
    }


@app.get("/status")
async def public_status(request: Request):
    async with AsyncSessionLocal() as db:
        total_devices = int(await db.scalar(select(func.count()).select_from(Device).where(~Device.is_revoked)) or 0)
        online_devices = int(await db.scalar(select(func.count()).select_from(Device).where(~Device.is_revoked, Device.status == "online")) or 0)
        offline_devices = int(await db.scalar(select(func.count()).select_from(Device).where(~Device.is_revoked, Device.status == "offline")) or 0)
        active_critical_alerts = int(
            await db.scalar(
                select(func.count()).select_from(Alert).where(
                    Alert.severity == "critical",
                    ~Alert.is_resolved,
                )
            )
            or 0
        )

    status = "operational"
    if online_devices == 0 and total_devices > 0:
        status = "down"
    elif active_critical_alerts > 0:
        status = "degraded"

    payload = {
        "status": status,
        "total_devices": total_devices,
        "online_devices": online_devices,
        "offline_devices": offline_devices,
        "active_critical_alerts": active_critical_alerts,
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(payload)

    title = {
        "operational": "All Systems Operational",
        "degraded": "Degraded",
        "down": "Outage Detected",
    }[status]
    color = {
        "operational": "#16a34a",
        "degraded": "#f59e0b",
        "down": "#dc2626",
    }[status]
    return HTMLResponse(
        f"""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>DTSYS Status</title>
            <style>
              body {{ font-family: sans-serif; background: #f8fafc; color: #0f172a; padding: 40px; }}
              .card {{ max-width: 720px; margin: 0 auto; background: white; border: 1px solid #e2e8f0; border-radius: 18px; padding: 28px; }}
              .status {{ color: {color}; font-size: 2rem; font-weight: 700; }}
              .grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr)); gap: 12px; margin-top: 20px; }}
              .metric {{ background: #f8fafc; border-radius: 12px; padding: 14px; border: 1px solid #e2e8f0; }}
              .label {{ font-size: 12px; text-transform: uppercase; color: #64748b; }}
              .value {{ font-size: 22px; font-weight: 700; margin-top: 6px; }}
            </style>
          </head>
          <body>
            <div class="card">
              <div class="status">{title}</div>
              <p>Last updated {payload["last_updated"]}</p>
              <div class="grid">
                <div class="metric"><div class="label">Devices</div><div class="value">{total_devices}</div></div>
                <div class="metric"><div class="label">Online</div><div class="value">{online_devices}</div></div>
                <div class="metric"><div class="label">Offline</div><div class="value">{offline_devices}</div></div>
                <div class="metric"><div class="label">Critical Alerts</div><div class="value">{active_critical_alerts}</div></div>
              </div>
            </div>
          </body>
        </html>
        """
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=not settings.is_production,
        timeout_keep_alive=300,
    )
