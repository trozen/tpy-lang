# copy(x) where x is NOT at last use -- no warning (copy is needed)
from tpy import Int32, Own, copy


class Box:
    value: Int32


def consume(b: Own[Box]) -> Int32:
    return b.value


def main():
    b = Box()
    b.value = 42
    print(consume(copy(b)))  # tpyc: ok (not last use -- b used below)
    print(b.value)


main()
