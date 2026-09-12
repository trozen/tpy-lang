# A forward-referenced borrowing callee: facts unknown at the bind, so the
# consume conservatively copies (with warning) and n stays valid.
from tpy import int32, Own


class P:
    vals: list[int32]

    def __init__(self):
        self.vals = [5]


def main():
    xs = [P()]
    n = first(xs)
    print(drop(xs))  # tpyc: warning(/copies/)
    print(n.vals[0])


def first(xs: list[P]) -> P:
    return xs[0]


def drop(xs: Own[list[P]]) -> int32:
    store: list[list[P]] = []
    store.append(xs)
    return len(store)


main()
