# `await` inside a `for ... else:` clause (Phase D3 of the generator ->
# resumable-frame migration -- the CFG now models break-vs-normal-exit, so a
# suspension in the else clause is supported for async too). The else runs on
# normal loop exit and is skipped by `break`; both arms are exercised.
import asyncio
from tpy import Int32


async def tick(label: str) -> None:
    print(label)


async def drive(brk: Int32) -> None:
    for i in range(3):
        if i == brk:
            break
        await tick("body")
    else:
        await tick("else")


async def caller() -> None:
    print("no break:")
    await drive(99)
    print("break:")
    await drive(1)


def main() -> None:
    asyncio.run(caller())


main()
