# A borrowed @nocopy element cannot be materialized into an owning slot, so the
# per-element copy diagnostic is an ERROR rather than a warning here. The
# borrow arrives as a CALL RESULT (an rvalue), the path the whole-tuple lvalue
# checks skip -- the sibling error_tuple_append_member_nocopy covers the lvalue
# source.
from tpy import Int32, Own
from tplib import Box


def make_mixed(b: Box[Int32]) -> tuple[Own[Int32], Box[Int32]]:
    return (Int32(1), b)


def main() -> None:
    b = Box(Int32(7))
    xs: list[tuple[Int32, Box[Int32]]] = []
    xs.append(make_mixed(b))  # tpyc: error(/cannot copy non-copyable type 'Box\[Int32\]' into owned storage/)


main()
