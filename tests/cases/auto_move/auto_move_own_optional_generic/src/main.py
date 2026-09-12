# Own[T] | None generic function param: auto-move at call site.
from tpy import int32, Own


class Box:
    value: int32


def take_optional[T](item: Own[T] | None, fallback: int32) -> int32:
    return fallback


def main():
    b = Box()
    b.value = int32(42)
    print(take_optional[Box](b, int32(99)))


main()
