# A coroutine returning a single-element `tuple[T]`: the one-slot tuple is
# parenthesized rather than brace-initialized.
import asyncio


async def solo[T](x: T) -> tuple[T]:
    await asyncio.sleep(0)
    # The single-element tuple return is the subject.
    return (x,)


async def go() -> None:
    t = await solo(7)
    print(t[0])


def main() -> None:
    asyncio.run(go())


main()
