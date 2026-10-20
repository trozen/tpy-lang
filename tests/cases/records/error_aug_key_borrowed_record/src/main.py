# An augmented key from a method body returning a record it stores is a
# reference into that storage, not a new value, so it cannot be held without
# a copy; binding it to a name first holds it.
from typing import Self


class K:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __hash__(self) -> int:
        return self.n

    def __eq__(self, o: Self) -> bool:
        return self.n == o.n


class Holder:
    k: K

    def __init__(self) -> None:
        self.k = K(1)

    def get(self) -> K:
        return self.k


def main() -> None:
    h = Holder()
    rd = {K(1): 0}
    # `h.get()` runs its body once and hands back `h.k` in place
    rd[h.get()] += 1  # tpyc: error(/the subscript key here is evaluated once but is not known to be a new value, so it cannot be held without a copy; bind it to a name first/)
    print(len(rd))


main()
