"""Async wrappers that keep argon2 off the event loop and bound its memory use.

Each hash or verification takes tens of milliseconds of CPU and about 19 MiB of memory.
Running it in a worker thread keeps other requests responsive, and the semaphore caps
how many run at once, so a burst of logins cannot exhaust a small container's memory.
"""

import asyncio
from typing import Final

from app.core import security

MAX_CONCURRENT_HASHES: Final = 4

_slots = asyncio.Semaphore(MAX_CONCURRENT_HASHES)


async def hash_password(password: str) -> str:
    async with _slots:
        return await asyncio.to_thread(security.hash_password, password)


async def verify_password(password_hash: str, password: str) -> bool:
    async with _slots:
        return await asyncio.to_thread(security.verify_password, password_hash, password)


async def verify_dummy_password(password: str) -> None:
    async with _slots:
        await asyncio.to_thread(security.verify_dummy_password, password)
