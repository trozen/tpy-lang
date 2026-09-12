# A mixed owned/value tuple local (tuple[Own[A], int32]) passed by value: the
# Own element must not be copied at the boundary; @nocopy makes a copy an error.
from tpy import nocopy, Own, int32


@nocopy
class A:
    n: int32

    def __init__(self, n: int32):
        self.n = n


def make() -> tuple[Own[A], int32]:
    return (A(5), 7)


def consume(p: tuple[Own[A], int32]) -> int32:
    return p[0].n + p[1]


def main():
    t = make()
    print(consume(t))


main()
