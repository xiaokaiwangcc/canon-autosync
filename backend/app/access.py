import base64
import binascii
import hmac
import ipaddress
import os
import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import DATA_DIR


def api_password() -> str | None:
    configured = os.environ.get("CANON_API_PASSWORD")
    if configured:
        return configured
    if os.environ.get("CANON_REQUIRE_AUTH") != "1":
        return None

    path = DATA_DIR / "api-password"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        password = path.read_text(encoding="utf-8").strip()
        if not password:
            raise RuntimeError(f"API password file is empty: {path}")
        return password
    password = secrets.token_urlsafe(24)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(password + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return password


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _valid_credentials(header: str, password: str) -> bool:
    scheme, _, payload = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        credentials = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(credentials, f"canon:{password}".encode())


async def protect_requests(request: Request, call_next):
    password = request.app.state.api_password
    host = request.url.hostname or ""
    if password is None:
        peer = request.client.host if request.client else ""
        if not _loopback(peer) or (host != "localhost" and not _loopback(host)):
            return JSONResponse({"detail": "Local access only"}, status_code=403)

    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
    if origin is not None:
        try:
            parsed = urlsplit(origin)
            same_origin = parsed.scheme == request.url.scheme and parsed.netloc == request.url.netloc
        except ValueError:
            same_origin = False
        if not same_origin:
            return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)

    if password is not None and not _valid_credentials(request.headers.get("authorization", ""), password):
        return JSONResponse(
            {"detail": "Authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Canon AutoSync", charset="UTF-8"'},
        )
    return await call_next(request)
