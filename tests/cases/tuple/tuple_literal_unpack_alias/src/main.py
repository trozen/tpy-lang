# A tuple-LITERAL unpack of reference elements must ALIAS its sources, not
# copy them -- mutating through a target is observed at the source (CPython
# parity). Covers lvalue subscript elements, a mixed lvalue+rvalue unpack,
# and a `_` discard target.
from tpy import Int32, Own


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def fresh() -> Own[Counter]:
    return Counter(100)


def main() -> None:
    items = [Counter(1), Counter(2), Counter(3)]

    # Both targets alias their list elements; the mutation is observed.
    a, b = (items[0], items[1])
    a.n += 10
    b.n += 20
    print(items[0].n)
    print(items[1].n)

    # `_` discards the second element (still evaluated); the first aliases.
    c, _ = (items[2], items[0])
    c.n += 5
    print(items[2].n)

    # Mixed: lvalue alias + fresh rvalue. The alias mutates the source; the
    # rvalue is an independent fresh object.
    d, e = (items[1], fresh())
    d.n += 1
    print(items[1].n)
    print(e.n)

    # Bare-name reference elements (the direct-bind path, no temps): f aliases
    # items[0], g aliases items[2].
    src0 = items[0]
    src2 = items[2]
    f, g = (src0, src2)
    f.n += 100
    g.n += 200
    print(items[0].n)
    print(items[2].n)


main()
