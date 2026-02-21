"""Test that 'not' operator rejects types without __bool__ or __len__."""

class Point:
    x: int
    y: int
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p = Point(1, 2)
    result = not p  # tpyc: error(/Invalid operand type for 'not': Point/)
