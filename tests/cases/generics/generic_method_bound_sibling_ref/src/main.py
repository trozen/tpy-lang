# The sibling-referencing bound ([R, T: Container[R]]) on a generic METHOD --
# exercises the methods.py bound-validation caller, not just the free-function
# one, so the fix covers both sites.
from typing import Protocol
from tpy import Int32


class Container[T](Protocol):
    def get(self) -> T: ...


class IntBox:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
    def get(self) -> Int32:
        return self.v


class Runner:
    def pick[R, T: Container[R]](self, x: T) -> R:
        return x.get()


def main() -> None:
    r = Runner()
    print(r.pick[Int32, IntBox](IntBox(42)))   # tpyc: ok


main()
