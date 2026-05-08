# Multi-await with a reference-typed local (`list[Int32]`) live across
# a suspension. Validates that non-value-type hoisted locals get an
# `std::optional<T>` slot in the coro frame (not bare storage), which
# is how the codegen handles "uninitialized until first assignment +
# preserved across suspensions" for non-value types.
from tpy import Int32, Own
from tpy.coro import poll_once

async def make_list() -> Own[list[Int32]]:
    return [Int32(10), Int32(20), Int32(30)]

async def get_multiplier() -> Int32:
    return Int32(2)

async def caller() -> Int32:
    # `xs: list[Int32]` is set in region 0 (before await get_multiplier())
    # and read in region 2 (after that await). Must be hoisted as
    # `std::optional<std::vector<int32_t>> xs;` on the frame.
    xs = await make_list()
    multiplier = await get_multiplier()
    total = Int32(0)
    for x in xs:
        total = total + x * multiplier
    return total

def main() -> None:
    print(poll_once(caller()).value())

main()
