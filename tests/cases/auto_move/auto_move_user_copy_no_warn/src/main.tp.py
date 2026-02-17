# User-defined function named 'copy' should NOT trigger unnecessary copy warning,
# even when tpy.copy is also imported (user def shadows the import)
from tpy import Int32, Own, copy


class Box:
    value: Int32


def copy(b: Own[Box]) -> Own[Box]:
    return b


def consume(b: Own[Box]) -> Int32:
    return b.value


def main():
    b = Box()
    b.value = 42
    print(consume(copy(b)))  # tpyc: ok


main()
