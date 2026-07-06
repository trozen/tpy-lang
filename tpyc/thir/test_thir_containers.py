"""THIR container sites: subscript reads, method calls (THIRMethodCall),
container-literal locals, container call args."""

from __future__ import annotations

import dataclasses

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _emit_expr
from .nodes import (
    Form, THIRCall, THIRCoerce, THIRContainerLiteral, THIRExprStmt,
    THIRForEach, THIRLiteral, THIRMethodCall, THIRName, THIRSubscript,
    THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn, _PRELUDE, _F1_RECORDS,
)

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

    def test_bigint_key_dict_param_routes(self):
        # A BigInt (`int`) dict key is admitted like a fixed-int one; the
        # `.to_fixed_check<int32_t>()` narrow fires per-index on the INDEX
        # type, not here. Isolated by a trivial body so only the param gate
        # decides.
        thir = _lower(
            _PRELUDE
            + "def g(d: dict[int, Int32], i: Int32) -> Int32:\n    return i\n")
        assert _fn(thir, "g") is not None

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

    def test_sort_native_function_routes(self):
        # `xs.sort()` -- @native(tpy::sort_in_place, function=True): the receiver
        # becomes the first arg (evaluate-once, no double-eval of a side-effecting
        # receiver).
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> None:\n    xs.sort()\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall)
        assert mc.native_function_name == "tpy::sort_in_place"
        assert mc.cpp_template is None

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
        assert "::tpy::sort_in_place(xs);" in cpp             # @native free function
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


# --- S4 leftover: container-returning call iterables (`for x in make_list():`) ---

# The call's capture verdict mirrors is_lvalue_iterable's call arm
# (_call_iterable_lvalue): an `Own[...]` return is a by-value rvalue (the owning
# `auto __obj_N =` capture), a borrow / readonly borrow return (`T&` /
# `const T&`) is a C++ lvalue (`auto& __obj_N =`). Bytes-returning calls and
# subscript iterables stay gate-excluded.
class TestContainerCallIterable:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def test_own_list_return_is_rvalue_capture(self):
        thir = _lower(
            "from tpy import Int32, Own\n"
            "def make_list(n: Int32) -> Own[list[Int32]]:\n    return [n, n]\n"
            "def f() -> Int32:\n    s = 0\n"
            "    for x in make_list(4):\n        s = s + x\n    return s\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForEach) and not loop.iterable_lvalue
        assert isinstance(loop.iterable, THIRCall)

    def test_borrow_list_return_is_lvalue_capture(self):
        thir = _lower(
            _PRELUDE
            + "def get_list(items: list[Int32]) -> list[Int32]:\n    return items\n"
            + "def f(items: list[Int32]) -> Int32:\n    s = 0\n"
            + "    for x in get_list(items):\n        s = s + x\n    return s\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForEach) and loop.iterable_lvalue

    def test_readonly_borrow_return_is_lvalue_capture(self):
        thir = _lower(
            "from tpy import Int32, readonly\n"
            "def view(items: list[Int32]) -> readonly[list[Int32]]:\n    return items\n"
            "def f(items: list[Int32]) -> Int32:\n    s = 0\n"
            "    for x in view(items):\n        s = s + x\n    return s\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForEach) and loop.iterable_lvalue

    def test_own_dict_return_key_iteration_routes(self):
        thir = _lower(
            "from tpy import Int32, Own\n"
            "def make_dict() -> Own[dict[Int32, Int32]]:\n    return {1: 10}\n"
            "def f() -> Int32:\n    s = 0\n"
            "    for k in make_dict():\n        s = s + k\n    return s\n")
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForEach) and not loop.iterable_lvalue

    def test_record_elements_from_borrow_return_route(self):
        # A `list[record]` borrow return composes with the F1-record loop var
        # (`auto&&` alias); a field write through it mutates the source list.
        thir = _lower_ctx(
            _F1_RECORDS
            + "def get(items: list[Inner]) -> list[Inner]:\n    return items\n"
            + "def f(items: list[Inner]) -> None:\n"
            + "    for p in get(items):\n        p.value = p.value + 1\n")
        loop = _fn(thir, "f").body[0]
        assert isinstance(loop, THIRForEach) and loop.iterable_lvalue

    def test_bytes_returning_call_iterable_ineligible(self):
        # _call_eligible admits a bytes return in value position; the for-each
        # arm filters it (the owned-vs-view capture shape is a deferred cell).
        thir = _lower(
            _PRELUDE
            + 'def make() -> bytes:\n    return b"ab"\n'
            + "def f() -> Int32:\n    s = 0\n"
            + "    for x in make():\n        s = s + 1\n    return s\n")
        assert _fn(thir, "f") is None

    def test_byte_identical(self):
        src = (
            "from tpy import Int32, Own, readonly\n"
            "def make_list(n: Int32) -> Own[list[Int32]]:\n    return [n, n]\n"
            "def get_list(items: list[Int32]) -> list[Int32]:\n    return items\n"
            "def view(items: list[Int32]) -> readonly[list[Int32]]:\n    return items\n"
            "def f() -> Int32:\n    s = 0\n"
            "    for x in make_list(4):\n        s = s + x\n"
            "    items = [4, 5]\n"
            "    for y in get_list(items):\n        s = s + y\n"
            "    for z in view(items):\n        s = s + z\n"
            "    return s\n"
            "def main():\n    print(f())\nmain()\n")
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        assert "auto __obj_0 = make_list(4);" in thir_cpp
        assert "auto& __obj_1 = get_list(items);" in thir_cpp
        assert "auto& __obj_2 = view(items);" in thir_cpp
