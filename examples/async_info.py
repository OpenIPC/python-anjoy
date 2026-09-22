#!/usr/bin/env python3
"""Async device-info fetch."""
import asyncio
import sys
from anjoy.aio import AsyncAnjoyClient

async def main(host):
    cam = AsyncAnjoyClient(host, "admin", "123456")
    print(await cam.info())

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "192.168.0.123"))
