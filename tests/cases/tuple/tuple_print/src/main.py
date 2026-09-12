# Tuple printing: direct print, single-element, nested tuple, record with tuple field.
from tpy import int32

class Pair:
    data: tuple[int32, str]
    def __init__(self, a: int32, b: str) -> None:
        self.data = (a, b)
    def __str__(self) -> str:
        return "Pair((" + str(self.data[0]) + ", '" + self.data[1] + "'))"

def main() -> None:
    t: tuple[int32, int32] = (int32(10), int32(20))
    print(t)
    # Single-element tuple (trailing comma)
    s: tuple[int32] = (int32(42),)
    print(s)
    # Record with tuple field
    p = Pair(int32(1), "hello")
    print(p)
    # Nested tuple
    n: tuple[tuple[int32, int32], str] = ((int32(3), int32(4)), "xy")
    print(n)

main()
