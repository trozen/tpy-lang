"""Nested-position temp threading: `allow_temps` rides through call-shaped
args, method receivers, binop operands, and the coro-factory arm, so the
ArgTemp rows fire at any depth under a flushable statement; plus the free-call
dict / set / Array literal ref-param hoist row."""

from __future__ import annotations

import pytest

from ..diagnostics import SemanticError
from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_EAT = (
    "from tpy import Int32\n"
    "def eat(xs: list[Int32]) -> Int32:\n"
    "    n = len(xs)\n"
    "    if n > 0:\n"
    "        xs.pop()\n"
    "    return n\n"
)

_UNION_HOLDER = (
    "from tpy import Int32\n"
    "class A:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "class B:\n"
    "    y: Int32\n"
    "    def __init__(self, y: Int32) -> None:\n"
    "        self.y = y\n"
    "class Holder:\n"
    "    sub: A | B\n"
    "    def __init__(self, sub: A | B) -> None:\n"
    "        self.sub = sub\n"
    "    def tag(self) -> Int32:\n"
    "        return 1\n"
)


class TestNestedTempThreading:
    def test_nested_call_container_literal_routes(self):
        # The literal ArgTemp fires one call level down: the print arg's
        # native len() call passes the flush right into the plain call's
        # args (`print(len(grow([1, 2])))`).
        src = (_EAT
               + "def grow(xs: list[Int32]) -> list[Int32]:\n"
               + "    xs.append(9)\n"
               + "    return xs\n"
               + "def use() -> None:\n"
               + "    print(len(grow([1, 2])))\n"
               + "    print(eat([1, 2, 3]) + 1)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("argtemp.container_literal", 0) >= 2
        _assert_byte_identical(src)

    def test_ctor_receiver_union_temp_routes(self):
        # A ctor RVALUE method receiver whose own arg needs the union-lift
        # temp (`Holder(A(1)).tag()`): the receiver recursion carries the
        # flush right into the ctor's arg rows.
        src = (_UNION_HOLDER
               + "def use() -> None:\n"
               + "    print(Holder(A(1)).tag())\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_guard_position_nested_temp_stays_ast(self):
        # A match guard is never a flush point: the nested literal ArgTemp
        # must keep falling back even though the same shape routes at a
        # statement position.
        src = (_EAT
               + "def use(n: Int32) -> Int32:\n"
               + "    match n:\n"
               + "        case v if eat([1, 2]) > 0:\n"
               + "            return v\n"
               + "        case _:\n"
               + "            return -1\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestDictSetArrayLiteralArgTemp:
    def test_dict_literal_free_arg_routes(self):
        src = ("from tpy import Int32\n"
               "def total(d: dict[str, Int32]) -> Int32:\n"
               "    n = 0\n"
               "    for k in d:\n"
               "        n += d[k]\n"
               "    return n\n"
               "def use() -> None:\n"
               "    print(total({'a': 1, 'b': 2}))\n"
               "    print(total({}))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("argtemp.container_literal", 0) >= 2
        _assert_byte_identical(src)

    def test_array_literal_free_arg_routes(self):
        src = ("from tpy import Int32, Array\n"
               "def sum3(a: Array[Int32, 3]) -> Int32:\n"
               "    return a[0] + a[1] + a[2]\n"
               "def use() -> None:\n"
               "    print(sum3([1, 2, 3]))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("argtemp.container_literal", 0) >= 1
        _assert_byte_identical(src)

    def test_native_slot_dict_literal_stays_ast(self):
        # The row is scoped to PLAIN free calls: a dict literal into a
        # native stub's slot keeps its own (unrouted) path.
        src = ("from tpy import Int32\n"
               "def use() -> None:\n"
               "    print(len({'a': 1}))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestCondTemps:
    def test_while_cond_temp_restructures(self):
        # Anonymous cond temps re-evaluate per iteration: `while (true) {
        # <temp>; if (!(cond)) break; ... }` (_gen_while's restructured head).
        src = (_EAT
               + "def use() -> Int32:\n"
               + "    it = 0\n"
               + "    while eat([1, 2, 3]) > 1:\n"
               + "        it += 1\n"
               + "        if it > 20:\n"
               + "            return it\n"
               + "    return it\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_elif_cond_temp_nests(self):
        # An elif cond registering temps abandons the flat chain: `} else {
        # <temp>; if (...) { ... } }` with the SAME `__tmp_N` numbering the
        # AST's discard-and-regenerate reissues.
        src = (_EAT
               + "def use(x: Int32) -> Int32:\n"
               + "    if x < 0:\n"
               + "        return -1\n"
               + "    elif eat([10, 20]) > 1:\n"
               + "        return 0\n"
               + "    else:\n"
               + "        return 1\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_scalar_walrus_cond_routes(self):
        # The value-scalar walrus: named pre-decl before the loop (visible
        # after it), inline `(n = v)` assign in the condition.
        src = ("from tpy import Int32\n"
               "def use(stop: Int32) -> Int32:\n"
               "    src = [3, 2, 1, 0]\n"
               "    i = 0\n"
               "    total = 0\n"
               "    while (n := src[i]) > stop:\n"
               "        total += n\n"
               "        i += 1\n"
               "    return total + n\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("expr.walrus_scalar", 0) >= 1
        _assert_byte_identical(src)

    def test_if_walrus_nested_temp_routes(self):
        # A temp nested INSIDE the walrus value evaluates before the
        # assignment on both paths: the if head lowers, the walrus predecl
        # and the arg temp flush in decl order (predecl first).
        src = (_EAT
               + "def use(x: Int32) -> Int32:\n"
               + "    if (v := eat([10, 20])) == x:\n"
               + "        return v\n"
               + "    return -v\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("expr.walrus_scalar", 0) >= 1
        cpp = _assert_byte_identical(src)
        assert ("int32_t v;\n"
                "    std::vector<int32_t> __tmp_1 = {10, 20};\n"
                "    if (((v = eat(__tmp_1)) == x))" in cpp[1])

    def test_elif_walrus_nested_temp_flushes_in_else_block(self):
        # The nested-elif flush point: the predecl + temp land inside the
        # else block, predecl first (the AST's else-block flush order).
        src = (_EAT
               + "def use(x: Int32) -> Int32:\n"
               + "    if x < 0:\n"
               + "        return -1\n"
               + "    elif (v := eat([10, 20])) == x:\n"
               + "        return v\n"
               + "    else:\n"
               + "        return -v\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        cpp = _assert_byte_identical(src)
        assert ("    } else {\n"
                "        int32_t v;\n"
                "        std::vector<int32_t> __tmp_1 = {10, 20};\n"
                "        if (((v = eat(__tmp_1)) == x))" in cpp[1])

    def test_if_walrus_outside_temp_stays_ast(self):
        # A temp OUTSIDE the walrus in the same if cond keeps the fence:
        # hoisting it ahead of the if would run it before the walrus
        # assignment it may read.
        src = (_EAT
               + "def use(x: Int32) -> Int32:\n"
               + "    if (v := x) == eat([1, 2]):\n"
               + "        return v\n"
               + "    return -v\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)

    def test_mixed_walrus_temp_cond_stays_ast(self):
        # Mixed walrus + temps keeps the AST's legacy single-eval flush
        # (BUGS residual): an in-head temp could run before the walrus
        # assignment it reads.
        src = (_EAT
               + "def use(k: Int32) -> Int32:\n"
               + "    total = 0\n"
               + "    guard = 0\n"
               + "    while (m := eat([k, k])) > 0 and guard < 5:\n"
               + "        total += m\n"
               + "        guard += 1\n"
               + "    return total\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)

    def test_str_walrus_first_decl_routes(self):
        # A FIRST-DECL owned-str walrus target predeclares the bare owned
        # slot on the named row and assigns in place, the value-scalar
        # shape at an owning buffer type.
        src = ("def use(s: str) -> str:\n"
               "    if (t := s + \"!\"):\n"
               "        return t\n"
               "    return s\n")
        _assert_routes_byte_identical(src)


class TestWalrusLadder:
    """The _gen_named_expr target-class ladder: ptr-Optional targets,
    borrow-alias pointer targets, value-opt/owned-viewfam reassigns,
    owned slots, borrow tuples -- each with its boundary."""

    _BOX = ("from tpy import Int32\n"
            "class Box:\n"
            "    val: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.val = v\n")

    def test_opt_ptr_field_lift_routes(self):
        src = (self._BOX
               + "class H:\n"
               + "    opt: Box | None\n"
               + "    def __init__(self, b: Box | None) -> None:\n"
               + "        self.opt = b\n"
               + "def use(h: H) -> Int32:\n"
               + "    if (t := h.opt) is not None:\n"
               + "        return t.val\n"
               + "    return 0\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("expr.walrus_opt_ptr", 0) >= 1
        _assert_byte_identical(src)

    def test_borrow_alias_name_and_field_recv(self):
        # `(q := b)` -> `Box* q = nullptr;` + `(q = &(b), *q)`; the field
        # read off the walrus takes the dot over the comma form.
        src = (self._BOX
               + "def use() -> Int32:\n"
               + "    b = Box(5)\n"
               + "    n = (q := b).val\n"
               + "    q.val = 9\n"
               + "    return n + b.val\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("expr.walrus_ptr_alias", 0) >= 1
        assert w.get("field.walrus_recv", 0) >= 1
        _assert_byte_identical(src)

    def test_value_opt_reassign_routes(self):
        src = ("from tpy import Int32\n"
               "def use() -> Int32:\n"
               "    x: Int32 | None = 5\n"
               "    if (x := None) is None:\n"
               "        return 1\n"
               "    return 0\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("expr.walrus_value_opt", 0) >= 1
        _assert_byte_identical(src)

    _GET_OPT = ("from tpy import Int32\n"
                "def get_opt(x: Int32) -> Int32 | None:\n"
                "    if x > 0:\n"
                "        return x * 10\n"
                "    return None\n")

    def test_value_opt_first_decl_walrus_routes(self):
        # FIRST-DECL value-opt scalar walrus: predecl the bare
        # `std::optional<int32_t> val;` slot, assign in place, and compose
        # `.has_value()` with the is-not-None compare; narrowed reads ride
        # the value-opt local registrations.
        src = (self._GET_OPT
               + "def use() -> Int32:\n"
               + "    if (val := get_opt(3)) is not None:\n"
               + "        return val + 5\n"
               + "    return -1\n"
               + "def main() -> None:\n    print(use())\nmain()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("expr.walrus_value_opt", 0) >= 1
        cpp = _assert_byte_identical(src)
        assert "std::optional<int32_t> val;" in cpp[1]
        assert "if (((val = get_opt(3)).has_value()))" in cpp[1]

    def test_value_opt_walrus_reuse_declares_once(self):
        # A REUSED target is the reassign on the predeclared slot -- no
        # duplicate C++ declaration.
        src = (self._GET_OPT
               + "def use() -> Int32:\n"
               + "    if (val := get_opt(3)) is not None:\n"
               + "        return val\n"
               + "    if (val := get_opt(5)) is not None:\n"
               + "        return val\n"
               + "    return -1\n"
               + "def main() -> None:\n    print(use())\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        cpp = _assert_byte_identical(src)
        assert cpp[1].count("std::optional<int32_t> val;") == 1

    def test_ptr_optional_walrus_call_source_stays_ast(self):
        # BOUNDARY (BUGS.md): a POINTER-repr Optional walrus with a CALL
        # source is the broken optional_to_ptr-less AST emit -- must keep
        # falling back, not be captured by the value-opt arm.
        src = ("from tpy import Int32, Own\n"
               "class Item:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def maybe_get(i: Int32) -> 'Own[Item | None]':\n"
               "    if i > 0:\n"
               "        return Item(i)\n"
               "    return None\n"
               "def use(i: Int32) -> Int32:\n"
               "    if (x := maybe_get(i)) is not None:\n"
               "        return x.x\n"
               "    return 0\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)

    def test_sibling_branch_nonvalue_rebind_sema_rejected(self):
        # The sibling-branch re-bind hazard for the NEW walrus classes is
        # UNREACHABLE: sema rejects walrus REASSIGNMENT of a non-value
        # local outright, so the function-scoped walrus_predeclared
        # asymmetry is load-bearing only for the scalar class (pinned by
        # test_scalar_walrus_cond_routes). This pins the sema boundary so
        # a future sema widening re-opens the question loudly.
        src = (self._BOX
               + "def use(flag: bool) -> Int32:\n"
               + "    a = Box(1)\n"
               + "    b = Box(2)\n"
               + "    if flag:\n"
               + "        n = (q := a).val\n"
               + "    else:\n"
               + "        n = (q := b).val\n"
               + "    return n\n")
        with pytest.raises(SemanticError,
                           match="walrus reassignment of non-value"):
            _lower_ctx(src)

    def test_resumable_walrus_rungs_stay_ast(self):
        # The pointer/slot rungs are SYNC-only: a resumable body's locals
        # are frame fields with different source renders.
        src = (self._BOX
               + "from typing import Iterator\n"
               + "def g() -> Iterator[Int32]:\n"
               + "    b = Box(5)\n"
               + "    yield (q := b).val\n"
               + "    yield q.val\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "g") is None
        _assert_byte_identical(src)

    def test_hoisted_walrus_target_stays_ast(self):
        # A try-hoisted walrus target keeps the AST's forward-declared
        # hoist model (need_predecl asymmetry unmirrored for hoists).
        src = (self._BOX
               + "class H:\n"
               + "    items: list[Int32]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.items = [1, 2]\n"
               + "    def view(self) -> list[Int32]:\n"
               + "        return self.items\n"
               + "def use(h: H) -> Int32:\n"
               + "    try:\n"
               + "        if len(v := h.view()) > 0:\n"
               + "            v.append(9)\n"
               + "    except ValueError:\n"
               + "        return -1\n"
               + "    return len(h.items)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)


class TestValueSelect:
    """Value-position and/or (THIRValueSelect, _gen_logical_value's value
    slice): eval-once LHS temps, lazy in-branch RHS, mixed-operand casts;
    record results and bool positions keep their own paths."""

    def test_scalar_select_routes_with_temp(self):
        src = ("from tpy import Int32\n"
               "def f(c: list[Int32], d: list[Int32]) -> Int32:\n"
               "    return c[0] or d[0]\n")
        thir, w = _lower_ctx_witnessed(src)
        fn = _fn(thir, "f")
        assert fn is not None
        assert w.get("binop.value_select", 0) >= 1
        sel = fn.body[0].value
        assert sel.lhs_temp_cpp == "auto&&" and sel.op == "||"
        _assert_byte_identical(src)

    def test_name_lhs_no_temp_and_chain(self):
        src = ("from tpy import Int32\n"
               "def f(a: Int32, c: list[Int32]) -> Int32:\n"
               "    return a or c[0] or c[1]\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("binop.value_select", 0) >= 2
        _assert_byte_identical(src)

    def test_side_effecting_lhs_evaluates_once(self):
        # The eval-once LHS temp: a side-effecting call LHS renders into
        # ONE `auto&& __tmp_N` reused by the truthy test and the chosen
        # branch -- a second render would double the side effect (the
        # byte-diff pins the AST's single-temp form).
        src = ("from tpy import Int32\n"
               "def bump(log: list[Int32], v: Int32) -> Int32:\n"
               "    log.append(v)\n"
               "    return v\n"
               "def f(log: list[Int32]) -> Int32:\n"
               "    return bump(log, 3) or 7\n")
        thir, w = _lower_ctx_witnessed(src)
        fn = _fn(thir, "f")
        assert fn is not None
        sel = fn.body[0].value
        assert sel.lhs_temp_cpp == "auto&&"
        _assert_byte_identical(src)

    def test_bool_position_keeps_bool_arm(self):
        src = ("from tpy import Int32\n"
               "def f(a: Int32, b: Int32) -> bool:\n"
               "    return a > 0 or b > 0\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert not w.get("binop.value_select")
        _assert_byte_identical(src)

    def test_record_select_routes_ref_alias(self):
        # RE-PINNED ROUTED (decl-slot track): a record-result select now
        # rides the reference-select machinery -- the always-truthy fold
        # (`true`) and the `Box&` REF_ALIAS bind (see
        # test_thir_wave_refselect for the full family).
        src = ("from tpy import Int32\n"
               "class Box:\n"
               "    val: Int32\n"
               "    def __init__(self, v: Int32) -> None:\n"
               "        self.val = v\n"
               "def f(a: Box, b: Box) -> Int32:\n"
               "    r = a or b\n"
               "    return r.val\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


class TestContainerPtrSlot:
    def test_reassigned_literal_decl_routes(self):
        # `std::vector<int32_t> __slot_1 = {1, 2, 3};` + `std::vector<...>*
        # a = &__slot_1;`, the `a = &(b)` reseat, the arrow method receiver,
        # and the deref len read.
        src = ("from tpy import Int32\n"
               "def use() -> Int32:\n"
               "    a = [1, 2, 3]\n"
               "    b = [4, 5, 6]\n"
               "    a = b\n"
               "    a.append(7)\n"
               "    return len(b) + len(a)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("decl.container_slot_rvalue", 0) >= 1
        _assert_byte_identical(src)

    def test_self_assign_and_setitem_deref(self):
        # `xs = xs;` (bare pointer copy) + the setitem/getitem derefs
        # (`::tpy::__setitem__((*xs), 0, 9)`).
        src = ("from tpy import Int32\n"
               "def use() -> Int32:\n"
               "    xs = [1, 2, 3]\n"
               "    xs = xs\n"
               "    xs[0] = 9\n"
               "    return xs[0] + xs[1]\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_branch_rebound_literal_not_claimed_by_ptr_slot_arm(self):
        # A branch-first reassigned container literal must not be claimed by
        # the same-block ptr-slot arm (witness absence -- the prescan guards
        # leave it to the branch-decl machinery). No byte compare: this is
        # the pre-existing branch-leaked PendingListType CODEGEN crash shape
        # (BUGS.md pending-container entry; both paths crash identically),
        # so only the lowering-side classification is checkable.
        src = ("from tpy import Int32\n"
               "def use(k: Int32) -> Int32:\n"
               "    if k > 0:\n"
               "        xs = [1, 2]\n"
               "    else:\n"
               "        xs = [3]\n"
               "    ys = [9]\n"
               "    xs = ys\n"
               "    return len(xs)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("decl.container_slot_rvalue", 0) == 0

    def test_move_through_container_literal_stays_ast(self):
        # A move-through target (`ys = xs` consuming xs at last use) keeps
        # the AST machinery; the decl arm must not claim it.
        src = ("from tpy import Int32\n"
               "def use() -> Int32:\n"
               "    xs = [1, 2]\n"
               "    xs = [3, 4]\n"
               "    ys = xs\n"
               "    return len(ys)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)

    def test_mixed_walrus_union_temp_cond_stays_ast(self):
        # The mixed-cond reject counts EVERY emit-time temp kind, not just
        # THIRArgTemp: a walrus + a union-lift ctor TEMP in one while cond
        # must fall back (the temp-bearing THIRUnionArgLift branch).
        src = ("from tpy import Int32, Float64\n"
               "class Dog:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "class Cat:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def check(a: Cat | Dog) -> bool:\n"
               "    return isinstance(a, Dog)\n"
               "def use(k: Int32) -> Int32:\n"
               "    total = 0\n"
               "    while (m := k - total) > 0 and check(Dog(m)):\n"
               "        total += 1\n"
               "    return total\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)

    def test_if_cond_walrus_read_after_block(self):
        # The if-cond walrus binding stays readable AFTER the if block
        # closes (function-scope pre-decl, the while sibling's guarantee).
        src = ("from tpy import Int32\n"
               "def use(k: Int32) -> Int32:\n"
               "    if (m := k + 1) > 3:\n"
               "        k += 1\n"
               "    return m + k\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_rvalue_reassigned_literal_routes_inline_reseat(self):
        # A literal-rvalue reseat rides the INLINE_RVALUE reseat arm off the
        # same pointer-local decl (`xs = &(__slot_N = {3, 4, 5})`-style
        # in-place block slot) -- dualgen-verified byte-identical.
        src = ("from tpy import Int32\n"
               "def use() -> Int32:\n"
               "    xs = [1, 2]\n"
               "    xs = [3, 4, 5]\n"
               "    return len(xs)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


_PET = (
    "from typing import Protocol\n"
    "from tpy import dynamic, Own\n"
    "from tplib import Box\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    def name(self) -> str: ...\n"
    "class Parrot(Pet):\n"
    "    label: str\n"
    "    def __init__(self, label: str) -> None:\n"
    "        self.label = label\n"
    "    def name(self) -> str:\n"
    "        return self.label\n"
    "class Dog:\n"
    "    label: str\n"
    "    def __init__(self, label: str) -> None:\n"
    "        self.label = label\n"
    "    def name(self) -> str:\n"
    "        return self.label\n"
)


class TestDynOwnConformerArg:
    def test_inherit_and_structural_route(self):
        # The verdict-keyed conformer wraps at `Own[P]` param slots: an
        # inheritance conformer takes `std::make_unique<Parrot>(...)`, a
        # structural one `::tpy::make_adapter<Pet>(...)` --
        # classify_dyn_own_arg is the single authority shared with the AST
        # render. (Decl positions ride sema's covariant upcast instead, so
        # the rows are exercised through a call.)
        src = (_PET
               + "def keep(p: Own[Pet]) -> str:\n"
               + "    b: Box[Pet] = Box(p)\n"
               + "    return b.get().name()\n"
               + "def use() -> str:\n"
               + "    return keep(Parrot(label='P')) + keep(Dog(label='R'))\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("dynown.make_unique", 0) >= 1
        assert w.get("dynown.adapter_conformer", 0) >= 1
        _assert_byte_identical(src)

    def test_own_readonly_protocol_slot_predicate_declines(self):
        # An `Own[readonly[P]]` slot never takes the adapter render on the
        # AST path (the ReadonlyType wrapper defeats is_dyn_protocol at the
        # AST key), so the gate keys the RAW wrapped type and must decline.
        from .lower.checks import _dyn_own_conformer_arg
        from ..typesys import OwnType, ReadonlyType, NominalType
        from ..parse.nodes import TpyName
        from .testutil import _compile
        from ..compilation_context import activate_compiler
        src = (_PET + "def use() -> str:\n    return Dog(label='R').name()\n")
        compiler, modules = _compile(src)
        entry = [m for m in modules if m.is_entry_point][0]
        with activate_compiler(compiler):
            ro_slot = OwnType(ReadonlyType(NominalType("Pet")))
            # The readonly reject fires on the SLOT alone, before any arg
            # shape/type consideration.
            assert _dyn_own_conformer_arg(
                TpyName(name="d"), ro_slot,
                {"d": NominalType("Dog")}, entry.analyzer) is None

    def test_covariant_arg_temp_and_inline(self):
        # A covariant NAME arg hoists the typed temp (`Box<Pet> __tmp_N =
        # std::move(bc);`); a covariant ctor RVALUE binds the Own slot
        # inline through the converting move ctor.
        src = (_PET
               + "def take(b: Box[Pet]) -> str:\n"
               + "    return b.get().name()\n"
               + "def use() -> str:\n"
               + "    bc = Box(Parrot(label='P'))\n"
               + "    xs: list[Box[Pet]] = []\n"
               + "    xs.append(Box(Parrot(label='Q')))\n"
               + "    return take(bc)\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("argtemp.covariant", 0) >= 1
        _assert_byte_identical(src)


class TestFixedIntLiteralBinopTarget:
    def test_int64_widening_rebuilds_template(self):
        # The dedicated literal arm re-resolves the operator at the TARGET
        # width: `b: Int64 = (4 + 5) + 6` -> `add_check<int64_t>` nested
        # paren-free (a latent int32-width divergence before the rebuild).
        src = ("from tpy import Int64\n"
               "def use() -> Int64:\n"
               "    b: Int64 = (4 + 5) + 6\n"
               "    return b\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_target_less_print_keeps_parens_or_folds(self):
        # A target-less print arg: a variable-free literal tree FOLDS
        # (`print(1 + (2 + 3))` -> `6`); a constant-VALUED but
        # variable-involving tree (subscripts over a literal-seeded
        # container) keeps the runtime chain WITH the wrapping parens.
        src = ("from tpy import Int32\n"
               "def use() -> None:\n"
               "    print(1 + (2 + 3))\n"
               "    xs = [1, 2]\n"
               "    xs = xs\n"
               "    print(xs[0] + xs[1])\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)
