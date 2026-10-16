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
