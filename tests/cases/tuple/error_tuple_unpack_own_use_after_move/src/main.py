# An owned unpack target obeys move tracking like any owned local: using it
# after it has been moved onward is rejected.
from tpy import Own, nocopy, Int32


@nocopy
class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def make_pair() -> tuple[Own[Counter], Own[Counter]]:
    return (Counter(1), Counter(2))


def consume(c: Own[Counter]) -> None:
    print(c.n)


def main() -> None:
    a, b = make_pair()
    consume(a)  # tpyc: error(/used after this point/)
    consume(a)


main()
