# Function-local variable seeded by an integer literal retro-widens to a
# fixed-unsigned type when first used at an ARG slot of that type. Mirrors
# the `offset = 0; ... _pcre_match(..., offset, ...)` shape in lib/tpy/re.py
# (the only literal-cast site B is designed to clean up).
from tpy import UInt32, UInt64


def take_u32(x: UInt32) -> UInt32:
    return x


def take_u64(x: UInt64) -> UInt64:
    return x


def loop_shape(limit: UInt64) -> UInt64:
    # offset starts at literal 0 (no annotation), then both the binop ARG
    # at line 18 and the comparison vs limit lock it in as UInt64.
    offset = 0
    while offset < limit:
        offset = take_u64(offset) + 1
    return offset


def repeated_use() -> None:
    # First call retro-widens; later use of the same local just sees UInt32.
    a = 5
    print(take_u32(a))
    print(take_u32(a + 1))


def literal_only_branch() -> None:
    # Reassignment from another integer literal keeps the seed alive, so a
    # later UInt64 demand still triggers retro-widen.
    n = 0
    if True:
        n = 7
    print(take_u64(n))


def main() -> None:
    print(loop_shape(3))
    repeated_use()
    literal_only_branch()


main()
