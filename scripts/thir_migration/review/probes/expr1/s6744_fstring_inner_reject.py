from tpy import Int32
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
    def __eq__(self, other: Point) -> bool:
        return self.x == other.x
class Item:
    pts: list[Point]
    def __init__(self) -> None:
        self.pts = []
def f(item: Item, p: Point) -> str:
    return f'{p in item.pts}'
def main() -> None:
    print(f(Item(), Point(1)))
main()
