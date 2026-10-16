from typing import Annotated
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from functools import lru_cache


KNOWN_ENVIRONMENTS = {"development", "test", "testing", "staging", "production"}

# Values copied from .env.example / defaults that must never reach production.
_PLACEHOLDER_MARKERS = ("change_me", "changeme", "dev-secret", "your-domain", "example.com")
_WEAK_PASSWORDS = {"", "dtsys", "dtsysredis", "postgres", "password", "redis", "admin", "admin123", "secret"}


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _url_password(url: str) -> str | None:
    try:
        return urlsplit(url).password
    except ValueError:
        return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://dtsys:dtsys@localhost:5432/dtsys"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Security
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ENROLLMENT_TOKEN_EXPIRE_MINUTES: int = 60

    # App
    ENVIRONMENT: str = "development"
    APP_NAME: str = "DTSYS"
    BASE_URL: str = "http://localhost:8000"
    API_V1_PREFIX: str = "/api/v1"
    # Accepts a comma-separated list ("https://a.com,https://b.com") or a JSON array.
    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # Device heartbeat: if not seen in this many seconds, mark offline
    DEVICE_OFFLINE_THRESHOLD_SECONDS: int = 120

    # Alert thresholds
    ALERT_CPU_PERCENT: float = 90.0
    ALERT_RAM_PERCENT: float = 90.0
    ALERT_DISK_PERCENT: float = 90.0
    ALERT_CPU_TEMP_CELSIUS: float = 85.0
    ALERT_NTP_OFFSET_MS: float = 500.0

    # Prometheus /metrics scrape endpoint. Disabled (404) when empty; otherwise
    # requires "Authorization: Bearer <token>".
    METRICS_TOKEN: str = ""

    # First admin (used during DB seed only)
    FIRST_ADMIN_PASSWORD: str = "changeme"

    # LDAP / Active Directory
    LDAP_ENABLED: bool = False
    LDAP_SERVER: str = ""
    LDAP_PORT: int = 389
    LDAP_USE_SSL: bool = False
    LDAP_BIND_DN: str = ""
    LDAP_BIND_PASSWORD: str = ""
    LDAP_BASE_DN: str = ""
    LDAP_USER_FILTER: str = "(sAMAccountName={username})"
    LDAP_ADMIN_GROUP_DN: str = ""

    # Email notifications
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "dtsys@example.com"
    SMTP_TLS: bool = True
    EMAIL_ENABLED: bool = False

    # Data retention
    METRIC_RETENTION_DAYS: int = 90
    EVENT_RETENTION_DAYS: int = 365
    COMMAND_RETENTION_DAYS: int = 180
    ALERT_RETENTION_DAYS: int = 365

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_origins(cls, value):
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def _validate_environment(cls, value):
        # A typo such as "prod" or "Production" used to fall through as "not
        # production": no safety checks, create_all on the live DB, /docs enabled.
        normalized = str(value).strip().lower()
        if normalized not in KNOWN_ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT={value!r} is not recognised; use one of {sorted(KNOWN_ENVIRONMENTS)}"
            )
        return normalized

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    def assert_production_safe(self) -> None:
        """Refuse to boot with known-insecure defaults when ENVIRONMENT=production."""
        if not self.is_production:
            return

        problems: list[str] = []

        if (
            len(self.SECRET_KEY) < 32
            or _looks_like_placeholder(self.SECRET_KEY)
            or len(set(self.SECRET_KEY)) < 10
        ):
            problems.append(
                "SECRET_KEY is a placeholder, too short (<32) or low-entropy. "
                "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )

        if (
            len(self.FIRST_ADMIN_PASSWORD) < 12
            or _looks_like_placeholder(self.FIRST_ADMIN_PASSWORD)
            or self.FIRST_ADMIN_PASSWORD.lower() in _WEAK_PASSWORDS
        ):
            problems.append(
                "FIRST_ADMIN_PASSWORD is a placeholder or shorter than 12 characters. "
                "Set a strong password before first boot."
            )

        for label, url in (("DATABASE_URL", self.DATABASE_URL), ("REDIS_URL", self.REDIS_URL)):
            password = _url_password(url)
            if password is None or password.lower() in _WEAK_PASSWORDS or _looks_like_placeholder(password):
                problems.append(
                    f"{label} has no password or a default/placeholder password; "
                    "set POSTGRES_PASSWORD / REDIS_PASSWORD to strong values."
                )

        if self.METRICS_TOKEN and (len(self.METRICS_TOKEN) < 16 or _looks_like_placeholder(self.METRICS_TOKEN)):
            problems.append("METRICS_TOKEN is set but weak (<16 chars or placeholder); use a random value or leave it empty.")

        for origin in self.ALLOWED_ORIGINS:
            if _looks_like_placeholder(origin):
                problems.append(f"ALLOWED_ORIGINS still contains a placeholder value: {origin}")

        localhost_origins = [
            origin
            for origin in self.ALLOWED_ORIGINS
            if "localhost" in origin or "127.0.0.1" in origin
        ]
        if localhost_origins:
            problems.append(
                f"ALLOWED_ORIGINS contains localhost/127.0.0.1 entries in production: {localhost_origins}. "
                "Set it to the real frontend origin(s)."
            )

        if problems:
            raise RuntimeError(
                "Refusing to start with ENVIRONMENT=production due to insecure configuration:\n- "
                + "\n- ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
