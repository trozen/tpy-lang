# An in-place dunder's parameter is force-const; an unmutated, non-escaping one
# keeps that force, so a local bound to it is a const borrow. @nocopy makes a
# silent copy at that binding a compile error.
from tpy import Int32, nocopy


@nocopy
class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Acc:
    total: Int32

    def __init__(self, total: Int32) -> None:
        self.total = total

    def __iadd__(self, other: Counter) -> "Acc":
        c = other  # const borrow of the force-const dunder parameter
        self.total += c.n
        return self


def main() -> None:
    a = Acc(1)
    a += Counter(4)
    print(a.total)


main()
