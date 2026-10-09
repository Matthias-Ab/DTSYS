from typing import Annotated
import time
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import check_rate_limit, get_redis
from app.db.session import get_db
from app.config import get_settings
from app.core import totp
from app.core.exceptions import BadRequestError, UnauthorizedError
from app.core.security import decode_token, verify_password, set_refresh_cookie, REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
from app.dependencies import get_current_user
from app.core.rate_limit import limiter
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.audit_service import log_action

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str
    otp: str | None = None


class MfaCodeRequest(BaseModel):
    code: str


class MfaDisableRequest(BaseModel):
    password: str
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    refresh_token: str | None = None

@router.post("/login")
@limiter.limit("10/minute")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
):
    client_ip = _get_client_ip(request)
    rate_limit_key = f"rate_limit:login:{client_ip}"
    if not await check_rate_limit(redis, rate_limit_key, limit=10, window_secs=60):
        response.headers["Retry-After"] = "60"
        return Response(
            content='{"detail":"Too many login attempts"}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": "60"},
        )

    service = AuthService(db)
    try:
        user = await service.authenticate(body.username, body.password)
    except UnauthorizedError:
        await log_action(
            db,
            None,
            "login_failed",
            ip=client_ip,
            details={"username": body.username},
            username=body.username,
        )
        await db.commit()
        raise
    try:
        service.verify_mfa(user, body.otp)
    except UnauthorizedError:
        if body.otp:
            await log_action(
                db,
                user,
                "login_mfa_failed",
                ip=client_ip,
                details={"username": body.username},
            )
            await db.commit()
        raise
    tokens = service.issue_tokens(user)
    await log_action(
        db,
        user,
        "login_success",
        ip=client_ip,
    )
    await db.commit()
    set_refresh_cookie(response, tokens["refresh_token"])
    return tokens


@router.post("/refresh")
@limiter.limit("20/minute")
async def refresh(
    request: Request,
    response: Response,
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
):
    refresh_token = body.refresh_token or request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise UnauthorizedError("Missing refresh token")

    if await redis.get(f"revoked:refresh:{refresh_token}"):
        raise UnauthorizedError("Refresh token revoked")
    service = AuthService(db)
    tokens = await service.refresh_access_token(refresh_token)
    set_refresh_cookie(response, tokens["refresh_token"])
    return tokens


@router.post("/logout")
@limiter.limit("20/minute")
async def logout(
    request: Request,
    response: Response,
    body: LogoutRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(get_redis)],
):
    refresh_token = body.refresh_token or request.cookies.get(REFRESH_COOKIE_NAME)
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)

    if not refresh_token:
        return {"message": "Logged out"}

    try:
        payload = decode_token(refresh_token)
    except ValueError:
        raise UnauthorizedError("Invalid refresh token")

    if payload.get("type") != "refresh":
        raise UnauthorizedError("Wrong token type")

    exp = payload.get("exp")
    ttl = 0
    if exp:
        ttl = max(int(exp - time.time()), 0)
    await redis.setex(f"revoked:refresh:{refresh_token}", ttl or 3600, "revoked")
    return {"message": "Logged out"}


@router.get("/mfa/status")
async def mfa_status(current_user: Annotated[User, Depends(get_current_user)]):
    return {"mfa_enabled": bool(current_user.totp_enabled)}


@router.post("/mfa/setup")
@limiter.limit("10/minute")
async def mfa_setup(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Start enrolment: generate a secret (inactive until /mfa/enable confirms a code)."""
    if current_user.totp_enabled:
        raise BadRequestError("MFA is already enabled")
    secret = totp.generate_secret()
    current_user.totp_secret = totp.encrypt_secret(secret)
    current_user.totp_last_step = None
    await db.commit()
    return {
        "secret": secret,
        "otpauth_uri": totp.provisioning_uri(secret, current_user.username, get_settings().APP_NAME),
    }


@router.post("/mfa/enable")
@limiter.limit("10/minute")
async def mfa_enable(
    request: Request,
    body: MfaCodeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if current_user.totp_enabled:
        raise BadRequestError("MFA is already enabled")
    secret = totp.decrypt_secret(current_user.totp_secret or "") if current_user.totp_secret else None
    if not secret:
        raise BadRequestError("Run MFA setup first")
    step = totp.verify_code(secret, body.code)
    if step is None:
        raise BadRequestError("Invalid MFA code")
    current_user.totp_enabled = True
    current_user.totp_last_step = step
    await log_action(db, current_user, "mfa_enabled", ip=_get_client_ip(request))
    await db.commit()
    return {"mfa_enabled": True}


@router.post("/mfa/disable")
@limiter.limit("10/minute")
async def mfa_disable(
    request: Request,
    body: MfaDisableRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.totp_enabled:
        raise BadRequestError("MFA is not enabled")
    if not verify_password(body.password, current_user.password_hash):
        raise UnauthorizedError("Invalid password")
    AuthService(db).verify_mfa(current_user, body.code)
    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_last_step = None
    await log_action(db, current_user, "mfa_disabled", ip=_get_client_ip(request))
    await db.commit()
    return {"mfa_enabled": False}


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
