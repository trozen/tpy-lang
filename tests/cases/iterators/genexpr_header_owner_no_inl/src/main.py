# A capture-free genexpr in a function templated only by a protocol-typed
# param: the frame struct sits in the header with that function's body, and a
# module with no generator of its own emits no `_inl.hpp`, so the frame's
# `__next__` is defined in the .cpp. (Its own case: any generator in the module
# would bring the inl header back.)
from typing import Iterable
from tpy import int32


def proto(it: Iterable[int32], xs: list[int32]) -> int32:
    # the genexpr reads neither `it` nor any local: its frame is no template.
    t = sum(x * 2 for x in xs)  # tpyc: ok
    for v in it:
        t += v
    return t


def main() -> None:
    extra = [1, 2]
    print("proto", proto(extra, [3, 4]))


main()
