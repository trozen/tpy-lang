# Generic function with tuple type parameter
from tpy import Int32

def first_of_pair[T](p: tuple[T, T]) -> T:
    return p[0]

def swap[A, B](p: tuple[A, B]) -> tuple[B, A]:
    return (p[1], p[0])

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(" + str(self.x) + ", " + str(self.y) + ")"

def main() -> None:
    nums = (Int32(10), Int32(20))
    x = first_of_pair(nums)
    print(x)

    pair = (Int32(5), "five")
    swapped = swap(pair)
    print(swapped)

    # Generic swap with record type (non-value) -- verify reference semantics
    pt = Point(Int32(1), Int32(2))
    pt_pair = (Int32(42), pt)
    swapped2 = swap(pt_pair)
    print(swapped2[0].x)
    print(swapped2[1])
    pt.x = Int32(99)
    print(swapped2[0].x)

main()
