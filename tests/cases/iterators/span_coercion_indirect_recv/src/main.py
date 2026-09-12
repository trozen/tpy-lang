# The `__span__()` coercion off an INDIRECT receiver: `self` inside a method and
# a narrowed pointer-repr `Buf | None` name both reach the member through a
# pre-deref (`(*name).__span__()`), where a plain record name calls it directly.
from tpy import int32, Span, readonly


class Buf:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    def __span__(self) -> Span[readonly[int32]]:
        return self.xs

    def own_total(self) -> int32:
        return total(self)      # tpyc: ok -- `self` is the indirect receiver


def total(sp: Span[readonly[int32]]) -> int32:
    t = 0
    for x in sp:
        t += x
    return t


def opt_total(b: Buf | None) -> int32:
    if b is not None:
        return total(b)         # tpyc: ok -- a narrowed pointer name derefs
    return -1


def plain_total(b: Buf) -> int32:
    return total(b)             # tpyc: ok -- a record name calls directly


def main() -> None:
    b = Buf()
    print(b.own_total(), opt_total(b), plain_total(b), opt_total(None))


main()
