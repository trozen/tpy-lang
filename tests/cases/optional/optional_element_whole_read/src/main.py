# A container literal whose ELEMENT slot is the Optional itself takes an
# un-narrowed Optional source whole -- the element IS the optional, so the read
# is the bare binding, not the narrowed deref. Covered for the scalar, Span and
# value-tuple families.
from tpy import int32, Span


def scalars(x: int32 | None) -> int32:
    xs = [x]                # tpyc: ok -- an `int32 | None` element
    return len(xs)


def spans(sp: Span[int32] | None) -> int32:
    xs = [sp]               # tpyc: ok -- a `Span[int32] | None` element
    return len(xs)


def pairs(tp: tuple[int32, int32] | None) -> int32:
    xs = [tp]               # tpyc: ok -- a value-tuple Optional element
    return len(xs)


def main() -> None:
    data: list[int32] = [1, 2, 3]
    sp: Span[int32] = data
    print(scalars(7), scalars(None))
    print(spans(sp), spans(None))
    print(pairs((1, 2)), pairs(None))


main()
