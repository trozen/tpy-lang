# The FREE-FUNCTION spelling of error_return_container_borrow_method_own: a
# borrow-returning free call at an OWNING container return slot rejects for
# the same reason (the by-value slot would copy-initialize from the reference
# with nothing in the source saying so, and CPython aliases the list instead,
# BUGS.md#own-return-borrow-call-silent-copy). Pinned separately because this
# spelling used to COMPILE into the silent copy, where the method spelling
# rejected.
from tpy import Int32, Own


def first(rows: list[list[Int32]]) -> list[Int32]:
    return rows[0]


def take(rows: list[list[Int32]]) -> Own[list[Int32]]:
    # `first(rows)` hands back `std::vector<int32_t>&`; filling the owning
    # slot from it is the silent copy.
    return first(rows)  # tpyc: error(/return\.container_borrow_call_needs_copy/)


def main() -> None:
    rows = [[1, 2], [3]]
    got = take(rows)
    got.append(9)
    print(len(rows[0]), len(got))


main()
