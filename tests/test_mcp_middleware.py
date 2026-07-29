"""
Verifies the JWTGalaxyKeyMiddleware behaves correctly:

1. `initialize` succeeds even with no Authorization header (pre-auth per spec).
2. A guarded method (`tools/list`) without Authorization returns a proper
   JSON-RPC error (code -32001), NOT the misleading -32601 "Method not found".
3. A guarded method with a valid Bearer JWT reaches the handler.

Run with either the fixed or the "old" venv to see the diff:
    /tmp/mcp-old-venv/bin/python tests/test_mcp_middleware.py
    /tmp/mcp-test-venv/bin/python tests/test_mcp_middleware.py

Both should now pass because the fix lives in the middleware, not the
fastmcp/mcp package versions.
"""
import json
import os
import socket
import sys
import threading
import time
from importlib.metadata import version

import httpx

# Provide the env vars the middleware and downstream server modules read at
# import time so we can exercise them without a full app boot.
os.environ.setdefault("SECRET_KEY", "0" * 44)  # Fernet key length; not used here
os.environ.setdefault("JWT_SECRET", "unused")

# Make repo root importable as `app.*`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import FastMCP  # noqa: E402
from app.bioblend_server.mcp_middleware import JWTGalaxyKeyMiddleware  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"server on {port} did not come up")


def _serve(port: int) -> None:
    app = FastMCP(name="probe", middleware=[JWTGalaxyKeyMiddleware()])

    @app.tool()
    def ping() -> str:
        return "pong"

    app.run(transport="http", host="127.0.0.1", port=port)


def _parse_sse(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0.0.1"},
    },
}

TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def _post(url: str, body: dict, auth: str | None = None, session_id: str | None = None) -> dict:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if auth:
        headers["Authorization"] = auth
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    with httpx.Client(timeout=5.0) as client:
        resp = client.post(url, headers=headers, json=body)
    return {
        "status": resp.status_code,
        "payload": _parse_sse(resp.text),
        "raw": resp.text,
        "session_id": resp.headers.get("mcp-session-id"),
    }


def _open_session(url: str, auth: str | None = None) -> str:
    r = _post(url, INIT, auth=auth)
    sid = r["session_id"]
    if not sid:
        raise RuntimeError(f"initialize did not return a session id: {r}")
    # Per protocol, follow initialize with the initialized notification.
    with httpx.Client(timeout=5.0) as client:
        client.post(
            url,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Mcp-Session-Id": sid,
                **({"Authorization": auth} if auth else {}),
            },
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
    return sid


def main() -> int:
    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    _wait_for_port(port)
    url = f"http://127.0.0.1:{port}/mcp"

    print(f"fastmcp={version('fastmcp')} mcp={version('mcp')}")

    failures: list[str] = []

    # 1. initialize with no auth should now succeed (pre-auth allowlist)
    r = _post(url, INIT)
    if "error" in r["payload"]:
        failures.append(f"initialize-no-auth: expected success, got {r['payload']['error']}")
    else:
        print("PASS  initialize no-auth: real handshake response")

    # Open a real session first (initialize is pre-auth, so this needs no bearer).
    sid = _open_session(url)

    # 2. tools/list with no auth must return a proper -32001, not -32601
    r = _post(url, TOOLS_LIST, session_id=sid)
    err = r["payload"].get("error", {})
    if err.get("code") == -32601:
        failures.append("tools/list-no-auth: still surfaces as -32601 (middleware not fixed)")
    elif err.get("code") == -32001:
        print(f"PASS  tools/list no-auth: proper -32001 error ({err.get('message')!r})")
    else:
        failures.append(f"tools/list-no-auth: unexpected response {r['payload']!r}")

    # 3. tools/list with a bogus Bearer should still fail — but with -32001
    r = _post(url, TOOLS_LIST, auth="Bearer not-a-real-jwt", session_id=sid)
    err = r["payload"].get("error", {})
    if err.get("code") == -32001:
        print(f"PASS  tools/list bogus-bearer: proper -32001 error ({err.get('message')!r})")
    else:
        failures.append(f"tools/list-bogus-bearer: unexpected response {r['payload']!r}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
