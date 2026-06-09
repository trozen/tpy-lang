# A mixed tuple[Own[A], value] unpack: the Own element becomes a movable owned
# local (moved onward), the value element keeps ordinary value semantics. Only
# the Own-typed target is promoted to movable -- the value target is unaffected.
from tpy import Own, nocopy, Int32


@nocopy
class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def make() -> tuple[Own[Counter], Int32]:
    return (Counter(7), 99)


def consume(c: Own[Counter]) -> None:
    print(c.n)


def main() -> None:
    a, n = make()
    consume(a)
    print(n)


main()
