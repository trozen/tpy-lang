# An escaping (`Callable`) lambda snapshots its captures by value, so a capture
# C++ cannot copy has no entry -- in a plain sync body, not only in a frame
# (the frame twin is error_frame_lambda_nocopy_snapshot, and both reject
# through the same entry builder). An ordinary borrowed PARAM is the shape the
# peel chain has to reach: `g` is declared `RefType[Guard]`, and it is the
# PAYLOAD under the wrapper -- a record with `__del__`, whose copy constructor
# C++ deletes -- that decides. Without the peel this reaches the toolchain as
# `[g](int32_t i) ...` over a deleted copy constructor. The `Own[Guard]`,
# local and frame-member shapes reject through the same predicate.
from typing import Callable
from tpy import int32


class Guard:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __del__(self) -> None:
        print("bye")


def apply(f: Callable[[int32], int32], v: int32) -> int32:
    return f(v)


def run(g: Guard) -> None:
    print(apply(lambda i: i + g.x, 1))  # tpyc: error(/lambda\.capture_nocopy/)


def main() -> None:
    keep = Guard(5)
    run(keep)


main()
