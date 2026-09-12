# Unpacking an owned-element tuple param consumes it (moves the elements out);
# using the param after that is a use-after-move, rejected with a clean
# diagnostic like the scalar Own[T] check.
from tpy import Own, nocopy, int32


@nocopy
class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bad(p: tuple[Own[A], Own[A]]) -> int32:
    a, b = p  # tpyc: error(/used after this point and cannot be unpacked by move/)
    return a.n + p[1].n


def main() -> None:
    print(bad((A(1), A(2))))


main()
