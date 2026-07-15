"""THIR container sites: subscript reads, method calls (THIRMethodCall),
container-literal locals, container call args."""

from __future__ import annotations

import dataclasses

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _emit_expr
from .nodes import (
    Form, THIRArgTemp, THIRBinOp, THIRCall, THIRCoerce, THIRContainerLiteral,
    THIRExprStmt, THIRFieldAccess, THIRForEach, THIRFormConvert, THIRGenExpr,
    THIRLiteral, THIRMembership, THIRMethodCall, THIRMove, THIRName, THIRReturn,
    THIRSelf, THIRSetItem, THIRStrLiteral, THIRSubscript, THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _lower_ctx_witnessed, _fn,
    _PRELUDE, _F1_RECORDS,
)


def _module_cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return cpp

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
        # An owned-str-keyed dict param routes; so does a StrView-keyed one under
        # the compositional param gate (the signature renders identically). The
        # view-key divergence -- literal keys pin to static storage -- lives in
        # the body SUBSCRIPT gate now, not the param gate, so a body that does not
        # subscript `d` routes for both key kinds.
        thir = _lower(
            _PRELUDE
            + "def g(d: dict[str, Int32], i: Int32) -> Int32:\n    return i\n")
        assert _fn(thir, "g") is not None
        view = _lower(
            _PRELUDE
            + "from tpy import StrView\n"
            + "def g(d: dict[StrView, Int32], i: Int32) -> Int32:\n    return i\n")
        assert _fn(view, "g") is not None

    def test_set_subscript_ineligible(self):
        # A `set[scalar]` param routes (membership/len/iteration -- see
        # TestMembership), but `set` has no `__getitem__`, so a subscript keeps
        # the body on the AST path (sema would error on real code; the reject is
        # a slice guard). The subscript-free control routes.
        thir = _lower(
            _PRELUDE
            + "def h(s: set[Int32], i: Int32) -> Int32:\n    return s[i]\n")
        assert _fn(thir, "h") is None
        ctrl = _lower(
            _PRELUDE
            + "def h(s: set[Int32], i: Int32) -> Int32:\n    return i\n")
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

    def test_unused_own_container_param_routes(self):
        # The signature owns the move-in ABI; an unused parameter does not
        # constrain lowering of the scalar return.
        thir = _lower(
            _PRELUDE + "from tpy import Own\n"
            + "def o(items: Own[list[Int32]], i: Int32) -> Int32:\n    return i\n")
        assert _fn(thir, "o") is not None

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

    _ENUM = ("from enum import Enum\n"
             "class Color(Enum):\n    Red = 0\n    Green = 1\n    Blue = 2\n")

    def test_enum_element_read_routes(self):
        # An enum element read is a bare VALUE-form read (`::tpy::__getitem__`),
        # admitted by the compositional value-leaf gate alongside scalars. The
        # container is a local (enum-element PARAM signatures ride the sibling
        # signature cell); list and dict-value both route.
        thir = _lower_ctx(
            self._ENUM + _PRELUDE
            + "def f() -> Color:\n"
            + "    xs = [Color.Red, Color.Green]\n"
            + "    return xs[1]\n")
        fn = _fn(thir, "f")
        assert fn is not None
        sub = fn.body[1].value
        assert isinstance(sub, THIRSubscript) and sub.form is Form.VALUE
        thir_d = _lower_ctx(
            self._ENUM + _PRELUDE
            + "def g(k: Int32) -> Color:\n"
            + "    d = {0: Color.Red, 1: Color.Blue}\n"
            + "    return d[k]\n")
        assert _fn(thir_d, "g") is not None

    def test_optional_element_read_ineligible(self):
        # An Optional-element container read is composite (`std::optional<T>`),
        # not a bare value-leaf -- the naive read emit would mis-tag it VALUE, so
        # it stays AST. The subscript-free control routes.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n"
            + "    xs: list[Int32 | None] = [1, None]\n"
            + "    y = xs[0]\n"
            + "    return 1\n")
        assert _fn(thir, "f") is None
        ctrl = _lower(
            _PRELUDE
            + "def f() -> Int32:\n"
            + "    xs: list[Int32 | None] = [1, None]\n"
            + "    return 1\n")
        assert _fn(ctrl, "f") is not None



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

    def test_record_element_arg_routes(self):
        # A record NAME into an Own[record] element slot renders bare
        # (push_back takes the lvalue -- inline_template), so the container arm
        # routes it once the record-element receiver is admitted.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n"
            + "def f(xs: list[P], p: P) -> None:\n    xs.append(p)\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall) and mc.method_cpp == "push_back"
        assert isinstance(mc.args[0], THIRName) and mc.args[0].name == "p"

    def test_str_param_arg_routes(self):
        # A str PARAM is a view (std::string_view) resolved `str`, not `StrView`,
        # so the arg gate reads `param_names` to tell it from an owned str local;
        # like a view local it materializes an owned copy `std::string(s)` at the
        # Own[str] element slot.
        thir = _lower(
            "from tpy import Int32\n"
            + "def f(xs: list[str], s: str) -> None:\n    xs.append(s)\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall) and mc.method_cpp == "push_back"
        arg = mc.args[0]
        assert isinstance(arg, THIRFormConvert) and arg.form is Form.STORAGE
        assert isinstance(arg.value, THIRName) and arg.value.form is Form.BORROW

    def test_reassigned_str_param_arg_ineligible(self):
        # A reassigned str param hoists an owned-copy prologue in the AST, so the
        # whole body is rejected -- the view form the append relies on is no
        # longer stable.
        thir = _lower(
            "from tpy import Int32\n"
            + "def f(xs: list[str], s: str) -> None:\n"
            + "    s = 'x'\n    xs.append(s)\n")
        assert _fn(thir, "f") is None

    def test_str_owned_local_arg_ineligible(self):
        # An owned-str STORAGE source (a concat result) takes gen_call_arg's
        # copy+move-temp cascade the bare/convert emit does not reproduce -> AST.
        thir = _lower(
            "from tpy import Int32\n"
            + "def f(xs: list[str], a: str, b: str) -> None:\n"
            + "    t = a + b\n    xs.append(t)\n")
        assert _fn(thir, "f") is None

    def test_str_view_local_arg_routes(self):
        # A VIEW-form str local into an Own[str] slot materializes an owned copy
        # `std::string(x)` via the S1 view->owned THIRFormConvert.
        thir = _lower(
            "from tpy import Int32\n"
            + "def f(xs: list[str]) -> None:\n    s = 'hi'\n    xs.append(s)\n")
        mc = _fn(thir, "f").body[1].expr
        assert isinstance(mc, THIRMethodCall)
        arg = mc.args[0]
        assert isinstance(arg, THIRFormConvert) and arg.form is Form.STORAGE
        assert isinstance(arg.value, THIRName) and arg.value.form is Form.BORROW

    def test_str_literal_arg_routes(self):
        # A str literal lands bare (const char[N] -> the vector's std::string ctor).
        thir = _lower(
            "from tpy import Int32\n"
            + "def f(xs: list[str]) -> None:\n    xs.append('lit')\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall)
        assert isinstance(mc.args[0], THIRStrLiteral)

    def test_record_ctor_arg_routes(self):
        # A same-nominal ctor rvalue binds the Own[record] element slot bare.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n"
            + "def f(xs: list[P]) -> None:\n    xs.append(P(5))\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall) and mc.method_cpp == "push_back"

    def test_record_own_move_arg_routes(self):
        # A movable Own[record] param at its last use moves into the slot.
        thir = _lower_ctx(
            "from tpy import Int32, Own\n"
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n"
            + "def f(xs: list[P], p: Own[P]) -> None:\n    xs.append(p)\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall)
        assert isinstance(mc.args[0], THIRMove)

    def test_record_returning_method_ineligible(self):
        # `xs.pop()` on a record-element list returns a record -- outside the
        # arm's value-position ret set -> AST.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n"
            + "def f(xs: list[P]) -> None:\n    xs.pop()\n")
        assert _fn(thir, "f") is None

    def test_record_clear_routes(self):
        # A no-arg mutator on a record-element receiver routes (void, no args).
        thir = _lower_ctx(
            "from tpy import Int32\n"
            + "class P:\n    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n"
            + "def f(xs: list[P]) -> None:\n    xs.clear()\n")
        mc = _fn(thir, "f").body[0].expr
        assert isinstance(mc, THIRMethodCall) and mc.method_cpp == "clear"

    def test_field_receiver_ineligible(self):
        # `self.items.append(...)` -- a non-name receiver rides a later cell.
        thir = _lower(
            "from tpy import Int32\n"
            + "class H:\n    items: list[Int32]\n"
            + "    def __init__(self):\n        self.items = []\n"
            + "    def add(self, n: Int32) -> None:\n        self.items.append(n)\n")
        assert _fn(thir, "add") is None

    def test_set_receiver_routes(self):
        # `.add()` routes through the set-receiver method arm; the body no
        # longer falls back, and the render matches the AST path.
        src = (_PRELUDE
               + "def f(s: set[Int32], n: Int32) -> None:\n    s.add(n)\n"
               + "f({1, 2}, 3)\n")
        assert _fn(_lower(src), "f") is not None
        assert _module_cpp(src, thir=True) == _module_cpp(src, thir=False)

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


# --- str / record element-slot append (the widened container-method arg arm) ---


class TestContainerElementAppendEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import Int32\n"
        + "class P:\n    x: Int32\n"
        + "    def __init__(self, x: Int32):\n        self.x = x\n"
        + "def grow_str(xs: list[str], sp: str) -> None:\n"
        + "    s = 'hi'\n"
        + "    xs.append(s)\n"           # view local -> std::string(s)
        + "    xs.append(sp)\n"          # str param (view/BORROW) -> std::string(sp)
        + "    xs.append('lit')\n"       # literal -> bare
        + "def grow_rec(xs: list[P], p: P) -> None:\n"
        + "    xs.append(p)\n"           # record name -> bare
        + "    xs.append(P(5))\n"        # ctor rvalue -> bare
        + "    xs.clear()\n"
        + "def main():\n"
        + "    xs: list[str] = []\n    grow_str(xs, 'yo')\n"
        + "    ps: list[P] = []\n    grow_rec(ps, P(1))\n"
        + "    print(len(xs))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "xs.push_back(std::string(s));" in cpp   # view local owned copy
        assert "xs.push_back(std::string(sp));" in cpp  # str param owned copy
        assert 'xs.push_back("lit");' in cpp            # literal bare
        assert "xs.push_back(p);" in cpp                # record name bare
        assert "xs.push_back(P(5));" in cpp             # ctor rvalue bare


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


# --- Container-literal element families (the widened THIRContainerLiteral
# slots): F1 records (ctor rvalues / names / moves / make_vector), nested list
# literals, enums, Optional[scalar], value tuples, owned bytes ---


_ELEM_RECORDS = (
    "from tpy import Int32, nocopy\n"
    "class P:\n    x: Int32\n"
    "    def __init__(self, x: Int32):\n        self.x = x\n"
    "@nocopy\nclass Q:\n    x: Int32\n"
    "    def __init__(self, x: Int32):\n        self.x = x\n"
)


class TestContainerLiteralElementFamilies:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def _both(self, src: str) -> str:
        ast_cpp = self._cpp(src, thir=False)
        assert self._cpp(src, thir=True) == ast_cpp
        return ast_cpp

    def test_record_ctor_rvalues_route(self):
        # Read-only literal demotes to Array; ctor rvalues brace-init.
        src = (_ELEM_RECORDS
               + "def f() -> Int32:\n    ps = [P(1), P(2)]\n    return len(ps)\n"
               + "def main():\n    print(f())\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        fn = _fn(thir, "f")
        assert fn is not None and isinstance(fn.body[0].init, THIRContainerLiteral)
        assert faces.get("containerlit.record_elem")
        cpp = self._both(src)
        assert "std::array<P, 2> ps = {P(1), P(2)};" in cpp

    def test_record_name_last_use_moves(self):
        # Both names are movable owned locals at their last use -> per-element
        # std::move in the std::array aggregate-init (no make_vector for Array).
        src = (_ELEM_RECORDS
               + "def f() -> Int32:\n"
               + "    a = P(1)\n    b = P(2)\n    ps = [a, b]\n    return len(ps)\n"
               + "def main():\n    print(f())\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("containerlit.move")
        cpp = self._both(src)
        assert "std::array<P, 2> ps = {std::move(a), std::move(b)};" in cpp

    def test_record_name_used_later_copies(self):
        # `a` is read after the literal -> not a last use -> brace copy, and
        # the field read keeps the body routed (scalar field off an F1 local).
        src = (_ELEM_RECORDS
               + "def f() -> Int32:\n"
               + "    a = P(1)\n    b = P(2)\n    ps = [a, b]\n"
               + "    return len(ps) + a.x\n"
               + "def main():\n    print(f())\nmain()\n")
        cpp = self._both(src)
        assert "std::array<P, 2> ps = {a, std::move(b)};" in cpp

    def test_movable_vector_takes_make_vector(self):
        # An annotated list stays a vector; a moved element switches the whole
        # literal to ::tpy::make_vector (const initializer_list would copy).
        src = (_ELEM_RECORDS
               + "def f() -> Int32:\n"
               + "    a = P(1)\n    zs: list[P] = [a]\n    return len(zs)\n"
               + "def main():\n    print(f())\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        fn = _fn(thir, "f")
        assert fn is not None
        lit = fn.body[1].init
        assert isinstance(lit, THIRContainerLiteral) and lit.make_container
        assert faces.get("containerlit.make")
        cpp = self._both(src)
        assert "std::vector<P> zs = ::tpy::make_vector<P>(std::move(a));" in cpp

    def test_nocopy_vector_takes_make_vector(self):
        # A @nocopy element type forces make_vector even for rvalue elements.
        src = (_ELEM_RECORDS
               + "def f() -> Int32:\n"
               + "    qs: list[Q] = [Q(1), Q(2)]\n    return len(qs)\n"
               + "def main():\n    print(f())\nmain()\n")
        cpp = self._both(src)
        assert "std::vector<Q> qs = ::tpy::make_vector<Q>(Q(1), Q(2));" in cpp

    def test_nocopy_array_keeps_brace_init(self):
        # std::array aggregate-init moves fine -- no make arm for the demoted
        # Array even with @nocopy elements.
        src = (_ELEM_RECORDS
               + "def f() -> Int32:\n    qs = [Q(1), Q(2)]\n    return len(qs)\n"
               + "def main():\n    print(f())\nmain()\n")
        cpp = self._both(src)
        assert "std::array<Q, 2> qs = {Q(1), Q(2)};" in cpp

    def test_record_field_access_element_ineligible(self):
        # A field-read element (`[h.p]`) is not a name/ctor-rvalue shape.
        thir = _lower(
            _ELEM_RECORDS
            + "class H:\n    p: P\n"
            + "    def __init__(self):\n        self.p = P(1)\n"
            + "def f(h: H) -> Int32:\n    ps = [h.p]\n    return len(ps)\n")
        assert _fn(thir, "f") is None

    def test_record_dict_value_ineligible(self):
        # Record dict values stay tagged (container_lit.elem.record).
        thir = _lower_ctx(
            _ELEM_RECORDS
            + "def f() -> Int32:\n    d = {1: P(1)}\n    return len(d)\n")
        assert _fn(thir, "f") is None

    def test_nested_list_array_outer_routes(self):
        # Read-only outer demotes to Array; the emit adds the extra aggregate
        # brace level around the vector elements.
        src = (_PRELUDE
               + "def f() -> Int32:\n    m = [[1, 2], [3, 4, 5]]\n    return len(m)\n"
               + "def main():\n    print(f())\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("containerlit.container_elem")
        cpp = self._both(src)
        assert ("std::array<std::vector<int32_t>, 2> m = {{{1, 2}, {3, 4, 5}}};"
                in cpp)

    def test_nested_list_vector_outer_routes(self):
        # An annotated (vector) outer: inner literals render bare braces, no
        # extra aggregate level.
        src = (_PRELUDE
               + "def f() -> Int32:\n"
               + "    m: list[list[Int32]] = [[1, 2], [3]]\n    return len(m)\n"
               + "def main():\n    print(f())\nmain()\n")
        cpp = self._both(src)
        assert "std::vector<std::vector<int32_t>> m = {{1, 2}, {3}};" in cpp

    def test_nested_empty_inner_vector_outer_ineligible(self):
        # An un-threaded (list-element) empty inner renders bare `{}` on the
        # AST; the spelled THIR emit would diverge -> AST path.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n"
            + "    m: list[list[Int32]] = [[], [1]]\n    return len(m)\n")
        assert _fn(thir, "f") is None

    def test_nested_view_source_ineligible(self):
        # A view-form str element inside an un-threaded nested literal: the
        # AST has no elem target there, so the S5 wrap never fires -> AST path
        # (only literal elements stay admitted).
        thir = _lower(
            "from tpy import Int32, StrView\n"
            + "def f(sv: StrView) -> Int32:\n"
            + "    m: list[list[str]] = [[sv]]\n    return len(m)\n")
        assert _fn(thir, "f") is None

    def test_nested_str_literals_route(self):
        src = (_PRELUDE
               + "def f() -> Int32:\n"
               + "    m = [[\"a\", \"b\"], [\"c\"]]\n    return len(m)\n"
               + "def main():\n    print(f())\nmain()\n")
        cpp = self._both(src)
        assert ("std::array<std::vector<std::string>, 2> m = "
                "{{{\"a\", \"b\"}, {\"c\"}}};" in cpp)

    def test_enum_elements_route(self):
        src = ("from tpy import Int32\n"
               + "from enum import Enum\n"
               + "class Color(Enum):\n    RED = 1\n    GREEN = 2\n    BLUE = 3\n"
               + "def f() -> Int32:\n"
               + "    xs = [Color.RED, Color.GREEN]\n"
               + "    s = {Color.RED, Color.BLUE}\n"
               + "    d = {1: Color.RED, 2: Color.GREEN}\n"
               + "    return len(xs) + len(s) + len(d)\n"
               + "def main():\n    print(f())\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("containerlit.enum_elem")
        cpp = self._both(src)
        assert "std::array<Color, 2> xs = {Color::RED, Color::GREEN};" in cpp
        assert ("::tpy::ordered_set<Color> s = "
                "::tpy::ordered_set<Color>({Color::RED, Color::BLUE});") in cpp
        assert ("::tpy::ordered_map<int32_t, Color> d = ::tpy::ordered_map<"
                "int32_t, Color>({{1, Color::RED}, {2, Color::GREEN}});") in cpp

    def test_optional_scalar_elements_route(self):
        # None -> the STORAGE-form std::nullopt; scalar values land bare.
        src = (_PRELUDE
               + "def f() -> Int32:\n"
               + "    xs: list[Int32 | None] = [1, None, 3]\n    return len(xs)\n"
               + "def main():\n    print(f())\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("containerlit.optional_elem")
        cpp = self._both(src)
        assert ("std::vector<std::optional<int32_t>> xs = "
                "{1, std::nullopt, 3};") in cpp

    def test_optional_str_elements_route(self):
        # Compositional Optional element gate (broadened from scalar-only): a
        # value-repr Optional[str] slot's inner routes like the bare owned-str
        # slot -- a str literal lands bare (the implicit std::string conversion),
        # None -> std::nullopt. The wrap is a pure function of the slot type.
        src = ("from tpy import Int32\nfrom typing import Optional\n"
               + "def f() -> Int32:\n"
               + "    xs: list[Optional[str]] = [\"a\", None, \"b\"]\n"
               + "    d: dict[Int32, Optional[str]] = {1: \"x\", 2: None}\n"
               + "    return len(xs) + len(d)\n"
               + "def main():\n    print(f())\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("containerlit.optional_elem")
        cpp = self._both(src)
        assert ("std::vector<std::optional<std::string>> xs = "
                "{\"a\", std::nullopt, \"b\"};") in cpp
        assert ("::tpy::ordered_map<int32_t, std::optional<std::string>> d = "
                "::tpy::ordered_map<int32_t, std::optional<std::string>>("
                "{{1, \"x\"}, {2, std::nullopt}});") in cpp

    def test_optional_record_elements_ineligible(self):
        # Pointer-repr Optional (record inner -> std::variant<T*,...>) is the
        # named exclusion: its storage-form lift is not the value-repr wrap.
        thir = _lower_ctx(
            _ELEM_RECORDS
            + "def f() -> Int32:\n"
            + "    xs: list[P | None] = [None]\n    return len(xs)\n")
        assert _fn(thir, "f") is None

    def test_tuple_literal_elements_route(self):
        src = (_PRELUDE
               + "def f() -> Int32:\n"
               + "    xs = [(1, 2), (3, 4)]\n    return len(xs)\n"
               + "def main():\n    print(f())\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("containerlit.tuple_elem")
        cpp = self._both(src)
        assert ("std::array<std::tuple<int32_t, int32_t>, 2> xs = "
                "{std::tuple<int32_t, int32_t>{1, 2}, "
                "std::tuple<int32_t, int32_t>{3, 4}};") in cpp

    def test_value_tuple_name_element_routes(self):
        # A value-tuple NAME element copies into the slot (value type, no
        # aliasing, never moved) -- byte-identical to the AST's copy. (Was
        # deferred: literals only.)
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n"
            + "    t = (1, 2)\n    xs = [t]\n    return len(xs)\n")
        assert _fn(thir, "f") is not None

    def test_value_tuple_str_element_name_copies_not_moves(self):
        # The observable shape: a value-tuple with an OWNED-STR element. Copy vs
        # std::move is distinguishable here (a moved-from string), so the
        # byte-diff pins that THIR copies -- never moves -- exactly like the AST
        # (a value-tuple is `is_value_type()`, so `_container_elem_move_source`
        # never fires and no AST movable path marks it).
        src = (_PRELUDE
               + "def f() -> Int32:\n"
               + "    t = (\"a\", 1)\n    xs = [t]\n    return len(xs)\n"
               + "def main():\n    print(f())\nmain()\n")
        assert _fn(_lower(src), "f") is not None
        self._both(src)  # asserts THIR == AST emit

    def test_own_tuple_element_name_ineligible(self):
        # The divergence risk the arm's comment names: an `Own[tuple[...]]`
        # element param the AST would MOVE (move-in ABI) is not a value-tuple
        # binding, so it must NOT reach the value-tuple-name arm -- stays AST.
        thir = _lower(
            "from tpy import Int32, Own\n"
            "def f(t: Own[tuple[Int32, Int32]]) -> Int32:\n"
            "    xs = [t]\n    return len(xs)\n")
        assert _fn(thir, "f") is None

    def test_bytes_literal_elements_route(self):
        # Owned-bytes element slots: literals render bytes_literal_owned.
        src = (_PRELUDE
               + "def f() -> Int32:\n    xs = [b\"ab\", b\"c\"]\n    return len(xs)\n"
               + "def main():\n    print(f())\nmain()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("containerlit.bytes_elem")
        cpp = self._both(src)
        assert ("std::array<std::vector<uint8_t>, 2> xs = "
                "{::tpy::bytes_literal_owned(\"ab\", 2), "
                "::tpy::bytes_literal_owned(\"c\", 1)};") in cpp

    def test_bytes_view_source_copies(self):
        # A BytesView param element copies into the owned slot (bytes_copy),
        # mirroring the AST's _view_source_to_owned chokepoint.
        src = ("from tpy import Int32, BytesView\n"
               + "def f(v: BytesView) -> Int32:\n"
               + "    xs: list[bytes] = [v]\n    return len(xs)\n"
               + "def main():\n    print(f(b\"vv\"))\nmain()\n")
        cpp = self._both(src)
        assert ("std::vector<std::vector<uint8_t>> xs = "
                "{::tpy::bytes_copy(v)};") in cpp

    def test_promoted_view_local_does_not_move(self):
        # A sema-movable but VALUE-typed local (a view-resolved promoted str)
        # never enters codegen's movable set (the tier-1 non-value filter in
        # _gen_var_decl), so the element takes the plain view->owned copy --
        # no std::move, no make_vector switch. Regression: the unfiltered
        # sema set moved it and diverged (str/str_pending_* corpus cases).
        src = ("from tpy import Int32, Own\n"
               + "def in_list() -> Own[list[str]]:\n"
               + "    label: str = \"no\"\n    return [label]\n"
               + "def main():\n    print(in_list())\nmain()\n")
        cpp = self._both(src)
        assert "return {std::string(label)};" in cpp

    def test_union_elements_stay_ineligible(self):
        thir = _lower(
            _PRELUDE
            + "from tpy import Float64\n"
            + "def f() -> Int32:\n"
            + "    xs: list[Int32 | Float64] = [1]\n    return len(xs)\n")
        assert _fn(thir, "f") is None



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

    def test_span_param_arg_routes(self):
        # A Span slot converts (`::tpy::as_mut_span(xs)`) -- routed since the
        # ptr/span coerce-disposition cell (the spanlike wrap).
        thir = _lower(
            "from tpy import Int32, Span\n"
            + "def use_span(sp: Span[Int32]) -> Int32:\n    return len(sp)\n"
            + "def f(xs: list[Int32]) -> Int32:\n    return use_span(xs)\n")
        assert _fn(thir, "f") is not None

    def test_span_method_arg_routes_byte_identical(self):
        # A user record with __span__() passed at a Span slot renders the
        # AST's `{0}.__span__()` arm (span_method_to_span_arg).
        src = (
            "from tpy import Int32, Span, readonly\n"
            + "class Buf:\n"
            + "    xs: list[Int32]\n"
            + "    def __init__(self):\n        self.xs = [1, 2]\n"
            + "    def __span__(self) -> Span[readonly[Int32]]:\n"
            + "        return self.xs\n"
            + "def use_span(sp: Span[readonly[Int32]]) -> Int32:\n"
            + "    return len(sp)\n"
            + "def f(b: Buf) -> Int32:\n    return use_span(b)\n"
            + "def main():\n    print(f(Buf()))\nmain()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = self._cpp(src, thir=True)
        assert cpp == self._cpp(src, thir=False)
        assert "use_span(b.__span__())" in cpp

    def test_span_array_literal_arg_routes_byte_identical(self):
        # An array-literal arg at a Span slot: the helper wraps the
        # make_array-typed brace init (as_mut_span(std::array<T, N>{...})),
        # elements target-typed through the Array family.
        src = (
            "from tpy import Span\n"
            + "def use_span(sp: Span[int]) -> int:\n    return len(sp)\n"
            + "def f() -> int:\n    return use_span([10, 20, 30])\n"
            + "def main():\n    print(f())\nmain()\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        cpp = self._cpp(src, thir=True)
        assert cpp == self._cpp(src, thir=False)
        assert ("::tpy::as_mut_span(std::array<::tpy::BigInt, 3>"
                "{::tpy::BigInt(10), ::tpy::BigInt(20), ::tpy::BigInt(30)})"
                in cpp)

    def test_protocol_param_method_arg_ineligible(self):
        # list.extend(other: Iterable[Own[T]]) -- the Iterable[Own[T]] slot's
        # auto-consuming-iteration path diverges: a movable-last-use local arg
        # emits `list_extend(xs, own_iter(std::move(ys)))`, not the bare
        # `list_extend(xs, ys)`. A container PARAM (or non-last-use local) is
        # never movable, so it DOES pass bare, but safely characterizing that
        # subset needs the body movable-locals set the arg gate does not thread
        # (it mirrors only Own-param seeding), so the whole shape stays AST.
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
        # The method-call validation half of the widening: d.update(e) -- the
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


# --- Value-view Span returns (`-> Span[scalar]` / `-> Span[readonly[scalar]]`,
# `std::span<T>` / `std::span<const T>`) -- a value type, rendered bare, its
# lifetime the caller's concern (same as the AST). Only byte-identical return
# SOURCES route: a bare span NAME. A `Spannable`->span conversion source (Array
# field -> `::tpy::as_mut_span`) and the `Span[T]`->`Span[readonly[T]]` widen
# both carry a coerce outside `_coerce_disposition` -> AST path.


class TestSpanReturn:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    _SPAN = "from tpy import Int32, Span, Array, readonly\n"

    def test_span_name_return_routes_bare(self):
        thir = _lower(
            self._SPAN
            + "def passthru(buf: Span[Int32]) -> Span[Int32]:\n    return buf\n")
        fn = _fn(thir, "passthru")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRName)

    def test_readonly_span_name_return_routes(self):
        # Span[readonly[T]] -> Span[readonly[T]] is identical (no coerce).
        thir = _lower(
            self._SPAN
            + "def ro(buf: Span[readonly[Int32]]) -> Span[readonly[Int32]]:\n"
            + "    return buf\n")
        assert _fn(thir, "ro") is not None

    def test_span_multi_return_routes(self):
        thir = _lower(
            self._SPAN
            + "def pick(a: Span[Int32], b: Span[Int32], f: bool) -> Span[Int32]:\n"
            + "    if f:\n        return a\n    return b\n")
        assert _fn(thir, "pick") is not None

    def test_widen_coerce_return_routes(self):
        # Span[T] -> Span[readonly[T]] widen carries span_to_readonly_span --
        # an identity disposition since the ptr/span coerce cell (the C++
        # span const-widening is implicit).
        thir = _lower(
            self._SPAN
            + "def widen(buf: Span[Int32]) -> Span[readonly[Int32]]:\n"
            + "    return buf\n")
        assert _fn(thir, "widen") is not None

    def test_array_field_convert_return_ineligible(self):
        # An Array field -> span conversion return (::tpy::as_mut_span) carries
        # the spanlike_to_span RETURN coerce -> AST.
        src = (
            self._SPAN
            + "class Buf:\n"
            + "    data: Array[Int32, 4]\n"
            + "    def __init__(self) -> None:\n"
            + "        self.data = Array[Int32, 4]()\n"
            + "    def view(self) -> Span[Int32]:\n        return self.data\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "view") is None

    def test_span_span_return_ineligible(self):
        # Span[Span[Int32]] -- the element is not a scalar -> AST.
        thir = _lower(
            self._SPAN
            + "def outer(buf: Span[Span[Int32]]) -> Span[Span[Int32]]:\n"
            + "    return buf\n")
        assert _fn(thir, "outer") is None

    def test_byte_identical(self):
        src = (
            self._SPAN
            + "def passthru(buf: Span[Int32]) -> Span[Int32]:\n    return buf\n"
            + "def ro(buf: Span[readonly[Int32]]) -> Span[readonly[Int32]]:\n"
            + "    return buf\n"
            + "def pick(a: Span[Int32], b: Span[Int32], f: bool) -> Span[Int32]:\n"
            + "    if f:\n        return a\n    return b\n"
            + "def widen(buf: Span[Int32]) -> Span[readonly[Int32]]:\n"
            + "    return buf\n"
            + "def main() -> None:\n    pass\nmain()\n"
        )
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        assert "std::span<const int32_t> widen(std::span<int32_t> buf)" in thir_cpp


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
        # Call lowering admits a bytes return in value position; the for-each
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


class TestNativeIterableBuiltins:
    # `all`/`any`/`sum` are @native builtins over a structural `Iterable[T]`
    # param; the C++ overload is a template that binds a builtin-container arg
    # BARE (`::tpy::builtin_all(xs)`), no adapter/span wrap -- so a container name
    # into that native slot routes, unlike a plain-TPy Iterable param.
    def test_all_over_name_routes_bare(self):
        thir = _lower(_PRELUDE + "def f(xs: list[bool]) -> bool:\n"
                      "    return all(xs)\n")
        v = _fn(thir, "f").body[0].value
        assert isinstance(v, THIRCall) and v.native_name == "tpy::builtin_all"
        assert isinstance(v.args[0], THIRName) and v.args[0].name == "xs"

    def test_any_over_name_routes_bare(self):
        thir = _lower(_PRELUDE + "def f(xs: list[bool]) -> bool:\n"
                      "    return any(xs)\n")
        v = _fn(thir, "f").body[0].value
        assert isinstance(v, THIRCall) and v.native_name == "tpy::builtin_any"
        assert isinstance(v.args[0], THIRName)

    def test_sum_over_name_routes(self):
        thir = _lower(_PRELUDE + "def f(xs: list[Int32]) -> Int32:\n"
                      "    return sum(xs)\n")
        assert _fn(thir, "f") is not None

    # A genexpr into that Iterable consumer lowers to the make_generator IIFE
    # (`all(x > 0 for x in xs)`), restricted to the lvalue-container / single-var
    # / no-filter / scalar slice.
    def test_genexpr_over_name_routes(self):
        thir = _lower(_PRELUDE + "def f(xs: list[Int32]) -> bool:\n"
                      "    return all(x > 0 for x in xs)\n")
        v = _fn(thir, "f").body[0].value
        assert isinstance(v, THIRCall)
        assert isinstance(v.args[0], THIRGenExpr)

    def test_genexpr_emit_byte_identical(self):
        src = (_PRELUDE + "def f(xs: list[Int32]) -> bool:\n"
               "    return all(x > 0 for x in xs)\n")
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast_cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=False))[1]
        thir_cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))[1]
        assert ast_cpp == thir_cpp
        assert "make_generator<bool>" in thir_cpp

    def test_genexpr_literal_source_moves(self):
        # A container-literal source is a prvalue -> moved_source form (no IIFE).
        thir = _lower(_PRELUDE + "def f() -> Int32:\n"
                      "    return sum(x * x for x in [1, 2, 3, 4])\n")
        v = _fn(thir, "f").body[0].value
        assert isinstance(v.args[0], THIRGenExpr) and v.args[0].moved_source

    def test_genexpr_literal_emit_byte_identical(self):
        src = (_PRELUDE + "def f() -> Int32:\n"
               "    return sum(x * x for x in [1, 2, 3, 4])\n")
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast_cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=False))[1]
        thir_cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))[1]
        assert ast_cpp == thir_cpp
        assert "__started = false" in thir_cpp

    def test_genexpr_range_stays_ast(self):
        # A range() source uses the counter-lambda arm -- outside the slice.
        thir = _lower(_PRELUDE + "def f(n: Int32) -> bool:\n"
                      "    return all(x > 0 for x in range(n))\n")
        assert _fn(thir, "f") is None

    def test_genexpr_filter_stays_ast(self):
        # A filter condition takes the per-iteration temp-flush arm -- deferred.
        thir = _lower(_PRELUDE + "def f(xs: list[Int32]) -> bool:\n"
                      "    return all(x > 0 for x in xs if x < 10)\n")
        assert _fn(thir, "f") is None

    def test_genexpr_dict_source_stays_ast(self):
        # A dict `*__beg` yields a key/value pair, so the scalar loop-var binding
        # would misroute -- the slice excludes dict sources (guards the exclusion:
        # re-adding dict without key-extraction would route + emit wrong C++).
        thir = _lower(_PRELUDE + "def f(d: dict[Int32, Int32]) -> bool:\n"
                      "    return all(k for k in d)\n")
        assert _fn(thir, "f") is None

    def test_genexpr_narrowed_source_stays_ast(self):
        # A narrowed-Optional source is outside the slice -- stays AST.
        thir = _lower(_PRELUDE + "def f(xs: list[Int32] | None) -> bool:\n"
                      "    if xs is None:\n        return False\n"
                      "    return all(x > 0 for x in xs)\n")
        assert _fn(thir, "f") is None

    def test_genexpr_set_source_routes(self):
        # A non-list builtin container (set) routes the lvalue genexpr too.
        thir = _lower(_PRELUDE + "def f(s: set[Int32]) -> bool:\n"
                      "    return all(x > 0 for x in s)\n")
        assert isinstance(_fn(thir, "f").body[0].value.args[0], THIRGenExpr)

    def test_genexpr_captures_outer_local(self):
        # An element reading an outer local captures it (`&t`) in both the IIFE
        # and the inner lambda -- exercises _genexpr_captures past the trivial
        # empty-capture form.
        src = (_PRELUDE + "def f(xs: list[Int32], t: Int32) -> bool:\n"
               "    return all(x > t for x in xs)\n")
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast_cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=False))[1]
        thir_cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))[1]
        assert ast_cpp == thir_cpp
        assert "&t" in thir_cpp


# --- str method returning `Own[list[str]]` as a for-each iterable
#     (`for w in s.split():`) ---

# The method result is an rvalue -- the owning `auto __obj_N =
# ::tpy::str_split_whitespace(s);` capture (iterable_lvalue False, the dict-view
# verdict). The str list ELEMENT resolves to a `std::string_view` loop var like
# any list[str] name. Bytes-receiver splits (`data.split(sep)` -> list[bytes])
# and non-name / field receivers defer.
class TestStrListMethodIterable:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def test_whitespace_split_routes_rvalue_capture(self):
        thir, faces = _lower_ctx_witnessed(
            "def f(s: str) -> None:\n    for w in s.split():\n        print(w)\n")
        loop = _fn(thir, "f").body[0]
        assert isinstance(loop, THIRForEach) and not loop.iterable_lvalue
        assert isinstance(loop.iterable, THIRMethodCall)
        assert faces.get("foreach.str_list_method")

    def test_sep_and_maxsplit_split_route(self):
        thir = _lower_ctx(
            "def f(s: str) -> None:\n"
            "    for w in s.split(','):\n        print(w)\n"
            "    for w in s.split(',', 1):\n        print(w)\n")
        fn = _fn(thir, "f")
        assert all(isinstance(st, THIRForEach) and not st.iterable_lvalue
                   for st in fn.body)

    def test_splitlines_routes(self):
        thir = _lower_ctx(
            "def f(s: str) -> None:\n    for line in s.splitlines():\n        print(line)\n")
        assert _fn(thir, "f") is not None

    def test_bytes_split_iterable_ineligible(self):
        # A bytes receiver returns list[bytes] (a reference-element list); the
        # str-receiver pin rejects it and the elem gate has no bytes-element arm.
        thir = _lower_ctx(
            "def f(data: bytes) -> None:\n"
            "    for chunk in data.split(b','):\n        print(len(chunk))\n")
        assert _fn(thir, "f") is None

    def test_field_receiver_split_ineligible(self):
        # A str-FIELD receiver (`self.name.split()`) is outside the bare-name pin.
        thir = _lower_ctx(
            "class H:\n    def __init__(self, name: str):\n        self.name = name\n"
            "    def go(self) -> None:\n"
            "        for w in self.name.split():\n            print(w)\n")
        assert _fn(thir, "go") is None

    def test_byte_identical(self):
        src = (
            "def f(s: str) -> None:\n"
            "    for w in s.split():\n        print(w)\n"
            "    for w in s.split(','):\n        print(w)\n"
            "    for w in s.split(',', 1):\n        print(w)\n"
            "    for line in s.splitlines():\n        print(line)\n"
            "def main():\n    f('a b c')\nmain()\n")
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        assert "auto __obj_0 = ::tpy::str_split_whitespace(s);" in thir_cpp
        assert "std::string_view w = *__beg_0;" in thir_cpp


# --- Storage container returns (`-> Own[list/dict/set]`) + span params ---


class TestContainerStorageReturn:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def test_bare_owned_name_routes(self):
        thir = _lower(
            "from tpy import Int32, Own\n"
            "def make() -> Own[list[Int32]]:\n"
            "    xs: list[Int32] = [1, 2]\n    xs.append(3)\n    return xs\n")
        fn = _fn(thir, "make")
        assert fn is not None
        ret = fn.body[-1].value
        assert isinstance(ret, THIRName) and ret.name == "xs"

    def test_literal_returns_route(self):
        thir = _lower(
            "from tpy import Int32, Own\n"
            "def rl() -> Own[list[Int32]]:\n    return [1, 2]\n"
            "def re() -> Own[list[Int32]]:\n    return []\n"
            "def rd() -> Own[dict[str, Int32]]:\n    return {'a': 1}\n"
            "def rs() -> Own[set[Int32]]:\n    return {4, 5}\n")
        for name in ("rl", "re", "rd", "rs"):
            fn = _fn(thir, name)
            assert fn is not None, name
            assert isinstance(fn.body[0].value, THIRContainerLiteral), name

    def test_own_container_param_still_ineligible(self):
        # An `Own[list]` PARAM keeps the body on the AST path (the move-in
        # ABI cell `_container_scalar_read` excludes) -- the return-slot
        # widening must not have opened it. A BORROWED or reassigned-alias
        # bare-name source cannot reach the return arm at all: sema rejects
        # `return <borrowed>` at an Own slot without copy(), so the arm's
        # reassigned/pointers checks are defensive.
        thir = _lower(
            "from tpy import Int32, Own\n"
            "def f(xs: Own[list[Int32]]) -> Own[list[Int32]]:\n    return xs\n")
        assert _fn(thir, "f") is None

    def test_record_element_literal_return_ineligible(self):
        # Elements outside the scalar/str slice keep the literal on the AST
        # path (the decl gate's element checks, shared at the return arm).
        thir = _lower(
            "from tpy import Int32, Own\n"
            "class P:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "def f() -> Own[list[P]]:\n    return [P(1)]\n")
        assert _fn(thir, "f") is None

    def test_byte_identical(self):
        src = (
            "from tpy import Int32, Own\n"
            "def make() -> Own[list[Int32]]:\n"
            "    xs: list[Int32] = [1, 2]\n    xs.append(3)\n    return xs\n"
            "def rl() -> Own[list[Int32]]:\n    return [7, 8]\n"
            "def re() -> Own[list[Int32]]:\n    return []\n"
            "def rd() -> Own[dict[str, Int32]]:\n    return {'a': 1}\n"
            "def rs() -> Own[set[Int32]]:\n    return {4, 5}\n"
            "def main():\n"
            "    print(len(make()), len(rl()), len(re()), len(rd()), len(rs()))\n"
            "main()\n")
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        assert "return xs;" in thir_cpp                 # bare owned-name NRVO
        assert "return {7, 8};" in thir_cpp
        assert "return std::vector<int32_t>{};" in thir_cpp
        assert ('return ::tpy::ordered_map<std::string, int32_t>({{"a", 1}});'
                in thir_cpp)
        assert "return ::tpy::ordered_set<int32_t>({4, 5});" in thir_cpp


class TestOwnViewFamReturn:
    def test_own_str_and_bytes_route(self):
        thir = _lower(
            "from tpy import Own\n"
            "def rs() -> Own[str]:\n    return 'hi'\n"
            "def rb() -> Own[bytes]:\n    return b'xy'\n")
        assert _fn(thir, "rs") is not None
        assert _fn(thir, "rb") is not None


class TestSpanParam:
    def test_span_scalar_param_routes(self):
        # Subscript / len on a Span[scalar] param: the list-family emits
        # (bounds-safe operator[], ::tpy::__len__) verbatim.
        thir = _lower(
            "from tpy import Int32, Span\n"
            "def total(values: Span[Int32]) -> Int32:\n"
            "    t: Int32 = 0\n    i: Int32 = 0\n"
            "    while i < len(values):\n"
            "        t += values[i]\n        i += 1\n"
            "    return t\n")
        assert _fn(thir, "total") is not None

    def test_readonly_span_param_routes(self):
        thir = _lower(
            "from tpy import Int32, Span, readonly\n"
            "def first(values: Span[readonly[Int32]]) -> Int32:\n"
            "    return values[0]\n")
        fn = _fn(thir, "first")
        assert fn is not None
        assert isinstance(fn.body[0].value, THIRSubscript)

    def test_record_element_span_routes(self):
        # A `Span[record]` param routes under the compositional gate: the
        # `std::span<P>` by-value signature is AST-emitted and element-neutral,
        # and `len(ps)` renders the bare `::tpy::__len__` on both paths.
        thir = _lower_ctx(
            "from tpy import Int32, Span\n"
            "class P:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "def f(ps: Span[P]) -> Int32:\n    return len(ps)\n")
        assert _fn(thir, "f") is not None


# --- Container subscript writes: `c[k] = v` / `c[k] OP= v` (THIRSetItem) ---


class TestContainerSetItem:
    def test_list_setitem_routes(self):
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32], i: Int32, v: Int32) -> None:\n"
            + "    xs[i] = v\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRSetItem)
        assert isinstance(stmt.target, THIRSubscript)
        assert isinstance(stmt.target.receiver, THIRName)
        assert stmt.target.receiver.name == "xs" and not stmt.target.bounds_safe
        assert isinstance(stmt.value, THIRName) and stmt.value.name == "v"

    def test_dict_str_key_routes(self):
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[str, Int32], k: str) -> None:\n    d[k] = 1\n"
            + "def g(d: dict[Int32, Int32], k: Int32) -> None:\n    d[k] = 2\n")
        assert isinstance(_fn(thir, "f").body[0], THIRSetItem)
        assert isinstance(_fn(thir, "g").body[0], THIRSetItem)

    def test_array_and_span_route(self):
        thir = _lower(
            "from tpy import Int32, Span, Array\n"
            "def f(ar: Array[Int32, 4], sp: Span[Int32], i: Int32) -> None:\n"
            "    ar[i] = 1\n    sp[i] = 2\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert all(isinstance(s, THIRSetItem) for s in fn.body)

    def test_bigint_index_narrows(self):
        # A runtime-BigInt index takes the `.to_fixed_check<int32_t>()` wrap
        # (gen_index_expr's narrow), like the read/del sides.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32], n: int) -> None:\n    xs[n] = 6\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRSetItem)
        assert isinstance(stmt.target.index, THIRCoerce)

    def test_str_value_view_source_copies(self):
        # A view-form str source into an owned-str element takes the explicit
        # `std::string(s)` copy (_view_source_to_owned); a literal lands bare.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[str], s: str) -> None:\n"
            + "    xs[0] = s\n    xs[1] = 'lit'\n")
        fn = _fn(thir, "f")
        copy = fn.body[0].value
        assert isinstance(copy, THIRFormConvert) and copy.form is Form.STORAGE
        assert isinstance(fn.body[1].value, THIRStrLiteral)

    def test_flushable_value_position(self):
        # A temp-hoisting call value routes: the write is a flushable
        # statement position (the AST's single gen_stmt flush point).
        thir = _lower_ctx(
            _F1_RECORDS
            + "def get(a: Leaf) -> Int32:\n    return a.n\n"
            + "def f(xs: list[Int32]) -> None:\n    xs[0] = get(Leaf(4))\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRSetItem)
        assert isinstance(stmt.value, THIRCall)
        assert any(isinstance(a, THIRArgTemp) for a in stmt.value.args)

    def test_slice_assign_ineligible(self):
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32], ys: list[Int32]) -> None:\n"
            + "    xs[0:2] = ys\n")
        assert _fn(thir, "f") is None

    def test_record_element_ineligible(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(xs: list[Leaf], a: Leaf) -> None:\n    xs[0] = a\n")
        assert _fn(thir, "f") is None

    def test_own_container_receiver_ineligible(self):
        thir = _lower(
            "from tpy import Int32, Own\n"
            "def f(xs: Own[list[Int32]]) -> None:\n    xs[0] = 1\n")
        assert _fn(thir, "f") is None


class TestContainerAugSetItem:
    def test_scalar_aug_routes(self):
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32], i: Int32) -> None:\n    xs[i] += 7\n")
        stmt = _fn(thir, "f").body[0]
        assert isinstance(stmt, THIRSetItem)
        binop = stmt.value
        assert isinstance(binop, THIRBinOp) and not binop.paren_wrap
        assert isinstance(binop.left, THIRSubscript)
        assert not stmt.target.bounds_safe and not binop.left.bounds_safe

    def test_bigint_value_takes_cast(self):
        # FixedInt element += BigInt value -> the `({0}).to_fixed_check<T>()`
        # cast on the value, like the name-target arm.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32], n: int) -> None:\n    xs[1] += n\n")
        binop = _fn(thir, "f").body[0].value
        assert binop.right_cast == "({0}).to_fixed_check<int32_t>()"

    def test_str_element_aug_routes(self):
        # `ys[0] += "a"` -- the read-modify-write pair over the resolved str
        # concat (`::tpy::str_concat(<read>, "a")`), NOT the name-target
        # in-place `+=` append.
        thir = _lower(
            _PRELUDE
            + "def f(ys: list[str], t: dict[str, str], k: str) -> None:\n"
            + "    ys[0] += 'a'\n    t['x'] += k\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert all(isinstance(s, THIRSetItem)
                   and isinstance(s.value, THIRBinOp) for s in fn.body)

    def test_record_element_aug_routes_as_field_target(self):
        thir = _lower_ctx(
            _F1_RECORDS
            + "def f(xs: list[Leaf]) -> None:\n    xs[0].n += 1\n")
        fn = _fn(thir, "f")
        # `xs[0].n += 1` is a FIELD target over a record-element subscript
        # receiver -- not the subscript-write shape (no THIRSetItem): it rides
        # the scalar-aug field arm (`_field_over_container_subscript_ok`).
        assert fn is not None
        assert not any(isinstance(s, THIRSetItem) for s in fn.body)


class TestContainerSetItemEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import Int32, Span, Array\n"
        "def f(xs: list[Int32], sp: Span[Int32], ar: Array[Int32, 4],"
        " i: Int32, n: int) -> None:\n"
        "    xs[0] = 1\n"
        "    xs[i] = 2\n"
        "    sp[i] = 3\n"
        "    ar[i] = 4\n"
        "    j = 0\n"
        "    while j < len(xs):\n"
        "        xs[j] = xs[j] + 1\n"
        "        j += 1\n"
        "    xs[n] = 6\n"
        "    xs[i] += 7\n"
        "    k = 0\n"
        "    while k < len(xs):\n"
        "        xs[k] += 1\n"
        "        k += 1\n"
        "def g(ys: list[str], d: dict[str, Int32], t: dict[str, str],"
        " s: str) -> None:\n"
        "    ys[0] = s\n"
        "    ys[1] = 'lit'\n"
        "    t['b'] = s\n"
        "    d['x'] += 1\n"
        "    del d['x']\n"
        "def main() -> None:\n"
        "    xs = [1, 2, 3]\n"
        "    f(xs, xs, Array[Int32, 4](0), 1, 2)\n"
        "    ys = ['a', 'b']\n"
        "    d = {'x': 1}\n"
        "    t = {'x': 'y'}\n"
        "    g(ys, d, t, 's')\n"
        "    print(xs[0], ys[0])\n"
        "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_setitem_family(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::__setitem__(xs, 0, 1);" in cpp        # literal index
        assert "::tpy::__setitem__(xs, i, 2);" in cpp        # dynamic index
        assert "::tpy::__setitem__(sp, i, 3);" in cpp        # span
        assert "::tpy::__setitem__(ar, i, 4);" in cpp        # Array
        # bounds-proven loop write takes the direct operator[] (both sides).
        assert ("xs[static_cast<std::size_t>(j)] = "
                "(::tpy::add_check<int32_t>("
                "xs[static_cast<std::size_t>(j)], 1));" in cpp)
        # BigInt index narrows inside the checked dunder.
        assert ("::tpy::__setitem__(xs, n.to_fixed_check<int32_t>(), 6);"
                in cpp)
        # aug renders the checked read-modify-write pair...
        assert ("::tpy::__setitem__(xs, i, "
                "::tpy::add_check<int32_t>(::tpy::__getitem__(xs, i), 7));"
                in cpp)
        # ...even inside a bounds-proven loop (the AST aug arm never
        # bounds-elides).
        assert ("::tpy::__setitem__(xs, k, "
                "::tpy::add_check<int32_t>(::tpy::__getitem__(xs, k), 1));"
                in cpp)
        # str values: view source copies, literal lands bare; str-keyed dict.
        assert "::tpy::__setitem__(ys, 0, std::string(s));" in cpp
        assert '::tpy::__setitem__(ys, 1, "lit");' in cpp
        assert '::tpy::__setitem__(t, "b", std::string(s));' in cpp
        assert ('::tpy::__setitem__(d, "x", '
                '::tpy::add_check<int32_t>(::tpy::__getitem__(d, "x"), 1));'
                in cpp)
        assert '::tpy::__delitem__(d, "x");' in cpp


# --- Field-access receivers: `self.xs[i]` / `h.d[k] = v` / `del self.d[k]` --
# the subscript read/write/del gates widened past bare names to a one-level
# container field off an admitted receiver name (_field_receiver_ok), typed at
# the field's DECLARED type. ---

_CONTAINER_FIELDS = (
    "from tpy import Int32, readonly\n"
    "from typing import Optional\n"
    "class H:\n"
    "    xs: list[Int32]\n"
    "    d: dict[Int32, Int32]\n"
    "    names: list[str]\n"
    "    maybe: Optional[list[Int32]]\n"
    "    def __init__(self):\n"
    "        self.xs = [1, 2, 3]\n"
    "        self.d = {1: 10}\n"
    "        self.names = ['a', 'b']\n"
    "        self.maybe = None\n"
)


class TestFieldReceiverSubscript:
    def test_self_read_routes(self):
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "    def get(self, i: Int32) -> Int32:\n"
            + "        return self.xs[i]\n"
            + "    @readonly\n"
            + "    def get_ro(self, i: Int32) -> Int32:\n"
            + "        return self.xs[i]\n")
        for name in ("get", "get_ro"):
            sub = _fn(thir, name).body[0].value
            assert isinstance(sub, THIRSubscript) and not sub.bounds_safe
            recv = sub.receiver
            assert isinstance(recv, THIRFieldAccess) and recv.field_cpp == "xs"
            assert isinstance(recv.receiver, THIRSelf) and recv.is_arrow

    def test_param_field_read_routes(self):
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "def f(h: H, k: Int32) -> Int32:\n    return h.d[k]\n")
        sub = _fn(thir, "f").body[0].value
        recv = sub.receiver
        assert isinstance(recv, THIRFieldAccess) and not recv.is_arrow
        assert isinstance(recv.receiver, THIRName) and recv.receiver.name == "h"

    def test_optional_ptr_receiver_routes(self):
        # A PROVEN Optional-ptr borrow receiver renders `h->xs` (the field
        # node's arrow), inside the same subscript emit.
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "def f(h: Optional[H]) -> Int32:\n"
            + "    if h is not None:\n        return h.xs[0]\n"
            + "    return -1\n")
        sub = _fn(thir, "f").body[0].then_body[0].value
        assert isinstance(sub.receiver, THIRFieldAccess) and sub.receiver.is_arrow

    def test_setitem_routes_with_witness(self):
        thir, faces = _lower_ctx_witnessed(
            _CONTAINER_FIELDS
            + "    def put(self, i: Int32, v: Int32) -> None:\n"
            + "        self.xs[i] = v\n"
            + "def g(h: H, k: Int32, v: Int32) -> None:\n    h.d[k] = v\n")
        for name in ("put", "g"):
            stmt = _fn(thir, name).body[0]
            assert isinstance(stmt, THIRSetItem)
            assert isinstance(stmt.target.receiver, THIRFieldAccess)
        assert faces.get("setitem.field_recv") == 2
        assert faces.get("subscript.field_recv", 0) >= 2  # target lowering

    def test_aug_routes(self):
        # `self.d[k] += v` -- the checked read-modify-write pair; bounds_safe
        # forced off on both reads like the name-receiver aug.
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "    def bump(self, k: Int32, v: Int32) -> None:\n"
            + "        self.d[k] += v\n")
        stmt = _fn(thir, "bump").body[0]
        assert isinstance(stmt, THIRSetItem)
        assert isinstance(stmt.value, THIRBinOp)
        assert isinstance(stmt.value.left, THIRSubscript)
        assert not stmt.target.bounds_safe and not stmt.value.left.bounds_safe

    def test_str_element_write_copies(self):
        # A view-form str source into an owned-str element keeps the explicit
        # `std::string(s)` copy over the field receiver.
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "    def put(self, i: Int32, s: str) -> None:\n"
            + "        self.names[i] = s\n")
        stmt = _fn(thir, "put").body[0]
        assert isinstance(stmt.value, THIRFormConvert)
        assert stmt.value.form is Form.STORAGE

    def test_del_routes(self):
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "    def drop(self, k: Int32) -> None:\n"
            + "        del self.d[k]\n")
        stmt = _fn(thir, "drop").body[0]
        assert isinstance(stmt, THIRExprStmt)
        assert isinstance(stmt.expr, THIRCall)
        assert stmt.expr.native_name == "tpy::__delitem__"
        assert isinstance(stmt.expr.args[0], THIRFieldAccess)

    def test_bigint_index_narrows(self):
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "    def at(self, n: int) -> Int32:\n"
            + "        return self.xs[n]\n")
        sub = _fn(thir, "at").body[0].value
        assert isinstance(sub.index, THIRCoerce)

    def test_deep_chain_ineligible(self):
        # `self.inner.ys[i]` -- a two-level receiver chain stays AST
        # (_field_receiver_ok pins the receiver base to a bare name).
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class G:\n    ys: list[Int32]\n"
            "    def __init__(self):\n        self.ys = [5]\n"
            "class H:\n    inner: G\n"
            "    def __init__(self):\n        self.inner = G()\n"
            "    def deep(self, i: Int32) -> Int32:\n"
            "        return self.inner.ys[i]\n"
            "    def deep_put(self, i: Int32) -> None:\n"
            "        self.inner.ys[i] = 1\n")
        assert _fn(thir, "deep") is None and _fn(thir, "deep_put") is None

    def test_narrowed_optional_field_ineligible(self):
        # A narrowed Optional[list] FIELD receiver renders `(*this->maybe)` on
        # the AST path; the gates type at the DECLARED field type (Optional ->
        # family reject) and the narrowing condition itself is unrouted, so
        # the body stays AST.
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "    def read(self) -> Int32:\n"
            + "        if self.maybe is not None:\n"
            + "            return self.maybe[0]\n"
            + "        return -1\n"
            + "    def put(self, v: Int32) -> None:\n"
            + "        if self.maybe is not None:\n"
            + "            self.maybe[0] = v\n")
        assert _fn(thir, "read") is None and _fn(thir, "put") is None

    def test_checked_optional_field_ineligible(self):
        # An UNPROVEN Optional[list] field receiver takes the AST's
        # `::tpy::deref_optional_check(this->maybe)[...]` -- the subscript's
        # runtime-check marker rejects it.
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "    def read(self) -> Int32:\n"
            + "        return self.maybe[0]\n")
        assert _fn(thir, "read") is None

    def test_record_element_field_ineligible(self):
        # A container field whose ELEMENT is a record: the family gate
        # (_container_scalar_read) rejects it -- receiver widening does not
        # open non-value elements.
        thir = _lower_ctx(
            _F1_RECORDS
            + "class G:\n    rs: list[Leaf]\n"
            + "    def __init__(self):\n        self.rs = []\n"
            + "    def put(self, a: Leaf) -> None:\n        self.rs[0] = a\n")
        assert _fn(thir, "put") is None


class TestFieldReceiverSubscriptEmit:
    def _cpp(self, src: str, thir: bool):
        # hpp + cpp: methods of a record emit inline in the header.
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _CONTAINER_FIELDS
        + "    def get(self, i: Int32) -> Int32:\n"
        + "        return self.xs[i]\n"
        + "    def put(self, i: Int32, v: Int32) -> None:\n"
        + "        self.xs[i] = v\n"
        + "    def bump(self, k: Int32, v: Int32) -> None:\n"
        + "        self.d[k] += v\n"
        + "    def rename(self, i: Int32, s: str) -> None:\n"
        + "        self.names[i] = s\n"
        + "    def drop(self, k: Int32) -> None:\n"
        + "        del self.d[k]\n"
        + "    def big(self, n: int) -> Int32:\n"
        + "        return self.xs[n]\n"
        + "def f(h: H, k: Int32, v: Int32) -> None:\n"
        + "    h.d[k] = v\n"
        + "def p(h: Optional[H], v: Int32) -> None:\n"
        + "    if h is not None:\n"
        + "        h.xs[0] = v\n"
        + "def main() -> None:\n"
        + "    h = H()\n"
        + "    h.put(0, 4)\n"
        + "    h.bump(1, 2)\n"
        + "    h.rename(0, 'z')\n"
        + "    h.drop(1)\n"
        + "    f(h, 2, 5)\n"
        + "    p(h, 6)\n"
        + "    print(h.get(0), h.big(1))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_field_receiver_forms(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return ::tpy::__getitem__(this->xs, i);" in cpp
        assert "::tpy::__setitem__(this->xs, i, v);" in cpp
        assert ("::tpy::__setitem__(this->d, k, "
                "::tpy::add_check<int32_t>(::tpy::__getitem__(this->d, k), v));"
                in cpp)
        assert "::tpy::__setitem__(this->names, i, std::string(s));" in cpp
        assert "::tpy::__delitem__(this->d, k);" in cpp
        assert ("return ::tpy::__getitem__(this->xs, "
                "n.to_fixed_check<int32_t>());" in cpp)
        assert "::tpy::__setitem__(h.d, k, v);" in cpp    # record param recv
        assert "::tpy::__setitem__(h->xs, 0, v);" in cpp  # proven Optional-ptr


# --- Storage-call decls and returns (container-returning free calls) ---

# A container-returning free call in the two storage sinks: the decl init
# (`xs = make_list(n)` -> `std::vector<int32_t> xs = make_list(n);`) and the
# return slot (`return make_list(n);`) -- both render the bare call on both
# paths. A reassigned container local is a POINTER-LOCAL on the AST path
# (the two-slot rebind machinery), and a record-element container return is
# outside the literal-decl families -- both stay on the AST path.
class TestContainerCallSlots:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE.replace("import Int32", "import Own, Int32")
        + "def make_list(n: Int32) -> Own[list[Int32]]:\n    return [n, n + 1]\n"
        + "def make_dict() -> Own[dict[Int32, Int32]]:\n    return {1: 2}\n"
        + "def make_set() -> Own[set[Int32]]:\n    return {3, 4}\n"
        + "def fwd(n: Int32) -> Own[list[Int32]]:\n    return make_list(n)\n"
        + "def use(n: Int32) -> Int32:\n"
        + "    xs = make_list(n)\n"
        + "    d = make_dict()\n"
        + "    s = make_set()\n"
        + "    print(len(s))\n"
        + "    return xs[0] + d[1]\n"
        + "def main():\n    print(use(3))\n    print(fwd(1)[1])\nmain()\n"
    )

    def test_decl_and_return_route(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        fn = _fn(thir, "use")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl) and isinstance(decl.init, THIRCall)
        assert _fn(thir, "fwd") is not None
        assert faces.get("decl.storage_call", 0) >= 3   # list + dict + set decls
        assert faces.get("ret.container_call", 0) == 1  # fwd's return

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_bare_call_decl(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::vector<int32_t> xs = make_list(n);" in cpp
        assert "::tpy::ordered_map<int32_t, int32_t> d = make_dict();" in cpp
        assert "::tpy::ordered_set<int32_t> s = make_set();" in cpp
        assert "return make_list(n);" in cpp

    def test_reassigned_container_local_ineligible(self):
        # A reassigned container local takes the AST's pointer-local + rebind
        # slot machinery -- the whole body stays on the AST path.
        src = (
            _PRELUDE.replace("import Int32", "import Own, Int32")
            + "def make_list(n: Int32) -> Own[list[Int32]]:\n    return [n]\n"
            + "def use(n: Int32) -> Int32:\n"
            + "    xs = make_list(n)\n"
            + "    xs = make_list(n + 1)\n"
            + "    return xs[0]\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        cpp_t = self._cpp(src + "def main():\n    print(use(1))\nmain()\n", thir=True)
        cpp_a = self._cpp(src + "def main():\n    print(use(1))\nmain()\n", thir=False)
        assert cpp_t == cpp_a
        assert "std::vector<int32_t>* xs" in cpp_t  # the AST pointer-local shape

    def test_record_element_container_ineligible(self):
        # `list[record]` is outside the literal-decl families (spelled decl
        # type / receiver gates do not line up) -- decl and return reject.
        src = (
            _F1_RECORDS
            + "def make(n: Int32) -> Own[list[Leaf]]:\n    return [Leaf(n)]\n"
            + "def use(n: Int32) -> Int32:\n"
            + "    xs = make(n)\n"
            + "    return len(xs)\n"
            + "def fwd(n: Int32) -> Own[list[Leaf]]:\n    return make(n)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        assert _fn(thir, "fwd") is None


class TestLenFieldReceiver:
    """`len(recv.field)` -- the builtin len over one-level container/str/bytes
    fields (the `for i in range(len(self.xs))` unblock)."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _PRELUDE
        + "class H:\n"
        + "    xs: list[Int32]\n"
        + "    name: str\n"
        + "    data: bytes\n"
        + "    def __init__(self):\n"
        + "        self.xs = [1, 2]\n"
        + "        self.name = 'ab'\n"
        + "        self.data = b'xyz'\n"
        + "    def total(self) -> Int32:\n"
        + "        return len(self.xs)\n"
        + "    def vlen(self) -> Int32:\n"
        + "        return len(self.name) + len(self.data)\n"
        + "    def sum_all(self) -> Int32:\n"
        + "        acc = 0\n"
        + "        for i in range(len(self.xs)):\n"
        + "            acc += self.xs[i]\n"
        + "        return acc\n"
        + "def free_len(h: H) -> Int32:\n"
        + "    return len(h.xs)\n"
        + "def main():\n"
        + "    h = H()\n"
        + "    print(h.total(), h.vlen(), h.sum_all(), free_len(h))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_routes_and_witnesses(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        for name in ("total", "vlen", "sum_all", "free_len"):
            assert _fn(thir, name) is not None, name
        assert wit.get("len.field_recv", 0) >= 4

    def test_emits_len_over_field(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return ::tpy::__len__(this->xs);" in cpp
        assert "return ::tpy::__len__(h.xs);" in cpp
        # The range-len bound hoists to a stop temp like any non-literal bound.
        assert "int32_t __stop_0 = ::tpy::__len__(this->xs);" in cpp

    def test_inherited_container_field_routes(self):
        # `len(b.xs)` where `xs` is declared on the base class:
        # `_field_decl_type` walks the MRO, and the C++ member access
        # renders identically to an own field.
        src = (
            _PRELUDE
            + "class A:\n"
            + "    xs: list[Int32]\n"
            + "    def __init__(self):\n        self.xs = [1]\n"
            + "class B(A):\n"
            + "    def __init__(self):\n        super().__init__()\n"
            + "def f(b: B) -> Int32:\n    return len(b.xs)\n"
            + "def main():\n    b = B()\n    print(f(b))\nmain()\n"
        )
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None

    def test_two_level_chain_rejects(self):
        # `len(h.inner.xs)` -- a chained receiver stays on the AST path.
        src = (
            _PRELUDE
            + "class A:\n"
            + "    xs: list[Int32]\n"
            + "    def __init__(self):\n        self.xs = [1]\n"
            + "class B:\n"
            + "    a: A\n"
            + "    def __init__(self):\n        self.a = A()\n"
            + "def f(b: B) -> Int32:\n    return len(b.a.xs)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_optional_container_field_rejects(self):
        # An Optional[list] field types at the DECLARED Optional -> family
        # reject (the AST unwraps the narrowed read differently).
        src = (
            _PRELUDE
            + "from typing import Optional\n"
            + "class H:\n"
            + "    xs: Optional[list[Int32]]\n"
            + "    def __init__(self):\n        self.xs = None\n"
            + "def f(h: H) -> Int32:\n"
            + "    if h.xs is not None:\n"
            + "        return len(h.xs)\n"
            + "    return 0\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None


class TestContainerFieldIteration:
    """`for x in recv.field:` over container fields -- the field renders inside
    the same lvalue `auto& __obj_N =` capture a name iterable takes."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _PRELUDE
        + "class P:\n"
        + "    v: Int32\n"
        + "    def __init__(self, v: Int32):\n        self.v = v\n"
        + "class H:\n"
        + "    xs: list[Int32]\n"
        + "    d: dict[Int32, Int32]\n"
        + "    names: list[str]\n"
        + "    ps: list[P]\n"
        + "    def __init__(self):\n"
        + "        self.xs = [1, 2]\n"
        + "        self.d = {1: 10}\n"
        + "        self.names = ['a', 'bb']\n"
        + "        self.ps = [P(5)]\n"
        + "    def sums(self) -> Int32:\n"
        + "        acc = 0\n"
        + "        for x in self.xs:\n"
        + "            acc += x\n"
        + "        for k in self.d:\n"
        + "            acc += k\n"
        + "        for s in self.names:\n"
        + "            acc += len(s)\n"
        + "        return acc\n"
        + "    def bump(self) -> None:\n"
        + "        for p in self.ps:\n"
        + "            p.v += 1\n"
        + "def free_iter(h: H) -> Int32:\n"
        + "    acc = 0\n"
        + "    for x in h.xs:\n"
        + "        acc += x\n"
        + "    return acc\n"
        + "def main():\n"
        + "    h = H()\n"
        + "    h.bump()\n"
        + "    print(h.sums(), free_iter(h), h.ps[0].v)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_routes_and_witnesses(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        for name in ("sums", "bump", "free_iter"):
            assert _fn(thir, name) is not None, name
        assert wit.get("foreach.container_field", 0) >= 4

    def test_emits_field_capture(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "auto& __obj_0 = this->xs;" in cpp
        assert "auto& __obj_0 = h.xs;" in cpp
        # Record loop var stays the borrow alias off the field capture.
        assert "auto&& p = *__beg_0;" in cpp

    def test_field_iterable_node(self):
        thir = _lower_ctx(self.SRC)
        loop = _fn(thir, "free_iter").body[1]
        assert isinstance(loop, THIRForEach) and loop.iterable_lvalue
        assert isinstance(loop.iterable, THIRFieldAccess)

    def test_optional_container_field_rejects(self):
        src = (
            _PRELUDE
            + "from typing import Optional\n"
            + "class H:\n"
            + "    xs: Optional[list[Int32]]\n"
            + "    def __init__(self):\n        self.xs = None\n"
            + "def f(h: H) -> Int32:\n"
            + "    acc = 0\n"
            + "    if h.xs is not None:\n"
            + "        for x in h.xs:\n"
            + "            acc += x\n"
            + "    return acc\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None


class TestBytesFieldSubscript:
    """Bytes-family FIELD subscript reads (`self.data[i]` -> UInt8) through the
    `::tpy::bytes_getitem` dispatch, incl. the readonly-method const receiver."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _PRELUDE
        + "from tpy import readonly\n"
        + "class H:\n"
        + "    data: bytes\n"
        + "    def __init__(self):\n"
        + "        self.data = b'xyz'\n"
        + "    @readonly\n"
        + "    def first(self) -> UInt8:\n"
        + "        return self.data[0]\n"
        + "    def at(self, i: Int32) -> UInt8:\n"
        + "        return self.data[i]\n"
        + "def free_at(h: H, i: Int32) -> UInt8:\n"
        + "    return h.data[i]\n"
        + "def main():\n"
        + "    h = H()\n"
        + "    print(h.first(), h.at(1), free_at(h, 2))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_routes_and_witnesses(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        for name in ("first", "at", "free_at"):
            assert _fn(thir, name) is not None, name
        assert wit.get("subscript.bytes_field", 0) >= 3

    def test_emits_bytes_getitem_over_field(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return ::tpy::bytes_getitem(this->data, 0);" in cpp
        assert "return ::tpy::bytes_getitem(this->data, i);" in cpp
        assert "return ::tpy::bytes_getitem(h.data, i);" in cpp


class TestBytesElementRead:
    """Owned-bytes element subscript reads (`chunks[0]` on list[bytes], the
    io.py family): STORAGE form -- owned decl/return sinks copy implicitly,
    view-resolved bindings / span args convert implicitly, all bare."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _PRELUDE
        + "class B:\n"
        + "    _chunks: list[bytes]\n"
        + "    def __init__(self):\n"
        + "        self._chunks = [b'ab', b'cde']\n"
        + "    def owned(self) -> Int32:\n"
        + "        buf: bytes = self._chunks[0]\n"
        + "        return len(buf)\n"
        + "    def first(self) -> bytes:\n"
        + "        return self._chunks[0]\n"
        + "def use(b: bytes) -> Int32:\n"
        + "    return len(b)\n"
        + "def viewed(parts: list[bytes]) -> Int32:\n"
        + "    x = parts[1]\n"
        + "    return len(x)\n"
        + "def arg_pos(parts: list[bytes]) -> Int32:\n"
        + "    return use(parts[0])\n"
        + "def cmp_pos(parts: list[bytes]) -> bool:\n"
        + "    return parts[0] == b'ab'\n"
        + "def dict_val(d: dict[Int32, bytes]) -> Int32:\n"
        + "    v: bytes = d[1]\n"
        + "    return len(v)\n"
        + "def main():\n"
        + "    b = B()\n"
        + "    parts = [b'ab', b'cde']\n"
        + "    print(b.owned(), len(b.first()), viewed(parts))\n"
        + "    print(arg_pos(parts), cmp_pos(parts), dict_val({1: b'q'}))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_routes_and_witnesses(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        for name in ("owned", "first", "viewed", "arg_pos", "cmp_pos",
                     "dict_val"):
            assert _fn(thir, name) is not None, name
        assert wit.get("subscript.bytes_elem", 0) >= 6

    def test_storage_form_no_copy_wrap(self):
        # The element lvalue lands bare -- an owned decl copies implicitly and
        # a return copies implicitly; no ::tpy::bytes_copy wrap on either path.
        cpp = self._cpp(self.SRC, thir=True)
        assert ("std::vector<uint8_t> buf = "
                "::tpy::__getitem__(this->_chunks, 0);") in cpp
        assert "return ::tpy::__getitem__(this->_chunks, 0);" in cpp
        assert ("std::span<const uint8_t> x = "
                "::tpy::__getitem__(parts, 1);") in cpp
        assert "bytes_copy" not in cpp

    def test_element_node_form(self):
        thir = _lower_ctx(self.SRC)
        ret = _fn(thir, "first").body[0]
        assert isinstance(ret.value, THIRSubscript)
        assert ret.value.form is Form.STORAGE

    def test_bytes_view_element_rejects(self):
        # A BytesView-element container keeps the static-storage literal-pin
        # question open -> reject at the family gate.
        src = (
            _PRELUDE
            + "from tpy import BytesView\n"
            + "def f(parts: list[BytesView]) -> Int32:\n"
            + "    x = parts[0]\n"
            + "    return len(x)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_bytes_element_write_rejects(self):
        # Writes keep _container_scalar_read's families; a bytes VALUE write
        # has its own AST wraps -> the body stays on the AST path.
        src = (
            _PRELUDE
            + "def f(parts: list[bytes]) -> None:\n"
            + "    parts[0] = b'zz'\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None


class TestRecordElementSubscript:
    """F1-record element reads (`ps[i]` -> `T&` BORROW): field read/write/aug
    off the element, and the REF_ALIAS borrow-local bind with the AST's
    element-borrow const propagation."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _PRELUDE
        + "from tpy import readonly\n"
        + "class P:\n"
        + "    x: Int32\n"
        + "    def __init__(self, x: Int32):\n        self.x = x\n"
        + "def read_field(ps: list[P]) -> Int32:\n"
        + "    return ps[0].x\n"
        + "def write_field(ps: list[P]) -> None:\n"
        + "    ps[1].x = 42\n"
        + "def aug_field(ps: list[P]) -> None:\n"
        + "    ps[0].x += 1\n"
        + "def bind_mut(ps: list[P]) -> Int32:\n"
        + "    p = ps[0]\n"
        + "    p.x += 5\n"
        + "    return p.x\n"
        + "def bind_ro(ps: list[P]) -> Int32:\n"
        + "    p = ps[0]\n"
        + "    return p.x\n"
        + "def bind_ro_annot(ps: readonly[list[P]]) -> Int32:\n"
        + "    p = ps[0]\n"
        + "    return p.x\n"
        + "def main():\n"
        + "    ps = [P(1), P(2)]\n"
        + "    write_field(ps)\n"
        + "    aug_field(ps)\n"
        + "    print(read_field(ps), bind_mut(ps), bind_ro(ps), bind_ro_annot(ps))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_routes_and_witnesses(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        for name in ("read_field", "write_field", "aug_field", "bind_mut",
                     "bind_ro", "bind_ro_annot"):
            assert _fn(thir, name) is not None, name
        assert wit.get("subscript.record_elem", 0) >= 6

    def test_emits_element_access(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return ::tpy::__getitem__(ps, 0).x;" in cpp
        assert "::tpy::__getitem__(ps, 1).x = 42;" in cpp
        # Aug-assign doubles the target render like the AST substitution.
        assert ("::tpy::__getitem__(ps, 0).x = "
                "::tpy::add_check<int32_t>(::tpy::__getitem__(ps, 0).x, 1);"
                in cpp)

    def test_ref_alias_const_propagation(self):
        # A read-only receiver (const-inferred or readonly-annotated) makes
        # the element alias const; a mutated one binds mutable.
        cpp = self._cpp(self.SRC, thir=True)
        assert "P& p = ::tpy::__getitem__(ps, 0);" in cpp        # bind_mut
        assert "const P& p = ::tpy::__getitem__(ps, 0);" in cpp  # bind_ro*
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "bind_mut").body[0].is_const is False
        assert _fn(thir, "bind_ro").body[0].is_const is True
        assert _fn(thir, "bind_ro_annot").body[0].is_const is True

    def test_field_read_node_shape(self):
        thir = _lower_ctx(self.SRC)
        ret = _fn(thir, "read_field").body[0]
        fa = ret.value
        assert isinstance(fa, THIRFieldAccess) and not fa.is_arrow
        assert isinstance(fa.receiver, THIRSubscript)
        assert fa.receiver.form is Form.BORROW

    def test_optional_element_rejects(self):
        # `list[P | None]` elements take the AST's Optional wraps.
        src = (
            _PRELUDE
            + "class P:\n"
            + "    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n"
            + "def f(ps: list[P | None]) -> Int32:\n"
            + "    q = ps[0]\n"
            + "    if q is not None:\n"
            + "        return q.x\n"
            + "    return 0\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_reassigned_alias_rejects(self):
        # A reassigned element alias is a POINTER (reseat) local -- the
        # subscript source keeps the field-receiver pin there.
        src = (
            _PRELUDE
            + "class P:\n"
            + "    x: Int32\n"
            + "    def __init__(self, x: Int32):\n        self.x = x\n"
            + "def f(ps: list[P]) -> Int32:\n"
            + "    p = ps[0]\n"
            + "    p = ps[1]\n"
            + "    return p.x\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None


# --- set params + dict/set membership (`needle in c` -> `(c.contains(needle))`,
# the resolved_contains arm). A `set[scalar]` param is newly admitted; its len /
# iteration reuse the container machinery, membership routes via `.contains`. ---
class TestMembership:
    def test_set_membership_routes(self):
        thir, faces = _lower_ctx_witnessed(
            _PRELUDE
            + "def f(xs: set[Int32], n: Int32) -> bool:\n    return n in xs\n")
        fn = _fn(thir, "f")
        assert fn is not None
        mem = fn.body[0].value
        assert isinstance(mem, THIRMembership) and not mem.negate
        assert mem.method_cpp == "contains"
        assert isinstance(mem.receiver, THIRName) and mem.receiver.name == "xs"
        assert isinstance(mem.needle, THIRName) and mem.needle.name == "n"
        assert faces.get("binop.membership")

    def test_set_not_in_negates(self):
        thir = _lower(
            _PRELUDE
            + "def f(xs: set[Int32]) -> bool:\n    return 3 not in xs\n")
        mem = _fn(thir, "f").body[0].value
        assert isinstance(mem, THIRMembership) and mem.negate
        assert isinstance(mem.needle, THIRLiteral) and mem.needle.value == 3

    def test_membership_condition_routes(self):
        # `if n in xs:` -- a bool membership's truthiness IS its value render.
        thir = _lower(
            _PRELUDE
            + "def f(xs: set[Int32], n: Int32) -> Int32:\n"
            + "    if n in xs:\n        return 1\n    return 0\n")
        assert _fn(thir, "f") is not None

    def test_dict_membership_routes(self):
        # A dict param is already admitted; `k in d` newly routes via .contains.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[Int32, Int32], k: Int32) -> bool:\n    return k in d\n")
        mem = _fn(thir, "f").body[0].value
        assert isinstance(mem, THIRMembership) and mem.method_cpp == "contains"

    def test_bigint_set_membership_routes(self):
        thir = _lower(
            "def f(xs: set[int], b: int) -> bool:\n    return b in xs\n")
        assert isinstance(_fn(thir, "f").body[0].value, THIRMembership)

    def test_str_needle_routes(self):
        # A str needle (literal or view name) renders bare into
        # `contains(...)` on both paths, like a scalar needle.
        src = (_PRELUDE
               + "def lit(xs: set[str]) -> bool:\n    return \"a\" in xs\n"
               + "def name(xs: set[str], k: str) -> bool:\n    return k in xs\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "lit") is not None
        assert _fn(thir, "name") is not None
        mem = _fn(thir, "lit").body[0].value
        assert isinstance(mem, THIRMembership)

    def test_str_field_needle_routes_byte_identical(self):
        # A str FIELD needle (`p.name in d`) renders the bare member read
        # into contains(...) on both paths -- the owned-str-field-ok
        # position shared with the compare operands.
        src = (
            "class P:\n"
            + "    name: str\n"
            + "    def __init__(self, n: str):\n        self.name = n\n"
            + "def f(p: P, d: set[str]) -> bool:\n    return p.name in d\n"
            + "def g(p: P, d: dict[str, int]) -> bool:\n"
            + "    return p.name not in d\n"
            + "def main():\n"
            + "    print(f(P(\"a\"), {\"a\", \"b\"}), g(P(\"c\"), {\"a\": 1}))\n"
            + "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        assert _fn(thir, "g") is not None
        compiler, modules = _compile(src)
        _, cpp_t = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        compiler, modules = _compile(src)
        _, cpp_a = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=False))
        assert cpp_t == cpp_a
        assert "(d.contains(p.name))" in cpp_t

    def test_set_len_routes(self):
        # len over a set reuses ::tpy::__len__ (element-agnostic); admitted now
        # that the set param routes.
        thir = _lower(
            _PRELUDE
            + "def f(xs: set[Int32]) -> Int32:\n    return len(xs)\n")
        assert _fn(thir, "f") is not None

    def test_set_iteration_routes(self):
        thir = _lower(
            _PRELUDE
            + "def f(xs: set[Int32]) -> Int32:\n"
            + "    total = 0\n    for x in xs:\n        total += x\n    return total\n")
        fn = _fn(thir, "f")
        assert fn is not None and isinstance(fn.body[1], THIRForEach)

    def test_str_dict_needle_routes(self):
        # An owned-str-keyed dict membership (`k in d`, view_key_target=None)
        # renders the bare needle -- routes with the set-needle widening.
        thir = _lower_ctx(
            _PRELUDE
            + "def f(d: dict[str, Int32], k: str) -> bool:\n    return k in d\n")
        assert _fn(thir, "f") is not None

    def test_view_keyed_needle_rejects(self):
        # A VIEW-keyed container threads view_key_target into the needle's
        # literal render (static-storage pin) -- not mirrored, so the body
        # stays AST.
        thir = _lower_ctx(
            "from tpy import StrView\n"
            "def f(xs: set[StrView]) -> bool:\n    return \"a\" in xs\n")
        assert _fn(thir, "f") is None

    def test_set_mutation_routes(self):
        # `.add()` routes through the set-receiver method arm; membership
        # tests keep their own reject pins below. Byte-compared: the arg
        # render must match the AST path (the elem-typed stub wrap).
        src = (_PRELUDE
               + "def f(xs: set[Int32], n: Int32) -> None:\n    xs.add(n)\n"
               + "f({1, 2}, 3)\n")
        assert _fn(_lower(src), "f") is not None
        assert _module_cpp(src, thir=True) == _module_cpp(src, thir=False)

    def test_list_membership_stays_ast(self):
        # list has no `__contains__` member (std::ranges::contains); no
        # resolved_contains -> the membership arm rejects, body stays AST.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> bool:\n    return 3 in xs\n")
        assert _fn(thir, "f") is None


class TestMembershipEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def _both(self, src: str) -> str:
        ast_cpp = self._cpp(src, thir=False)
        assert self._cpp(src, thir=True) == ast_cpp
        return ast_cpp

    SRC = (
        _PRELUDE
        + "def sm(xs: set[Int32], n: Int32) -> bool:\n    return n in xs\n"
        + "def sn(xs: set[Int32]) -> bool:\n    return 3 not in xs\n"
        + "def dm(d: dict[Int32, Int32], k: Int32) -> bool:\n    return k in d\n"
        + "def ss(xs: set[str], s: str) -> bool:\n    return s in xs\n"
        + "def ds(d: dict[str, Int32]) -> bool:\n    return \"x\" in d\n"
        + "def si(xs: set[Int32]) -> Int32:\n"
        + "    total = 0\n    for x in xs:\n        total += x\n    return total\n"
        + "def main():\n"
        + "    s = {1, 2}\n    print(sm(s, 1))\n    print(sn(s))\n    print(si(s))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_contains(self):
        cpp = self._both(self.SRC)
        assert "return (xs.contains(n));" in cpp
        assert "return (!(xs.contains(3)));" in cpp
        assert "return (d.contains(k));" in cpp
        assert "return (xs.contains(s));" in cpp
        assert 'return (d.contains("x"));' in cpp


# --- Container PARAM routing: a `list`/`dict`/`set`/`Array`/`Span` param of ANY
# fully-concrete element routes when its body's uses route, since the by-ref/
# by-span param signature is AST-emitted and element-type-neutral. Each case here
# is a container-of-nonscalar the old element-family gate rejected. ---
class TestCompositionalContainerParam:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def _routes_identical(self, src: str):
        assert _fn(_lower_ctx(src), "f") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    _P = ("from tpy import Int32\nclass P:\n    x: Int32\n"
          "    def __init__(self, x: Int32):\n        self.x = x\n")

    def test_dict_record_value_subscript(self):
        # dict VALUE = record (old dict gate admitted only scalar/str/bytes values).
        self._routes_identical(
            self._P + "def f(d: dict[Int32, P], k: Int32) -> Int32:\n    return d[k].x\n")

    def test_span_record_iter(self):
        # Span[record] (old Span arm admitted only scalar elements).
        self._routes_identical(
            "from tpy import Int32, Span\nclass P:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "def f(rs: Span[P]) -> Int32:\n    t = 0\n"
            "    for r in rs:\n        t += r.x\n    return t\n")

    def test_nested_list_len(self):
        # list[list[Int32]] -- a nested container, no enumerated arm covered it.
        self._routes_identical(
            _PRELUDE
            + "def f(xs: list[list[Int32]]) -> Int32:\n    return len(xs)\n")

    def test_list_union_element_len(self):
        # list of a value union element.
        self._routes_identical(
            "from tpy import Int32, Float64\n"
            "def f(xs: list[Int32 | Float64]) -> Int32:\n    return len(xs)\n")

    def test_set_str_iter(self):
        # set[str] (old set arm admitted only scalar elements).
        self._routes_identical(
            _PRELUDE
            + "def f(rs: set[str]) -> Int32:\n    t = 0\n"
            + "    for r in rs:\n        t += len(r)\n    return t\n")

    def test_dict_char_key_len(self):
        # A Char-keyed dict (old key check admitted only fixed-int/BigInt/owned-str).
        self._routes_identical(
            "from tpy import Int32, Char\n"
            "def f(d: dict[Char, Int32]) -> Int32:\n    return len(d)\n")

    def test_view_keyed_dict_len_routes_subscript_rejects(self):
        # The compositional split: a StrView-keyed dict param is admitted (its
        # signature renders identically), so a `len(d)` body routes -- while a
        # `d["a"]` subscript body stays AST (the body subscript gate rejects the
        # view-keyed static-storage pin). Widening the param gate never routes
        # the divergent read.
        ok = ("from tpy import Int32, StrView\n"
              "def f(d: dict[StrView, Int32]) -> Int32:\n    return len(d)\n")
        self._routes_identical(ok)
        sub = ("from tpy import Int32, StrView\n"
               "def f(d: dict[StrView, Int32]) -> Int32:\n    return d[\"a\"]\n")
        assert _fn(_lower_ctx(sub), "f") is None

    def test_generic_list_param_len_routes(self):
        # The signature renders the generic parameter; len() lowering consumes
        # the container without needing its element representation.
        src = (_PRELUDE
               + "def f[T](xs: list[T]) -> Int32:\n    return len(xs)\n")
        self._routes_identical(src)

    def test_own_container_param_rejects(self):
        # `Own[list]` (move-in `T&&`, a distinct ABI) keeps its reject.
        src = ("from tpy import Int32, Own\n"
               "def f(xs: Own[list[Int32]]) -> Int32:\n    return len(xs)\n")
        assert _fn(_lower_ctx(src), "f") is None


class TestMethodArgLiteralTargets:
    """Numeric-literal method args mirror gen_call_arg's target threading:
    a builtin-stub member threads the RAW param type (a plain BigInt slot
    wraps, an Own-wrapped slot renders bare -- the hint is never
    Own-unwrapped), while a user-record method renders target-less (the AST
    record loop passes target_type=None). Regression pin for the merged
    set-receiver widening x try-routing composition (exceptions/
    key_error_caught divergence)."""

    SRC = (
        "from tpy import Int32\n\n"
        "class C:\n    v: int\n"
        "    def __init__(self) -> None:\n        self.v = 0\n"
        "    def bump(self, by: int) -> None:\n        self.v += by\n\n"
        "def f() -> None:\n"
        "    xs: list[int] = [1]\n"
        "    xs.append(2)\n"
        "    s: set[int] = {1, 2}\n"
        "    s.remove(99)\n"
        "    s.discard(3)\n"
        "    s.add(7)\n"
        "    c = C()\n"
        "    c.bump(5)\n"
    )

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_target_typed_stub_literals(self):
        cpp = self._cpp(self.SRC, thir=True)
        # plain BigInt slots (T substituted) take the ctor wrap
        assert "::tpy::set_remove(s, ::tpy::BigInt(99))" in cpp
        assert "s.erase(::tpy::BigInt(3))" in cpp
        # Own-wrapped slots render bare (the raw hint is not unwrapped)
        assert "s.insert(7)" in cpp
        assert "xs.push_back(2)" in cpp
        # user-record method args stay target-less
        assert "c.bump(5)" in cpp


# --- Decl-init storage calls: container/tuple/union-returning METHOD calls
# (`parts = s.split(",")`) ride the same plain value decl as the free-call
# form; a BORROW container return (`return self._items`, a C++ `T&`) binds a
# `T&` alias on the AST path and must keep the body AST. ---
class TestContainerFromCallDecl:
    _H = (
        "from tpy import Int32, Own\n"
        "class H:\n"
        "    _items: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self._items = [1, 2]\n"
        "    def borrowed(self) -> list[Int32]:\n"
        "        return self._items\n"
        "    def fresh(self) -> Own[list[Int32]]:\n"
        "        return [3, 4]\n"
    )

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_view_method_container_decl_routes(self):
        thir = _lower(
            "from tpy import Int32\n"
            "def f(s: str) -> Int32:\n"
            "    parts = s.split(\",\")\n"
            "    return len(parts)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl) and isinstance(
            decl.init, THIRMethodCall)

    def test_own_method_container_decl_routes(self):
        thir = _lower_ctx(
            self._H
            + "def f(h: H) -> Int32:\n"
            + "    xs = h.fresh()\n"
            + "    return len(xs)\n")
        assert _fn(thir, "f") is not None

    def test_borrow_method_container_decl_ineligible(self):
        # AST binds `std::vector<int32_t>& xs = h.borrowed();` -- the plain
        # copy this arm emits would silently un-alias it.
        thir = _lower_ctx(
            self._H
            + "def f(h: H) -> Int32:\n"
            + "    xs = h.borrowed()\n"
            + "    return len(xs)\n")
        assert _fn(thir, "f") is None

    def test_borrow_free_call_container_decl_ineligible(self):
        # The free-call sibling: a borrow container return at a decl is the
        # AST's `T&` alias, not a copy (regression pin for the storage_call
        # arm's rvalue guard).
        thir = _lower(
            "from tpy import Int32\n"
            "def pick(a: list[Int32]) -> list[Int32]:\n"
            "    return a\n"
            "def f(a: list[Int32]) -> Int32:\n"
            "    xs = pick(a)\n"
            "    return len(xs)\n")
        assert _fn(thir, "f") is None

    def test_reassigned_method_container_decl_ineligible(self):
        # A reassigned container local takes the AST's pointer-local
        # machinery regardless of the init's call kind.
        thir = _lower_ctx(
            self._H
            + "def f(h: H, k: list[Int32]) -> Int32:\n"
            + "    xs = h.fresh()\n"
            + "    xs = k\n"
            + "    return len(xs)\n")
        assert _fn(thir, "f") is None

    def test_byte_identical(self):
        src = (
            self._H
            + "def f(s: str) -> Int32:\n"
            + "    parts = s.split(\",\")\n"
            + "    return len(parts)\n"
            + "def g(h: H) -> Int32:\n"
            + "    xs = h.fresh()\n"
            + "    ys = h.borrowed()\n"
            + "    return len(xs) + len(ys)\n"
            + "def main() -> None:\n"
            + "    h = H()\n"
            + "    print(f(\"a,b\"), g(h))\n"
            + "main()\n")
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        assert "std::vector<std::string> parts = ::tpy::str_split(s, \",\");" in thir_cpp
        assert "std::vector<int32_t>& ys = h.borrowed();" in thir_cpp


# --- Span locals: a value-view decl (`s2 = sp` / `s = get_span(a)` /
# `s = r.view(sp)`) renders the plain spelled copy on both paths. ---
class TestSpanLocalDecl:
    _SPAN = "from tpy import Int32, Span, Array, readonly\n"

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_span_name_copy_decl_routes(self):
        thir = _lower(
            self._SPAN
            + "def f(sp: Span[Int32]) -> Int32:\n"
            + "    s2 = sp\n"
            + "    return s2[0]\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl) and isinstance(decl.init, THIRName)

    def test_span_call_decl_routes(self):
        # The callee takes a routed (list) arg -- a span ARG is its own
        # (unrouted) pass-through row, deliberately not opened here.
        thir = _lower(
            self._SPAN
            + "def view(xs: list[Int32]) -> Span[Int32]:\n"
            + "    return xs\n"
            + "def f(xs: list[Int32]) -> Int32:\n"
            + "    s = view(xs)\n"
            + "    return s[0]\n")
        assert _fn(thir, "f") is not None

    def test_span_method_decl_routes(self):
        thir = _lower_ctx(
            self._SPAN
            + "class R:\n"
            + "    _data: list[Int32]\n"
            + "    def __init__(self) -> None:\n"
            + "        self._data = [1, 2]\n"
            + "    def view(self) -> Span[Int32]:\n"
            + "        return self._data\n"
            + "def f(r: R) -> Int32:\n"
            + "    s = r.view()\n"
            + "    return s[0]\n")
        assert _fn(thir, "f") is not None

    def test_span_of_span_decl_ineligible(self):
        # The element is not an eligible scalar -> the decl gate keeps it AST.
        thir = _lower(
            self._SPAN
            + "def f(sp: Span[Span[Int32]]) -> Int32:\n"
            + "    s2 = sp\n"
            + "    return 0\n")
        assert _fn(thir, "f") is None

    def test_byte_identical(self):
        src = (
            self._SPAN
            + "def view(xs: list[Int32]) -> Span[Int32]:\n"
            + "    return xs\n"
            + "def f(xs: list[Int32]) -> Int32:\n"
            + "    s2 = view(xs)\n"
            + "    s3 = s2\n"
            + "    return s3[0] + s2[1]\n"
            + "def ro(sp: Span[readonly[Int32]]) -> Int32:\n"
            + "    s2 = sp\n"
            + "    return s2[0]\n"
            + "def main() -> None:\n"
            + "    a = [1, 2, 3]\n"
            + "    print(f(a), ro(a))\n"
            + "main()\n")
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        assert "std::span<int32_t> s2 = view(xs);" in thir_cpp
        assert "std::span<const int32_t> s2 = sp;" in thir_cpp


class TestSetitemWidenedValueSlots:
    """Cell: setitem non-scalar value slots -- nested-container literals
    (type-prefixed on the checked path, bare on the bounds-safe lvalue path)
    and the Optional/union borrow->storage element lifts."""

    _RECS = (
        "from tpy import Int32\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "class B:\n"
        "    y: Int32\n"
        "    def __init__(self, y: Int32) -> None:\n        self.y = y\n"
    )

    def _cpp(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_container_literal_value_type_prefix(self):
        src = (
            "from tpy import Int32\n"
            "def f() -> None:\n"
            "    groups: dict[str, list[Int32]] = {}\n"
            "    groups[\"odds\"] = [1, 3, 5]\n"
            "def main() -> None:\n    f()\nmain()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert ('::tpy::__setitem__(groups, "odds", '
                "std::vector<int32_t>{1, 3, 5});") in cpp

    def test_bounds_safe_literal_value_stays_bare(self):
        # The `x[i] = value` lvalue path binds a brace-init directly.
        src = (
            "from tpy import Int32\n"
            "def f() -> None:\n"
            "    rows: list[list[Int32]] = [[0], [0]]\n"
            "    for i in range(len(rows)):\n"
            "        rows[i] = [i, i + 1]\n"
            "def main() -> None:\n    f()\nmain()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert ("rows[static_cast<std::size_t>(i)] = "
                "{i, (::tpy::add_check<int32_t>(i, 1))};") in cpp

    def test_optional_elem_borrow_name_lifts(self):
        src = (self._RECS
               + "def store(xs: list[A | None], p: A | None) -> None:\n"
               + "    xs[0] = p\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "store") is not None
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        assert ("::tpy::__setitem__(xs, 0, ::tpy::ptr_to_optional(p));"
                in self._cpp(src, thir=True))

    def test_union_elem_name_lifts_narrowed_stores_bare(self):
        src = (self._RECS
               + "def store(xs: list[A | B], p: A | B) -> None:\n"
               + "    xs[0] = p\n"
               + "def store_narrowed(xs: list[A | B], p: A | B) -> None:\n"
               + "    if isinstance(p, A):\n"
               + "        xs[0] = p\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "store") is not None
        assert _fn(thir, "store_narrowed") is not None
        cpp = self._cpp(src, thir=True)
        assert cpp == self._cpp(src, thir=False)
        assert ("::tpy::__setitem__(xs, 0, "
                "::tpy::to_value_variant<std::variant<A, B>>(p));") in cpp
        assert "::tpy::__setitem__(xs, 0, __p);" in cpp

    def test_record_rvalue_value_rejects(self):
        # A ctor-rvalue source into an Optional element stays AST (only the
        # borrow-name lift is mirrored).
        thir = _lower_ctx(
            self._RECS
            + "def store(xs: list[A | None]) -> None:\n"
            + "    xs[0] = A(1)\n")
        assert _fn(thir, "store") is None

    def test_nonliteral_container_value_rejects(self):
        # A container NAME source (the copy/move question) stays AST.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def store(m: list[list[Int32]], v: list[Int32]) -> None:\n"
            "    m[0] = v\n")
        assert _fn(thir, "store") is None
