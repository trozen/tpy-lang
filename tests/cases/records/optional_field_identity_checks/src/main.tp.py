from tpy import Int32, copy


class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


class Holder:
    value: Point | None

    def __init__(self) -> None:
        self.value = None


h = Holder()

# None field: all four combinations
print(h.value is None)
print(h.value is not None)
print(None is h.value)
print(None is not h.value)

h.value = copy(Point(1, 2))

# Non-None field: all four combinations
print(h.value is None)
print(h.value is not None)
print(None is h.value)
print(None is not h.value)
