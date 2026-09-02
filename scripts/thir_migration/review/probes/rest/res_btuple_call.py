from tpy import Int32
class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
from typing import Iterator
def mk(xs: list[P]) -> tuple[Int32, P]:
    return (1, xs[0])
def g(xs: list[P]) -> Iterator[tuple[Int32, P]]:
    yield mk(xs)
    yield mk(xs)
def main() -> None:
    pass
main()
