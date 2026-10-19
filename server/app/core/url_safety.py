"""Guard against server-side request forgery for admin-supplied URLs (webhooks).

A webhook URL makes the Celery worker issue an HTTP POST from inside the
deployment network, so ``http://169.254.169.254/...`` (cloud metadata),
``http://redis:6379`` or any internal admin service would otherwise be reachable by
anyone who can create a notification rule. Only http(s) URLs whose host resolves
exclusively to globally routable addresses are accepted.
"""
import ipaddress
import socket
from urllib.parse import urlsplit


def _check_ip(raw: str) -> None:
    ip = ipaddress.ip_address(raw)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast:
        raise ValueError(f"URL host resolves to a non-public address ({ip})")


def validate_public_http_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https")
    if not parts.hostname:
        raise ValueError("URL has no host")
    if parts.username or parts.password:
        raise ValueError("URL must not contain credentials")

    host = parts.hostname
    try:
        _check_ip(host)  # literal IP address
        return url.strip()
    except ValueError as exc:
        if "non-public" in str(exc):
            raise

    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError("URL host does not resolve")
    for info in infos:
        _check_ip(info[4][0])
    return url.strip()
