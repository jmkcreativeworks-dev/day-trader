"""OAuth 2.1 (PKCE + dynamic client registration) against Robinhood's
Trading MCP server (https://agent.robinhood.com/mcp/trading), using the
official `mcp` Python SDK's client auth support - this is the same
generic mechanism `claude mcp add --transport http <url>` uses, not
anything Robinhood-specific.

Tokens (and the dynamically-registered client id/secret) persist to a
JSON file (ROBINHOOD_TOKEN_FILE) so RobinhoodBroker can run headless
after the one-time interactive login in
scripts/robinhood_oauth_setup.py. This module never attempts an
interactive login itself - if there's no usable token, or a refresh
fails, it raises RobinhoodAuthError and expects the caller (the
scheduler) to pause trading rather than hang waiting for a browser
that isn't there.
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from app.config import settings

CLIENT_NAME = "day-trader"


class RobinhoodAuthError(Exception):
    """No usable Robinhood session, and getting one would require an
    interactive browser login. Callers (the scheduler) should pause
    trading and surface this - never attempt the login themselves."""


class RobinhoodToolError(Exception):
    """A specific tool call failed or returned an error result - not
    necessarily an auth problem (e.g. insufficient buying power, bad
    symbol, market closed)."""


class FileTokenStorage(TokenStorage):
    """Persists tokens + the dynamically-registered client info to one
    JSON file. Single-writer only (one broker/script at a time) -
    that's the actual usage pattern here, no locking needed."""

    def __init__(self, path: str):
        self.path = path

    def _read(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path) as f:
            return json.load(f)

    def _write(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    async def get_tokens(self) -> OAuthToken | None:
        data = self._read().get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = json.loads(tokens.model_dump_json())
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = json.loads(client_info.model_dump_json())
        self._write(data)

    def has_tokens(self) -> bool:
        return bool(self._read().get("tokens"))


async def _headless_redirect_handler(authorization_url: str) -> None:
    raise RobinhoodAuthError(
        "Robinhood requires a fresh interactive login (redirect to "
        f"{authorization_url}) and this is a headless process. Run "
        "`python scripts/robinhood_oauth_setup.py` interactively, then "
        "resume trading from the dashboard."
    )


async def _headless_callback_handler():
    # Should never actually be reached - _headless_redirect_handler
    # raises first. Exists because OAuthClientProvider requires both.
    raise RobinhoodAuthError(
        "Robinhood session needs re-authentication. Run "
        "`python scripts/robinhood_oauth_setup.py` interactively, then "
        "resume trading from the dashboard."
    )


def build_oauth_provider(
    storage: TokenStorage,
    redirect_handler: Callable[[str], Awaitable[None]],
    callback_handler: Callable[[], Awaitable[Any]],
    redirect_port: int = 3030,
) -> OAuthClientProvider:
    client_metadata = OAuthClientMetadata.model_validate({
        "client_name": CLIENT_NAME,
        "redirect_uris": [f"http://localhost:{redirect_port}/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    })
    return OAuthClientProvider(
        server_url=settings.ROBINHOOD_MCP_URL,
        client_metadata=client_metadata,
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


@asynccontextmanager
async def connect(oauth: OAuthClientProvider):
    """Low-level: opens one MCP session using a caller-supplied OAuth
    provider. scripts/robinhood_oauth_setup.py uses this directly with
    an *interactive* provider; open_session() below wraps it with a
    headless one for the broker."""
    async with httpx.AsyncClient(auth=oauth, follow_redirects=True, timeout=30.0) as http_client:
        async with streamable_http_client(
            url=settings.ROBINHOOD_MCP_URL, http_client=http_client
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session


@asynccontextmanager
async def open_session():
    """Headless session for RobinhoodBroker/scripts that must not
    prompt for login: raises RobinhoodAuthError instead."""
    storage = FileTokenStorage(settings.ROBINHOOD_TOKEN_FILE)
    if not storage.has_tokens():
        raise RobinhoodAuthError(
            f"No Robinhood credentials at {settings.ROBINHOOD_TOKEN_FILE}. "
            "Run `python scripts/robinhood_oauth_setup.py` once, "
            "interactively, before enabling live trading."
        )

    oauth = build_oauth_provider(storage, _headless_redirect_handler, _headless_callback_handler)
    try:
        async with connect(oauth) as session:
            yield session
    except RobinhoodAuthError:
        raise
    except Exception as e:
        raise RobinhoodAuthError(f"Robinhood MCP connection/auth failed: {e}") from e


def call_tool_sync(name: str, arguments: dict) -> Any:
    """Synchronous wrapper for BrokerAdapter's sync interface: opens a
    fresh session, calls one tool, closes. Simple and robust over
    efficient - fine at this call volume (a handful of calls per
    15-minute tick). Raises RobinhoodAuthError or RobinhoodToolError."""
    async def _run():
        async with open_session() as session:
            result = await session.call_tool(name, arguments)
            return extract_result(result)
    return asyncio.run(_run())


def extract_result(result) -> Any:
    """MCP tool results can carry structured output (result.structuredContent)
    or plain-text content that happens to be JSON. Prefer structured;
    fall back to parsing text; fall back to raw text.

    This - and every `.get(...)` field-name guess in robinhood_broker.py -
    is exactly what `python scripts/robinhood_list_tools.py` output needs
    to be checked against before trusting live trading. See the mapping
    comment at the top of robinhood_broker.py."""
    if getattr(result, "isError", False):
        raise RobinhoodToolError(_first_text(result) or "tool call failed with no error detail")

    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured

    text = _first_text(result)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _first_text(result) -> str | None:
    for item in getattr(result, "content", None) or []:
        if getattr(item, "type", None) == "text":
            return item.text
    return None
