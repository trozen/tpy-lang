# A field-access alias (a = o.inner) references o's storage: a consume of
# o while a is live must copy (with warning), not move -- a move would let
# a observe the moved-from field.
from tpy import Int32, Own


class Inner:
    vals: list[Int32]

    def __init__(self):
        self.vals = [7, 8]


class Outer:
    inner: Inner

    def __init__(self):
        self.inner = Inner()


class Keeper:
    stored: list[Outer]

    def __init__(self):
        self.stored = []

    def take(self, o: Own[Outer]):
        self.stored.append(o)


def main():
    h = Keeper()
    o = Outer()
    a = o.inner
    h.take(o)  # tpyc: warning(/copies/)
    print(len(a.vals))


main()
