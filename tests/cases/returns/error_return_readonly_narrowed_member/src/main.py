# A member narrowed out of a readonly union is readonly: it does not fit a
# mutable reference result, as a narrowed readonly Optional does not.
from tpy import int32, readonly


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Inner:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value


def f(u: readonly[Box | Inner], d: Inner) -> Inner:
    if isinstance(u, Inner):
        return u  # tpyc: error(/Cannot return readonly\[Inner\] at mutable return type/)
    return d


def main() -> None:
    i = Inner(2)
    u: Box | Inner = i
    d = Inner(9)
    f(u, d).value = 5
    print(i.value)


main()
