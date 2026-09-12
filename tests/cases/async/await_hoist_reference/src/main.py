# Multi-await with a reference-typed local (`list[int32]`) live across
# a suspension. Validates that non-value-type hoisted locals get a
# `tpy::frame_slot<T>` slot in the coro frame (not bare storage), which
# is how the codegen handles "uninitialized until first assignment +
# preserved across suspensions" for non-value types.
from tpy import int32, Own
from tpy.coro import poll_once

async def make_list() -> Own[list[int32]]:
    return [int32(10), int32(20), int32(30)]

async def get_multiplier() -> int32:
    return int32(2)

async def caller() -> int32:
    # `xs: list[int32]` is set in region 0 (before await get_multiplier())
    # and read in region 2 (after that await). Must be hoisted as
    # `tpy::frame_slot<std::vector<int32_t>> xs;` on the frame.
    xs = await make_list()
    multiplier = await get_multiplier()
    total = int32(0)
    for x in xs:
        total = total + x * multiplier
    return total

def main() -> None:
    print(poll_once(caller()).value())

main()
