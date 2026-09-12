import math
from typing import Iterator
from tpy import int32
class Nums:
    xs: list[float]
    def __init__(self) -> None:
        self.xs = [1.0, 2.0]
    def __iter__(self) -> Iterator[float]:
        for x in self.xs:
            yield x
def f(n: int32) -> float:
    match n:
        case 1 if math.fsum(Nums()) > 0.0:
            return 1.0
        case _:
            return 0.0
def main() -> None:
    pass
main()
