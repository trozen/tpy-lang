# Generic function with tuple type parameter
from tpy import int32

def first_of_pair[T](p: tuple[T, T]) -> T:
    return p[0]

def swap[A, B](p: tuple[A, B]) -> tuple[B, A]:
    return (p[1], p[0])

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(" + str(self.x) + ", " + str(self.y) + ")"

def main() -> None:
    nums = (int32(10), int32(20))
    x = first_of_pair(nums)
    print(x)

    pair = (int32(5), "five")
    swapped = swap(pair)
    print(swapped)

    # Generic swap with record type (non-value) -- verify reference semantics
    pt = Point(int32(1), int32(2))
    pt_pair = (int32(42), pt)
    swapped2 = swap(pt_pair)
    print(swapped2[0].x)
    print(swapped2[1])
    pt.x = int32(99)
    print(swapped2[0].x)

main()
