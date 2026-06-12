# Value and subscript-target reads of one assignment form a single C++
# full-expression with unspecified evaluation order: the consume in the
# value must not auto-move b while the key still reads b.items.
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
    d: dict[Int32, Int32] = {}
    d[len(b.items)] = k.take(b)  # tpyc: warning(/copies/)
    print(d)


main()
