# Reseating a pointer-repr Optional local: two rvalue reseats reuse the same
# hidden slot, and an lvalue reseat from a field aliases it -- so mutating
# through the reseated name is visible on the record it came from.
from tpy import Int32, Own


class Inner:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


class Box:
    inner: Inner

    def __init__(self, inner: Own[Inner]) -> None:
        self.inner = inner


def double_rvalue_reseat() -> Int32:
    p: Inner | None
    p = Inner(1)
    p = Inner(2)  # the second reseat reuses the first's slot
    if p is not None:
        return p.value
    return 0


def lvalue_reseat(b: Box) -> Int32:
    p: Inner | None = None
    p = b.inner  # an lvalue reseat -- an alias, not a copy
    if p is not None:
        p.value = 9
    return b.inner.value


def main() -> None:
    print(double_rvalue_reseat(), lvalue_reseat(Box(Inner(3))))


main()
