# Alias suppresses auto-move -- implicit copy error for copyable type.
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def main():
    p = Point()
    p.x = 42
    alias = p
    consume(p)         # tpyc: error(/implicit copy/)
    print(alias.x)


main()
