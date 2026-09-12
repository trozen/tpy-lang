from tpy import int32
from typing import Protocol


# Protocol with a single field
class HasValue(Protocol):
    value: int32


# Protocol with multiple fields
class HasXY(Protocol):
    x: int32
    y: int32


# Protocol with fields and methods combined
class Container(Protocol):
    count: int32

    def is_empty(self) -> bool:
        ...


# Generic protocol with field using type parameter
class Holder[T](Protocol):
    item: T


# Record conforming to HasValue
class Point:
    value: int32

    def __init__(self, v: int32):
        self.value = v


# Record conforming to HasXY
class Vec2:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


# Record conforming to Container
class Box:
    count: int32

    def __init__(self, c: int32):
        self.count = c

    def is_empty(self) -> bool:
        return self.count == 0


# Record conforming to Holder[int32]
class IntHolder:
    item: int32

    def __init__(self, v: int32):
        self.item = v


# Generic class with protocol field bound
class Wrapper[T: HasValue]:
    inner: T

    def __init__(self, val: T):
        self.inner = val

    def get_inner_value(self) -> int32:
        return self.inner.value


# Function using protocol field
def get_value[T: HasValue](item: T) -> int32:
    return item.value


# Function using protocol with multiple fields
def sum_xy[T: HasXY](item: T) -> int32:
    return item.x + item.y


# Function using protocol with field and method
def describe[T: Container](item: T) -> int32:
    if item.is_empty():
        return 0
    return item.count


# Function using generic protocol with field
def get_item[T: Holder[int32]](holder: T) -> int32:
    return holder.item


def main() -> None:
    # Test generic function with protocol field
    p = Point(42)
    print(get_value(p))

    # Test protocol with multiple fields
    v = Vec2(10, 20)
    print(sum_xy(v))

    # Test protocol with field + method
    b1 = Box(5)
    b2 = Box(0)
    print(describe(b1))
    print(describe(b2))

    # Test generic class with protocol field bound
    w = Wrapper(Point(100))
    print(w.get_inner_value())

    # Test generic protocol with type parameter in field
    ih = IntHolder(77)
    print(get_item(ih))


main()
