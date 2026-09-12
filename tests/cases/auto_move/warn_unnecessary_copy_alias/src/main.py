# Aliased tpy.copy should still trigger the unnecessary copy warning
from tpy import int32, Own
from tpy import copy as c


class Box:
    value: int32


def consume(b: Own[Box]) -> int32:
    return b.value


def main():
    b = Box()
    b.value = 42
    print(consume(c(b)))  # tpyc: warning(/unnecessary copy/)


main()
