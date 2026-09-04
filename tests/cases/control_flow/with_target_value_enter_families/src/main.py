# `with` as-targets whose `__enter__` returns a self-contained VALUE outside the
# scalar/str families: a tuple, bytes, and a value union each copy out of the
# manager, so reads ride the copied value rather than borrowing the manager.
from tpy import Int32, StrView


class Pair:
    a: Int32
    b: Int32

    def __init__(self, a: Int32, b: Int32) -> None:
        self.a = a
        self.b = b

    def __enter__(self) -> tuple[Int32, Int32]:
        return (self.a, self.b)

    def __exit__(self, et, ev, tb) -> None:
        print("pair exit")


class Blob:
    def __enter__(self) -> bytes:
        return b"abc"

    def __exit__(self, et, ev, tb) -> None:
        print("blob exit")


class Either:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __enter__(self) -> Int32 | StrView:
        if self.n > 0:
            return self.n
        return "zero"

    def __exit__(self, et, ev, tb) -> None:
        print("either exit")


def sum_pair(t: tuple[Int32, Int32]) -> Int32:
    return t[0] + t[1]


def first_byte(b: bytes) -> Int32:
    return b[0]


def main() -> None:
    # A tuple enter target: the copied tuple reads by index and passes on.
    with Pair(3, 4) as t:  # tpyc: ok
        print(t[0], t[1])
        print(sum_pair(t))
    # A bytes enter target.
    with Blob() as b:  # tpyc: ok
        print(len(b))
        print(first_byte(b))
    # A value-union enter target, one case per alternative.
    with Either(5) as u:  # tpyc: ok
        print(u)
    with Either(0) as u2:  # tpyc: ok
        print(u2)


main()
