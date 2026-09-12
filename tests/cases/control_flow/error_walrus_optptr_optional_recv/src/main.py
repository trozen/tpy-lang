# A pointer-Optional walrus over a container element keeps the ELEMENT's
# optional whole, which suppresses the receiver's own None check too -- so an
# unproven-Optional RECEIVER must keep rejecting rather than dereference it
# blind.
from tpy import int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(xs: list[Rec | None] | None) -> int32:
    # `xs` is not proven non-None, so the element read would need a checked
    # deref the whole-read lowering cannot spell.
    if (r := xs[0]) is not None:  # tpyc: error(/expr\.walrus/)
        return r.n
    return 0


def main() -> None:
    xs: list[Rec | None] = [Rec(4)]
    print(f(xs))


main()
