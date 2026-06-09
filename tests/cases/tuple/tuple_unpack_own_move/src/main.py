# Whole-tuple unpack of a tuple[Own[A], Own[B]] rvalue binds each target as an
# owned, movable local: the elements are moved out of the consumed temporary
# and can be moved onward (here into Own[] params). The @nocopy payload forces
# move-not-copy -- a silent copy at the unpack or the call would be a compile
# error, and the post-move mutation observes the moved-out object.
from tpy import Own, nocopy, Int32


@nocopy
class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def make_pair() -> tuple[Own[Counter], Own[Counter]]:
    return (Counter(1), Counter(2))


def consume(c: Own[Counter]) -> None:
    c.n += 10
    print(c.n)


def main() -> None:
    a, b = make_pair()  # tpyc: ok
    consume(a)
    consume(b)


main()
