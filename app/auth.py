"""HTTP Basic Auth as defense-in-depth. The dashboard is normally only
reachable through a Cloudflare-Access-gated hostname, but this box is
also reachable directly via LAN/Tailscale IP, which bypasses that gate
entirely - this middleware makes sure a bare IP hit still needs a
credential.

Credentials are DB-backed (dashboard_users) so users can be added and
removed from the /users page without touching .env or restarting."""
import base64

import bcrypt
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from app.db import SessionLocal
from app.models import DashboardUser

_UNAUTHENTICATED_PATHS = {"/healthz"}

# Checked (and discarded) on every unknown-username attempt so that
# "wrong password" and "no such user" take the same amount of time -
# otherwise response latency alone would reveal which usernames exist.
_DUMMY_HASH = bcrypt.hashpw(b"no-such-user", bcrypt.gensalt())


class BasicAuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["path"] in _UNAUTHENTICATED_PATHS:
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        auth_header = headers.get(b"authorization")
        username = await self._authenticate(auth_header) if auth_header else None

        if username:
            scope.setdefault("state", {})["username"] = username
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
    async def _authenticate(auth_header: bytes) -> str | None:
        try:
            scheme, _, credentials = auth_header.decode().partition(" ")
            if scheme.lower() != "basic":
                return None
            username, _, password = base64.b64decode(credentials).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return None

        # bcrypt is deliberately slow - run it off the event loop so one
        # login attempt doesn't stall every other in-flight request.
        return await run_in_threadpool(_check_credentials, username, password)


def _check_credentials(username: str, password: str) -> str | None:
    db = SessionLocal()
    try:
        user = db.query(DashboardUser).filter(DashboardUser.username == username).first()
        if user is None:
            bcrypt.checkpw(password.encode(), _DUMMY_HASH)
            return None
        if bcrypt.checkpw(password.encode(), user.password_hash.encode()):
            return user.username
        return None
    finally:
        db.close()
