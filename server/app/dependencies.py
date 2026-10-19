import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.organization import OrganizationMember
from app.models.user import User
from app.core.security import decode_token
from app.core.exceptions import UnauthorizedError, ForbiddenError

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")

    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise UnauthorizedError("Invalid or expired token")

    if payload.get("type") != "access":
        raise UnauthorizedError("Wrong token type")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError()

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id), User.is_active))
    user = result.scalar_one_or_none()
    if not user:
        raise UnauthorizedError("User not found")

    return user


async def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    if current_user.role != "admin":
        raise ForbiddenError("Admin role required")
    return current_user


async def get_current_org_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> uuid.UUID:
    """The organization the caller is acting in.

    The token's ``org_id`` claim is only a *request* to act in that organization; it
    is checked against current membership on every call. Trusting the claim alone
    meant a user removed from an organization (or whose token carried a forged or
    stale org) kept full access until the token expired.
    """
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")

    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise UnauthorizedError("Invalid or expired token")

    if payload.get("type") != "access":
        raise UnauthorizedError("Wrong token type")

    org_claim = payload.get("org_id")
    user_claim = payload.get("sub")
    if not org_claim or not user_claim:
        raise UnauthorizedError("Missing organization")

    try:
        org_id = uuid.UUID(org_claim)
        user_id = uuid.UUID(user_claim)
    except ValueError:
        raise UnauthorizedError("Invalid organization")

    membership = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.org_id == org_id,
            OrganizationMember.user_id == user_id,
        )
    )
    if membership.scalar_one_or_none() is None:
        raise ForbiddenError("Not a member of this organization")
    return org_id
