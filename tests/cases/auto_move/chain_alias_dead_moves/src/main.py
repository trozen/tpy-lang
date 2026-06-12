# Dead-alias precision guard: a field alias whose last use precedes the
# consume must NOT suppress auto-move (no copy, no warning).
from tpy import Int32, Own


class Inner:
    vals: list[Int32]

    def __init__(self):
        self.vals = [7, 8]


class Outer:
    inner: Inner

    def __init__(self):
        self.inner = Inner()


def take(o: Own[Outer]) -> Int32:
    store: list[Outer] = []
    store.append(o)
    return len(store)


def main():
    o = Outer()
    a = o.inner
    print(len(a.vals))
    print(take(o))  # tpyc: ok


main()
