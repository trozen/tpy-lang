# Three-element all-Own tuple unpack: every target is moved out of the
# consumed temporary and moved onward.
from tpy import Own, nocopy, Int32


@nocopy
class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def make_triple() -> tuple[Own[Counter], Own[Counter], Own[Counter]]:
    return (Counter(1), Counter(2), Counter(3))


def consume(c: Own[Counter]) -> None:
    print(c.n)


def main() -> None:
    a, b, c = make_triple()
    consume(a)
    consume(b)
    consume(c)


main()
