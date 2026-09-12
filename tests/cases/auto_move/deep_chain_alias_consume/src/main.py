# A 2-level chain alias (a = o.items[0], subscript of a field) roots at
# o, so a later consume of o copies (with warning) instead of moving.
from tpy import int32, Own


class Inner:
    vals: list[int32]

    def __init__(self):
        self.vals = [7, 8]


class Outer:
    items: list[Inner]

    def __init__(self):
        self.items = [Inner()]


def take(o: Own[Outer]) -> int32:
    store: list[Outer] = []
    store.append(o)
    return len(store)


def main():
    o = Outer()
    a = o.items[0]
    print(take(o))  # tpyc: warning(/copies/)
    print(len(a.vals))


main()
