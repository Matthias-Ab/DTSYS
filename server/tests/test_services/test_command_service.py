import uuid

import pytest

from app.core.exceptions import BadRequestError
from app.models.command import Command
from app.models.device import Device
from app.services.command_service import CommandService
from app.websocket.handler import MessageHandler


@pytest.mark.asyncio
async def test_dispatch_command_offline_device(db_session):
    device = Device(id=uuid.uuid4(), hostname="offline-device", os_type="linux", api_key_hash="hash")
    db_session.add(device)

    service = CommandService(db_session)
    command, sent = await service.dispatch_command(device.id, "shell", {"command": "echo ok"}, issued_by=None)

    assert sent is False
    assert command.status == "pending"
    assert len(db_session.items(Command)) == 1


@pytest.mark.asyncio
async def test_dispatch_invalid_type(db_session):
    service = CommandService(db_session)

    with pytest.raises(BadRequestError):
        await service.dispatch_command(uuid.uuid4(), "evil", {}, issued_by=None)


@pytest.mark.asyncio
async def test_handle_command_result_updates_command(db_session):
    device = Device(id=uuid.uuid4(), hostname="device-06", os_type="linux", api_key_hash="hash")
    db_session.add(device)
    command = Command(
        id=uuid.uuid4(),
        device_id=device.id,
        command_type="shell",
        payload={"command": "echo ok"},
        status="sent",
    )
    db_session.add(command)

    handler = MessageHandler(db_session, redis=None)
    await handler.handle(
        device,
        {
            "type": "command_result",
            "data": {"command_id": str(command.id), "exit_code": 0, "output": "ok"},
        },
    )

    assert command.status == "completed"
    assert command.exit_code == 0
    assert command.output == "ok"
    assert command.completed_at is not None


@pytest.mark.asyncio
async def test_command_row_is_committed_before_it_is_sent_to_the_device(db_session, monkeypatch):
    """A fast agent can answer in ~1ms; the row must already be committed by then."""
    from app.models.command import Command
    from app.models.device import Device
    from app.services import command_service as module

    device = Device(id=uuid.uuid4(), hostname="fast-agent", os_type="linux", api_key_hash="hash")
    db_session.add(device)

    events: list[str] = []
    real_commit = db_session.commit

    async def spy_commit():
        events.append("commit")
        await real_commit()

    async def fake_send(device_id, message):
        events.append("send")
        return True

    monkeypatch.setattr(db_session, "commit", spy_commit)
    monkeypatch.setattr(module.manager, "send_to_device", fake_send)

    command, sent = await module.CommandService(db_session).dispatch_command(
        device.id, "shell", {"command": "echo hi"}, issued_by=None
    )

    assert sent is True
    assert events[:2] == ["commit", "send"]
    assert command.status == "sent"
    assert db_session.items(Command)[0].status == "sent"


@pytest.mark.asyncio
async def test_offline_device_fails_command_when_requested(db_session, monkeypatch):
    from app.models.device import Device
    from app.services import command_service as module

    device = Device(id=uuid.uuid4(), hostname="offline", os_type="linux", api_key_hash="hash")
    db_session.add(device)

    async def fake_send(device_id, message):
        return False

    monkeypatch.setattr(module.manager, "send_to_device", fake_send)
    command, sent = await module.CommandService(db_session).dispatch_command(
        device.id, "shell", {"command": "echo hi"}, issued_by=None, fail_if_offline=True
    )
    assert sent is False
    assert command.status == "failed" and command.output == "Device not connected"

    queued, sent = await module.CommandService(db_session).dispatch_command(
        device.id, "shell", {"command": "echo hi"}, issued_by=None
    )
    assert sent is False and queued.status == "pending"


@pytest.mark.asyncio
async def test_reconnect_only_redelivers_never_delivered_commands(db_session):
    """A 'sent' command may already have run; re-sending it would execute it twice."""
    from datetime import datetime, timezone

    from app.websocket.router import _undelivered_commands

    device = Device(id=uuid.uuid4(), hostname="reconnecting", os_type="linux", api_key_hash="hash")
    other = Device(id=uuid.uuid4(), hostname="other", os_type="linux", api_key_hash="hash")
    db_session.add(device)
    db_session.add(other)
    now = datetime.now(timezone.utc)
    for owner, status in ((device, "pending"), (device, "sent"), (device, "completed"), (device, "failed"), (other, "pending")):
        db_session.add(Command(device_id=owner.id, command_type="shell", payload={}, status=status, created_at=now))

    due = await _undelivered_commands(db_session, device.id)
    assert [c.status for c in due] == ["pending"]
    assert all(c.device_id == device.id for c in due)
