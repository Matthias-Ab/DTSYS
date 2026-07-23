import pytest
import uuid

from app.core.exceptions import UnauthorizedError
from app.core.security import create_access_token, create_refresh_token
from app.models.user import User
from app.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_login_success(client, monkeypatch):
    async def fake_authenticate(self, username: str, password: str) -> User:
        return User(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            username=username,
            password_hash="unused",
            role="admin",
            is_active=True,
        )

    monkeypatch.setattr(AuthService, "authenticate", fake_authenticate)

    response = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "changeme"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"]
    assert payload["refresh_token"]


@pytest.mark.asyncio
async def test_login_wrong_password(client, monkeypatch):
    async def fake_authenticate(self, username: str, password: str):
        raise UnauthorizedError("Invalid username or password")

    monkeypatch.setattr(AuthService, "authenticate", fake_authenticate)

    response = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token(client, monkeypatch):
    refresh_token = create_refresh_token("00000000-0000-0000-0000-000000000001")

    async def fake_refresh_access_token(self, token: str) -> dict:
        assert token == refresh_token
        return {
            "access_token": create_access_token(
                "00000000-0000-0000-0000-000000000001",
                {"role": "admin", "org_id": "00000000-0000-0000-0000-000000000010"},
            ),
            "refresh_token": create_refresh_token("00000000-0000-0000-0000-000000000001"),
            "token_type": "bearer",
        }

    monkeypatch.setattr(AuthService, "refresh_access_token", fake_refresh_access_token)

    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    assert response.json()["access_token"]


@pytest.mark.asyncio
async def test_login_rate_limit(client, monkeypatch):
    async def fake_authenticate(self, username: str, password: str):
        raise UnauthorizedError("Invalid username or password")

    monkeypatch.setattr(AuthService, "authenticate", fake_authenticate)

    for _ in range(9):
        response = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
        assert response.status_code == 401

    response = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 429
