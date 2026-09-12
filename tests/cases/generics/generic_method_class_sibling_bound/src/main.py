# A generic method whose bound names a CLASS-level sibling type param
# ([R on the class] used in `def pick[T: Container[R]]`). Regression guard:
# bound validation must substitute with the merged class+method map, or the
# class param R is unresolved and the check crashes with an unlocated error.
from typing import Protocol
from tpy import int32


class Container[T](Protocol):
    def get(self) -> T: ...


class IntBox:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
    def get(self) -> int32:
        return self.v


class Runner[R]:
    def pick[T: Container[R]](self, x: T) -> R:
        return x.get()


def main() -> None:
    print(Runner[int32]().pick[IntBox](IntBox(42)))   # tpyc: ok


main()
