from tpy import Int32, copy


class Point:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


class Holder:
    value: Point | None

    def __init__(self) -> None:
        self.value = None


def read(h: Holder) -> Int32:
    if h.value is not None:
        return h.value.x + 1  # tpyc: ok
    return 0


h1 = Holder()
h1.value = copy(Point(10))
h2 = Holder()
print(read(h1))
print(read(h2))
