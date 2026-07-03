# The same wrong-record class pattern must also be rejected against a
# UNION subject (the base-name fuzzy fallback must not bind a distinct
# same-named record; parameterized-member fuzzy matching is unaffected).
from shapes import Point as Foreign


class Point:
    def __init__(self, y: int) -> None:
        self.y = y


class Other:
    def __init__(self, z: int) -> None:
        self.z = z


def describe(p: Foreign | Other) -> int:
    match p:
        case Point(y=v):  # tpyc: error(/does not match union member/)
            return v
        case Other(z=w):
            return w
    return -1


def main() -> None:
    print(describe(Foreign(7)))


main()
