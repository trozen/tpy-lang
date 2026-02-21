from tpy import Int32, Own

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

# Own[T] | None returns std::optional<T> by value — no aliasing concern
def maybe_make(x: Int32) -> Own[Point] | None:
    if x > 0:
        return Point(x, x)
    return None

# Global-scope field assignment from Own[T] | None
h = Holder()
h.value = maybe_make(5)   # tpyc: ok
print(h.value is None)
print(h.value.x)
print(h.value.y)

h.value = maybe_make(-1)  # tpyc: ok
print(h.value is None)

# Function-scope field assignment from Own[T] | None
def test() -> None:
    h2 = Holder()

    h2.value = maybe_make(3)   # tpyc: ok
    print(h2.value is None)
    print(h2.value.x)

    h2.value = maybe_make(-1)  # tpyc: ok
    print(h2.value is None)

test()
