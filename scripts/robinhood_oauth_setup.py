#!/usr/bin/env python3
"""One-time INTERACTIVE login to Robinhood's Trading MCP. This needs a
real browser login - it cannot be automated - so it's meant to be run
by a human, not by the scheduler or any other unattended process:

    docker compose exec day-trader-app python scripts/robinhood_oauth_setup.py

It prints an authorization URL - open it in a browser and log into
Robinhood. This script listens for the OAuth callback on container
port 3030 (published to the host as the same port, see
docker-compose.yml). If your browser runs on a different machine than
this devbox (the normal case over SSH), forward the port first:

    ssh -L 3030:localhost:3030 devbox

then open the printed URL in a browser on that machine. If the
automatic callback doesn't arrive (port forwarding not set up, etc.)
this falls back to letting you paste the redirected URL in by hand.

On success, tokens (and the dynamically-registered MCP client info)
are saved to ROBINHOOD_TOKEN_FILE so app/brokers/robinhood_broker.py
can run headless afterward - re-run this any time re-authentication is
needed (e.g. after a RobinhoodAuthError pauses the bot). Next step
after this succeeds: python scripts/robinhood_list_tools.py
"""
import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.client.auth import AuthorizationCodeResult  # noqa: E402

from app.brokers.robinhood_oauth import (  # noqa: E402
    FileTokenStorage,
    RobinhoodAuthError,
    build_oauth_provider,
    connect,
    extract_result,
)
from app.config import settings  # noqa: E402

CALLBACK_PORT = 3030


class _CallbackHTTPHandler(BaseHTTPRequestHandler):
    callback_data: dict = {}

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            self.callback_data["code"] = params["code"][0]
            self.callback_data["state"] = params.get("state", [None])[0]
            self.callback_data["iss"] = params.get("iss", [None])[0]
            body = b"<html><body><h1>Authorized</h1><p>You can close this tab and return to the terminal.</p></body></html>"
            self.send_response(200)
        elif "error" in params:
            self.callback_data["error"] = params["error"][0]
            body = f"<html><body><h1>Authorization failed</h1><p>{params['error'][0]}</p></body></html>".encode()
            self.send_response(400)
        else:
            self.send_response(404)
            body = b""
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # quiet - we print our own status lines


def _start_callback_server(callback_data: dict) -> HTTPServer:
    _CallbackHTTPHandler.callback_data = callback_data
    # 0.0.0.0, not localhost: this runs inside the container, and needs
    # to be reachable via the published Docker port from outside it.
    server = HTTPServer(("0.0.0.0", CALLBACK_PORT), _CallbackHTTPHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def _redirect_handler(authorization_url: str) -> None:
    print("\n" + "=" * 72)
    print("Open this URL in a browser and log into Robinhood:\n")
    print(authorization_url)
    print(f"\nListening for the callback on container port {CALLBACK_PORT}.")
    print("If your browser is on a different machine than this devbox:")
    print(f"    ssh -L {CALLBACK_PORT}:localhost:{CALLBACK_PORT} devbox")
    print("then open the URL above in a browser on that machine.")
    print("=" * 72 + "\n")


def _make_callback_handler(callback_data: dict):
    async def _callback_handler() -> AuthorizationCodeResult:
        print(f"Waiting up to 5 minutes for the callback on :{CALLBACK_PORT} ...")
        deadline = time.time() + 300
        while time.time() < deadline:
            if callback_data.get("code"):
                break
            if callback_data.get("error"):
                raise RobinhoodAuthError(f"Robinhood returned an error: {callback_data['error']}")
            time.sleep(0.5)
        else:
            print("\nNo automatic callback received (port forwarding not set up?).")
            pasted = input(
                "Paste the full redirected URL you landed on (or just its "
                "'code' parameter) here: "
            ).strip()
            if pasted.startswith("http"):
                parsed = parse_qs(urlparse(pasted).query)
                callback_data["code"] = parsed.get("code", [None])[0]
                callback_data["state"] = parsed.get("state", [None])[0]
                callback_data["iss"] = parsed.get("iss", [None])[0]
            else:
                callback_data["code"] = pasted

        if not callback_data.get("code"):
            raise RobinhoodAuthError("No authorization code received - aborting.")

        return AuthorizationCodeResult(
            code=callback_data["code"],
            state=callback_data.get("state"),
            iss=callback_data.get("iss"),
        )

    return _callback_handler


async def main():
    print(f"Token file: {settings.ROBINHOOD_TOKEN_FILE}")
    print(f"MCP server: {settings.ROBINHOOD_MCP_URL}\n")

    storage = FileTokenStorage(settings.ROBINHOOD_TOKEN_FILE)
    callback_data: dict = {}
    server = _start_callback_server(callback_data)

    try:
        oauth = build_oauth_provider(
            storage, _redirect_handler, _make_callback_handler(callback_data),
            redirect_port=CALLBACK_PORT,
        )
        async with connect(oauth) as session:
            print("Connected and authenticated.\n")
            result = await session.call_tool("get_accounts", {})
            print("get_accounts response - VERIFY this is your isolated Agentic account:")
            print(json.dumps(extract_result(result), indent=2, default=str))
    finally:
        server.shutdown()

    print(f"\nTokens saved to {settings.ROBINHOOD_TOKEN_FILE}.")
    print("Next: python scripts/robinhood_list_tools.py")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RobinhoodAuthError as e:
        print(f"\nSetup failed: {e}")
        raise SystemExit(1)
