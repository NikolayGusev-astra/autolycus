#!/usr/bin/env python3
"""Standalone WebSocket server for Hermes/Autolycus backend.

Usage:
    python ws_server.py --port 8443
    python ws_server.py --port 0  # OS picks free port, prints WS_READY:PORT

Wire protocol: newline-delimited JSON-RPC 2.0 (same as tui_gateway.stdio).
"""

import argparse
import asyncio
import json
import logging
import os
import sys

# Ensure autolycus is importable
_src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from tui_gateway.server import dispatch, resolve_skin
from tui_gateway.ws import WSTransport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
_log = logging.getLogger("ws_server")


async def handle_client(ws, path=None):
    loop = asyncio.get_running_loop()
    transport = WSTransport(ws, loop)

    # Send gateway.ready event
    await transport.write_async({
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "gateway.ready", "payload": {"skin": resolve_skin()}},
    })

    try:
        async for message in ws:
            try:
                req = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                transport.write({
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": "parse error"},
                    "id": None,
                })
                continue

            resp = dispatch(req, transport)
            if resp is not None:
                if not transport.write(resp):
                    break
    except Exception as e:
        _log.debug("Client disconnected: %s", e)
    finally:
        transport.close()


async def main():
    parser = argparse.ArgumentParser(description="Hermes/Autolycus WebSocket server")
    parser.add_argument("--port", type=int, default=8443, help="Port (0 = auto)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    args = parser.parse_args()

    try:
        import websockets
    except ImportError:
        print("ERROR: websockets package required. Install: pip install websockets", file=sys.stderr)
        sys.exit(1)

    async def handler(ws, path=None):
        await handle_client(ws, path)

    server = await websockets.serve(handler, args.host, args.port)
    actual_port = server.sockets[0].getsockname()[1]

    # Signal readiness to parent process (Tauri desktop)
    print(f"WS_READY:{actual_port}", flush=True)
    _log.info("WebSocket server listening on %s:%d", args.host, actual_port)

    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
