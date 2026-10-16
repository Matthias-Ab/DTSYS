"""Installer endpoints for agent bootstrap scripts."""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()
# In the repo layout the scripts sit next to server/; in the container image they
# are not copied (build context is ./server), so docker-compose.prod.yml mounts
# them and points SCRIPTS_DIR at the mount.
SCRIPTS_DIR = os.environ.get("SCRIPTS_DIR") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../../scripts")
)


def _script_response(name: str) -> FileResponse:
    path = os.path.join(SCRIPTS_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"{name} is not available on this server")
    return FileResponse(path, media_type="text/plain", filename=name)


@router.get("/install-agent.sh", include_in_schema=False)
async def install_script_linux():
    return _script_response("install-agent.sh")


@router.get("/install-agent.ps1", include_in_schema=False)
async def install_script_windows():
    return _script_response("install-agent.ps1")
