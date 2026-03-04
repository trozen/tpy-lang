# Mutable Span element in tuple return -- should capture as T& (not const).
from tpy import Int32, Span, Array

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def get_first(s: Span[Point]) -> tuple[Point, Int32]:
    return (s[0], Int32(1))

def main() -> None:
    arr: Array[Point, 3] = [Point(Int32(1), Int32(2)),
                            Point(Int32(3), Int32(4)),
                            Point(Int32(5), Int32(6))]
    t = get_first(arr)
    print(t[0].x, t[0].y, t[1])
    # Mutation through returned reference
    t[0].x = Int32(99)
    print(arr[0].x)

main()
