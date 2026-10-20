# An augmented key that is a view into a container chosen by a condition
# that runs a user `__eq__` cannot be evaluated once; binding the chosen
# value to a name first can. (A documented restriction:
# docs/LANGUAGE_FEATURES.md, Assignment.)
from typing import Self
from tpy import int32


class V:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: Self) -> bool:
        print("eq")
        return self.n == other.n


def main() -> None:
    names = [" k ", " j "]
    vs = [V(1), V(2)]
    a = V(2)
    d = {"k": 0, "j": 0}
    # `a in vs` runs `__eq__`, so the whole ternary runs once, and its value
    # is a view into `names`
    d[names[0].strip() if a in vs else names[1].strip()] += 1  # tpyc: error(/the subscript key here is a view chosen inside a conditional expression and cannot be evaluated once; bind the value it views to a name first/)
    print(d)


main()
