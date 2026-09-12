# Field-target sibling of the same-statement split: the target object b
# is written after the value consumes b in one statement -- the consume
# must not auto-move b out from under its own field store.
from tpy import int32, Own


class Blob:
    items: list[int32]
    n: int32

    def __init__(self):
        self.items = [10, 20, 30]
        self.n = 0


class K:
    stored: list[Blob]

    def __init__(self):
        self.stored = []

    def take(self, b: Own[Blob]) -> int32:
        self.stored.append(b)
        return 99


def main():
    k = K()
    b = Blob()
    b.n = k.take(b)  # tpyc: warning(/copies/)
    print(b.n)
    print(len(b.items))


main()
