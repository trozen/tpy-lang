"""The poly early-return narrowing cell: negated guards
(`if not isinstance(v, Sub): return/raise`), the statement-level persistent
cast-and-cache alias (`THIRDynNarrowAlias`), the poly narrowing assert, and
the boundaries that must keep falling back (else body, `self` subject,
folded re-assert). Converted from the cell's dualgen boundary probes."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

_PRELUDE = (
    "from typing import Optional, Protocol\n"
    "from tpy import Int32, dynamic, readonly\n"
    "@dynamic\n"
    "class Tagged(Protocol):\n"
    "    def kind(self) -> str: ...\n"
    "class Base(Tagged):\n"
    "    def __init__(self) -> None:\n"
    "        pass\n"
    "    def kind(self) -> str:\n"
    "        return \"base\"\n"
    "    def tag(self) -> Int32:\n"
    "        return 0\n"
    "class A(Base):\n"
    "    def __init__(self) -> None:\n"
    "        pass\n"
    "    def tag(self) -> Int32:\n"
    "        return 1\n"
    "class B(A):\n"
    "    def __init__(self) -> None:\n"
    "        pass\n"
    "    def tag(self) -> Int32:\n"
    "        return 2\n"
    "class Free:\n"
    "    def __init__(self) -> None:\n"
    "        pass\n"
    "    def kind(self) -> str:\n"
    "        return \"free\"\n"
)


class TestDynPostIfRoutes:
    def test_bare_and_raise_guards(self):
        # The negated guard on a Ref-wrapped polymorphic-CLASS param (the
        # `_poly_subject_decl` Ref peel), return and raise terminators; the
        # deep-const param verdict spells the const cast.
        src = _PRELUDE + (
            "def f(p: Base) -> Int32:\n"
            "    if not isinstance(p, A):\n"
            "        return -1\n"
            "    return p.tag()\n"
            "def g(p: Base) -> Int32:\n"
            "    if not isinstance(p, A):\n"
            "        raise ValueError(\"want A\")\n"
            "    return p.tag()\n"
            "def main() -> None:\n"
            "    print(f(A()))\n"
            "    print(g(A()))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        out = hpp + cpp
        assert "if ((!((dynamic_cast<A*>(&p) != nullptr))))" in out
        assert "A& __p = *dynamic_cast<A*>(&p);" in out
        assert "return __p.tag();" in out

    def test_sequential_bump_and_anchor(self):
        # A re-narrowing chain bumps the alias (`__p` -> `__p_2`) and keeps
        # the cast input anchored to the ORIGINAL subject (`&p`, never the
        # live alias) via `lc.narrow.poly_source`.
        src = _PRELUDE + (
            "def f(p: Base) -> Int32:\n"
            "    if not isinstance(p, A):\n"
            "        return -1\n"
            "    if not isinstance(p, B):\n"
            "        return p.tag()\n"
            "    return p.tag() + 10\n"
            "def main() -> None:\n"
            "    print(f(B()))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        out = hpp + cpp
        assert "A& __p = *dynamic_cast<A*>(&p);" in out
        assert "B& __p_2 = *dynamic_cast<B*>(&p);" in out

    def test_optional_subject_bare_cast_input(self):
        # A pointer-shaped Optional[Base] subject spells the cast input
        # bare (`p`, already a pointer), not `&p`.
        src = _PRELUDE + (
            "def f(p: Optional[Base]) -> Int32:\n"
            "    if not isinstance(p, A):\n"
            "        return -1\n"
            "    return p.tag()\n"
            "def main() -> None:\n"
            "    print(f(None))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "*dynamic_cast<A*>(p);" in hpp + cpp

    def test_structural_conformer_adapter_cast(self):
        # A structural conformer of the @dynamic protocol rides
        # `dyn_adapter_cast` through the shared narrow_cast_rhs chokepoint.
        src = _PRELUDE + (
            "def f(t: Tagged) -> str:\n"
            "    if not isinstance(t, Free):\n"
            "        return \"other\"\n"
            "    return t.kind()\n"
            "def main() -> None:\n"
            "    print(f(Free()))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "::tpy::dyn_adapter_cast<Tagged, Free>(&t)" in hpp + cpp

    def test_non_terminating_guard_no_alias(self):
        # A negated guard whose then-body does NOT terminate lowers the
        # condition but emits no post-if alias (sema narrows nothing).
        src = _PRELUDE + (
            "def f(p: Base) -> Int32:\n"
            "    n = 0\n"
            "    if not isinstance(p, A):\n"
            "        n = 1\n"
            "    return n + p.tag()\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["narrow.dyn_neg_guard"] >= 1
        assert not faces.get("narrow.dyn_post_if")
        _assert_byte_identical(src)

    def test_assert_narrow_and_rebump(self):
        # The poly narrowing assert: null-check condition + persistent
        # alias; a second assert on a STRICTER member re-casts with the
        # bumped alias from the original subject.
        src = _PRELUDE + (
            "def f(p: Base) -> Int32:\n"
            "    assert isinstance(p, A)\n"
            "    assert isinstance(p, B)\n"
            "    return p.tag()\n"
            "def main() -> None:\n"
            "    print(f(B()))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        out = hpp + cpp
        assert ("if (!((dynamic_cast<A*>(&p) != nullptr))) "
                "::tpy::raise_assertion_error();") in out
        assert "A& __p = *dynamic_cast<A*>(&p);" in out
        assert "B& __p_2 = *dynamic_cast<B*>(&p);" in out
        assert "return __p_2.tag();" in out

    def test_assert_then_guard_cross_site_bump(self):
        # Cross-call-site: assert (persistent) then early-return guard --
        # the guard's bump must see the assert's alias in
        # `persistent_aliases`.
        src = _PRELUDE + (
            "def f(p: Base) -> Int32:\n"
            "    assert isinstance(p, A)\n"
            "    if not isinstance(p, B):\n"
            "        return p.tag()\n"
            "    return p.tag() + 10\n"
            "def main() -> None:\n"
            "    print(f(B()))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        out = hpp + cpp
        assert "B& __p_2 = *dynamic_cast<B*>(&p);" in out
        # The guard's then-branch read renames to the assert's alias.
        assert "return __p.tag();" in out


class TestDynPostIfBoundaries:
    def test_guard_with_else_stays_ast(self):
        # An else body would need the branch-entry extraction (the else
        # fact is always concrete here) -- unmirrored, whole body falls
        # back.
        src = _PRELUDE + (
            "def f(p: Base) -> Int32:\n"
            "    if not isinstance(p, A):\n"
            "        return -1\n"
            "    else:\n"
            "        return p.tag()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.if:if.dyn_narrow_shape")

    def test_self_subject_stays_ast(self):
        # A `self` subject's post-guard reads route through the receiver
        # arms (`this->`), which the narrow rename does not reach -- the
        # AST reads its `__self` alias. Dualgen caught the divergence;
        # fenced at the arm and the assert gate.
        src = _PRELUDE + (
            "class Widened(Base):\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def check(self) -> Int32:\n"
            "        if not isinstance(self, Narrow):\n"
            "            return -1\n"
            "        return self.tag()\n"
            "class Narrow(Widened):\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.if:if.dyn_narrow_shape")

    def test_value_position_compound_class_subject_routes(self):
        # `isinstance(p, A) and <narrowed read>` in VALUE position now
        # routes: the `&&` poly leaf lowers the check via the cast
        # chokepoint and SPELLS the RHS read inline (`(*static_cast<A*>(
        # &p))`) -- the former fence's "no value-position machinery"
        # reason was removed by the round-C inline-narrow arm. The bare
        # `_poly_isinstance_value_info` RefType guard stays (this arm
        # spells, it does not widen the vinfo).
        src = _PRELUDE + (
            "def f(p: Base) -> bool:\n"
            "    return isinstance(p, A) and p.tag() > 0\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("binop.poly_inline_narrow", 0) >= 1
        _assert_byte_identical(src)

    def test_same_member_reassert_recasts_with_bump(self):
        # A poly re-assert of the SAME member is NOT sema-folded (unlike
        # the union U4 fold): the AST re-checks and re-extracts with the
        # bumped alias, and the poly assert arm mirrors it -- must not
        # take the union-keyed `_reassert_bump_info` path (whose
        # `subject_union` lookup would crash on a poly subject).
        src = _PRELUDE + (
            "def f(p: Base) -> Int32:\n"
            "    assert isinstance(p, A)\n"
            "    assert isinstance(p, A)\n"
            "    return p.tag()\n"
            "def main() -> None:\n"
            "    print(f(A()))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        out = hpp + cpp
        assert "A& __p = *dynamic_cast<A*>(&p);" in out
        assert "A& __p_2 = *dynamic_cast<A*>(&p);" in out
        assert "return __p_2.tag();" in out

    def test_self_subject_assert_routes(self):
        # Converted fence: the assert-self leg landed -- reads rename through
        # the SPELLED map (the receiver arm's substitution), so the poly
        # narrowing assert on `self` routes with the `__self` alias.
        src = _PRELUDE + (
            "class Widened(Base):\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def check(self) -> Int32:\n"
            "        assert isinstance(self, Narrow)\n"
            "        return self.tag()\n"
            "class Narrow(Widened):\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "check") is not None
        _assert_byte_identical(src)

    def test_resumable_guard_stays_ast(self):
        # The resumable fence: a negated poly guard inside a generator
        # frame keeps rejecting (the `__self`/frame-field alias rename is
        # unmirrored; sync-only slice).
        src = _PRELUDE + (
            "from typing import Iterator\n"
            "def gen(p: Base) -> Iterator[Int32]:\n"
            "    if not isinstance(p, A):\n"
            "        return\n"
            "    yield p.tag()\n"
            "def main() -> None:\n"
            "    for v in gen(A()):\n"
            "        print(v)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "resumable:stmt.if:if.dyn_narrow_shape")


class TestStoredExceptionRaise:
    # The stored-exception raise (`raise s.exc` on a narrowed
    # Optional[Box[E]] field): the raise operand lowers at RECEIVER (it
    # receives `.__raise__()`), where the pre-existing receiver-position
    # field admissions carry the narrowed member read the gather_settled
    # quartet was sole-blocked on (the candidate gate row proved
    # unwitnessable and was deleted).
    SRC = (
        "from typing import Optional\n"
        "from tpy import Own\n"
        "from tplib.box import Box\n"
        "class MyError(Exception):\n"
        "    def __init__(self, msg: str) -> None:\n"
        "        super().__init__(msg)\n"
        "class Slot:\n"
        "    exc: Optional[Box[MyError]]\n"
        "    def __init__(self) -> None:\n"
        "        self.exc = None\n"
        "    def put(self, e: Own[Box[MyError]]) -> None:\n"
        "        self.exc = e\n"
        "def blow(s: Slot) -> None:\n"
        "    if s.exc is not None:\n"
        "        raise s.exc\n"
        "def main() -> None:\n"
        "    s = Slot()\n"
        "    s.put(Box(MyError(\"stored\")))\n"
        "    try:\n"
        "        blow(s)\n"
        "    except MyError as e:\n"
        "        print(\"caught:\", e.message)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        # NB every raise-operand field flavor (narrowed and plain) routes
        # via pre-existing RECEIVER-position field admissions -- the cell's
        # real fix was the operand USE change alone; a candidate
        # generic-record gate row proved unwitnessable and was deleted
        # (the dead-row doctrine).
        hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert "(*s.exc).__deref__().__raise__();" in hpp + cpp

    def test_receiver_row_scoped_out_of_value_positions(self):
        # The same field read at a VALUE position (a decl init) routes
        # through the narrowed-field DECL machinery -- byte-identical
        # either way (kept as a routing witness for that adjacent shape).
        from ..codegen_cpp import CodeGenOptions
        from .testutil import _assert_byte_identical, _compile, _entry
        src = self.SRC.replace(
            "def blow(s: Slot) -> None:\n"
            "    if s.exc is not None:\n"
            "        raise s.exc\n",
            "def peek(s: Slot) -> None:\n"
            "    if s.exc is not None:\n"
            "        b = s.exc\n"
            "        print(1)\n",
        ).replace(
            "    try:\n"
            "        blow(s)\n"
            "    except MyError as e:\n"
            "        print(\"caught:\", e.message)\n",
            "    peek(s)\n",
        )
        _assert_byte_identical(src)
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=True,
                                   comment_line_numbers=False))


class TestContainerComparePair:
    # The same-type container equality pair (`self.tags == other.tags` --
    # the @dataclass __eq__ chain): the container's own operator, bare
    # renders; the container FIELD operand rides BORROW_BIND into the
    # field gate's container row.
    SRC = (
        "from tpy import Int32\n"
        "class Bag:\n"
        "    tags: list[str]\n"
        "    def __init__(self) -> None:\n"
        "        self.tags = []\n"
        "    def same(self, other: Bag) -> bool:\n"
        "        return self.tags == other.tags\n"
        "def main() -> None:\n"
        "    a = Bag()\n"
        "    b = Bag()\n"
        "    print(a.same(b))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        from .testutil import _compile, _entry
        from ..codegen_cpp import CodeGenOptions
        hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert "(this->tags == other.tags)" in hpp + cpp
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        assert compiler._thir_face_witnesses.get("binop.container_eq")

    def test_array_field_pair_stays_ast(self):
        # BOUNDARY: the Array flavor is admitted at the PAIR gate but the
        # Array field operand has no read row (`_plain_container_read`
        # excludes it at the field gate) -- the body falls back
        # byte-identically. Wire the operand row before claiming Array.
        src = (
            "from tpy import Int32\n"
            "from tpy import Array\n"
            "class Grid:\n"
            "    cells: Array[Int32, 2]\n"
            "    def __init__(self) -> None:\n"
            "        self.cells = [0, 0]\n"
            "    def same(self, other: Grid) -> bool:\n"
            "        return self.cells == other.cells\n"
            "def main() -> None:\n"
            "    print(Grid().same(Grid()))\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:field.result_type")

    def test_ordering_op_stays_ast(self):
        # BOUNDARY: container ORDERING (`<`) has no witnessed shape --
        # the pair row is equality-only.
        src = self.SRC.replace("self.tags == other.tags",
                               "self.tags < other.tags")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.return:binop.shape.<")


class TestCheckedCallReceiver:
    # The deref-checked CALL receiver rows: an unproven field or method
    # access off a BORROW-returning ptr-Optional call wraps the raw `T*`
    # result (`::tpy::deref_check(find(...)).x` / `.mag()`).
    SRC = (
        "from typing import Optional\n"
        "from tpy import Int32\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "    def mag(self) -> Int32:\n"
        "        return self.x\n"
        "def find(pts: list[Point], target: Int32) -> Optional[Point]:\n"
        "    for p in pts:\n"
        "        if p.x == target:\n"
        "            return p\n"
        "    return None\n"
        "def main() -> None:\n"
        "    pts = [Point(5)]\n"
        "    print(find(pts, 5).x)\n"
        "    print(find(pts, 5).mag())\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        from .testutil import _compile, _entry
        from ..codegen_cpp import CodeGenOptions
        hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        out = hpp + cpp
        assert "::tpy::deref_check(find(pts, 5)).x" in out
        assert "::tpy::deref_check(find(pts, 5)).mag()" in out
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        assert compiler._thir_face_witnesses.get("field.opt_check_call_recv")
        assert compiler._thir_face_witnesses.get("method.opt_check_call_recv")

    def test_own_returning_call_receiver_stays_ast(self):
        # BOUNDARY: an Own-declared Optional return is a MATERIALIZED
        # storage optional -- deref_check takes a raw `T*`, so the shape
        # is excluded (the AST's own emit for it is uncompilable C++,
        # filed in BUGS.md; the fence keeps THIR off it byte-identically).
        src = (
            "from tpy import Int32, Own\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def make_maybe(flag: bool) -> Own[Point | None]:\n"
            "    if flag:\n"
            "        return Point(5)\n"
            "    return None\n"
            "def main() -> None:\n"
            "    print(make_maybe(True).x)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.expr_stmt:field.receiver_shape")


class TestDoubleSubscriptFieldReceiver:
    # The DOUBLE-subscript field receiver (`nested[0][0].x`): the inner
    # subscript is a nested-container element lvalue, so every level
    # renders the same __getitem__ nest with `.` member access.
    SRC = (
        "from tpy import Int32\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def main() -> None:\n"
        "    nested = [[Point(7)]]\n"
        "    print(nested[0][0].x)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert ("::tpy::__getitem__(::tpy::__getitem__(nested, 0), 0).x"
                in hpp + cpp)


class TestIterableOverrideOptionalCheckInvariant:
    # The cross-file invariant the checked-call receiver's
    # ptr_opt_passthrough threading rests on: all three iterable_override
    # predicates reject a runtime-check-marked call, so the ungated
    # passthrough fall-through can never carry a live
    # needs_optional_runtime_check. A fourth predicate must join this pin.
    SRC = (
        "from tpy import Int32\n"
        "def f(d: dict[str, Int32]) -> Int32:\n"
        "    n = 0\n"
        "    for v in d.values():\n"
        "        n += v\n"
        "    return n\n"
        "def main() -> None:\n"
        "    print(f({'a': 1}))\n"
        "main()\n"
    )

    def test_all_three_predicates_reject_checked_calls(self):
        from .testutil import _compile, _entry
        from ..compilation_context import activate_compiler
        from ..parse.nodes import TpyForEach, TpyMethodCall
        from ..thir.lower.predicates import _dict_view_iterable_ok
        from ..thir.lower.checks import (
            _member_gen_call_iterable_ok, _str_list_method_iterable_ok)
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        with activate_compiler(compiler):
            an = entry.analyzer
            fn = next(f for f in entry.ast.functions if f.name == "f")
            fe = next(s for s in fn.body if isinstance(s, TpyForEach))
            call = fe.iterable
            assert isinstance(call, TpyMethodCall)
            declared = {"d": dict(fn.params)["d"]}
            # Sanity: the unmarked call IS admitted by the dict-view arm.
            assert _dict_view_iterable_ok(
                call, declared, an, methods=("values", "keys", "items"))
            # The invariant: with the runtime-check marker set, every
            # override predicate declines.
            call.needs_optional_runtime_check = True
            try:
                assert not _dict_view_iterable_ok(
                    call, declared, an,
                    methods=("values", "keys", "items"))
                assert not _str_list_method_iterable_ok(call, declared, an)
                assert not _member_gen_call_iterable_ok(call, declared, an)
            finally:
                call.needs_optional_runtime_check = False
