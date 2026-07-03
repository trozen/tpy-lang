# A class pattern for a LOCAL record in a union must still match under a
# same-named-record collision (the foreign alias is in scope but distinct).
from shapes import Point as Foreign


class Point:
    def __init__(self, y: int) -> None:
        self.y = y


class Other:
    def __init__(self, z: int) -> None:
        self.z = z


def pick(p: Point | Other) -> int:
    match p:
        case Point(y=v):
            return v
        case Other(z=w):
            return w
    return -1


def use_foreign(p: Foreign) -> int:
    return p.x


def main() -> None:
    print(pick(Point(9)))
    print(pick(Other(4)))
    print(use_foreign(Foreign(7)))


main()
