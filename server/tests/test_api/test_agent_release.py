import pytest


@pytest.mark.asyncio
async def test_version_includes_checksum_and_signature(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path))
    (tmp_path / "version.txt").write_text("9.9.9\n")
    (tmp_path / "dtsys-agent-linux-amd64.sha256").write_text("a" * 64 + "\n")
    (tmp_path / "dtsys-agent-linux-amd64.sig").write_text("c2lnbmF0dXJl\n")

    body = (await client.get("/api/v1/agent/version?platform=linux&arch=amd64")).json()
    assert body["version"] == "9.9.9"
    assert body["sha256"] == "a" * 64
    assert body["signature"] == "c2lnbmF0dXJl"


@pytest.mark.asyncio
async def test_version_signature_null_when_unsigned(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path))
    body = (await client.get("/api/v1/agent/version?platform=darwin&arch=arm64")).json()
    assert body["signature"] is None
    assert body["sha256"] is None


@pytest.mark.asyncio
async def test_status_available_under_api_prefix(client, monkeypatch):
    """The SPA's /status page and the JSON endpoint must not share a path behind nginx."""
    from app import main as main_module

    class _Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def scalar(self, _stmt):
            return 0

    monkeypatch.setattr(main_module, "AsyncSessionLocal", lambda: _Db())
    response = await client.get("/api/v1/status", headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.json()["status"] == "operational"


@pytest.mark.asyncio
async def test_installer_scripts_served_or_404(client, tmp_path, monkeypatch):
    from app.api.v1 import installer

    (tmp_path / "install-agent.sh").write_text("#!/bin/sh\necho hi\n")
    monkeypatch.setattr(installer, "SCRIPTS_DIR", str(tmp_path))

    ok = await client.get("/install-agent.sh")
    assert ok.status_code == 200 and "echo hi" in ok.text
    missing = await client.get("/install-agent.ps1")
    assert missing.status_code == 404
