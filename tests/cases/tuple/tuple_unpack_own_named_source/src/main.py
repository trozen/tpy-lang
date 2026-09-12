# Whole-tuple unpack of a NAMED owned-tuple local at its last use moves the
# source into the temp and moves each element out, rather than copying the
# whole tuple. @nocopy forces the move: a silent copy would be a C++ build
# error, so a passing run proves the source was moved, not copied.
from tpy import Own, nocopy, int32


@nocopy
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_pair() -> tuple[Own[Counter], Own[Counter]]:
    return (Counter(1), Counter(2))


def consume(c: Own[Counter]) -> None:
    c.n += 10
    print(c.n)


def main() -> None:
    t = make_pair()
    a, b = t  # tpyc: ok
    consume(a)
    consume(b)


main()
