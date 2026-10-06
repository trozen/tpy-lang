# A tuple local assigned more than once refers to its class elements, so an
# element that is a value of its own -- the copy a `d.get(..)` member makes
# in place -- has nothing to refer to: a located refusal, not ill-formed C++.
from tpy import int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def reseat(d: dict[str, P], fb: P) -> int32:
    # the first binding of a local the next line rebinds is the refused one
    t = (d.get("a", fb), 1)  # tpyc: error(/tuple variable 't' is assigned more than once.*use a separate tuple variable for this assignment/)
    print(t[0].n)
    t = (d.get("zz", fb), 0)
    return t[0].n


def main() -> None:
    print(reseat({"a": P(5)}, P(1)))


main()
