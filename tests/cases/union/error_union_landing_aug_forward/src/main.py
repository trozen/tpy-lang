# A `+=` fallback through `__add__` stores its result as `v = v + 1`: a subclass result is upcast,
# warned; another type is refused, as `o = o + 2` is (LANGUAGE_FEATURES.md, the `__add__` fallback).
from tpy import ValueType, int32


class Base(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Sub":
        return Sub(self.n + k)


class Sub(Base, ValueType):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class Other(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> int32:
        return self.n + k


def main() -> None:
    v = Base(1)
    v += 2  # tpyc: warning(/upcast narrows 'Sub' to 'Base'/)
    o = Other(5)
    o += 2  # tpyc: error(/Type mismatch in '\+=' to 'o': expected Other, got int32/)
    print(v.n, o)


main()
