"""
Local reproduction / verification for the MCP handshake failure.

Runs an in-process FastMCP server on streamable-HTTP transport and drives
the JSON-RPC `initialize` handshake against it. On the broken combo
(fastmcp 2.13.1 + mcp 1.28.x) the server returns -32601 "Method not found";
on the fix combo it returns a proper handshake response with
`serverInfo` and `protocolVersion`.

Run this once per venv to check whether the combination works:
    /tmp/mcp-old-venv/bin/python tests/test_mcp_handshake.py   # broken
    /tmp/mcp-test-venv/bin/python tests/test_mcp_handshake.py  # fix
"""
import json
import socket
import threading
import time
from importlib.metadata import version

import httpx
from fastmcp import FastMCP


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
    app = FastMCP(name="probe")

    @app.tool()
    def ping() -> str:
        return "pong"

    app.run(transport="http", host="127.0.0.1", port=port)


INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0.0.1"},
    },
}


def _parse_sse(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


def run() -> int:
    port = _free_port()
    thread = threading.Thread(target=_serve, args=(port,), daemon=True)
    thread.start()
    _wait_for_port(port)

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    print(f"fastmcp={version('fastmcp')} mcp={version('mcp')}")

    results = {}
    for suffix, follow in [("/mcp", False), ("/mcp/", False), ("/mcp", True)]:
        url = f"http://127.0.0.1:{port}{suffix}"
        label = f"POST {suffix} follow_redirects={follow}"
        print(f"--- {label} ---")
        try:
            with httpx.Client(timeout=5.0, follow_redirects=follow) as client:
                resp = client.post(url, headers=headers, json=INIT_BODY)
        except Exception as e:
            print(f"  transport error: {e}")
            results[label] = "transport-error"
            continue
        print(f"  status={resp.status_code}")
        snippet = resp.text[:400].replace("\n", " ").replace("\r", "")
        print(f"  body[:400]={snippet}")
        if resp.status_code == 200:
            payload = _parse_sse(resp.text)
            if "error" in payload:
                results[label] = f"jsonrpc-error {payload['error']}"
            else:
                results[label] = "ok"
        else:
            results[label] = f"http-{resp.status_code}"

    print("--- summary ---")
    for k, v in results.items():
        print(f"  {k} -> {v}")

    return 0 if any(v == "ok" for v in results.values()) else 1

    print(f"status={resp.status_code}")
    print(f"body={resp.text!r}")

if __name__ == "__main__":
    raise SystemExit(run())
