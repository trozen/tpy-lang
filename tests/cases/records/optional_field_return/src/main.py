from tpy import Int32, copy


class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


class Box:
    item: Point | None

    def __init__(self) -> None:
        self.item = None

    def get_item(self) -> Point | None:
        return self.item

    def has_item(self) -> bool:
        return self.item is not None


b = Box()
r = b.get_item()
print(r is None)

b.item = copy(Point(3, 4))
r = b.get_item()
print(r is not None)
print(r.x)
print(r.y)
