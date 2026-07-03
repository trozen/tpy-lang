"""THIR value-scalar core: lowering eligibility/shape, statement shapes
(for-range/foreach/print), scalar exprs, dump, and the byte-identical emit
contract (THIR codegen == AST codegen for the slice)."""

from __future__ import annotations

import io

from ..codegen_cpp.context import CodeGenOptions
from ..parse.nodes import TpyCall
from .dump import dump_thir
from .emit import emit_thir_body
from .lower import _is_len_native
from .nodes import (
    Form, PrintForm, THIRAssign, THIRBinOp, THIRCall, THIRExprStmt,
    THIRForEach, THIRForRange, THIRIf, THIRLiteral, THIRName, THIRPrint,
    THIRReturn, THIRStrLiteral, THIRUnaryNot, THIRVarDecl, THIRWhile,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn, _lower_ctor, _PRELUDE,
    _F1_RECORDS,
)

class TestEligibility:
    def test_simple_function_is_eligible(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    b = a\n    return b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert [p.name for p in fn.params] == ["a"]
        assert isinstance(fn.body[0], THIRVarDecl) and fn.body[0].name == "b"
        assert isinstance(fn.body[0].init, THIRName) and fn.body[0].init.name == "a"
        assert isinstance(fn.body[1], THIRReturn)

    def test_reassignment_lowers_to_assign(self):
        # The parser emits TpyVarDecl for every `name = expr`; a write to an
        # already-bound name must lower to THIRAssign, not a re-declaration.
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n"
                      + "    x = a\n    x = b\n    return x\n")
        fn = _fn(thir, "f")
        assert isinstance(fn.body[0], THIRVarDecl)   # x = a   (first: decl)
        assert isinstance(fn.body[1], THIRAssign)    # x = b   (reassign)
        assert fn.body[1].target.name == "x"

    def test_param_reassignment_is_assign(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    a = a\n    return a\n")
        fn = _fn(thir, "f")
        assert isinstance(fn.body[0], THIRAssign)    # param already bound

    def test_void_return_is_eligible(self):
        thir = _lower(_PRELUDE + "def f(a: Int32):\n    b = a\n")
        assert _fn(thir, "f") is not None

    def test_global_reference_is_ineligible(self):
        # A name resolving to a module global needs a qualified C++ symbol the
        # slice does not yet materialize -> stays on the AST path.
        thir = _lower(_PRELUDE + "G: Int32 = 5\ndef f() -> Int32:\n    return G\n")
        assert _fn(thir, "f") is None

    def test_arith_binop_is_eligible(self):
        # c = a + b + 1; return c -- nested arithmetic with a literal
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n    c = a + b + 1\n    return c\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].init, THIRBinOp)
        assert fn.body[0].init.op == "+"
        assert isinstance(fn.body[0].init.left, THIRBinOp)   # (a + b) + 1

    def test_same_module_call_is_eligible(self):
        thir = _lower(_PRELUDE
                      + "def g(a: Int32) -> Int32:\n    return a + 1\n"
                      + "def f(a: Int32) -> Int32:\n    return g(a) + g(a + 1)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]               # return g(a) + g(a + 1)
        assert isinstance(ret.value, THIRBinOp)
        assert isinstance(ret.value.left, THIRCall) and ret.value.left.callee == "g"

    def test_builtin_call_is_ineligible(self):
        # `abs` is an imported builtin -> qualified/special emit, not bare.
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    return abs(a)\n")
        assert _fn(thir, "f") is None

    def test_scalar_aug_assign_routes(self):
        # TpyAugAssign on a scalar local -- formerly ineligible, now admitted.
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    a += 1\n    return a\n")
        assert _fn(thir, "f") is not None

    def test_wide_literal_is_ineligible(self):
        # A literal outside [-2**31, 2**31-1] needs a suffix/cast the emitter
        # does not reproduce, even in a slot (UInt64) that can hold it.
        thir = _lower(_PRELUDE
                      + "def f(a: UInt64) -> UInt64:\n    b = a\n    b = 5000000000\n    return b\n")
        assert _fn(thir, "f") is None

    def test_if_else_is_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n"
                      + "    r = a\n"
                      + "    if a < b:\n        r = b\n    else:\n        r = a\n"
                      + "    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[1], THIRIf)
        assert isinstance(fn.body[1].condition, THIRBinOp)
        assert fn.body[1].condition.op == "<"
        assert isinstance(fn.body[1].then_body[0], THIRAssign)

    def test_elif_lowers_as_nested_if(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32) -> Int32:\n    r = a\n"
                      + "    if a < 0:\n        r = 0\n    elif a > 9:\n        r = 9\n"
                      + "    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[1]
        assert isinstance(outer, THIRIf)
        assert len(outer.else_body) == 1 and isinstance(outer.else_body[0], THIRIf)

    def test_branch_local_first_decl_is_ineligible(self):
        # `t` is first-declared inside the branch -> needs scope machinery.
        thir = _lower(_PRELUDE
                      + "def f(a: Int32) -> Int32:\n    r = a\n"
                      + "    if a < 0:\n        t = 0 - a\n        r = t\n"
                      + "    return r\n")
        assert _fn(thir, "f") is None

    def test_truthiness_condition_is_ineligible(self):
        # `if a:` (int truthiness) is not a comparison condition.
        thir = _lower(_PRELUDE
                      + "def f(a: Int32) -> Int32:\n    r = a\n"
                      + "    if a:\n        r = 0\n    return r\n")
        assert _fn(thir, "f") is None

    def test_literal_first_decl_is_eligible(self):
        # `total = 0` resolves to the default int (not IntLiteralType).
        thir = _lower(_PRELUDE + "def f() -> Int32:\n    total = 0\n    return total\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.body[0].resolved_type.name == "Int32"

    def test_while_is_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    i = 0\n"
                      + "    while i < n:\n        i = i + 1\n    return i\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[1], THIRWhile)
        assert fn.body[1].condition.op == "<"

    def test_while_else_is_ineligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    i = 0\n"
                      + "    while i < n:\n        i = i + 1\n    else:\n        i = 0\n"
                      + "    return i\n")
        assert _fn(thir, "f") is None

    def test_while_with_break_is_ineligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    i = 0\n"
                      + "    while i < n:\n        if i > 3:\n            break\n        i = i + 1\n"
                      + "    return i\n")
        assert _fn(thir, "f") is None

    def test_non_fixed_int_param_is_ineligible(self):
        # `int` is BigInt, not a fixed-width scalar -> outside the slice.
        thir = _lower("def f(a: int) -> int:\n    b = a\n    return b\n")
        assert _fn(thir, "f") is None

    def test_staticmethod_routes_like_free_function(self):
        # A staticmethod has no `self` receiver; its body lowers like a free
        # function's (the `static` prefix is signature-only).
        thir = _lower_ctx(
            _PRELUDE
            + "class C:\n    @staticmethod\n    def m(a: Int32) -> Int32:\n        b = a\n        return b\n")
        assert _fn(thir, "m") is not None

    def test_generic_is_ineligible(self):
        thir = _lower("def f[T](a: T) -> T:\n    b = a\n    return b\n")
        assert _fn(thir, "f") is None

    def test_walk_state_branch_copy_isolates_every_field(self):
        # branch_copy must deep-copy EVERY container field: a branch mutation
        # leaking into the parent scope would let a branch-local fact (a
        # POINTER decl, a narrowing) survive past the branch. Field-generic
        # so a sixth field added for a later cell can't be silently shared.
        from dataclasses import fields
        from .lower import _WalkState
        ws = _WalkState({"a": None})
        copy = ws.branch_copy()
        for f in fields(_WalkState):
            container = getattr(copy, f.name)
            if isinstance(container, dict):
                container["x"] = None
            else:
                container.add("x")
            assert "x" not in getattr(ws, f.name)


class TestForRange:
    def test_range_stop_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    total = 0\n"
                      + "    for i in range(n):\n        total = total + i\n    return total\n")
        fn = _fn(thir, "f")
        assert fn is not None
        loop = fn.body[1]
        assert isinstance(loop, THIRForRange)
        assert loop.var == "i"
        assert loop.start is None                       # range(stop) -> implicit 0
        assert isinstance(loop.stop, THIRName) and loop.stop.name == "n"
        assert loop.stop_is_literal is False            # name bound -> hoisted
        assert isinstance(loop.body[0], THIRAssign)

    def test_range_start_stop_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(a, b):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange)
        assert isinstance(loop.start, THIRName) and loop.start.name == "a"
        assert isinstance(loop.stop, THIRName) and loop.stop.name == "b"
        assert loop.start_is_literal is False and loop.stop_is_literal is False

    def test_range_literal_bound_inlined(self):
        thir = _lower(_PRELUDE
                      + "def f() -> Int32:\n    total = 0\n"
                      + "    for i in range(10):\n        total = total + i\n    return total\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange) and loop.stop_is_literal is True

    def test_literal_start_name_stop(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(2, n):\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert loop.start_is_literal is True and loop.stop_is_literal is False

    def test_nested_range_eligible(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(a, b):\n        for j in range(b):\n"
                      + "            acc = acc + j\n    return acc\n")
        outer = _fn(thir, "f").body[1]
        assert isinstance(outer, THIRForRange)
        assert isinstance(outer.body[0], THIRForRange)

    def test_loop_var_reassign_in_body_eligible(self):
        # The loop var is visible in the body; rebinding it lowers to THIRAssign,
        # not a re-declaration (it is declared by the C++ for-init).
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        i = i + 1\n        acc = acc + i\n    return acc\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForRange)
        assert isinstance(loop.body[0], THIRAssign) and loop.body[0].target.name == "i"

    def test_for_else_is_ineligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        acc = acc + i\n    else:\n        acc = 0\n"
                      + "    return acc\n")
        assert _fn(thir, "f") is None

    def test_loop_var_used_after_is_ineligible(self):
        # `i` read after the loop -> sema hoists the loop var (pre-declaration),
        # which the emitter's plain C++ for-scope binding does not reproduce.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    last = 0\n"
                      + "    for i in range(n):\n        last = i\n    return last + i\n")
        assert _fn(thir, "f") is None

    def test_stepped_range_is_ineligible(self):
        # 3-arg range -> step handling (overflow checks etc.) outside the slice.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(0, n, 2):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None

    def test_break_in_body_is_ineligible(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        if i > 3:\n            break\n        acc = acc + i\n"
                      + "    return acc\n")
        assert _fn(thir, "f") is None

    def test_continue_in_body_is_ineligible(self):
        # TpyContinue is not in the supported statement set (default-reject) --
        # an explicit guard so a future _stmt_eligible arm can't silently route it.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n):\n        if i > 3:\n            continue\n        acc = acc + i\n"
                      + "    return acc\n")
        assert _fn(thir, "f") is None

    def test_loop_var_shadowing_outer_is_ineligible(self):
        # A loop var name already bound in the outer scope hits the AST path's
        # was_declared handling (no fresh for-init decl), which the slice skips.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n    i = 0\n"
                      + "    for i in range(n):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None

    def test_binop_bound_is_ineligible(self):
        # A non-literal, non-name bound (binop) is deferred: gen_range_args'
        # _gen_expr_deref(arg, ptype) rendering is not yet net-confirmed vs _emit_expr.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(n + 1):\n        acc = acc + i\n    return acc\n")
        assert _fn(thir, "f") is None



# --- Statement-shape axis: container iteration (for x in list/set/dict) + len() ---

# `for x in <NativeIterable>:` over a value-scalar element (the begin/end loop, a value
# loop var) routes; a record element (auto&& loop var), str/bytes-key dict, tuple-unpack,
# and generators/user-iterators ride later cells. `len(c)` -> `::tpy::__len__(c)`.
class TestForEachContainer:
    def test_list_scalar_routes(self):
        thir = _lower(
            _PRELUDE
            + "def total(items: list[Int32]) -> Int32:\n    s = 0\n"
            + "    for x in items:\n        s = s + x\n    return s\n")
        fn = _fn(thir, "total")
        assert fn is not None
        loop = fn.body[1]
        assert isinstance(loop, THIRForEach) and loop.var == "x"
        assert isinstance(loop.iterable, THIRName) and loop.iterable.name == "items"

    def test_dict_fixed_int_key_routes(self):
        thir = _lower(
            _PRELUDE
            + "def keysum(d: dict[Int32, Int32]) -> Int32:\n    s = 0\n"
            + "    for k in d:\n        s = s + k\n    return s\n")
        loop = _fn(thir, "keysum").body[1]
        assert isinstance(loop, THIRForEach) and loop.var == "k"

    def test_record_element_routes(self):
        # A `list[record]` element binds `auto&&`/`const auto&` (a borrow alias);
        # field reads are `.field`, like a record param.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(items: list[Inner]) -> Int32:\n    s = 0\n"
            + "    for p in items:\n        s = s + p.value\n    return s\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForEach) and loop.var == "p"
        assert isinstance(loop.iterable, THIRName) and loop.iterable.name == "items"

    def test_record_field_mutation_routes(self):
        # Writing through the record loop var (`p.value = ...`) routes -- the alias
        # semantics match Python (the list element is mutated). Sema forbids reassigning
        # the loop var itself, so that divergent case never reaches THIR.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(items: list[Inner]) -> None:\n"
            + "    for p in items:\n        p.value = p.value + 1\n")
        loop = _fn(thir, "f").body[0]
        assert isinstance(loop, THIRForEach) and loop.var == "p"

    def test_dict_record_value_ineligible(self):
        # `for k in d` over dict[int, record] yields scalar KEYS, but the param itself
        # isn't admitted: `_container_scalar_read` requires a scalar VALUE, and record
        # values ride a later cell (`dict[int, record]` key iteration), so it stays AST.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(d: dict[Int32, Inner]) -> Int32:\n    s = 0\n"
            + "    for k in d:\n        s = s + k\n    return s\n")
        assert _fn(thir, "f") is None

    def test_str_keyed_dict_iteration_routes(self):
        # An owned-str dict key routes (S5): the loop var is a fresh view var,
        # here usage-resolved to a std::string_view binding.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[str, Int32]) -> Int32:\n    s = 0\n"
            + "    for k in d:\n        s = s + 1\n    return s\n")
        fn = _fn(thir, "f")
        assert fn is not None
        loop = fn.body[1]
        assert isinstance(loop, THIRForEach)
        assert loop.elem_type.to_cpp() == "std::string_view"

    def test_tuple_unpack_ineligible(self):
        # `for k, v in d.items()` (tuple-unpack) rides a later cell.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[Int32, Int32]) -> Int32:\n    s = 0\n"
            + "    for k, v in d.items():\n        s = s + k\n    return s\n")
        assert _fn(thir, "f") is None

    def test_len_call_routes(self):
        thir = _lower(
            _PRELUDE
            + "def ln(xs: list[Int32]) -> Int32:\n    return len(xs)\n")
        call = _fn(thir, "ln").body[0].value
        assert isinstance(call, THIRCall) and call.native_name == "tpy::__len__"

    def test_range_len_routes(self):
        # `for i in range(len(xs))` -- len as a range bound; composes with the
        # container subscript read (and lights up its bounds-safe branch).
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> Int32:\n    n = 0\n"
            + "    for i in range(len(xs)):\n        n = n + xs[i]\n    return n\n")
        assert _fn(thir, "f") is not None

    def test_user_len_is_not_native_dispatched(self):
        # A user function named `len` has native_name None, so the emit keeps it a plain
        # call -- the len dispatch keys on the resolved symbol, not the source name.
        _, modules = _compile(
            _PRELUDE
            + "def len(x: Int32) -> Int32:\n    return x\n"
            + "def f(y: Int32) -> Int32:\n    return len(y)\n")
        entry = _entry(modules)
        f = next(fn for fn in entry.ast.functions if fn.name == "f")
        call = f.body[0].value
        assert isinstance(call, TpyCall) and not _is_len_native(call)

    def test_len_on_pointer_local_record_ineligible(self):
        # Regression: len() is gated on container type. A record with __len__ bound to a
        # reseated pointer-local emits bare `::tpy::__len__(p)`, but the AST derefs it
        # (`(*p)`); admitting it would break the byte-identical contract (a g++ error).
        src = (
            "from tpy import Int32\n"
            "class Bag:\n    data: list[Int32]\n"
            "    def __init__(self, d: list[Int32]) -> None:\n        self.data = d\n"
            "    def __len__(self) -> Int32:\n        return len(self.data)\n"
            "class Two:\n    a: Bag\n    b: Bag\n"
            "    def __init__(self, a: Bag, b: Bag) -> None:\n"
            "        self.a = a\n        self.b = b\n"
            "def pick(o: Two, flag: bool) -> Int32:\n"
            "    p = o.a\n    if flag:\n        p = o.b\n    return len(p)\n")
        assert _fn(_lower_ctx(src), "pick") is None



class TestForEachContainerEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def total(items: list[Int32]) -> Int32:\n    s = 0\n"
        + "    for x in items:\n        s = s + x\n    return s\n"
        + "def count_pos(xs: list[Int32]) -> Int32:\n    n = 0\n"
        + "    for i in range(len(xs)):\n        if xs[i] > 0:\n            n = n + 1\n"
        + "    return n\n"
        + "def main():\n    print(total([1, 2, 3]))\n    print(count_pos([1, -2, 3]))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_begin_end_loop(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "auto& __obj_0 = items;" in cpp
        assert "auto __beg_0 = __obj_0.begin();" in cpp
        assert "for (; __beg_0 != __end_0; ++__beg_0) {" in cpp
        assert "int32_t x = *__beg_0;" in cpp

    def test_emits_len_and_reaches_bounds_safe(self):
        # len -> ::tpy::__len__, hoisted into the range temp; the routed range(len)
        # loop makes the container subscript's bounds-safe branch live.
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::__len__(xs)" in cpp
        assert "xs[static_cast<std::size_t>(i)]" in cpp

    def test_mixed_foreach_range_counter_parity(self):
        # A for-each then a range-for in one function shares the per-function loop-index
        # counter; the numbering must stay in sync with the AST (for-each -> __obj_0,
        # the following range-for -> __stop_1).
        src = (
            _PRELUDE
            + "def f(items: list[Int32], n: Int32) -> Int32:\n    s = 0\n"
            + "    for x in items:\n        s = s + x\n"
            + "    for i in range(n):\n        s = s + i\n    return s\n"
            + "def main():\n    print(f([1, 2], 3))\nmain()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert "auto& __obj_0 = items;" in cpp and "__stop_1 = n;" in cpp

    def test_dump_for_each(self):
        thir = _lower(
            _PRELUDE
            + "def total(items: list[Int32]) -> Int32:\n    s = 0\n"
            + "    for x in items:\n        s = s + x\n    return s\n")
        assert "for %x in %items:" in dump_thir(thir)

    # A `list[record]` for-each: the loop var is a borrow alias (`const auto&` when
    # read-only, `auto&&` when the body mutates through it), field reads are `.field`.
    REC_SRC = (
        _F1_RECORDS
        + "def total(items: list[Inner]) -> Int32:\n    s = 0\n"
        + "    for p in items:\n        s = s + p.value\n    return s\n"
        + "def bump(items: list[Inner]) -> None:\n"
        + "    for p in items:\n        p.value = p.value + 1\n"
        + "def main():\n    xs = [Inner(1), Inner(2)]\n    bump(xs)\n    print(total(xs))\nmain()\n"
    )

    def test_record_byte_identical(self):
        assert self._cpp(self.REC_SRC, thir=True) == self._cpp(self.REC_SRC, thir=False)

    def test_record_const_loop_var_binding(self):
        # Read-only loop var -> `const auto&` (the const_loop_var thread; hardcoded False
        # would wrongly emit `auto&&` here). Field read is `.value` (dot, like a param).
        cpp = self._cpp(self.REC_SRC, thir=True)
        assert "const auto& p = *__beg_0;" in cpp
        assert "s = (::tpy::add_check<int32_t>(s, p.value));" in cpp

    def test_record_mutating_loop_var_binding(self):
        # Mutation through the loop var -> `auto&&` (a non-const alias); the write
        # `p.value = ...` goes through the reference, aliasing the list element.
        cpp = self._cpp(self.REC_SRC, thir=True)
        assert "auto&& p = *__beg_0;" in cpp

    def test_nested_record_for_each(self):
        # A `list[record]` loop nested inside another routes (both loops), and the
        # per-function loop-index counter stays in sync with the AST (__obj_0 outer,
        # __obj_1 inner). Asserting routing keeps the byte-identity check non-vacuous.
        src = (
            _F1_RECORDS
            + "def pair_sum(xs: list[Inner], ys: list[Inner]) -> Int32:\n    s = 0\n"
            + "    for a in xs:\n        for b in ys:\n"
            + "            s = s + a.value + b.value\n    return s\n"
            + "def main():\n    print(pair_sum([Inner(1)], [Inner(2)]))\nmain()\n")
        outer = _fn(_lower_ctx(src), "pair_sum").body[1]
        assert isinstance(outer, THIRForEach) and isinstance(outer.body[0], THIRForEach)
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert "auto& __obj_0 = xs;" in cpp and "auto& __obj_1 = ys;" in cpp

    def test_ctor_list_record_param_for_each(self):
        # Admitting `list[record]` params also broadens CONSTRUCTOR eligibility: a ctor
        # whose body iterates a `list[record]` param (the loop demotes into the ctor
        # tail) routes too. Non-vacuous: the ctor lowers (not None) + byte-identical.
        src = (
            _F1_RECORDS
            + "class Sum:\n    total: Int32\n"
            + "    def __init__(self, items: list[Inner]):\n        self.total = 0\n"
            + "        for it in items:\n            self.total = self.total + it.value\n"
            + "def main():\n    s = Sum([Inner(1), Inner(2)])\n    print(s.total)\nmain()\n")
        assert _lower_ctor(src, "Sum") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)



# --- Statement-shape axis: expression statements (print + bare eligible call) ---

class TestPrintStmt:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def test_str_literal_and_scalar_route(self):
        thir = _lower(
            _PRELUDE
            + "def f(n: Int32) -> None:\n    print(\"n =\", n)\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRPrint) and len(stmt.args) == 2
        assert isinstance(stmt.args[0].expr, THIRStrLiteral)
        assert stmt.args[0].print_form is PrintForm.RAW
        assert stmt.args[1].print_form is PrintForm.RAW

    def test_arg_forms(self):
        # bool -> BOOL, double -> FLOAT, 8-bit int -> INT8, wider int -> RAW.
        thir = _lower(
            _PRELUDE
            + "def f(ok: bool, r: float, b: UInt8, n: Int32) -> None:\n"
            + "    print(ok, r, b, n)\n")
        forms = [a.print_form for a in _fn(thir, "f").body[0].args]
        assert forms == [PrintForm.BOOL, PrintForm.FLOAT, PrintForm.INT8, PrintForm.RAW]

    def test_empty_print_routes(self):
        thir = _lower(_PRELUDE + "def f() -> None:\n    print()\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRPrint) and stmt.args == ()

    def test_bare_call_stmt_routes(self):
        # A same-module free-function call discarded for side effects (void return).
        thir = _lower(
            _PRELUDE
            + "def g(n: Int32) -> None:\n    print(n)\n"
            + "def f(n: Int32) -> None:\n    g(n)\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRExprStmt) and isinstance(stmt.expr, THIRCall)

    def test_scalar_returning_call_stmt_routes(self):
        # A discarded scalar return also routes as a bare statement.
        thir = _lower(
            _PRELUDE
            + "def g(n: Int32) -> Int32:\n    return n\n"
            + "def f(n: Int32) -> None:\n    g(n)\n")
        assert isinstance(_fn(thir, "f").body[0], THIRExprStmt)

    def test_str_var_arg_routes(self):
        # A str variable streams raw like the AST's is_any_str_type arm.
        thir = _lower(
            "from tpy import Int32\n"
            + "def f(s: str) -> None:\n    print(s)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        arg = fn.body[0].args[0]
        assert arg.print_form is PrintForm.RAW
        assert isinstance(arg.expr, THIRName) and arg.expr.form is Form.BORROW

    def test_bigint_arg_ineligible(self):
        # A plain `int` is BigInt, not an eligible fixed-int scalar -> AST path.
        thir = _lower("def f(x: int) -> None:\n    print(x)\n")
        assert _fn(thir, "f") is None

    def test_kwargs_ineligible(self):
        # sep=/end=/file=/flush= take gen_print's richer path -> AST.
        thir = _lower(
            _PRELUDE
            + "def f(n: Int32) -> None:\n    print(n, end=\"\")\n")
        assert _fn(thir, "f") is None

    def test_container_arg_ineligible(self):
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> None:\n    print(xs)\n")
        assert _fn(thir, "f") is None

    def test_shadowed_print_ineligible(self):
        # A user function named `print` is not the builtin; conservatively stays AST
        # (never misrouted to the stream emit).
        thir = _lower(
            _PRELUDE
            + "def print(n: Int32) -> None:\n    return\n"
            + "def f(n: Int32) -> None:\n    print(n)\n")
        assert _fn(thir, "f") is None

    def test_byte_identical(self):
        src = (
            _PRELUDE
            + "def report(n: Int32, ok: bool, r: float, b: UInt8) -> None:\n"
            + "    print(\"n =\", n)\n    print(n, ok, r)\n    print(b)\n    print()\n"
            + "    blank()\n"
            + "def blank() -> None:\n    print(\"--\")\n"
            + "def main():\n    report(3, True, 1.5, 7)\nmain()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert 'std::cout << "n =" << " " << n << "\\n";' in cpp
        assert 'std::cout << n << " " << ::tpy::print_bool(ok) << " " << ::tpy::print_float(r) << "\\n";' in cpp
        assert 'std::cout << static_cast<int>(b) << "\\n";' in cpp
        assert 'std::cout << "\\n";' in cpp
        assert "blank();" in cpp

    def test_dump(self):
        thir = _lower(_PRELUDE + "def f(n: Int32) -> None:\n    print(\"x\", n)\n")
        assert "print(str('x') [raw], %n [raw])" in dump_thir(thir)

    def test_native_call_arg_ineligible(self):
        # A @native function is ::-qualified at the call site; the bare THIRCall
        # emit can't reproduce that, so a print with a native-call arg stays AST.
        thir = _lower(
            "from tpy.extern import native\nfrom tpy import Int32\n"
            + "@native\ndef ext() -> Int32: ...\n"
            + "def f() -> None:\n    print(ext())\n")
        assert _fn(thir, "f") is None

    def test_native_bare_call_ineligible(self):
        thir = _lower(
            "from tpy.extern import native\n"
            + "@native\ndef ext() -> None: ...\n"
            + "def f() -> None:\n    ext()\n")
        assert _fn(thir, "f") is None

    def test_export_c_call_ineligible(self):
        # An @export(binding="C") function has EXPORT_C linkage and emits its raw
        # extern-C symbol at the call site (not a bare name); the fi.linkage gate
        # keeps a caller of it on the AST path. Unlike a plain @native (caught by
        # the native_function/native_name check), EXPORT_C has a body and only the
        # linkage gate excludes it.
        thir = _lower(
            "from tpy.extern import export\nfrom tpy import Int32\n"
            + "@export(binding=\"C\")\ndef ext(x: Int32) -> Int32:\n    return x\n"
            + "def f(n: Int32) -> Int32:\n    return ext(n)\n")
        assert _fn(thir, "f") is None

    def test_desugar_shares_one_source_comment(self):
        # A tuple-unpack desugars to several assigns on one source line; only the
        # first carries the source comment (no_source_comment set on the rest), so
        # the emitter doesn't repeat it -- byte-identical to the AST path.
        thir = _lower(
            _PRELUDE
            + "def f(p: Int32, q: Int32) -> Int32:\n    a = p\n    b = q\n"
            + "    a, b = b, a\n    return a + b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert sum(1 for s in fn.body if s.no_source_comment) == 3



class TestFloat:
    def test_float_param_return_local_eligible(self):
        thir = _lower("def f(a: float, b: float) -> float:\n    c = a + b\n    return c\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.params[0].type.to_cpp() == "double"
        assert isinstance(fn.body[0], THIRVarDecl)
        assert fn.body[0].resolved_type.to_cpp() == "double"

    def test_float_arith_and_literal_eligible(self):
        thir = _lower("def f(a: float) -> float:\n    return a * 2.0 - 1.5\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].value, THIRBinOp) and fn.body[0].value.op == "-"

    def test_float_literal_local_eligible(self):
        thir = _lower("def f() -> float:\n    x = 2.5\n    return x\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.body[0].resolved_type.to_cpp() == "double"

    def test_float_comparison_condition_eligible(self):
        thir = _lower("def f(a: float, b: float) -> float:\n    r = a\n"
                      "    if a < b:\n        r = b\n    return r\n")
        assert _fn(thir, "f") is not None

    def test_float_comparison_as_value_eligible(self):
        # _binop_eligible admits comparisons for any eligible scalar incl. float.
        thir = _lower("def f(a: float, b: float) -> bool:\n    r = a < b\n    return r\n")
        assert _fn(thir, "f") is not None

    def test_float_truediv_is_ineligible(self):
        # `/` (true division) has AST op `div`, absent from _ARITH_OPS (which
        # lists `/`, a token the parser never emits), so _binop_eligible rejects
        # it -- AST path. (truediv DOES carry a `::tpy::truediv` cpp_template;
        # contrast `//`, op `//`, which is eligible. See TODO re: enabling it.)
        thir = _lower("def f(a: float, b: float) -> float:\n    return a / b\n")
        assert _fn(thir, "f") is None

    def test_float32_is_ineligible(self):
        # Float32 literals need a `f` suffix the slice does not emit, so the
        # whole Float32 family stays on the AST path.
        thir = _lower("from tpy import Float32\n"
                      "def f(a: Float32) -> Float32:\n    b = a\n    return b\n")
        assert _fn(thir, "f") is None



class TestBool:
    def test_bool_param_return_local_eligible(self):
        thir = _lower("def f(a: bool) -> bool:\n    b = a\n    return b\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.params[0].type.to_cpp() == "bool"

    def test_comparison_as_value_eligible(self):
        # A comparison used as a value (`r = x < y`), not just an if-condition.
        thir = _lower(_PRELUDE + "def f(x: Int32, y: Int32) -> bool:\n"
                      "    r = x < y\n    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0], THIRVarDecl)
        assert fn.body[0].resolved_type.to_cpp() == "bool"
        assert isinstance(fn.body[0].init, THIRBinOp) and fn.body[0].init.op == "<"

    def test_mixed_sign_comparison_is_ineligible(self):
        # A signed-vs-unsigned comparison emits std::cmp_* (not a bare operator),
        # so it stays on the AST path -- as a value and as a condition.
        thir = _lower(_PRELUDE + "from tpy import UInt32\n"
                      "def f(a: Int32, b: UInt32) -> bool:\n    return a < b\n")
        assert _fn(thir, "f") is None

    def test_retro_widened_seed_compare_routes(self):
        # `offset = 0` retro-widens to UInt64 from the take_u64 arg slot; the
        # `offset < limit` compare is then same-sign and must NOT be excluded by
        # the mixed-sign gate (regression guard for the resolved-local-type
        # tracking -- analyzer.get_expr_type(offset) is the signed seed).
        thir = _lower("from tpy import UInt64\n"
                      "def take_u64(x: UInt64) -> UInt64:\n    return x\n"
                      "def f(limit: UInt64) -> UInt64:\n    offset = 0\n"
                      "    while offset < limit:\n        offset = take_u64(offset) + 1\n"
                      "    return offset\n")
        assert _fn(thir, "f") is not None

    def test_bool_literal_eligible(self):
        thir = _lower("def f() -> bool:\n    ok = True\n    return ok\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].init, THIRLiteral) and fn.body[0].init.value is True

    def test_comparison_as_call_arg_eligible(self):
        # comparison-as-value reaching a same-module call arg: g(x < y).
        thir = _lower(_PRELUDE
                      + "def g(b: bool) -> bool:\n    return b\n"
                      + "def f(x: Int32, y: Int32) -> bool:\n    return g(x < y)\n")
        assert _fn(thir, "f") is not None

    def test_bare_bool_condition_eligible(self):
        thir = _lower(_PRELUDE + "def f(x: Int32, done: bool) -> Int32:\n    r = x\n"
                      "    if done:\n        r = 0\n    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None and isinstance(fn.body[1], THIRIf)
        assert isinstance(fn.body[1].condition, THIRName)
        assert fn.body[1].condition.name == "done"

    def test_bare_bool_while_condition_eligible(self):
        thir = _lower(_PRELUDE + "def f(go: bool) -> Int32:\n    r = 0\n"
                      "    while go:\n        r = r + 1\n    return r\n")
        fn = _fn(thir, "f")
        assert fn is not None and isinstance(fn.body[1], THIRWhile)
        assert isinstance(fn.body[1].condition, THIRName)
        assert fn.body[1].condition.name == "go"

    def test_bool_literal_condition_is_ineligible(self):
        # `if True:` is excluded -- the AST path may dead-branch-eliminate a
        # bool-literal condition, which a bare `if (true)` would not reproduce.
        thir = _lower("def f(a: bool) -> bool:\n    r = a\n"
                      "    if True:\n        r = a\n    return r\n")
        assert _fn(thir, "f") is None



class TestBoolOps:
    def test_logical_and_routes(self):
        # Bool-result `and` over bool operands: the bare-operator emit arm.
        thir = _lower("def f(a: bool, b: bool) -> bool:\n    return a and b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        v = fn.body[0].value
        assert isinstance(v, THIRBinOp) and v.op == "&&" and v.resolved is None

    def test_logical_or_routes(self):
        # `or` alongside `and` -- a separate guard so a future edit gating one
        # operator can't silently drop the other.
        thir = _lower("def f(a: bool, b: bool) -> bool:\n    return a or b\n")
        fn = _fn(thir, "f")
        assert fn is not None and fn.body[0].value.op == "||"

    def test_not_routes(self):
        thir = _lower("def f(a: bool) -> bool:\n    return not a\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].value, THIRUnaryNot)

    def test_and_or_not_condition_routes(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, flag: bool) -> Int32:\n"
                      "    if a < b and flag:\n        return 1\n"
                      "    while not flag or a == b:\n        a = a + 1\n"
                      "    return a\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0], THIRIf) and fn.body[0].condition.op == "&&"
        assert isinstance(fn.body[1], THIRWhile) and fn.body[1].condition.op == "||"
        assert isinstance(fn.body[1].condition.left, THIRUnaryNot)

    def test_value_semantics_or_is_ineligible(self):
        # `n or 5` (non-bool result) takes _gen_logical_value's Python operand
        # semantics (temp + ternary `(n ? n : 5)`), not the bare operator.
        thir = _lower(_PRELUDE + "def f(n: Int32) -> Int32:\n    return n or 5\n")
        assert _fn(thir, "f") is None

    def test_non_bool_operand_is_ineligible(self):
        # An int operand under a bool result (`flag and n`) renders through
        # truthiness reasoning the slice does not carry -- stays on the AST path.
        thir = _lower(_PRELUDE
                      + "def f(flag: bool, n: Int32) -> bool:\n    return flag and n\n")
        assert _fn(thir, "f") is None

    def test_int_truthiness_not_is_ineligible(self):
        # `not n` (int operand) is bool-result but truthy-wraps the operand.
        thir = _lower(_PRELUDE + "def f(n: Int32) -> bool:\n    return not n\n")
        assert _fn(thir, "f") is None

    def test_unary_minus_is_ineligible(self):
        # The arithmetic unaries take the resolved_unaryop emit path.
        thir = _lower(_PRELUDE + "def f(n: Int32) -> Int32:\n    m = n\n    return -m\n")
        assert _fn(thir, "f") is None

    def test_mixed_sign_compare_operand_is_ineligible(self):
        # The mixed-sign gate applies inside a logical operand too (the pair
        # would emit std::cmp_*, not the bare operator).
        thir = _lower(_PRELUDE + "from tpy import UInt32\n"
                      "def f(a: Int32, b: UInt32, flag: bool) -> bool:\n"
                      "    return flag and a < b\n")
        assert _fn(thir, "f") is None

    def test_record_operand_compare_is_ineligible(self):
        # A record compare also reaches the rb=None bare-operator arm (a user
        # dunder has no template), but its operands need gen_expr_deref's
        # indirection -- `self` renders `(*this)`. The @total_ordering-
        # synthesized `not (self <= other)` bodies pinned this divergence.
        thir = _lower_ctx(
            _PRELUDE
            + "class C:\n"
            + "    n: Int32\n"
            + "    def __init__(self, n: Int32):\n        self.n = n\n"
            + "    def __le__(self, other: C) -> bool:\n"
            + "        return self.n <= other.n\n"
            + "    def gt(self, other: C) -> bool:\n"
            + "        return not (self <= other)\n")
        assert _fn(thir, "gt") is None



class TestBoolOpsEmit:
    def test_emit_and_or_not(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, flag: bool) -> bool:\n"
                      "    x = flag and not (a < b)\n    return x or flag\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    bool x = (flag && (!((a < b))));\n"
            "    return (x || flag);\n")

    def test_emit_not_nested_and_double_not(self):
        thir = _lower("def f(a: bool, b: bool) -> bool:\n"
                      "    x = not (a and b)\n    return not not x\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    bool x = (!((a && b)));\n"
            "    return (!((!(x))));\n")

    def test_emit_condition_matches_value_render(self):
        # Condition position reuses the value render (`gen_truthy_expr` reduces
        # to it for the admitted bool shapes).
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, flag: bool) -> Int32:\n"
                      "    if a < b and flag:\n        return 1\n"
                      "    while not flag:\n        a = a + 1\n"
                      "    return a\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    if (((a < b) && flag)) {\n"
            "        return 1;\n"
            "    }\n"
            "    while ((!(flag))) {\n"
            "        a = (::tpy::add_check<int32_t>(a, 1));\n"
            "    }\n"
            "    return a;\n")

    def test_emit_bool_print_arg(self):
        thir = _lower("def f(x: bool, y: bool):\n    print(x and y, not x)\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    std::cout << ::tpy::print_bool((x && y)) << \" \" "
            "<< ::tpy::print_bool((!(x))) << \"\\n\";\n")



class TestChainedCompare:
    def test_simple_chain_routes(self):
        # Simple (name) intermediate -> the inline arm: a left-folded && of the
        # sema pairs.
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32) -> bool:\n"
                      "    return a < b < c\n")
        fn = _fn(thir, "f")
        assert fn is not None
        v = fn.body[0].value
        assert isinstance(v, THIRBinOp) and v.op == "&&" and v.resolved is None
        assert v.left.op == "<" and v.right.op == "<"

    def test_four_operand_chain_routes(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32, d: Int32) -> bool:\n"
                      "    return a < b <= c < d\n")
        fn = _fn(thir, "f")
        assert fn is not None
        v = fn.body[0].value          # ((p0 && p1) && p2)
        assert v.op == "&&" and v.left.op == "&&" and v.right.op == "<"

    def test_complex_endpoints_route(self):
        # Endpoints may be non-simple (evaluated once); only INTERMEDIATES
        # trigger the statement-expr arm.
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32) -> bool:\n"
                      "    return a + 1 < b < c + 2\n")
        assert _fn(thir, "f") is not None

    def test_complex_intermediate_is_ineligible(self):
        # A non-simple intermediate (`b + 1`) binds a `_cmp1` temp inside a GCC
        # statement expression (`({ auto&& _cmp1 = ...; ... })`) -> AST path.
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32) -> bool:\n"
                      "    return a < b + 1 < c\n")
        assert _fn(thir, "f") is None

    def test_call_intermediate_is_ineligible(self):
        # A call intermediate (`len(xs)`) is non-simple -> statement-expr arm.
        thir = _lower(_PRELUDE + "def f(a: Int32, c: Int32) -> bool:\n"
                      "    xs = [1, 2, 3]\n    return a < len(xs) < c\n")
        assert _fn(thir, "f") is None

    def test_mixed_sign_pair_is_ineligible(self):
        # Each pair gets the single-comparison gates (mixed-sign -> std::cmp_*).
        thir = _lower(_PRELUDE + "from tpy import UInt32\n"
                      "def f(a: Int32, b: UInt32, c: UInt32) -> bool:\n"
                      "    return a < b < c\n")
        assert _fn(thir, "f") is None

    def test_chain_condition_routes(self):
        thir = _lower(_PRELUDE + "def f(i: Int32, n: Int32) -> Int32:\n"
                      "    if 0 <= i < n:\n        return i\n    return 0\n")
        fn = _fn(thir, "f")
        assert fn is not None and isinstance(fn.body[0], THIRIf)
        assert fn.body[0].condition.op == "&&"



class TestChainedCompareEmit:
    def test_emit_inline_chain(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32) -> bool:\n"
                      "    return a < b < c\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return ((a < b) && (b < c));\n"

    def test_emit_four_operand_chain(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32, c: Int32, d: Int32) -> bool:\n"
                      "    return a < b < c < d\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (((a < b) && (b < c)) && (c < d));\n"

    def test_emit_chain_condition(self):
        thir = _lower(_PRELUDE + "def f(i: Int32, n: Int32) -> Int32:\n"
                      "    if 0 <= i < n:\n        return i\n    return 0\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    if (((0 <= i) && (i < n))) {\n"
            "        return i;\n"
            "    }\n"
            "    return 0;\n")



class TestDump:
    def test_dump_format(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    b = a\n    return b\n")
        assert dump_thir(thir) == (
            "fn f(a: Int32) -> Int32:\n"
            "  %b: Int32 = %a\n"
            "  return %b\n"
        )

    def test_dump_empty(self):
        # `int` is BigInt -- not an eligible scalar, so nothing routes.
        thir = _lower("def f(a: int) -> int:\n    return a\n")
        assert "(no THIR-eligible functions)" in dump_thir(thir)

    def test_dump_for_range(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    total = 0\n"
                      + "    for i in range(n):\n        total = total + i\n    return total\n")
        assert dump_thir(thir) == (
            "fn f(n: Int32) -> Int32:\n"
            "  %total: Int32 = lit(0)\n"
            "  for %i in range(0, %n):\n"
            "    %total = binop(%total, +, %i)\n"
            "  return %total\n"
        )

    def test_dump_storage_none(self):
        # An F2c None return surfaces the STORAGE form tag on the None literal
        # (the tag selects std::nullopt vs nullptr at emit).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def nothing() -> Own[Inner] | None:\n    return None\n")
        assert "return lit(None) [storage]" in dump_thir(thir)



class TestEmit:
    def test_emit_body_no_comments(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    b = a\n    return b\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    int32_t b = a;\n    return b;\n"

    def test_emit_binop_overflow_checked(self):
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32) -> Int32:\n    return a + b + 1\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    return (::tpy::add_check<int32_t>"
            "((::tpy::add_check<int32_t>(a, b)), 1));\n")

    def test_emit_binop_div_floor_when_divisor_nonzero(self):
        thir = _lower(_PRELUDE + "def f(a: Int32) -> Int32:\n    return a // 2\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (::tpy::div_floor<int32_t>(a, 2));\n"

    def test_emit_binop_div_check_with_runtime_divisor(self):
        # A non-literal divisor is not proven non-zero -> checked div helper.
        thir = _lower(_PRELUDE + "def f(a: Int32, b: Int32) -> Int32:\n    return a // b\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (::tpy::div_check<int32_t>(a, b));\n"

    def test_emit_call_with_args(self):
        thir = _lower(_PRELUDE
                      + "def g(a: Int32, b: Int32) -> Int32:\n    return a + b\n"
                      + "def f(a: Int32) -> Int32:\n    return g(a, a + 1)\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return g(a, (::tpy::add_check<int32_t>(a, 1)));\n"

    def test_emit_while(self):
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    i = 0\n"
                      + "    while i < n:\n        i = i + 1\n    return i\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t i = 0;\n"
            "    while ((i < n)) {\n"
            "        i = (::tpy::add_check<int32_t>(i, 1));\n"
            "    }\n"
            "    return i;\n"
        )

    def test_emit_range_stop(self):
        # range(name): bound captured once into __stop_0, then C-style for.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    total = 0\n"
                      + "    for i in range(n):\n        total = total + i\n    return total\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t total = 0;\n"
            "    int32_t __stop_0 = n;\n"
            "    for (int32_t i = 0; i < __stop_0; ++i) {\n"
            "        total = (::tpy::add_check<int32_t>(total, i));\n"
            "    }\n"
            "    return total;\n"
        )

    def test_emit_range_literal_inlined(self):
        thir = _lower(_PRELUDE
                      + "def f() -> Int32:\n    total = 0\n"
                      + "    for i in range(10):\n        total = total + i\n    return total\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t total = 0;\n"
            "    for (int32_t i = 0; i < 10; ++i) {\n"
            "        total = (::tpy::add_check<int32_t>(total, i));\n"
            "    }\n"
            "    return total;\n"
        )

    def test_emit_comparison_as_value(self):
        thir = _lower(_PRELUDE + "def f(x: Int32, y: Int32) -> bool:\n"
                      "    r = x < y\n    return r\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    bool r = (x < y);\n"
            "    return r;\n"
        )

    def test_emit_comparison_in_return(self):
        thir = _lower(_PRELUDE + "def f(x: Int32, y: Int32) -> bool:\n    return x < y\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (x < y);\n"

    def test_emit_derived_comparison(self):
        # `!=` has no resolved_binop -> the bare C++ operator branch of _emit_binop.
        thir = _lower(_PRELUDE + "def f(x: Int32, y: Int32) -> bool:\n    return x != y\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (x != y);\n"

    def test_emit_bare_bool_condition(self):
        thir = _lower(_PRELUDE + "def f(x: Int32, done: bool) -> Int32:\n    r = x\n"
                      "    if done:\n        r = 0\n    return r\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t r = x;\n"
            "    if (done) {\n"
            "        r = 0;\n"
            "    }\n"
            "    return r;\n"
        )

    def test_emit_bool_literal(self):
        thir = _lower("def f() -> bool:\n    ok = True\n    return ok\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    bool ok = true;\n"
            "    return ok;\n"
        )

    def test_emit_bare_bool_while(self):
        thir = _lower(_PRELUDE + "def f(go: bool) -> Int32:\n    r = 0\n"
                      "    while go:\n        r = r + 1\n    return r\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t r = 0;\n"
            "    while (go) {\n"
            "        r = (::tpy::add_check<int32_t>(r, 1));\n"
            "    }\n"
            "    return r;\n"
        )

    def test_emit_float_mul_literal(self):
        # Float arithmetic flows through the same templated binop path as int;
        # the float literal renders as repr(value) in a double slot.
        thir = _lower("def f(a: float) -> float:\n    return a * 2.0\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return ((a) * (2.0));\n"

    def test_emit_float_floordiv(self):
        thir = _lower("def f(a: float, b: float) -> float:\n    return a // b\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == "    return (::tpy::floordiv(a, b));\n"

    def test_emit_range_literal_start_name_stop(self):
        # range(literal, name): literal start inlined (no __start_N), name stop
        # hoisted to __stop_0 -- the start_is_literal=True / stop_is_literal=False path.
        thir = _lower(_PRELUDE
                      + "def f(n: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(2, n):\n        acc = acc + i\n    return acc\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t acc = 0;\n"
            "    int32_t __stop_0 = n;\n"
            "    for (int32_t i = 2; i < __stop_0; ++i) {\n"
            "        acc = (::tpy::add_check<int32_t>(acc, i));\n"
            "    }\n"
            "    return acc;\n"
        )

    def test_emit_nested_range_counter_parity(self):
        # The hidden-temp index must reproduce ctx.iter_counter: the outer loop
        # takes index 0 (__start_0/__stop_0) BEFORE its body, so the nested loop
        # takes index 1 (__stop_1) -- pre-order, matching _gen_range_counter_loop.
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n    acc = 0\n"
                      + "    for i in range(a, b):\n        for j in range(b):\n"
                      + "            acc = acc + j\n    return acc\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t acc = 0;\n"
            "    int32_t __start_0 = a;\n"
            "    int32_t __stop_0 = b;\n"
            "    for (int32_t i = __start_0; i < __stop_0; ++i) {\n"
            "        int32_t __stop_1 = b;\n"
            "        for (int32_t j = 0; j < __stop_1; ++j) {\n"
            "            acc = (::tpy::add_check<int32_t>(acc, j));\n"
            "        }\n"
            "    }\n"
            "    return acc;\n"
        )

    def test_emit_if_else(self):
        thir = _lower(_PRELUDE
                      + "def f(a: Int32, b: Int32) -> Int32:\n"
                      + "    r = a\n"
                      + "    if a < b:\n        r = b\n    else:\n        r = a\n"
                      + "    return r\n")
        buf = io.StringIO()
        emit_thir_body(buf, _fn(thir, "f"))
        assert buf.getvalue() == (
            "    int32_t r = a;\n"
            "    if ((a < b)) {\n"
            "        r = b;\n"
            "    } else {\n"
            "        r = a;\n"
            "    }\n"
            "    return r;\n"
        )



class TestByteIdentical:
    """The load-bearing contract: THIR codegen == AST codegen for the slice,
    over a module that mixes routed (eligible) and AST-path (ineligible) funcs."""

    SRC = (
        _PRELUDE
        + "def passthru(a: Int32) -> Int32:\n    b = a\n    return b\n"
        + "def widen(p: UInt8) -> UInt8:\n    q = p\n    return q\n"
        + "def calc(a: Int32, b: Int32) -> Int32:\n    c = a * b + 1\n    return c // 2\n"
        + "def combo(a: Int32) -> Int32:\n    return calc(a, passthru(a)) + passthru(a + 1)\n"
        + "def clamp(x: Int32, lo: Int32, hi: Int32) -> Int32:\n"
        + "    r = x\n    if x < lo:\n        r = lo\n    elif x > hi:\n        r = hi\n    return r\n"
        + "def sum_to(n: Int32) -> Int32:\n    total = 0\n    i = 0\n"
        + "    while i < n:\n        total = total + i\n        i = i + 1\n    return total\n"
        + "def triangle(n: Int32) -> Int32:\n    total = 0\n"
        + "    for i in range(n):\n        total = total + i\n    return total\n"
        + "def matrix_sum(r: Int32, c: Int32) -> Int32:\n    acc = 0\n"
        + "    for i in range(r):\n        for j in range(0, c):\n            acc = acc + i\n    return acc\n"
        + "def scale(x: float, k: float) -> float:\n    r = x * k - 1.5\n    return r\n"
        + "def is_lt(a: Int32, b: Int32) -> bool:\n    return a < b\n"
        + "def check(a: Int32, b: Int32, flag: bool) -> bool:\n    ok = a < b\n"
        + "    if flag:\n        ok = a == b\n    return ok\n"
        + "def main():\n    print(clamp(sum_to(combo(7)), 0, 50) + triangle(5) + matrix_sum(3, 4))\n"
        + "    print(scale(2.0, 3.0))\n    print(is_lt(1, 2))\n    print(check(1, 2, True))\n\nmain()\n"
    )

    def test_entry_cpp_byte_identical(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        ast = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True))
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=True, thir_codegen=True))
        assert thir == ast
