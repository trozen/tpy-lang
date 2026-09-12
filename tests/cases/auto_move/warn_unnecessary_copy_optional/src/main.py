# Unnecessary copy() warning should fire for Own[T] | None params too.
from tpy import int32, Own, copy


class Box:
    value: int32


def consume_own(b: Own[Box]) -> int32:
    return b.value


def consume_optional(b: Own[Box] | None) -> int32:
    if b is None:
        return int32(-1)
    return consume_own(b)


def main():
    b = Box()
    b.value = 42
    # Warning: copy is unnecessary because b is at its last use
    print(consume_optional(copy(b)))  # tpyc: warning(/unnecessary copy/)


main()
