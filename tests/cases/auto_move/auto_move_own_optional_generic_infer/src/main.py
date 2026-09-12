# Own[T] | None generic param: T inferred from arg type (no explicit type args).
from tpy import int32, Own


class Box:
    value: int32


def take_optional[T](item: Own[T] | None, fallback: int32) -> int32:
    return fallback


def main():
    b = Box()
    b.value = int32(42)
    # T inferred as Box from the bare Box arg coerced to Own[Box] | None
    print(take_optional(b, int32(99)))
    # None arg: T must be explicit since it can't be inferred from None
    print(take_optional[Box](None, int32(77)))


main()
