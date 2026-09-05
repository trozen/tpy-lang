# A coroutine's Own[container] return slot holds the container by value, so
# every reference-typed container spells the same `Poll<T>` payload. bytearray
# and Array are reference types like list/dict/set and take the same slot.
#
# The `Box` leg is the one that proves the frame TRANSFERS the container
# rather than copying it out: `Box` is @nocopy, so a copy at the await
# boundary would be a C++ compile error instead of an invisible duplicate.
import asyncio

from tpy import Array, Int32, Own
from tplib.box import Box


async def make_list() -> Own[list[Int32]]:
    return [1, 2]


async def make_bytes() -> Own[bytearray]:      # the bytearray return slot
    return bytearray(b"ab")


async def make_array() -> Own[Array[Int32, 3]]:  # the Array return slot
    return [7, 8, 9]


async def make_boxes() -> Own[list[Box[Int32]]]:   # the @nocopy payload
    return [Box(1), Box(2)]


async def drive() -> None:
    xs = await make_list()
    xs.append(3)                                # the awaited value is owned here
    print(len(xs))
    ba = await make_bytes()
    ba.append(99)
    print(len(ba), ba[2])
    arr = await make_array()
    arr[0] = 70
    print(arr[0], arr[2])
    bs = await make_boxes()
    bs.append(Box(3))
    print(len(bs))


asyncio.run(drive())
