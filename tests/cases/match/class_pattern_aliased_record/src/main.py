# Under a same-named-record collision (aliased Foreign + local Point), each
# correct class pattern must still match its own record -- neither confused.
from shapes import Point as Foreign


class Point:
    def __init__(self, y: int) -> None:
        self.y = y


def foreign_x(p: Foreign) -> int:
    match p:
        case Foreign(x=v):
            return v
    return -1


def local_y(p: Point) -> int:
    match p:
        case Point(y=v):
            return v
    return -1


def main() -> None:
    print(foreign_x(Foreign(7)))
    print(local_y(Point(9)))


main()
