# The coroutine flavour of the container frame param: an async fn's frame is a
# Poll state machine rather than a generator lambda, but its param families
# read the same reference axis, so bytearray and Array capture like list does.
import asyncio
from tpy import Int32, Array


async def fill_buf(b: bytearray) -> Int32:  # tpyc: ok
    await asyncio.sleep(0)
    b.append(66)
    return len(b)


async def bump_arr(a: Array[Int32, 2]) -> Int32:  # tpyc: ok
    await asyncio.sleep(0)
    a[0] = a[0] + 5
    return a[0]


async def push_list(xs: list[Int32]) -> Int32:
    await asyncio.sleep(0)
    xs.append(9)
    return len(xs)


async def drive() -> None:
    # The coroutine mutates through its captured param; the caller's own
    # object must show the change, which a by-value frame capture would hide.
    b = bytearray(b"a")
    print(await fill_buf(b), len(b))

    a = Array[Int32, 2]()
    print(await bump_arr(a), a[0])

    xs = [1]
    print(await push_list(xs), len(xs))


asyncio.run(drive())
