"""OWASP-oriented guards: SSRF, headers, request limits, localhost origin checks."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Request body / message limits (A04: Insecure Design / DoS)
MAX_MESSAGE_CHARS = 100_000
MAX_IMPORT_BYTES = 25 * 1024 * 1024  # 25 MiB
MAX_HISTORY_MESSAGES = 120
MAX_IMPORT_CHATS = 100
MAX_IMPORT_MESSAGES_PER_CHAT = 2_000
MAX_JSON_DEPTH = 40
MAX_DISCOVERY_FILES = 400
READ_CHUNK = 64 * 1024

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Hosts we never allow for Foundry URL (A10: SSRF)
_BLOCKED_HOST_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def _host_port(value: str) -> tuple[str, str | None]:
    """Split host[:port] without treating IPv6 brackets as ports incorrectly."""
    raw = (value or "").strip().lower()
    if not raw:
        return "", None
    if raw.startswith("["):
        # [::<1>]:port
        end = raw.find("]")
        if end == -1:
            return raw, None
        host = raw[: end + 1]
        rest = raw[end + 1 :]
        port = rest[1:] if rest.startswith(":") and rest[1:] else None
        return host, port
    if raw.count(":") == 1:
        host, port = raw.split(":", 1)
        return host, port or None
    return raw, None


class LocalhostOnlyMiddleware(BaseHTTPMiddleware):
    """Reject non-loopback Host / mismatched Origin for state-changing API calls."""

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith(
            "/api/"
        ):
            host_header = (request.headers.get("host") or "").strip()
            host, host_port = _host_port(host_header)
            if not host or host not in _LOOPBACK_HOSTS:
                return JSONResponse(
                    {"detail": "API mutations only allowed via localhost"},
                    status_code=403,
                )

            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlparse(origin)
                    ohost = (parsed.hostname or "").lower()
                    if ohost == "::1":
                        ohost_cmp = "[::1]"
                    else:
                        ohost_cmp = ohost
                    if ohost_cmp not in _LOOPBACK_HOSTS and ohost not in (
                        "127.0.0.1",
                        "localhost",
                        "::1",
                    ):
                        return JSONResponse(
                            {"detail": "Cross-origin API requests are blocked"},
                            status_code=403,
                        )
                    # Same-port as Host when Origin includes a port (blocks cross-port CSRF)
                    oport = str(parsed.port) if parsed.port else None
                    req_port = host_port or (
                        str(request.url.port) if request.url.port else None
                    )
                    if oport and req_port and oport != req_port:
                        return JSONResponse(
                            {"detail": "Origin port must match Host"},
                            status_code=403,
                        )
                    if oport and not req_port:
                        # Default ports: require Origin omit port or match scheme default
                        default = "443" if parsed.scheme == "https" else "80"
                        if oport != default:
                            return JSONResponse(
                                {"detail": "Origin port must match Host"},
                                status_code=403,
                            )
                except Exception:
                    return JSONResponse({"detail": "Invalid Origin"}, status_code=403)
        return await call_next(request)


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_foundry_url(url: str) -> str:
    """Require public HTTPS endpoint — blocks SSRF to localhost/metadata.

    Call on every outbound Foundry request (not only connect) to reduce DNS-rebinding TOCTOU.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Foundry messages URL is empty")
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise ValueError("Foundry messages URL must use https://")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("Foundry messages URL is missing a host")
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError("Foundry messages URL cannot target localhost")
    if any(host.endswith(suf) for suf in _BLOCKED_HOST_SUFFIXES):
        raise ValueError("Foundry messages URL host is not allowed")
    if _is_private_ip(host):
        raise ValueError("Foundry messages URL cannot target a private IP")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve Foundry host: {e}") from e
    for info in infos:
        ip = info[4][0]
        if _is_private_ip(ip):
            raise ValueError("Foundry messages URL resolves to a private/reserved address")
    if parsed.username or parsed.password:
        raise ValueError("Foundry messages URL must not embed credentials")
    return raw


def normalize_usage(raw: dict | None, *, provider: str) -> dict[str, int]:
    """Unify Anthropic + Bedrock usage into input/output/total token counts."""
    raw = raw or {}
    if provider == "aws":
        inp = int(raw.get("inputTokens") or raw.get("input_tokens") or 0)
        out = int(raw.get("outputTokens") or raw.get("output_tokens") or 0)
        total = int(raw.get("totalTokens") or raw.get("total_tokens") or (inp + out))
    else:
        inp = int(raw.get("input_tokens") or raw.get("inputTokens") or 0)
        cache_create = int(raw.get("cache_creation_input_tokens") or 0)
        cache_read = int(raw.get("cache_read_input_tokens") or 0)
        out = int(raw.get("output_tokens") or raw.get("outputTokens") or 0)
        if not inp and (cache_create or cache_read):
            inp = cache_create + cache_read
        total = int(raw.get("total_tokens") or (inp + out))
    return {
        "input_tokens": max(0, inp),
        "output_tokens": max(0, out),
        "total_tokens": max(0, total),
    }


def clamp_message(text: str) -> str:
    t = (text or "").strip()
    if not t:
        raise ValueError("Message is empty")
    if len(t) > MAX_MESSAGE_CHARS:
        raise ValueError(f"Message exceeds {MAX_MESSAGE_CHARS:,} character limit")
    return t


def sanitize_provider_error(status: int, body: str, *, limit: int = 240) -> str:
    """Return a short client-safe error without dumping upstream bodies."""
    snippet = " ".join((body or "").split())
    if len(snippet) > limit:
        snippet = snippet[: limit - 1] + "…"
    # Strip obvious secret-looking substrings
    for marker in ("api-key", "api_key", "authorization", "x-api-key", "secret"):
        if marker in snippet.lower():
            return f"Upstream HTTP {status}"
    return f"Upstream HTTP {status}" + (f": {snippet}" if snippet else "")


async def read_upload_limited(upload: UploadFile, *, max_bytes: int = MAX_IMPORT_BYTES) -> bytes:
    """Stream an upload and reject before buffering past max_bytes."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Import file too large (max {max_bytes // (1024 * 1024)} MiB)")
        chunks.append(chunk)
    return b"".join(chunks)


def assert_json_depth(obj: object, *, max_depth: int = MAX_JSON_DEPTH) -> None:
    stack: list[tuple[object, int]] = [(obj, 1)]
    while stack:
        cur, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(f"JSON nesting exceeds depth {max_depth}")
        if isinstance(cur, dict):
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append((v, depth + 1))
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append((v, depth + 1))
