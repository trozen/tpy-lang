# Optional record subject: `case None:` stays reachable after `case Point():`
# (value side only); mutation through the narrowed subject aliases the caller.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def describe(p: Point | None) -> int32:
    match p:
        case Point():
            p.x = p.x + 1
            return p.x
        case None:
            return -1


def main() -> None:
    p = Point(3)
    print(describe(p))
    print(p.x)
    print(describe(None))


main()
