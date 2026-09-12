# A borrowed @nocopy element cannot be materialized into an owning slot, so the
# per-element copy diagnostic is an ERROR rather than a warning here. The
# borrow arrives as a CALL RESULT (an rvalue), the path the whole-tuple lvalue
# checks skip -- the sibling error_tuple_append_member_nocopy covers the lvalue
# source.
from tpy import int32, Own
from tplib import Box


def make_mixed(b: Box[int32]) -> tuple[Own[int32], Box[int32]]:
    return (int32(1), b)


def main() -> None:
    b = Box(int32(7))
    xs: list[tuple[int32, Box[int32]]] = []
    xs.append(make_mixed(b))  # tpyc: error(/cannot copy non-copyable type 'Box\[int32\]' into owned storage/)


main()
