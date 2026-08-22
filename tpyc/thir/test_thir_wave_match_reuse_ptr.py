"""A nested `match` re-seating an enclosing match's REFERENCE capture: the
shared `T*` hoist slot, its const rung (re-asked at the decl site, since
sema fixes the stmt-borrow const flag before mutation inference settles a
borrowed param's const-ness), and the two reuse bind forms (field
sub-pattern and whole-subject `as`)."""

from __future__ import annotations

from .testutil import (_assert_byte_identical, _assert_routes_byte_identical,
                       _fn, _lower_ctx_witnessed)

_REC = (
    "from tpy import Int32\n"
    "class Inner:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "class Holder:\n"
    "    inner: Inner\n"
    "    def __init__(self, inner: Inner) -> None:\n"
    "        self.inner = inner\n"
)


class TestNestedCaptureReusePtr:
    def test_field_capture_reuse_routes_const_slot(self):
        # The re-seat aliases the INNER subject; the shared slot is const
        # because one of the binds reads a const subject.
        src = (_REC
               + "def reseats(h: Holder, g: Holder) -> Int32:\n"
               + "    match h:\n"
               + "        case Holder(inner=q):\n"
               + "            match g:\n"
               + "                case Holder(inner=q):\n"
               + "                    pass\n"
               + "            g.inner.n = 99\n"
               + "            return q.n\n"
               + "    return -1\n"
               + "def main() -> None:\n"
               + "    print(reseats(Holder(Inner(1)), Holder(Inner(2))))\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "const Inner* q;" in cpp
        assert "q = &(__match_subject_2.inner);" in cpp

    def test_as_capture_reuse_routes_ptr_bind(self):
        # The whole-subject `as` reuse takes the SAME address-of bind: the
        # inner match's own hoist map is empty, so without the reuse arm the
        # binding would spell a value copy.
        src = (_REC
               + "def as_pattern(h: Holder, g: Holder) -> Int32:\n"
               + "    match h:\n"
               + "        case Holder() as w:\n"
               + "            match g:\n"
               + "                case Holder() as w:\n"
               + "                    pass\n"
               + "            g.inner.n = 55\n"
               + "            return w.inner.n\n"
               + "    return -1\n"
               + "def main() -> None:\n"
               + "    print(as_pattern(Holder(Inner(1)), Holder(Inner(2))))\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "as_pattern") is not None
        assert faces.get("match.bind_reuse_ptr", 0) >= 1
        assert faces.get("match.hoist_ptr_const", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "const Holder* w;" in cpp
        assert "w = &(__match_subject_2);" in cpp


class TestNestedCaptureReuseBoundaries:
    def test_single_match_hoist_keeps_phase1_verdict(self):
        # BOUNDARY: the const re-ask is scoped to match-rooted NESTED reuse.
        # A single match keeps the Phase-1 verdict (understated const -- the
        # open AST defect), and THIR must mirror it rather than "fix" it.
        src = (_REC
               + "def show(h: Holder) -> Int32:\n"
               + "    total = 0\n"
               + "    match h:\n"
               + "        case Holder(inner=q):\n"
               + "            total += q.n\n"
               + "    return total\n"
               + "def main() -> None:\n"
               + "    print(show(Holder(Inner(1))))\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "Inner* q;" in cpp
        assert "const Inner* q;" not in cpp

    def test_plain_borrow_local_reuse_keeps_its_own_arm(self):
        # BOUNDARY: the admission keys on the MATCH ptr-hoist set, never on
        # `pointers` / ref aliases at large. A capture reusing a PLAIN
        # borrow local (`const Inner& q = h.inner;`) keeps its pre-existing
        # arm -- neither reuse face fires, so a re-key to a broader set
        # fails here.
        src = (_REC
               + "def f(h: Holder, g: Holder) -> Int32:\n"
               + "    q = h.inner\n"
               + "    total = q.n\n"
               + "    match g:\n"
               + "        case Holder(inner=q):\n"
               + "            total += q.n\n"
               + "    return total\n"
               + "def main() -> None:\n"
               + "    print(f(Holder(Inner(1)), Holder(Inner(2))))\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("match.bind_reuse_ptr", 0) == 0
        assert faces.get("match.hoist_ptr_const", 0) == 0
        _assert_byte_identical(src)
