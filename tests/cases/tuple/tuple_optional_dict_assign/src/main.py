# dict[K, tuple[T | None, ...]] subscript-assign of an rvalue tuple lifts
# pointer-form -> storage-form via tuple_to_storage. Storage-form sources
# (subscript, field, value-form local) skip the wrap.
from tpy import int32


class P:
    x: int32
    def __init__(self, x: int32) -> None:
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

    d: dict[int32, tuple[P | None, P | None]] = {}
    # rvalue tuple literal -> storage-form lift required
    d[int32(0)] = (a, b)
    d[int32(1)] = (a, None)
    d[int32(2)] = (None, None)

    show(d[int32(0)])
    show(d[int32(1)])
    show(d[int32(2)])

    # storage-form source: assigning d[k] to another dict slot should
    # not double-wrap.
    d2: dict[int32, tuple[P | None, P | None]] = {}
    d2[int32(0)] = d[int32(0)]  # tpyc: warning(/copies/) warning(/copies/)
    show(d2[int32(0)])


main()
