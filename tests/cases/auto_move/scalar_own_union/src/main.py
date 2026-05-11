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
from tpy import Int32, Own


class A:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32
    def __init__(self, y: Int32) -> None:
        self.y = y


def describe(u: Own[A | B]) -> Int32:
    if isinstance(u, A):
        return u.x
    if isinstance(u, B):
        return u.y
    return Int32(0)


def pick(flag: bool) -> Own[A | B]:
    if flag:
        return A(7)
    return B(11)


def borrow_union(u: A | B) -> Int32:
    if isinstance(u, A):
        return u.x
    if isinstance(u, B):
        return u.y
    return Int32(0)


def forward_to_borrow(u: Own[A | B]) -> Int32:
    # Forwarding the storage-form variant param into a pointer-variant
    # slot needs to_ptr_variant; the bare value-variant doesn't convert.
    return borrow_union(u)


def return_to_borrow(u: Own[A | B]) -> A | B:
    # KNOWN-UB: to_ptr_variant(u) returns pointers into u's storage; u
    # dies at function exit so the returned variant<A*, B*> dangles.
    # Codegen is faithful to the source; the missing diagnostic is filed
    # under BUGS.md "Safety / borrow checker" (Own[Optional]/[Union]
    # dangling return). Caller reads r immediately and bytes haven't yet
    # been clobbered. When the sema rejection lands, rewrite this test.
    return u


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


def test_return_into_pointer_variant_return_type() -> None:
    r = return_to_borrow(A(19))
    if isinstance(r, A):
        print(r.x)


def main() -> None:
    test_body_isinstance_narrowing()
    test_return_into_pointer_variant_receiver()
    test_forward_to_borrow_slot()
    test_return_into_pointer_variant_return_type()


main()
