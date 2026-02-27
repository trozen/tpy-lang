# Test __str__/__repr__ dispatch: str(), repr(), print(), f-string, !r, !s
class Point:
    x: int
    y: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

    def __repr__(self) -> str:
        return f"Point(x={self.x}, y={self.y})"

def main() -> None:
    p: Point = Point(3, 7)

    # str() dispatches to __str__
    print(str(p))

    # repr() dispatches to __repr__
    print(repr(p))

    # print() uses operator<< which delegates to __str__
    print(p)

    # f-string uses __str__
    print(f"point = {p}")

    # !r uses __repr__
    print(f"debug: {p!r}")

    # !s uses __str__
    print(f"display: {p!s}")

main()
