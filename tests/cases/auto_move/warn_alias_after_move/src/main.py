# Alias suppresses auto-move -- implicit copy error for copyable type.
from tpy import int32, Own


class Point:
    x: int32


def consume(p: Own[Point]) -> int32:
    return p.x


def main():
    p = Point()
    p.x = 42
    alias = p
    consume(p)         # tpyc: warning(/copies.*into owned storage/)
    print(alias.x)


main()
