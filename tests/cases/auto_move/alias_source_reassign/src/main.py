# Alias source reassigned (copyable type) -- move of new source allowed.
from tpy import int32, Own


class Point:
    x: int32
    y: int32


def consume(p: Own[Point]) -> int32:
    return p.x + p.y


def main():
    p = Point()
    p.x = 1
    p.y = 2
    alias = p
    print(alias.x)
    p = Point()         # reassign p -- alias detached
    p.x = 10
    p.y = 20
    print(consume(p))   # tpyc: ok (auto-move, alias doesn't constrain)
    print(alias.x)


main()
