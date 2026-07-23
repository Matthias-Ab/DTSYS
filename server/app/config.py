from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


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
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    # Device heartbeat: if not seen in this many seconds, mark offline
    DEVICE_OFFLINE_THRESHOLD_SECONDS: int = 120

    # Alert thresholds
    ALERT_CPU_PERCENT: float = 90.0
    ALERT_RAM_PERCENT: float = 90.0
    ALERT_DISK_PERCENT: float = 90.0
    ALERT_CPU_TEMP_CELSIUS: float = 85.0
    ALERT_NTP_OFFSET_MS: float = 500.0

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

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    def assert_production_safe(self) -> None:
        """Refuse to boot with known-insecure defaults when ENVIRONMENT=production."""
        if not self.is_production:
            return

        problems: list[str] = []

        if self.SECRET_KEY == "dev-secret-key-change-in-production" or len(self.SECRET_KEY) < 32:
            problems.append(
                "SECRET_KEY is the default/placeholder value or shorter than 32 characters. "
                "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )

        if self.FIRST_ADMIN_PASSWORD == "changeme":
            problems.append(
                "FIRST_ADMIN_PASSWORD is still the default 'changeme'. Set a strong password before first boot."
            )

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
