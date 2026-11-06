# A sub-protocol is its parent only at the arguments it names: a
# `Child[int32]` is a `Base[int32]`, not a `Base[float]`.
from typing import Protocol
from tpy import int32


class Base[T](Protocol):
    def get(self) -> T: ...


class Child[T](Base[T], Protocol):
    pass


class Impl:
    def __init__(self) -> None:
        pass

    def get(self) -> int32:
        return 7


def use(b: Base[float]) -> None:
    print(b.get() + 0.5)


def fwd(c: Child[int32]) -> None:
    use(c)  # tpyc: error(/Type Child\[int32\] does not conform to protocol Base\[float\]/)


def main() -> None:
    fwd(Impl())


main()
