# An aggregate ELEMENT slot over a @property read whose getter LENDS the
# receiver's storage -- and the receiver here is a NAMED binding, alive the
# whole time, so this is NOT the dying-source rule. The element slot has to
# pick between STORAGE (a copy: silent, where the field spelling `b._rec`
# aliases `Rec*` and CPython aliases too, so `b._rec.x = 99` below would be
# invisible through the tuple) and BORROW (a `Rec&` element, which no C++
# container can hold). The read means neither, so the element has no render;
# The comprehension sibling
# (`[b.rec for i in range(1)]`) takes the same tag at the same lowerer.
# The METHOD twin `(b.rec_m(), 1)` renders the copy instead of the
# alias, so there is no twin render for the getter to take.
# The composed tag's BASE differs by route -- `expr.tuple_literal` here, where
# the element is captured REF and the decl takes the pointer-repr path, and
# `expr.container_literal` where it is captured VALUE -- and that is left as
# it is on purpose: the base names the expression kind actually being lowered,
# and unifying it would have to lie about one of them. The DETAIL is the same
# over both routes, and the detail is what names the rule.
from tpy import int32


class Rec:
    x: int32

    def __init__(self) -> None:
        self.x = 1


class Bag:
    _rec: Rec

    def __init__(self) -> None:
        self._rec = Rec()

    @property
    def rec(self) -> Rec:
        return self._rec


def main() -> None:
    b = Bag()
    t = (b.rec, 1)  # tpyc: error(/container_elem.accessor_lends_storage/)
    print(t[0].x)


main()
