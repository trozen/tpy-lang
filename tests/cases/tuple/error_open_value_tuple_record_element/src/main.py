# `tuple[T, Record]` is not an OPEN VALUE tuple: the concrete element is a
# reference type copied at every instantiation, so the call is refused.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def smaller[T](a: tuple[T, Box], b: tuple[T, Box]) -> Own[tuple[T, Box]]:
    return min(a, b, key=lambda p: p[1].n)  # tpyc: error(/holds a reference type/)


def main() -> None:
    print(smaller(("a", Box(3)), ("b", Box(1)))[1].n)


main()
