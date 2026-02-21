# Test that bool() on a type without __bool__ produces an error

class Point:
    x: int
    y: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p = Point(1, 2)
    b = bool(p)  # tpyc: error(/cannot convert/)

main()
