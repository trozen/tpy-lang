# List comprehension: basic expression transformation
from tpy import int32, Own

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    # Transform with range
    squares: list[int32] = [x * x for x in range(5)]
    print(squares)

    # Identity comprehension from list
    items: list[int32] = [10, 20, 30]
    copy = [x for x in items]
    print(copy)

    # Two-arg range (non-literal stop to stay on list path)
    stop: int32 = 7
    shifted = [x for x in range(3, stop)]
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

    # Three-arg range (non-literal step to stay on list path)
    step: int32 = 3
    stepped = [x for x in range(0, 10, step)]
    print(stepped)

def make_list(n: int32) -> Own[list[int32]]:
    return [x * 10 for x in range(n)]

main()
