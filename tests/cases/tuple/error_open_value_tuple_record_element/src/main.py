# `tuple[T, Record]` is not an OPEN VALUE tuple: the concrete element is a
# reference type, so the borrow/storage duality matters and the return rejects.
from tpy import Int32, Own


class Box:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def smaller[T](a: tuple[T, Box], b: tuple[T, Box]) -> Own[tuple[T, Box]]:
    return min(a, b, key=lambda p: p[1].n)  # tpyc: error(/call.ret_type.tuple/)


def main() -> None:
    print(smaller(("a", Box(3)), ("b", Box(1)))[1].n)


main()
