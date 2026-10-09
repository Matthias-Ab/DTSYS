
import pytest

from app.core import totp
from app.models.user import User


def _admin(db_session) -> User:
    return db_session.items(User)[0]


def _headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def _fresh_code(secret: str, last_step: int | None = None) -> str:
    step = totp.current_step()
    if last_step is not None and step <= last_step:
        step = last_step + 1  # next step is within the +1 window
    return totp._hotp(secret, step)


@pytest.mark.asyncio
async def test_mfa_enrolment_and_login_flow(client, db_session, admin_token):
    admin = _admin(db_session)

    setup = await client.post("/api/v1/auth/mfa/setup", headers=_headers(admin_token))
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    assert admin.totp_secret != secret  # stored encrypted
    assert not admin.totp_enabled

    bad = await client.post("/api/v1/auth/mfa/enable", json={"code": "000000"}, headers=_headers(admin_token))
    assert bad.status_code == 400

    good = await client.post("/api/v1/auth/mfa/enable", json={"code": _fresh_code(secret)}, headers=_headers(admin_token))
    assert good.status_code == 200
    assert admin.totp_enabled

    # Password alone no longer logs in.
    no_otp = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "changeme"})
    assert no_otp.status_code == 401
    assert no_otp.json()["detail"] == "MFA code required"

    wrong = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "changeme", "otp": "000000"})
    assert wrong.status_code == 401

    ok = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "changeme", "otp": _fresh_code(secret, admin.totp_last_step)},
    )
    assert ok.status_code == 200
    assert ok.json()["access_token"]


@pytest.mark.asyncio
async def test_mfa_code_cannot_be_replayed(client, db_session, admin_token):
    admin = _admin(db_session)
    secret = totp.generate_secret()
    admin.totp_secret = totp.encrypt_secret(secret)
    admin.totp_enabled = True
    code = totp._hotp(secret, totp.current_step())

    first = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "changeme", "otp": code})
    assert first.status_code == 200
    replay = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "changeme", "otp": code})
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_mfa_disable_requires_password_and_code(client, db_session, admin_token):
    admin = _admin(db_session)
    secret = totp.generate_secret()
    admin.totp_secret = totp.encrypt_secret(secret)
    admin.totp_enabled = True

    wrong_pw = await client.post(
        "/api/v1/auth/mfa/disable",
        json={"password": "nope", "code": _fresh_code(secret)},
        headers=_headers(admin_token),
    )
    assert wrong_pw.status_code == 401
    assert admin.totp_enabled

    ok = await client.post(
        "/api/v1/auth/mfa/disable",
        json={"password": "changeme", "code": _fresh_code(secret)},
        headers=_headers(admin_token),
    )
    assert ok.status_code == 200
    assert not admin.totp_enabled
    assert admin.totp_secret is None


@pytest.mark.asyncio
async def test_setup_rejected_when_already_enabled(client, db_session, admin_token):
    _admin(db_session).totp_enabled = True
    response = await client.post("/api/v1/auth/mfa/setup", headers=_headers(admin_token))
    assert response.status_code == 400
