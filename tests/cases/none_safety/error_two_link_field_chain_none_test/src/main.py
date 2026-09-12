# `is not None` over a TWO-link record field chain (`t.outer.inner.value`):
# TPy lowers the one-link form only, so the case pins the reject.
from tpy import int32


class Inner:
    value: int32 | None

    def __init__(self, v: int32 | None) -> None:
        self.value = v


class Outer:
    inner: Inner

    def __init__(self, inner: Inner) -> None:
        self.inner = inner


class Top:
    outer: Outer

    def __init__(self, o: Outer) -> None:
        self.outer = o


def get_value(t: Top) -> int32:
    # The chain rule covers ONE plain record link; a second link is out.
    if t.outer.inner.value is not None:  # tpyc: error(/binop.shape.is not/)
        return t.outer.inner.value
    return 0


def main() -> None:
    print(get_value(Top(Outer(Inner(5)))))


main()
