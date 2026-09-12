# Opt-in fast-path pattern: `Iterable[T] | NativeIterable[T]` + isinstance
# narrowing. The NativeIterable branch compiles to range-for via begin/end;
# the Iterable branch falls back to the universal __iter__/__next__ loop.
# The runtime assertions use list (clearly NativeIterable in both tpyc and
# CPython) so outputs match across both runtimes; the narrowing codegen
# itself is verified by the generated .hpp snapshot (distinct branches).
from typing import Iterable
from tpy import int32, NativeIterable


def sum_fast(it: Iterable[int32] | NativeIterable[int32]) -> int32:
    total: int32 = 0
    if isinstance(it, NativeIterable):
        # Narrowed to NativeIterable[int32] -- emitted as begin/end range-for.
        for x in it:
            total += x
    else:
        # Still Iterable[int32] -- emitted as universal __iter__/__next__.
        for x in it:
            total += x
    return total


def sum_nested(it: Iterable[int32] | NativeIterable[int32], flag: bool) -> int32:
    # Exercise the protocol_narrowings save/restore across nested branches:
    # an inner if-block inside the narrowing branch must not leak its own
    # narrowing out, and the narrowing must still be visible after the
    # inner block re-exits.
    total: int32 = 0
    if isinstance(it, NativeIterable):
        if flag:
            for x in it:
                total += x
        # Narrowing still in effect here -- this loop should also be range-for.
        for x in it:
            total += x * 10
    else:
        for x in it:
            total += x
    return total


def sum_elif(kind: int32, it: Iterable[int32] | NativeIterable[int32]) -> int32:
    # Exercise elif-branch save/restore: the narrowing must be local to the
    # isinstance branch and not leak across elif/else.
    total: int32 = 0
    if kind == 0:
        total = 1
    elif isinstance(it, NativeIterable):
        for x in it:
            total += x
    elif kind == 1:
        total = 99
    else:
        for x in it:
            total += x
    return total


def main() -> None:
    nums: list[int32] = [1, 2, 3, 4]

    # Basic narrowing.
    print(sum_fast(nums))                # 10

    # Nested narrowing inside the NativeIterable branch.
    print(sum_nested(nums, True))        # 10 + 100 = 110
    print(sum_nested(nums, False))       # 100

    # Elif-chain narrowing.
    print(sum_elif(0, nums))             # 1 (kind==0 short-circuit)
    print(sum_elif(2, nums))             # 10 (falls to NativeIterable branch)
    print(sum_elif(1, nums))             # 99 (kind==1 branch, skipped NativeIterable)


main()
