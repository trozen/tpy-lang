"""Pins for the `deref_to_target` Ptr[T] coerce at the two BORROW-form slots:
the record borrow RETURN (`return ::tpy::deref_check(ptr);`) and a borrow
LOCAL's decl / reseat (`Point& p2 = ::tpy::deref_check(ptr);`, or the
reseatable `Point* copy = &(::tpy::deref_check(ptr));`). Both share
`_deref_coerce_borrow_slot` with the already-landed ARG position, so the key
cannot drift.

The boundary units cover the three shapes that must keep rejecting: the
record-wrapper `__deref__()` flavor (its render is a slot-typed VALUE copy),
an `Own[record]` STORAGE return, and a `Ptr[readonly[T]]` source at a
non-readonly slot (the AST emits an ill-formed `T&`-from-`const T` bind there
-- see BUGS-worthy note in the cell's commit)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally, _assert_routes_byte_identical, _fn, _lower_ctx_witnessed,
                       _thir_ctx)

_POINT = (
    "from tpy import Int32, Ptr, Own, readonly\n"
    "class Point:\n"
    "    x: Int32\n"
    "    y: Int32\n"
    "    def __init__(self, x: Int32, y: Int32) -> None:\n"
    "        self.x = x\n"
    "        self.y = y\n"
)

# A record wrapper exposing `__deref__` -- the flavor that hoists a slot-typed
# VALUE copy at the arg position and has no borrow-slot render at all.
_REF = (
    "class Ref:\n"
    "    pt: Point\n"
    "    def __init__(self, pt: Point) -> None:\n"
    "        self.pt = pt\n"
    "    def __deref__(self) -> Point:\n"
    "        return self.pt\n"
)


class TestDerefCoerceReturn:
    def test_ptr_deref_at_borrow_return_routes(self):
        src = _POINT + (
            "def deref_and_return(ptr: Ptr[Point]) -> Point:\n"
            "    return ptr\n"
            "def main() -> None:\n"
            "    pt = Point(1, 2)\n"
            "    p: Ptr[Point] = pt\n"
            "    print(deref_and_return(p).x)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Point& deref_and_return(Point* ptr) {" in hpp + cpp
        assert "return ::tpy::deref_check(ptr);" in hpp + cpp
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "deref_and_return") is not None
        assert faces["ret.record_deref_coerce"] >= 1

    def test_readonly_ptr_at_readonly_return_routes(self):
        # The const flavor: the `const T&` spelling comes from the signature
        # emitter, so the return statement's render is unchanged.
        src = _POINT + (
            "def deref_ro(p: Ptr[readonly[Point]]) -> readonly[Point]:\n"
            "    return p\n"
            "def main() -> None:\n"
            "    pt = Point(1, 2)\n"
            "    cp: Ptr[readonly[Point]] = pt\n"
            "    print(deref_ro(cp).y)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "const Point& deref_ro(const Point* p) {" in hpp + cpp
        assert "return ::tpy::deref_check(p);" in hpp + cpp

    def test_wrapper_deref_at_borrow_return_keeps_rejecting(self):
        # BOUNDARY: the record-wrapper flavor's arg render is a hoisted
        # slot-typed VALUE copy; no borrow-return render exists for it.
        src = _POINT + _REF + (
            "def unwrap(r: Ref) -> Point:\n"
            "    return r\n"
            "def main() -> None:\n"
            "    r = Ref(Point(1, 2))\n"
            "    print(unwrap(r).x)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.record_source.TpyCoerce.borrow")

    def test_ptr_deref_at_own_storage_return_keeps_rejecting(self):
        # BOUNDARY: `Own[Point]` is the by-value direction, so the slot is a
        # STORAGE return -- the borrow arm must not reach it. The owning
        # slot's copy row does claim the source (sema warns the copy), but
        # the deref COERCE has no read at the copy-construct's borrow bind,
        # so the reject lands one level in.
        src = _POINT + (
            "def own_from_ptr(p: Ptr[Point]) -> Own[Point]:\n"
            "    return p\n"
            "def main() -> None:\n"
            "    pt = Point(1, 2)\n"
            "    ptr: Ptr[Point] = pt\n"
            "    print(own_from_ptr(ptr).x)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:expr.coerce")

    def test_readonly_ptr_at_mutable_return_keeps_rejecting(self):
        # BOUNDARY (the const fence): `Point& f(const Point* p)` is what the
        # AST emits here -- g++ rejects it ("binding reference of type
        # 'Point&' to 'const Point' discards qualifiers"), so the shape stays
        # off THIR rather than mirroring an ill-formed render.
        src = _POINT + (
            "def deref_mut(p: Ptr[readonly[Point]]) -> Point:\n"
            "    return p\n"
            "def main() -> None:\n"
            "    pt = Point(1, 2)\n"
            "    cp: Ptr[readonly[Point]] = pt\n"
            "    print(deref_mut(cp).x)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:return.record_source.TpyCoerce.borrow")


class TestDerefCoerceLocal:
    def test_single_assign_binds_ref_alias(self):
        src = _POINT + (
            "def main() -> None:\n"
            "    pt = Point(5, 7)\n"
            "    ptr: Ptr[Point] = pt\n"
            "    p2: Point = ptr\n"
            "    print(p2.x)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Point& p2 = ::tpy::deref_check(ptr);" in hpp + cpp
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["decl.deref_coerce_alias"] >= 1

    def test_reassigned_binds_pointer_and_reseats(self):
        # A C++ reference cannot reseat, so the reassigned local is the
        # address-of pointer form -- decl and reseat both.
        src = _POINT + (
            "def main() -> None:\n"
            "    pt = Point(7, 8)\n"
            "    ptr: Ptr[Point] = pt\n"
            "    copy: Point = ptr\n"
            "    print(copy.x)\n"
            "    pt2 = Point(9, 10)\n"
            "    ptr2: Ptr[Point] = pt2\n"
            "    copy = ptr2\n"
            "    print(copy.x)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Point* copy = &(::tpy::deref_check(ptr));" in hpp + cpp
        assert "copy = &(::tpy::deref_check(ptr2));" in hpp + cpp
        assert "copy->x" in hpp + cpp
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces["decl.deref_coerce_addr"] >= 1
        assert faces["reseat.deref_coerce"] >= 1

    def test_rvalue_reassign_takes_the_rebind_slot(self):
        # BOUNDARY: a later RVALUE reassign needs the two-slot `__slot_N`
        # machinery, so the decl must carry `needs_rebind_slot` -- dropping it
        # would emit a reseat referencing an undeclared slot.
        src = _POINT + (
            "def main() -> None:\n"
            "    pt = Point(1, 2)\n"
            "    ptr: Ptr[Point] = pt\n"
            "    copy: Point = ptr\n"
            "    print(copy.x)\n"
            "    copy = Point(3, 4)\n"
            "    print(copy.x)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "Point* copy = &(::tpy::deref_check(ptr));" in hpp + cpp
        assert "copy = &*(__slot_1 = Point(3, 4));" in hpp + cpp

    def test_wrapper_deref_at_borrow_local_keeps_rejecting(self):
        # BOUNDARY: the wrapper flavor's only render is a VALUE copy temp.
        src = _POINT + _REF + (
            "def main() -> None:\n"
            "    r = Ref(Point(1, 2))\n"
            "    p2: Point = r\n"
            "    print(p2.x)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")

    def test_readonly_ptr_at_mutable_local_keeps_rejecting(self):
        # BOUNDARY (the const fence): the AST emits `Point& p2 =
        # ::tpy::deref_check(cp);` off a `const Point*`, which g++ rejects.
        src = _POINT + (
            "def main() -> None:\n"
            "    pt = Point(1, 2)\n"
            "    cp: Ptr[readonly[Point]] = pt\n"
            "    p2: Point = cp\n"
            "    print(p2.x)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")
