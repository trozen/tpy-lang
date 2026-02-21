# Aliased tpy.copy should still trigger the unnecessary copy warning
from tpy import Int32, Own
from tpy import copy as c


class Box:
    value: Int32


def consume(b: Own[Box]) -> Int32:
    return b.value


def main():
    b = Box()
    b.value = 42
    print(consume(c(b)))  # tpyc: warning(/unnecessary copy/)


main()
