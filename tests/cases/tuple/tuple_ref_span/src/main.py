# Mutable Span element in tuple return -- should capture as T& (not const).
from tpy import int32, Span, Array

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def get_first(s: Span[Point]) -> tuple[Point, int32]:
    return (s[0], int32(1))

def main() -> None:
    arr: Array[Point, 3] = [Point(int32(1), int32(2)),
                            Point(int32(3), int32(4)),
                            Point(int32(5), int32(6))]
    t = get_first(arr)
    print(t[0].x, t[0].y, t[1])
    # Mutation through returned reference
    t[0].x = int32(99)
    print(arr[0].x)

main()
