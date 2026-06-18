# A named owned-tuple local passed by value into a tuple[Own[A], Own[A]] param
# moves into the owned (std::tuple<...>&&) param; @nocopy makes a copy an error.
from tpy import nocopy, Own, Int32


@nocopy
class A:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n


def make_pair() -> tuple[Own[A], Own[A]]:
    return (A(1), A(2))


def consume(p: tuple[Own[A], Own[A]]) -> Int32:
    return p[0].n + p[1].n


def main():
    t = make_pair()
    print(consume(t))


main()
