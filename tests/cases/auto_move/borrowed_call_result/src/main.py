# A local bound from a borrowing call (first's return borrows xs[0]) must
# suppress auto-move of xs at a later consume -- moving would dangle n
# (SIGSEGV before the fix). The consume copies instead, with the warning.
from tpy import Int32, Own


class P:
    vals: list[Int32]

    def __init__(self):
        self.vals = [5]


def first(xs: list[P]) -> P:
    return xs[0]


def drop(xs: Own[list[P]]) -> Int32:
    store: list[list[P]] = []
    store.append(xs)
    return len(store)


def main():
    xs = [P()]
    n = first(xs)
    print(drop(xs))  # tpyc: warning(/copies/)
    print(n.vals[0])


main()
