# A BORROW-returning method call at an OWNING container return slot
# (`-> Own[list[Int32]]`): the reference would copy-initialize the by-value
# slot with nothing in the source saying so, and the copy is OBSERVABLE --
# CPython hands back the very list, so `got.append(3)` there would show up in
# `h.items` and here it would not. The copy has to be spelled (`copy()`)
# until the sema warning that demands it reaches the return position
# (BUGS.md#own-return-borrow-call-silent-copy), so the shape rejects. The
# BORROW-returning dunder at the same slot rejects for the same reason
# (error_return_container_borrow_dunder).
from tpy import Int32, Own


class Holder:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def borrow(self) -> list[Int32]:
        return self.items


def take(h: Holder) -> Own[list[Int32]]:
    # `h.borrow()` hands back `std::vector<int32_t>&`; filling the owning slot
    # from it is the silent copy.
    return h.borrow()  # tpyc: error(/return\.container_borrow_call_needs_copy/)


def main() -> None:
    h = Holder()
    got = take(h)
    got.append(3)
    print(len(got), got[2])


main()
