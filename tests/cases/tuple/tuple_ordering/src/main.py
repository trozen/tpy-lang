# Tuple lexicographic ordering: <, <=, >, >=, including @nocopy elements
from __future__ import annotations
from tpy import Int32, nocopy

def main() -> None:
    a = (1, 2, 3)
    b = (1, 2, 4)
    c = (1, 2, 3)

    print(a < b)
    print(a > b)
    print(a <= c)
    print(a >= c)
    print(b > a)
    print(b <= a)

    # String tuples
    x = ("apple", "banana")
    y = ("apple", "cherry")
    print(x < y)
    print(x >= y)

    # Mixed comparison in conditional
    if (1, 0) < (1, 1):
        print("less")

    # @nocopy element -- ordering must not copy
    test_nocopy_ordering()

@nocopy
class Rank:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v

    def __eq__(self, other: Rank) -> bool:
        return self.val == other.val

    def __lt__(self, other: Rank) -> bool:
        return self.val < other.val

    def __le__(self, other: Rank) -> bool:
        return self.val <= other.val

    def __gt__(self, other: Rank) -> bool:
        return self.val > other.val

    def __ge__(self, other: Rank) -> bool:
        return self.val >= other.val

def test_nocopy_ordering() -> None:
    r1 = Rank(1)
    r2 = Rank(2)
    r3 = Rank(3)
    print((r1, r2) < (r1, r3))
    print((r1, r2) >= (r1, r3))
    print((r1, r2, r3) >= (r1, r2, r3))
    print((r1, r2, r3) == (r1, r2, r3))

main()
