# A coroutine returning a tuple with a VIEW element: a view pins a lifetime
# the frame's return slot cannot hold. The call site is never reached; the
# signature alone is the rejected shape.
import asyncio
from tpy import int32, StrView


async def f(s: StrView) -> tuple[StrView, int32]:  # tpyc: error(/res\.return_type/)
    await asyncio.sleep(0)
    # The returned tuple carries a view element.
    return (s, 1)


def main() -> None:
    pass


main()
