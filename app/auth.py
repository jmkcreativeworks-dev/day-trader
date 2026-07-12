"""HTTP Basic Auth as defense-in-depth. The dashboard is normally only
reachable through a Cloudflare-Access-gated hostname, but this box is
also reachable directly via LAN/Tailscale IP, which bypasses that gate
entirely - this middleware makes sure a bare IP hit still needs a
credential."""
import base64
import secrets

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

_UNAUTHENTICATED_PATHS = {"/healthz"}


class BasicAuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["path"] in _UNAUTHENTICATED_PATHS:
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        auth_header = headers.get(b"authorization")

        if auth_header and self._is_valid(auth_header):
            await self.app(scope, receive, send)
            return

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"www-authenticate", b'Basic realm="day-trader"'),
                (b"content-type", b"text/plain"),
            ],
        })
        await send({"type": "http.response.body", "body": b"Unauthorized"})

    @staticmethod
    def _is_valid(auth_header: bytes) -> bool:
        try:
            scheme, _, credentials = auth_header.decode().partition(" ")
            if scheme.lower() != "basic":
                return False
            username, _, password = base64.b64decode(credentials).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return False

        valid_username = secrets.compare_digest(username, settings.DASHBOARD_USERNAME)
        valid_password = secrets.compare_digest(password, settings.DASHBOARD_PASSWORD)
        return valid_username and valid_password
