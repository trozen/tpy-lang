# A subscript alias (n = xs[0]) references xs's element storage: a later
# consume of xs copies (with warning) instead of moving, so n stays valid.
from tpy import Int32, Own


class P:
    vals: list[Int32]

    def __init__(self):
        self.vals = [5]


def drop(xs: Own[list[P]]) -> Int32:
    store: list[list[P]] = []
    store.append(xs)
    return len(store)


def main():
    xs = [P()]
    n = xs[0]
    print(drop(xs))  # tpyc: warning(/copies/)
    print(n.vals[0])


main()
