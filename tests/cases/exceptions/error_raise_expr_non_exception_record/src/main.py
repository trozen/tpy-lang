# Error: raise <expr> with user record that doesn't inherit from Exception
from tpy import int32

class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def test() -> None:
    p = Point(1)
    raise p  # tpyc: error(/must inherit from Exception/)
