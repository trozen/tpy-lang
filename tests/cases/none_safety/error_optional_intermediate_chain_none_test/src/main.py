# `is not None` over a two-link field chain whose intermediate link is itself
# Optional: not lowered yet, so the case pins the reject.
from tpy import int32


class Inner:
    value: int32 | None

    def __init__(self, v: int32 | None) -> None:
        self.value = v


class Outer:
    inner: Inner

    def __init__(self, inner: Inner) -> None:
        self.inner = inner


class OptTop:
    inner: Inner | None

    def __init__(self, i: Inner) -> None:
        self.inner = i


def get_value(t: OptTop) -> int32:
    # An Optional-declared intermediate link reads through an unwrap the chain
    # rule excludes.
    if t.inner is not None and t.inner.value is not None:  # tpyc: error(/binop.shape.is not/)
        return t.inner.value
    return 0


def main() -> None:
    print(get_value(OptTop(Inner(5))))


main()
