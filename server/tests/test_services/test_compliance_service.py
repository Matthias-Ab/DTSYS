from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.compliance_service import _evaluate_rules


def _device(**kw):
    defaults = {"last_seen": datetime.now(timezone.utc), "os_type": "linux"}
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _metric(**kw):
    defaults = {"disk_total_gb": 100.0, "disk_used_gb": 40.0, "disk_percent": 40.0}
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _one(rule, device=None, metric=None, packages=()):
    ok, details, violations = _evaluate_rules([rule], device or _device(), metric, list(packages))
    return ok, details[0], violations


def test_no_rules_is_compliant():
    assert _evaluate_rules([], _device(), None, []) == (True, [], 0)


def test_max_offline_hours():
    ok, _, _ = _one({"type": "max_offline_hours", "value": 24})
    assert ok
    stale = _device(last_seen=datetime.now(timezone.utc) - timedelta(hours=30))
    ok, detail, violations = _one({"type": "max_offline_hours", "value": 24}, device=stale)
    assert not ok and violations == 1 and "30." in detail["detail"]


def test_max_offline_hours_never_seen_and_naive_timestamp():
    ok, detail, _ = _one({"type": "max_offline_hours", "value": 24}, device=_device(last_seen=None))
    assert not ok and detail["detail"] == "Device has never reported"
    naive = _device(last_seen=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
    assert _one({"type": "max_offline_hours", "value": 24}, device=naive)[0]


def test_disk_free_min_gb():
    assert _one({"type": "disk_free_min_gb", "value": 10}, metric=_metric())[0]
    assert not _one({"type": "disk_free_min_gb", "value": 80}, metric=_metric())[0]
    assert not _one({"type": "disk_free_min_gb", "value": 10}, metric=None)[0]
    assert not _one({"type": "disk_free_min_gb", "value": 10}, metric=_metric(disk_total_gb=None))[0]


def test_max_disk_percent():
    assert _one({"type": "max_disk_percent", "value": 90}, metric=_metric())[0]
    assert not _one({"type": "max_disk_percent", "value": 30}, metric=_metric())[0]
    assert not _one({"type": "max_disk_percent", "value": 90}, metric=_metric(disk_percent=None))[0]


def test_required_and_forbidden_software_are_case_insensitive_substrings():
    pkgs = ["ClamAV Antivirus", "git"]
    assert _one({"type": "required_software", "value": "antivirus"}, packages=pkgs)[0]
    assert not _one({"type": "required_software", "value": "edr"}, packages=pkgs)[0]
    assert not _one({"type": "forbidden_software", "value": "GIT"}, packages=pkgs)[0]
    assert _one({"type": "forbidden_software", "value": "torrent"}, packages=pkgs)[0]


def test_os_type():
    assert _one({"type": "os_type", "value": "Linux"})[0]
    assert not _one({"type": "os_type", "value": "windows"})[0]
    assert not _one({"type": "os_type", "value": "linux"}, device=_device(os_type=None))[0]


def test_unknown_rule_passes_and_violations_are_counted():
    ok, detail, _ = _one({"type": "made_up", "value": 1})
    assert ok and "Unknown rule type" in detail["detail"]

    rules = [
        {"type": "os_type", "value": "windows"},
        {"type": "required_software", "value": "edr"},
        {"type": "os_type", "value": "linux"},
    ]
    ok, details, violations = _evaluate_rules(rules, _device(), None, [])
    assert not ok and violations == 2
    assert [d["passed"] for d in details] == [False, False, True]
