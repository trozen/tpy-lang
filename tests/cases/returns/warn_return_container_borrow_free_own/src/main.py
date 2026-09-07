# The FREE-FUNCTION spelling of warn_return_container_borrow_method_own: a
# borrow-returning free call at an OWNING container return slot copies and
# warns for the same reason. Pinned separately because a free call has no
# receiver to read -- only the callee's return convention says the source is
# borrowed. The copy is the ACKNOWLEDGED CPython divergence (CPython aliases
# the list), so the case prints only what both agree on and the WARNING is the
# pin.
from tpy import Int32, Own, copy


def first(rows: list[list[Int32]]) -> list[Int32]:
    return rows[0]


def take(rows: list[list[Int32]]) -> Own[list[Int32]]:
    # `first(rows)` hands back `std::vector<int32_t>&`; filling the owning
    # slot from it is the copy the warning declares.
    return first(rows)  # tpyc: warning(/copies list\[Int32\] into owned storage/)


def take_copy(rows: list[list[Int32]]) -> Own[list[Int32]]:
    return copy(first(rows))  # tpyc: ok


def main() -> None:
    rows = [[1, 2], [3]]
    print(len(take(rows)), len(take_copy(rows)), len(rows[0]))


main()
