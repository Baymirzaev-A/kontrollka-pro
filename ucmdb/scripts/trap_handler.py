#!/usr/bin/env python3
import sys
import json
import aiohttp
import asyncio

UCMDB_API = "http://localhost:8000/api/traps/ingest"


async def send_trap(data: dict):
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(UCMDB_API, json=data, timeout=aiohttp.ClientTimeout(total=5))
    except Exception as e:
        print(f"Error sending trap: {e}", file=sys.stderr)


async def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        parts = line.split(' ', 4)
        if len(parts) >= 5:
            trap = {
                "timestamp": f"{parts[0]} {parts[1]}",
                "host": parts[2],
                "type": parts[3],
                "data": parts[4] if len(parts) > 4 else "",
                "community": "public"
            }
            await send_trap(trap)


if __name__ == "__main__":
    asyncio.run(main())