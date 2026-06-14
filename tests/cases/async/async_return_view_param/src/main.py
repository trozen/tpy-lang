# An async def returning a str param (string_view) converts to the owned
# std::string return slot like the sync return path, across all three async sites.
import asyncio
from typing import Optional


async def direct(tag: str) -> str:
    return tag  # direct site


async def in_finally(tag: str) -> str:
    try:
        return tag  # finally-chain site
    finally:
        print("cleanup")


async def pending_slot(tag: str) -> str:
    try:
        return tag  # pending-return slot: an await in the finally routes the return through it
    finally:
        await asyncio.sleep(0)
        print("done")


async def opt_ternary(tag: Optional[str]) -> str:
    return tag if tag is not None else "fallback"


async def main_coro() -> None:
    print(await direct("a"))
    print(await in_finally("b"))
    print(await pending_slot("c"))
    print(await opt_ternary("d"))
    print(await opt_ternary(None))


def main() -> None:
    asyncio.run(main_coro())


main()
