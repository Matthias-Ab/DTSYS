import pytest

from app.config import Settings


def _settings(**env):
    return Settings(_env_file=None, **env)


def test_allowed_origins_accepts_plain_comma_and_json_forms(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example.com")
    assert Settings(_env_file=None).ALLOWED_ORIGINS == ["https://a.example.com"]
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example.com, https://b.example.com")
    assert Settings(_env_file=None).ALLOWED_ORIGINS == ["https://a.example.com", "https://b.example.com"]
    monkeypatch.setenv("ALLOWED_ORIGINS", '["https://a.example.com"]')
    assert Settings(_env_file=None).ALLOWED_ORIGINS == ["https://a.example.com"]


def test_production_refuses_insecure_defaults():
    with pytest.raises(RuntimeError) as exc:
        _settings(ENVIRONMENT="production").assert_production_safe()
    message = str(exc.value)
    assert "SECRET_KEY" in message and "FIRST_ADMIN_PASSWORD" in message and "ALLOWED_ORIGINS" in message


def test_production_accepts_hardened_config():
    _settings(
        ENVIRONMENT="production",
        SECRET_KEY="x" * 40,
        FIRST_ADMIN_PASSWORD="a-strong-passphrase",
        ALLOWED_ORIGINS="https://dtsys.example.com",
    ).assert_production_safe()
