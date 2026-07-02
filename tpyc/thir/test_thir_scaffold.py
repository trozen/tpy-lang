"""THIR value-scalar slice: lowering eligibility/shape, dump, and the
byte-identical emit contract (THIR codegen == AST codegen for the slice)."""

from __future__ import annotations

import dataclasses
import io

from .. import get_lib_dir
from ..codegen_cpp.context import CodeGenOptions
from ..compiler import Compiler
from .dump import dump_thir
from .emit import _emit_expr, emit_thir_body, emit_thir_constructor_tail
from .lower import _is_len_native, lower_module
from ..parse.nodes import TpyCall
from ..codegen_cpp.forms import LocalBinding
from .nodes import (
    Form, PrintForm, THIRAssign, THIRBinOp, THIRBytesLiteral, THIRCall,
    THIRCharLiteral,
    THIRCoerce, THIRContainerLiteral,
    THIRExprStmt, THIRFieldAccess, THIRForEach, THIRForRange,
    THIRFormConvert, THIRFString, THIRFStringArg, THIRIf, THIRLiteral,
    THIRMethodCall, THIRName, THIRPrint, THIRReturn, THIRSelf, THIRStrAppend,
    THIRStrLiteral, THIRStrSlice, THIRSubscript, THIRUnaryNot, THIRVarDecl,
    THIRWhile,
)

_STDLIB_DIRS = [get_lib_dir() / "tpy"]


def _compile(source: str):
    compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
    return compiler, compiler.compile()


def _entry(modules):
    return [m for m in modules if m.is_entry_point][0]


def _lower(source: str):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    return lower_module(entry.ast, entry.analyzer)


def _lower_ctx(source: str):
    """Lower inside the compiler context -- required once non-value records are
    involved: `NominalType.is_user_record` / `.to_cpp()` resolve through the
    active Compiler (the registry / native-name maps), unlike the value-scalar
    types `_lower` covers."""
    from ..compilation_context import activate_compiler
    compiler, modules = _compile(source)
    entry = _entry(modules)
    with activate_compiler(compiler):
        return lower_module(entry.ast, entry.analyzer)


def _fn(thir, name):
    return next((f for f in thir.functions if f.name == name), None)


def _lower_ctor(source: str, record_name: str):
    """Lower one record's constructor to its THIRConstructor (or None if outside
    the M3 slice). Within the compiler context -- records resolve through the live
    registry / native-name maps, like `_lower_ctx`."""
    from ..compilation_context import activate_compiler
    from .lower import iter_module_constructors, lower_constructor
    compiler, modules = _compile(source)
    entry = _entry(modules)
    with activate_compiler(compiler):
        for rec, init, self_type in iter_module_constructors(entry.ast, entry.analyzer):
            if rec.name == record_name:
                return lower_constructor(rec, init, entry.analyzer,
                                         self_type=self_type)
    return None


def _ctor_tail(ctor) -> str:
    buf = io.StringIO()
    emit_thir_constructor_tail(buf, ctor)
    return buf.getvalue()


_PRELUDE = "from tpy import Int32, UInt8, UInt64\n"

# Shared F1-record fixture (records need `_lower_ctx` / a full compile -- see its
# docstring). Defined here so class-body-level source builders can reference it.
_F1_RECORDS = (
    "from tpy import Int32, Own, readonly\n"
    "class Leaf:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "class Inner:\n"
    "    value: Int32\n"
    "    opt: Leaf | None\n"
    "    def __init__(self, value: Int32):\n        self.value = value\n        self.opt = None\n"
    "class Box:\n"
    "    inner: Inner\n"
    "    opt: Inner | None\n"
    "    n: Int32\n"
    "    def __init__(self, inner: Own[Inner]):\n"
    "        self.inner = inner\n        self.opt = None\n        self.n = 0\n"
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

    def test_staticmethod_is_ineligible(self):
        # The method frontier (M1) admits instance methods only; a staticmethod
        # has no `self` receiver and takes a different emit path.
        thir = _lower_ctx(
            _PRELUDE
            + "class C:\n    @staticmethod\n    def m(a: Int32) -> Int32:\n        b = a\n        return b\n")
        assert _fn(thir, "m") is None

    def test_generic_is_ineligible(self):
        thir = _lower("def f[T](a: T) -> T:\n    b = a\n    return b\n")
        assert _fn(thir, "f") is None


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


# --- F1 form rung: single-assignment non-value record locals + field reads ---

class TestF1Eligibility:
    def test_ref_alias_local_eligible(self):
        # x = b.inner -- a plain record field read binds a single-assignment T&
        # alias (REF_ALIAS); x.value is a scalar field read off it.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    x = b.inner\n    return x.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS
        assert decl.form is Form.BORROW
        assert decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRFieldAccess)
        assert decl.init.field_cpp == "inner" and not decl.init.is_arrow
        assert decl.init.form is Form.STORAGE
        # the scalar field read off the REF_ALIAS local
        read = fn.body[1].value
        assert isinstance(read, THIRFieldAccess) and read.field_cpp == "value"
        assert read.form is Form.VALUE

    def test_optional_to_ptr_local_eligible(self):
        # p = b.opt -- a storage-form Optional[record] field read lifts to a
        # borrow T* via optional_to_ptr (OPTIONAL_TO_PTR); const because b is a
        # const-ref param.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    p = b.opt\n    return 0\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const
        assert decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRFormConvert)
        assert decl.init.form is Form.BORROW
        assert isinstance(decl.init.value, THIRFieldAccess)
        assert decl.init.value.form is Form.STORAGE

    def test_mixed_mutation_free_function_keys_on_param_index(self):
        # A free function mutates b but only reads a.opt; the const verdict must
        # key on a's param index (0), not b's (1) -- exercises the index-based
        # _param_is_const(record_name=None) lookup that a single-param fn never does.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def mix(a: Box, b: Box) -> Int32:\n"
            + "    b.n = 1\n    p = a.opt\n    return 0\n")
        decl = next(s for s in _fn(thir, "mix").body
                    if isinstance(s, THIRVarDecl)
                    and s.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR)
        assert decl.form is Form.BORROW and decl.is_const

    def test_scalar_field_read_off_param_eligible(self):
        # The working, common F1 pattern: scalar field reads off a record param
        # (value form, no borrow local). `return p.x + p.y` and `a = p.x`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    a = b.n\n    return a\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        # a scalar local; the field read is a plain value-form field access
        assert decl.cpp_local_representation is None
        assert isinstance(decl.init, THIRFieldAccess)
        assert decl.init.form is Form.VALUE and decl.init.field_cpp == "n"

    def test_method_routes_via_self_receiver(self):
        # The method frontier (M1): a method's `self.field` reads route the same
        # as a record param's. `x = self.b` binds a REF_ALIAS off the `this`
        # receiver; `x.n` is a scalar read off it.
        thir = _lower_ctx(
            _F1_RECORDS
            + "class Wrap:\n    b: Box\n"
            + "    def __init__(self, b: Own[Box]):\n        self.b = b\n"
            + "    def get(self) -> Int32:\n        x = self.b\n        return x.n\n")
        fn = _fn(thir, "get")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS
        # the field source reads `self->b` (the `this` pointer renders `->`)
        assert isinstance(decl.init, THIRFieldAccess)
        assert isinstance(decl.init.receiver, THIRSelf) and decl.init.is_arrow

    def test_reassigned_nonvalue_local_routes_as_pointer(self):
        # F2: a reassigned plain-record local with lvalue field sources is a
        # reseatable `T*` pointer-local (POINTER), no longer AST-only.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box) -> Int32:\n"
            + "    x = b.inner\n    x = c.inner\n    return x.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.POINTER
        assert decl.form is Form.BORROW and decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRFormConvert)  # &(b.inner)
        assert decl.init.form is Form.BORROW
        assert isinstance(decl.init.value, THIRFieldAccess)
        assert decl.init.value.form is Form.STORAGE

    def test_call_passing_record_arg_is_ineligible(self):
        # Passing an Own[record] / record param positionally crosses an ownership
        # boundary (an Own param auto-moves at last use: `consume(std::move(p))`),
        # which the bare-name THIRCall emit does not reproduce -- so the caller
        # stays on the AST path even though the callee is a plain free function.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def consume(p: Own[Inner]) -> Int32:\n        return p.value\n"
            + "def forward(b: Box) -> Int32:\n        return consume(b.inner)\n")
        # consume itself (Own[record] param + scalar field read) is eligible;
        # forward (passes a record arg) is not.
        assert _fn(thir, "consume") is not None
        assert _fn(thir, "forward") is None

    def test_method_call_on_record_is_ineligible(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Int32:\n    x = b.inner\n    return x.value + b.n\n")
        # b.n is a scalar field read (fine); but a method call would not be. Use
        # one with a method call to confirm rejection.
        thir2 = _lower_ctx(
            _F1_RECORDS
            + "class Counter:\n    k: Int32\n"
            + "    def __init__(self):\n        self.k = 0\n"
            + "    def bump(self) -> Int32:\n        self.k = self.k + 1\n        return self.k\n"
            + "def g(c: Counter) -> Int32:\n    return c.bump()\n")
        assert _fn(thir2, "g") is None
        # The pure field-read function is eligible.
        assert _fn(thir, "f") is not None


class TestF1Emit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def read_ref(b: Box) -> Int32:\n    x = b.inner\n    return x.value\n"
        + "def read_opt(b: Box) -> Int32:\n    p = b.opt\n    return 0\n"
        + "def scalar(b: Box) -> Int32:\n    a = b.n\n    return a + b.n\n"
        + "def chain(b: Box) -> Int32:\n    x = b.inner\n    y = x.value\n    return y\n"
        # const path: a readonly receiver makes the REF_ALIAS a `const Inner&`,
        # and the chained Optional read off that const local a `const Inner*`
        # (exercises both _f1_is_const branches: the ReadonlyType read and the
        # const-propagation through a const F1 local).
        + "def ro_chain(b: readonly[Box]) -> Int32:\n"
        + "    x = b.inner\n    p = x.opt\n    return x.value\n"
        + "def main():\n"
        + "    box = Box(Inner(3))\n"
        + "    print(read_ref(box) + read_opt(box) + scalar(box) + chain(box) + ro_chain(box))\n"
        + "main()\n"
    )

    def test_f1_byte_identical(self):
        # The load-bearing F1 contract: every routed form (REF_ALIAS,
        # OPTIONAL_TO_PTR, scalar field reads) emits identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_ref_alias_emits_reference(self):
        # T& alias of the field storage (non-const here: the field is a plain
        # record off the param -- sema does not mark the read readonly).
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner& x = b.inner;" in cpp
        assert "return x.value;" in cpp

    def test_optional_to_ptr_emits_lift(self):
        # const because the receiver param is const (an F1 body cannot mutate it).
        cpp = self._cpp(self.SRC, thir=True)
        assert "const Inner* p = ::tpy::optional_to_ptr(b.opt);" in cpp

    def test_scalar_field_read_emits(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "int32_t a = b.n;" in cpp        # scalar field read into a local
        assert cpp.count("b.n") >= 2            # the read + the return operand

    def test_const_borrow_local_forms(self):
        # A readonly receiver yields a `const Inner&` REF_ALIAS, and the chained
        # Optional read off that const local a `const Inner*` -- guards the two
        # _f1_is_const const paths (the byte-identical assertion above already
        # pins them to the AST path; these check the const spelling explicitly).
        cpp = self._cpp(self.SRC, thir=True)
        assert "const Inner& x = b.inner;" in cpp
        assert "const Leaf* p = ::tpy::optional_to_ptr(x.opt);" in cpp


# --- F2 form rung: reassigned/rebound pointer-locals (lvalue reseat) ---


class TestF2PointerLocal:
    def test_rvalue_reseat_is_ineligible(self):
        # Reseating from an rvalue (a constructor) needs the `__slot_N` rebind
        # machinery -- deferred past F2's lvalue-reseat slice -> AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, which: Int32) -> Int32:\n"
            + "    x = b.inner\n    if which < 0:\n        x = Inner(9)\n    return x.value\n")
        assert _fn(thir, "f") is None

    def test_name_alias_reseat_is_ineligible(self):
        # Reseating from a name (not a field source) is the deferred name-alias
        # case -> AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Int32:\n"
            + "    x = b.inner\n    y = c.inner\n"
            + "    if which < 0:\n        x = y\n    return x.value\n")
        assert _fn(thir, "f") is None

    def test_reseat_lowers_to_assign_with_convert(self):
        # The reseat is a THIRAssign whose value is the `&(...)` storage->borrow
        # convert; the read off the pointer-local is an arrow field access.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Int32:\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        reseat = fn.body[1].then_body[0]
        assert isinstance(reseat, THIRAssign) and reseat.target.name == "x"
        assert isinstance(reseat.value, THIRFormConvert)
        assert reseat.value.form is Form.BORROW
        assert isinstance(reseat.value.value, THIRFieldAccess)  # c.inner
        ret = fn.body[2]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.is_arrow


class TestF2Emit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def reseat(b: Box, c: Box, which: Int32) -> Int32:\n"
        + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x.value\n"
        + "def main():\n"
        + "    box = Box(Inner(3))\n    print(reseat(box, box, -1))\n"
        + "main()\n"
    )

    def test_f2_byte_identical(self):
        # The load-bearing F2a contract: the pointer-local init / reseat / arrow
        # read emit identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_pointer_local_init_and_reseat(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner* x = &(b.inner);" in cpp
        assert "x = &(c.inner);" in cpp

    def test_arrow_read_off_pointer_local(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return x->value;" in cpp

    def test_const_pointer_local(self):
        # A readonly receiver makes the reseatable pointer-local a `const Inner*`
        # (exercises _f1_is_const for POINTER), reseated and read identically.
        src = (
            _F1_RECORDS
            + "def f(b: readonly[Box], c: readonly[Box], which: Int32) -> Int32:\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x.value\n"
            + "def main():\n    box = Box(Inner(1))\n    print(f(box, box, -1))\nmain()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert "const Inner* x = &(b.inner);" in cpp
        assert "return x->value;" in cpp


# --- F2b form rung: Optional borrow->storage write (ptr_to_optional) ---


class TestF2bWrite:
    def test_optional_field_write_routes(self):
        # p = src.opt (OPTIONAL_TO_PTR borrow) ; dst.opt = p lowers to a field-target
        # THIRAssign whose value is the borrow->storage convert (ptr_to_optional).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = p\n")
        fn = _fn(thir, "move_opt")
        assert fn is not None
        write = fn.body[1]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "opt"
        assert isinstance(write.value, THIRFormConvert) and write.value.form is Form.STORAGE
        assert write.value.value.form is Form.BORROW  # the `p` borrow being lifted

    def test_copy_acknowledged_value_is_ineligible(self):
        # `copy(p)` (the explicit acknowledgment) is a call -- deferred to the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "from tpy import copy\n"
            + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = copy(p)\n")
        assert _fn(thir, "move_opt") is None

    def test_scalar_field_write_routes_as_plain_assign(self):
        # A scalar (non-optional) field write is not the F2b borrow->storage shape;
        # it routes via the scalar-field-write cell as a plain value assign.
        thir = _lower_ctx(
            _F1_RECORDS + "def setn(dst: Box):\n    dst.n = 5\n")
        st = _fn(thir, "setn").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        assert not isinstance(st.value, THIRFormConvert)

    def test_ref_alias_value_is_ineligible(self):
        # A `T&` REF_ALIAS value is not a `T*` pointer source (the AST path emits it
        # differently), so an optional-field write from it stays on the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(src: Box, dst: Box):\n    x = src.inner\n    dst.opt = x\n")
        assert _fn(thir, "f") is None


class TestF2bEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = p\n"
        + "def main():\n    a = Box(Inner(1))\n    b = Box(Inner(2))\n    move_opt(a, b)\nmain()\n"
    )

    def test_f2b_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_ptr_to_optional(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "dst.opt = ::tpy::ptr_to_optional(p);" in cpp

    def test_written_receiver_is_non_const(self):
        # Mutation enters the slice: the written receiver is a non-const `Box&`,
        # the read-only one a `const Box&` (pure const_borrow_params sema read).
        cpp = self._cpp(self.SRC, thir=True)
        assert "void move_opt(const Box& src, Box& dst)" in cpp


class TestF2PointerReceiver:
    """F2 paths where a POINTER local is itself the field-access receiver, so the
    field renders `x->field`: an Optional READ source (`optional_to_ptr(x->opt)`)
    and an Optional WRITE target (`x->opt = ptr_to_optional(leaf)`). Both are
    admitted by the F2a/F2b gates (a POINTER local is an F1-record receiver) and
    must stay byte-identical to the AST path; neither the scaffold nor the two
    corpus cases exercised them before."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def read_opt(b: Box, c: Box, which: Int32) -> Int32:\n"
        + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    q = x.opt\n    return 0\n"
        + "def write_opt(b: Box, c: Box, src: Inner, which: Int32):\n"
        + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    leaf = src.opt\n    x.opt = leaf\n"
        + "def main():\n"
        + "    bx = Box(Inner(1))\n    cx = Box(Inner(2))\n    s = Inner(3)\n"
        + "    print(read_opt(bx, cx, -1))\n    write_opt(bx, cx, s, 1)\nmain()\n"
    )

    def test_both_route(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "read_opt") is not None
        assert _fn(thir, "write_opt") is not None

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_optional_source_off_pointer_local(self):
        # `q = x.opt` off a POINTER local -> the storage read uses `x->opt`.
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::optional_to_ptr(x->opt)" in cpp

    def test_optional_write_off_pointer_local(self):
        # `x.opt = leaf` off a POINTER local -> the write target uses `x->opt`.
        cpp = self._cpp(self.SRC, thir=True)
        assert "x->opt = ::tpy::ptr_to_optional(leaf);" in cpp

    def test_reassigned_optional_local_is_ineligible(self):
        # A reassigned OPTIONAL_TO_PTR (optional pointer-local) needs the rebind-
        # slot machinery, so it stays on the AST path (the optional branch of
        # classify_local_binding returns OTHER for a reassigned name).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Int32:\n"
            + "    p = b.opt\n    if which < 0:\n        p = c.opt\n    return 0\n")
        assert _fn(thir, "f") is None


# --- F2c form rung: storage-form Optional[record] return + None write ---


class TestF2cReturn:
    def test_borrow_return_routes(self):
        # A storage-form `Own[Inner] | None` return lifts a borrow `T*` via
        # ptr_to_optional (copy): the return value is a borrow->storage convert.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def find(b: Box) -> Own[Inner] | None:\n    p = b.opt\n    return p\n")
        fn = _fn(thir, "find")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFormConvert) and ret.value.form is Form.STORAGE
        assert ret.value.value.form is Form.BORROW  # the `p` borrow being lifted

    def test_none_return_routes(self):
        # `return None` into a storage-form Optional lowers to a STORAGE-form None
        # literal (-> std::nullopt), not a borrow convert.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def nothing(b: Box) -> Own[Inner] | None:\n    return None\n")
        fn = _fn(thir, "nothing")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRLiteral) and ret.value.value is None
        assert ret.value.form is Form.STORAGE

    def test_pointer_repr_return_is_ineligible(self):
        # `Inner | None` is pointer-repr (the function returns a borrow `Inner*`),
        # a different direction than the storage `Own[Inner] | None` slot -> AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Inner | None:\n    p = b.opt\n    return p\n")
        assert _fn(thir, "f") is None

    def test_rvalue_return_is_ineligible(self):
        # A non-borrow, non-None source (here an rvalue ctor) into the storage
        # return slot is the direct-construction branch -- deferred to the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box) -> Own[Inner] | None:\n    return Inner(5)\n")
        assert _fn(thir, "f") is None

    def test_pointer_local_borrow_return_routes(self):
        # The borrow-return source via the POINTER (not OPTIONAL_TO_PTR) branch of
        # `_is_borrow_ptr_local`: a reseatable `T*` returned into Own[Inner]|None
        # copies (a POINTER is a non-owning borrow -> move=False).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, c: Box, which: Int32) -> Own[Inner] | None:\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    return x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[2]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.STORAGE and ret.value.move is False


class TestF2cNoneWrite:
    def test_none_field_write_routes(self):
        # `b.opt = None` lowers to a field-target THIRAssign whose value is a
        # STORAGE-form None literal (-> std::nullopt), no form convert.
        thir = _lower_ctx(
            _F1_RECORDS + "def clear(b: Box):\n    b.opt = None\n")
        fn = _fn(thir, "clear")
        assert fn is not None
        write = fn.body[0]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "opt"
        assert isinstance(write.value, THIRLiteral) and write.value.value is None
        assert write.value.form is Form.STORAGE


class TestF2cEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def find(b: Box) -> Own[Inner] | None:\n    p = b.opt\n    return p\n"
        + "def nothing(b: Box) -> Own[Inner] | None:\n    return None\n"
        + "def clear(b: Box):\n    b.opt = None\n"
        + "def main():\n"
        + "    bx = Box(Inner(3))\n    clear(bx)\n    a = find(bx)\n    c = nothing(bx)\n    print(0)\n"
        + "main()\n"
    )

    def test_f2c_byte_identical(self):
        # The load-bearing F2c contract: the borrow-return lift, the None return,
        # and the None field write all emit identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_borrow_return_emits_ptr_to_optional(self):
        assert "return ::tpy::ptr_to_optional(p);" in self._cpp(self.SRC, thir=True)

    def test_none_return_emits_nullopt(self):
        assert "return std::nullopt;" in self._cpp(self.SRC, thir=True)

    def test_none_write_emits_nullopt(self):
        assert "b.opt = std::nullopt;" in self._cpp(self.SRC, thir=True)

    def test_none_write_via_pointer_receiver(self):
        # `x.opt = None` off a POINTER local receiver -> `x->opt = std::nullopt;`
        # (the None write through the arrow-receiver path), byte-identical.
        src = (
            _F1_RECORDS
            + "def clearp(b: Box, c: Box, which: Int32):\n"
            + "    x = b.inner\n    if which < 0:\n        x = c.inner\n    x.opt = None\n"
            + "def main():\n    bx = Box(Inner(1))\n    clearp(bx, bx, 1)\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        assert "x->opt = std::nullopt;" in self._cpp(src, thir=True)


# --- F3 form rung: storage->borrow tuple read (tuple_to_pointer) ---

# A record with a pointer-repr tuple field: storage form `std::tuple<int32_t,
# Leaf>`, borrow form `std::tuple<int32_t, Leaf*>`.
_F3_RECORDS = (
    "from tpy import Int32, readonly\n"
    "class Leaf:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "class Holder:\n"
    "    pair: tuple[Int32, Leaf]\n"
    "    def __init__(self, b: Leaf):\n        self.pair = (1, b)\n"
)


class TestF3TupleReturn:
    def test_borrow_tuple_return_routes(self):
        # `return h.pair` lifts the storage tuple field into the borrow-form tuple
        # return via a STORAGE->BORROW convert (the tuple_to_pointer family).
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_field(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n")
        fn = _fn(thir, "ret_field")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFormConvert) and ret.value.form is Form.BORROW
        assert ret.value.is_const is False  # mutable receiver -> mutable Leaf* elements
        assert ret.value.value.form is Form.STORAGE  # the h.pair storage read

    def test_value_tuple_return_is_ineligible(self):
        # An all-value-scalar tuple has no pointer-repr element (borrow == storage),
        # so no tuple_to_pointer lift applies -- it stays on the AST path.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_pair(h: Holder) -> tuple[Int32, Int32]:\n    return (1, 2)\n")
        assert _fn(thir, "ret_pair") is None

    def test_tuple_field_init_ctor_stays_on_ast_path(self):
        # Regression guard for the M3 ctor-frontier fix: `Holder.__init__` does
        # `self.pair = (1, b)` -- a leading own-field init of an F3+ tuple type the
        # AST hoists into the member-init-list but THIR cannot reproduce there. It
        # must REJECT the whole ctor (return None, AST path) rather than demote the
        # init into the body, which would diverge from the AST's MIL hoist.
        assert _lower_ctor(_F3_RECORDS, "Holder") is None

    def test_storage_tuple_alias_local_routes(self):
        # A storage-tuple alias local (`t = h.pair`) binds `auto&&` (a STORAGE-form
        # alias) and a `return t` lifts it via tuple_to_pointer like a direct field
        # source.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_alias(h: Holder) -> tuple[Int32, Leaf]:\n"
            + "    t = h.pair\n    return t\n")
        fn = _fn(thir, "ret_alias")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.STORAGE_TUPLE_ALIAS
        assert decl.form is Form.STORAGE
        ret = fn.body[1]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.BORROW
        assert ret.value.value.form is Form.STORAGE  # the `t` alias read


class TestF3TupleReturnEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_RECORDS
        + "def ret_field(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n"
        + "def main():\n    h = Holder(Leaf(5))\n    t = ret_field(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_f3_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_tuple_to_pointer(self):
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, Leaf*>>(h.pair);"
                in self._cpp(self.SRC, thir=True))

    ALIAS_SRC = (
        _F3_RECORDS
        + "def ret_alias(h: Holder) -> tuple[Int32, Leaf]:\n"
        + "    t = h.pair\n    return t\n"
        + "def main():\n    h = Holder(Leaf(5))\n    t = ret_alias(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_alias_byte_identical(self):
        assert self._cpp(self.ALIAS_SRC, thir=True) == self._cpp(self.ALIAS_SRC, thir=False)

    def test_alias_emits_auto_ref(self):
        cpp = self._cpp(self.ALIAS_SRC, thir=True)
        assert "auto&& t = h.pair;" in cpp
        assert "return ::tpy::tuple_to_pointer<std::tuple<int32_t, Leaf*>>(t);" in cpp

    # A const (readonly) receiver makes the borrow tuple's element pointers const,
    # exercising the `to_cpp_return_const()` arm of the tuple_to_pointer lift -- for
    # both a direct field return and a storage-tuple alias local.
    CONST_SRC = (
        _F3_RECORDS
        + "def f(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
        + "    return h.pair\n"
        + "def g(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
        + "    t = h.pair\n    return t\n"
        + "def main():\n    h = Holder(Leaf(5))\n    a = f(h)\n    b = g(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_const_byte_identical(self):
        assert self._cpp(self.CONST_SRC, thir=True) == self._cpp(self.CONST_SRC, thir=False)

    def test_const_receiver_emits_const_tuple_to_pointer(self):
        cpp = self._cpp(self.CONST_SRC, thir=True)
        # direct field return + alias local both lift with const element pointers.
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, const Leaf*>>(h.pair);"
                in cpp)
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, const Leaf*>>(t);"
                in cpp)

    def test_const_receiver_lowers_const_convert(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
            + "    return h.pair\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.is_const is True


# --- F3 form rung: borrow->storage tuple field write (tuple_to_storage) ---

# Records with a pointer-repr Optional-element tuple field: storage form
# `std::tuple<std::optional<T>, ...>`, borrow form `std::tuple<T*, ...>`.
_F3_OPT_RECORDS = (
    "from tpy import Int32\n"
    "class T:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
    "class Holder:\n"
    "    pair: tuple[T | None, T | None]\n"
    "    def __init__(self) -> None:\n        self.pair = (None, None)\n"
)


class TestF3TupleFieldWrite:
    def test_borrow_tuple_field_write_routes(self):
        # `self.pair = p` where p is a borrow tuple param lifts borrow->storage via
        # a STORAGE-form convert (the tuple_to_storage family); the receiver becomes
        # a written (non-const) self.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "class Setter:\n"
            + "    pair: tuple[T | None, T | None]\n"
            + "    def __init__(self) -> None:\n        self.pair = (None, None)\n"
            + "    def update(self, p: tuple[T | None, T | None]) -> None:\n"
            + "        self.pair = p\n")
        fn = _fn(thir, "update")
        assert fn is not None
        write = fn.body[0]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "pair"
        assert isinstance(write.value, THIRFormConvert)
        assert write.value.form is Form.STORAGE and write.value.move is False
        assert write.value.value.form is Form.BORROW  # the borrow tuple param p

    def test_storage_source_field_write_is_ineligible(self):
        # A storage-form source (`other.pair`, a field read) is a direct copy with
        # no tuple_to_storage wrap -- a later F3 cell, so it stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def copy_from(h: Holder, other: Holder) -> None:\n"
            + "    h.pair = other.pair\n")
        assert _fn(thir, "copy_from") is None


class TestF3TupleFieldWriteEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_OPT_RECORDS
        + "def upd(h: Holder, p: tuple[T | None, T | None]) -> None:\n"
        + "    h.pair = p\n"
        + "def main():\n    h = Holder()\n    t = T(1)\n    upd(h, (t, None))\n    print(0)\n"
        + "main()\n"
    )

    def test_f3_write_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_tuple_to_storage(self):
        assert ("h.pair = ::tpy::tuple_to_storage<std::tuple<std::optional<T>, "
                "std::optional<T>>>(p);" in self._cpp(self.SRC, thir=True))


# --- Statement-shape axis: value-result tuple subscript reads (std::get<N>) ---

# The first cell of the statement-shape axis. A value-scalar tuple param
# (`const std::tuple<...>&`, its signature emitted by the AST path) read by
# subscript routes its body; the value-scalar slot of an already-routed
# pointer-repr tuple reads the same way. Borrow-result (record / Optional) element
# reads stay on the AST path.
class TestTupleSubscriptRead:
    def test_value_tuple_param_subscript_routes(self):
        # `tuple[Int32, Int32]` is admitted as a param; `p[0]` / `p[1]` lower to
        # value-form THIRSubscript reads off the param name.
        thir = _lower(
            _PRELUDE
            + "def consume(p: tuple[Int32, Int32]) -> Int32:\n    return p[0] + p[1]\n")
        fn = _fn(thir, "consume")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRBinOp)
        left = ret.value.left
        assert isinstance(left, THIRSubscript) and left.index.value == 0
        assert left.form is Form.VALUE
        assert isinstance(left.receiver, THIRName) and left.receiver.name == "p"
        assert ret.value.right.index.value == 1

    def test_negative_index_normalized(self):
        # `p[-3]` on a 3-tuple folds to index 0; `p[-1]` to index 2 -- the AST's
        # _extract_compile_time_index normalization.
        thir = _lower(
            _PRELUDE
            + "def f(p: tuple[Int32, Int32, Int32]) -> Int32:\n    return p[-3] + p[-1]\n")
        ret = _fn(thir, "f").body[0]
        assert ret.value.left.index.value == 0
        assert ret.value.right.index.value == 2

    def test_value_scalar_slot_of_pointer_repr_tuple_routes(self):
        # The Int32 slot of a pointer-repr tuple `tuple[Int32, Leaf]` reads as a
        # plain std::get (value form), reusing the already-routed borrow receiver.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def scalar_slot(t: tuple[Int32, Leaf]) -> Int32:\n    return t[0]\n")
        fn = _fn(thir, "scalar_slot")
        assert fn is not None
        sub = fn.body[0].value
        assert isinstance(sub, THIRSubscript) and sub.index.value == 0
        assert sub.form is Form.VALUE

    def test_value_scalar_tuple_local_is_ineligible(self):
        # Only value-tuple PARAMS are admitted in this cell; a value-tuple local
        # (`t = (1, 2)`) needs literal construction / init-source eligibility, a
        # later cell -- so a function building one stays on the AST path.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    t = (1, 2)\n    return t[0]\n")
        assert _fn(thir, "f") is None


class TestTupleSubscriptReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def consume(p: tuple[Int32, Int32]) -> Int32:\n    return p[0] + p[1]\n"
        + "def main():\n    print(consume((1, 2)))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_std_get(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::get<0>(p)" in cpp and "std::get<1>(p)" in cpp


# The routing-heavy subscript cell: a record-element read `t[N].field`. The subscript
# yields a borrow -- `std::get<N>(t)->field` off a borrow-form tuple param, or
# `std::get<N>(t).field` off a storage `auto&&` alias. Optional-element member access
# (null-check path), standalone binds, and writes stay on the AST path.
class TestTupleSubscriptRecordRead:
    def test_record_element_via_borrow_param_routes(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def via_param(t: tuple[Int32, Leaf]) -> Int32:\n    return t[1].n\n")
        fn = _fn(thir, "via_param")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFieldAccess)
        assert ret.value.is_arrow  # borrow-tuple param -> std::get<1>(t) is a T*
        sub = ret.value.receiver
        assert isinstance(sub, THIRSubscript) and sub.index.value == 1
        assert sub.form is Form.BORROW

    def test_record_element_via_storage_alias_reads_dot(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def via_alias(h: Holder) -> Int32:\n    a = h.pair\n    return a[1].n\n")
        fn = _fn(thir, "via_alias")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret.value, THIRFieldAccess)
        assert ret.value.is_arrow is False  # storage auto&& alias -> T&, dot access
        assert isinstance(ret.value.receiver, THIRSubscript)

    def test_element_form_drives_arrow_vs_dot(self):
        # Element form, not the receiver alone, decides `->` vs `.`: an Own element is
        # held by value (`std::get<0>(p).n`, dot), a bare-reference element is a borrow
        # pointer (`std::get<1>(p)->n`, arrow). Regression for the mixed owned+borrow
        # tuple (_tuple_subscript_yields_borrow_ptr mirror).
        thir = _lower_ctx(
            "from tpy import Own, Int32\n"
            "class A:\n    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            "def f(p: tuple[Own[A], A]) -> Int32:\n    return p[0].n + p[1].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        add = fn.body[0].value
        assert isinstance(add.left, THIRFieldAccess) and add.left.is_arrow is False
        assert isinstance(add.right, THIRFieldAccess) and add.right.is_arrow is True

    def test_negative_index_and_multi_element_record_read(self):
        # A negative index on a record element, and a record at index 2 of a 3-tuple:
        # both normalize to std::get<2> and arrow-decide correctly.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Int32, Leaf]) -> Int32:\n"
            + "    return t[-1].n + t[2].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        add = fn.body[0].value
        assert isinstance(add.left, THIRFieldAccess) and add.left.is_arrow
        assert isinstance(add.left.receiver, THIRSubscript) and add.left.receiver.index.value == 2
        assert add.right.receiver.index.value == 2

    def test_readonly_tuple_record_read_routes(self):
        # A readonly[tuple[...]] receiver still reads a record element via `->` (the
        # const is carried in the param type, not the access) -- byte-identical.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: readonly[tuple[Int32, Leaf]]) -> Int32:\n    return t[1].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.is_arrow
        assert isinstance(ret.value.receiver, THIRSubscript)

    def test_standalone_record_element_read_is_ineligible(self):
        # `b = t[1]` binds a record borrow local from a subscript -- the borrow-local
        # binding source path keeps its name-receiver gate, so this stays on AST.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Leaf]) -> Int32:\n    b = t[1]\n    return b.n\n")
        assert _fn(thir, "f") is None


class TestTupleSubscriptRecordReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_RECORDS
        + "def via_param(t: tuple[Int32, Leaf]) -> Int32:\n    return t[1].n\n"
        + "def via_alias(h: Holder) -> Int32:\n    a = h.pair\n    return a[1].n\n"
        + "def main():\n    h = Holder(Leaf(5))\n"
        + "    print(via_param(h.pair) + via_alias(h))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_arrow_and_dot(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return std::get<1>(t)->n;" in cpp   # borrow param
        assert "return std::get<1>(a).n;" in cpp     # storage alias


# The read frontier's tail: an unproven `Optional[record]`-element member access
# `t[N].field` -> `deref_check(...).field`. Off a borrow tuple the element is a nullable
# `T*`; off a storage alias it is `std::optional<T>` lifted to `T*` via optional_to_ptr.
class TestTupleSubscriptOptionalRead:
    def test_optional_element_via_borrow_param_routes(self):
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.deref_check
        assert ret.value.is_arrow is False  # deref_check reads `.` after the checked deref
        assert isinstance(ret.value.receiver, THIRSubscript)  # already a T*, no lift

    def test_optional_element_via_storage_alias_lifts(self):
        # Off a storage auto&& alias the element is std::optional<T>, lifted to T* via a
        # STORAGE->BORROW optional_to_ptr convert before the deref_check.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(h: Holder) -> Int32:\n    a = h.pair\n    return a[0].x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.deref_check
        conv = ret.value.receiver
        assert isinstance(conv, THIRFormConvert) and conv.form is Form.BORROW
        assert isinstance(conv.value, THIRSubscript) and conv.value.form is Form.STORAGE

    def test_index_1_and_readonly_optional_route(self):
        # An Optional element at index 1, and a readonly[tuple] receiver, both route
        # (index normalization + the const deref_check/optional_to_ptr overloads are
        # shared, unmodified machinery).
        thir = _lower_ctx(
            "from tpy import readonly\n" + _F3_OPT_RECORDS
            + "def i1(t: tuple[T | None, T | None]) -> Int32:\n    return t[1].x\n"
            + "def ro(t: readonly[tuple[T | None, T | None]]) -> Int32:\n    return t[0].x\n")
        assert _fn(thir, "i1") is not None and _fn(thir, "ro") is not None
        assert _fn(thir, "i1").body[0].value.receiver.index.value == 1

    def test_optional_element_write_is_ineligible(self):
        # A write through an Optional-element subscript (`t[0].x = 5`) keeps the
        # name-receiver gate on the write path -> stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> None:\n    t[0].x = 5\n")
        assert _fn(thir, "f") is None

    def test_optional_element_bind_is_ineligible(self):
        # A standalone bind of an Optional element (`e = t[0]`) is a borrow-local
        # binding source, which keeps the name-receiver gate -> stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    e = t[0]\n    return e.x\n")
        assert _fn(thir, "f") is None

    def test_dump_renders_deref_check(self):
        # The --dump-thir rendering of the runtime-null-checked member access (the one
        # test exercising dump.py's deref_check branch).
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n")
        assert "deref_check(%t[0] [borrow]).x" in dump_thir(thir)


class TestTupleSubscriptOptionalReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_OPT_RECORDS
        + "def viap(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n"
        + "def viaa(h: Holder) -> Int32:\n    a = h.pair\n    return a[0].x\n"
        + "def main():\n    h = Holder()\n    print(0)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_deref_check(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::deref_check(std::get<0>(t)).x" in cpp
        assert "::tpy::deref_check(::tpy::optional_to_ptr(std::get<0>(a))).x" in cpp


# The write position that closes the tuple-subscript family: a scalar field write to a
# record element, `t[N].field = <scalar>` (and `+= <scalar>`) -> `std::get<N>(t)->field
# = ...`. The target renders the same as the record-element read; only the write/aug-write
# eligibility gates are extended to the subscript target.
class TestTupleSubscriptWrite:
    def test_record_element_field_write_routes(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n = 5\n")
        fn = _fn(thir, "w")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow
        assert isinstance(st.target.receiver, THIRSubscript)

    def test_record_element_field_aug_write_routes(self):
        # `t[N].field += y` lowers to `target = (target OP value)`; the subscript target
        # renders identically on both sides.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n += 3\n")
        st = _fn(thir, "w").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.value, THIRBinOp)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow

    def test_write_via_storage_alias_dots(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(h: Holder) -> None:\n    a = h.pair\n    a[1].n = 9\n")
        st = _fn(thir, "w").body[1]
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow is False

    def test_optional_element_write_still_ineligible(self):
        # Extending the scalar-field-write gate to subscript targets must NOT admit an
        # Optional-element write (needs a null-checked write) -- markers reject it.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def w(t: tuple[T | None, T | None]) -> None:\n    t[0].x = 5\n")
        assert _fn(thir, "w") is None


class TestTupleSubscriptWriteEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    # Both target forms are byte-diffed and emit-checked: the borrow-param arrow write
    # (`w`) and the storage-alias dot write (`wa`), each with a plain and a `+=` variant.
    SRC = (
        "from tpy import Int32\n"
        "class Leaf:\n    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "class Holder:\n    pair: tuple[Int32, Leaf]\n"
        "    def __init__(self, b: Leaf) -> None:\n        self.pair = (1, b)\n"
        "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n = 5\n    t[1].n += 3\n"
        "def wa(h: Holder) -> None:\n    a = h.pair\n    a[1].n = 9\n    a[1].n += 2\n"
        "def main():\n    leaf = Leaf(1)\n    w((5, leaf))\n    h = Holder(leaf)\n"
        "    wa(h)\n    print(leaf.n)\n"
        "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_writes(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::get<1>(t)->n = 5;" in cpp                      # borrow param -> arrow
        assert ("std::get<1>(t)->n = ::tpy::add_check<int32_t>(std::get<1>(t)->n, 3);"
                in cpp)
        assert "std::get<1>(a).n = 9;" in cpp                       # storage alias -> dot
        assert ("std::get<1>(a).n = ::tpy::add_check<int32_t>(std::get<1>(a).n, 2);"
                in cpp)


# --- Statement-shape axis: container subscript reads (list[scalar] /
# dict[fixed-int, scalar] -> ::tpy::__getitem__ / bounds-safe operator[]) ---

# A scalar-element container param (`list[scalar]` / `dict[fixed-int, scalar]`, its
# signature emitted by the AST path) read by a subscript routes its body. Distinct
# from the tuple subscript: a runtime index EXPR (not a compile-time std::get offset)
# plus the `bounds_safe` fact on the node. A BigInt / view-typed (str/bytes) key or
# index, a container local, and `set` (no __getitem__) ride later cells.
class TestContainerSubscriptRead:
    def test_list_scalar_param_routes(self):
        thir = _lower(
            _PRELUDE
            + "def at(items: list[Int32], i: Int32) -> Int32:\n    return items[i]\n")
        fn = _fn(thir, "at")
        assert fn is not None
        sub = fn.body[0].value
        assert isinstance(sub, THIRSubscript) and sub.form is Form.VALUE
        assert isinstance(sub.receiver, THIRName) and sub.receiver.name == "items"
        assert isinstance(sub.index, THIRName) and sub.index.name == "i"
        assert not sub.bounds_safe

    def test_literal_index_routes(self):
        # `items[0]` -- a literal index (still the checked dunder; a param's length is
        # unknown, so a literal index is not bounds-safe).
        thir = _lower(
            _PRELUDE
            + "def first(items: list[Int32]) -> Int32:\n    return items[0]\n")
        sub = _fn(thir, "first").body[0].value
        assert isinstance(sub, THIRSubscript) and isinstance(sub.index, THIRLiteral)
        assert sub.index.value == 0 and not sub.bounds_safe

    def test_dict_fixed_int_key_routes(self):
        thir = _lower(
            _PRELUDE
            + "def get(d: dict[Int32, Int32], k: Int32) -> Int32:\n    return d[k]\n")
        sub = _fn(thir, "get").body[0].value
        assert isinstance(sub, THIRSubscript) and isinstance(sub.receiver, THIRName)

    def test_bigint_key_dict_param_ineligible(self):
        # A BigInt (`int`) key needs the `.to_fixed_check` narrow (a later cell), so a
        # BigInt-keyed dict param stays on the AST path. Isolated by a trivial body so
        # only the param gate decides.
        thir = _lower(
            _PRELUDE
            + "def g(d: dict[int, Int32], i: Int32) -> Int32:\n    return i\n")
        assert _fn(thir, "g") is None

    def test_str_keyed_dict_param_routes(self):
        # An owned-str dict key routes (S5); a StrView-keyed dict stays AST --
        # its literal keys pin to static storage (view_key_target).
        thir = _lower(
            _PRELUDE
            + "def g(d: dict[str, Int32], i: Int32) -> Int32:\n    return i\n")
        assert _fn(thir, "g") is not None
        view = _lower(
            _PRELUDE
            + "from tpy import StrView\n"
            + "def g(d: dict[StrView, Int32], i: Int32) -> Int32:\n    return i\n")
        assert _fn(view, "g") is None

    def test_set_param_ineligible(self):
        # `set` has no `__getitem__`; the param gate rejects it (the same shape with a
        # `list` param routes -- the control below isolates the container-kind gate).
        thir = _lower(
            _PRELUDE
            + "def h(s: set[Int32], i: Int32) -> Int32:\n    return i\n")
        assert _fn(thir, "h") is None
        ctrl = _lower(
            _PRELUDE
            + "def h(s: list[Int32], i: Int32) -> Int32:\n    return i\n")
        assert _fn(ctrl, "h") is not None

    def test_container_local_with_ctor_elements_routes(self):
        # Container-literal locals route (TestContainerLiteralLocal), and scalar
        # ctor-call elements are eligible exprs -- the elements fold into the
        # brace-init like bare literals.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    xs = [Int32(1), Int32(2)]\n    return xs[0]\n")
        fn = _fn(thir, "f")
        assert fn is not None
        lit = fn.body[0].init
        assert isinstance(lit, THIRContainerLiteral)
        assert all(isinstance(e, THIRCall) and e.cpp_template == "{0}"
                   for e in lit.elements)

    def test_readonly_container_routes(self):
        # A `readonly[list/dict]` param routes (byte-identical): sema readonly-wraps
        # only non-value elements, so a scalar element read is never `readonly[scalar]`,
        # and `_container_scalar_read` unwraps readonly on the container.
        rl = _lower(
            _PRELUDE + "from tpy import readonly\n"
            + "def r(items: readonly[list[Int32]], i: Int32) -> Int32:\n    return items[i]\n")
        assert _fn(rl, "r") is not None
        rd = _lower(
            _PRELUDE + "from tpy import readonly\n"
            + "def r(d: readonly[dict[Int32, Int32]], k: Int32) -> Int32:\n    return d[k]\n")
        assert _fn(rd, "r") is not None

    def test_own_container_param_ineligible(self):
        # `Own[list]` (a move-in `T&&` param) is excluded explicitly -- its ABI differs
        # from the borrow shape this slice assumes; it rides a later cell. Isolated by a
        # trivial body so only the param gate decides.
        thir = _lower(
            _PRELUDE + "from tpy import Own\n"
            + "def o(items: Own[list[Int32]], i: Int32) -> Int32:\n    return i\n")
        assert _fn(thir, "o") is None

    def test_negative_literal_index_routes(self):
        # A negative literal index `items[-1]` folds to a plain literal (the
        # AST's _gen_unaryop literal-negation branch); the checked dunder
        # normalizes it at runtime on both paths.
        thir = _lower(
            _PRELUDE
            + "def n(items: list[Int32]) -> Int32:\n    return items[-1]\n")
        fn = _fn(thir, "n")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRSubscript)
        assert isinstance(ret.value.index, THIRLiteral)
        assert ret.value.index.value == -1


class TestContainerSubscriptReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def first(items: list[Int32]) -> Int32:\n    return items[0]\n"
        + "def at(items: list[Int32], i: Int32) -> Int32:\n    return items[i]\n"
        + "def dget(d: dict[Int32, Int32], k: Int32) -> Int32:\n    return d[k]\n"
        + "def main():\n"
        + "    xs = [10, 20]\n"
        + "    print(first(xs))\n    print(at(xs, 1))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_getitem(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return ::tpy::__getitem__(items, 0);" in cpp        # literal index
        assert "return ::tpy::__getitem__(items, i);" in cpp        # dynamic list index
        assert "return ::tpy::__getitem__(d, k);" in cpp            # dict fixed-int key

    def test_bounds_safe_emits_size_t_cast(self):
        # No routable cell-1 body produces a bounds_safe container subscript yet (the
        # producer is for-loops -- a later statement-shape cell), so exercise the emit
        # branch directly off a real lowered node.
        thir = _lower(
            _PRELUDE
            + "def at(xs: list[Int32], i: Int32) -> Int32:\n    return xs[i]\n")
        sub = _fn(thir, "at").body[0].value
        assert not sub.bounds_safe
        bounded = dataclasses.replace(sub, bounds_safe=True)
        assert _emit_expr(bounded) == "xs[static_cast<std::size_t>(i)]"

    def test_bounds_safe_literal_index_no_cast(self):
        # The literal-index sub-branch of the bounds_safe emit (`recv[idx]`, no cast) --
        # dead on both paths today (bounds_safe requires a name index), but a faithful
        # mirror of _gen_subscript:6148, so exercise it directly off a lowered node.
        thir = _lower(
            _PRELUDE
            + "def first(xs: list[Int32]) -> Int32:\n    return xs[0]\n")
        sub = _fn(thir, "first").body[0].value
        assert isinstance(sub.index, THIRLiteral)
        bounded = dataclasses.replace(sub, bounds_safe=True)
        assert _emit_expr(bounded) == "xs[0]"


# --- F2d form rung: rvalue rebind-slot pointer-locals (the __slot_N machinery) ---


class TestF2dRebindSlot:
    def test_rvalue_reassigned_routes(self):
        # An rvalue-reassigned plain-record local is a rebind-slot pointer-local:
        # the decl is a REBIND_SLOT whose init is the rvalue ctor (no convert),
        # the reseat a plain THIRAssign of the ctor, and reads are arrow accesses.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def reb() -> Int32:\n"
            + "    p = Inner(1)\n    a = p.value\n    p = Inner(2)\n    return p.value + a\n")
        fn = _fn(thir, "reb")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT
        assert decl.form is Form.BORROW and decl.cpp_type == "Inner"
        assert isinstance(decl.init, THIRCall) and decl.init.callee == "Inner"
        read = fn.body[1].init  # a = p.value -> arrow read off the pointer-local
        assert isinstance(read, THIRFieldAccess) and read.is_arrow
        reseat = fn.body[2]
        assert isinstance(reseat, THIRAssign) and reseat.target.name == "p"
        assert isinstance(reseat.value, THIRCall) and reseat.value.callee == "Inner"

    def test_single_assignment_rvalue_is_ineligible(self):
        # No reassignment -> a plain value local (`Inner p = Inner(1);`), not a
        # rebind-slot pointer-local -> stays on the AST path.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n    p = Inner(1)\n    return p.value\n")
        assert _fn(thir, "f") is None

    def test_kwarg_ctor_normalizes_and_routes(self):
        # sema rewrites a single-param ctor kwarg to a positional arg before
        # lowering (`Inner(value=1)` -> `Inner(1)`), so it still routes as a
        # rebind-slot rvalue source. (The `init.kwargs` guard only fires for a
        # ctor sema leaves un-normalized.)
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    p = Inner(value=1)\n    a = p.value\n    p = Inner(value=2)\n    return a + p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT
        assert isinstance(decl.init, THIRCall) and len(decl.init.args) == 1

    def test_record_arg_ctor_source_is_ineligible(self):
        # A rebind-slot ctor whose arg is a non-scalar (a record value-local) needs
        # the AST's arg deref / auto-move, which the bare THIRCall arg emit does not
        # reproduce -- so the arg gate (mirroring _call_eligible) rejects it -> AST
        # path. (Box's ctor takes Own[Inner]; `a` is a record value-local.)
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f() -> Int32:\n"
            + "    a = Inner(0)\n    p = Box(a)\n    p = Box(a)\n    return p.n\n")
        assert _fn(thir, "f") is None

    def test_function_call_rebind_source_routes(self):
        # A by-value record-returning FREE FUNCTION (not a ctor) is also a valid
        # rebind-slot rvalue source; it emits as the bare `make_inner()`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def make_inner() -> Own[Inner]:\n    return Inner(9)\n"
            + "def f() -> Int32:\n"
            + "    p = make_inner()\n    p = make_inner()\n    return p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert decl.cpp_local_representation is LocalBinding.REBIND_SLOT
        assert isinstance(decl.init, THIRCall) and decl.init.callee == "make_inner"

    def test_conditional_reseat_routes(self):
        # A REBIND_SLOT reseat inside an `if`-body (the in-branch reseat path).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(x: Int32) -> Int32:\n"
            + "    p = Inner(1)\n    if x < 0:\n        p = Inner(2)\n    return p.value\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].cpp_local_representation is LocalBinding.REBIND_SLOT
        reseat = fn.body[1].then_body[0]  # the in-branch reseat
        assert isinstance(reseat, THIRAssign) and isinstance(reseat.value, THIRCall)

    def test_lvalue_reseat_of_rebind_slot_is_ineligible(self):
        # A REBIND_SLOT local (rvalue first decl) reseated with an lvalue field
        # source is the deferred mixed case -- `_is_record_rvalue_source` needs a
        # ctor/call, so it stays on the AST path (mirror of the POINTER+rvalue case).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(b: Box, x: Int32) -> Int32:\n"
            + "    p = Inner(1)\n    if x < 0:\n        p = b.inner\n    return p.value\n")
        assert _fn(thir, "f") is None


class TestF2dEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def reb() -> Int32:\n"
        + "    p = Inner(1)\n    a = p.value\n    p = Inner(2)\n    return p.value + a\n"
        # two rebind-slot locals -> __slot_1.._slot_4, exercising slot numbering.
        + "def two() -> Int32:\n"
        + "    p = Inner(1)\n    q = Inner(2)\n    p = Inner(3)\n    q = Inner(4)\n"
        + "    return p.value + q.value\n"
        + "def main():\n    print(reb() + two())\nmain()\n"
    )

    def test_f2d_byte_identical(self):
        # The load-bearing F2d contract: the two-slot init, the optional rebind
        # slot, the pointer reseat, and the arrow reads emit identically to the
        # AST path -- including slot numbering across two rebind-slot locals.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_two_slot_init_and_reseat(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner __slot_1 = Inner(1);" in cpp
        assert "std::optional<Inner> __slot_2;" in cpp
        assert "Inner* p = &__slot_1;" in cpp
        assert "p = &*(__slot_2 = Inner(2));" in cpp

    def test_arrow_reads_off_rebind_local(self):
        assert "int32_t a = p->value;" in self._cpp(self.SRC, thir=True)

    def test_slot_numbering_across_two_locals(self):
        # The second rebind-slot local numbers after the first (init then rebind):
        # p -> __slot_1/__slot_2, q -> __slot_3/__slot_4.
        cpp = self._cpp(self.SRC, thir=True)
        assert "Inner __slot_3 = Inner(2);" in cpp
        assert "std::optional<Inner> __slot_4;" in cpp
        assert "Inner* q = &__slot_3;" in cpp
        assert "q = &*(__slot_4 = Inner(4));" in cpp

    def test_function_call_source_byte_identical(self):
        # A by-value record-returning function as the rvalue source emits the bare
        # call into the two-slot form, byte-identical to the AST path.
        src = (
            _F1_RECORDS
            + "def make_inner() -> Own[Inner]:\n    return Inner(9)\n"
            + "def f() -> Int32:\n"
            + "    p = make_inner()\n    a = p.value\n    p = make_inner()\n    return p.value + a\n"
            + "def main():\n    print(f())\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        assert "Inner __slot_1 = make_inner();" in self._cpp(src, thir=True)

    def test_conditional_reseat_byte_identical(self):
        # A rebind-slot reseat inside an `if`-body emits identically to the AST path
        # (the slot is allocated at the top-level decl, reused in the branch).
        src = (
            _F1_RECORDS
            + "def f(x: Int32) -> Int32:\n"
            + "    p = Inner(1)\n    if x < 0:\n        p = Inner(2)\n    return p.value\n"
            + "def main():\n    print(f(-1))\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


# --- F2e form rung: the _move write + return variants (owned source) ---


class TestF2eMove:
    def test_write_move_routes(self):
        # An owned rebind-slot local written into an optional field at last use
        # lifts borrow->storage with move=True (-> ptr_to_optional_move).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def store(dst: Box):\n    p = Inner(1)\n    p = Inner(2)\n    dst.opt = p\n")
        fn = _fn(thir, "store")
        assert fn is not None
        write = fn.body[2]
        assert isinstance(write, THIRAssign) and isinstance(write.value, THIRFormConvert)
        assert write.value.form is Form.STORAGE and write.value.move is True

    def test_write_copy_when_not_last_use(self):
        # The same rebind-slot read again after the write is not a last use -> copy.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def keep(dst: Box) -> Int32:\n    p = Inner(1)\n    p = Inner(2)\n"
            + "    dst.opt = p\n    return p.value\n")
        write = _fn(thir, "keep").body[2]
        assert isinstance(write.value, THIRFormConvert) and write.value.move is False

    def test_return_move_routes(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "def make(flag: Int32) -> Own[Inner] | None:\n"
            + "    p = Inner(1)\n    p = Inner(2)\n    return p\n")
        ret = _fn(thir, "make").body[2]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.STORAGE and ret.value.move is True

    def test_nonowning_borrow_write_stays_copy(self):
        # Regression: an OPTIONAL_TO_PTR (non-owning) source is never movable, so
        # the optional-field write stays a copy (move=False) -- F2b unchanged.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def move_opt(src: Box, dst: Box):\n    p = src.opt\n    dst.opt = p\n")
        write = _fn(thir, "move_opt").body[1]
        assert isinstance(write.value, THIRFormConvert) and write.value.move is False


class TestF2eEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F1_RECORDS
        + "def store(dst: Box):\n    p = Inner(1)\n    p = Inner(2)\n    dst.opt = p\n"
        + "def keep(dst: Box) -> Int32:\n"
        + "    p = Inner(1)\n    p = Inner(2)\n    dst.opt = p\n    return p.value\n"
        + "def make(flag: Int32) -> Own[Inner] | None:\n"
        + "    p = Inner(1)\n    p = Inner(2)\n    return p\n"
        + "def main():\n    d = Box(Inner(0))\n    store(d)\n    print(keep(d))\n    r = make(0)\n"
        + "main()\n"
    )

    def test_f2e_byte_identical(self):
        # The load-bearing F2e contract: the move write, the copy write (not last
        # use), and the move return all emit identically to the AST path.
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_write_move_emits_move_helper(self):
        assert "dst.opt = ::tpy::ptr_to_optional_move(p);" in self._cpp(self.SRC, thir=True)

    def test_write_copy_emits_copy_helper(self):
        assert "dst.opt = ::tpy::ptr_to_optional(p);" in self._cpp(self.SRC, thir=True)

    def test_return_move_emits_move_helper(self):
        assert "return ::tpy::ptr_to_optional_move(p);" in self._cpp(self.SRC, thir=True)


# --- M1 method frontier: instance methods with a `self` (`this`) receiver ---

# Methods over the F1 records. `get_n` is a scalar field read (auto-readonly,
# const self); `head` a REF_ALIAS off self; `peek` an OPTIONAL_TO_PTR off a
# readonly self (-> `const Inner*`); `reset` a non-readonly method writing
# `self.opt = None` (mutates self -> non-const `this`).
_M1_METHODS = (
    _F1_RECORDS
    + "    def get_n(self) -> Int32:\n        return self.n\n"
    + "    def head(self) -> Int32:\n        x = self.inner\n        return x.value\n"
    + "    def peek(self) -> Int32:\n        p = self.opt\n        return 0\n"
    + "    def reset(self):\n        self.opt = None\n"
)


class TestMethodFrontier:
    def test_scalar_field_read_off_self(self):
        thir = _lower_ctx(_M1_METHODS)
        fn = _fn(thir, "get_n")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFieldAccess)
        assert isinstance(ret.value.receiver, THIRSelf)
        assert ret.value.is_arrow and ret.value.form is Form.VALUE

    def test_ref_alias_off_self(self):
        decl = _fn(_lower_ctx(_M1_METHODS), "head").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS
        assert isinstance(decl.init.receiver, THIRSelf) and decl.init.is_arrow

    def test_optional_to_ptr_off_readonly_self_is_const(self):
        # A readonly method's `self` is const, so the borrow lifts to `const T*`.
        decl = _fn(_lower_ctx(_M1_METHODS), "peek").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_nonreadonly_method_routes(self):
        # `reset` writes `self.opt = None` -> self is non-readonly (non-const
        # `this`); routes via the F2c storage-form None write.
        assert _fn(_lower_ctx(_M1_METHODS), "reset") is not None

    def test_staticmethod_excluded(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @staticmethod\n    def smethod(a: Int32) -> Int32:\n        return a\n")
        assert _fn(thir, "smethod") is None

    def test_record_param_method_routes(self):
        # M2: a non-readonly method takes an F1-record param like a free function;
        # `other.n` is a scalar field read off the record param.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def with_box(self, other: Box) -> Int32:\n        return other.n\n")
        assert _fn(thir, "with_box") is not None

    def test_generic_record_method_excluded(self):
        # A generic record's `self` is templated -> outside the F1-record slice.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Wrap[T]:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    def get(self) -> Int32:\n        return self.n\n")
        assert _fn(thir, "get") is None

    def test_constructor_excluded(self):
        # The ctor body is emitted via the member-init-list driver, not gen_body;
        # iter_module_callables skips record.init_method (the M3 ctor frontier).
        thir = _lower_ctx(_M1_METHODS)
        assert _fn(thir, "__init__") is None

    def test_property_getter_excluded(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @property\n    def doubled(self) -> Int32:\n        return self.n\n")
        assert _fn(thir, "doubled") is None

    def test_optional_to_ptr_off_mutable_self_is_nonconst(self):
        # A non-readonly method (writes self.opt) reads self.opt off a non-const
        # `this`, so the borrow lifts to a mutable `Inner*`, not `const Inner*`.
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def churn(self):\n        p = self.opt\n        self.opt = None\n")
        decl = _fn(thir, "churn").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and not decl.is_const


class TestMethodFrontierEmit:
    def _emit(self, src: str, thir: bool):
        # Instance methods emit inline in the struct (the .hpp), so the contract
        # is checked over header + source, not just the .cpp.
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _M1_METHODS
        + "def main():\n    b = Box(Inner(0))\n    print(b.get_n() + b.head())\n"
        + "    print(b.peek())\n    b.reset()\n"
        + "main()\n"
    )

    def test_methods_byte_identical(self):
        # The load-bearing contract for the frontier: method bodies emit
        # identically from THIR and the AST path.
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_renders_as_this_arrow(self):
        # The `self` receiver renders as the C++ `this` pointer with `->`.
        assert "return this->n;" in self._emit(self.SRC, thir=True)

    def test_readonly_self_optional_read_is_const(self):
        assert "const Inner* p = ::tpy::optional_to_ptr(this->opt);" in self._emit(self.SRC, thir=True)

    def test_nonreadonly_self_none_write(self):
        assert "this->opt = std::nullopt;" in self._emit(self.SRC, thir=True)


# --- M2: record params on instance methods (readonly or not) ---

_M2_METHODS = (
    _F1_RECORDS
    # reads a scalar field off the record param -> param is const-ref (not mutated)
    + "    def sum_with(self, other: Box) -> Int32:\n        return self.n + other.n\n"
    # reads other.opt -> a borrow local off a const record param (const Inner*)
    + "    def peek_other(self, other: Box) -> Int32:\n        p = other.opt\n        return 0\n"
    # writes other.opt -> the param is mutated, so it is a mutable ref (Inner*)
    + "    def clear_other(self, other: Box):\n        p = other.opt\n        other.opt = None\n"
    # mutates b but only reads a.opt: the const verdict must key on a's param index
    + "    def mix(self, a: Box, b: Box) -> Int32:\n        b.n = 1\n        p = a.opt\n        return 0\n"
)


class TestMethodFrontierM2:
    def test_const_record_param_scalar_read_routes(self):
        assert _fn(_lower_ctx(_M2_METHODS), "sum_with") is not None

    def test_optional_to_ptr_off_const_record_param_is_const(self):
        # `other` is not mutated -> const-ref param -> the borrow off other.opt
        # lifts to `const Inner*` (the const verdict comes from the method's own
        # const_borrow_params, looked up on the owning record).
        decl = _fn(_lower_ctx(_M2_METHODS), "peek_other").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_optional_to_ptr_off_mutated_record_param_is_nonconst(self):
        # `clear_other` writes other.opt -> `other` is a mutable ref, so the
        # borrow off it is `Inner*`, not `const Inner*`.
        decl = _fn(_lower_ctx(_M2_METHODS), "clear_other").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and not decl.is_const

    def test_readonly_method_with_record_param_routes(self):
        # An explicit @readonly method with a record param routes: for a plain
        # F1-record (ref) param the forced-const and inferred-const verdicts
        # coincide, so const_borrow_params is exact (no carve-out needed).
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @readonly\n    def ro_with(self, other: Box) -> Int32:\n"
            + "        return other.n\n")
        assert _fn(thir, "ro_with") is not None

    def test_auto_readonly_method_with_record_param_routes(self):
        # A non-mutating method that reads a record param is auto-readonly; it
        # must still route (this is the common case the rung exists for).
        thir = _lower_ctx(
            _F1_RECORDS
            + "    def auto_ro(self, other: Box) -> Int32:\n        return other.n\n")
        assert _fn(thir, "auto_ro") is not None

    def test_optional_to_ptr_off_explicit_readonly_record_param_is_const(self):
        # An explicit @readonly method that lifts a borrow off a record param:
        # the param is not mutated, so the forced-const verdict and the inferred
        # const_borrow_params verdict coincide -> `const Inner*` (pins the claim
        # the eligibility comment rests on, for the explicit-readonly path).
        thir = _lower_ctx(
            _F1_RECORDS
            + "    @readonly\n    def ro_peek(self, other: Box) -> Int32:\n"
            + "        p = other.opt\n        return 0\n")
        decl = _fn(thir, "ro_peek").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const

    def test_mixed_mutation_const_verdict_keys_on_param_index(self):
        # `mix` mutates b but only reads a.opt; the const verdict must key on a's
        # param index (0), not b's (1) -- exercises the index-based
        # const_borrow_params lookup that a single-param method never does.
        decl = next(s for s in _fn(_lower_ctx(_M2_METHODS), "mix").body
                    if isinstance(s, THIRVarDecl)
                    and s.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR)
        assert decl.form is Form.BORROW and decl.is_const


class TestMethodFrontierM2Emit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _M2_METHODS
        + "def main():\n    a = Box(Inner(1))\n    b = Box(Inner(2))\n"
        + "    print(a.sum_with(b))\n    print(a.peek_other(b))\n    a.clear_other(b)\n"
        + "main()\n"
    )

    def test_m2_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_const_record_param_borrow_emits_const(self):
        assert ("const Inner* p = ::tpy::optional_to_ptr(other.opt);"
                in self._emit(self.SRC, thir=True))


# --- Readonly free functions: a `@readonly` free function is admitted (its
# record params are forced const, which coincides with the inferred verdict) ---

_RO_FREE = (
    _F1_RECORDS
    + "@readonly\ndef width(b: Box) -> Int32:\n    return b.n\n"
    + "@readonly\ndef peek_free(b: Box) -> Int32:\n    p = b.opt\n    return 0\n"
)


class TestReadonlyFreeFunction:
    def test_readonly_free_function_routes(self):
        assert _fn(_lower_ctx(_RO_FREE), "width") is not None

    def test_optional_to_ptr_off_readonly_free_param_is_const(self):
        # A readonly free function forces its record param const; that coincides
        # with the inferred const_borrow_params verdict (param not mutated), so the
        # borrow off b.opt lifts to `const Inner*`.
        decl = _fn(_lower_ctx(_RO_FREE), "peek_free").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.OPTIONAL_TO_PTR
        assert decl.form is Form.BORROW and decl.is_const


class TestReadonlyFreeFunctionEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _RO_FREE
        + "def main():\n    b = Box(Inner(5))\n    print(width(b))\n    print(peek_free(b))\n"
        + "main()\n"
    )

    def test_readonly_free_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_readonly_free_param_borrow_emits_const(self):
        assert ("const Inner* p = ::tpy::optional_to_ptr(b.opt);"
                in self._emit(self.SRC, thir=True))


# --- Scalar field writes: `recv.field = <scalar>` off an F1-record receiver ---

_SCALAR_WRITE = (
    "from tpy import Int32\n"
    "class Counter:\n    count: Int32\n    other: Int32\n"
    "    def __init__(self, count: Int32):\n        self.count = count\n        self.other = 0\n"
    "    def reset(self):\n        self.count = 0\n"
    "    def copy_field(self):\n        self.count = self.other\n"
    # off a record param in a free function (non-self receiver)
    "def bump(c: Counter, n: Int32):\n    c.count = n\n    c.other = c.count + 1\n"
)


class TestScalarFieldWrite:
    def test_write_off_self_routes_as_plain_assign(self):
        # `self.count = 0` lowers to a plain THIRAssign (value form), NOT the F2b
        # borrow->storage path -- the field is a scalar, so no THIRFormConvert.
        fn = _fn(_lower_ctx(_SCALAR_WRITE), "reset")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow
        assert not isinstance(st.value, THIRFormConvert)
        assert st.value.form is Form.VALUE

    def test_field_to_field_scalar_copy(self):
        st = _fn(_lower_ctx(_SCALAR_WRITE), "copy_field").body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.value, THIRFieldAccess) and st.value.form is Form.VALUE

    def test_write_off_record_param_routes(self):
        # A scalar field write off a record param in a free function (non-self).
        assert _fn(_lower_ctx(_SCALAR_WRITE), "bump") is not None

    def test_property_setter_target_excluded(self):
        # A field write that is really a @property setter takes a method-call
        # emit path, not a plain field assign -> stays on the AST path.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class C:\n    _n: Int32\n"
            "    def __init__(self):\n        self._n = 0\n"
            "    @property\n    def n(self) -> Int32:\n        return self._n\n"
            "    @n.setter\n    def n(self, v: Int32):\n        self._n = v\n"
            "    def use(self):\n        self.n = 5\n")
        assert _fn(thir, "use") is None

    def test_write_off_pointer_local_routes_with_arrow(self):
        # Receiver is an F2 reseatable `T*` pointer-local -- the third
        # `_field_receiver_ok` receiver kind, rendering `->` (distinct from self's
        # `this->` and a record param's `.`).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def via_ptr(b: Box, c: Box):\n    x = b.inner\n    x = c.inner\n    x.value = 5\n")
        fn = _fn(thir, "via_ptr")
        assert fn is not None
        st = fn.body[-1]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow

    def test_mixed_scalar_and_optional_write_body(self):
        # Both write forms in one body exercise the `or`-dispatch in
        # `_stmt_eligible` -- neither blocks the other's eligibility.
        thir = _lower_ctx(
            _F1_RECORDS + "def mixed(b: Box):\n    b.n = 7\n    b.opt = None\n")
        assert _fn(thir, "mixed") is not None


class TestScalarFieldWriteEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _SCALAR_WRITE
        + "def main():\n    c = Counter(3)\n    c.reset()\n    c.copy_field()\n    bump(c, 7)\n"
        + "    print(c.count + c.other)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_scalar_write_emits_arrow_assign(self):
        assert "this->count = 0;" in self._emit(self.SRC, thir=True)

    def test_param_scalar_write_emits_dot_assign(self):
        assert "c.count = n;" in self._emit(self.SRC, thir=True)

    def test_pointer_local_scalar_write_emits_arrow(self):
        src = (
            _F1_RECORDS
            + "def via_ptr(b: Box, c: Box):\n    x = b.inner\n    x = c.inner\n    x.value = 5\n"
            + "def main():\n    b = Box(Inner(1))\n    via_ptr(b, b)\n    print(b.inner.value)\n"
            + "main()\n")
        assert "x->value = 5;" in self._emit(src, thir=True)
        assert self._emit(src, thir=True) == self._emit(src, thir=False)


# --- Augmented assignment: `x += y` / `recv.field += y` (scalar) ---

_AUG = (
    "from tpy import Int32\n"
    "class Counter:\n    count: Int32\n"
    "    def __init__(self, count: Int32):\n        self.count = count\n"
    "    def tick(self, n: Int32):\n        self.count += n\n"
    # local aug-assign + a field aug-assign off a record param (non-self)
    "def bump(c: Counter, n: Int32) -> Int32:\n"
    "    n += 1\n    c.count += n\n    return n\n"
)


class TestScalarAugAssign:
    def test_local_aug_assign_routes_as_binop(self):
        st = _fn(_lower_ctx(_AUG), "bump").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRName)
        assert isinstance(st.value, THIRBinOp) and st.value.op == "+"
        # the binop's left operand re-reads the target (the AST likewise
        # substitutes the target string into both the lvalue and the binop).
        assert isinstance(st.value.left, THIRName) and st.value.left.name == "n"

    def test_field_aug_assign_off_param(self):
        st = _fn(_lower_ctx(_AUG), "bump").body[1]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        assert isinstance(st.value, THIRBinOp)
        assert isinstance(st.value.left, THIRFieldAccess)

    def test_field_aug_assign_off_self(self):
        st = _fn(_lower_ctx(_AUG), "tick").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.target, THIRFieldAccess)
        assert st.target.is_arrow
        assert isinstance(st.value, THIRBinOp)

    def test_inplace_dunder_excluded(self):
        # `xs += [v]` resolves to list_extend (__iadd__) -- mutates in place via a
        # method call, not the binop substitution -> AST path.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def grow(v: Int32):\n    xs = [1]\n    xs += [v]\n")
        assert _fn(thir, "grow") is None

    def test_str_aug_assign_routes_as_append(self):
        # `s += t` on a str takes the in-place-append emit -- not the scalar
        # synthetic-binop path this class covers (S3, THIRStrAppend).
        thir = _lower_ctx(
            "def cat(t: str):\n    s = 'a'\n    s += t\n")
        stmt = _fn(thir, "cat").body[1]
        assert isinstance(stmt, THIRStrAppend)

    def test_subscript_aug_assign_excluded(self):
        # A subscript target takes the set_value/get_value path -> AST.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def at(xs: list[Int32], i: Int32):\n    xs[i] += 1\n")
        assert _fn(thir, "at") is None

    def test_float_local_aug_assign_routes(self):
        # A double `float` is an eligible scalar -- the value-scalar slice is not
        # int-only.
        st = _fn(_lower(
            "def f(a: float) -> float:\n    a += 1.0\n    return a\n"), "f").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.value, THIRBinOp)


class TestScalarAugAssignEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _AUG
        + "def main():\n    c = Counter(3)\n    c.tick(2)\n    print(bump(c, 4))\n"
        + "    print(c.count)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_self_field_aug_emits_arrow(self):
        # No outer parens (the aug-assign RHS is a full statement RHS).
        assert ("this->count = ::tpy::add_check<int32_t>(this->count, n);"
                in self._emit(self.SRC, thir=True))

    def test_floordiv_aug_not_swapped(self):
        # `q //= d` with a non-proven-zero divisor must emit the checked helper,
        # not div_floor -- the AST aug-assign path never swaps it.
        src = (
            "from tpy import Int32\n"
            "def f(q: Int32, d: Int32) -> Int32:\n    q //= d\n    return q\n"
            "def main():\n    print(f(10, 3))\nmain()\n")
        out = self._emit(src, thir=True)
        assert "div_floor" not in out
        assert out == self._emit(src, thir=False)

    def test_other_ops_byte_identical(self):
        # -= *= %= alongside the += / //= already covered.
        src = (
            "from tpy import Int32\n"
            "def f(a: Int32, b: Int32) -> Int32:\n"
            "    a -= b\n    a *= b\n    a %= b\n    return a\n"
            "def main():\n    print(f(20, 3))\nmain()\n")
        assert self._emit(src, thir=True) == self._emit(src, thir=False)

    def test_float_aug_byte_identical(self):
        src = (
            "def g(a: float, b: float) -> float:\n    a += b\n    a *= b\n    return a\n"
            "def main():\n    print(g(1.5, 2.0))\nmain()\n")
        assert self._emit(src, thir=True) == self._emit(src, thir=False)


class TestConstructor:
    """The M3a ctor frontier: pure-MIL scalar constructors of flat records --
    every `__init__` statement is a hoistable own-scalar field init, so the
    member-init-list is the whole body and the C++ body is `{}`."""

    _POINT = (
        _PRELUDE
        + "class Point:\n    x: Int32\n    y: Int32\n"
        + "    def __init__(self, x: Int32, y: Int32):\n"
        + "        self.x = x\n        self.y = y\n")

    def test_pure_scalar_ctor_routes(self):
        ctor = _lower_ctor(self._POINT, "Point")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["x", "y"]
        assert ctor.body == ()  # pure-MIL: empty body

    def test_pure_scalar_ctor_tail_emit(self):
        ctor = _lower_ctor(self._POINT, "Point")
        assert _ctor_tail(ctor) == " : x(x), y(y) {}\n"

    def test_no_param_literal_inits_route(self):
        ctor = _lower_ctor(
            _PRELUDE
            + "class Counter:\n    n: Int32\n    step: Int32\n"
            + "    def __init__(self):\n        self.n = 0\n        self.step = 1\n",
            "Counter")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : n(0), step(1) {}\n"

    def test_field_init_from_sibling_field_routes(self):
        # An RHS reading a sibling scalar field renders `b(this->a)` (the corpus
        # byte-diff validates this against the AST path).
        ctor = _lower_ctor(
            _PRELUDE
            + "class C:\n    a: Int32\n    b: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        self.a = a\n        self.b = self.a\n",
            "C")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : a(a), b(this->a) {}\n"

    def test_pass_body_ctor_routes(self):
        # M3c-trivia: `pass` is non-init trivia -- it stays in the body (so the
        # braces are ` {\n    }`, not ` {}`) but breaks no chain, so the field init
        # still hoists. It emits no code; its `loc` carries the `// pass` comment.
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n        pass\n",
            "P")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["x"]
        assert len(ctor.body) == 1
        assert _ctor_tail(ctor) == " : x(x) {\n    }\n"

    def test_docstring_ctor_routes(self):
        # M3c-trivia: a docstring is non-init trivia too -- same body-brace effect,
        # chain intact. It emits neither code nor comment (loc=None).
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + '        """doc"""\n        self.x = x\n',
            "P")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["x"]
        assert _ctor_tail(ctor) == " : x(x) {\n    }\n"

    def test_trivia_only_ctor_routes(self):
        # A ctor whose body is only trivia (no field inits) -- the body is
        # non-empty but emits nothing, so ` {\n    }` with no init list.
        ctor = _lower_ctor(
            _PRELUDE
            + "class E:\n    def __init__(self) -> None:\n        pass\n",
            "E")
        assert ctor is not None
        assert ctor.mil_inits == ()
        assert _ctor_tail(ctor) == " {\n    }\n"

    def test_trivia_comment_loc_asymmetry(self):
        # The byte-identity hinge: `pass` keeps its source loc (the AST emits its
        # `// pass` source comment), a docstring lowers with loc=None (the AST emits
        # NO comment for a docstring -- its simple-stmt code is None).
        pass_ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n        pass\n",
            "P")
        assert pass_ctor.body[0].loc is not None
        doc_ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + '        """doc"""\n        self.x = x\n',
            "P")
        assert doc_ctor.body[0].loc is None

    def test_non_init_print_body_routes(self):
        # A print() body statement is an eligible expression statement, so a ctor
        # with a hoistable field init + a print demotes cleanly: the field init
        # hoists to the MIL, the print rides the body (byte-identical ctor tail).
        ctor = _lower_ctor(
            _PRELUDE
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + "        self.x = x\n        print(x)\n",
            "P")
        assert ctor is not None
        assert len(ctor.mil_inits) == 1  # self.x = x hoisted to the MIL
        assert _ctor_tail(ctor) == ' : x(x) {\n        std::cout << x << "\\n";\n    }\n'

    def test_non_init_method_call_body_is_ineligible(self):
        # A non-init body statement outside the eligible expr-statement set (a
        # method call is a TpyMethodCall, not a routable TpyCall) keeps the ctor
        # on the AST path.
        ctor = _lower_ctor(
            _PRELUDE
            + "class C:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + "        self.x = x\n        self.reset()\n"
            + "    def reset(self) -> None:\n        self.x = 0\n",
            "C")
        assert ctor is None

    def test_body_local_demotion_routes(self):
        # M3c-demotion: a field init whose RHS reads a body-local can't hoist (the
        # local isn't in scope at MIL time), so it demotes into the body alongside
        # the local's var-decl. No MIL; the body holds both statements.
        ctor = _lower_ctor(
            _PRELUDE
            + "class W:\n    size: Int32\n"
            + "    def __init__(self, w: Int32, h: Int32):\n"
            + "        area = w * h\n        self.size = area\n",
            "W")
        assert ctor is not None
        assert ctor.mil_inits == ()
        assert len(ctor.body) == 2  # the var-decl + the demoted field write
        assert _ctor_tail(ctor) == (
            " {\n        int32_t area = (::tpy::mul_check<int32_t>(w, h));\n"
            "        this->size = area;\n    }\n")

    def test_chain_break_demotes_hoistable_init(self):
        # M3c-demotion: a non-init statement breaks the hoist chain, so a *hoistable*
        # field init after it must demote (the MIL runs before the body -- hoisting
        # would reorder it past the chain-breaking statement). `self.a` (before the
        # break) hoists; `self.b` (after) demotes.
        ctor = _lower_ctor(
            _PRELUDE
            + "def helper(x: Int32) -> Int32:\n    return x\n"
            + "class W:\n    a: Int32\n    b: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + "        self.a = x\n        z = helper(y)\n        self.b = z\n",
            "W")
        assert ctor is not None
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["a"]
        assert _ctor_tail(ctor) == (
            " : a(x) {\n        int32_t z = helper(y);\n"
            "        this->b = z;\n    }\n")

    def test_demotion_byte_identical(self):
        # End-to-end byte-identity for the demotion shapes (body-local demote +
        # chain-break demote of a hoistable init) through the THIR seam vs the AST.
        src = (
            _PRELUDE
            + "def helper(x: Int32) -> Int32:\n    return x\n"
            + "class W:\n    a: Int32\n    b: Int32\n    c: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + "        self.a = x\n        t = helper(y)\n"
            + "        self.b = t\n        self.c = x\n"
            + "def main():\n    w = W(1, 2)\n    print(w.a + w.b + w.c)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_demotion_cascade_byte_identical(self):
        # A demoted init breaks the chain, so a subsequent otherwise-hoistable init
        # also demotes (cascade). All three end up in the body in source order.
        src = (
            _PRELUDE
            + "class W:\n    a: Int32\n    b: Int32\n"
            + "    def __init__(self, x: Int32):\n"
            + "        n = x + 1\n        self.a = n\n        self.b = x\n"
            + "def main():\n    w = W(5)\n    print(w.a + w.b)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_record_field_demotion_is_ineligible(self):
        # A demoted *record*-field write is not a body-eligible statement
        # (`_stmt_eligible` admits only scalar / Optional field writes), so the ctor
        # stays on the AST path. Byte-safe; bounds the M3c-demotion slice.
        ctor = _lower_ctor(
            self._INNER
            + "class W:\n    rec: Inner\n"
            + "    def __init__(self, v: Int32):\n"
            + "        m = Inner(v)\n        self.rec = m\n",
            "W")
        assert ctor is None

    def test_single_base_super_init_routes(self):
        # M3d-1: a single-F1-base ctor routes -- `super().__init__(a)` lowers to a
        # `Base(a)` base initializer prepended to the MIL; own fields hoist as usual.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32, b: Int32):\n"
            + "        super().__init__(a)\n        self.b = b\n")
        ctor = _lower_ctor(src, "Derived")
        assert ctor is not None
        assert [bi.base_cpp for bi in ctor.base_inits] == ["Base"]
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["b"]
        assert _ctor_tail(ctor) == " : Base(a), b(b) {}\n"
        assert _lower_ctor(src, "Base") is not None  # the flat base routes too

    def test_inherited_field_write_routes(self):
        # M3d: a direct inherited-field write (`self.a = ...`, `a` owned by the base)
        # goes to the BODY (the base ctor owns the MIL slot), without breaking the hoist
        # chain -- so the own field `b` still hoists. `this->a = a;` lands in the body.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32, b: Int32):\n"
            + "        super().__init__(a)\n        self.a = a\n        self.b = b\n")
        ctor = _lower_ctor(src, "Derived")
        assert ctor is not None
        assert [bi.base_cpp for bi in ctor.base_inits] == ["Base"]
        assert [mi.field_cpp for mi in ctor.mil_inits] == ["b"]  # own field hoists
        assert _ctor_tail(ctor) == " : Base(a), b(b) {\n        this->a = a;\n    }\n"

    def test_init_reading_inherited_field_demotes(self):
        # An own-field init reading an inherited field written earlier in the body must
        # demote (the MIL runs before that write) -- the expr_reads_self_field trigger.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        super().__init__(a)\n        self.a = a\n        self.b = self.a\n")
        ctor = _lower_ctor(src, "Derived")
        assert ctor is not None
        assert ctor.mil_inits == ()  # b demotes (reads self.a written in the body)
        assert _ctor_tail(ctor) == (
            " : Base(a) {\n        this->a = a;\n        this->b = this->a;\n    }\n")

    def test_multi_base_routes(self):
        # M3d: multiple bases route -- each explicit `BaseN.__init__(self, ...)` lowers
        # to a base initializer, sorted by parent declaration order (A before B).
        src = (
            _PRELUDE
            + "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
            + "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
            + "class C(A, B):\n    z: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32, z: Int32):\n"
            + "        A.__init__(self, x)\n        B.__init__(self, y)\n        self.z = z\n")
        ctor = _lower_ctor(src, "C")
        assert ctor is not None
        assert [bi.base_cpp for bi in ctor.base_inits] == ["A", "B"]
        assert _ctor_tail(ctor) == " : A(x), B(y), z(z) {}\n"

    def test_multi_base_byte_identical(self):
        src = (
            _PRELUDE
            + "class A:\n    x: Int32\n    def __init__(self, x: Int32):\n        self.x = x\n"
            + "class B:\n    y: Int32\n    def __init__(self, y: Int32):\n        self.y = y\n"
            + "class C(A, B):\n    z: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32, z: Int32):\n"
            + "        A.__init__(self, x)\n        B.__init__(self, y)\n        self.z = z\n"
            + "def main():\n    c = C(1, 2, 3)\n    print(c.x + c.y + c.z)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_single_base_byte_identical(self):
        # End-to-end byte-identity for the single-base super-init ctor through the
        # THIR seam vs the AST path (the derived signature is AST-emitted; only the
        # base-init + field MIL tail routes).
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32, b: Int32):\n"
            + "        super().__init__(a)\n        self.b = b\n"
            + "def main():\n    d = Derived(1, 2)\n    print(d.a + d.b)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_non_f1_base_is_ineligible(self):
        # A non-F1 base (here generic) keeps the derived ctor on the AST path -- its
        # `to_cpp()` would not match the bare render. Guards the `_f1_record(parent)`
        # gate (no corpus byte-diff covers it -- the routed ctors all have F1 bases).
        src = (
            _PRELUDE
            + "class Box[T]:\n    v: T\n    def __init__(self, v: T):\n        self.v = v\n"
            + "class IntBox(Box[Int32]):\n    n: Int32\n"
            + "    def __init__(self, v: Int32, n: Int32):\n"
            + "        super().__init__(v)\n        self.n = n\n")
        assert _lower_ctor(src, "IntBox") is None

    def test_field_read_optional_byte_identical(self):
        # A param field-read into an Optional[record] field (`self.opt = b.inner`)
        # constructs the optional directly -- the TpyFieldAccess arm of
        # `_is_record_value_source` flowing into the Optional `else` (no ptr_to_optional).
        src = (
            self._INNER
            + "class Box:\n    inner: Inner\n"
            + "    def __init__(self, inner: Inner):\n        self.inner = inner\n"
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, b: Box):\n        self.opt = b.inner\n"
            + "def main():\n    h = H(Box(Inner(5)))\n    print(0)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_optional_own_param_sibling_byte_identical(self):
        # The `Optional[Own[Inner]]` own-optional peel shape emits identically end-to-end
        # (the sibling `Own[Inner | None]` is `test_own_optional_param_byte_identical`).
        src = (
            "from typing import Optional\n" + self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Optional[Own[Inner]]):\n        self.opt = m\n"
            + "def main():\n    h = H(Inner(4))\n    print(0)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_inherited_field_write_byte_identical(self):
        # End-to-end byte-identity for the M3d inherited-field-write body branch and
        # the demote-reads-inherited-field path through the THIR seam vs the AST.
        src = (
            _PRELUDE
            + "class Base:\n    a: Int32\n"
            + "    def __init__(self, a: Int32):\n        self.a = a\n"
            + "class Derived(Base):\n    b: Int32\n"
            + "    def __init__(self, a: Int32):\n"
            + "        super().__init__(a)\n        self.a = a\n        self.b = self.a\n"
            + "def main():\n    d = Derived(7)\n    print(d.a + d.b)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_non_scalar_field_is_ineligible(self):
        # A str field is M3b+ form work, not M3a scalar.
        ctor = _lower_ctor(
            _PRELUDE
            + "class S:\n    name: str\n"
            + "    def __init__(self, name: str):\n        self.name = name\n",
            "S")
        assert ctor is None

    def test_bigint_field_is_ineligible(self):
        # Bare `int` -> BigInt is outside the eligible-scalar set (as everywhere in
        # the THIR slice), so a BigInt-field ctor stays on the AST path.
        ctor = _lower_ctor(
            "class C:\n    n: int\n"
            + "    def __init__(self, n: int):\n        self.n = n\n",
            "C")
        assert ctor is None

    def test_ineligible_param_with_scalar_fields_is_ineligible(self):
        # The PARAM gate must reject a ctor whose fields are all scalar but a param
        # is non-eligible: it would otherwise emit `: n(n) {}` byte-identically, so the
        # corpus byte-diff cannot guard a regression here -- only this unit test can.
        # (An `Optional` param stays on the AST path; `list[scalar]` is now admitted.)
        ctor = _lower_ctor(
            _PRELUDE
            + "class C:\n    n: Int32\n"
            + "    def __init__(self, n: Int32, x: Int32 | None):\n"
            + "        self.n = n\n",
            "C")
        assert ctor is None

    def _hpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, _ = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp

    def test_ctor_byte_identical(self):
        # End-to-end: the ctor MIL tail emits identically through the THIR seam
        # (generator -> records -> emit) and the AST path. The ctor lives in the
        # .hpp (inline in the struct), so compare that half.
        src = (
            _PRELUDE
            + "class Point:\n    x: Int32\n    y: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + "        self.x = x\n        self.y = y\n"
            + "def main():\n    p = Point(1, 2)\n    print(p.x + p.y)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    # --- M3b: record / Optional[record] member-init-list fields ---

    _INNER = (
        "from tpy import Int32\n"
        "class Inner:\n    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n")

    def test_optional_field_from_optional_param_routes(self):
        # The M3 cell: an Optional[record] field <- Optional[record] borrow param
        # lifts via ptr_to_optional (the F2b conversion, now in MIL position).
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Inner | None):\n        self.opt = m\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(::tpy::ptr_to_optional(m)) {}\n"

    def test_optional_field_none_routes(self):
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self):\n        self.opt = None\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(std::nullopt) {}\n"

    def test_record_field_copy_routes(self):
        # A plain record field <- non-own record param: an implicit MIL copy.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, p: Inner):\n        self.rec = p\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(p) {}\n"

    def test_record_field_explicit_copy_unwraps(self):
        # `copy(p)` is the explicit field-copy acknowledgment; it unwraps to the same
        # `rec(p)` direct-init as the bare `self.rec = p` (the MIL copies implicitly).
        ctor = _lower_ctor(
            "from tpy import Int32, copy\n"
            + "class Inner:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, p: Inner):\n        self.rec = copy(p)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(p) {}\n"

    _OWN_INNER = (
        "from tpy import Int32, Own\n"
        "class Inner:\n    v: Int32\n"
        "    def __init__(self, v: Int32):\n        self.v = v\n")

    def test_own_record_param_record_field_moves(self):
        # M3b-move: an Own[record] param at last use moves into a record field
        # (the common ownership-taking ctor) -- `rec(std::move(p))`.
        ctor = _lower_ctor(
            self._OWN_INNER
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, p: Own[Inner]):\n        self.rec = p\n",
            "H")
        assert ctor is not None
        assert ctor.mil_inits[0].move
        assert _ctor_tail(ctor) == " : rec(std::move(p)) {}\n"

    def test_own_record_param_optional_field_moves(self):
        # M3b-move: an Own[record] param moves into an Optional[record] field --
        # `opt(std::move(p))`, NOT ptr_to_optional (an own source skips that arm).
        ctor = _lower_ctor(
            self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, p: Own[Inner]):\n        self.opt = p\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(std::move(p)) {}\n"

    def test_own_optional_param_moves(self):
        # M3b-rvalue: an own-optional param (`Own[Inner | None]`) moves into an
        # Optional[record] field via the move arm -- `opt(std::move(m))`, NOT
        # ptr_to_optional (the own source skips that arm, as for a plain Own param).
        ctor = _lower_ctor(
            self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Own[Inner | None]):\n        self.opt = m\n",
            "H")
        assert ctor is not None
        assert ctor.mil_inits[0].move
        assert _ctor_tail(ctor) == " : opt(std::move(m)) {}\n"

    def test_optional_own_param_moves(self):
        # The sibling own-optional shape `Optional[Own[Inner]]` peels differently but
        # emits the same move MIL.
        ctor = _lower_ctor(
            "from typing import Optional\n" + self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Optional[Own[Inner]]):\n        self.opt = m\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(std::move(m)) {}\n"

    def test_ctor_call_record_source_routes(self):
        # M3b-rvalue: an rvalue ctor-call source (`self.rec = Inner(v)`) constructs the
        # record field directly from the prvalue -- `rec(Inner(v))`.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, v: Int32):\n        self.rec = Inner(v)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(Inner(v)) {}\n"

    def test_ctor_call_optional_source_routes(self):
        # M3b-rvalue: an rvalue ctor-call into an Optional[record] field constructs the
        # optional directly from the prvalue -- `opt(Inner(v))`, NOT ptr_to_optional.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, v: Int32):\n        self.opt = Inner(v)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(Inner(v)) {}\n"

    def test_record_field_from_param_field_read_routes(self):
        # M3b-rvalue: a field-read off a param record (`self.rec = b.inner`) copies the
        # field into the record member -- `rec(b.inner)`.
        ctor = _lower_ctor(
            self._INNER
            + "class Box:\n    inner: Inner\n"
            + "    def __init__(self, inner: Inner):\n        self.inner = inner\n"
            + "class H:\n    rec: Inner\n"
            + "    def __init__(self, b: Box):\n        self.rec = b.inner\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : rec(b.inner) {}\n"

    def test_self_field_record_read_source_is_ineligible(self):
        # A `self.<record field>` read source is ordering-sensitive in the MIL (the
        # pointee may be uninitialized) -- excluded; the ctor falls to the AST path.
        ctor = _lower_ctor(
            self._INNER
            + "class H:\n    a: Inner\n    b: Inner\n"
            + "    def __init__(self, p: Inner):\n"
            + "        self.a = p\n        self.b = self.a\n",
            "H")
        assert ctor is None

    def test_optional_field_byte_identical(self):
        # End-to-end byte-identity for the ptr_to_optional + None MIL cases, mixed
        # with a scalar field, through the THIR seam vs the AST path.
        src = (
            self._INNER
            + "class H:\n    n: Int32\n    opt: Inner | None\n"
            + "    def __init__(self, n: Int32, m: Inner | None):\n"
            + "        self.n = n\n        self.opt = m\n"
            + "def main():\n    h = H(5, None)\n    print(h.n)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_own_param_move_byte_identical(self):
        # The move arm's load-bearing contract: the own-param std::move MIL (into
        # both a record field and an Optional field) emits identically through THIR
        # and the AST path.
        src = (
            "from tpy import Int32, Own\n"
            + "class Inner:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class H:\n    inner: Inner\n    opt: Inner | None\n"
            + "    def __init__(self, a: Own[Inner], b: Own[Inner]):\n"
            + "        self.inner = a\n        self.opt = b\n"
            + "def main():\n    h = H(Inner(1), Inner(2))\n    print(h.inner.v)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_rvalue_source_byte_identical(self):
        # End-to-end byte-identity for the M3b-rvalue shapes: a ctor-call source into
        # a record field and into an Optional field, and a param field-read into a
        # record field, all through the THIR seam vs the AST path.
        src = (
            self._INNER
            + "class Box:\n    inner: Inner\n"
            + "    def __init__(self, inner: Inner):\n        self.inner = inner\n"
            + "class H:\n    rec: Inner\n    opt: Inner | None\n    cp: Inner\n"
            + "    def __init__(self, v: Int32, b: Box):\n"
            + "        self.rec = Inner(v)\n        self.opt = Inner(v)\n"
            + "        self.cp = b.inner\n"
            + "def main():\n    h = H(7, Box(Inner(3)))\n    print(h.rec.v)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_own_optional_param_byte_identical(self):
        # An own-optional param (`Own[Inner | None]`) moving into an Optional field
        # emits identically through THIR and the AST path.
        src = (
            self._OWN_INNER
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Own[Inner | None]):\n        self.opt = m\n"
            + "def main():\n    h = H(Inner(4))\n    print(0)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_trivia_body_byte_identical(self):
        # M3c-trivia: docstring + pass non-init bodies emit the ` {\n    }` braces
        # identically through THIR and the AST path (the byte-diff with source
        # comments ON further validates the pass/docstring comment asymmetry).
        src = (
            _PRELUDE
            + "class P:\n    x: Int32\n    y: Int32\n"
            + "    def __init__(self, x: Int32, y: Int32):\n"
            + '        """A point."""\n        self.x = x\n        self.y = y\n        pass\n'
            + "def main():\n    p = P(1, 2)\n    print(p.x + p.y)\nmain()\n")
        assert self._hpp(src, thir=True) == self._hpp(src, thir=False)

    def test_optional_copy_source_routes(self):
        # M3b-rvalue: `copy()` is unwrapped before the Optional check (matching the
        # record arm, `test_record_field_explicit_copy_unwraps`), so a copy()-wrapped
        # pointer-repr Optional borrow source lifts via ptr_to_optional just like the
        # bare `self.opt = m` -- `opt(::tpy::ptr_to_optional(m))`.
        ctor = _lower_ctor(
            "from tpy import Int32, copy\n"
            + "class Inner:\n    v: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.v = v\n"
            + "class H:\n    opt: Inner | None\n"
            + "    def __init__(self, m: Inner | None):\n        self.opt = copy(m)\n",
            "H")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : opt(::tpy::ptr_to_optional(m)) {}\n"


# --- Method-call sites: container mutation/read calls (THIRMethodCall) ---


class TestMethodCall:
    def test_native_member_routes(self):
        # `xs.append(v)` -- @native member rename: push_back, no free-function symbol.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32], n: Int32) -> None:\n    xs.append(n)\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRExprStmt)
        mc = stmt.expr
        assert isinstance(mc, THIRMethodCall) and mc.method_cpp == "push_back"
        assert mc.native_function_name is None and mc.cpp_template is None
        assert isinstance(mc.receiver, THIRName) and mc.receiver.name == "xs"

    def test_native_function_routes(self):
        # `xs.pop()` -- @native(..., function=True): the receiver becomes the first arg.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> None:\n    xs.pop()\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall)
        assert mc.native_function_name == "tpy::pop_back"

    def test_cpp_template_routes(self):
        # `xs.sort()` -- @cpp_template body carried for expansion at emit.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> None:\n    xs.sort()\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall)
        assert mc.cpp_template == "std::stable_sort({self}.begin(), {self}.end())"

    def test_plain_member_routes(self):
        # `xs.clear()` -- bare @native member (no rename): the escaped source name.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> None:\n    xs.clear()\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall) and mc.method_cpp == "clear"
        assert mc.native_function_name is None and mc.cpp_template is None

    def test_value_position_routes(self):
        # `a = xs.pop()` -- a scalar-returning method call in a decl init.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> Int32:\n"
            + "    a = xs.pop()\n    return a + xs.pop()\n")
        fn = _fn(thir, "f")
        assert isinstance(fn.body[0], THIRVarDecl)
        assert isinstance(fn.body[0].init, THIRMethodCall)

    def test_dict_readonly_key_slot_routes(self):
        # `d.pop(k)` -- a readonly[K] param slot unwraps to the scalar key.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[Int32, Int32], k: Int32) -> None:\n    d.pop(k)\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall)
        assert mc.native_function_name == "tpy::dict_pop"

    def test_void_call_value_position_ineligible(self):
        # A void method call is discard-only: statement position routes, but a void
        # `return xs.clear()` (None-typed) must not slip through the value gate.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> None:\n    return xs.clear()\n")
        assert _fn(thir, "f") is None

    def test_record_element_arg_ineligible(self):
        # A record arg crosses an ownership boundary (Own move / borrow lift) -> AST.
        thir = _lower(
            "from tpy import Int32\n"
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n"
            + "def f(xs: list[P], p: P) -> None:\n    xs.append(p)\n")
        assert _fn(thir, "f") is None

    def test_str_arg_ineligible(self):
        # A str arg into an Own[str] slot takes the owned-copy conversion -> AST.
        thir = _lower(
            "from tpy import Int32\n"
            + "def f(xs: list[str], s: str) -> None:\n    xs.append(s)\n")
        assert _fn(thir, "f") is None

    def test_field_receiver_ineligible(self):
        # `self.items.append(...)` -- a non-name receiver rides a later cell.
        thir = _lower(
            "from tpy import Int32\n"
            + "class H:\n    items: list[Int32]\n"
            + "    def __init__(self):\n        self.items = []\n"
            + "    def add(self, n: Int32) -> None:\n        self.items.append(n)\n")
        assert _fn(thir, "add") is None

    def test_set_receiver_ineligible(self):
        # set params are not in the admitted container family (ride a later cell).
        thir = _lower(
            _PRELUDE
            + "def f(s: set[Int32], n: Int32) -> None:\n    s.add(n)\n")
        assert _fn(thir, "f") is None

    def test_user_record_method_ineligible(self):
        # A user-record method call takes the record emit path (temps, TypeParamRef
        # handling) -- a different frontier.
        thir = _lower(
            "from tpy import Int32\n"
            + "class C:\n    v: Int32\n"
            + "    def __init__(self):\n        self.v = 0\n"
            + "    def bump(self) -> None:\n        self.v += 1\n"
            + "def f(c: C) -> None:\n    c.bump()\n")
        assert _fn(thir, "f") is None

    def test_negative_literal_arg_routes(self):
        # A `-1` arg folds to a plain literal (the AST's _gen_unaryop
        # literal-negation branch), rendered bare into the arg slot (behind
        # the int_literal_to_fixed_int passthrough coerce).
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[Int32, Int32], k: Int32) -> Int32:\n"
            + "    return d.pop(k, -1)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        arg = fn.body[0].value.args[1]
        assert isinstance(arg, THIRCoerce)
        assert isinstance(arg.expr, THIRLiteral) and arg.expr.value == -1


class TestMethodCallEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def grow(xs: list[Int32], n: Int32) -> None:\n"
        + "    xs.append(n)\n"
        + "    xs.insert(0, 7)\n"
        + "    xs.sort()\n"
        + "    xs.clear()\n"
        + "def take(xs: list[Int32]) -> Int32:\n"
        + "    a = xs.pop()\n    return a + xs.pop()\n"
        + "def dtake(d: dict[Int32, Int32], k: Int32) -> Int32:\n"
        + "    d.pop(k)\n    return d.pop(k, 0)\n"
        + "def main():\n"
        + "    xs = [2, 1]\n"
        + "    grow(xs, 9)\n    print(take(xs))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "xs.push_back(n);" in cpp                      # @native member rename
        assert "::tpy::list_insert(xs, 0, 7);" in cpp         # @native free function
        assert "std::stable_sort(xs.begin(), xs.end());" in cpp  # @cpp_template
        assert "xs.clear();" in cpp                           # plain member
        assert "int32_t a = ::tpy::pop_back(xs);" in cpp      # value position
        assert "return ::tpy::dict_pop_default(d, k, 0);" in cpp


# --- Container-literal locals (THIRContainerLiteral) ---


class TestContainerLiteralLocal:
    def test_mutated_list_literal_routes_as_vector(self):
        # Mutation keeps the literal a list (sema's PendingListType resolution).
        thir = _lower(
            _PRELUDE
            + "def f() -> None:\n    xs = [1, 2]\n    xs.append(3)\n")
        fn = _fn(thir, "f")
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        lit = decl.init
        assert isinstance(lit, THIRContainerLiteral) and len(lit.elements) == 2
        assert decl.resolved_type.name == "list"

    def test_readonly_list_literal_routes_as_array(self):
        # No mutation -> sema demotes the literal local to Array[T, N].
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    ys = [1, 2, 3]\n    return ys[1]\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl.init, THIRContainerLiteral)
        assert decl.resolved_type.name == "Array"

    def test_empty_annotated_list_routes(self):
        thir = _lower(
            _PRELUDE
            + "def f(n: Int32) -> Int32:\n"
            + "    zs: list[Int32] = []\n    zs.append(n)\n    return len(zs)\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl.init, THIRContainerLiteral)
        assert decl.init.elements == ()

    def test_dict_literal_routes(self):
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    d = {1: 10, 2: 20}\n    return d[1]\n")
        decl = _fn(thir, "f").body[0]
        lit = decl.init
        assert isinstance(lit, THIRContainerLiteral)
        assert len(lit.elements) == 2 and len(lit.values) == 2

    def test_set_literal_routes(self):
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    s = {4, 5}\n    return len(s)\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl.init, THIRContainerLiteral)
        assert decl.init.values == ()

    def test_reassigned_container_local_ineligible(self):
        # A reassigned container local is a POINTER-LOCAL on the AST path
        # (aliasing rebind, `a = &(b)`); the plain value decl would silently
        # copy -- the whole function stays AST.
        thir = _lower(
            _PRELUDE
            + "def f() -> None:\n"
            + "    a = [1, 2]\n    b = [3, 4]\n    a = b\n    a.append(5)\n")
        assert _fn(thir, "f") is None

    def test_record_element_literal_ineligible(self):
        thir = _lower(
            "from tpy import Int32\n"
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n"
            + "def f() -> None:\n    ps = [P(1), P(2)]\n    ps.pop()\n")
        assert _fn(thir, "f") is None

    def test_nested_list_literal_ineligible(self):
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    m = [[1, 2], [3]]\n    return len(m)\n")
        assert _fn(thir, "f") is None

    def test_list_repeat_ineligible(self):
        # `[0] * n` is a TpyListRepeat, a different node/emit -> AST path.
        thir = _lower(
            _PRELUDE
            + "def f(n: Int32) -> Int32:\n    xs = [0] * n\n    return len(xs)\n")
        assert _fn(thir, "f") is None

    def test_container_alias_decl_ineligible(self):
        # `ys = xs` (container alias) is not a literal init -> AST path.
        thir = _lower(
            _PRELUDE
            + "def f() -> None:\n    xs = [1]\n    ys = xs\n    ys.append(2)\n")
        assert _fn(thir, "f") is None

    def test_literal_operand_binop_ineligible(self):
        # `ys[0] + ys[2]` -- both operands IntLiteral-typed non-names: the AST's
        # fixed-target literal-operand branch emits WITHOUT the paren wrap
        # (position-dependent), so the shape stays on the AST path.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    ys = [1, 2, 3]\n    return ys[0] + ys[2]\n")
        assert _fn(thir, "f") is None


class TestContainerLiteralLocalEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def f() -> Int32:\n"
        + "    xs = [1, 2]\n    xs.append(3)\n"
        + "    ys = [10, 20, 30]\n"
        + "    zs: list[Int32] = []\n    zs.append(ys[1])\n"
        + "    d = {1: 100}\n"
        + "    e: dict[Int32, Int32] = {}\n"
        + "    s = {7, 8}\n"
        + "    total = len(xs) + len(zs)\n"
        + "    total = total + len(d) + len(e) + len(s)\n"
        + "    for v in ys:\n        total = total + v\n"
        + "    return total\n"
        + "def main():\n    print(f())\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_families(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::vector<int32_t> xs = {1, 2};" in cpp             # mutated -> vector
        assert "std::array<int32_t, 3> ys = {10, 20, 30};" in cpp     # read-only -> array
        assert "std::vector<int32_t> zs = std::vector<int32_t>{};" in cpp  # empty list
        assert ("::tpy::ordered_map<int32_t, int32_t> d = "
                "::tpy::ordered_map<int32_t, int32_t>({{1, 100}});") in cpp
        assert ("::tpy::ordered_map<int32_t, int32_t> e = "
                "::tpy::ordered_map<int32_t, int32_t>();") in cpp     # empty dict
        assert "::tpy::ordered_set<int32_t> s = ::tpy::ordered_set<int32_t>({7, 8});" in cpp

    def test_bool_float_elements_route(self):
        # The non-int eligible scalars as literal elements: bool and double
        # float lists route (and stay byte-identical) like the Int32 ones.
        src = (
            _PRELUDE
            + "def f() -> None:\n"
            + "    flags = [True, False]\n    flags.append(True)\n"
            + "    vals = [1.5, 2.5]\n    vals.append(3.5)\n"
            + "    print(len(flags) + len(vals))\n"
            + "def main():\n    f()\nmain()\n"
        )
        thir = _lower(src)
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].init, THIRContainerLiteral)
        assert isinstance(fn.body[2].init, THIRContainerLiteral)
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


# --- Container call args (the pass-through arg widening) ---


class TestContainerCallArgs:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def test_free_call_container_arg_routes(self):
        thir = _lower(
            _PRELUDE
            + "def use(xs: list[Int32]) -> Int32:\n    return len(xs)\n"
            + "def f(xs: list[Int32]) -> Int32:\n    return use(xs)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[0].value
        assert isinstance(call, THIRCall) and isinstance(call.args[0], THIRName)

    def test_literal_local_arg_routes(self):
        # The composition the widening exists for: a literal local passed on.
        thir = _lower(
            _PRELUDE
            + "def use(xs: list[Int32]) -> Int32:\n    return len(xs)\n"
            + "def f() -> Int32:\n    ys = [1, 2]\n    return use(ys)\n")
        assert _fn(thir, "f") is not None

    def test_dict_arg_stmt_position_routes(self):
        thir = _lower(
            _PRELUDE
            + "def wipe(d: dict[Int32, Int32]) -> None:\n    d.clear()\n"
            + "def f(d: dict[Int32, Int32]) -> None:\n    wipe(d)\n")
        assert isinstance(_fn(thir, "f").body[0], THIRExprStmt)

    def test_own_container_param_ineligible(self):
        # An Own[list] slot auto-moves at last use (`f(std::move(xs))`) -> AST.
        thir = _lower(
            "from tpy import Int32, Own\n"
            + "def consume(xs: Own[list[Int32]]) -> Int32:\n    return len(xs)\n"
            + "def f() -> Int32:\n    zs = [1]\n    return consume(zs)\n")
        assert _fn(thir, "f") is None

    def test_span_param_ineligible(self):
        # A Span slot converts (`::tpy::as_mut_span(xs)`) -> AST.
        thir = _lower(
            "from tpy import Int32, Span\n"
            + "def use_span(sp: Span[Int32]) -> Int32:\n    return len(sp)\n"
            + "def f(xs: list[Int32]) -> Int32:\n    return use_span(xs)\n")
        assert _fn(thir, "f") is None

    def test_protocol_param_method_arg_ineligible(self):
        # list.extend(other: Iterable[Own[T]]) -- a protocol slot (adapter /
        # consuming-iteration handling) -> AST.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32], ys: list[Int32]) -> None:\n    xs.extend(ys)\n")
        assert _fn(thir, "f") is None

    def test_byte_identical(self):
        src = (
            _PRELUDE
            + "def use(xs: list[Int32]) -> Int32:\n    return len(xs)\n"
            + "def grow(xs: list[Int32], n: Int32) -> None:\n    xs.append(n)\n"
            + "def f() -> Int32:\n"
            + "    ys = [1, 2]\n    grow(ys, 3)\n    return use(ys)\n"
            + "def main():\n    print(f())\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_method_call_container_arg_routes(self):
        # The _method_call_eligible half of the widening: d.update(e) -- the
        # param is dict[K, Own[V]] (Own wraps only the VALUE type arg, so the
        # slot is still a non-Own concrete dict).
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[Int32, Int32], e: dict[Int32, Int32]) -> None:\n"
            + "    d.update(e)\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRExprStmt)
        mc = stmt.expr
        assert isinstance(mc, THIRMethodCall) and isinstance(mc.args[0], THIRName)

    def test_array_arg_routes(self):
        # Array[T, N] as the pass-through family -- corpus-vacuous (every
        # corpus Array-arg call site is blocked by another gate), so this unit
        # is the byte-diff's only guard for the shape.
        src = (
            "from tpy import Int32, Array\n"
            + "def use_arr(a: Array[Int32, 2]) -> Int32:\n    return len(a)\n"
            + "def f() -> Int32:\n    ys = [1, 2]\n    return use_arr(ys)\n"
            + "def main():\n    print(f())\nmain()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


# --- Bare numeric-literal call args + negated int literals (increment 45) ---

_NUMLIT_PRELUDE = "from tpy import Int32, Int64, Float32, Float64\n"


class TestNumericLiteralArgs:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def test_float_literal_arg_routes(self):
        # A bare float literal (FloatLiteralType) into a double slot renders
        # repr(v) bare on both paths.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float64) -> Float64:\n    return x\n"
            + "def g() -> Float64:\n    return f(1.5)\n")
        fn = _fn(thir, "g")
        assert fn is not None
        call = fn.body[0].value
        assert isinstance(call.args[0], THIRLiteral) and call.args[0].value == 1.5

    def test_float32_slot_ineligible(self):
        # A float literal into a Float32 slot arrives float_literal_to_float32
        # coerce-wrapped (the `f`-suffix render) -> AST path.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float32) -> Float32:\n    return x\n"
            + "def g() -> None:\n    v = f(1.5)\n    print(1)\n")
        assert _fn(thir, "g") is None

    def test_inf_literal_arg_ineligible(self):
        # `1e400` parses to inf; repr(inf) is not valid C++ -> AST path.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float64) -> Float64:\n    return x\n"
            + "def g() -> None:\n    v = f(1e400)\n    print(1)\n")
        assert _fn(thir, "g") is None

    def test_bigint_slot_literal_ineligible(self):
        # An int literal into a BigInt slot wraps `::tpy::BigInt(3)` -> AST.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: int) -> int:\n    return x\n"
            + "def g() -> None:\n    v = f(3)\n    print(1)\n")
        assert _fn(thir, "g") is None

    def test_negated_int_literal_positions_route(self):
        # The fold covers every admitted literal position: decl init, call arg
        # (behind the int_literal coerce), compare operand, subscript index,
        # slice bound, range bound, print arg.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Int32) -> Int32:\n    return x\n"
            + "def g(xs: list[Int32], s: str) -> Int32:\n"
            + "    a = -3\n"
            + "    print(f(-3), xs[-1], s[1:-1], -7)\n"
            + "    for i in range(-3, 3):\n        a = a + i\n"
            + "    if a > -10:\n        return a + -2\n"
            + "    return -1\n")
        fn = _fn(thir, "g")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRLiteral) and decl.init.value == -3
        rng = fn.body[2]
        assert isinstance(rng, THIRForRange) and rng.start_is_literal
        # The range bound arrives behind the int_literal passthrough coerce.
        start = rng.start.expr if isinstance(rng.start, THIRCoerce) else rng.start
        assert isinstance(start, THIRLiteral) and start.value == -3

    def test_negated_float_literal_ineligible(self):
        # A negated FLOAT literal is not folded by the AST -- it takes the
        # resolved __neg__ template (`-(1.5)`) -> AST path.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def f(x: Float64) -> Float64:\n    return x\n"
            + "def g() -> None:\n    v = f(-1.5)\n    print(1)\n"
            + "def h() -> None:\n    v = -2.5\n    print(v)\n")
        assert _fn(thir, "g") is None
        assert _fn(thir, "h") is None

    def test_negated_name_ineligible(self):
        # Only a LITERAL operand folds; `-x` needs the __neg__ template.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def g(x: Int32) -> Int32:\n    return -x\n")
        assert _fn(thir, "g") is None

    def test_out_of_range_negation_ineligible(self):
        # A negation outside the +-int32 literal range takes the wide-literal
        # suffix/cast render -> AST path. INT32_MIN itself (-2**31) still folds.
        thir = _lower(
            _NUMLIT_PRELUDE
            + "def g() -> Int64:\n    return -2147483649\n"
            + "def h() -> Int32:\n    return -2147483648\n")
        assert _fn(thir, "g") is None
        assert _fn(thir, "h") is not None

    def test_byte_identical(self):
        src = (
            _NUMLIT_PRELUDE
            + "def f(x: Int32, y: Float64) -> Float64:\n    return y\n"
            + "def sink(y: Float64) -> Float64:\n    return y\n"
            + "def g(xs: list[Int32], s: str) -> Int32:\n"
            + "    a = -3\n"
            + "    print(f(3, 1.5), sink(2.5), xs[-1], s[1:-1], s[-3:], -7)\n"
            + "    for i in range(-3, 3):\n        a = a + i\n"
            + "    if a > -10:\n        return a + -2\n"
            + "    return -1\n"
            + "def main():\n"
            + "    xs = [1, 2, 3]\n"
            + '    print(g(xs, "hello world"))\n'
            + "main()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "g") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)


# --- S1 str slice: str/StrView values (params, locals, print, compare, len,
# --- return, call args); the view->owned copy as an explicit THIRFormConvert ---

class TestStrValues:
    def test_view_local_from_literal(self):
        # Literal init, no owned-forcing usage -> StrView local; the literal
        # (const char[N], VALUE form) is never wrapped.
        thir = _lower('def f() -> None:\n    s = "hi"\n    print(s)\n')
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.resolved_type.to_cpp() == "std::string_view"
        assert isinstance(decl.init, THIRStrLiteral)
        assert decl.init.form is Form.VALUE

    def test_owned_local_init_from_view_wraps(self):
        # v is a view of the param; u is forced owned (aug-assign) -> its init
        # off the view copies explicitly (std::string(v)).
        thir = _lower(
            "def f(a: str) -> None:\n"
            '    v = a\n    u = v\n    u += "z"\n    print(u, v)\n')
        body = _fn(thir, "f").body
        u_decl = body[1]
        assert isinstance(u_decl, THIRVarDecl)
        assert isinstance(u_decl.init, THIRFormConvert)
        assert u_decl.init.form is Form.STORAGE
        assert isinstance(body[2], THIRStrAppend)
        thir = _lower(
            "def g(a: str) -> str:\n    u = a\n    return u\n"
            "def h(a: str) -> None:\n    u = g(a)\n    print(u)\n")
        g = _fn(thir, "g")
        decl = g.body[0]
        # u resolves VIEW (view-safe param source, no owned-forcing usage), so
        # the decl init stays bare; the owned-RETURN wrap fires instead.
        assert decl.resolved_type.to_cpp() == "std::string_view"
        ret = g.body[1]
        assert isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.STORAGE
        # h: u initialized from an owned-returning call -> owned local, bare init.
        h = _fn(thir, "h")
        hdecl = h.body[0]
        assert hdecl.resolved_type.to_cpp() == "std::string"
        assert isinstance(hdecl.init, THIRCall)
        assert hdecl.init.form is Form.STORAGE

    def test_return_param_wraps_return_literal_bare(self):
        thir = _lower(
            'def f(a: str) -> str:\n    return a\n'
            'def g() -> str:\n    return "lit"\n')
        f_ret = _fn(thir, "f").body[0]
        assert isinstance(f_ret.value, THIRFormConvert)
        inner = f_ret.value.value
        assert isinstance(inner, THIRName) and inner.form is Form.BORROW
        g_ret = _fn(thir, "g").body[0]
        assert isinstance(g_ret.value, THIRStrLiteral)  # VALUE form, bare

    def test_reassign_owned_from_view_bare(self):
        # Plain reassignment uses std::string's implicit operator=(string_view)
        # -- the AST emits `t = a;` bare, so no convert node.
        thir = _lower(
            "def f(a: str) -> None:\n"
            '    t = f_src()\n    t = a\n    print(t)\n'
            "def f_src() -> str:\n"
            '    return "x"\n')
        body = _fn(thir, "f").body
        assign = body[1]
        assert isinstance(assign, THIRAssign)
        assert isinstance(assign.value, THIRName)  # no THIRFormConvert

    def test_compare_and_len_route(self):
        thir = _lower(
            'def f(a: str, b: str) -> bool:\n    return a < b\n'
            'def g(a: str) -> Int32:\n    return len(a)\n'
            "from tpy import Int32\n")
        cmp_ret = _fn(thir, "f").body[0]
        assert isinstance(cmp_ret.value, THIRBinOp)
        # str.__lt__ resolves with a `{self} < {0}` template -> `(a < b)`.
        assert _emit_expr(cmp_ret.value) == "(a < b)"
        len_ret = _fn(thir, "g").body[0]
        assert isinstance(len_ret.value, THIRCall)
        assert len_ret.value.native_name == "tpy::__len__"

    def test_str_args_pass_through(self):
        thir = _lower(
            "def greet(name: str) -> None:\n    print(name)\n"
            'def f(a: str) -> None:\n    greet(a)\n    greet("bob")\n')
        f = _fn(thir, "f")
        assert f is not None
        assert isinstance(f.body[0].expr.args[0], THIRName)
        assert isinstance(f.body[1].expr.args[0], THIRStrLiteral)

    def test_aug_assign_routes(self):
        # str += lowers to the in-place append (S3).
        thir = _lower('def f() -> None:\n    t = "x"\n    t += "y"\n    print(t)\n')
        app = _fn(thir, "f").body[1]
        assert isinstance(app, THIRStrAppend)
        assert app.target == "t"
        assert isinstance(app.value, THIRStrLiteral)

    def test_reassigned_str_param_ineligible(self):
        # A reassigned str param hoists an owned copy in the AST prologue.
        thir = _lower('def f(a: str) -> None:\n    a = "other"\n    print(a)\n')
        assert _fn(thir, "f") is None

    def test_concat_routes(self):
        # str + str routes as an owned (STORAGE) String-result binop; the
        # return's string_to_str coercion is an identity passthrough (S3).
        thir = _lower("def f(a: str, b: str) -> str:\n    return a + b\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret.value, THIRCoerce)
        binop = ret.value.expr
        assert isinstance(binop, THIRBinOp)
        assert binop.form is Form.STORAGE
        assert _emit_expr(ret.value) == "(::tpy::str_concat(a, b))"

    def test_cross_type_coercion_view_return_routes(self):
        # `return a` at a StrView return wraps a sema TpyCoerce (str_to_strview)
        # -- identity in every position, a THIRCoerce passthrough that sets
        # BORROW itself (a view result whatever the source form).
        thir = _lower(
            "from tpy import StrView\n"
            "def f(a: str) -> StrView:\n    return a\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCoerce)
        assert ret.coercion_name == "str_to_strview"
        assert ret.form is Form.BORROW
        assert _emit_expr(ret) == "a"

    def test_string_param_ineligible(self):
        # tpy.String (const std::string&) is outside the S1 slice.
        thir = _lower(
            "from tpy import String\n"
            "def f(s: String) -> None:\n    print(s)\n")
        assert _fn(thir, "f") is None

    def test_fstring_routes(self):
        # F6 S2: an f-string is an owned-str expr (STORAGE) -- see TestFString.
        thir = _lower('def f(a: str) -> None:\n    print(f"v={a}")\n')
        assert _fn(thir, "f") is not None


class TestStrValuesEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        "def greet(name: str) -> None:\n"
        '    print("hello", name)\n'
        "def pick(a: str, b: str) -> str:\n"
        "    if a < b:\n        return a\n"
        '    s = "fallback"\n    return s\n'
        "def owned_chain(a: str) -> None:\n"
        "    t = pick(a, a)\n"
        "    t = a\n"
        "    print(t, len(t))\n"
        "def eq_test(a: str) -> bool:\n"
        '    return a == "yes"\n'
        "def main() -> None:\n"
        '    greet("bob")\n'
        '    print(pick("alpha", "beta"))\n'
        '    owned_chain("q")\n'
        '    print(eq_test("yes"))\n'
        "main()\n"
    )

    def test_str_byte_identical(self):
        thir = _lower(self.SRC)
        for name in ("greet", "pick", "owned_chain", "eq_test", "main"):
            assert _fn(thir, name) is not None, name
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_owned_init_wrap_byte_identical(self):
        # The decl-init view->owned copy (std::string u = std::string(v);):
        # forced owned by a later reassign from an owned source.
        src = (
            "def mk() -> str:\n"
            '    return "own"\n'
            "def f(a: str) -> None:\n"
            "    u = a\n"
            "    u = mk()\n"
            "    print(u)\n"
            'f("q")\n'
        )
        thir = _lower(src)
        f = _fn(thir, "f")
        assert f is not None
        decl = f.body[0]
        assert isinstance(decl.init, THIRFormConvert)
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_overload_impl_ineligible(self):
        # An overload IMPL is emitted once per stub with per-stub dead-branch
        # facts; routing the shared body would hijack every specialization
        # (caught by the byte-diff: calls/overload_literal_bool `describe`).
        src = (
            "from typing import Literal, overload\n"
            "@overload\n"
            'def describe(x: Literal[True]) -> str: ...\n'
            "@overload\n"
            'def describe(x: Literal[False]) -> str: ...\n'
            "def describe(x: bool) -> str:\n"
            '    if x:\n        return "yes"\n    return "no"\n')
        thir = _lower(src)
        assert _fn(thir, "describe") is None

    def test_overload_impl_method_ineligible(self):
        # The method arm of the overload-impl rejection: a literal-overloaded
        # METHOD impl is per-stub specialized just like a free function
        # (_gen_literal_specialized_method); the gate reads the owning record's
        # method overload count. No corpus case load-bears this arm.
        src = (
            "from typing import Literal, overload\n"
            "from tpy import Int32\n"
            "class Box:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    @overload\n"
            "    def pick(self, x: Literal[True]) -> Int32: ...\n"
            "    @overload\n"
            "    def pick(self, x: Literal[False]) -> Int32: ...\n"
            "    def pick(self, x: bool) -> Int32:\n"
            "        if x:\n            return self.n\n        return 0\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "pick") is None

    def test_multi_overload_str_literal_arg_ineligible(self):
        # A str-LITERAL arg to a multi-overload callee is pinned to its param's
        # view form (`std::string_view("...")`, _wants_str_literal_pin) -- the
        # bare-literal THIR emit would diverge, so the CALLER stays AST. No
        # corpus case load-bears this reject (overload_str_literal_arg's main
        # is ineligible for other reasons).
        src = (
            "from typing import Literal, overload\n"
            "from tpy import Int32\n"
            "@overload\n"
            'def mode(m: Literal["r"]) -> Int32: ...\n'
            "@overload\n"
            'def mode(m: Literal["w"]) -> Int32: ...\n'
            "def mode(m: str) -> Int32:\n"
            '    if m == "r":\n        return 1\n    return 2\n'
            "def caller() -> Int32:\n"
            '    return mode("r")\n')
        thir = _lower(src)
        assert _fn(thir, "caller") is None


# --- F-strings (F6 S2) ---


class TestFString:
    def test_all_literal_decl(self):
        # A pure-literal f-string is an owned std::string ctor (STORAGE), so an
        # owned local decl-init lands bare (no view->owned wrap).
        thir = _lower('def f() -> str:\n    a = f"hello"\n    return a\n')
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl.init, THIRFString)
        assert decl.init.form is Form.STORAGE
        assert decl.init.parts == ("hello",)
        assert _emit_expr(decl.init) == 'std::string("hello")'

    def test_wrapper_rows(self):
        # str-family args pass through bare; bool/double/int8 carry their
        # Python-compatible wrap templates; wider fixed ints stay bare.
        thir = _lower(
            "from tpy import Int8, Int32, Float64\n"
            "def f(s: str, n: Int32, m: Int8, b: bool, x: Float64) -> str:\n"
            '    return f"{s}|{n}|{m}|{b}|{x}"\n')
        ret = _fn(thir, "f").body[0]
        fstr = ret.value
        assert isinstance(fstr, THIRFString)
        args = [p for p in fstr.parts if isinstance(p, THIRFStringArg)]
        assert [a.wrap for a in args] == [
            None, None, "static_cast<int>({0})", "::tpy::bool_to_str({0})",
            "::tpy::float_to_str({0})"]
        assert _emit_expr(fstr) == (
            'std::format("{}|{}|{}|{}|{}", s, n, static_cast<int>(m), '
            "::tpy::bool_to_str(b), ::tpy::float_to_str(x))")

    def test_literal_args(self):
        # Literal value parts: int resolves through the default int (bare),
        # float/bool carry their wraps, a str literal passes through as
        # const char[N].
        thir = _lower('def f() -> str:\n    return f"{5} {1.5} {True} {\'x\'}"\n')
        fstr = _fn(thir, "f").body[0].value
        assert _emit_expr(fstr) == (
            'std::format("{} {} {} {}", 5, ::tpy::float_to_str(1.5), '
            '::tpy::bool_to_str(true), "x")')

    def test_brace_escaping(self):
        # Literal braces double for std::format; the pure-literal path keeps
        # them raw (std::string, no format machinery).
        thir = _lower(
            "from tpy import Int32\n"
            'def f(n: Int32) -> str:\n    return f"{{{n}}}"\n'
            'def g() -> str:\n    return f"a{{b"\n')
        assert _emit_expr(_fn(thir, "f").body[0].value) == \
            'std::format("{{{}}}", n)'
        assert _emit_expr(_fn(thir, "g").body[0].value) == 'std::string("a{b")'

    def test_embedded_nul(self):
        # NUL in a literal segment: explicit-length std::string / vformat arms
        # (the char* ctor and format's consteval ctor would strlen-truncate).
        thir = _lower(
            "from tpy import Int32\n"
            'def f() -> str:\n    return f"a\\x00b"\n'
            'def g(n: Int32) -> str:\n    return f"a\\x00{n}"\n')
        assert _emit_expr(_fn(thir, "f").body[0].value) == \
            'std::string("a\\000b", 3)'
        assert _emit_expr(_fn(thir, "g").body[0].value) == (
            'std::vformat(std::string_view{"a\\000{}", 4}, '
            "std::make_format_args(n))")

    def test_sinks_compose(self):
        # The owned result feeds the S1 sinks bare: print arg (RAW), call arg
        # into a str param, compare operand.
        thir = _lower(
            "from tpy import Int32\n"
            "def use(s: str) -> Int32:\n    return len(s)\n"
            "def f(a: str, n: Int32) -> bool:\n"
            '    print(f"n={n}")\n'
            '    x = use(f"a={a}")\n'
            '    return f"{a}!" == a\n')
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].args[0].expr, THIRFString)
        assert fn.body[0].args[0].print_form is PrintForm.RAW
        assert isinstance(fn.body[1].init.args[0], THIRFString)
        ret = fn.body[2]
        assert isinstance(ret.value, THIRBinOp)
        assert isinstance(ret.value.left, THIRFString)

    def test_conversion_ineligible(self):
        # !r / !s and format specs change the placeholder/wrapper table -> AST.
        thir = _lower(
            "from tpy import Int32\n"
            'def f(a: str) -> str:\n    return f"{a!r}"\n'
            'def g(n: Int32) -> str:\n    return f"{n:04}"\n')
        assert _fn(thir, "f") is None
        assert _fn(thir, "g") is None

    def test_bigint_arg_ineligible(self):
        # A runtime-BigInt arg takes the `.to_string()` row -- not mirrored.
        thir = _lower('def f(n: int) -> str:\n    return f"n={n}"\n')
        assert _fn(thir, "f") is None

    def test_char_arg_ineligible(self):
        # Char has no mirrored wrapper row (S4 introduces Char values).
        thir = _lower(
            "from tpy import Char\n"
            'def f(c: Char) -> str:\n    return f"c={c}"\n')
        assert _fn(thir, "f") is None

    def test_container_arg_ineligible(self):
        # Containers format via _container_to_str (`::tpy::list_to_str(xs)`)
        # -- not mirrored. (An ANNOTATED local: container params and
        # unannotated container locals in f-strings are pre-existing sema
        # rejections -- Ref[list] / PendingList are not unwrapped by
        # _analyze_fstring; see BUGS.md.)
        thir = _lower(
            "from tpy import Int32\n"
            "def f() -> str:\n"
            "    xs: list[Int32] = [1, 2]\n"
            '    return f"{xs}"\n')
        assert _fn(thir, "f") is None

    def test_ineligible_inner_expr_rejects(self):
        # The interpolated expr itself must be in the slice (a global is not).
        thir = _lower(
            "from tpy import Int32\n"
            "G = 1\n"
            'def f() -> str:\n    return f"{G}"\n')
        assert _fn(thir, "f") is None

    def test_string_concat_arg_routes(self):
        # A String (concat result) is std::string -- it formats bare, exactly
        # like the str-family row (the S3-in-S2 composition).
        thir = _lower(
            'def f(a: str, b: str) -> str:\n    return f"{a + b}"\n')
        fstr = _fn(thir, "f").body[0].value
        assert isinstance(fstr, THIRFString)
        assert _emit_expr(fstr) == 'std::format("{}", (::tpy::str_concat(a, b)))'

    def test_empty_fstring(self):
        # `f""` is a TpyFString with no parts: the all-literal arm over an
        # empty join -- `std::string("")`.
        thir = _lower('def f() -> str:\n    return f""\n')
        fstr = _fn(thir, "f").body[0].value
        assert isinstance(fstr, THIRFString) and fstr.parts == ()
        assert _emit_expr(fstr) == 'std::string("")'


class TestFStringEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import Int8, Int32, Float64\n"
        "def use(s: str) -> Int32:\n"
        "    return len(s)\n"
        "def f(name: str, n: Int32, m: Int8, b: bool, x: Float64) -> str:\n"
        '    a = f"hello"\n'
        '    a = f"name={name} n={n} m={m} b={b} x={x}"\n'
        '    print(a, f"inline {n}", use(f"arg {name}"))\n'
        '    same = f"{name}!" == name\n'
        "    print(same)\n"
        '    return f"bye {name} {{esc}} {5} {1.5} {True}"\n'
        "def main() -> None:\n"
        '    print(f("bob", 3, 2, True, 1.5))\n'
        "main()\n"
    )

    def test_fstring_byte_identical(self):
        thir = _lower(self.SRC)
        # main stays AST: its bare numeric-literal call args are outside the
        # call-arg slice (a pre-S2 frontier, not an f-string gap).
        for name in ("use", "f"):
            assert _fn(thir, name) is not None, name
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)


# --- Scalar type-constructor calls (Int32(x) / Float64(x) / bool(n)) ---


_CTOR_PRELUDE = "from tpy import Int32, Int64, UInt32, UInt64, Float64\n"


class TestStrConcat:
    def test_string_local_and_copy(self):
        # A concat-initialized local is String-typed (std::string); a local
        # bound to it copies bare (owned -> owned, no view wrap).
        thir = _lower(
            "def f(a: str, b: str) -> str:\n"
            "    c = a + b\n    c2 = c\n    print(c2)\n    return c\n")
        body = _fn(thir, "f").body
        decl, copy_decl = body[0], body[1]
        assert isinstance(decl, THIRVarDecl) and isinstance(decl.init, THIRBinOp)
        assert decl.init.form is Form.STORAGE
        assert isinstance(copy_decl.init, THIRName)
        assert copy_decl.init.form is Form.STORAGE  # String local: owned lvalue

    def test_self_append_peephole(self):
        # `t = t + a` fires the in-place-append peephole at a reassignment,
        # exactly like the AST's _try_str_inplace_append.
        thir = _lower(
            'def f(a: str) -> str:\n    t = "p"\n    t = t + a\n    return t\n')
        app = _fn(thir, "f").body[1]
        assert isinstance(app, THIRStrAppend)
        assert app.target == "t"
        assert isinstance(app.value, THIRName)

    def test_reversed_operands_no_peephole(self):
        # `x = b + x` must NOT fire the peephole (left operand != target).
        thir = _lower(
            "def f(a: str, b: str) -> str:\n"
            "    x = a + b\n    x = b + x\n    return x\n")
        assign = _fn(thir, "f").body[1]
        assert isinstance(assign, THIRAssign)
        assert isinstance(assign.value, THIRBinOp)

    def test_aug_assign_concat_rhs(self):
        # `t += a + b`: the append's value is the nested (paren-wrapped) concat.
        thir = _lower(
            'def f(a: str, b: str) -> str:\n    t = "s"\n    t += a + b\n'
            "    return t\n")
        app = _fn(thir, "f").body[1]
        assert isinstance(app, THIRStrAppend)
        assert _emit_expr(app.value) == "(::tpy::str_concat(a, b))"

    def test_concat_feeds_print_call_len(self):
        # A concat composes with the S1 sinks: print arg, same-type call arg
        # (via the identity string_to_str coercion), len of a String local.
        thir = _lower(
            "from tpy import Int32\n"
            "def use(s: str) -> Int32:\n    return len(s)\n"
            "def f(a: str, b: str) -> None:\n"
            "    print(a + b)\n"
            "    n = use(a + b)\n"
            "    c = a + b\n"
            "    print(len(c), n)\n")
        f = _fn(thir, "f")
        assert f is not None
        assert isinstance(f.body[0].args[0].expr, THIRBinOp)
        assert isinstance(f.body[1].init, THIRCall)

    def test_aug_assign_param_ineligible(self):
        # A str param's aug-assign needs the AST's owned-copy prologue (and the
        # AST path currently miscompiles it) -- stays off the slice.
        thir = _lower('def f(a: str) -> str:\n    a += "x"\n    return a\n')
        assert _fn(thir, "f") is None

    def test_char_operand_ineligible(self):
        # A Char operand resolves the char_to_str __add__ overload -- Char
        # values ride the S4 cell, so the shape stays on the AST path.
        thir = _lower(
            "from tpy import Char\n"
            "def f(a: str, c: Char) -> str:\n    return a + c\n")
        assert _fn(thir, "f") is None

    def test_str_repeat_ineligible(self):
        # `s * n` resolves __mul__ (str_repeat) with a str result -- not the
        # String-result concat arm; stays on the AST path.
        thir = _lower(
            "from tpy import Int32\n"
            "def f(a: str, n: Int32) -> str:\n    return a * n\n")
        assert _fn(thir, "f") is None

    def test_string_param_still_ineligible(self):
        # The concat slice admits String locals/operands but must not widen the
        # param gate: a String param spells `const std::string&`.
        thir = _lower(
            "from tpy import String\n"
            "def f(s: String, a: str) -> str:\n    return a\n")
        assert _fn(thir, "f") is None

    def test_concat_in_compare_routes(self):
        # A String concat result is a compare operand like any str value --
        # std::string takes the same templates / bare operators (the S3-in-S1
        # composition).
        thir = _lower(
            "def f(a: str, b: str, c: str) -> bool:\n    return a + b == c\n")
        cmp = _fn(thir, "f").body[0].value
        assert isinstance(cmp, THIRBinOp)
        assert _emit_expr(cmp) == "((::tpy::str_concat(a, b)) == c)"


class TestStrConcatEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import Int32\n"
        "def use(s: str) -> Int32:\n"
        "    return len(s)\n"
        "def concat_params(a: str, b: str) -> str:\n"
        "    c = a + b\n"
        "    return c\n"
        "def string_local_flows(a: str, b: str) -> str:\n"
        "    c = a + b\n"
        "    c2 = c\n"
        "    print(c, c2, len(c))\n"
        "    c += a\n"
        "    c = c + b\n"
        "    d = c + c2\n"
        "    return d\n"
        "def reassign_nonpeep(a: str, b: str) -> str:\n"
        "    x = a + b\n"
        "    x = b + x\n"
        "    return x\n"
        "def nested3(a: str, b: str, c: str) -> str:\n"
        "    return a + b + c\n"
        "def cond_concat(a: str, b: str) -> str:\n"
        '    t = "z"\n'
        "    if a < b:\n"
        "        t = t + a\n"
        "    else:\n"
        "        t += b\n"
        "    return t\n"
        "def call_arg(a: str, b: str) -> Int32:\n"
        "    return use(a + b)\n"
        "def main() -> None:\n"
        '    print(concat_params("a", "b"))\n'
        '    print(string_local_flows("a", "b"))\n'
        '    print(reassign_nonpeep("c", "d"))\n'
        '    print(nested3("g", "h", "i"))\n'
        '    print(cond_concat("j", "k"))\n'
        '    print(call_arg("x", "y"))\n'
        "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("use", "concat_params", "string_local_flows",
                     "reassign_nonpeep", "nested3", "cond_concat", "call_arg",
                     "main"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_shapes(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::string c = (::tpy::str_concat(a, b));" in cpp
        assert "c += a;" in cpp          # str += statement
        assert "c += b;" in cpp          # x = x + y peephole
        assert "x = (::tpy::str_concat(b, x));" in cpp  # reversed: no peephole
        assert ("return (::tpy::str_concat((::tpy::str_concat(a, b)), c));"
                in cpp)                  # nested concat, bare owned return
        assert "use((::tpy::str_concat(a, b)))" in cpp  # concat call arg


class TestScalarCtorCall:
    def test_literal_passthrough_routes(self):
        # Int32(0) -- the same-type overload's `{0}` template over a literal.
        thir = _lower(_CTOR_PRELUDE + "def f() -> Int32:\n    x = Int32(0)\n    return x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        init = fn.body[0].init
        assert isinstance(init, THIRCall) and init.callee == "Int32"
        assert init.cpp_template == "{0}"
        assert isinstance(init.args[0], THIRLiteral) and init.args[0].value == 0

    def test_int_cast_check_routes(self):
        # Int64(a) with a: Int32 -- the generic AnyFixedInt overload; sema has
        # already substituted {cpp} with the concrete return spelling.
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> Int64:\n    return Int64(a)\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall)
        assert ret.cpp_template == "::tpy::int_cast_check<int64_t>({0})"

    def test_zero_arg_ctor_routes(self):
        # Int32() -- the 0-arity overload's constant template.
        thir = _lower(_CTOR_PRELUDE + "def f() -> Int32:\n    return Int32()\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall) and ret.cpp_template == "0"
        assert ret.args == ()

    def test_float_from_int_routes(self):
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> float:\n    return Float64(a)\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall)
        assert ret.cpp_template == "static_cast<double>({0})"

    def test_binop_arg_routes(self):
        # The cast wraps a parenthesized binop -- the arg lowers through the
        # normal THIRBinOp (paren_wrap default), so the emit keeps the parens.
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> Int64:\n    return Int64(a + a)\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall) and isinstance(ret.args[0], THIRBinOp)

    def test_ctor_as_call_and_print_arg_routes(self):
        thir = _lower(_CTOR_PRELUDE
                      + "def use(v: Int64) -> Int64:\n    return v\n"
                      + "def f(a: Int32) -> None:\n"
                      + "    print(use(Int64(a)), Int32(7))\n")
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[0].args[0].expr
        assert isinstance(outer, THIRCall) and outer.callee == "use"
        assert isinstance(outer.args[0], THIRCall)
        assert outer.args[0].cpp_template is not None

    def test_bool_ctor_routes(self):
        thir = _lower(_CTOR_PRELUDE + "def f() -> bool:\n    return bool(1)\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRCall) and ret.cpp_template == "({0} != 0)"

    def test_bigint_result_ineligible(self):
        # int(x) constructs a BigInt -- not an eligible scalar result.
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> None:\n    x = int(a)\n    print(a)\n")
        assert _fn(thir, "f") is None

    def test_str_result_ineligible(self):
        thir = _lower(_CTOR_PRELUDE
                      + "def f(a: Int32) -> None:\n    s = str(a)\n    print(a)\n")
        assert _fn(thir, "f") is None

    def test_float_str_arg_ineligible(self):
        # float("nan") folds to a numeric_limits constant on the AST path -- the
        # str-literal arg fails the scalar arg gate, keeping the fold there.
        thir = _lower(_CTOR_PRELUDE
                      + "def f() -> float:\n    return float(\"nan\")\n")
        assert _fn(thir, "f") is None

    def test_char_ctor_ineligible(self):
        # Char("a") resolves to a @native(function=True) ctor (no cpp_template),
        # and neither the str arg nor the Char result is an eligible scalar.
        thir = _lower("from tpy import Char\n"
                      + "def f() -> None:\n    c = Char(\"a\")\n    print(1)\n")
        assert _fn(thir, "f") is None

    def test_float32_ctor_ineligible(self):
        # Float32 literals need an `f` suffix the slice does not emit.
        thir = _lower("from tpy import Float32\n"
                      + "def f() -> None:\n    x = Float32(1.5)\n    print(1)\n")
        assert _fn(thir, "f") is None

    def test_wide_literal_arg_ineligible(self):
        # A literal outside int32 range renders with a static_cast wrap
        # (_gen_int_literal_value) the bare THIRLiteral emit does not reproduce.
        thir = _lower(_CTOR_PRELUDE
                      + "def f() -> None:\n    g = UInt32(4294967295)\n    print(1)\n")
        assert _fn(thir, "f") is None

    def test_negative_literal_arg_routes(self):
        # A `-3` ctor arg folds to a plain literal on both paths (the AST's
        # _gen_unaryop literal-negation branch feeds the template expansion).
        thir = _lower(_CTOR_PRELUDE
                      + "def f() -> None:\n    d = Int32(-3)\n    print(d)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[0].init
        assert isinstance(call.args[0], THIRLiteral) and call.args[0].value == -3

    def test_negated_float_literal_arg_ineligible(self):
        # A negated FLOAT literal takes the resolved __neg__ template
        # (`-(1.5)`), a render the slice does not reproduce -> AST path.
        thir = _lower(_CTOR_PRELUDE
                      + "def f() -> None:\n    d = Float64(-1.5)\n    print(d)\n")
        assert _fn(thir, "f") is None


class TestScalarCtorCallEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _CTOR_PRELUDE
        + "def conv(a: Int32, b: UInt32) -> Int64:\n"
        + "    w = Int64(a)\n"
        + "    u = UInt64(b)\n"
        + "    s = Int64(a + a)\n"
        + "    return w + s\n"
        + "def seed() -> Int32:\n"
        + "    z = Int32()\n"
        + "    x = Int32(0)\n"
        + "    y = Int32(x)\n"
        + "    return x + y + z\n"
        + "def fl(a: Int32) -> float:\n"
        + "    m = Float64(a)\n"
        + "    return m + Float64(1.5)\n"
        + "def flags() -> bool:\n"
        + "    k = bool(1)\n"
        + "    return k\n"
        + "def use(v: Int64) -> Int64:\n    return v\n"
        + "def main():\n"
        + "    print(conv(3, UInt32(4)))\n"
        + "    print(seed(), fl(2), flags())\n"
        + "    print(use(Int64(9)))\n"
        + "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("conv", "seed", "fl", "flags"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "int64_t w = ::tpy::int_cast_check<int64_t>(a);" in cpp
        assert "uint64_t u = ::tpy::int_cast_check<uint64_t>(b);" in cpp
        # the cast keeps the binop's paren wrap
        assert ("int64_t s = ::tpy::int_cast_check<int64_t>"
                "((::tpy::add_check<int32_t>(a, a)));") in cpp
        assert "int32_t z = 0;" in cpp           # zero-arg ctor
        assert "int32_t x = 0;" in cpp           # literal passthrough
        assert "int32_t y = x;" in cpp           # same-type passthrough
        assert "double m = static_cast<double>(a);" in cpp
        assert "bool k = (1 != 0);" in cpp
        assert "use(9)" in cpp                   # ctor folded in a call arg


# --- S4 str subscript / slice / iteration: Char reads (checked + bounds-safe),
# --- str_slice views, Char loop vars, char-literal compares; the reassigned
# --- StrView param widening (param_needs_copy_for_reassign is the exact gate) ---

class TestStrSubscriptSliceIter:
    def test_char_subscript_routes(self):
        thir = _lower(
            "def f(s: str) -> None:\n    c = s[0]\n    print(c)\n")
        body = _fn(thir, "f").body
        decl = body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.resolved_type.to_cpp() == "char"
        assert isinstance(decl.init, THIRSubscript)
        assert decl.init.form is Form.VALUE
        assert not decl.init.bounds_safe
        assert _emit_expr(decl.init) == "::tpy::__getitem__(s, 0)"
        # print(Char) streams raw -- Char has no int_traits, so no int8 cast.
        assert body[1].args[0].print_form is PrintForm.RAW

    def test_bounds_safe_subscript_routes(self):
        thir = _lower(
            "def f(s: str) -> None:\n"
            "    for i in range(len(s)):\n        print(s[i])\n")
        loop = _fn(thir, "f").body[0]
        sub = loop.body[0].args[0].expr
        assert isinstance(sub, THIRSubscript) and sub.bounds_safe
        assert _emit_expr(sub) == "s[static_cast<std::size_t>(i)]"

    def test_char_literal_compare_routes(self):
        # The str literal opposite a Char renders as a char literal (the AST's
        # _comparison_targets char arm); char-vs-char compares stay bare.
        thir = _lower(
            "def f(s: str) -> bool:\n    return s[0] == \"x\"\n"
            "def g(s: str) -> bool:\n    c = s[0]\n    d = s[1]\n    return c != d\n"
            "def h(s: str) -> bool:\n    return \"y\" == s[1]\n")
        f_ret = _fn(thir, "f").body[0].value
        assert isinstance(f_ret.right, THIRCharLiteral)
        assert _emit_expr(f_ret) == '(::tpy::__getitem__(s, 0) == \'x\')'
        g_ret = _fn(thir, "g").body[2].value
        assert isinstance(g_ret.left, THIRName) and isinstance(g_ret.right, THIRName)
        assert _emit_expr(g_ret) == "(c != d)"
        h_ret = _fn(thir, "h").body[0].value
        assert isinstance(h_ret.left, THIRCharLiteral)  # literal on the left

    def test_escaped_char_literal_compare(self):
        # A Char-targeted literal needing escaping threads escape_cpp_char.
        thir = _lower(
            "def f(s: str) -> bool:\n    return s[0] == \"'\"\n"
            'def g(s: str) -> bool:\n    return s[0] == "\\n"\n')
        f_ret = _fn(thir, "f").body[0].value
        assert _emit_expr(f_ret) == "(::tpy::__getitem__(s, 0) == '\\'')"
        g_ret = _fn(thir, "g").body[0].value
        assert _emit_expr(g_ret) == "(::tpy::__getitem__(s, 0) == '\\n')"

    def test_str_literal_pair_stays_str(self):
        # Two str literals have no Char operand -> the str-pair arm, plain
        # string-literal compare on both paths (no char target arises).
        thir = _lower('def f() -> bool:\n    return "a" == "b"\n')
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret.left, THIRStrLiteral)
        assert isinstance(ret.right, THIRStrLiteral)

    def test_char_param_return_and_call_arg(self):
        thir = _lower(
            "from tpy import Char\n"
            "def is_x(c: Char) -> bool:\n    return c == \"x\"\n"
            "def pick(s: str) -> Char:\n    return s[1]\n"
            "def f(s: str) -> None:\n    c = s[0]\n    b = is_x(c)\n    print(b)\n")
        assert _fn(thir, "is_x") is not None
        assert _fn(thir, "pick") is not None
        f = _fn(thir, "f")
        call = f.body[1].init
        assert isinstance(call, THIRCall) and isinstance(call.args[0], THIRName)

    def test_char_literal_decl_routes(self):
        # A Char-annotated decl init from a str literal renders as a
        # target-typed char literal (`char c = 'x';`, the AST's gen_expr
        # char arm). (Reassigning / returning a str literal into a Char slot
        # is sema-rejected, so those gate guards are defense in depth only.)
        thir = _lower(
            "from tpy import Char\n"
            'def f() -> None:\n    c: Char = "x"\n    print(c)\n')
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRCharLiteral)
        assert decl.resolved_type.to_cpp() == "char"
        assert _emit_expr(decl.init) == "'x'"

    def test_char_literal_call_arg_routes(self):
        # A single-char literal into a Char param slot renders as a char
        # literal too (`take('a')`) -- the arg lowers against its slot.
        thir = _lower(
            "from tpy import Char\n"
            "def take(c: Char) -> None:\n    print(c)\n"
            'def f() -> None:\n    take("a")\n')
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[0].expr
        assert isinstance(call.args[0], THIRCharLiteral)
        assert _emit_expr(call) == "take('a')"

    def test_slice_routes_view_sinks(self):
        thir = _lower(
            "def f(s: str) -> None:\n"
            "    a = s[1:3]\n    b = s[2:]\n    c = s[:2]\n"
            "    print(a, b, c)\n")
        body = _fn(thir, "f").body
        sl = body[0].init
        assert isinstance(sl, THIRStrSlice) and sl.form is Form.BORROW
        assert _emit_expr(sl) == "::tpy::str_slice(s, ::tpy::BasicSlice{1, 3})"
        assert _emit_expr(body[1].init) == (
            "::tpy::str_slice(s, ::tpy::BasicSlice{2, std::nullopt})")
        assert _emit_expr(body[2].init) == (
            "::tpy::str_slice(s, ::tpy::BasicSlice{std::nullopt, 2})")

    def test_slice_variable_bounds_route(self):
        thir = _lower(
            "from tpy import Int32\n"
            "def f(s: str, i: Int32, j: Int32) -> None:\n"
            "    d = s[i:j]\n    print(d)\n")
        sl = _fn(thir, "f").body[0].init
        assert _emit_expr(sl) == "::tpy::str_slice(s, ::tpy::BasicSlice{i, j})"

    def test_slice_owned_sink_routes(self):
        # A slice into an owned sink arrives as a sema strview_to_str TpyCoerce
        # and materializes: the coerce lowers to the view->owned THIRFormConvert
        # (`std::string(...)` -- the S1 emit chokepoint).
        thir = _lower(
            "def f(s: str) -> str:\n    return s[1:3]\n"
            "def g(s: str) -> None:\n    t: str = s[1:3]\n    print(t)\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRFormConvert) and ret.form is Form.STORAGE
        assert isinstance(ret.value, THIRStrSlice)
        assert _emit_expr(ret) == (
            "std::string(::tpy::str_slice(s, ::tpy::BasicSlice{1, 3}))")
        decl = _fn(thir, "g").body[0]
        assert isinstance(decl.init, THIRFormConvert)
        assert decl.init.form is Form.STORAGE

    def test_stepped_slice_routes_owned(self):
        # `s[::2]` -> `::tpy::str_stepped_slice(s, ::tpy::Slice{...})`, an
        # OWNED std::string result (STORAGE -- bare at every sink, unlike the
        # non-stepped view). A negative step folds like any negated literal.
        thir = _lower("def f(s: str) -> str:\n    a = s[::2]\n    print(a)\n"
                      "    return s[1:8:-2]\n")
        fn = _fn(thir, "f")
        assert fn is not None
        sl = fn.body[0].init
        assert isinstance(sl, THIRStrSlice) and sl.stepped
        assert sl.form is Form.STORAGE
        assert (_emit_expr(sl)
                == "::tpy::str_stepped_slice(s, ::tpy::Slice{std::nullopt, std::nullopt, 2})")
        ret = fn.body[2].value
        assert (_emit_expr(ret)
                == "::tpy::str_stepped_slice(s, ::tpy::Slice{1, 8, -2})")

    def test_slice_variable_index_routes(self):
        # `s[sl]` off a slice-object param: the index renders bare into the
        # resolved template; basic_slice -> view result, slice -> owned.
        thir = _lower(
            "from tpy import basic_slice\n"
            "def f(s: str, sl: basic_slice) -> None:\n    print(s[sl])\n"
            "def g(s: str, st: slice) -> None:\n    print(s[st])\n")
        f_sub = _fn(thir, "f").body[0].args[0].expr
        assert isinstance(f_sub, THIRStrSlice) and f_sub.index is not None
        assert f_sub.form is Form.BORROW
        assert _emit_expr(f_sub) == "::tpy::str_slice(s, sl)"
        g_sub = _fn(thir, "g").body[0].args[0].expr
        assert g_sub.form is Form.STORAGE
        assert _emit_expr(g_sub) == "::tpy::str_stepped_slice(s, st)"

    def test_slice_object_local_ineligible(self):
        # A slice-object LOCAL (`sl = basic_slice(1, 3)`, a ctor over
        # `Int32 | None` Optional slots) rides a later cell -> AST path.
        thir = _lower(
            "from tpy import basic_slice\n"
            "def f(s: str) -> None:\n"
            "    sl = basic_slice(1, 3)\n    print(s[sl])\n")
        assert _fn(thir, "f") is None

    def test_field_receiver_slice_and_iteration_route(self):
        # A str-family field off an F1-record receiver as the sliced /
        # iterated str: both render the bare field read (`h.name`).
        thir = _lower_ctx(
            "class H:\n"
            "    name: str\n"
            "    def __init__(self, name: str):\n        self.name = name\n"
            "def f(h: H) -> None:\n"
            "    a = h.name[1:3]\n    print(a)\n"
            "    for c in h.name:\n        print(c)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        sl = fn.body[0].init
        assert isinstance(sl, THIRStrSlice)
        assert _emit_expr(sl) == "::tpy::str_slice(h.name, ::tpy::BasicSlice{1, 3})"
        loop = fn.body[2]
        assert isinstance(loop, THIRForEach)
        assert _emit_expr(loop.iterable) == "h.name"

    def test_call_receiver_slice_routes(self):
        # An owned-str call result sliced directly: a view sink (print) takes
        # the bare `::tpy::str_slice(full(s), ...)`; an owned-local sink takes
        # the S1 view->owned copy (`std::string b = std::string(...)`, the
        # BORROW-form THIRFormConvert). A call-result ITERABLE is an rvalue
        # (`auto` capture, a different emit) -> AST path.
        thir = _lower(
            "def full(s: str) -> str:\n    return s\n"
            "def f(s: str) -> None:\n    print(full(s)[0:2])\n"
            "def g(s: str) -> None:\n    b = full(s)[0:2]\n    print(b)\n"
            "def h(s: str) -> None:\n"
            "    for c in full(s):\n        print(c)\n")
        f_sub = _fn(thir, "f").body[0].args[0].expr
        assert isinstance(f_sub, THIRStrSlice)
        assert (_emit_expr(f_sub)
                == "::tpy::str_slice(full(s), ::tpy::BasicSlice{0, 2})")
        g_init = _fn(thir, "g").body[0].init
        assert isinstance(g_init, THIRFormConvert)
        assert isinstance(g_init.value, THIRStrSlice)
        assert _fn(thir, "h") is None  # rvalue iterable

    def test_str_iteration_routes(self):
        thir = _lower(
            "def f(s: str) -> None:\n    for c in s:\n        print(c)\n"
            'def g() -> None:\n    t = "abc"\n    for c in t:\n        print(c)\n'
            "def h(s: str) -> None:\n    v = s[1:]\n    for c in v:\n        print(c)\n")
        for name in ("f", "g", "h"):
            fn = _fn(thir, name)
            assert fn is not None, name
            loop = fn.body[-1]
            assert isinstance(loop, THIRForEach)
            assert loop.elem_type.to_cpp() == "char"

    def test_reassigned_strview_param_routes(self):
        # Only param_needs_copy_for_reassign types (owned str/bytes, BigInt)
        # hoist the AST's mutable-copy prologue; a StrView param is a by-value
        # view and reassigns in place on both paths.
        thir = _lower(
            "from tpy import StrView\n"
            "def f(s: StrView, flag: bool) -> None:\n"
            "    if flag:\n        s = s[1:]\n    print(s)\n"
            'def g(a: str) -> None:\n    a = "other"\n    print(a)\n')
        f = _fn(thir, "f")
        assert f is not None
        reassign = f.body[0].then_body[0]
        assert isinstance(reassign, THIRAssign)
        assert isinstance(reassign.value, THIRStrSlice)
        assert _fn(thir, "g") is None  # owned str param still rejected


class TestStrSubscriptSliceIterEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import Char, Int32, StrView\n"
        "def first(s: str) -> Char:\n"
        "    return s[0]\n"
        "def count_x(s: str) -> Int32:\n"
        "    n = 0\n"
        "    for c in s:\n"
        '        if c == "x":\n'
        "            n = n + 1\n"
        "    return n\n"
        "def find_x(s: str) -> Int32:\n"
        "    n = 0\n"
        "    for i in range(len(s)):\n"
        '        if s[i] == "x":\n'
        "            n = n + 1\n"
        "    return n\n"
        "def trim(s: StrView, flag: bool) -> None:\n"
        "    if flag:\n"
        "        s = s[1:]\n"
        "    print(s)\n"
        "def views(s: str) -> None:\n"
        "    a = s[1:3]\n"
        "    b = s[2:]\n"
        "    print(a, b, len(s))\n"
        "def main() -> None:\n"
        '    print(first("q"))\n'
        '    print(count_x("axbx"), find_x("xcx"))\n'
        # a literal arg into a StrView slot arrives str_to_strview
        # coerce-wrapped (deferred cross-type cell) -- pass a view local
        '    s = "hello"\n'
        "    trim(s, True)\n"
        '    views("world")\n'
        "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("first", "count_x", "find_x", "trim", "views", "main"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return ::tpy::__getitem__(s, 0);" in cpp
        assert "char c = *__beg_0;" in cpp                       # Char loop var
        assert "if ((c == 'x')) {" in cpp                        # char literal
        assert "if ((s[static_cast<std::size_t>(i)] == 'x')) {" in cpp
        assert "s = ::tpy::str_slice(s, ::tpy::BasicSlice{1, std::nullopt});" in cpp
        assert "std::string_view a = ::tpy::str_slice(s, ::tpy::BasicSlice{1, 3});" in cpp

    # S4 leftovers (increment 45): stepped slices, slice-typed variable
    # indices, Char-targeted literal decls / call args, non-name receivers.
    SRC2 = (
        "from tpy import Char, basic_slice\n"
        "class H:\n"
        "    name: str\n"
        "    def __init__(self, name: str):\n        self.name = name\n"
        "def full(s: str) -> str:\n"
        "    return s\n"
        "def stepped(s: str) -> str:\n"
        "    t = s[::2]\n"
        "    print(t, s[::-1])\n"
        "    return s[1:8:2]\n"
        "def var_index(s: str, sl: basic_slice, st: slice) -> None:\n"
        "    print(s[sl], s[st])\n"
        "def take(c: Char) -> None:\n"
        "    print(c)\n"
        "def chars() -> None:\n"
        '    c: Char = "x"\n'
        "    print(c)\n"
        '    take("a")\n'
        "def non_name(h: H, s: str) -> None:\n"
        "    a = h.name[1:3]\n"
        "    print(a, full(s)[0:2])\n"
        "    for ch in h.name:\n"
        "        print(ch)\n"
        "def main() -> None:\n"
        '    print(stepped("abcdefgh"))\n'
        '    var_index("hello", basic_slice(1, 3), slice(0, 5, 2))\n'
        "    chars()\n"
        '    non_name(H("greetings"), "abcdef")\n'
        "main()\n"
    )

    def test_s4_leftovers_routed(self):
        thir = _lower_ctx(self.SRC2)
        for name in ("stepped", "var_index", "take", "chars", "non_name"):
            assert _fn(thir, name) is not None, name
        # main stays AST: the slice-object ctor locals are a deferred cell.
        assert _fn(thir, "main") is None

    def test_s4_leftovers_byte_identical(self):
        assert self._cpp(self.SRC2, thir=True) == self._cpp(self.SRC2, thir=False)

    def test_s4_leftovers_emit_arms(self):
        cpp = self._cpp(self.SRC2, thir=True)
        assert ("std::string t = ::tpy::str_stepped_slice(s, "
                "::tpy::Slice{std::nullopt, std::nullopt, 2});") in cpp
        assert ("::tpy::str_stepped_slice(s, "
                "::tpy::Slice{std::nullopt, std::nullopt, -1})") in cpp
        assert "return ::tpy::str_stepped_slice(s, ::tpy::Slice{1, 8, 2});" in cpp
        assert "::tpy::str_slice(s, sl)" in cpp                 # basic_slice var
        assert "::tpy::str_stepped_slice(s, st)" in cpp         # slice var
        assert "char c = 'x';" in cpp                           # Char decl
        assert "take('a');" in cpp                              # Char call arg
        assert "::tpy::str_slice(h.name, ::tpy::BasicSlice{1, 3})" in cpp
        assert "::tpy::str_slice(full(s), ::tpy::BasicSlice{0, 2})" in cpp
        assert "auto& __obj_0 = h.name;" in cpp                 # field iterable


class TestCrossCellEmit:
    """Compositions ACROSS the S2/S3/S4 cells (developed in parallel worktrees
    and merged): a slice view consumed inline as a concat operand and as an
    f-string arg, and slice-then-iterate. The per-cell byte-identical tests
    can't see a merge regression between cells; these can."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    SRC = (
        "def cut_join(s: str) -> str:\n"
        "    t = s[1:3] + s\n"
        "    return t\n"
        "def fmt_slice(s: str) -> str:\n"
        '    return f"mid={s[1:3]} c={s[0] == \'h\'}"\n'
        "def iter_slice(s: str) -> None:\n"
        "    v = s[1:]\n"
        "    for c in v:\n"
        "        print(c)\n"
        "def main() -> None:\n"
        '    s = "hello"\n'
        "    print(cut_join(s), fmt_slice(s))\n"
        "    iter_slice(s)\n"
        "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("cut_join", "fmt_slice", "iter_slice", "main"):
            assert _fn(thir, name) is not None, name

    def test_coerce_carries_inner_form(self):
        # The string_to_str passthrough at cut_join's owned return: the coerce
        # must carry the wrapped String local's STORAGE form (not the VALUE
        # default), so the owned-sink BORROW check reads the real source shape.
        thir = _lower(self.SRC)
        ret = _fn(thir, "cut_join").body[-1]
        assert isinstance(ret.value, THIRCoerce)
        assert ret.value.form is Form.STORAGE

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)


# --- Cross-type str-family coercions (str <-> StrView <-> String) ---


class TestStrCrossTypeCoercions:
    def test_view_slot_identity_routes(self):
        # str_to_strview at ARG is identity; the coerce sets BORROW itself (a
        # view into the source), not the wrapped expression's form.
        thir = _lower(
            "from tpy import StrView\n"
            "def take(v: StrView) -> None:\n    print(v)\n"
            'def f(s: str) -> None:\n    take("lit")\n    take(s)\n')
        calls = [st.expr for st in _fn(thir, "f").body]
        for call in calls:
            arg = call.args[0]
            assert isinstance(arg, THIRCoerce)
            assert arg.coercion_name == "str_to_strview"
            assert arg.form is Form.BORROW
        assert _emit_expr(calls[0]) == 'take("lit")'
        assert _emit_expr(calls[1]) == "take(s)"

    def test_string_slot_dispositions(self):
        # str_to_string: identity for a NUL-free literal at ARG (const char[N]
        # binds const std::string& directly), materialize (`std::string(x)`)
        # for any other source; strview_to_string always materializes.
        thir = _lower(
            "from tpy import String, StrView\n"
            "def take(p: String) -> None:\n    print(p)\n"
            "def f(s: str, v: StrView) -> None:\n"
            '    take("lit")\n    take(s)\n    take(v)\n')
        calls = [st.expr for st in _fn(thir, "f").body]
        assert isinstance(calls[0].args[0], THIRCoerce)
        assert _emit_expr(calls[0]) == 'take("lit")'
        for call, src in zip(calls[1:], ("s", "v")):
            arg = call.args[0]
            assert isinstance(arg, THIRFormConvert) and arg.form is Form.STORAGE
            assert _emit_expr(call) == f"take(std::string({src}))"

    def test_nul_literal_into_string_slot_materializes(self):
        # cpp_string_literal_expr's NUL arm is not a bare-quote token, so the
        # AST lambda wraps it (`std::string(std::string_view{...})`) -- the
        # disposition mirrors the token check structurally (NUL in the value).
        thir = _lower(
            "from tpy import String\n"
            "def take(p: String) -> None:\n    print(p)\n"
            'def f() -> None:\n    take("a\\x00b")\n')
        arg = _fn(thir, "f").body[0].expr.args[0]
        assert isinstance(arg, THIRFormConvert)
        assert _emit_expr(arg).startswith("std::string(std::string_view{")

    def test_string_local_annotated_init_materializes(self):
        # strview_to_string at INIT: `m: String = <view>` copies explicitly.
        thir = _lower(
            "from tpy import String\n"
            "def f(s: str) -> None:\n    m: String = s[1:]\n    print(m)\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl.init, THIRFormConvert)
        assert _emit_expr(decl.init) == (
            "std::string(::tpy::str_slice(s, ::tpy::BasicSlice{1, std::nullopt}))")

    def test_own_slot_coerce_ineligible(self):
        # An Own[str] ARG slot crosses the gen_call_arg auto-move cascade (its
        # own deferred frontier) -> the coerce is rejected, the caller stays AST.
        thir = _lower(
            "from tpy import Own\n"
            "def take(p: Own[str]) -> None:\n    print(p)\n"
            "def f(s: str) -> None:\n    take(s[1:])\n")
        assert _fn(thir, "f") is None

    def test_coerced_literal_multi_overload_pin_ineligible(self):
        # The AST pins a str literal (COERCE-PEELED, gen_call_arg) to its
        # param's view form for a multi-overload callee
        # (`std::string_view("lit")`); the bare THIRCall emit does not
        # reproduce the pin -> AST path. A non-literal arg still routes.
        thir = _lower(
            "from typing import overload\n"
            "from tpy import Int32, StrView\n"
            "@overload\n"
            "def pick(x: StrView) -> Int32: ...\n"
            "@overload\n"
            "def pick(x: Int32) -> Int32: ...\n"
            "def pick(x: StrView | Int32) -> Int32:\n    return 1\n"
            'def caller() -> Int32:\n    return pick("lit")\n'
            "def caller2(s: str) -> Int32:\n    return pick(s)\n")
        assert _fn(thir, "caller") is None
        assert _fn(thir, "caller2") is not None


class TestStrCrossTypeCoercionEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import String, StrView\n"
        "def take_view(v: StrView) -> None:\n    print(v)\n"
        "def take_string(p: String) -> None:\n    print(p)\n"
        "def ret_slice(s: str) -> str:\n    return s[1:3]\n"
        "def init_slice(s: str) -> None:\n    t: str = s[1:3]\n    print(t)\n"
        "def cmp_concat(a: str, b: str, c: str) -> bool:\n    return a + b == c\n"
        'def fstr_concat(a: str, b: str) -> str:\n    return f"{a + b}"\n'
        "def calls(s: str, v: StrView) -> None:\n"
        '    take_view("lit")\n    take_view(s)\n    take_view(s[1:])\n'
        '    take_string("lit")\n    take_string(s)\n    take_string(v)\n'
        '    take_string("a\\x00b")\n'
        "def slice_compare(s: str) -> bool:\n"
        '    return s[1:3] == "ab"\n'
        "def string_arg_pass(a: str, b: str) -> None:\n"
        "    take_string(a + b)\n    take_view(a + b)\n"
    )

    def test_str_coercions_byte_identical(self):
        thir = _lower(self.SRC)
        # take_string stays AST (String params are an S3 deferral); every
        # coercion SOURCE shape routes.
        for name in ("ret_slice", "init_slice", "cmp_concat", "fstr_concat",
                     "calls", "slice_compare", "string_arg_pass", "take_view"):
            assert _fn(thir, name) is not None, name
        assert _fn(thir, "take_string") is None
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)


# --- S5 dict[str] / container-of-str keys + elements: owned-str dict keys
# --- (subscript reads, methods, iteration), str-element container literals
# --- with the per-slot view->owned wrap, and the follow-on str element reads ---

class TestDictStrContainers:
    def test_str_key_literal_read(self):
        thir = _lower(
            _PRELUDE
            + 'def f() -> Int32:\n    d = {"a": 1, "b": 2}\n    return d["a"]\n')
        body = _fn(thir, "f").body
        lit = body[0].init
        assert isinstance(lit, THIRContainerLiteral)
        assert all(isinstance(k, THIRStrLiteral) for k in lit.elements)
        assert _emit_expr(lit) == (
            '::tpy::ordered_map<std::string, int32_t>({{"a", 1}, {"b", 2}})')
        sub = body[1].value
        assert isinstance(sub, THIRSubscript) and not sub.bounds_safe
        assert _emit_expr(sub) == '::tpy::__getitem__(d, "a")'

    def test_str_key_variable_read(self):
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[str, Int32], k: str) -> Int32:\n    return d[k]\n")
        sub = _fn(thir, "f").body[0].value
        assert isinstance(sub, THIRSubscript)
        assert _emit_expr(sub) == "::tpy::__getitem__(d, k)"

    def test_str_value_read_forms(self):
        # A str element/value read carries its resolved shape: owned (STORAGE)
        # lands bare in the owned local; a view-resolved read stays BORROW.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[str, str], k: str) -> None:\n"
            + '    v = d[k]\n    v += "!"\n    print(v)\n'  # v forced owned
            + "def g(d: dict[str, str], k: str) -> None:\n"
            + "    v = d[k]\n    print(v)\n")               # v stays a view
        f_decl = _fn(thir, "f").body[0]
        assert isinstance(f_decl.init, THIRSubscript)
        assert f_decl.init.form is Form.STORAGE
        assert f_decl.resolved_type.to_cpp() == "std::string"
        g_decl = _fn(thir, "g").body[0]
        assert g_decl.resolved_type.to_cpp() == "std::string_view"

    def test_view_elem_owned_slot_wrap(self):
        # A view-form str name into an owned std::string element slot copies
        # via the S1 THIRFormConvert (std::string(x)); dict keys likewise.
        thir = _lower(
            "def f(s: str, t: str) -> None:\n"
            "    xs = [s, t]\n    d = {s: 1}\n    print(len(xs), len(d))\n")
        body = _fn(thir, "f").body
        xs_lit = body[0].init
        assert all(isinstance(el, THIRFormConvert) for el in xs_lit.elements)
        assert _emit_expr(xs_lit.elements[0]) == "std::string(s)"
        d_lit = body[1].init
        assert isinstance(d_lit.elements[0], THIRFormConvert)
        assert _emit_expr(d_lit) == (
            "::tpy::ordered_map<std::string, int32_t>({{std::string(s), 1}})")

    def test_owned_elem_lands_bare(self):
        # An owned-str local element copies implicitly (str locals are value
        # types, never movable -- no make_vector/std::move arm).
        thir = _lower(
            'def f() -> None:\n    k = "x"\n    k += "y"\n'
            "    xs = [k]\n    print(len(xs), k)\n")
        lit = _fn(thir, "f").body[2].init
        assert isinstance(lit.elements[0], THIRName)
        assert lit.elements[0].form is Form.STORAGE
        assert _emit_expr(lit) == "{k}"

    def test_dict_str_methods_route(self):
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[str, Int32], k: str) -> Int32:\n"
            + '    x = d.pop(k)\n    y = d.pop("gone", 0)\n'
            + '    z = d.get("a", 7)\n    d.clear()\n    return x + y + z\n')
        body = _fn(thir, "f").body
        pop = body[0].init
        assert isinstance(pop, THIRMethodCall)
        assert _emit_expr(pop) == "::tpy::dict_pop(d, k)"
        assert _emit_expr(body[1].init) == '::tpy::dict_pop_default(d, "gone", 0)'
        assert _emit_expr(body[2].init) == '::tpy::dict_get_default(d, "a", 7)'

    def test_owned_str_method_result_form(self):
        # xs.pop() on list[str] returns an owned std::string by value: STORAGE,
        # landing bare in the owned local and the owned return.
        thir = _lower(
            "def f(xs: list[str]) -> str:\n    x = xs.pop()\n    return x\n"
            "def g(xs: list[str]) -> str:\n    return xs.pop()\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl.init, THIRMethodCall)
        assert decl.init.form is Form.STORAGE
        g_ret = _fn(thir, "g").body[0]
        assert isinstance(g_ret.value, THIRMethodCall)
        assert _emit_expr(g_ret.value) == "::tpy::pop_back(xs)"

    def test_list_str_iteration_routes(self):
        thir = _lower(
            "def f(xs: list[str]) -> None:\n    for s in xs:\n        print(s)\n")
        loop = _fn(thir, "f").body[0]
        assert isinstance(loop, THIRForEach)
        assert loop.elem_type.to_cpp() == "std::string_view"

    def test_owned_loop_var_routes(self):
        # A mutated str loop var usage-resolves OWNED: a std::string typed copy.
        thir = _lower(
            "def f(xs: list[str]) -> None:\n"
            '    for s in xs:\n        s += "!"\n        print(s)\n')
        loop = _fn(thir, "f").body[0]
        assert isinstance(loop, THIRForEach)
        assert loop.elem_type.to_cpp() == "std::string"

    def test_own_str_slot_arg_ineligible(self):
        # xs.append(s) / st.add(s): an Own[str] slot materializes an owned copy
        # (std::string(s)) or a copy-into-temp + std::move -- stays AST.
        thir = _lower(
            "def f(xs: list[str], s: str) -> None:\n    xs.append(s)\n"
            'def g(xs: list[str]) -> None:\n    xs.append("lit")\n')
        assert _fn(thir, "f") is None
        assert _fn(thir, "g") is None

    def test_view_and_bytes_containers_ineligible(self):
        # StrView-keyed/valued dicts hold views (literal keys pin to static
        # storage via view_key_target); bytes rides S6.
        thir = _lower(
            _PRELUDE
            + "from tpy import StrView\n"
            + "def f(d: dict[Int32, StrView], i: Int32) -> Int32:\n    return i\n"
            + "def g(d: dict[bytes, Int32], i: Int32) -> Int32:\n    return i\n"
            + "def h(xs: list[StrView], i: Int32) -> Int32:\n    return i\n")
        for name in ("f", "g", "h"):
            assert _fn(thir, name) is None, name

    def test_subscript_write_stays_ast(self):
        # d[k] = v (__setitem__) is not a routed statement shape for ANY
        # container family (pre-existing parked cell) -- unchanged by S5.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[str, Int32], k: str) -> None:\n    d[k] = 3\n")
        assert _fn(thir, "f") is None


class TestDictStrContainersEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def get(d: dict[str, Int32], k: str) -> Int32:\n"
        + "    return d[k]\n"
        + "def total(d: dict[str, Int32]) -> Int32:\n"
        + "    t = 0\n"
        + "    for k in d:\n"
        + "        t = t + d[k]\n"
        + "    return t\n"
        + "def bump(d: dict[str, Int32], e: dict[str, Int32]) -> Int32:\n"
        + '    x = d.pop("gone", 0)\n'
        + "    d.update(e)\n"
        + "    return x + len(d)\n"
        + "def wrap(s: str, t: str) -> Int32:\n"
        + "    xs = [s, t]\n"
        + "    st = {s, t}\n"
        + "    m = {s: 1, t: 2}\n"
        + "    return len(xs) + len(st) + len(m)\n"
        + "def pick(xs: list[str], i: Int32) -> str:\n"
        + "    return xs[i]\n"
        + "def collect(xs: list[str]) -> str:\n"
        + '    out = ""\n'
        + "    for s in xs:\n"
        + "        out += s\n"
        + "    return out\n"
        + "def vals(d: dict[str, str], k: str, p: str, q: str) -> None:\n"
        + "    v = d[k]\n"
        + "    w = d[p + q]\n"
        + '    if v == "x":\n'
        + "        print(v)\n"
        + "    print(w, len(d))\n"
        + "def main() -> None:\n"
        + '    d = {"a": 1, "b": 2, "gone": 3}\n'
        + '    print(get(d, "a"), total(d))\n'
        + '    e = {"c": 4}\n'
        + "    print(bump(d, e))\n"
        + '    print(wrap("v", "w"))\n'
        + '    xs = ["p", "q"]\n'
        + "    print(pick(xs, 0), collect(xs))\n"
        + '    m = {"xy": "x", "q": "r"}\n'
        + '    vals(m, "xy", "x", "y")\n'
        + "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("get", "total", "bump", "wrap", "pick", "collect",
                     "vals", "main"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return ::tpy::__getitem__(d, k);" in cpp
        assert "std::string_view k = *__beg_0;" in cpp        # str key loop var
        assert '::tpy::dict_pop_default(d, "gone", 0)' in cpp
        assert "::tpy::dict_update(d, e);" in cpp             # container pass-through
        assert "{std::string(s), std::string(t)}" in cpp      # list elem wrap
        assert ("::tpy::ordered_set<std::string>"
                "({std::string(s), std::string(t)})") in cpp  # set elem wrap
        assert ("::tpy::ordered_map<std::string, int32_t>"
                "({{std::string(s), 1}, {std::string(t), 2}})") in cpp
        assert "return ::tpy::__getitem__(xs, i);" in cpp     # bare owned sink
        assert "out += s;" in cpp                             # view loop var append
        assert ("std::string_view w = "
                "::tpy::__getitem__(d, (::tpy::str_concat(p, q)));") in cpp


# --- bytes / BytesView values (F6 S6) ---


class TestBytesValues:
    def test_view_local_from_literal(self):
        # Literal init, no owned-forcing usage -> BytesView local; the literal
        # renders its static-storage span form (owned=False), incl. on a
        # reassignment (the binding, not the init type, decides the render).
        thir = _lower(
            'def f() -> None:\n    v = b"ab"\n    print(v)\n    v = b"cd"\n'
            "    print(v)\n")
        body = _fn(thir, "f").body
        decl = body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.resolved_type.to_cpp() == "std::span<const uint8_t>"
        assert isinstance(decl.init, THIRBytesLiteral) and not decl.init.owned
        reassign = body[2]
        assert isinstance(reassign, THIRAssign)
        assert isinstance(reassign.value, THIRBytesLiteral)
        assert not reassign.value.owned

    def test_owned_local_init_from_view_wraps(self):
        # u is forced owned (reassigned from an owned call result), so its
        # decl init off the view param copies explicitly (::tpy::bytes_copy).
        thir = _lower(
            "def mk() -> bytes:\n"
            '    return b"own"\n'
            "def f(a: bytes) -> None:\n"
            "    u = a\n    u = mk()\n    print(u)\n")
        f = _fn(thir, "f")
        decl = f.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.resolved_type.to_cpp() == "std::vector<uint8_t>"
        assert isinstance(decl.init, THIRFormConvert)
        assert decl.init.form is Form.STORAGE
        assert _emit_expr(decl.init) == "::tpy::bytes_copy(a)"
        # mk's owned-bytes call result is STORAGE -- the reassign stays bare.
        reassign = f.body[1]
        assert isinstance(reassign.value, THIRCall)
        assert reassign.value.form is Form.STORAGE

    def test_return_param_wraps_return_literal_owned(self):
        thir = _lower(
            "def f(a: bytes) -> bytes:\n    return a\n"
            'def g() -> bytes:\n    return b"lit"\n')
        f_ret = _fn(thir, "f").body[0]
        assert isinstance(f_ret.value, THIRFormConvert)
        inner = f_ret.value.value
        assert isinstance(inner, THIRName) and inner.form is Form.BORROW
        assert _emit_expr(f_ret.value) == "::tpy::bytes_copy(a)"
        g_ret = _fn(thir, "g").body[0]
        assert isinstance(g_ret.value, THIRBytesLiteral) and g_ret.value.owned

    def test_cross_type_coercion_ineligible(self):
        # A literal or bytes value at a BytesView return arrives wrapped in
        # the cross-type view coercion (position-dependent) -> AST path,
        # mirroring the str cross-type deferral.
        thir = _lower(
            "from tpy import BytesView\n"
            'def g() -> BytesView:\n    return b"ab"\n'
            "def h(a: bytes) -> BytesView:\n    return a\n")
        assert _fn(thir, "g") is None
        assert _fn(thir, "h") is None

    def test_compare_and_len_route(self):
        # bytes __eq__ is a @native free-function dunder (no cpp_template) --
        # the native binop arm renders gen_call_from_fi's shape.
        thir = _lower(
            "from tpy import Int32\n"
            "def f(a: bytes, b: bytes) -> bool:\n    return a == b\n"
            "def g(a: bytes) -> Int32:\n    return len(a)\n"
            'def h(a: bytes) -> bool:\n    return a == b"ab"\n')
        cmp_ret = _fn(thir, "f").body[0]
        assert isinstance(cmp_ret.value, THIRBinOp)
        assert _emit_expr(cmp_ret.value) == "(::tpy::bytes_eq(a, b))"
        len_ret = _fn(thir, "g").body[0]
        assert isinstance(len_ret.value, THIRCall)
        assert len_ret.value.native_name == "tpy::__len__"
        # A literal compare operand renders OWNED (no target in compare position).
        lit_cmp = _fn(thir, "h").body[0].value
        assert isinstance(lit_cmp.right, THIRBytesLiteral) and lit_cmp.right.owned

    def test_bytes_args_pass_through(self):
        # Value args pass bare; a literal into a bytes/BytesView slot takes the
        # static-span pin (owned=False), incl. through the view coercion wrap.
        thir = _lower(
            "from tpy import BytesView\n"
            "def use_owned(b: bytes) -> None:\n    print(b)\n"
            "def use_view(v: BytesView) -> None:\n    print(v)\n"
            "def f(a: bytes) -> None:\n"
            "    use_owned(a)\n"
            '    use_owned(b"oo")\n'
            '    use_view(b"vv")\n')
        f = _fn(thir, "f")
        assert isinstance(f.body[0].expr.args[0], THIRName)
        lit_owned = f.body[1].expr.args[0]
        assert isinstance(lit_owned, THIRBytesLiteral) and not lit_owned.owned
        lit_view = f.body[2].expr.args[0]
        assert isinstance(lit_view, THIRBytesLiteral) and not lit_view.owned

    def test_print_arg_form(self):
        thir = _lower('def f(a: bytes) -> None:\n    print(a, b"x")\n')
        args = _fn(thir, "f").body[0].args
        assert args[0].print_form is PrintForm.BYTES
        assert args[1].print_form is PrintForm.BYTES
        assert isinstance(args[1].expr, THIRBytesLiteral) and args[1].expr.owned

    def test_aug_assign_ineligible(self):
        # bytes += is a concat-and-assign (::tpy::bytes_concat), not the str
        # in-place append -- deferred, stays AST.
        thir = _lower(
            'def f() -> None:\n    t = b"a"\n    t += b"b"\n    print(t)\n')
        assert _fn(thir, "f") is None

    def test_concat_ineligible(self):
        thir = _lower("def f(a: bytes, b: bytes) -> bytes:\n    return a + b\n")
        assert _fn(thir, "f") is None

    def test_iteration_ineligible(self):
        # bytes is NativeIterable[UInt8]; its loop emit is unverified -> AST.
        thir = _lower(
            "def f(a: bytes) -> None:\n    for x in a:\n        print(x)\n")
        assert _fn(thir, "f") is None

    def test_subscript_ineligible(self):
        # b[i] -> UInt8 rides a later cell (the container gates are
        # list/dict-pinned and the str gate is str-family-pinned).
        thir = _lower(
            "from tpy import UInt8\n"
            "def f(a: bytes) -> UInt8:\n    return a[0]\n")
        assert _fn(thir, "f") is None

    def test_reassigned_bytes_param_ineligible(self):
        # An owned-bytes param reassign hoists the AST's owned-copy prologue
        # (param_needs_copy_for_reassign).
        thir = _lower(
            'def f(a: bytes) -> None:\n    a = b"other"\n    print(a)\n')
        assert _fn(thir, "f") is None

    def test_bytearray_param_ineligible(self):
        # bytearray is a reference type on a different axis.
        thir = _lower("def f(a: bytearray) -> None:\n    print(len(a))\n")
        assert _fn(thir, "f") is None

    def test_own_bytes_slot_ineligible(self):
        # An Own[bytes] slot materializes an owned copy at the call boundary.
        thir = _lower(
            "from tpy import Own\n"
            "def sink(b: Own[bytes]) -> None:\n    print(b)\n"
            "def f(a: bytes) -> None:\n    sink(a)\n")
        assert _fn(thir, "f") is None


class TestBytesValuesEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import BytesView, Int32\n"
        "def mk() -> bytes:\n"
        '    return b"own"\n'
        "def use_view(v: BytesView) -> None:\n"
        "    print(v, len(v))\n"
        "def ret_copy(b: bytes) -> bytes:\n"
        "    return b\n"
        "def eq_test(b: bytes) -> bool:\n"
        '    return b == b"yes"\n'
        "def chain(a: bytes) -> None:\n"
        "    v = a\n"
        "    u = v\n"
        "    u = mk()\n"
        "    print(u, v)\n"
        "def lits() -> None:\n"
        '    v = b"ab"\n'
        '    e = b""\n'
        '    print(v, e, b"raw\\x00z")\n'
        "def main() -> None:\n"
        '    use_view(b"vv")\n'
        '    use_view(b"")\n'
        '    print(ret_copy(b"q"), eq_test(b"yes"))\n'
        '    chain(b"c")\n'
        "    lits()\n"
        "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("mk", "use_view", "ret_copy", "eq_test", "chain", "lits",
                     "main"):
            assert _fn(thir, name) is not None, name

    def test_bytes_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
        # The view->owned STORAGE convert (::tpy::bytes_copy) -- reachable for
        # the first time in this cell -- at both sinks:
        assert "return ::tpy::bytes_copy(b);" in cpp                # return
        assert "std::vector<uint8_t> u = ::tpy::bytes_copy(v);" in cpp  # decl init
        # Literal renders: span pin at view sinks, owned elsewhere; empty forms.
        assert 'use_view(::tpy::bytes_literal("vv", 2));' in cpp
        assert "use_view(std::span<const uint8_t>{});" in cpp
        assert 'std::span<const uint8_t> v = ::tpy::bytes_literal("ab", 2);' in cpp
        assert "std::span<const uint8_t> e = std::span<const uint8_t>{};" in cpp
        assert '::tpy::BytesPrinter(::tpy::bytes_literal_owned("raw\\000z", 5))' in cpp
        # The native-dunder compare and the BytesPrinter wrap.
        assert '(::tpy::bytes_eq(b, ::tpy::bytes_literal_owned("yes", 3)))' in cpp
        assert "::tpy::BytesPrinter(u)" in cpp

    def test_owned_init_wrap_byte_identical(self):
        # The minimal shape exercising the bytes arm of the view->owned
        # convert (`::tpy::bytes_copy`) -- previously UNREACHABLE in emit.py.
        src = (
            "def mk() -> bytes:\n"
            '    return b"own"\n'
            "def f(a: bytes) -> None:\n"
            "    u = a\n"
            "    u = mk()\n"
            "    print(u)\n"
            'f(b"q")\n'
        )
        thir = _lower(src)
        f = _fn(thir, "f")
        assert f is not None
        assert isinstance(f.body[0].init, THIRFormConvert)
        cpp = self._cpp(src, thir=True)
        assert "std::vector<uint8_t> u = ::tpy::bytes_copy(a);" in cpp
        assert cpp == self._cpp(src, thir=False)


# --- Branch-integration review units: ctor-rvalue arg gates, Array[str, N] ---
# --- literals, and the String-init identity pin ---


class TestIntegrationReviewUnits:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_float_literal_ctor_rvalue_routes(self):
        # A bare float literal into a double ctor slot passes through on the
        # F2d rebind-slot rvalue source, like a free-call arg (incr 45).
        src = (
            "from tpy import Float64\n"
            "class P:\n"
            "    x: Float64\n"
            "    def __init__(self, x: Float64):\n        self.x = x\n"
            "def f() -> Float64:\n"
            "    p = P(1.5)\n    a = p.x\n    p = P(2.5)\n    return p.x + a\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRCall) and decl.init.callee == "P"
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_omitted_default_ctor_rvalue_ineligible(self):
        # An omitted default is synthesized by the AST arg emit; the bare
        # THIRCall does not reproduce it -> the fi/arity gate rejects.
        src = (
            "from tpy import Int32\n"
            "class Q:\n"
            "    a: Int32\n"
            "    b: Int32\n"
            "    def __init__(self, a: Int32, b: Int32 = 2):\n"
            "        self.a = a\n        self.b = b\n"
            "def f() -> Int32:\n"
            "    q = Q(1)\n    x = q.a\n    q = Q(3)\n    return x + q.a\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_array_str_literal_routes(self):
        # A read-only str-element list literal demotes to Array[str, N] (the
        # S5 Array-family widening); the view-form param element takes the
        # per-slot owned copy.
        src = (
            "def f(s: str) -> None:\n"
            '    ys = ["a", s]\n'
            "    print(ys[0])\n")
        thir = _lower(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRContainerLiteral)
        assert decl.resolved_type.name == "Array"
        assert isinstance(decl.init.elements[1], THIRFormConvert)
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_string_init_identity_mirrors_ast_miscompile(self):
        # `m: String = s` (a view-form source) is IDENTITY at INIT on both
        # paths and emits `std::string m = s;` -- ill-formed C++ (the
        # string_view ctor is explicit): a PRE-EXISTING AST miscompile, see
        # BUGS.md. THIR mirrors it byte-identically; when the AST arm is
        # fixed, this pin and the byte-diff flag the lockstep update.
        src = (
            "from tpy import String\n"
            "def f(s: str) -> None:\n    m: String = s\n    print(m)\n")
        thir = _lower(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl.init, THIRCoerce)
        assert decl.init.coercion_name == "str_to_string"
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
