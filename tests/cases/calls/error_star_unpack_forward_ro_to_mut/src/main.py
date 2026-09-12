# Forwarding a readonly vararg (`*xs: readonly[Box]`, sema type
# Span[readonly[Box]]) into a *mutable* *args slot is rejected cleanly -- the
# source's own readonly span type distinguishes it; no provenance exemption
# lets it through into a varargs<Box> that exposes mutable element access.
from tpy import int32, readonly, nocopy


@nocopy
class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def take_mut(*items: Box) -> int32:
    n: int32 = 0
    for b in items:
        n += b.val
    return n


def fwd(*xs: readonly[Box]) -> int32:
    return take_mut(*xs)  # tpyc: error(/Cannot pass readonly\[Box\] as mutable Box when unpacking into \*args/)


def main() -> None:
    a = Box(1)
    print(fwd(a))


main()
