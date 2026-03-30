# Error: raise <expr> with user record that doesn't inherit from Exception
from tpy import Int32

class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x

def test() -> None:
    p = Point(1)
    raise p  # tpyc: error(/must inherit from Exception/)
