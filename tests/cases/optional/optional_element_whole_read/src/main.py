# A container literal whose ELEMENT slot is the Optional itself takes an
# un-narrowed Optional source whole -- the element IS the optional, so the read
# is the bare binding, not the narrowed deref. Covered for the scalar, Span and
# value-tuple families.
from tpy import Int32, Span


def scalars(x: Int32 | None) -> Int32:
    xs = [x]                # tpyc: ok -- an `Int32 | None` element
    return len(xs)


def spans(sp: Span[Int32] | None) -> Int32:
    xs = [sp]               # tpyc: ok -- a `Span[Int32] | None` element
    return len(xs)


def pairs(tp: tuple[Int32, Int32] | None) -> Int32:
    xs = [tp]               # tpyc: ok -- a value-tuple Optional element
    return len(xs)


def main() -> None:
    data: list[Int32] = [1, 2, 3]
    sp: Span[Int32] = data
    print(scalars(7), scalars(None))
    print(spans(sp), spans(None))
    print(pairs((1, 2)), pairs(None))


main()
