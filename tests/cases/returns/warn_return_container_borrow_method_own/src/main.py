# The CONTAINER payload of warn_return_record_borrow_method_own: a
# BORROW-returning method call at an `Own[list[int32]]` return copies into the
# by-value slot and warns. The copy is the ACKNOWLEDGED CPython divergence --
# CPython hands back the very list, so an append to the result would show in
# `h.items` there and not here -- so the case prints only what both agree on
# and the WARNING is the pin. The BORROW-returning dunder at the same slot
# warns for the same reason (warn_return_container_borrow_dunder).
from tpy import int32, Own, copy


class Holder:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def borrow(self) -> list[int32]:
        return self.items


def take(h: Holder) -> Own[list[int32]]:
    # `h.borrow()` hands back `std::vector<int32_t>&`; filling the owning slot
    # from it is the copy the warning declares.
    return h.borrow()  # tpyc: warning(/copies list\[int32\] into owned storage/)


def take_copy(h: Holder) -> Own[list[int32]]:
    return copy(h.borrow())  # tpyc: ok


def main() -> None:
    h = Holder()
    print(len(take(h)), len(take_copy(h)), len(h.items))


main()
