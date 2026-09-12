# Own[T] | None param should be auto-moved at last use, same as Own[T].
from tpy import int32, Own


class Point:
    x: int32
    y: int32


def consume_own(p: Own[Point]) -> int32:
    return p.x + p.y


def forward_optional(p: Own[Point] | None) -> int32:
    if p is None:
        return int32(-1)
    # p is at its last use; should be auto-moved (no copy warning)
    return consume_own(p)


def main():
    p = Point()
    p.x = 5
    p.y = 7
    print(forward_optional(p))


main()
