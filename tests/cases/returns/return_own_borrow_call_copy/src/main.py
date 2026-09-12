# The inverse of error_return_{container,record}_borrow_*_own: the spelling
# that compiles once the copy is asked for. COPY SEMANTICS ARE THE POINT
# here -- every return hands back an independent object, so the mutations
# after each boundary are deliberately NOT visible at the source, and CPython
# agrees because `copy()` deep-copies there. Both payload families take the
# same ONE-step spelling over every borrowed source shape (method call, free
# call, field, ternary of two calls).
from tpy import int32, Own, copy


class Payload:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    items: list[int32]
    p: Payload

    def __init__(self) -> None:
        self.items = [1, 2]
        self.p = Payload(1)

    def bctr(self) -> list[int32]:
        return self.items

    def brec(self) -> Payload:
        return self.p


def take_container(h: Holder) -> Own[list[int32]]:
    # A borrow-returning METHOD call, copied in one step.
    return copy(h.bctr())  # tpyc: ok


def first(rows: list[list[int32]]) -> list[int32]:
    return rows[0]


def frec(h: Holder) -> Payload:
    return h.p


def take_free(rows: list[list[int32]]) -> Own[list[int32]]:
    # A FREE borrow-returning call is the same borrowed source a method call
    # is, for either family.
    return copy(first(rows))  # tpyc: ok


def take_record(h: Holder) -> Own[Payload]:
    # The record hatch is one step over the same method-call source.
    return copy(h.brec())  # tpyc: ok


def take_record_free(h: Holder) -> Own[Payload]:
    return copy(frec(h))  # tpyc: ok


def take_record_field(h: Holder) -> Own[Payload]:
    # A FIELD read is an lvalue source, admitted by the same value-form rule.
    return copy(h.p)  # tpyc: ok


def take_record_ifexpr(h: Holder, pick: bool) -> Own[Payload]:
    # A ternary of two borrow-returning calls: both arms are lvalues, and the
    # copy-construct tail wraps whichever one runs.
    return copy(h.brec() if pick else frec(h))  # tpyc: ok


def main() -> None:
    h = Holder()
    got = take_container(h)
    got.append(3)
    print(len(h.items), len(got))

    rec = take_record(h)
    rec.n = 42
    print(h.p.n, rec.n)

    rec_free = take_record_free(h)
    rec_free.n = 43
    print(h.p.n, rec_free.n)

    rec_field = take_record_field(h)
    rec_field.n = 44
    print(h.p.n, rec_field.n)

    rec_if = take_record_ifexpr(h, False)
    rec_if.n = 45
    print(h.p.n, rec_if.n)

    rows = [[5]]
    free = take_free(rows)
    free.append(6)
    print(len(rows[0]), len(free))

    # The source side of the boundary is independent too.
    h.items.append(9)
    h.p.n = 7
    print(len(h.items), len(got), h.p.n, rec.n)


main()
