# The FREE-FUNCTION spelling of warn_return_record_borrow_method_own: a
# borrow-returning free call at an OWNING record return slot copies and warns.
# Pinned separately because a free call has no receiver to read -- only the
# callee's return convention says the source is borrowed. The copy is the
# ACKNOWLEDGED CPython divergence (CPython hands back the very Payload), so
# the case prints only what both agree on and the WARNING is the pin.
from tpy import int32, Own, copy


class Payload:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def first(rows: list[Payload]) -> Payload:
    return rows[0]


def take(rows: list[Payload]) -> Own[Payload]:
    # `first(rows)` hands back `Payload&`; filling the owning slot from it is
    # the copy the warning declares.
    return first(rows)  # tpyc: warning(/copies Payload into owned storage/)


def take_copy(rows: list[Payload]) -> Own[Payload]:
    return copy(first(rows))  # tpyc: ok


def main() -> None:
    rows = [Payload(1)]
    print(take(rows).n, take_copy(rows).n, rows[0].n)


main()
