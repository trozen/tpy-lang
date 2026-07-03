# A class pattern whose record merely shares the subject's canonical name
# (a same-named local record vs an aliased import) must be rejected.
from shapes import Point as Foreign


class Point:
    def __init__(self, y: int) -> None:
        self.y = y


def describe(p: Foreign) -> int:
    match p:
        case Point(y=v):  # tpyc: error(/does not match subject type/)
            return v
    return -1


def main() -> None:
    print(describe(Foreign(7)))


main()
