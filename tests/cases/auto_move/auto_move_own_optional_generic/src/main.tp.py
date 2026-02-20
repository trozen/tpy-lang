# Own[T] | None generic function param: auto-move at call site.
from tpy import Int32, Own


class Box:
    value: Int32


def take_optional[T](item: Own[T] | None, fallback: Int32) -> Int32:
    return fallback


def main():
    b = Box()
    b.value = Int32(42)
    print(take_optional[Box](b, Int32(99)))


main()
