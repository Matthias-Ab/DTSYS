import pytest
import uuid

from app.core.security import verify_password
from app.models.organization import OrganizationMember
from app.models.user import User
from tests.conftest import DEFAULT_ORG_ID


def _add_to_org(db_session, user: User) -> User:
    """User administration is per organization: the target must be a member."""
    db_session.add(user)
    db_session.add(OrganizationMember(id=uuid.uuid4(), org_id=DEFAULT_ORG_ID, user_id=user.id, role="member"))
    return user


@pytest.mark.asyncio
async def test_list_users_admin_only(client, db_session, admin_token):
    _add_to_org(
        db_session,
        User(id=uuid.uuid4(), username="viewer1", password_hash="unused", role="viewer", is_active=True),
    )

    unauthorized = await client.get("/api/v1/admin/users")
    assert unauthorized.status_code == 401

    response = await client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    usernames = {entry["username"] for entry in response.json()}
    assert {"admin", "viewer1"} <= usernames


@pytest.mark.asyncio
async def test_update_user_role(client, db_session, admin_token):
    user = User(
        id=uuid.uuid4(),
        username="viewer2",
        password_hash="unused",
        role="viewer",
        is_active=True,
    )
    _add_to_org(db_session, user)

    response = await client.patch(
        f"/api/v1/admin/users/{user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "admin"},
    )
    assert response.status_code == 200
    assert user.role == "admin"


@pytest.mark.asyncio
async def test_cannot_delete_last_admin(client, admin_token):
    response = await client.delete(
        "/api/v1/admin/users/00000000-0000-0000-0000-000000000001",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_password_reset(client, db_session, admin_token):
    user = User(
        id=uuid.uuid4(),
        username="viewer3",
        password_hash="old-hash",
        role="viewer",
        is_active=True,
    )
    _add_to_org(db_session, user)

    response = await client.patch(
        f"/api/v1/admin/users/{user.id}/password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"password": "new-secret-123"},
    )
    assert response.status_code == 200
    assert verify_password("new-secret-123", user.password_hash)


@pytest.mark.asyncio
async def test_delete_non_last_admin_soft_deactivates(client, db_session, admin_token):
    user = User(
        id=uuid.uuid4(),
        username="admin2",
        password_hash="unused",
        role="admin",
        is_active=True,
    )
    _add_to_org(db_session, user)

    response = await client.delete(
        f"/api/v1/admin/users/{user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert user.is_active is False
