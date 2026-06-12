# An unknown-shape borrow from a forward-referenced callee gates moves
# only: it must not mint mutation-invalidation warnings on the source.
from tpy import Int32


class P:
    vals: list[Int32]

    def __init__(self):
        self.vals = [7]


def main():
    xs = [P()]
    n = pick(xs)
    print(len(n.vals))
    xs.append(P())  # tpyc: ok
    print(len(xs))


def pick(xs: list[P]) -> P:
    return xs[0]


main()
