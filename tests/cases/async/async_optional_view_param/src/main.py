# A `bytes | None` / `str | None` async-def param is captured OWNED in the coro
# frame; reading content (indexing) after the await guards against a dangling
# borrow of the literal arg's storage across the suspension.
import asyncio
from typing import Optional


async def first_bytes(b: bytes | None) -> int:
    await asyncio.sleep(0)
    if b is None:
        return -1
    return int(b[0]) + int(b[1]) + int(b[2])   # reads buffer content post-await


async def str_len(s: Optional[str]) -> int:
    await asyncio.sleep(0)
    return len(s) if s is not None else -1


async def main_coro() -> None:
    print(await first_bytes(b"abc"))   # literal temporary, read across suspension
    print(await first_bytes(None))
    print(await str_len("hello"))
    print(await str_len(None))


def main() -> None:
    asyncio.run(main_coro())


main()
