# Tuple printing: direct print, single-element, nested tuple, record with tuple field.
from tpy import Int32

class Pair:
    data: tuple[Int32, str]
    def __init__(self, a: Int32, b: str) -> None:
        self.data = (a, b)
    def __str__(self) -> str:
        return "Pair((" + str(self.data[0]) + ", '" + self.data[1] + "'))"

def main() -> None:
    t: tuple[Int32, Int32] = (Int32(10), Int32(20))
    print(t)
    # Single-element tuple (trailing comma)
    s: tuple[Int32] = (Int32(42),)
    print(s)
    # Record with tuple field
    p = Pair(Int32(1), "hello")
    print(p)
    # Nested tuple
    n: tuple[tuple[Int32, Int32], str] = ((Int32(3), Int32(4)), "xy")
    print(n)

main()
