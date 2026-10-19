import pytest

from app.config import Settings

GOOD = dict(
    ENVIRONMENT="production",
    SECRET_KEY="a3f9c1e07b5d4c8e9a1f2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70",
    FIRST_ADMIN_PASSWORD="a-strong-passphrase",
    ALLOWED_ORIGINS="https://dtsys.example.org",
    DATABASE_URL="postgresql+asyncpg://dtsys:Zq8vN2mXk4pL7wRt@postgres:5432/dtsys",
    REDIS_URL="redis://:Hy6bT3cJr9dQ5sVe@redis:6379/0",
)


def _settings(**overrides):
    return Settings(_env_file=None, **{**GOOD, **overrides})


def test_allowed_origins_accepts_plain_comma_and_json_forms(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example.com")
    assert Settings(_env_file=None).ALLOWED_ORIGINS == ["https://a.example.com"]
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example.com, https://b.example.com")
    assert Settings(_env_file=None).ALLOWED_ORIGINS == ["https://a.example.com", "https://b.example.com"]
    monkeypatch.setenv("ALLOWED_ORIGINS", '["https://a.example.com"]')
    assert Settings(_env_file=None).ALLOWED_ORIGINS == ["https://a.example.com"]


def test_production_accepts_hardened_config():
    _settings().assert_production_safe()
    _settings(METRICS_TOKEN="x9Kd83jLm2Qp7TzW").assert_production_safe()


def test_production_refuses_insecure_defaults():
    with pytest.raises(RuntimeError) as exc:
        # Explicit insecure values: the test must not depend on the ambient environment
        # (CI exports its own DATABASE_URL/REDIS_URL).
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            SECRET_KEY="dev-secret-key-change-in-production",
            FIRST_ADMIN_PASSWORD="changeme",
            ALLOWED_ORIGINS="http://localhost:3000",
            DATABASE_URL="postgresql+asyncpg://dtsys:dtsys@localhost:5432/dtsys",
            REDIS_URL="redis://localhost:6379/0",
        ).assert_production_safe()
    message = str(exc.value)
    for expected in ("SECRET_KEY", "FIRST_ADMIN_PASSWORD", "ALLOWED_ORIGINS", "DATABASE_URL", "REDIS_URL"):
        assert expected in message


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        # The exact values shipped in .env.example used to pass the check.
        ({"SECRET_KEY": "change_me_secret_key_at_least_32_chars"}, "SECRET_KEY"),
        ({"SECRET_KEY": "a" * 64}, "SECRET_KEY"),  # long but low entropy
        ({"FIRST_ADMIN_PASSWORD": "changeme"}, "FIRST_ADMIN_PASSWORD"),
        ({"FIRST_ADMIN_PASSWORD": "short"}, "FIRST_ADMIN_PASSWORD"),
        ({"ALLOWED_ORIGINS": "https://your-domain.com"}, "ALLOWED_ORIGINS"),
        ({"ALLOWED_ORIGINS": "http://localhost:3000"}, "ALLOWED_ORIGINS"),
        ({"DATABASE_URL": "postgresql+asyncpg://dtsys:change_me_strong_password@postgres/dtsys"}, "DATABASE_URL"),
        ({"DATABASE_URL": "postgresql+asyncpg://dtsys:dtsys@postgres/dtsys"}, "DATABASE_URL"),
        ({"REDIS_URL": "redis://redis:6379/0"}, "REDIS_URL"),
        ({"REDIS_URL": "redis://:change_me_redis_password@redis:6379/0"}, "REDIS_URL"),
        ({"METRICS_TOKEN": "short"}, "METRICS_TOKEN"),
    ],
)
def test_production_rejects_each_placeholder(overrides, fragment):
    with pytest.raises(RuntimeError, match=fragment):
        _settings(**overrides).assert_production_safe()


@pytest.mark.parametrize("value", ["prod", "Prod", "live", "", "productoin"])
def test_unknown_environment_is_rejected_not_treated_as_dev(value):
    with pytest.raises(ValueError, match="not recognised"):
        Settings(_env_file=None, ENVIRONMENT=value)


def test_environment_is_normalised_and_test_is_allowed():
    assert Settings(_env_file=None, ENVIRONMENT=" Production ").ENVIRONMENT == "production"
    assert Settings(_env_file=None, ENVIRONMENT="test").is_production is False
