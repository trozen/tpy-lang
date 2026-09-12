# Alias created AFTER source reassignment (copyable type) -- alias tracks
# the current value. Auto-move suppressed, implicit copy error fires.
from tpy import int32, Own


class Point:
    x: int32
    y: int32


def consume(p: Own[Point]) -> int32:
    return p.x + p.y


def main():
    p = Point()
    p.x = 0
    p.y = 0
    p = Point()
    p.x = 1
    p.y = 2
    alias = p
    print(consume(p))   # tpyc: warning(/copies.*into owned storage/)
    print(alias.x)


main()
