import re
import shlex
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_user, get_current_org_id, require_admin
from app.models.device import Device
from app.models.software import SoftwareInventory
from app.models.user import User
from app.services.audit_service import log_action
from app.services.command_service import CommandService
from app.services.scoping import devices_in_org

router = APIRouter(prefix="/software-updates", tags=["software-updates"])


# Package names end up inside a root/SYSTEM shell command line, so only characters
# that every package manager uses and no shell treats specially are accepted
# (no spaces, quotes, ; & | $ ` ( ) < > \\ newlines or globbing characters).
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:@-]{0,127}$")
MAX_PACKAGES = 100
MAX_DEVICES = 500


class SoftwareDispatchRequest(BaseModel):
    software_names: list[str] = Field(min_length=1, max_length=MAX_PACKAGES)
    device_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_DEVICES)

    @field_validator("software_names")
    @classmethod
    def _safe_names(cls, names: list[str]) -> list[str]:
        for name in names:
            if not PACKAGE_NAME.fullmatch(name):
                raise ValueError(f"invalid package name: {name!r}")
        return names


@router.get("/pending")
async def get_pending_updates(
    current_user: Annotated[User, Depends(get_current_user)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(SoftwareInventory)
        .join(Device, SoftwareInventory.device_id == Device.id)
        .where(SoftwareInventory.update_available, Device.org_id == current_org_id)
    )
    packages = result.scalars().all()

    grouped: dict[str, dict] = {}
    for package in packages:
        current = grouped.setdefault(
            package.name,
            {
                "software_name": package.name,
                "current_versions": set(),
                "affected_device_ids": set(),
            },
        )
        if package.version:
            current["current_versions"].add(package.version)
        current["affected_device_ids"].add(str(package.device_id))

    return [
        {
            "software_name": name,
            "current_versions": sorted(data["current_versions"]),
            "affected_device_ids": sorted(data["affected_device_ids"]),
            "affected_count": len(data["affected_device_ids"]),
        }
        for name, data in sorted(grouped.items())
    ]


@router.post("/dispatch")
async def dispatch_updates(
    body: SoftwareDispatchRequest,
    current_user: Annotated[User, Depends(require_admin)],
    current_org_id: Annotated[uuid.UUID, Depends(get_current_org_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    command_service = CommandService(db)
    devices = {device.id: device for device in await devices_in_org(db, body.device_ids, current_org_id)}

    dispatched = []
    for device_id in body.device_ids:
        device = devices.get(device_id)
        if device is None:
            continue
        command, _sent = await command_service.dispatch_command(
            device_id=device_id,
            command_type="shell",
            payload={"command": _build_update_command(device.os_type, body.software_names)},
            issued_by=current_user.id,
        )
        dispatched.append({
            "device_id": str(device_id),
            "command_id": str(command.id),
        })

    await log_action(
        db,
        current_user,
        "software_update_dispatched",
        resource_type="software_update",
        details={"packages": body.software_names, "device_count": len(dispatched)},
    )
    await db.commit()
    return {"dispatched": dispatched}


def _build_update_command(os_type: str, packages: list[str]) -> str:
    package_list = " ".join(shlex.quote(pkg) for pkg in packages)
    if os_type == "windows":
        return f'powershell -Command "if (Get-Command winget -ErrorAction SilentlyContinue) {{ {"; ".join([f"winget upgrade --exact --id {pkg} --silent" for pkg in packages])} }} elseif (Get-Command choco -ErrorAction SilentlyContinue) {{ choco upgrade -y {package_list} }} else {{ Write-Output \'No supported package manager found\' }}"'
    if os_type == "macos":
        return f"brew upgrade {package_list}"
    return f"if command -v apt-get >/dev/null 2>&1; then apt-get install -y {package_list}; elif command -v dnf >/dev/null 2>&1; then dnf update -y {package_list}; else echo 'No supported package manager found'; exit 1; fi"
