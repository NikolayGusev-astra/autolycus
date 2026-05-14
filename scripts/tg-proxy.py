#!/usr/bin/env python3
"""TCP forwarder: local -> Telegram Bot API via direct IP"""
import asyncio, sys

TARGET = ("149.154.167.220", 443)
BIND = ("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 8443)

async def pipe(r, w):
    try:
        while True:
            d = await r.read(65536)
            if not d: break
            w.write(d); await w.drain()
    except: pass
    finally:
        for h in (w,): 
            try: h.close()
            except: pass

async def handle(r, w):
    try:
        rr, rw = await asyncio.open_connection(*TARGET)
        await asyncio.gather(pipe(r, rw), pipe(rr, w))
    except: 
        try: w.close()
        except: pass

async def main():
    s = await asyncio.start_server(handle, *BIND)
    print(f"TG proxy :{BIND[1]} -> {TARGET[0]}:{TARGET[1]}")
    async with s: await s.serve_forever()

asyncio.run(main())
