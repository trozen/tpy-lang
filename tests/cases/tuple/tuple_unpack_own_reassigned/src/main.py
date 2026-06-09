# A later-reassigned unpack target is NOT promoted to a movable owned local
# (it stays an ordinary reassignable local); unpack, reassign, and read work.
from tpy import Own, nocopy, Int32


@nocopy
class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def make_pair() -> tuple[Own[Counter], Own[Counter]]:
    return (Counter(1), Counter(2))


def make_one() -> Own[Counter]:
    return Counter(9)


def main() -> None:
    a, b = make_pair()
    a = make_one()
    print(a.n)
    print(b.n)


main()
