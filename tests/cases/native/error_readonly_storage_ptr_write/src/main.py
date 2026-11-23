# `ptr()` off a readonly storage points at readonly storage: a write through
# it is refused.
from tpy import int32, readonly
from tpy.mem import UninitStorage


def poke(s: readonly[UninitStorage[int32]]) -> None:
    p = s.ptr()
    sp = p.span(1)
    sp[0] = 9  # tpyc: error(/readonly/)


def main() -> None:
    s = UninitStorage[int32]()
    s.construct(1)
    poke(s)
    s.reset()


main()
