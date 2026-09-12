# Scalar Own[A | B] (non-value pointer-variant Union). Param renders as
# `std::variant<A, B>&&` (storage form -- forces ownership transfer at
# the ABI). Body access pattern:
#  - isinstance(u, A) -> std::holds_alternative<A>(u).
#  - isinstance-narrowed branch binds `u` to `std::get<A>(u)` (extraction
#    via _emit_isinstance_extractions); subsequent member access uses
#    that local. The else-branch flow-narrowing is a separate
#    pre-existing limitation -- write explicit isinstance per branch.
#
# Sema reaches the union narrowing because `_filter_union_codegen_facts`
# peels Own before checking declared-type unionness.
from tpy import int32, Own


class A:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    y: int32
    def __init__(self, y: int32) -> None:
        self.y = y


def describe(u: Own[A | B]) -> int32:
    if isinstance(u, A):
        return u.x
    if isinstance(u, B):
        return u.y
    return int32(0)


def pick(flag: bool) -> Own[A | B]:
    if flag:
        return A(7)
    return B(11)


def borrow_union(u: A | B) -> int32:
    if isinstance(u, A):
        return u.x
    if isinstance(u, B):
        return u.y
    return int32(0)


def forward_to_borrow(u: Own[A | B]) -> int32:
    # Forwarding the storage-form variant param into a pointer-variant
    # slot needs to_ptr_variant; the bare value-variant doesn't convert.
    return borrow_union(u)


def test_body_isinstance_narrowing() -> None:
    print(describe(A(7)))
    print(describe(B(11)))


def test_return_into_pointer_variant_receiver() -> None:
    # Own[A|B] return into the canonical A|B receiver -- existing
    # to_ptr_variant lift handles the storage->pointer-variant conversion.
    print(describe(pick(True)))
    print(describe(pick(False)))


def test_forward_to_borrow_slot() -> None:
    print(forward_to_borrow(A(13)))
    print(forward_to_borrow(B(17)))


def main() -> None:
    test_body_isinstance_narrowing()
    test_return_into_pointer_variant_receiver()
    test_forward_to_borrow_slot()


main()
