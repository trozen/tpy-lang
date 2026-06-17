# A mixed owned/value tuple local (tuple[Own[A], Int32]) passed by value: the
# Own element must not be copied at the boundary; @nocopy makes a copy an error.
from tpy import nocopy, Own, Int32


@nocopy
class A:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n


def make() -> tuple[Own[A], Int32]:
    return (A(5), 7)


def consume(p: tuple[Own[A], Int32]) -> Int32:
    return p[0].n + p[1]


def main():
    t = make()
    print(consume(t))


main()
