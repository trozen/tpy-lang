# Alias created AFTER source reassignment (copyable type) -- alias tracks
# the current value. Auto-move suppressed, implicit copy error fires.
from tpy import Int32, Own


class Point:
    x: Int32
    y: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x + p.y


def main():
    p = Point()
    p.x = 0
    p.y = 0
    p = Point()
    p.x = 1
    p.y = 2
    alias = p
    print(consume(p))   # tpyc: error(/implicit copy/)
    print(alias.x)


main()
