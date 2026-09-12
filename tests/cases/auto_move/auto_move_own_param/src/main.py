# Auto-move for Own[T] parameter forwarding: Own param passed to
# another Own param is auto-moved at last use.
from tpy import int32, Own


class Point:
    x: int32
    y: int32


def consume(p: Own[Point]) -> int32:
    return p.x + p.y


def forward(p: Own[Point]) -> int32:
    # p is an Own param; forwarding to another Own param at last use
    return consume(p)


def main():
    p = Point()
    p.x = 5
    p.y = 7
    print(forward(p))


main()
