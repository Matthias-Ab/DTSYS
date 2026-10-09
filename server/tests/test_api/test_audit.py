import uuid
from datetime import datetime, timezone

import pytest

from app.core.security import create_access_token
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services.audit_service import log_action
from tests.conftest import ADMIN_ID, DEFAULT_ORG_ID


def _viewer_token(db_session) -> str:
    viewer = User(id=uuid.uuid4(), username="viewer", password_hash="x", role="viewer", is_active=True, active_org_id=DEFAULT_ORG_ID)
    db_session.add(viewer)
    return create_access_token(str(viewer.id), {"role": "viewer", "username": "viewer", "org_id": str(DEFAULT_ORG_ID)})


def _entry(db_session, **kw) -> AuditLog:
    entry = AuditLog(username=kw.pop("username", "admin"), action=kw.pop("action", "x"), timestamp=datetime.now(timezone.utc), **kw)
    db_session.add(entry)
    return entry


@pytest.mark.asyncio
async def test_log_action_records_user_and_fields(db_session):
    admin = db_session.items(User)[0]
    entry = await log_action(db_session, admin, "device_revoked", "device", "abc", ip="1.2.3.4", details={"why": "lost"})
    assert entry in db_session.items(AuditLog)
    assert (entry.user_id, entry.username, entry.action) == (ADMIN_ID, "admin", "device_revoked")
    assert (entry.resource_type, entry.resource_id, entry.ip_address, entry.details) == ("device", "abc", "1.2.3.4", {"why": "lost"})


@pytest.mark.asyncio
async def test_log_action_anonymous_failed_login(db_session):
    entry = await log_action(db_session, None, "login_failed", username="mallory")
    assert entry.user_id is None and entry.username == "mallory"


@pytest.mark.asyncio
async def test_audit_endpoints_require_admin(client, db_session):
    token = _viewer_token(db_session)
    headers = {"Authorization": f"Bearer {token}"}
    for method, path in (("get", "/api/v1/audit"), ("get", "/api/v1/audit/export/csv"), ("post", "/api/v1/audit/stream/ticket")):
        response = await getattr(client, method)(path, headers=headers)
        assert response.status_code == 403, path
        response = await getattr(client, method)(path)
        assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_stream_ticket_is_stored_with_short_ttl(client, fake_redis, admin_token):
    response = await client.post("/api/v1/audit/stream/ticket", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    ticket = response.json()["ticket"]
    assert response.json()["expires_in"] == 30
    assert await fake_redis.get(f"audit_stream_ticket:{ticket}") == str(ADMIN_ID)
    assert 0 < await fake_redis.ttl(f"audit_stream_ticket:{ticket}") <= 30


@pytest.mark.asyncio
async def test_csv_export_neutralises_formula_injection(client, db_session, admin_token):
    _entry(db_session, username='=HYPERLINK("http://evil","x")', action="login_failed")
    _entry(db_session, username="+cmd", action="login_failed", details={"k": "v"})
    _entry(db_session, username="normal", action="login_success")

    response = await client.get("/api/v1/audit/export/csv", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.splitlines()
    assert lines[0].startswith("timestamp,username,action")
    body = "\n".join(lines[1:])
    assert '"\'=HYPERLINK(""http://evil"",""x"")"' in body
    assert '"\'+cmd"' in body
    assert '"normal"' in body
    assert '"=' not in body and '"+' not in body
