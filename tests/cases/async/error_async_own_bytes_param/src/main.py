# An `Own[bytes]` coroutine parameter: the sync signature spells the view
# while the frame field owns the buffer -- a form split, not a capture. The
# call site is never reached; the parameter alone is the rejected shape.
import asyncio
from tpy import Own


async def sink(b: Own[bytes]) -> None:  # tpyc: error(/res\.param_type:own_bytes/)
    await asyncio.sleep(0.001)
    print(len(b))


def main() -> None:
    pass


main()
