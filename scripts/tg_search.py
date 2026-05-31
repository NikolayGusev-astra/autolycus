#!/usr/bin/env python3
"""
Telegram channel/group search via Telethon.

Usage:
    python3 tg_search.py "Ford Explorer ремонт" [--limit 10] [--type channel|group|all]
    python3 tg_search.py --read @channel_name [--limit 20]
    python3 tg_search.py --history @channel_name "ключевое слово" [--limit 50]

Requirements: pip install telethon
Credentials: /root/wiki/credentials/telegram-api.json (api_id, api_hash)
"""

import asyncio, json, argparse, sys, os
from datetime import datetime

CRED_PATH = "/root/wiki/credentials/telegram-api.json"
SESSION_PATH = "/root/.cache/telethon_session"

def load_creds():
    with open(CRED_PATH) as f:
        return json.load(f)

async def search_messages(query: str, limit: int = 10, chat_type: str = "all"):
    """Search public channels/groups by message content."""
    from telethon import TelegramClient
    from telethon.tl.functions.messages import SearchRequest
    from telethon.tl.types import InputMessagesFilterEmpty

    creds = load_creds()
    client = TelegramClient(SESSION_PATH, creds["api_id"], creds["api_hash"])
    await client.start()

    try:
        results = await client(SearchRequest(
            peer="",
            q=query,
            filter=InputMessagesFilterEmpty(),
            min_date=None,
            max_date=None,
            offset_id=0,
            add_offset=0,
            limit=limit,
            max_id=0,
            min_id=0,
            from_id=None,
            hash=0
        ))

        output = []
        for msg in results.messages:
            chat = await client.get_entity(msg.peer_id) if msg.peer_id else None
            chat_title = getattr(chat, 'title', 'N/A') if chat else 'N/A'
            chat_username = getattr(chat, 'username', None) if chat else None
            username_str = f"@{chat_username}" if chat_username else "N/A"

            text = msg.text or "[no text]"
            if len(text) > 300:
                text = text[:300] + "..."

            date_str = msg.date.strftime("%Y-%m-%d %H:%M") if msg.date else "N/A"

            output.append({
                "chat": chat_title,
                "username": username_str,
                "date": date_str,
                "text": text,
                "url": f"https://t.me/{chat_username}" if chat_username else "N/A"
            })

        return output
    finally:
        await client.disconnect()

async def read_channel(username: str, limit: int = 20, search_query: str = None):
    """Read recent messages from a public channel, optionally filter by keyword."""
    from telethon import TelegramClient

    creds = load_creds()
    client = TelegramClient(SESSION_PATH, creds["api_id"], creds["api_hash"])
    await client.start()

    try:
        entity = await client.get_entity(username)
        title = getattr(entity, 'title', username)

        output = []
        async for msg in client.iter_messages(entity, limit=limit):
            text = msg.text or ""
            if search_query and search_query.lower() not in text.lower():
                continue
            if not text.strip():
                continue

            date_str = msg.date.strftime("%Y-%m-%d %H:%M") if msg.date else "N/A"
            if len(text) > 400:
                text = text[:400] + "..."

            output.append({
                "date": date_str,
                "text": text
            })

        return {"channel": title, "username": f"@{username}", "messages": output}
    finally:
        await client.disconnect()

def main():
    parser = argparse.ArgumentParser(description="Telegram search via Telethon")
    sub = parser.add_subparsers(dest="cmd")

    # Search
    s = sub.add_parser("search", help="Search messages across Telegram")
    s.add_argument("query", help="Search query")
    s.add_argument("--limit", type=int, default=10, help="Result limit")
    s.add_argument("--type", choices=["channel", "group", "all"], default="all")

    # Read channel
    r = sub.add_parser("read", help="Read recent messages from a channel")
    r.add_argument("username", help="Channel username (with or without @)")
    r.add_argument("--limit", type=int, default=20)
    r.add_argument("--q", help="Filter messages by keyword")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    if args.cmd == "search":
        results = asyncio.run(search_messages(args.query, args.limit, args.type))
        for r in results:
            print(f"\n{'='*60}")
            print(f"Канал: {r['chat']} ({r['username']})")
            print(f"Дата: {r['date']}")
            print(f"URL: {r['url']}")
            print(f"{'-'*60}")
            print(r['text'])

    elif args.cmd == "read":
        username = args.username.lstrip("@")
        result = asyncio.run(read_channel(username, args.limit, args.q))
        print(f"\n📡 {result['channel']} ({result['username']}) — {len(result['messages'])} сообщений\n")
        for msg in result['messages']:
            print(f"[{msg['date']}]")
            print(msg['text'])
            print(f"{'-'*40}")

if __name__ == "__main__":
    main()
