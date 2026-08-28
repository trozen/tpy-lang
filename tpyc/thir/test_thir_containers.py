"""THIR container sites: subscript reads, method calls (THIRMethodCall),
container-literal locals, container call args."""

from __future__ import annotations

import dataclasses

from ..codegen_cpp.context import CodeGenOptions
from ..codegen_cpp.forms import LocalBinding
from .testutil import _assert_byte_identical, _emit_expr
from .nodes import (
    Form, THIRArgTemp, THIRBinOp, THIRCall, THIRCoerce, THIRContainerLiteral,
    THIRExprStmt, THIRFieldAccess, THIRForEach, THIRFormConvert, THIRGenExpr,
    THIRLiteral, THIRMembership, THIRMethodCall, THIRMove, THIRName, THIRReturn,
    THIRStrMembership,
    THIRSelf, THIRSetItem, THIRStrLiteral, THIRSubscript, THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _lower_ctx_witnessed, _fn,
    _PRELUDE, _F1_RECORDS, _assert_byte_identical, _assert_rejects_at,
    _assert_routes_byte_identical,
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

    def test_enum_dict_key_routes(self):
        # The enum KEY twin of the enum-element row above: `dict[Color, V]` /
        # `set[Color]` key-position renders are key-type-neutral (the member
        # spelling `Color::Red` from the shared enum_cpp_name, a variable
        # bare), so literals, subscript reads/writes, del and key iteration
        # all route.
        src = (self._ENUM + _PRELUDE
               + "def read(m: dict[Color, Int32], c: Color) -> Int32:\n"
               + "    return m[c]\n"
               + "def write(m: dict[Color, Int32], c: Color) -> None:\n"
               + "    m[c] = 1\n"
               + "    del m[c]\n"
               + "def keys(m: dict[Color, Int32]) -> Int32:\n"
               + "    t = 0\n"
               + "    for k in m:\n        t += m[k]\n"
               + "    return t\n"
               + "def main():\n"
               + "    d = {Color.Red: 1, Color.Blue: 2}\n"
               + "    print(read(d, Color.Red) + keys(d))\n"
               + "    write(d, Color.Green)\n"
               + "main()\n")
        _assert_routes_byte_identical(src)
        thir = _lower_ctx(src)
        sub = _fn(thir, "read").body[0].value
        assert isinstance(sub, THIRSubscript) and sub.form is Form.VALUE
        assert isinstance(sub.index, THIRName) and sub.index.name == "c"

    def test_enum_set_method_receiver_routes(self):
        # `set[Color]` as a METHOD-CALL receiver: the enum arg renders bare at
        # every method arg/result position (`out.insert(c)`), the same slice
        # the list/dict-value enum element already rides.
        src = (self._ENUM + _PRELUDE
               + "def build(cs: list[Color]) -> Color:\n"
               + "    out: set[Color] = set()\n"
               + "    for c in cs:\n        out.add(c)\n"
               + "    out.discard(Color.Blue)\n"
               + "    out.remove(Color.Red)\n"
               + "    v = out.pop()\n"
               + "    out.clear()\n"
               + "    return v\n"
               # the list arg goes through a local: a container LITERAL at a
               # call arg is a separate un-lowered shape (call.arg_shape.container)
               + "def main():\n"
               + "    cs = [Color.Red, Color.Green]\n"
               + "    print(build(cs).name)\n"
               + "main()\n")
        _assert_routes_byte_identical(src)
        cpp = _module_cpp(src, thir=True)
        assert "out.insert(c);" in cpp
        assert "::tpy::set_remove(out, Color::Red);" in cpp

    def test_enum_keyed_dict_comprehension_still_defers(self):
        # BOUNDARY: the dict-COMPREHENSION key rides its own narrow slice
        # (`_comp_slot_ok`), not the widened `_dict_key_shape_ok`. An enum key
        # there still rejects the body -- a reject-unit, so byte-identity IS
        # the claim.
        src = (self._ENUM + _PRELUDE
               + "def f(cs: list[Color]) -> Int32:\n"
               + "    d = {c: 1 for c in cs}\n"
               + "    return len(d)\n"
               + "def main():\n    print(f([Color.Red]))\nmain()\n")
        _assert_byte_identical(src)
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=True,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        assert compiler._thir_fallback == {"body:expr.dict_comp": 1}

    def test_value_opt_scalar_element_read_routes(self):
        # A value-repr Optional-scalar element read into a value-opt decl slot
        # (`y: Int32 | None = xs[0]`) routes byte-identically via the
        # whole-optional element arm (gated on the value-opt decl position).
        src = (
            _PRELUDE
            + "def f() -> Int32:\n"
            + "    xs: list[Int32 | None] = [1, None]\n"
            + "    y = xs[0]\n"
            + "    return 1\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)



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

    def test_reassigned_str_param_arg_routes(self):
        # A reassigned str param takes the owned-copy prologue respell
        # (`std::string s = std::string(__param_s);`); its reads keep the
        # view-form renders, so the append still wraps `std::string(s)` --
        # a redundant but valid copy, exactly the AST's render.
        src = ("from tpy import Int32\n"
               "def f(xs: list[str], s: str) -> None:\n"
               "    s = 'x'\n    xs.append(s)\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(
            src + "def main() -> None:\n"
                  "    a: list[str] = []\n    f(a, \"y\")\n    print(a[0])\n"
                  "main()\n")

    def test_str_owned_local_arg_routes(self):
        # An owned-str STORAGE source (a concat result) takes gen_call_arg's
        # copy+move-temp cascade -- the OWNED form of the name, told from a
        # view by `declared` + `param_names`; a plain-function local renders
        # exactly like an owned global here.
        src = ("from tpy import Int32\n"
               "def f(xs: list[str], a: str, b: str) -> None:\n"
               "    t = a + b\n    xs.append(t)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("argtemp.own_str", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "std::string __tmp_1{t};" in cpp
        assert "xs.push_back(std::move(__tmp_1));" in cpp

    def test_bytes_owned_local_arg_lands_bare(self):
        # The bytes sibling is a different render, which is why it is its own
        # row: the AST passes an owned bytes lvalue BARE at an `Own[bytes]`
        # element slot (gen_call_arg's inline_template lvalue skip, which only
        # str is carved out of), so the owned-str row must not generalize
        # across the payload.
        src = ("def f(xs: list[bytes], a: bytes, b: bytes) -> None:\n"
               "    t = a + b\n    xs.append(t)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("arg.bytes_owned_name", 0) >= 1
        assert not faces.get("argtemp.own_str")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "xs.push_back(t);" in cpp

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

    def test_reassigned_container_local_routes_ptr_slot(self):
        # A NAME-reassigned container-literal local routes the pointer-local
        # binding (`std::vector<T> __slot_1 = {..}; std::vector<T>* a =
        # &__slot_1;` + the `a = &(b)` reseat) -- the container flavor of
        # RECORD_RVALUE; byte-identity is pinned by the wave file's
        # TestContainerPtrSlot.
        src = (
            _PRELUDE
            + "def f() -> None:\n"
            + "    a = [1, 2]\n    b = [3, 4]\n    a = b\n    a.append(5)\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_lazy_list_repeat_routes(self):
        # A LAZY `ListRepeatType`-resolved repeat (variable count, unmutated):
        # the decl spells the range object and the init drops the from_range
        # wrap the materialized shapes carry.
        src = (_PRELUDE
               + "def f(n: Int32) -> Int32:\n"
               + "    xs = [0] * n\n    return len(xs)\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert ("::tpy::repeat_range<int32_t> xs = "
                "::tpy::repeat_range<int32_t>(n, {0});") in cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["decl.list_repeat_slot"] == 1
        assert faces["list_repeat.lazy"] == 1

    def test_lazy_repeat_for_head_and_print_route(self):
        # The lazy local as a for-head iterable (the universal `__iter__`
        # loop over the lvalue capture) and as a print arg (ListPrinter).
        src = (_PRELUDE
               + "def f(n: Int32) -> None:\n"
               + "    r = [1] * n\n"
               + "    for v in r:\n        print(v)\n"
               + "    print(r)\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "auto& __src_0 = r;" in cpp
        assert "::tpy::ListPrinter(r)" in cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["foreach.list_repeat_local"] == 1

    def test_lazy_repeat_free_call_protocol_arg_routes(self):
        # A repeat RVALUE at a FREE call's structural-protocol slot hoists
        # the un-spelled `auto __tmp_N` structural temp.
        src = (_PRELUDE
               + "from typing import Iterable\n"
               + "def consume(items: Iterable[Int32]) -> Int32:\n"
               + "    total: Int32 = 0\n"
               + "    for v in items:\n        total += v\n"
               + "    return total\n"
               + "def f() -> Int32:\n    return consume([3] * 4)\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "auto __tmp_1 = ({" in cpp
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["argtemp.list_repeat_proto"] == 1

    def test_lazy_variable_count_protocol_arg_routes(self):
        # The VARIABLE-count sibling: the arg stays a lazy repeat_range, so
        # the temp holds the range object rather than the array aggregate.
        src = (_PRELUDE
               + "from typing import Iterable\n"
               + "def consume(items: Iterable[Int32]) -> Int32:\n"
               + "    total: Int32 = 0\n"
               + "    for v in items:\n        total += v\n"
               + "    return total\n"
               + "def f(n: Int32) -> Int32:\n    return consume([3] * n)\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "auto __tmp_1 = ::tpy::repeat_range<int32_t>(n, {3});" in cpp

    def test_lazy_repeat_method_arg_stays_ast(self):
        # The AST's METHOD arg loop passes the repeat INLINE (no temp), so
        # the free-call admission must not reach the method position.
        src = (_PRELUDE
               + "from typing import Iterable\n"
               + "class Sink:\n    total: Int32\n"
               + "    def __init__(self) -> None:\n        self.total = 0\n"
               + "    def take(self, items: Iterable[Int32]) -> None:\n"
               + "        for v in items:\n            self.total += v\n"
               + "def f() -> Int32:\n"
               + "    s = Sink()\n    s.take([5] * 3)\n    return s.total\n")
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        _assert_rejects_at(compiler._thir_fallback, "body:expr.method_call",
                           "method.arg_shape")
        _assert_byte_identical(src)

    def test_reassigned_lazy_repeat_stays_ast(self):
        # A REBOUND lazy local is a pointer-local on the AST path
        # (`repeat_range<T>* r = &__slot_N;`), not the plain spelled copy.
        src = (_PRELUDE
               + "def f(flag: bool, n: Int32) -> Int32:\n"
               + "    r = [1] * n\n"
               + "    if flag:\n        r = [2] * n\n"
               + "    return len(r)\n")
        assert _fn(_lower(src), "f") is None
        _assert_byte_identical(src)

    def test_lazy_repeat_dyn_protocol_arg_stays_ast(self):
        # A @dynamic slot takes the adapter machinery, not the structural
        # temp -- the admission excludes it.
        src = (_PRELUDE
               + "from tpy import dynamic\n"
               + "from typing import Protocol\n"
               + "@dynamic\n"
               + "class DynSized(Protocol):\n"
               + "    def __len__(self) -> Int32: ...\n"
               + "def dyn_len(d: DynSized) -> Int32:\n    return len(d)\n"
               + "def f(n: Int32) -> Int32:\n    return dyn_len([1] * n)\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_list_repeat_materialized_routes(self):
        # A materialized `list[T] = [v] * n` -> from_range(repeat_range(...)).
        src = (_PRELUDE
               + "def f(n: Int32) -> Int32:\n"
               + "    xs: list[Int32] = [7] * n\n    xs.append(1)\n"
               + "    return len(xs)\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_array_repeat_routes(self):
        # An `Array[T, N] = [v] * N` -> the array_from_index statement-expr.
        src = (_PRELUDE + "from tpy import Array\n"
               + "def f() -> Int32:\n"
               + "    a: Array[Int32, 4] = [9] * 4\n    return a[0]\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_repeat_multi_element_routes(self):
        # k>1 repeat: list brace-list of >1 elem, and the Array `__rep_N[__i % k]`
        # modulo-lambda arm (the single-element units miss both).
        src = (_PRELUDE + "from tpy import Array\n"
               + "def f() -> Int32:\n"
               + "    xs: list[Int32] = [1, 2] * 3\n"
               + "    a: Array[Int32, 6] = [1, 2, 3] * 2\n"
               + "    return xs[0] + a[5]\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_repeat_bigint_count_routes(self):
        # A BigInt count checks-converts: `repeat_range<T>(count.to_fixed_check
        # <int32_t>(), ...)` -- the only path exercising `count_bigint`.
        src = (_PRELUDE
               + "def f(n: int) -> Int32:\n"
               + "    xs: list[Int32] = [7] * n\n    xs.append(1)\n"
               + "    return len(xs)\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_repeat_record_element_does_not_move(self):
        # A record element at its LAST USE must NOT move: repeat copies the one
        # source into every slot, so a move would use-after-move slots 1..N-1.
        # The AST's _gen_list_repeat omits _maybe_move; THIR suppresses it.
        # (_lower_ctx: a non-value record needs the live compiler context.)
        src = (_PRELUDE
               + "class Pt:\n    x: Int32\n"
               + "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
               + "def f(n: Int32) -> Int32:\n"
               + "    p = Pt(3)\n    xs: list[Pt] = [p] * n\n    return len(xs)\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_container_alias_decl_ineligible(self):
        # `ys = xs` (container alias) is not a literal init -> AST path.
        thir = _lower(
            _PRELUDE
            + "def f() -> None:\n    xs = [1]\n    ys = xs\n    ys.append(2)\n")
        assert _fn(thir, "f") is None

    def test_literal_operand_binop_routes(self):
        # `ys[0] + ys[2]` -- both operands IntLiteral-typed non-names with a
        # FIXED-int target: the AST emits `add_check<int32_t>(...)` (no fold),
        # and THIR matches it with paren_wrap=False (the call-form render).
        src = (_PRELUDE
               + "def f() -> Int32:\n    ys = [1, 2, 3]\n    return ys[0] + ys[2]\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)



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

    def test_record_dict_value_routes(self):
        # Record dict-literal values route byte-identically (the container-literal
        # record-element value arm).
        src = (_ELEM_RECORDS
               + "def f() -> Int32:\n    d = {1: P(1)}\n    return len(d)\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

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

    def test_optional_record_ctor_none_elements_route(self):
        # A record-inner Optional element in a container STORAGE slot is
        # `std::optional<P>` (value), not the borrow-form `P*` its
        # uses_pointer_repr() describes: a record ctor lands via the implicit
        # `P -> std::optional<P>` and a bare None renders std::nullopt.
        src = (_ELEM_RECORDS
               + "def f() -> Int32:\n"
               + "    xs: list[P | None] = [P(1), None]\n"
               + "    return len(xs)\n"
               + "def main():\n    print(f())\nmain()\n")
        assert _fn(_lower_ctx(src), "f") is not None
        cpp = self._both(src)  # asserts THIR == AST emit
        assert ("std::vector<std::optional<P>> xs = "
                "{P(1), std::nullopt};") in cpp

    def test_optional_record_dict_value_ctor_none_route(self):
        # The dict-VALUE sibling of the list case: `dict[str, P | None]` holds
        # `std::optional<P>` values, a record ctor / None landing the same way.
        src = (_ELEM_RECORDS
               + "def f() -> Int32:\n"
               + "    d: dict[str, P | None] = {\"a\": P(1), \"b\": None}\n"
               + "    return len(d)\n"
               + "def main():\n    print(f())\nmain()\n")
        assert _fn(_lower_ctx(src), "f") is not None
        cpp = self._both(src)  # asserts THIR == AST emit
        assert "std::optional<P>" in cpp and "std::nullopt" in cpp

    def test_optional_record_name_element_ineligible(self):
        # The deferred boundary: a record NAME into a record-inner Optional
        # slot would need the pointer-local deref + last-use move mirror
        # threaded through the Optional inner -- stays on the AST path.
        thir = _lower_ctx(
            _ELEM_RECORDS
            + "def f(p: P) -> Int32:\n"
            + "    xs: list[P | None] = [p]\n    return len(xs)\n")
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

    def test_union_literal_elements_route(self):
        # LITERAL elements into a value-union slot convert implicitly and
        # render bare ({1} into vector<variant<...>>); non-literal union
        # sources (names, calls) still reject.
        src = (
            _PRELUDE
            + "from tpy import Float64\n"
            + "def f() -> Int32:\n"
            + "    xs: list[Int32 | Float64] = [1]\n    return len(xs)\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        cpp = self._both(src)
        assert "xs = {1};" in cpp

    def test_union_name_elements_stay_ineligible(self):
        thir = _lower(
            _PRELUDE
            + "from tpy import Float64\n"
            + "def f(v: Float64) -> Int32:\n"
            + "    xs: list[Int32 | Float64] = [v]\n    return len(xs)\n")
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

    def test_own_container_param_moves_at_last_use(self):
        # An Own[list] slot auto-moves at last use (`consume(std::move(zs))`):
        # _own_lvalue_temp_slot's container-payload branch, the same
        # copy+move row as records (a non-last-use lvalue takes the
        # `auto __tmp_N` copy temp instead).
        src = (
            "from tpy import Int32, Own\n"
            + "def consume(xs: Own[list[Int32]]) -> Int32:\n    return len(xs)\n"
            + "def f() -> Int32:\n    zs = [1]\n    return consume(zs)\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

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

    def test_movable_view_param_elem_makes_vector(self):
        # A narrowed value-Optional-VIEW param element at its last use takes
        # the make_vector escape with the move OUTSIDE the view->owned wrap
        # (`::tpy::make_vector<std::string>(std::move(std::string((*a))))`):
        # seed_param_locals movability is codegen-side, so
        # _container_elem_move_source front-runs the seeded param; the
        # converts apply before THIRMove (wrap-then-move).
        src = (
            "def f(a: str | None) -> None:\n"
            "    if a is not None:\n"
            "        xs: list[str] = [a]\n"
            "        print(xs)\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(src)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert ("::tpy::make_vector<std::string>"
                "(std::move(std::string((*a))))") in cpp

    def test_protocol_param_method_arg_routes(self):
        # list.extend(other: Iterable[Own[T]]): the Iterable-slot name arm
        # decides move-vs-bare at LOWERING from the live movable/last-use
        # facts -- a PARAM (never movable) binds bare
        # (`::tpy::list_extend(xs, ys)`); a movable last-use local takes
        # the consuming `::tpy::own_iter(std::move(b))` wrap (byte-pinned
        # by tests/cases/list/warn_extend_copy).
        src = (
            _PRELUDE
            + "def f(xs: list[Int32], ys: list[Int32]) -> None:\n    xs.extend(ys)\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

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

    def test_array_field_convert_return_routes(self):
        # An Array field -> span conversion return (::tpy::as_mut_span over
        # the bare member read) rides the spanlike-coerce field arm
        # (dualgen-verified byte-identical).
        src = (
            self._SPAN
            + "class Buf:\n"
            + "    data: Array[Int32, 4]\n"
            + "    def __init__(self) -> None:\n"
            + "        self.data = Array[Int32, 4]()\n"
            + "    def view(self) -> Span[Int32]:\n        return self.data\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "view") is not None
        _assert_byte_identical(src)

    def test_span_call_result_routes(self):
        # `span(x)` as a VALUE / decl / for-head source: the value-view
        # prvalue lands bare (call.span_value_ret) and the for-head takes
        # the owning `auto __obj_N = ::tpy::as_span(items);` capture
        # (foreach.span_call). The Spannable protocol-param arg binds bare.
        src = (
            "from tpy import Int32, Spannable, span\n"
            "def span_len(x: Spannable[Int32]) -> Int32:\n"
            "    return len(span(x))\n"
            "def span_sum(x: Spannable[Int32]) -> Int32:\n"
            "    total: Int32 = 0\n"
            "    for v in span(x):\n"
            "        total += v\n"
            "    return total\n"
            "def main() -> None:\n"
            "    items: list[Int32] = [1, 2, 3, 4]\n"
            "    print(span_len(items), span_sum(items))\n"
            "    print(len(span(items)))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "span_len") is not None
        assert _fn(thir, "span_sum") is not None
        assert faces.get("call.span_value_ret")
        assert faces.get("foreach.span_call")
        _assert_routes_byte_identical(src)

    def test_span_call_at_span_slot_still_defers(self):
        # BOUNDARY: a span() rvalue at a Span PARAM slot has no arg row --
        # the body keeps its arg-shape fallback.
        src = (
            "from tpy import Int32, Span, span, readonly\n"
            "def take(sp: Span[readonly[Int32]]) -> Int32:\n"
            "    return len(sp)\n"
            "def main() -> None:\n"
            "    items: list[Int32] = [1, 2, 3]\n"
            "    print(take(span(items)))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is None
        _assert_byte_identical(src)

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

    def test_container_ctor_iterable_routes(self):
        # A container INSTANTIATION at the for-head (`for v in set(xs):`):
        # the ctor prvalue rides the ITERABLE result rung
        # (call.container_ctor_iterable) into the owning `auto __obj_N =`
        # capture.
        src = (
            "from tpy import Int32\n"
            "def f() -> None:\n"
            "    xs: list[Int32] = [3, 1, 2, 3]\n"
            "    for v in set(xs):\n        print(v)\n"
            "f()\n")
        thir, faces = _lower_ctx_witnessed(src)
        loop = _fn(thir, "f").body[1]
        assert isinstance(loop, THIRForEach) and not loop.iterable_lvalue
        assert faces.get("call.container_ctor_iterable")
        _assert_routes_byte_identical(src)

    def test_list_of_generator_call_iterable_routes(self):
        # `for it in list(each(items)):` -- the instantiation's generator
        # arg (call.inst args) under the same ITERABLE ctor rung; ref-T
        # elements bind the borrow alias off the materialized list.
        src = (
            "from tpy import Int32, Comparable, Own, copy\n"
            "from typing import Iterator\n"
            "class Item:\n"
            "    key: Int32\n"
            "    def __init__(self, key: Int32) -> None:\n"
            "        self.key = key\n"
            "    def __lt__(self, other: 'Item') -> bool:\n"
            "        return self.key < other.key\n"
            "def each[T: Comparable](xs: list[T]) -> Iterator[Own[T]]:\n"
            "    for x in xs:\n"
            "        yield copy(x)\n"
            "def main() -> None:\n"
            "    items: list[Item] = [Item(3), Item(1)]\n"
            "    for it in list(each(items)):\n"
            "        print(it.key)\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("call.container_ctor_iterable")
        _assert_routes_byte_identical(src)

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


class TestNarrowedValueOptViewIterable:
    """A proven-narrowed value-repr `str | None` / `bytes | None` NAME at a
    for-head / comprehension source: the container begin/end loop over the
    deref capture (`auto& __obj_N = (*b);`) -- NOT the narrowed-alias
    universal loop (wrong render family for a container payload)."""

    def test_narrowed_bytes_param_for_and_comp_route(self):
        src = (
            "def sum_bytes(b: bytes | None) -> int:\n"
            "    if b is None:\n"
            "        return -1\n"
            "    acc = 0\n"
            "    for x in b:\n"
            "        acc += int(x)\n"
            "    return acc\n"
            "def comp_bytes(b: bytes | None) -> int:\n"
            "    if b is None:\n"
            "        return -1\n"
            "    return sum([int(x) for x in b])\n"
            "def main() -> None:\n"
            "    print(sum_bytes(b\"abc\"), comp_bytes(None))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "sum_bytes") is not None
        assert _fn(thir, "comp_bytes") is not None
        assert faces.get("foreach.narrowed_value_opt_view")
        assert faces.get("comp.narrowed_value_opt_view")
        _assert_routes_byte_identical(src)

    def test_narrowed_str_local_routes(self):
        # The owned-inner LOCAL flavor (`std::optional<std::string>`): the
        # same deref capture over the owned payload.
        src = (
            "def pick(flag: bool) -> str | None:\n"
            "    if flag:\n"
            "        return \"abc\"\n"
            "    return None\n"
            "def count(flag: bool) -> int:\n"
            "    s = pick(flag)\n"
            "    if s is None:\n"
            "        return -1\n"
            "    acc = 0\n"
            "    for c in s:\n"
            "        acc += ord(c)\n"
            "    return acc\n"
            "def main() -> None:\n"
            "    print(count(True), count(False))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "count") is not None
        assert faces.get("foreach.narrowed_value_opt_view")
        _assert_routes_byte_identical(src)

    def test_ptr_repr_narrowed_list_keeps_its_own_leg(self):
        # BOUNDARY: a narrowed POINTER-repr Optional container keeps the
        # landed narrowed_opt leg -- the value-opt admission must not
        # capture it (different route selection, same render family).
        src = (
            "def sum_list(xs: list[int] | None) -> int:\n"
            "    if xs is None:\n"
            "        return -1\n"
            "    acc = 0\n"
            "    for v in xs:\n"
            "        acc += v\n"
            "    return acc\n"
            "def main() -> None:\n"
            "    data: list[int] = [1, 2]\n"
            "    print(sum_list(data), sum_list(None))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "sum_list") is not None
        assert faces.get("foreach.narrowed_opt_listset")
        assert not faces.get("foreach.narrowed_value_opt_view")
        _assert_routes_byte_identical(src)


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

    def test_genexpr_range_routes(self):
        # A range() source takes the counter-lambda arm (`[__i = int32_t(0),
        # __stop = static_cast<int32_t>(n)]` -- the genexpr.range row;
        # former fence, converted when the C4 range cell landed).
        thir = _lower(_PRELUDE + "def f(n: Int32) -> bool:\n"
                      "    return all(x > 0 for x in range(n))\n")
        assert _fn(thir, "f") is not None

    def test_genexpr_filter_routes(self):
        # A filter condition wraps the yield inside the lambda (`if (x < 10)
        # { return ...; }` -- the genexpr.filter row; former fence, converted
        # when the C4 filter cell landed).
        thir = _lower(_PRELUDE + "def f(xs: list[Int32]) -> bool:\n"
                      "    return all(x > 0 for x in xs if x < 10)\n")
        assert _fn(thir, "f") is not None

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

    def test_bytes_split_iterable_routes(self):
        # A bytes receiver returns list[bytes]: the override admits it like
        # the str form (the owning __obj capture) and the shared elem gate
        # pins the bytes element.
        src = (
            "def f(data: bytes) -> None:\n"
            "    for chunk in data.split(b','):\n        print(len(chunk))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

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

    def test_own_container_param_returns_bare(self):
        # The `Own[list]` PARAM binding is spelled by value (`std::vector&&`)
        # and reads bare, so the return slot takes the plain name. A BORROWED
        # or reassigned-alias bare-name source cannot reach the return arm at
        # all: sema rejects `return <borrowed>` at an Own slot without
        # copy(), so the arm's reassigned/pointers checks are defensive.
        src = ("from tpy import Int32, Own\n"
               "def f(xs: Own[list[Int32]]) -> Own[list[Int32]]:\n"
               "    return xs\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _hpp, cpp = _assert_byte_identical(src)
        assert "return xs;" in cpp

    def test_record_element_literal_return_ineligible(self):
        # Elements outside the scalar/str slice keep the literal on the AST
        # path (the decl gate's element checks, shared at the return arm).
        thir = _lower(
            "from tpy import Int32, Own\n"
            "class P:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "def f() -> Own[list[P]]:\n    return [P(1)]\n")
        assert _fn(thir, "f") is None

    def test_record_element_call_return_routes(self):
        # A container-of-records returned FROM A CALL lands bare
        # (`return make();`) -- the whole container returns by value with no
        # per-element conversion, unlike the record-element LITERAL above whose
        # element render stays AST. This is the return-slot call widening.
        thir = _lower_ctx(
            "from tpy import Int32, Own\n"
            "class P:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "def make() -> Own[list[P]]:\n    return [P(1)]\n"
            "def forward() -> Own[list[P]]:\n    return make()\n")
        fn = _fn(thir, "forward")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRCall)

    def test_nested_container_call_return_routes(self):
        # A nested-container return (`list[list[Int32]]`) is also element-blind
        # at the call return sink -- the row above `_container_scalar_read`
        # would reject.
        thir = _lower(
            "from tpy import Int32, Own\n"
            "def rows() -> Own[list[list[Int32]]]:\n    return [[1], [2]]\n"
            "def forward() -> Own[list[list[Int32]]]:\n    return rows()\n")
        fn = _fn(thir, "forward")
        assert fn is not None
        assert isinstance(fn.body[0].value, THIRCall)

    def test_record_element_call_return_byte_identical(self):
        src = (
            "from tpy import Int32, Own\n"
            "class P:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "def make() -> Own[list[P]]:\n    return [P(1)]\n"
            "def forward() -> Own[list[P]]:\n    return make()\n"
            "def main():\n    print(len(forward()))\n"
            "main()\n")
        _assert_byte_identical(src)
        assert "return make();" in self._cpp(src, thir=True)

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


class TestRecordBorrowCallReturnDesignStop:
    # A call returning `T&` (a borrow record) read through a field
    # (`shared(a).x`) composes TRANSIENTLY -- the temp lives to the end of
    # the full expression and nothing binds, so no place/loan reasoning
    # arises. It routes via the DEDICATED field-recv flag (never a blanket
    # record-at-RECEIVER row -- that stays reverted). BINDING the same
    # result also routes now (decl.record_borrow_call, the REF_ALIAS decl
    # arm -- see TestRecordBorrowCallAlias); the former "frontier" design
    # stop is CLOSED, and future borrow-return positions open as their
    # own sink-specific flags.
    def test_borrow_record_call_field_read_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import Int32\n"
            "class Rec:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "def shared(a: Rec) -> Rec:\n    return a\n"
            "def use(a: Rec) -> Int32:\n    return shared(a).x\n"
            "def main() -> None:\n    print(use(Rec(4)))\nmain()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "return shared(a).x;" in cpp


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

    _KEY_PRE = (
        "from tpy import Int32\n"
        "def key_of(name: str, flag: bool | str) -> str:\n"
        "    if isinstance(flag, bool):\n"
        "        return name + (\"!\" if flag else \"?\")\n"
        "    return name + flag\n")

    def test_setitem_index_call_temp_routes(self):
        # A setitem whose INDEX call needs a value-union arg temp: the
        # write sits at a statement, so the temp flushes before the line
        # (`std::variant<bool, std::string> __tmp_N = true;` + the bare
        # call) -- allow_temps threads through the subscript arm's index.
        src = self._KEY_PRE + (
            "def f(d: dict[str, Int32]) -> None:\n"
            "    d[key_of(\"a\", True)] = 1\n"
            "def main() -> None:\n"
            "    d: dict[str, Int32] = {}\n"
            "    f(d)\n"
            "    print(d[\"a!\"])\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_read_decl_index_call_temp_routes(self):
        # The decl-init subscript READ is a flush position too.
        src = self._KEY_PRE + (
            "def f(d: dict[str, Int32]) -> Int32:\n"
            "    x = d[key_of(\"a\", True)]\n"
            "    return x\n"
            "def main() -> None:\n"
            "    d: dict[str, Int32] = {\"a!\": 7}\n"
            "    print(f(d))\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_aug_setitem_index_temp_defers(self):
        # The AUG-assign subscript target lowers temp-free (its target
        # renders twice, so a flushed index temp has no single flush
        # point) -- the temp-needing index keeps the body AST.
        src = self._KEY_PRE + (
            "def f(d: dict[str, Int32]) -> None:\n"
            "    d[key_of(\"a\", True)] += 1\n")
        thir = _lower(src)
        assert _fn(thir, "f") is None

    def test_user_record_setitem_index_temp_defers(self):
        # The user-record __setitem__ flavor lowers its target through
        # the record-getitem arm, which does not forward the flush use --
        # the temp-needing index keeps the body AST.
        src = self._KEY_PRE + (
            "class H:\n"
            "    d: dict[str, Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.d = {}\n"
            "    def __getitem__(self, k: str) -> Int32:\n"
            "        return self.d[k]\n"
            "    def __setitem__(self, k: str, v: Int32) -> None:\n"
            "        self.d[k] = v\n"
            "def f(h: H) -> None:\n"
            "    h[key_of(\"b\", True)] = 2\n")
        thir = _lower(src)
        assert _fn(thir, "f") is None

    _PAIRS_PRE = ("from tpy import Int32\n")

    def test_hoisted_container_unpack_target_routes(self):
        # `for k, v in d.items(): ...` with post-loop v.append: the
        # null-initialized pointer predecl + the per-iteration re-point
        # (frame_ptr_elem) + post-loop deref reads.
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, list[Int32]]) -> None:\n"
               "    for k, v in d.items():\n"
               "        print(k, len(v))\n"
               "    v.append(99)\n"
               "    print(len(v))\n"
               "def main() -> None:\n"
               "    d: dict[str, list[Int32]] = {}\n"
               "    d[\"a\"] = [1, 2]\n"
               "    f(d)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("foreach.hoist_ptr_null", 0) >= 1
        assert faces.get("foreach.hoist_ptr_target", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_container_tuple_chain_print_routes(self):
        # `print(pairs[1][1])`: the borrow lvalue
        # `std::get<1>(__getitem__(pairs, 1))` under the kind-keyed wrap.
        src = self._PAIRS_PRE + (
            "def f(pairs: list[tuple[Int32, list[Int32]]]) -> None:\n"
            "    print(pairs[1][1])\n"
            "def main() -> None:\n"
            "    pairs: list[tuple[Int32, list[Int32]]] = "
            "[(1, [10]), (2, [20])]\n"
            "    f(pairs)\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_container_tuple_chain_decl_defers(self):
        # The chain at a DECL sink (a REF_ALIAS bind) is unwitnessed --
        # stays AST.
        src = self._PAIRS_PRE + (
            "def f(pairs: list[tuple[Int32, list[Int32]]]) -> None:\n"
            "    row = pairs[0][1]\n"
            "    print(row)\n")
        thir = _lower(src)
        assert _fn(thir, "f") is None

    def test_hoisted_unpack_resumable_defers(self):
        # The resumable flavor's hoists are frame fields -- the sync
        # pointer-predecl machinery must not fire there.
        src = ("from tpy import Int32\n"
               "from typing import Iterator\n"
               "def g(d: dict[str, list[Int32]]) -> Iterator[Int32]:\n"
               "    for k, v in d.items():\n"
               "        yield len(v)\n"
               "    yield len(v)\n")
        thir = _lower(src)
        assert _fn(thir, "g") is None

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

    def test_slice_assign_routes(self):
        # `xs[0:2] = ys` routes byte-identically via THIRSliceAssign
        # (list_set_slice).
        src = (_PRELUDE
               + "def f(xs: list[Int32], ys: list[Int32]) -> None:\n"
               + "    xs[0:2] = ys\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_record_element_name_routes(self):
        # The F1-record element family admits the write, a plain record
        # NAME landing bare (`__setitem__(xs, 0, a);`).
        src = (_F1_RECORDS
               + "def f(xs: list[Leaf], a: Leaf) -> None:\n    xs[0] = a\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("setitem.record_name")
        _assert_byte_identical(src)

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

    def test_narrowed_optional_field_read_and_write_route(self):
        # Both halves resolve the narrowed Optional[list] FIELD at its inner
        # (the receiver resolver's narrowed_ok, now threaded from the setitem
        # gate too) and render the `(*this->maybe)` deref -- this pin used to
        # record both halves deferring, then only the write.
        src = (
            _CONTAINER_FIELDS
            + "    def read(self) -> Int32:\n"
            + "        if self.maybe is not None:\n"
            + "            return self.maybe[0]\n"
            + "        return -1\n"
            + "    def put(self, v: Int32) -> None:\n"
            + "        if self.maybe is not None:\n"
            + "            self.maybe[0] = v\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "read") is not None
        stmt = _fn(thir, "put").body[0].then_body[0]
        assert isinstance(stmt, THIRSetItem)
        assert isinstance(stmt.target.receiver, THIRFieldAccess)
        _assert_byte_identical(src)

    def test_checked_optional_field_ineligible(self):
        # An UNPROVEN Optional[list] field receiver takes the AST's
        # `::tpy::deref_optional_check(this->maybe)[...]` -- the subscript's
        # runtime-check marker rejects it.
        thir = _lower_ctx(
            _CONTAINER_FIELDS
            + "    def read(self) -> Int32:\n"
            + "        return self.maybe[0]\n")
        assert _fn(thir, "read") is None

    def test_record_element_field_routes(self):
        # The F1-record element row covers the write off a field receiver
        # too (`__setitem__(this->rs, 0, a);` -- bare NAME).
        src = (_F1_RECORDS
               + "class G:\n    rs: list[Leaf]\n"
               + "    def __init__(self):\n        self.rs = []\n"
               + "    def put(self, a: Leaf) -> None:\n        self.rs[0] = a\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "put") is not None
        assert faces.get("setitem.record_name")
        _assert_byte_identical(src)


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

    def test_record_element_container_call_decl_and_return_route(self):
        # A container-of-records RETURN from a call lands bare (the whole
        # container returns by value, no per-element conversion), and so does
        # the DECL: the element family decides the local's downstream reads,
        # not the slot spelling, so `xs = make(n)` is the plain copy the
        # generalised rvalue-call storage row admits.
        src = (
            _F1_RECORDS
            + "def make(n: Int32) -> Own[list[Leaf]]:\n    return [Leaf(n)]\n"
            + "def use(n: Int32) -> Int32:\n"
            + "    xs = make(n)\n"
            + "    return len(xs)\n"
            + "def fwd(n: Int32) -> Own[list[Leaf]]:\n    return make(n)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert _fn(thir, "fwd") is not None


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

    def test_two_level_chain_routes(self):
        # `len(b.a.xs)` -- the field-chain arg row (every link a plain
        # F1-record member) rides the structural protocol slot bare.
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
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None

    def test_optional_container_field_narrowed_len_routes(self):
        # RE-PINNED ROUTED (retslot track): the NARROWED Optional[list]
        # field read into len's structural slot rides the container-field
        # protocol-slot arm; dualgen-verified byte-identical.
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
            + "def main() -> None:\n"
            + "    h = H()\n"
            + "    h.xs = [1, 2]\n"
            + "    print(f(h))\n"
            + "main()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


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

    def test_optional_container_field_routes_with_one_unwrap(self):
        # The NARROWED Optional flavor of the row: it is a separate arm (the
        # bare-container row is DECLARED-type keyed, so it cannot claim this)
        # and the read owes exactly one `(*h.xs)` unwrap.
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
        assert _fn(thir, "f") is not None
        cpp = self._cpp(src, thir=True)
        assert "auto& __obj_0 = (*h.xs);" in cpp
        assert "(*(*h.xs))" not in cpp
        assert cpp == self._cpp(src, thir=False)


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

    def test_bytes_element_write_routes(self):
        # The owned-bytes value slot: a bytes LITERAL stores the owned
        # render (`bytes_literal_owned`) through the checked setitem.
        src = (
            _PRELUDE
            + "def f(parts: list[bytes]) -> None:\n"
            + "    parts[0] = b'zz'\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert ("::tpy::__setitem__(parts, 0, "
                "::tpy::bytes_literal_owned(\"zz\", 2));" in cpp[1])


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

    def test_optional_element_decl_routes_opt_to_ptr(self):
        # `q = ps[0]` on `list[P | None]`: the OPTIONAL_TO_PTR lift over
        # the bare `__getitem__` read; the None-test compares the pointer.
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
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert ("P* q = ::tpy::optional_to_ptr(::tpy::__getitem__(ps, 0));"
                in cpp[1])
        assert "if ((q != nullptr))" in cpp[1]

    def test_reassigned_alias_routes_as_pointer(self):
        # A reassigned element alias is a POINTER (reseat) local: the decl
        # lifts the element lvalue with the same address-of the reseats take.
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
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("decl.subscript_elem_addr")
        cpp = self._cpp(src, thir=True)
        assert "P* p = &(::tpy::__getitem__(ps, 0));" in cpp
        assert "p = &(::tpy::__getitem__(ps, 1));" in cpp
        _assert_byte_identical(src)


class TestNestedContainerSubscript:
    """Nested container indexing (`m[i][j]`): the receiver `m[i]` is a
    container-element subscript yielding a container borrow lvalue, indexed
    again -> nested `::tpy::__getitem__`. The subscript-receiver twin of the
    field-over-record-element-subscript arm (`ps[i].x`)."""

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def read2(m: list[list[Int32]]) -> Int32:\n"
        + "    return m[0][1]\n"
        + "def main():\n"
        + "    m: list[list[Int32]] = [[1, 2], [3, 4]]\n"
        + "    print(read2(m))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "read2") is not None

    def test_emits_nested_getitem(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert ("return ::tpy::__getitem__(::tpy::__getitem__(m, 0), 1);"
                in cpp)

    def test_slice_receiver_ineligible(self):
        # A slice receiver (`m[:][0]`) is not a plain container-element
        # subscript -- it stays on the AST path.
        thir = _lower(
            _PRELUDE
            + "def f(m: list[list[Int32]]) -> list[Int32]:\n    return m[:][0]\n")
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

    def test_view_keyed_needle_routes(self):
        # A VIEW-keyed container's membership now routes (the view-key
        # family): the str literal needle renders bare either way, so the
        # static-storage pin is a no-op for the str family.
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import StrView\n"
            "def f(xs: set[StrView]) -> bool:\n    return \"a\" in xs\n"
            "def main() -> None:\n"
            "    xs: set[StrView] = set()\n"
            "    xs.add(\"a\")\n"
            "    print(f(xs))\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert '(xs.contains("a"))' in cpp

    def test_set_mutation_routes(self):
        # `.add()` routes through the set-receiver method arm; membership
        # tests keep their own reject pins below. Byte-compared: the arg
        # render must match the AST path (the elem-typed stub wrap).
        src = (_PRELUDE
               + "def f(xs: set[Int32], n: Int32) -> None:\n    xs.add(n)\n"
               + "f({1, 2}, 3)\n")
        assert _fn(_lower(src), "f") is not None
        assert _module_cpp(src, thir=True) == _module_cpp(src, thir=False)

    def test_list_membership_routes(self):
        # list has no `__contains__` member -- the native NativeIterable
        # fallback renders `std::ranges::contains(recv, needle)`, like a set.
        thir = _lower(
            _PRELUDE
            + "def f(xs: list[Int32]) -> bool:\n    return 3 in xs\n")
        mem = _fn(thir, "f").body[0].value
        assert isinstance(mem, THIRMembership) and mem.ranges_contains
        assert isinstance(mem.needle, THIRLiteral) and mem.needle.value == 3

    def test_str_membership_is_substring_not_ranges_contains(self):
        # A str receiver is native-iterable (char sequence) but membership on
        # it is SUBSTRING (`.find()`), NOT element-containment -- it must route
        # via THIRStrMembership, never the ranges_contains native-iterable arm
        # (regression guard: `"x" in str(e)` wrongly used std::ranges::contains
        # and diverged, tplib/requests_timeout).
        src = _PRELUDE + "def f(s: str) -> bool:\n    return \"ab\" in s\n"
        mem = _fn(_lower(src), "f").body[0].value
        assert isinstance(mem, THIRStrMembership)
        assert not (isinstance(mem, THIRMembership)
                    and getattr(mem, "ranges_contains", False))
        _assert_byte_identical(src)


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

    def test_view_keyed_dict_len_and_subscript_route(self):
        # A StrView-keyed dict param: `len(d)` routes, and since the
        # view-key family landed the `d["a"]` subscript read routes too
        # (the str literal key renders bare at the view target -- the
        # static-storage pin is a no-op for the str family).
        ok = ("from tpy import Int32, StrView\n"
              "def f(d: dict[StrView, Int32]) -> Int32:\n    return len(d)\n")
        self._routes_identical(ok)
        sub = ("from tpy import Int32, StrView\n"
               "def f(d: dict[StrView, Int32]) -> Int32:\n    return d[\"a\"]\n")
        self._routes_identical(sub)

    def test_generic_list_param_len_routes(self):
        # The signature renders the generic parameter; len() lowering consumes
        # the container without needing its element representation.
        src = (_PRELUDE
               + "def f[T](xs: list[T]) -> Int32:\n    return len(xs)\n")
        self._routes_identical(src)

    def test_own_container_param_routes(self):
        # `Own[list]` is the move-in `T&&` ABI, a distinct SIGNATURE -- but
        # the body reads it exactly like the borrowed sibling above, bare.
        src = ("from tpy import Int32, Own\n"
               "def f(xs: Own[list[Int32]]) -> Int32:\n    return len(xs)\n")
        self._routes_identical(src)


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
# form; a BORROW container return (`return self._items`, a C++ `T&`) binds
# the `T&` alias via the method-call REF_ALIAS arm (free calls stay AST). ---
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

    def test_borrow_method_container_decl_binds_alias(self):
        # AST binds `std::vector<int32_t>& xs = h.borrowed();` -- the borrow
        # method-call REF_ALIAS arm binds the same alias (never the plain
        # copy that would silently un-alias it).
        thir = _lower_ctx(
            self._H
            + "def f(h: H) -> Int32:\n"
            + "    xs = h.borrowed()\n"
            + "    return len(xs)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl) and isinstance(
            decl.init, THIRMethodCall)
        assert decl.cpp_local_representation is LocalBinding.REF_ALIAS

    def test_borrow_free_call_container_decl_routes(self):
        # The free-call sibling ROUTES since the container
        # borrow-call cell: the `T&` alias decl (formerly the
        # rvalue-guard fence).
        src = (
            "from tpy import Int32\n"
            "def pick(a: list[Int32]) -> list[Int32]:\n"
            "    return a\n"
            "def f(a: list[Int32]) -> Int32:\n"
            "    xs = pick(a)\n"
            "    return len(xs)\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        from .testutil import _assert_byte_identical
        _assert_byte_identical(src)

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

    def test_readonly_span_projection_decl_routes(self):
        # `ro.__span__()` on a `Span[readonly[T]]` stamps a STACKED
        # `readonly[readonly[Int32]]` element; the decl slot, span value,
        # and subscript element checks all peel it (_peel_readonly).
        src = (self._SPAN
               + "def f() -> None:\n"
               + "    a: Array[Int32, 3] = [7, 8, 9]\n"
               + "    ro: Span[readonly[Int32]] = a\n"
               + "    s = ro.__span__()\n"
               + "    print(len(s), s[0])\n"
               + "f()\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)

    def test_record_elem_span_projection_stays_out(self):
        # BOUNDARY: the span VALUE/read slice stays scalar-only -- a
        # record-element readonly-span projection keeps rejecting (its
        # reads have no admitted arm).
        src = (self._SPAN
               + "class P:\n"
               + "    x: Int32\n"
               + "    def __init__(self, x: Int32):\n        self.x = x\n"
               + "def f(sp: Span[readonly[P]]) -> None:\n"
               + "    s = sp.__span__()\n"
               + "    print(len(s))\n")
        thir = _lower(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

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


class TestBytearraySurface:
    """The bytearray receiver family: ctor decl (storage_call), name print
    (ByteArrayPrinter), member natives (`append` -> push_back, bare `clear`),
    function=True natives (`pop`/`insert` -> ::tpy::bytearray_*), and the
    subscript write's own native dunder (::tpy::bytearray_setitem, never the
    containers' checked template)."""

    SRC = (
        "def main() -> None:\n"
        "    ba = bytearray(b'\\x01\\x02\\x03')\n"
        "    print(ba)\n"
        "    print(len(ba))\n"
        "    ba.append(4)\n"
        "    ba[0] = 10\n"
        "    popped = ba.pop()\n"
        "    print(popped)\n"
        "    ba.insert(1, 20)\n"
        "    ba.clear()\n"
        "main()\n"
    )

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_emits_bytearray_renders(self):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert "std::vector<uint8_t> ba = ::tpy::bytes_copy(" in cpp
        assert "::tpy::ByteArrayPrinter(ba)" in cpp
        assert "ba.push_back(4);" in cpp
        assert "::tpy::bytearray_setitem(ba, 0, 10);" in cpp
        assert "uint8_t popped = ::tpy::bytearray_pop(ba);" in cpp
        assert "::tpy::bytearray_insert(ba, 1, 20);" in cpp
        assert "ba.clear();" in cpp

    def test_list_setitem_keeps_checked_template(self):
        # The bytearray dispatch must not leak into the container families.
        _assert_byte_identical(
            "from tpy import Int32\n"
            "def f(xs: list[Int32], i: Int32) -> None:\n"
            "    xs[i] = 5\n")

    def test_extend_bytearray_arg_routes(self):
        # `a.extend(b)` -- a bytearray NAME joined the native-iterable
        # bare-bind family (a std::vector<uint8_t> binds the runtime
        # template like any vector), converting this former fence.
        thir = _lower_ctx(
            "def f(a: bytearray, b: bytearray) -> None:\n"
            "    a.extend(b)\n")
        assert _fn(thir, "f") is not None


class TestForSliceIterable:
    """The for-head slice-subscript arm: owning `auto __obj_N =` capture of
    the slice rvalue -- list, stepped, and the newly-reachable str-slice
    for-head all render byte-identically."""

    def test_list_slice_loop(self):
        src = ("from tpy import Int32\n"
               "def f(xs: list[Int32]) -> Int32:\n"
               "    n: Int32 = 0\n"
               "    for x in xs[1:3]:\n        n += x\n"
               "    return n\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_stepped_slice_loop(self):
        src = ("from tpy import Int32\n"
               "def f(xs: list[Int32]) -> Int32:\n"
               "    n: Int32 = 0\n"
               "    for x in xs[1:10:2]:\n        n += x\n"
               "    return n\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_str_slice_loop(self):
        # Newly reachable via the classifier arm (no container-family gate
        # there; the render arm carries the family restriction) -- pin the
        # byte-identity so a later narrowing can't silently regress it.
        src = ("def f(s: str) -> None:\n"
               "    for c in s[1:]:\n        print(c)\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)


class TestValueOptElemContainer:
    """The value-opt-element container chain (`list[Int32 | None]`): the
    setitem-None store, the append/insert None args, the element None-test,
    and the unproven checked-unwrap -- plus the deferred boundaries."""

    _HDR = "from tpy import Int32\nfrom typing import Optional\n"

    def test_setitem_none_stores_nullopt(self):
        src = (self._HDR
               + "def f(items: list[Optional[Int32]]) -> None:\n"
               + "    items[0] = None\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_append_insert_none(self):
        src = (self._HDR
               + "def f(items: list[Optional[Int32]]) -> None:\n"
               + "    items.append(None)\n"
               + "    items.insert(0, None)\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_elem_none_test_and_checked_unwrap(self):
        src = (self._HDR
               + "def f(items: list[Optional[Int32]]) -> Int32:\n"
               + "    if items[1] is not None:\n"
               + "        return items[1] + 1\n"
               + "    return 0\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_dict_scalar_optional_value(self):
        src = (self._HDR
               + "def f(d: dict[str, Optional[Int32]]) -> None:\n"
               + "    if d[\"k\"] is None:\n"
               + "        print(\"none\")\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_scalar_value_setitem_deferred(self):
        # Non-None value sources into the optional element stay deferred
        # (setitem.optval_value_shape) until their renders are witnessed.
        src = (self._HDR
               + "def f(items: list[Optional[Int32]]) -> None:\n"
               + "    items[0] = 5\n")
        assert _fn(_lower(src), "f") is None
        _assert_byte_identical(src)

    def test_view_elem_receiver_routes_but_its_arg_rows_still_gate(self):
        # The METHOD RECEIVER is element-blind (the render never spells the
        # element), so the value-opt-VIEW family reaches the gate like any
        # other container. What actually protects this family is the ARG
        # side, which keeps deciding per shape -- so the body routes only
        # as far as its arg rows allow, and the surrounding read positions
        # (setitem, for-each) keep their own element gates.
        src = ("from typing import Optional\n"
               + "def f(items: list[Optional[str]]) -> None:\n"
               + "    items.append(None)\n")
        # The routing assertion is the pin: AST fallback and THIR routing
        # render this body byte-identically, so identity alone would not
        # notice the receiver widening being reverted.
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_view_elem_arg_and_read_positions_still_defer(self):
        # The boundary that matters after the receiver widening: an
        # Optional[str] ELEMENT still gates at the arg / setitem / for-each
        # positions, which is where its render actually differs.
        src = ("from typing import Optional\n"
               + "from tpy import Int32\n"
               + "def read(items: list[Optional[str]]) -> Int32:\n"
               + "    total = 0\n"
               + "    for it in items:\n"
               + "        if it is not None:\n"
               + "            total += len(it)\n"
               + "    return total\n"
               + "def main() -> None:\n"
               + "    ys: list[Optional[str]] = [\"b\"]\n"
               + "    print(read(ys))\n"
               + "main()\n")
        assert _fn(_lower(src), "read") is None
        _assert_byte_identical(src)

class TestGenericRecordSetItem:
    """The open-T user-record setitem value slot (tplib ArrayList as the
    monomorphized-generic fixture): eligibility keys on the SUBSTITUTED
    element; record elements (the move machinery) stay deferred."""

    _HDR = ("from tpy import Int32\n"
            "from tplib import ArrayList\n")

    def test_scalar_element_routes(self):
        src = (self._HDR
               + "def f() -> None:\n"
               + "    a = ArrayList[Int32, 4]()\n"
               + "    a.append(10)\n    a.append(20)\n"
               + "    a[1] = 99\n"
               + "    print(a[1])\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_record_element_setitem_deferred(self):
        src = (self._HDR
               + "class R:\n    x: Int32\n"
               + "    def __init__(self) -> None:\n        self.x = 1\n"
               + "def f() -> None:\n"
               + "    a = ArrayList[R, 4]()\n"
               + "    a.append(R())\n"
               + "    a[0] = R()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)


class TestProtocolUnionCtorArg:
    """A NAME into an all-protocols union ctor slot: F1-record names bind
    bare, Span names take the address-of lift; containers stay deferred."""

    _HDR = ("from tpy import Int32, Span, Array\n"
            "from tplib import ArrayList\n")

    def test_record_name_binds_bare(self):
        src = (self._HDR
               + "def f() -> None:\n"
               + "    a = ArrayList[Int32, 4]()\n"
               + "    a.append(1)\n"
               + "    b = ArrayList[Int32, 4](a)\n"
               + "    print(b[0])\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_span_name_takes_addr_of(self):
        src = (self._HDR
               + "def f() -> None:\n"
               + "    arr: Array[Int32, 3] = [1, 2, 3]\n"
               + "    s: Span[Int32] = arr\n"
               + "    d = ArrayList[Int32, 8](s)\n"
               + "    print(d[0])\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_container_name_deferred(self):
        src = (self._HDR
               + "def f() -> None:\n"
               + "    xs = [1, 2, 3]\n"
               + "    d = ArrayList[Int32, 8](xs)\n"
               + "    print(d[0])\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)


class TestRecordElementSetItem:
    """F1-record element/value slots at the checked `__setitem__`: exact /
    covariant record rvalues, `copy(name)` copy-constructs, and plain record
    names (bare copy / last-use move) -- the requests-cluster pool-seeding
    family plus the copy_warnings_own_param shapes."""

    _PT = (
        "from tpy import Int32, copy\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.x = 0\n"
    )

    _COV = (
        "from tpy import Int32, dynamic\n"
        "from typing import Protocol\n"
        "from tplib import Box\n"
        "@dynamic\n"
        "class Conn(Protocol):\n"
        "    def ping(self) -> Int32: ...\n"
        "class Tcp(Conn):\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 1\n"
        "    def ping(self) -> Int32:\n"
        "        return self.n\n"
    )

    def test_exact_record_rvalue(self):
        src = (self._PT
               + "def f() -> None:\n"
               + "    items: list[Point] = [Point()]\n"
               + "    items[0] = Point()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("setitem.record_rvalue")
        _assert_byte_identical(src)

    def test_covariant_box_rvalue_dict_value(self):
        # The pool-seeding shape: `pool[key] = Box(conn)` into a
        # dict[str, Box[Proto]] value slot -- the converting move.
        src = (self._COV
               + "def f() -> None:\n"
               + "    pool: dict[str, Box[Conn]] = {}\n"
               + "    c = Tcp()\n"
               + '    pool["k"] = Box(c)\n')
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("setitem.record_rvalue")
        _assert_byte_identical(src)

    def test_record_name_copy_and_copy_builtin(self):
        # `items[0] = p` (bare copy, copy-warned) and `items[0] = copy(p)`
        # (the copy-construct rvalue `Point(p)`).
        src = (self._PT
               + "def f(p: Point) -> None:\n"
               + "    items: list[Point] = [Point()]\n"
               + "    items[0] = p\n"
               + "    items[0] = copy(p)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("setitem.record_name")
        assert faces.get("setitem.record_copy")
        _assert_byte_identical(src)

    def test_append_copy_builtin_arg(self):
        # `items.append(copy(p))` -> `items.push_back(Point(p));` -- the
        # copy-construct rvalue binding the Own[T] slot.
        src = (self._PT
               + "def f(p: Point) -> None:\n"
               + "    items: list[Point] = []\n"
               + "    items.append(copy(p))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("own.record_copy")
        _assert_byte_identical(src)

    def test_field_access_value_defers(self):
        # A field-access source is outside the vetted value shapes -- the
        # body falls back whole (byte-identical via the AST emit).
        src = (self._PT
               + "class H:\n"
               + "    pt: Point\n"
               + "    def __init__(self) -> None:\n"
               + "        self.pt = Point()\n"
               + "def f(h: H) -> None:\n"
               + "    items: list[Point] = [Point()]\n"
               + "    items[0] = h.pt\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)


class TestRecordElementSetItemMove:
    """The move half of the setitem record-NAME arm: an owned record local
    at its LAST use moves into the element (`__setitem__(items, 0,
    std::move(p));` -- the AST's _maybe_move on the setitem value path)."""

    def test_owned_local_last_use_moves(self):
        src = ("from tpy import Int32\n"
               "class Point:\n"
               "    x: Int32\n"
               "    def __init__(self) -> None:\n"
               "        self.x = 0\n"
               "def f() -> None:\n"
               "    items: list[Point] = [Point()]\n"
               "    p = Point()\n"
               "    items[0] = p\n"
               "    print(items[0].x)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("setitem.record_name")
        _assert_byte_identical(src)


class TestInstantiationGenFactoryArg:
    """`list(gen())` -- a generator-factory rvalue into a container
    instantiation renders the bare factory call inside the construct
    template; builtin iterator factories (map/filter) take the
    instantiation ladder's own combinator row (call.inst_iter_arg)."""

    _GEN = ("from typing import Iterator\n"
            "from tpy import Int32\n\n"
            "def gen(n: Int32) -> Iterator[Int32]:\n"
            "    i: Int32 = 0\n"
            "    while i < n:\n"
            "        yield i\n"
            "        i += 1\n\n")

    def test_gen_factory_arg_routes(self):
        src = (self._GEN
               + "def f() -> None:\n"
               + "    xs = list(gen(3))\n"
               + "    print(xs)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("call.inst_gen_arg")
        _assert_byte_identical(src)

    def test_builtin_map_factory_routes_combinator_row(self):
        # map/filter combinator rvalues take the iterator row -- a distinct
        # face from the plain generator-factory arm.
        src = ("from tpy import Int32\n\n"
               "def add(a: Int32, b: Int32) -> Int32:\n"
               "    return a + b\n\n"
               "def f() -> None:\n"
               "    xs: list[Int32] = [1, 2]\n"
               "    ys: list[Int32] = [3, 4]\n"
               "    print(list(map(add, xs, ys)))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("call.inst_iter_arg")
        assert not faces.get("call.inst_gen_arg")
        _assert_byte_identical(src)


class TestOwnElementSlotArgs:
    """Container-method args at `Own[T]` element slots: a dict/set literal
    renders its own spelled container, and a scalar into a value-repr
    `Optional[scalar]` slot passes bare (std::optional converts)."""

    def test_dict_literal_own_slot_routes(self):
        src = ("def main() -> None:\n"
               "    ds = [{1: 2}]\n"
               "    ds.append({3: 4})\n"
               "    print(len(ds))\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        _assert_byte_identical(src)

    def test_set_literal_own_slot_routes(self):
        src = ("def main() -> None:\n"
               "    ss = [{1, 2}]\n"
               "    ss.append({3, 4})\n"
               "    print(len(ss))\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        _assert_byte_identical(src)

    def test_scalar_into_value_opt_element_routes(self):
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    xs: list[Int32 | None] = []\n"
               "    xs.append(Int32(1))\n"
               "    xs.append(None)\n"
               "    print(len(xs))\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is not None
        _assert_byte_identical(src)

    def test_record_element_literal_still_defers(self):
        # BOUNDARY: the coincidence this row rests on (the literal renders
        # the same off the slot and off its own type) holds only for the
        # container families -- a record-element source keeps its own rows.
        src = ("from tpy import Int32\n"
               "class P:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def main() -> None:\n"
               "    ps = [[P(1)]]\n"
               "    ps.append([P(2)])\n"
               "    print(len(ps))\n"
               "main()\n")
        _assert_byte_identical(src)


class TestStructProtoUnionArg:
    """A NAME into a slot that is a union of STRUCTURAL protocols
    (`ArrayList.extend`'s `Spannable[T] | Iterable[Own[T]]`) renders BARE at a
    user-method call: the C++ method is a template whose concept picks the
    branch. The CTOR position keeps its address-of lift."""

    _AL = ("from tpy import Int32, UInt32\n"
           "from tplib.array_list import ArrayList\n")

    def test_method_union_slot_arg_routes_bare(self):
        src = (self._AL
               + "def use() -> Int32:\n"
               + "    a = ArrayList[Int32, 8]()\n"
               + "    b = ArrayList[Int32, 8]()\n"
               + "    b.append(1)\n"
               + "    a.extend(b)\n"
               + "    return Int32(len(a))\n"
               + "print(use())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("method.struct_proto_union_arg")
        _assert_byte_identical(src)
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(src)
        _, cpp = compiler.generate_code_to_strings(
            _entry(modules), options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert "a.extend(b);" in cpp
        assert "a.extend(&(b));" not in cpp

    def test_span_name_union_slot_arg_routes_bare(self):
        # The Span source flavor at the same method slot.
        src = (self._AL
               + "from tpy import Array, span\n"
               + "def use() -> Int32:\n"
               + "    c = ArrayList[Int32, 8]()\n"
               + "    arr: Array[Int32, 3] = [1, 2, 3]\n"
               + "    s = span(arr)\n"
               + "    c.extend(s)\n"
               + "    return Int32(len(c))\n"
               + "print(use())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("method.struct_proto_union_arg")
        _assert_byte_identical(src)

    def test_movable_last_use_container_source_routes_bare(self):
        # The load-bearing coincidence: a LAST-USE (movable) container into a
        # union carrying `Iterable[Own[T]]` renders bare on both paths only
        # because the AST's consuming own_iter rewrite is keyed on a BARE
        # protocol ptype. This pin fails the day that keying widens.
        src = (self._AL
               + "def use() -> Int32:\n"
               + "    a = ArrayList[Int32, 8]()\n"
               + "    src: list[Int32] = [1, 2]\n"
               + "    a.extend(src)\n"
               + "    return Int32(len(a))\n"
               + "print(use())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("method.struct_proto_union_arg")
        _assert_byte_identical(src)


class TestContainerFieldBareRead:
    # A container FIELD reads bare at the two positions where the read IS the
    # whole render: the for-head (begin()/end() taken off it) and the
    # `std::ranges::contains` haystack. Both key on `_field_receiver_ok`, so a
    # nested receiver chain stays out.
    _BAG = (
        "from tpy import Int32\n"
        "class Node:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n        self.val = v\n"
        "class Bag:\n"
        "    nodes: list[Node]\n"
        "    xs: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self.nodes = [Node(1)]\n        self.xs = [1, 2]\n")

    def test_container_field_membership_routes(self):
        src = (self._BAG
               + "    def has(self, item: Int32) -> bool:\n"
               + "        return item in self.xs\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("binop.membership_container_field", 0) == 1
        _assert_byte_identical(
            src + "def main() -> None:\n    print(Bag().has(1))\nmain()\n")

    def test_nested_field_receiver_still_defers(self):
        # BOUNDARY: both arms ride `_field_receiver_ok`, which admits a bare
        # NAME receiver only -- a nested `self.inner.xs` chain is unvetted and
        # must keep falling back at both positions.
        src = ("from tpy import Int32\n"
               "class Inner:\n"
               "    xs: list[Int32]\n"
               "    def __init__(self) -> None:\n        self.xs = [1, 2]\n"
               "class Outer:\n"
               "    inner: Inner\n"
               "    def __init__(self) -> None:\n"
               "        self.inner = Inner()\n"
               "    def has(self, item: Int32) -> bool:\n"
               "        return item in self.inner.xs\n"
               "    def total(self) -> Int32:\n"
               "        n = 0\n"
               "        for x in self.inner.xs:\n            n = n + x\n"
               "        return n\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "has") is None
        assert _fn(thir, "total") is None


class TestRecordRvalueNeedleMembership:
    """A record ctor RVALUE needle (`Key(1) in s`) renders bare inside
    `contains(...)` exactly like the record NAME needle beside it -- the needle
    is a value position, so the ctor's own emit IS the whole render."""

    _SRC = ("from dataclasses import dataclass\n"
            "from tpy import Int32, Own\n"
            "@dataclass(frozen=True)\n"
            "class Key:\n"
            "    n: Int32\n")

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_ctor_needle_set_literal_routes(self):
        src = (self._SRC
               + "def f() -> None:\n"
               + "    print(Key(1) in {Key(1), Key(2)})\n"
               + "f()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("binop.membership")
        assert ".contains(Key(1))" in self._cpp(src, thir=True)
        _assert_byte_identical(src)

    def test_ctor_needle_set_name_routes(self):
        src = (self._SRC
               + "def f(s: set[Key]) -> None:\n"
               + "    print(Key(1) in s)\n"
               + "    print(Key(9) not in s)\n"
               + "f({Key(1)})\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = self._cpp(src, thir=True)
        assert "s.contains(Key(1))" in cpp
        assert "!(s.contains(Key(9)))" in cpp
        _assert_byte_identical(src)

    def test_ctor_needle_dict_routes(self):
        src = (self._SRC
               + "def f(d: dict[Key, Int32]) -> None:\n"
               + "    print(Key(3) in d)\n"
               + "f({Key(3): 1})\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        assert "d.contains(Key(3))" in self._cpp(src, thir=True)
        _assert_byte_identical(src)

    def test_non_record_rvalue_needle_still_defers(self):
        # BOUNDARY: the widening is the RECORD-rvalue row. A tuple needle is a
        # different render (and `.items()` membership is its own later cell),
        # so it must keep rejecting.
        src = (self._SRC
               + "def f(d: dict[Key, Int32]) -> None:\n"
               + "    print((Key(3), 1) in d.items())\n"
               + "f({Key(3): 1})\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_method_call_rvalue_needle_still_defers(self):
        # BOUNDARY: `_record_rvalue_source_shape` admits a by-value
        # record-returning METHOD call at its other call sites, but this needle
        # row does not reach it -- the widening is the CTOR row only. Probed,
        # not assumed: an Own-returning method needle still falls back.
        src = (self._SRC
               + "class Mint:\n"
               + "    def __init__(self) -> None:\n        pass\n"
               + "    def make(self, n: Int32) -> Own[Key]:\n"
               + "        return Key(n)\n"
               + "def f(s: set[Key], m: Mint) -> None:\n"
               + "    print(m.make(1) in s)\n"
               + "f({Key(1)}, Mint())\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)


class TestArrayMoveThroughDecl:
    """Array last-use alias decl (`decl.move_through_array`): sema marks the
    expensive-copy value container move-through, so the decl spells the type
    and moves (`std::array<int32_t, 3> b = std::move(a);`). A non-last-use
    alias is a plain copy row -- unported, stays AST."""

    def test_last_use_alias_moves(self):
        src = ("def main() -> None:\n"
               "    a = [1, 2, 3]\n"
               "    b = a\n"
               "    print(len(b))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("decl.move_through_array")
        _assert_byte_identical(src)

    def test_live_source_alias_binds_reference(self):
        # A LIVE-source alias never takes the move-through arm; it now
        # binds the demoted-array reference (`std::array<...>& b = a;`)
        # via the alias-container row.
        src = ("def main() -> None:\n"
               "    a = [1, 2, 3]\n"
               "    b = a\n"
               "    print(len(b))\n"
               "    print(len(a))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert not faces.get("decl.move_through_array")
        cpp = _assert_byte_identical(src)
        assert "std::array<int32_t, 3>& b = a;" in cpp[1]


class TestDelItemElementBlindReceiver:
    """`del c[k]` never constructs, converts or reads the element slot, so the
    container receiver is admitted ELEMENT-BLIND and the key slice alone
    decides byte-parity -- the generalization of the dict[K, Any] row."""

    SRC = _PRELUDE + (
        "from tplib import ArrayList\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def main() -> None:\n"
        "    recs: list[P] = [P(1), P(2)]\n"
        "    del recs[0]\n"
        "    print(len(recs))\n"
        "    nested: list[list[Int32]] = [[1], [2]]\n"
        "    del nested[0]\n"
        "    print(len(nested))\n"
        "    dmap: dict[Int32, P] = {1: P(3)}\n"
        "    del dmap[1]\n"
        "    print(len(dmap))\n"
        "    a = ArrayList[Int32, 8]()\n"
        "    a.append(10)\n"
        "    del a[0]\n"
        "    print(len(a))\n"
        "main()\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        # record / container / dict-value elements all ride the one row...
        assert faces["delitem.container"] >= 3
        # ...and the GENERIC user record rides the __delitem__ row (its
        # instantiation reaches no part of the bare-receiver render).
        assert faces["delitem.user_record"] >= 1

    def test_renders_the_bare_helper_call(self):
        cpp = _module_cpp(self.SRC, thir=True)
        assert "::tpy::__delitem__(recs, 0);" in cpp
        assert "::tpy::__delitem__(a, 0);" in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_own_container_receiver_still_rejects(self):
        # The boundary: an `Own[container]` receiver is the move-in ABI, whose
        # C++ shape differs from the borrow the del emit assumes.
        src = _PRELUDE + (
            "from tpy import Own\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def take(rows: Own[list[P]]) -> Int32:\n"
            "    del rows[0]\n"
            "    return len(rows)\n"
            "def main() -> None:\n"
            "    print(take([P(1), P(2)]))\n"
            "main()\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "take") is None
        assert faces.get("delitem.container", 0) == 0
        _assert_byte_identical(src)


class TestPrintTupleRecordElement:
    """An F1-record tuple element at a print sink streams through the record's
    operator<<. The only question is whether the element read hands back the
    value or a pointer to it -- borrow-form derefs, storage-form is already
    the value."""

    SRC = _PRELUDE + (
        "class Pt:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "    def __repr__(self) -> str:\n"
        "        return \"Pt(\" + str(self.x) + \")\"\n"
        "class Holder:\n"
        "    points: tuple[Pt, Pt]\n"
        "    def __init__(self, a: Pt, b: Pt) -> None:\n"
        "        self.points = (a, b)\n"
        "def main() -> None:\n"
        "    p = Pt(1)\n"
        "    t = (Int32(0), p)\n"
        "    print(t[1])\n"
        "    p.x = 9\n"
        "    print(t[1])\n"
        "    h = Holder(Pt(2), Pt(3))\n"
        "    print(h.points[0])\n"
        "    print(t[0])\n"
        "main()\n"
    )

    def test_routes_and_witnesses(self):
        thir, faces = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "main") is not None
        assert faces["print.tuple_record_elem"] >= 3

    def test_borrow_element_derefs_and_storage_element_does_not(self):
        cpp = _module_cpp(self.SRC, thir=True)
        assert "std::cout << (*std::get<1>(t)) << " in cpp
        assert "std::cout << std::get<0>(h.points) << " in cpp
        # the value-scalar element keeps its own row -- no deref
        assert "std::cout << std::get<0>(t) << " in cpp

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_value_scalar_element_is_not_claimed(self):
        # The boundary: only an F1-RECORD element rides this row; a scalar
        # element streams through the ordinary print-form dispatch.
        src = _PRELUDE + (
            "def main() -> None:\n"
            "    t = (Int32(1), Int32(2))\n"
            "    print(t[0])\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("print.tuple_record_elem", 0) == 0
        _assert_byte_identical(src)


class TestMembershipFieldAndIterRows:
    """The wave-3 membership rows: a container-FIELD haystack composes into
    the same `.contains` member as a name receiver
    (`si.tags.contains(Tag("a", 1))`); a user `__contains__` off a
    call-rvalue field receiver chains the member call over the postfix
    field read; a user iterable with NO `__contains__` takes the AST's
    universal `__iter__`+`__next__` statement-expression loop (scalar
    needle over a bare declared name only -- a str needle keeps the
    whole-body fallback)."""

    _COMMON = (
        "from tpy import Int32, UInt64, Own\n"
        "class Tag:\n"
        "    name: str\n"
        "    n: Int32\n"
        "    def __init__(self, name: str, n: Int32):\n"
        "        self.name = name\n"
        "        self.n = n\n"
        "    def __eq__(self, other: Tag) -> bool:\n"
        "        return self.name == other.name and self.n == other.n\n"
        "    def __hash__(self) -> UInt64:\n"
        "        return UInt64(self.n)\n"
        "class Holder:\n"
        "    tags: set[Tag]\n"
        "    lookup: dict[Tag, str]\n"
        "    def __init__(self):\n"
        "        self.tags = {Tag(\"a\", 1)}\n"
        "        self.lookup = {Tag(\"a\", 1): \"x\"}\n"
    )

    def test_container_field_haystack_routes(self):
        src = (self._COMMON
               + "def probe(h: Holder) -> None:\n"
               + "    print(Tag(\"a\", 1) in h.tags)\n"
               + "    print(Tag(\"z\", 9) not in h.tags)\n"
               + "    print(Tag(\"a\", 1) in h.lookup)\n"
               + "probe(Holder())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        assert faces.get("binop.contains_field_recv", 0) >= 3
        _assert_byte_identical(src)

    _JAR = (
        "from tpy import Own\n"
        "class Jar:\n"
        "    keys: set[str]\n"
        "    def __init__(self):\n"
        "        self.keys = {\"k\"}\n"
        "    def __contains__(self, name: str) -> bool:\n"
        "        return name in self.keys\n"
        "class JarBox:\n"
        "    jar: Jar\n"
        "    def __init__(self):\n"
        "        self.jar = Jar()\n"
        "def make_box() -> Own[JarBox]:\n"
        "    return JarBox()\n"
    )

    def test_user_contains_call_recv_field_routes(self):
        src = (self._JAR
               + "def probe() -> None:\n"
               + "    print(\"k\" in make_box().jar)\n"
               + "    print(\"z\" not in make_box().jar)\n"
               + "probe()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        assert faces.get("field.call_recv", 0) >= 2
        assert faces.get("binop.user_membership", 0) >= 2
        _assert_byte_identical(src)

    def test_contains_recv_field_over_nonrecord_call_still_defers(self):
        # The receiver row requires an F1-RECORD-returning call under the
        # field (_field_over_call_ok); a field off a LIST-returning call
        # is outside it, so the membership keeps the whole-body fallback.
        src = (self._JAR
               + "def boxes() -> Own[list[JarBox]]:\n"
               + "    return [JarBox()]\n"
               + "def probe() -> None:\n"
               + "    print(\"k\" in boxes()[0].jar)\n"
               + "probe()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is None
        _assert_byte_identical(src)

    _BUF = (
        "from tpy import Int32, Own\n"
        "class Buf:\n"
        "    a: Int32\n"
        "    b: Int32\n"
        "    def __init__(self):\n"
        "        self.a = 10\n"
        "        self.b = 20\n"
        "    def __iter__(self) -> Own[BufIter]:\n"
        "        return BufIter(self)\n"
        "class BufIter:\n"
        "    src: Buf\n"
        "    i: Int32\n"
        "    def __init__(self, src: Buf):\n"
        "        self.src = src\n"
        "        self.i = 0\n"
        "    def __next__(self) -> Int32 | None:\n"
        "        self.i += 1\n"
        "        if self.i == 1:\n"
        "            return self.src.a\n"
        "        if self.i == 2:\n"
        "            return self.src.b\n"
        "        return None\n"
    )

    def test_iter_loop_membership_routes(self):
        src = (self._BUF
               + "def probe(b: Buf) -> None:\n"
               + "    print(10 in b)\n"
               + "    print(99 not in b)\n"
               + "probe(Buf())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        assert faces.get("binop.iter_membership", 0) >= 2
        _assert_byte_identical(src)

    def test_iter_loop_emit(self):
        src = (self._BUF
               + "def probe(b: Buf) -> None:\n"
               + "    print(10 in b)\n"
               + "probe(Buf())\n")
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert "auto&& __itr = ::tpy::__iter__(b);" in cpp
        assert "if (::tpy::unwrap_ref(*__r) == 10)" in cpp

    def test_str_needle_iterable_still_defers(self):
        # A non-scalar needle keeps the whole-body fallback: the AST's
        # view_key_target threading over the loop compare is unmirrored.
        src = (self._BUF.replace("Int32 | None", "Int32 | None")
               + "class Bag:\n"
               + "    a: str\n"
               + "    def __init__(self):\n"
               + "        self.a = \"x\"\n"
               + "    def __iter__(self) -> Own[BagIter]:\n"
               + "        return BagIter(self)\n"
               + "class BagIter:\n"
               + "    src2: Bag\n"
               + "    done: bool\n"
               + "    def __init__(self, src2: Bag):\n"
               + "        self.src2 = src2\n"
               + "        self.done = False\n"
               + "    def __next__(self) -> Int32 | None:\n"
               + "        if self.done:\n"
               + "            return None\n"
               + "        self.done = True\n"
               + "        return 1\n"
               + "def probe(b: Bag) -> None:\n"
               + "    print(\"x\" in b)\n"
               + "probe(Bag())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is None
        assert not faces.get("binop.iter_membership")
        _assert_byte_identical(src)

    def test_protocol_param_receiver_routes(self):
        # A structural Iterable[T] param receiver takes the same universal
        # loop over the bare monomorphized param -- the AST's is_native_in
        # admits only tpy.NativeIterable to ranges::contains.
        src = ("from typing import Iterable\n"
               "from tpy import Array, Int32\n"
               "def contains_value(items: Iterable[Int32],"
               " target: Int32) -> bool:\n"
               "    return target in items\n"
               "def go() -> None:\n"
               "    arr: Array[Int32, 3] = [10, 20, 30]\n"
               "    print(contains_value(arr, 20))\n"
               "go()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "contains_value") is not None
        assert faces.get("binop.iter_membership", 0) >= 1
        _assert_byte_identical(src)

    def test_native_iterable_param_still_defers(self):
        # BOUNDARY: a tpy.NativeIterable param takes the AST's
        # ranges::contains arm, which this leg does not mirror.
        src = ("from tpy import Array, Int32, NativeIterable\n"
               "def contains_value(items: NativeIterable[Int32],"
               " target: Int32) -> bool:\n"
               "    return target in items\n"
               "def go() -> None:\n"
               "    arr: Array[Int32, 3] = [10, 20, 30]\n"
               "    print(contains_value(arr, 30))\n"
               "go()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "contains_value") is None
        _assert_byte_identical(src)


class TestScalarPtrOptBinding:
    """The scalar-pointee widening of _opt_pointee_wide: `v = d.get("a")`
    on a value dict binds the borrow `int32_t* v = ::tpy::dict_get(...)`
    (decl.opt_call_passthrough), whole-optional sinks read the bare
    pointer (None tests, print_optional, optional pass-throughs), and a
    NARROWED value read derefs `(*v)` -- the one consumer shape the wide
    pointee classes never had. A reassigned local keeps the slot
    machinery (fallback)."""

    _P = ("from tpy import Int32\n"
          "def probe(d: dict[str, Int32]) -> Int32:\n")

    def test_decl_and_narrowed_deref_route(self):
        src = (self._P
               + "    v = d.get(\"a\")\n"
               + "    if v is not None:\n"
               + "        return v + 1\n"
               + "    return -1\n"
               + "print(probe({\"a\": 10}))\n")
        thir, faces = _lower_ctx_witnessed(src)
        fn = _fn(thir, "probe")
        assert fn is not None
        assert faces.get("decl.opt_call_passthrough", 0) >= 1
        _assert_byte_identical(src)
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert "int32_t* v = ::tpy::dict_get(d, \"a\");" in cpp
        assert "(::tpy::add_check<int32_t>((*v), 1))" in cpp

    def test_whole_optional_sinks_stay_bare(self):
        src = ("from tpy import Int32\n"
               "def take(v: Int32 | None) -> Int32:\n"
               "    if v is None:\n"
               "        return -1\n"
               "    return v\n"
               "def probe(d: dict[str, Int32]) -> Int32 | None:\n"
               "    v = d.get(\"a\")\n"
               "    print(v, v is None)\n"
               "    print(take(d.get(\"a\")))\n"
               "    return d.get(\"b\")\n"
               "print(probe({\"a\": 1, \"b\": 2}))\n")
        thir, _faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        _assert_byte_identical(src)

    def test_reassigned_local_still_defers(self):
        src = (self._P
               + "    v = d.get(\"a\")\n"
               + "    print(v)\n"
               + "    v = d.get(\"b\")\n"
               + "    print(v)\n"
               + "    return 0\n"
               + "print(probe({\"a\": 1}))\n")
        thir, _faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is None
        _assert_byte_identical(src)

    def test_unwired_whole_optional_sinks_defer(self):
        # The batch-3 Critical's boundary: an UNPROVEN whole read of the
        # scalar ptr-opt binding at a value-repr Optional sink (`return v`
        # / a value-opt param arg) rejects -- the AST lifts via
        # ptr_to_optional_move, unmirrored; a deref would be UB on None.
        src = ("from tpy import Int32\n"
               "def take(v: Int32 | None) -> Int32:\n"
               "    if v is None:\n"
               "        return -1\n"
               "    return v\n"
               "def ret_bound(d: dict[str, Int32]) -> Int32 | None:\n"
               "    v = d.get(\"a\")\n"
               "    return v\n"
               "def arg_bound(d: dict[str, Int32]) -> Int32:\n"
               "    v = d.get(\"zz\")\n"
               "    return take(v)\n"
               "def main() -> None:\n"
               "    d = {\"a\": 10}\n"
               "    print(ret_bound(d), arg_bound(d))\n"
               "main()\n")
        thir, _f = _lower_ctx_witnessed(src)
        assert _fn(thir, "ret_bound") is None
        assert _fn(thir, "arg_bound") is None
        _assert_byte_identical(src)


class TestListConcatBinop:
    """`list + list` -> `::tpy::list_concat(l, r)` (binop.list_concat) and
    the set-operator siblings: the native free-function dunder render, a
    literal operand taking the typed-brace prefix. Print args wrap the
    operator render in the kind-keyed printer; the RETURN container row
    stays a separate unrouted gate (boundary-pinned below)."""

    def test_reduce_lambda_concat_routes(self):
        src = ("from functools import reduce\n"
               "from tpy import Int32\n"
               "def go() -> None:\n"
               "    init: list[Int32] = [100]\n"
               "    nums: list[Int32] = [1, 2, 3]\n"
               "    built = reduce(lambda acc, x: acc + [x], nums, init)\n"
               "    print(built)\n"
               "go()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "go") is not None
        assert faces.get("binop.list_concat", 0) >= 1
        _assert_byte_identical(src)

    def test_print_arg_concat_routes(self):
        # A container binop at a PRINT arg wraps the operator render in the
        # kind-keyed printer (`ListPrinter((::tpy::list_concat(a, b)))`).
        src = ("from tpy import Int32\n"
               "def f(a: list[Int32], b: list[Int32]) -> None:\n"
               "    print(a + b)\n"
               "def go() -> None:\n"
               "    a: list[Int32] = [1]\n"
               "    b: list[Int32] = [2]\n"
               "    f(a, b)\n"
               "go()\n")
        _assert_routes_byte_identical(src)

    def test_set_operator_prints_route(self):
        src = ("from tpy import Int32\n"
               "def f() -> None:\n"
               "    a: set[Int32] = {1, 2, 3}\n"
               "    b: set[Int32] = {2, 3, 4}\n"
               "    print(a | b)\n"
               "    print(a & b)\n"
               "    print(a - b)\n"
               "    print(a ^ b)\n"
               "    print(a <= b)\n"
               "    print(a < b)\n"
               "f()\n")
        _assert_routes_byte_identical(src)

    def test_set_ctor_call_operand_routes(self):
        # A container-producing free CALL operand (`set(range(1, 4)) | {7}`
        # -- what a `[1..3, 7]` set literal desugars to): the fresh value
        # renders inline inside the dunder template, no temp.
        src = ("from tpy import Int32\n"
               "def f() -> None:\n"
               "    c = set(range(1, 4)) | {7}\n"
               "    d = {7} | set(range(1, 4))\n"
               "    print(len(c), len(d))\n"
               "f()\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert ("::tpy::set_union(::tpy::set_construct<int32_t>("
                "::tpy::Range<int32_t>(1, 4))") in cpp

    def test_borrow_returning_call_operand_still_defers(self):
        # BOUNDARY: the call-operand leg is scoped to RVALUE sources -- a
        # borrow-returning call is an lvalue whose render is the callee's
        # storage, so it must keep rejecting.
        src = ("from tpy import Int32\n"
               "class H:\n"
               "    items: set[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = {1}\n"
               "def borrow_set(h: H) -> set[Int32]:\n"
               "    return h.items\n"
               "def f(h: H) -> None:\n"
               "    c = borrow_set(h) | {7}\n"
               "    print(len(c))\n"
               "def main() -> None:\n"
               "    f(H())\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_method_call_operand_still_defers(self):
        # BOUNDARY: the leg admits FREE calls only -- a method-call operand
        # (its own receiver-form rows) stays out.
        src = ("from tpy import Int32, Own\n"
               "class H:\n"
               "    items: set[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = {1}\n"
               "    def get(self) -> set[Int32]:\n"
               "        return self.items\n"
               "def f(h: H) -> None:\n"
               "    c = h.get() | {7}\n"
               "    print(len(c))\n"
               "def main() -> None:\n"
               "    f(H())\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_list_ordering_compare_still_defers(self):
        # BOUNDARY: the ordering widening in _container_compare_pair is
        # SET-only -- a list ordering compare must keep falling back.
        src = ("from tpy import Int32\n"
               "def f() -> None:\n"
               "    a: list[Int32] = [1, 2]\n"
               "    b: list[Int32] = [1, 3]\n"
               "    print(a < b)\n"
               "f()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_return_container_concat_still_defers(self):
        # BOUNDARY: the RETURN container row is a separate unrouted gate
        # (return.container_source) -- the concat leg must not open it.
        src = ("from tpy import Int32, Own\n"
               "def concat(a: list[Int32], b: list[Int32])"
               " -> Own[list[Int32]]:\n"
               "    return a + b\n"
               "def go() -> None:\n"
               "    a: list[Int32] = [1]\n"
               "    b: list[Int32] = [2]\n"
               "    print(concat(a, b))\n"
               "go()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "concat") is None
        _assert_byte_identical(src)


class TestCallableElementLiteral:
    """A `list[Callable[...]]` literal: callable-returning call rvalues,
    routable lambdas / func refs, and callable-value NAMES render bare in
    the brace init (`{make_adder(1), make_negator()}`)."""

    def test_callable_call_rvalue_elements_route(self):
        src = ("from typing import Callable\n"
               "from tpy import Int32\n"
               "def make_adder(n: Int32) -> Callable[[Int32], Int32]:\n"
               "    return lambda x: x + n\n"
               "def f() -> None:\n"
               "    fns: list[Callable[[Int32], Int32]] = ["
               "make_adder(1), make_adder(2)]\n"
               "    print(len(fns))\n"
               "f()\n")
        _assert_routes_byte_identical(src)

    def test_record_element_source_still_gates(self):
        # A RECORD-ctor element at a record-element slot keeps its own
        # family's admission (the callable arm must not leak): boundary
        # via the record family's existing behavior -- byte-identical
        # either way.
        src = ("from tpy import Int32\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32):\n        self.x = x\n"
               "def f() -> None:\n"
               "    ps: list[P] = [P(1), P(2)]\n"
               "    print(len(ps))\n"
               "f()\n")
        _assert_byte_identical(src)


class TestSetAugNameValue:
    """`e |= b` with a SAME-typed set NAME value binds the in-place dunder
    bare (`::tpy::set_update(e, b)`); a bytearray `+=` target keeps its
    deliberate exclusion (in-place vs rebind is a reference-type aliasing
    axis)."""

    def test_set_name_value_routes(self):
        src = ("from tpy import Int32\n"
               "def f() -> None:\n"
               "    e: set[Int32] = {1, 2}\n"
               "    b: set[Int32] = {2, 3}\n"
               "    e |= b\n"
               "    print(len(e))\n"
               "f()\n")
        _assert_routes_byte_identical(src)
        thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("aug.inplace_dunder", 0) >= 1

    def test_bytearray_aug_routes(self):
        # Was a fence while only owned-bytes targets had the concat row;
        # the bytearray-local widening routes it through the same
        # concat-and-assign render.
        src = ("def f() -> None:\n"
               "    got = bytearray()\n"
               "    chunk = b\"xy\"\n"
               "    got += chunk\n"
               "    print(len(got))\n"
               "f()\n")
        _assert_routes_byte_identical(src)


class TestNarrowedOptDictWrite:
    """A None-narrowed ptr-repr Optional[container] NAME receiver WRITES
    through the deref (`__setitem__((*d), k, v)` -- the setitem gate
    proves it, the target lowers prechecked) and READS through the same
    deref (`::tpy::__getitem__((*lst), 0)`). An UNPROVEN receiver keeps
    its deref_check fence."""

    def test_narrowed_opt_dict_write_routes(self):
        src = ("def scan(d: dict[str, str] | None) -> int:\n"
               "    n = 0\n"
               "    if d is not None:\n"
               "        d[\"k\"] = \"v\"\n"
               "        n += len(d)\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(scan({\"a\": \"b\"}))\n"
               "    print(scan(None))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_narrowed_opt_list_read_routes(self):
        src = ("from tpy import Int32\n"
               "def f(lst: list[Int32] | None) -> None:\n"
               "    if lst is None:\n"
               "        return\n"
               "    lst[0] = 5\n"
               "    print(lst[-1])\n"
               "def main() -> None:\n"
               "    f([1])\n"
               "    f(None)\n"
               "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        # Twice: the write TARGET's receiver takes the same deref.
        assert faces.get("subscript.narrowed_ptr_opt_recv", 0) == 2
        _hpp, cpp = _assert_routes_byte_identical(src)
        # The checked dunder over the deref -- a raw `(*lst)[-1]` would
        # misindex instead of normalizing.
        assert "::tpy::__getitem__((*lst), -1)" in cpp
        assert "::tpy::__setitem__((*lst), 0, 5);" in cpp

    def test_narrowed_opt_dict_read_routes(self):
        # The dict flavor: the checked dunder raises KeyError where the raw
        # `operator[]` would default-insert.
        src = ("from tpy import Int32\n"
               "def f(d: dict[Int32, Int32] | None) -> None:\n"
               "    if d is None:\n"
               "        return\n"
               "    print(d[1])\n"
               "def main() -> None:\n"
               "    f({1: 2})\n"
               "    f(None)\n"
               "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("subscript.narrowed_ptr_opt_recv", 0) == 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::__getitem__((*d), 1)" in cpp

    def test_unproven_opt_list_read_stays_ast(self):
        # No narrowing: the read carries needs_optional_runtime_check and
        # rejects at subscript.optional_check, keeping the deref_check render.
        src = ("from tpy import Int32\n"
               "def f(lst: list[Int32] | None) -> None:\n"
               "    print(lst[0])\n"
               "def main() -> None:\n"
               "    f([1])\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_narrowed_opt_list_local_routes(self):
        # A slot-hoisted LOCAL is a pointer BINDING just like a param, so the
        # narrowed-inner leg keys on it the same way and the deref render is
        # the same `(*lst)`.
        src = ("from tpy import Int32\n"
               "def f() -> None:\n"
               "    lst: list[Int32] | None = [1, 2, 3]\n"
               "    if lst is None:\n"
               "        return\n"
               "    print(lst[0])\n"
               "def main() -> None:\n"
               "    f()\n"
               "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("subscript.narrowed_ptr_opt_recv", 0) == 1
        assert faces.get("decl.opt_slot_container_literal", 0) == 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::vector<int32_t>* lst = &__slot_1;" in cpp
        assert "::tpy::__getitem__((*lst), 0)" in cpp


class TestElemFieldChainSetitem:
    """A container FIELD off a record-element borrow lvalue as the setitem
    receiver (`root.kids["a"].kids["b"] = v` ->
    `__setitem__(__getitem__(root.kids, "a").kids, "b", v)`). A deeper
    chain (field-of-field off the element) stays AST."""
    _SRC = (
        "from __future__ import annotations\n"
        "from tpy import Int32\n"
        "class Node:\n"
        "    val: Int32\n"
        "    kids: dict[str, Node]\n"
        "    def __init__(self, val: Int32) -> None:\n"
        "        self.val = val\n"
        "        self.kids = {}\n"
        "def graft(root: Node) -> None:\n"
        "    root.kids[\"a\"].kids[\"b\"] = Node(99)\n"
        "def main() -> None:\n"
        "    r = Node(1)\n"
        "    r.kids[\"a\"] = Node(2)\n"
        "    graft(r)\n"
        "    print(r.kids[\"a\"].kids[\"b\"].val)\n"
        "main()\n")

    def test_elem_field_receiver_routes(self):
        thir = _lower_ctx(self._SRC)
        assert _fn(thir, "graft") is not None
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert ('::tpy::__setitem__(::tpy::__getitem__(root.kids, "a").kids,'
                ' "b", Node(99));') in cpp

    def test_field_of_field_chain_stays_ast(self):
        src = (
            "from __future__ import annotations\n"
            "from tpy import Int32\n"
            "class Inner:\n"
            "    slots: dict[str, Int32]\n"
            "    def __init__(self) -> None:\n        self.slots = {}\n"
            "class Node:\n"
            "    inner: Inner\n"
            "    def __init__(self) -> None:\n        self.inner = Inner()\n"
            "def put(nodes: list[Node]) -> None:\n"
            "    nodes[0].inner.slots[\"k\"] = 5\n"
            "def main() -> None:\n"
            "    ns = [Node()]\n"
            "    put(ns)\n"
            "    print(ns[0].inner.slots[\"k\"])\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "put") is None
        _assert_byte_identical(src)


class TestContainerBorrowCallDecl:
    """A BORROW container return binds a `T&` alias at its decl
    (`std::vector<int32_t>& items = identity<...>(t);` -- the record
    borrow-call row's container twin); mutation through the alias
    reaches the source, and a reassigned alias keeps the pre-existing
    reassigned fence."""

    def test_alias_decl_routes_and_aliases(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = (
            "from typing import Sized\n"
            "from tpy import Int32\n"
            "def identity[T: Sized](x: T) -> T:\n"
            "    return x\n"
            "def mutate_through() -> None:\n"
            "    xs: list[Int32] = [1, 2]\n"
            "    ys = identity(xs)\n"
            "    ys.append(9)\n"
            "    print(len(xs))\n"
            "def main() -> None:\n"
            "    mutate_through()\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("decl.container_borrow_call", 0) >= 1
        assert "std::vector<int32_t>& ys = identity<std::vector<int32_t>>(xs);" in cpp

    def test_reassigned_alias_defers(self):
        from .testutil import _assert_byte_identical, _fn, _lower_ctx
        src = (
            "from typing import Sized\n"
            "from tpy import Int32\n"
            "def identity[T: Sized](x: T) -> T:\n"
            "    return x\n"
            "def reassigned() -> None:\n"
            "    xs: list[Int32] = [1]\n"
            "    zs = identity(xs)\n"
            "    zs = identity(xs)\n"
            "    print(len(zs))\n"
            "reassigned()\n")
        _assert_byte_identical(src)
        assert _fn(_lower_ctx(src), "reassigned") is None

    def test_readonly_callee_defers(self):
        # BOUNDARY: a readonly-wrapped borrow return keeps the reject --
        # the const spelling (`const T&`) is unverified at this arm.
        from .testutil import _assert_byte_identical, _fn, _lower_ctx
        src = (
            "from tpy import Int32, readonly\n"
            "def pick_ro(a: readonly[list[Int32]])"
            " -> readonly[list[Int32]]:\n"
            "    return a\n"
            "def f(a: readonly[list[Int32]]) -> Int32:\n"
            "    xs = pick_ro(a)\n"
            "    return len(xs)\n"
            "f([1])\n")
        _assert_byte_identical(src)
        assert _fn(_lower_ctx(src), "f") is None


class TestReturnListRepeat:
    """`return [label] * 3` at an Own[list[str]] storage return: the repeat
    build renders target-typed by the slot (`::tpy::from_range<...>(
    ::tpy::repeat_range<...>(3, {std::string(label)}))`) -- untargeted
    resolve would demote to the Array flavor."""

    def test_return_repeat_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = (
            "from tpy import Own\n"
            "def in_list_repeat() -> Own[list[str]]:\n"
            "    label: str = \"no\"\n"
            "    return [label] * 3\n"
            "def main() -> None:\n"
            "    print(in_list_repeat())\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("ret.container_repeat", 0) >= 1
        assert ("return ::tpy::from_range<std::vector<std::string>>"
                "(::tpy::repeat_range<std::string>(3, {std::string(label)}))"
                in cpp)


class TestViewKeyFamily:
    """View-keyed sets/dicts (`set[StrView]`, `dict[BytesView, T]`): key
    positions thread the view target -- str literals land bare, bytes
    literals take the static view spelling; owned-bytes keys keep the
    owned spelling. Own[view] insert slots admit LITERALS only."""

    def test_strview_set_and_dict_route(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = (
            "from tpy import StrView, Int32\n"
            "def main() -> None:\n"
            "    s: set[StrView] = set()\n"
            "    s.add(\"hello\")\n"
            "    print(\"hello\" in s)\n"
            "    d: dict[StrView, Int32] = {}\n"
            "    d[\"k\"] = 1\n"
            "    print(d[\"k\"])\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("binop.contains_view_key", 0) >= 1
        assert 's.insert("hello");' in cpp
        assert '(s.contains("hello"))' in cpp

    def test_bytesview_static_spelling_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = (
            "from tpy import BytesView, Int32\n"
            "def main() -> None:\n"
            "    s: set[BytesView] = set()\n"
            "    s.add(b\"hi\")\n"
            "    print(b\"hi\" in s)\n"
            "    d: dict[BytesView, Int32] = {}\n"
            "    d[b\"k\"] = 1\n"
            "    print(d[b\"k\"])\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("arg.bytes_view_literal", 0) >= 1
        assert 's.insert(::tpy::bytes_literal("hi", 2));' in cpp
        assert '::tpy::__setitem__(d, ::tpy::bytes_literal("k", 1)' in cpp

    def test_owned_bytes_key_keeps_owned_spelling(self):
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import Int32\n"
            "def main() -> None:\n"
            "    d: dict[bytes, Int32] = {}\n"
            "    d[b\"k\"] = 1\n"
            "    print(d[b\"k\"])\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "bytes_literal_owned(\"k\", 1)" in cpp

    def test_view_insert_name_arg_still_defers(self):
        # A NAME arg at the Own[view] insert slot takes the AST's copy+move
        # view temp (unmirrored) -- the body keeps the AST path.
        from .testutil import _fn as _fn_l, _lower_ctx
        src = (
            "from tpy import StrView\n"
            "def f() -> None:\n"
            "    s: set[StrView] = set()\n"
            "    name = \"dyn\"\n"
            "    s.add(name)\n"
            "    print(len(s))\n")
        thir = _lower_ctx(src)
        assert _fn_l(thir, "f") is None

    def test_name_keyed_bytes_dict_reads_route(self):
        # NAME-keyed subscript reads over owned-bytes and BytesView-keyed
        # dicts: the retag is a no-op for non-literal keys and the name
        # renders bare on both paths.
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import Int32, BytesView\n"
            "def rb(d: dict[bytes, Int32], k: bytes) -> Int32:\n"
            "    return d[k]\n"
            "def rv(d: dict[BytesView, Int32], k: BytesView) -> Int32:\n"
            "    return d[k]\n"
            "def main() -> None:\n"
            "    d: dict[bytes, Int32] = {}\n"
            "    d[b\"a\"] = 1\n"
            "    print(rb(d, b\"a\"))\n"
            "    v: dict[BytesView, Int32] = {}\n"
            "    v[b\"x\"] = 2\n"
            "    print(rv(v, b\"x\"))\n"
            "main()\n")
        _assert_routes_byte_identical(src)

    def test_bytesview_insert_name_arg_still_defers(self):
        # The BytesView twin of the Own[view] name-arg boundary.
        from .testutil import _fn as _fn_l, _lower_ctx
        src = (
            "from tpy import BytesView\n"
            "def f(k: BytesView) -> None:\n"
            "    s: set[BytesView] = set()\n"
            "    s.add(k)\n"
            "    print(len(s))\n")
        thir = _lower_ctx(src)
        assert _fn_l(thir, "f") is None


class TestWrapperMemberContainerNameElem:
    """A member-container-typed NAME element at a recursive-union WRAPPER
    slot (`[1, inner]` at `list[Tree]`, inner: `list[Tree]`): the converting
    ctor absorbs it; a movable last use forces the make_vector switch
    (`::tpy::make_vector<Tree>(1, std::move(inner))`), a non-last-use copy
    keeps the plain brace."""

    _TREE = (
        "type Tree = int | list[Tree]\n"
        "def depth(t: Tree) -> int:\n"
        "    if isinstance(t, int):\n        return 0\n"
        "    return 1\n"
    )

    def test_moved_name_takes_make_vector(self):
        src = self._TREE + (
            "def f() -> None:\n"
            "    inner: list[Tree] = [3, 4]\n"
            "    nested: list[Tree] = [1, inner]\n"
            "    print(len(nested))\n")
        thir, faces = _lower_ctx_witnessed(src)
        f = _fn(thir, "f")
        assert f is not None
        assert faces.get("containerlit.make", 0) >= 1
        _assert_routes_byte_identical(src)

    def test_copied_name_keeps_brace(self):
        # Non-last-use: the shared move facts decline, no make switch --
        # the brace init copies the name.
        src = self._TREE + (
            "def f() -> None:\n"
            "    inner: list[Tree] = [3, 4]\n"
            "    nested: list[Tree] = [1, inner]\n"
            "    print(len(nested))\n"
            "    print(len(inner))\n")
        _assert_routes_byte_identical(src)

    def test_dict_value_flavor_routes(self):
        # The dict-VALUE sibling ({"b": inner} at dict[str, Tree]) rides the
        # same leg through the dict elem gate. (A NON-member container name
        # at the slot is a sema type error, so no reject boundary exists on
        # that axis; the leg's element-equality is the structural guard.)
        src = self._TREE + (
            "def f() -> None:\n"
            "    inner: list[Tree] = [5]\n"
            '    d: dict[str, Tree] = {"a": 1, "b": inner}\n'
            "    print(len(d))\n")
        _assert_routes_byte_identical(src)


class TestTparamDictKey:
    """The open-T dict/set key slice (`dict[T, int]` under `[T: Hashable]`):
    every consumer render is key-type-neutral, so the shared key predicate
    admits the TypeParamRef and the generic bodies route verbatim like
    their concrete twins. A dict LITERAL with a T-typed key EXPRESSION is
    not constructible (sema rejects `{x: 1}` even under the Hashable
    bound), so only the empty-literal MIL shape is pinned. The driver
    passes a NAME iterable: a literal at the generic structural slot has
    a pre-existing THIR argtemp-vs-bare divergence outside this cell."""

    _BAG = (
        "from tpy import Hashable\n"
        "from typing import Iterable\n"
        "class Bag[T: Hashable]:\n"
        "    _data: dict[T, int]\n"
        "    def __init__(self) -> None:\n"
        "        self._data = {}\n"
        "    def add_all(self, items: Iterable[T]) -> None:\n"
        "        for x in items:\n"
        "            self._data[x] = self._data.get(x, 0) + 1\n"
        "    def __getitem__(self, key: T) -> int:\n"
        "        return self._data.get(key, 0)\n"
        "    def __contains__(self, key: T) -> bool:\n"
        "        return key in self._data\n"
        "    def total(self) -> int:\n"
        "        s = 0\n"
        "        for k in self._data:\n"
        "            s = s + self._data[k]\n"
        "        return s\n"
        "def main() -> None:\n"
        "    b = Bag[str]()\n"
        "    xs = [\"a\", \"b\", \"a\"]\n"
        "    b.add_all(xs)\n"
        "    print(b[\"a\"], \"a\" in b, b.total())\n"
        "main()\n"
    )

    def test_tparam_key_bodies_route(self):
        # setitem + .get receiver + subscript read + membership needle +
        # the ctor's empty dict[T, int] MIL literal, all through the
        # widened shared key slice.
        thir, w = _lower_ctx_witnessed(self._BAG)
        for name in ("add_all", "__getitem__", "__contains__", "total"):
            assert _fn(thir, name) is not None, name
        assert w.get("binop.contains_tparam_needle", 0) >= 1
        _assert_routes_byte_identical(self._BAG)

    def test_tparam_set_membership_still_defers(self):
        # BOUNDARY: a set[T] receiver's membership takes the
        # resolved-__contains__ lane whose needle rows are not widened --
        # the body keeps the AST path.
        src = (
            "from tpy import Hashable\n"
            "class Seen[T: Hashable]:\n"
            "    _seen: set[T]\n"
            "    def __init__(self) -> None:\n"
            "        self._seen = set()\n"
            "    def add(self, x: T) -> None:\n"
            "        self._seen.add(x)\n"
            "    def has(self, x: T) -> bool:\n"
            "        return x in self._seen\n"
            "def main() -> None:\n"
            "    s = Seen[str]()\n"
            "    s.add(\"a\")\n"
            "    print(s.has(\"a\"))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "has") is None
        _assert_byte_identical(src)


class TestNestedArrayCtorAndChainedSubscript:
    """Array[Array[T,N],M] ctor rvalue at a Span slot + the s[0][0]
    chained subscript over a Span-of-Array receiver."""

    _SRC = ("from tpy import Int32, Span, Array\n"
            "def take(s: Span[Array[Int32, 2]]) -> Int32:\n"
            "    return s[0][0] + s[1][1]\n"
            "def main() -> None:\n"
            "    r: Int32 = take(Array[Array[Int32, 2], 2]([[1, 2],"
            " [3, 4]]))\n"
            "    print(r)\n"
            "main()\n")

    def test_nested_ctor_and_chain_route(self):
        thir = _lower_ctx(self._SRC)
        assert _fn(thir, "take") is not None
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(self._SRC)
        assert ("take(::tpy::as_mut_span(std::array<std::array<int32_t, 2>,"
                " 2>({{{1, 2}, {3, 4}}})))" in cpp[1])
        assert ("::tpy::__getitem__(::tpy::__getitem__(s, 0), 0)"
                in cpp[1])

    def test_flat_array_ctor_at_span_slot_routes(self):
        src = ("from tpy import Int32, Span, Array\n"
               "def take(s: Span[Int32]) -> Int32:\n"
               "    return s[0] + s[1]\n"
               "def main() -> None:\n"
               "    print(take(Array[Int32, 2]([1, 2])))\n"
               "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert w.get("call.container_ctor_value", 0) >= 1
        _assert_byte_identical(src)

    def test_span_container_elem_ref_alias_routes(self):
        # The span_elem_ok widening: a Span-of-container element binds the
        # REF_ALIAS local like a list-of-container element.
        src = ("from tpy import Int32, Span, Array\n"
               "def f(s: Span[Array[Int32, 2]]) -> Int32:\n"
               "    row = s[0]\n"
               "    return row[1]\n"
               "def main() -> None:\n"
               "    a: Array[Array[Int32, 2], 2] = [[1, 2], [3, 4]]\n"
               "    print(f(a))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert "std::array<int32_t, 2>& row = " in cpp[1]


class TestNarrowedOptRecordGetitem:
    """A None-narrowed ptr-repr `Optional[record]` NAME receiver spells the
    record's bare `operator[]` over the deref (`(*g)[i]`) -- the container
    arm's `_optrecv_deref` row one sink over. The row keys on the pointer
    BINDING set, which is what the deref render itself keys on, so a LOCAL
    whose ptr-slot decl put it in that set gets the same deref; an UNPROVEN
    receiver keeps its runtime check."""

    _SRC = (
        "from tpy import Int32\n"
        "class Grid:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 3\n"
        "    def __len__(self) -> Int32:\n"
        "        return self.n\n"
        "    def __getitem__(self, i: Int32) -> Int32:\n"
        "        return i * 2\n"
        "def read(g: Grid | None) -> None:\n"
        "    if g is None:\n"
        "        return\n"
        "    for i in range(len(g)):\n"
        "        print(g[i])\n"
        "def main() -> None:\n"
        "    read(Grid())\n"
        "    read(None)\n"
        "main()\n")

    def test_narrowed_opt_record_getitem_routes(self):
        _thir, faces = _lower_ctx_witnessed(self._SRC)
        assert faces.get("subscript.record_getitem", 0) >= 1
        assert faces.get("subscript.narrowed_ptr_opt_recv", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "(*g)[i]" in cpp

    def test_narrowed_opt_record_str_key_routes(self):
        src = (
            "class Table:\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def __getitem__(self, k: str) -> int:\n"
            "        return len(k)\n"
            "def read(t: Table | None) -> None:\n"
            "    if t is None:\n"
            "        return\n"
            "    print(t[\"abc\"])\n"
            "def main() -> None:\n"
            "    read(Table())\n"
            "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("subscript.narrowed_ptr_opt_recv", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert '(*t)["abc"]' in cpp

    def test_narrowed_opt_record_local_routes(self):
        # A ptr-SLOT local lands in the pointer binding set (`Grid* g =
        # &__slot_1;`), so it takes the same deref as a param -- unlike the
        # container sibling, whose local rejects one arm earlier at its decl.
        src = (
            "from tpy import Int32\n"
            "class Grid:\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def __getitem__(self, i: Int32) -> Int32:\n"
            "        return i * 2\n"
            "def read() -> None:\n"
            "    g: Grid | None = Grid()\n"
            "    if g is None:\n"
            "        return\n"
            "    print(g[0])\n"
            "def main() -> None:\n"
            "    read()\n"
            "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("subscript.narrowed_ptr_opt_recv", 0) >= 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "(*g)[0]" in cpp

    def test_unnarrowed_opt_record_getitem_stays_ast(self):
        # No narrowing: the read carries needs_optional_runtime_check, which
        # the record-getitem gate rejects outright (the AST keeps deref_check).
        src = (
            "from tpy import Int32\n"
            "class Grid:\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def __getitem__(self, i: Int32) -> Int32:\n"
            "        return i * 2\n"
            "def read(g: Grid | None) -> None:\n"
            "    print(g[0])\n"
            "def main() -> None:\n"
            "    read(Grid())\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "read") is None
        _assert_byte_identical(src)


class TestNarrowedOptNestedSubscript:
    """`rows[i][j]` off a None-narrowed ptr-repr `Optional[container]` NAME:
    the nested checked dunder over the `(*rows)` deref. The nested WRITE
    target keeps its own receiver reject."""

    _SRC = (
        "from tpy import Int32\n"
        "def read(rows: list[list[Int32]] | None) -> None:\n"
        "    if rows is None:\n"
        "        return\n"
        "    print(rows[-1][-1])\n"
        "def main() -> None:\n"
        "    read([[1, 2], [3, 4]])\n"
        "    read(None)\n"
        "main()\n")

    def test_narrowed_opt_nested_read_routes(self):
        _thir, faces = _lower_ctx_witnessed(self._SRC)
        assert faces.get("subscript.narrowed_opt_nested", 0) == 1
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert ("::tpy::__getitem__(::tpy::__getitem__((*rows), -1), -1)"
                in cpp)

    def test_narrowed_opt_nested_dict_of_dict_routes(self):
        src = (
            "from tpy import Int32\n"
            "def read(m: dict[str, dict[str, Int32]] | None) -> None:\n"
            "    if m is None:\n"
            "        return\n"
            "    print(m[\"a\"][\"b\"])\n"
            "def main() -> None:\n"
            "    read({\"a\": {\"b\": 5}})\n"
            "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("subscript.narrowed_opt_nested", 0) == 1
        _assert_routes_byte_identical(src)

    def test_narrowed_opt_nested_write_target_stays_ast(self):
        # The setitem gate has no narrowed-nested row: the write target's
        # subscript receiver still rejects (`setitem.recv.subscript`).
        src = (
            "from tpy import Int32\n"
            "def write(rows: list[list[Int32]] | None) -> None:\n"
            "    if rows is None:\n"
            "        return\n"
            "    rows[0][1] = 9\n"
            "def main() -> None:\n"
            "    write([[1, 2]])\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "write") is None
        _assert_byte_identical(src)


class TestBytearraySubscriptRead:
    """A `bytearray` subscript READ spells the same @native free-function
    dunder as bytes (`::tpy::bytes_getitem(b, i)`); only the WRITE side has
    its own `bytearray_*` natives. Name and clean-field receivers, plus the
    None-narrowed pointer-repr deref; an `Own[bytearray]` param keeps the
    bare-name reject its `len` sibling pins."""

    _SRC = (
        "from tpy import Int32\n"
        "class Holder:\n"
        "    data: bytearray\n"
        "    def __init__(self, d: bytearray) -> None:\n"
        "        self.data = d\n"
        "def plain(b: bytearray) -> None:\n"
        "    print(b[-1])\n"
        "def narrowed(b: bytearray | None) -> None:\n"
        "    if b is None:\n"
        "        return\n"
        "    print(b[0])\n"
        "def field(h: Holder) -> None:\n"
        "    print(h.data[0])\n"
        "def main() -> None:\n"
        "    ba = bytearray(b\"abc\")\n"
        "    plain(ba)\n"
        "    narrowed(ba)\n"
        "    field(Holder(bytearray(b\"pqr\")))\n"
        "main()\n")

    def test_bytearray_subscript_read_routes(self):
        _thir, faces = _lower_ctx_witnessed(self._SRC)
        assert faces.get("subscript.bytearray_recv", 0) == 3
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        assert "::tpy::bytes_getitem(b, -1)" in cpp
        assert "::tpy::bytes_getitem((*b), 0)" in cpp
        assert "::tpy::bytes_getitem(h.data, 0)" in cpp

    def test_own_bytearray_param_subscript_stays_ast(self):
        # BOUNDARY: the Own param slot's bare name read is position-pinned
        # elsewhere; the bytearray row declines it rather than widening a
        # second axis at once.
        src = ("from tpy import Own\n"
               "def take(b: Own[bytearray]) -> None:\n"
               "    print(b[0])\n"
               "def main() -> None:\n"
               "    take(bytearray(b\"ab\"))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "take") is None
        _assert_byte_identical(src)

    def test_list_subscript_keeps_checked_template(self):
        # The bytearray dispatch must not leak into the container families.
        src = ("from tpy import Int32\n"
               "def f(xs: list[Int32]) -> None:\n"
               "    print(xs[-1])\n"
               "def main() -> None:\n"
               "    f([1, 2])\n"
               "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::__getitem__(xs, -1)" in cpp
        assert "bytes_getitem" not in cpp


class TestContainerCallArgTemp:
    """A container-returning rvalue CALL hoists the `__tmp_N` ref-param temp:
    for a plain container slot (the `argtemp.container_call` row, now
    including bytearray) and for a pointer-repr `Optional[container]` slot
    (the literal face's call sibling -- `&(__tmp_N)`)."""

    def test_bytearray_call_rvalue_plain_slot_routes(self):
        src = ("def take(b: bytearray) -> None:\n"
               "    print(len(b))\n"
               "def main() -> None:\n"
               "    take(bytearray(b\"abc\"))\n"
               "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("argtemp.container_call", 0) == 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::vector<uint8_t> __tmp_1 = ::tpy::bytes_copy(" in cpp
        assert "take(__tmp_1);" in cpp

    def test_container_call_rvalue_optional_slot_routes(self):
        src = ("from tpy import Int32, Own\n"
               "def mk() -> Own[list[Int32]]:\n"
               "    return [1, 2, 3]\n"
               "def take(xs: list[Int32] | None) -> None:\n"
               "    if xs is None:\n"
               "        return\n"
               "    print(len(xs))\n"
               "def main() -> None:\n"
               "    take(mk())\n"
               "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("optptr.container_call_temp", 0) == 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::vector<int32_t> __tmp_1 = mk();" in cpp
        assert "take(&(__tmp_1));" in cpp

    def test_bytearray_call_rvalue_optional_slot_routes(self):
        src = ("def take(b: bytearray | None) -> None:\n"
               "    if b is None:\n"
               "        return\n"
               "    print(len(b))\n"
               "def main() -> None:\n"
               "    take(bytearray(b\"abc\"))\n"
               "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("optptr.container_call_temp", 0) == 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::vector<uint8_t> __tmp_1 = ::tpy::bytes_copy(" in cpp
        assert "take(&(__tmp_1));" in cpp

    def test_name_arg_at_optional_slot_takes_no_temp(self):
        # BOUNDARY: an LVALUE name at the same slot is the temp-free 'name'
        # face -- the call row must not claim it.
        src = ("from tpy import Int32\n"
               "def take(xs: list[Int32] | None) -> None:\n"
               "    if xs is None:\n"
               "        return\n"
               "    print(len(xs))\n"
               "def main() -> None:\n"
               "    ys: list[Int32] = [1]\n"
               "    take(ys)\n"
               "main()\n")
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("optptr.container_call_temp", 0) == 0
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "take(&(ys));" in cpp
