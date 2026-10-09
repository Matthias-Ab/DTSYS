import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.models.user import User
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token, decode_token
from app.core import totp
from app.core.exceptions import UnauthorizedError


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def authenticate(self, username: str, password: str) -> User:
        ldap_user = await self.authenticate_ldap(username, password)
        if ldap_user is not None:
            ldap_user.last_login = datetime.now(timezone.utc)
            return ldap_user

        result = await self.db.execute(
            select(User).where(User.username == username, User.is_active)
        )
        user = result.scalar_one_or_none()
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedError("Invalid username or password")
        user.last_login = datetime.now(timezone.utc)
        return user

    def verify_mfa(self, user: User, otp: str | None) -> None:
        """Raise unless the user has no MFA or supplied a fresh, valid TOTP code."""
        if not user.totp_enabled:
            return
        if not otp:
            raise UnauthorizedError("MFA code required")
        secret = totp.decrypt_secret(user.totp_secret or "")
        step = totp.verify_code(secret, otp, user.totp_last_step) if secret else None
        if step is None:
            raise UnauthorizedError("Invalid MFA code")
        user.totp_last_step = step

    async def authenticate_ldap(self, username: str, password: str) -> User | None:
        settings = get_settings()
        if not settings.LDAP_ENABLED:
            return None

        try:
            from ldap3 import ALL, Connection, Server, SUBTREE
        except Exception:
            return None

        server = Server(settings.LDAP_SERVER, port=settings.LDAP_PORT, use_ssl=settings.LDAP_USE_SSL, get_info=ALL)
        bind_connection = Connection(server, user=settings.LDAP_BIND_DN, password=settings.LDAP_BIND_PASSWORD, auto_bind=True)

        try:
            search_filter = settings.LDAP_USER_FILTER.format(username=username)
            bind_connection.search(
                search_base=settings.LDAP_BASE_DN,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=["distinguishedName", "memberOf"],
            )
            if not bind_connection.entries:
                return None

            entry = bind_connection.entries[0]
            user_dn = entry.entry_dn
            member_of = set(getattr(entry, "memberOf", []).values if hasattr(entry, "memberOf") else [])

            user_connection = Connection(server, user=user_dn, password=password, auto_bind=True)
            user_connection.unbind()

            role = "admin" if settings.LDAP_ADMIN_GROUP_DN and settings.LDAP_ADMIN_GROUP_DN in member_of else "viewer"
            result = await self.db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user is None:
                user = User(username=username, password_hash=hash_password(uuid.uuid4().hex), role=role, is_active=True)
                self.db.add(user)
                await self.db.flush()
            else:
                user.role = role
                user.is_active = True
            return user
        except Exception:
            return None
        finally:
            bind_connection.unbind()

    def issue_tokens(self, user: User) -> dict:
        org_id = str(user.active_org_id) if user.active_org_id else None
        return {
            "access_token": create_access_token(
                str(user.id),
                {"role": user.role, "username": user.username, "org_id": org_id},
            ),
            "refresh_token": create_refresh_token(str(user.id)),
            "token_type": "bearer",
        }

    async def refresh_access_token(self, refresh_token: str) -> dict:
        try:
            payload = decode_token(refresh_token)
        except ValueError:
            raise UnauthorizedError("Invalid refresh token")

        if payload.get("type") != "refresh":
            raise UnauthorizedError("Wrong token type")

        user_id = payload.get("sub")
        result = await self.db.execute(
            select(User).where(User.id == uuid.UUID(user_id), User.is_active)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise UnauthorizedError("User not found")

        return self.issue_tokens(user)

    async def create_user(self, username: str, password: str, role: str = "viewer") -> User:
        user = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
        )
        self.db.add(user)
        await self.db.flush()
        return user
