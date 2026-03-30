# filter over non-value types preserves references into the container
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def positive_x(p: Point) -> bool:
    return p.x > 0

def main() -> None:
    pts = [Point(-1, 0), Point(2, 3), Point(-5, 1), Point(4, 5)]

    # filter preserves references: mutate through loop variable
    for p in filter(positive_x, pts):
        p.y = p.y + 100

    # verify original list was mutated
    for p in pts:
        print(p.x, p.y)

main()
