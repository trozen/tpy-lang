# @readonly: iterating over a param yields readonly elements --
# mutating methods on elements are rejected.
from tpy import int32, readonly

class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def set_x(self, v: int32) -> None:
        self.x = v

@readonly
def test(xs: list[Point]) -> None:
    for p in xs:
        p.set_x(int32(99))  # tpyc: error(/readonly/)
