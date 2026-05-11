# Scalar Own[P | None] (pointer-repr Optional). Param renders as
# `std::optional<P>&&` (storage form -- forces ownership transfer at the
# ABI). Body access patterns:
#  - null check (`x is None` / `x is not None`) -> `.has_value()`.
#  - member access (`x.field`) -> `x->field` via `optional<P>::operator->`.
#  - field assignment from this local -> direct std::move into the field.
#  - argument forward into another `Own[P] | None` slot -> std::move.
#
# The Own qualifier is meaningful here: pointer-repr Optional is `P*` at
# the borrow boundary, but Own forces the storage form (`optional<P>`),
# which the function can move out of, store as a field, etc.
from tpy import Int32, Own


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


class Holder:
    slot: P | None
    def __init__(self) -> None:
        self.slot = None
    def store(self, p: Own[P | None]) -> None:
        self.slot = p


def take(x: Own[P | None]) -> Int32:
    if x is None:
        return Int32(-1)
    return x.x


def make(v: Int32) -> Own[P | None]:
    if v > 0:
        return P(v)
    return None


def passthrough(x: Own[P | None]) -> Own[P | None]:
    # Returning the storage-form Optional param directly: codegen must
    # `std::move(x)` the whole optional rather than dereffing -- (*x) on
    # a nullopt is UB and would also lose the None case.
    return x


def borrow(p: P | None) -> Int32:
    if p is None:
        return Int32(-1)
    return p.x


def forward_borrow(x: Own[P | None]) -> Int32:
    # Forwarding a storage-form Optional param into a borrow-form
    # `P | None` slot needs an explicit optional_to_ptr lift -- C++ won't
    # convert std::optional<P> to const P*.
    return borrow(x)


def return_into_borrow(x: Own[P | None]) -> P | None:
    # KNOWN-UB: returns optional_to_ptr(x) into a P* return; x dies at
    # function exit so the returned pointer dangles. Codegen is faithful
    # to the source; the missing diagnostic is filed under
    # BUGS.md "Safety / borrow checker" (Own[Optional]/[Union] dangling
    # return). Test reads rb->x immediately after the call -- happens to
    # work because the destructor of optional<P> doesn't clobber the
    # bytes. When the sema rejection lands, rewrite this test.
    return x


def make_pair(x: Own[P | None]) -> tuple[P | None, Int32]:
    # KNOWN-UB: same shape as return_into_borrow above -- the returned
    # tuple's P* slot points into x's storage and dangles after the
    # function returns. See BUGS.md borrow-checker entry.
    return (x, Int32(0))


def reassign_pointer(x: Own[P | None]) -> Int32:
    # Reassign a pointer-form local from the Own-Optional param: the
    # rebind path needs the lift, otherwise C++ assigns optional<P> to P*.
    y: P | None = None
    y = x
    if y is None:
        return Int32(-1)
    return y.x


def first_decl(x: Own[P | None]) -> Int32:
    # First-declaration of a pointer-form local from the Own-Optional
    # param (no prior `y = None` line). The var-decl path needs the lift
    # too -- otherwise C++ declares `P* y = x` with x as optional<P>.
    y = x
    if y is None:
        return Int32(-1)
    return y.x


def test_call_arg_lvalue_and_none() -> None:
    print(take(None))
    a = P(7)
    print(take(a))


def test_field_assign() -> None:
    h = Holder()
    h.store(P(42))
    if h.slot is not None:
        print(h.slot.x)
    h.store(None)
    print(h.slot is None)


def test_return_into_pointer_receiver() -> None:
    # Own[P|None] return into a P|None receiver: function returns
    # std::optional<P> (storage form) but the local is P*. Codegen
    # materializes a slot for the optional and lifts via optional_to_ptr.
    r = make(Int32(11))
    if r is not None:
        print(r.x)
    s = make(Int32(-1))
    print(s is None)


def test_forward_own_return_to_own_param() -> None:
    print(take(make(Int32(13))))


def test_return_passthrough() -> None:
    # The function's return type is the same Own[P|None] shape --
    # preserves the None case across the call.
    pt = passthrough(P(17))
    if pt is not None:
        print(pt.x)
    nope = passthrough(None)
    print(nope is None)


def test_forward_to_borrow_slot() -> None:
    # Forward into a borrow-form `P | None` slot via optional_to_ptr.
    print(forward_borrow(P(19)))
    print(forward_borrow(None))


def test_return_into_borrow_return_type() -> None:
    # Return into pointer-form `P | None` -- optional_to_ptr at return.
    rb = return_into_borrow(P(23))
    if rb is not None:
        print(rb.x)
    rb_none = return_into_borrow(None)
    print(rb_none is None)


def test_tuple_element() -> None:
    tp, tn = make_pair(P(29))
    if tp is not None:
        print(tp.x)
    print(tn)


def test_reassign_pointer_local() -> None:
    print(reassign_pointer(P(31)))
    print(reassign_pointer(None))


def test_first_decl_pointer_local() -> None:
    print(first_decl(P(37)))
    print(first_decl(None))


def test_rebind_from_successive_returns() -> None:
    # A single std::optional<P> slot holds the current value, the
    # pointer-local re-lifts after each rebind.
    z = make(Int32(41))
    z = make(Int32(43))
    if z is not None:
        print(z.x)
    z = make(Int32(-1))
    print(z is None)


def main() -> None:
    test_call_arg_lvalue_and_none()
    test_field_assign()
    test_return_into_pointer_receiver()
    test_forward_own_return_to_own_param()
    test_return_passthrough()
    test_forward_to_borrow_slot()
    test_return_into_borrow_return_type()
    test_tuple_element()
    test_reassign_pointer_local()
    test_first_decl_pointer_local()
    test_rebind_from_successive_returns()


main()
