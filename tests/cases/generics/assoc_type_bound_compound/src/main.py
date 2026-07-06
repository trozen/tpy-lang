# Associated-type inference through a COMPOUND bound type-arg: R sits inside
# list[R] in the bound (T: Container[list[R]]), so solving it recurses from
# the conformer's get() -> list[int] into the element type.
from typing import Protocol


class Container[X](Protocol):
    def get(self) -> X: ...


class IntListBox:
    xs: list[int]

    def __init__(self, xs: list[int]):
        self.xs = xs

    def get(self) -> list[int]:
        return self.xs


def first[R, T: Container[list[R]]](x: T) -> R:
    return x.get()[0]


def main() -> None:
    print(first(IntListBox([7, 8, 9])))   # tpyc: ok


main()
