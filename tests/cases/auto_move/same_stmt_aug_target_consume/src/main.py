# Aug-assign sibling of the same-statement split: the subscript-target
# index reads b.items while the value consumes b in one C++
# full-expression -- the consume must not auto-move b.
from tpy import Int32, Own


class Blob:
    items: list[Int32]

    def __init__(self):
        self.items = [10, 20, 30]


class K:
    stored: list[Blob]

    def __init__(self):
        self.stored = []

    def take(self, b: Own[Blob]) -> Int32:
        self.stored.append(b)
        return 99


def main():
    k = K()
    b = Blob()
    d: dict[Int32, Int32] = {3: 1}
    d[len(b.items)] += k.take(b)  # tpyc: warning(/copies/)
    print(d)


main()
