from tpy import int32
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __eq__(self, other: Point) -> bool:
        return self.x == other.x
class Item:
    pts: list[Point]
    def __init__(self) -> None:
        self.pts = []
def f(item: Item, p: Point) -> None:
    print(p in item.pts)
def main() -> None:
    f(Item(), Point(1))
main()
