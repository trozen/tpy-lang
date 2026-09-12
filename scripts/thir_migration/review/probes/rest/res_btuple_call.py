from tpy import int32
class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
from typing import Iterator
def mk(xs: list[P]) -> tuple[int32, P]:
    return (1, xs[0])
def g(xs: list[P]) -> Iterator[tuple[int32, P]]:
    yield mk(xs)
    yield mk(xs)
def main() -> None:
    pass
main()
