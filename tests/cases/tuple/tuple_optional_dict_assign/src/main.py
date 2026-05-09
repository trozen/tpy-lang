# dict[K, tuple[T | None, ...]] subscript-assign of an rvalue tuple lifts
# pointer-form -> storage-form via tuple_to_storage. Storage-form sources
# (subscript, field, value-form local) skip the wrap.
from tpy import Int32


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def show(p: tuple[P | None, P | None]) -> None:
    a, b = p
    if a is not None:
        print(a.x)
    else:
        print("none")
    if b is not None:
        print(b.x)
    else:
        print("none")


def main() -> None:
    a = P(1)
    b = P(2)

    d: dict[Int32, tuple[P | None, P | None]] = {}
    # rvalue tuple literal -> storage-form lift required
    d[Int32(0)] = (a, b)
    d[Int32(1)] = (a, None)
    d[Int32(2)] = (None, None)

    show(d[Int32(0)])
    show(d[Int32(1)])
    show(d[Int32(2)])

    # storage-form source: assigning d[k] to another dict slot should
    # not double-wrap.
    d2: dict[Int32, tuple[P | None, P | None]] = {}
    d2[Int32(0)] = d[Int32(0)]
    show(d2[Int32(0)])


main()
