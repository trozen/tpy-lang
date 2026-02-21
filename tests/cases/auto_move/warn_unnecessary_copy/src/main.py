# Warn when copy() is used but the variable is at its last use
from tpy import Int32, Own, copy


class Box:
    value: Int32


def consume(b: Own[Box]) -> Int32:
    return b.value


def make_box(v: Int32) -> Own[Box]:
    b = Box()
    b.value = v
    return copy(b)  # tpyc: warning(/unnecessary copy/)


def main():
    b = Box()
    b.value = 42
    # Warning at call arg site too
    print(consume(copy(b)))  # tpyc: warning(/unnecessary copy/)
    print(make_box(99).value)


main()
