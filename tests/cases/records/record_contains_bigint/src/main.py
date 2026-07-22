# A user __contains__ with a fixed-int param takes the standard call-arg
# narrow: an in-range BigInt needle converts and dispatches (out-of-range
# panics -- see the panic_ sibling).
from tpy import Int32


class Bag:
    xs: list[Int32]

    def __init__(self):
        self.xs = [1, 2]

    def __contains__(self, item: Int32) -> bool:
        return item in self.xs


def main():
    b = Bag()
    k: int = 2
    print(k in b)
    print(k + 1 in b)
    print(5 not in b)


main()
