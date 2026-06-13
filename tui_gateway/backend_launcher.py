#!/usr/bin/env python3
"""Launch Hermes/Autolycus backend for desktop.

Starts `hermes dashboard` (FastAPI server with REST + WebSocket API).
The desktop connects to this server.

Usage:
    python backend_launcher.py --port 9119
"""

import argparse
import subprocess
import sys
import os
import signal

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9119)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # Find hermes/autolycus executable
    python = sys.executable
    cmd = [python, "-m", "hermes_cli.main", "dashboard", "--port", str(args.port), "--host", args.host, "--no-open"]

    # Try autolycus first, then hermes
    for module in ["hermes_cli.main", "autolycus"]:
        try:
            result = subprocess.run(
                [python, "-m", module, "dashboard", "--help"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                cmd = [python, "-m", module, "dashboard", "--port", str(args.port), "--host", args.host, "--no-open"]
                break
        except Exception:
            continue

    print(f"Starting backend: {' '.join(cmd)}", flush=True)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )

    # Wait for server to be ready
    import time
    start = time.time()
    while time.time() - start < 30:
        import urllib.request
        try:
            urllib.request.urlopen(f"http://{args.host}:{args.port}/api/status", timeout=1)
            print(f"WS_READY:{args.port}", flush=True)
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("ERROR: Backend failed to start", file=sys.stderr)
        sys.exit(1)

    # Keep running
    try:
        proc.wait()
    except KeyboardInterrupt:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)

if __name__ == "__main__":
    main()
