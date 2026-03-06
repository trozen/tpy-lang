# List comprehension: basic expression transformation
from tpy import Int32, Own

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    # Transform with range
    squares: list[Int32] = [x * x for x in range(5)]
    print(squares)

    # Identity comprehension from list
    items: list[Int32] = [10, 20, 30]
    copy = [x for x in items]
    print(copy)

    # Two-arg range
    shifted = [x for x in range(3, 7)]
    print(shifted)

    # Record field access
    points: list[Point] = [Point(1, 2), Point(3, 4), Point(5, 6)]
    xs = [p.x for p in points]
    print(xs)

    # Comprehension as function argument
    print([x + 1 for x in range(3)])

    # String comprehension
    words: list[str] = ["hello", "world"]
    upper = [str(len(w)) for w in words]
    print(upper)

    # Comprehension as return value
    print(make_list(4))

    # Three-arg range (fallback to Range begin/end)
    stepped = [x for x in range(0, 10, 3)]
    print(stepped)

def make_list(n: Int32) -> Own[list[Int32]]:
    return [x * 10 for x in range(n)]

main()
