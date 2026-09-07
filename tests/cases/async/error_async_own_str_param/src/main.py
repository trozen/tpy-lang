# An `Own[str]` coroutine parameter: the sync signature spells the view
# while the frame field owns the buffer -- a form split, not a capture.
# `sink("hi")` is called, but the parameter shape alone is what rejects.
import asyncio
from tpy import Own


async def sink(s: Own[str]) -> None:  # tpyc: error(/res\.param_type:own_str/)
    await asyncio.sleep(0.001)
    print(s)


def main() -> None:
    asyncio.run(sink("hi"))


main()
