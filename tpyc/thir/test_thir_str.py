"""THIR F6 str family: str/StrView values, f-strings, concat/append,
subscript/slice/iteration, cross-type coercions, dict[str]/container-of-str."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _emit_expr
from .nodes import (
    Form, PrintForm, THIRAssign, THIRBinOp, THIRCall, THIRCharLiteral,
    THIRCoerce, THIRContainerLiteral, THIRForEach, THIRFormConvert,
    THIRFString, THIRFStringArg, THIRMethodCall, THIRName, THIRSetItem,
    THIRStrAppend, THIRStrLiteral, THIRStrMembership, THIRStrSlice,
    THIRSubscript, THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _lower_ctx_witnessed, _fn, _PRELUDE,
    _assert_byte_identical,
)

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

    # `return self.s` at a StrView slot: the str_to_strview coerce renders its
    # inner bare, so the member read is the emitted form (`return this->s;`).
    _VIEW_FIELD = (
        "from tpy import StrView\n"
        "class Box:\n"
        "    s: str\n"
        "    v: StrView\n"
        "    opt: str | None\n"
        "    def __init__(self, s: str, v: StrView) -> None:\n"
        "        self.s = s\n        self.v = v\n        self.opt = None\n")

    def test_view_coerce_str_field_inner_routes(self):
        # Only the OWNED `str` member needs the coerce arm: a StrView member
        # at a StrView slot needs no conversion, so sema wraps no TpyCoerce
        # and it rides the sync return's own str-field arm (ret.str_field).
        thir, faces = _lower_ctx_witnessed(
            self._VIEW_FIELD
            + "    def own(self) -> StrView:\n        return self.s\n"
            + "    def view(self) -> StrView:\n        return self.v\n")
        assert faces.get("coerce.str_field_view", 0) == 1
        assert faces.get("ret.str_field", 0) == 1

    def test_view_coerce_str_field_byte_identical(self):
        # Both receiver shapes (self and a record param) and both field
        # families (owned `str` member, `StrView` member).
        _assert_byte_identical(
            self._VIEW_FIELD
            + "    def own(self) -> StrView:\n        return self.s\n"
            + "    def view(self) -> StrView:\n        return self.v\n"
            + "def param_recv(b: Box) -> StrView:\n    return b.s\n"
            + "def main() -> None:\n"
            + '    b = Box("hello", "vv")\n'
            + "    print(b.own())\n    print(b.view())\n"
            + "    print(param_recv(b))\n"
            + "main()\n")

    def test_narrowed_optional_str_field_stays_ast(self):
        # BOUNDARY: a NARROWED `str | None` field renders `(*this->opt)`, but
        # the AST emits the bare member read there -- uncompilable (BUGS.md).
        # The admission types on the DECLARED field type so the shape stays
        # unrouted instead of diverging from (and silently fixing) the oracle.
        thir = _lower_ctx(
            self._VIEW_FIELD
            + "    def opt_view(self) -> StrView:\n"
            + "        if self.opt is not None:\n            return self.opt\n"
            + '        return "empty"\n')
        assert _fn(thir, "opt_view") is None

    def test_string_param_print_routes(self):
        thir = _lower(
            "from tpy import String\n"
            "def f(s: String) -> None:\n    print(s)\n")
        assert _fn(thir, "f") is not None

    def test_fstring_routes(self):
        # F6 S2: an f-string is an owned-str expr (STORAGE) -- see TestFString.
        thir = _lower('def f(a: str) -> None:\n    print(f"v={a}")\n')
        assert _fn(thir, "f") is not None


class TestStrReceiverMethods:
    """A str/StrView value-view receiver's builtin @cpp_template /
    @native(function=True) methods route through the general THIRMethodCall
    arm (validation widened at `_view_method_call_supported`)."""

    def test_str_literal_receiver_membership_wrap(self):
        # `"ell" in "hello"` -- a str-LITERAL receiver takes the
        # wrap_receiver_sv=True branch (the literal is wrapped in a
        # string_view for the .find() arm); a NAME receiver does not. Both
        # route byte-identically.
        lit = 'def f() -> bool:\n    return "ell" in "hello world"\n'
        ret = _fn(_lower(lit), "f").body[0]
        assert isinstance(ret.value, THIRStrMembership)
        assert ret.value.wrap_receiver_sv is True
        _assert_byte_identical(lit)
        name = 'def f(s: str) -> bool:\n    return "x" in s\n'
        ret2 = _fn(_lower(name), "f").body[0]
        assert isinstance(ret2.value, THIRStrMembership)
        assert ret2.value.wrap_receiver_sv is False
        _assert_byte_identical(name)

    def test_startswith_cpp_template(self):
        thir = _lower('def f(s: str) -> bool:\n    return s.startswith("hi")\n')
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret.value, THIRMethodCall)
        assert ret.value.cpp_template == "::tpy::str_startswith({self}, {0})"
        assert _emit_expr(ret.value) == '::tpy::str_startswith(s, "hi")'

    def test_find_scalar_result(self):
        thir = _lower("from tpy import Int32\n"
                      "def f(s: str) -> Int32:\n    return s.find(\"x\")\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret.value, THIRMethodCall)
        assert _emit_expr(ret.value) == '::tpy::str_find(s, "x")'

    def test_rfind_scalar_result(self):
        thir = _lower("from tpy import Int32\n"
                      "def f(s: str) -> Int32:\n    return s.rfind(\"x\")\n")
        assert _emit_expr(_fn(thir, "f").body[0].value) == '::tpy::str_rfind(s, "x")'

    def test_encode_native_function_prepends_receiver(self):
        # @native(function=True): the receiver becomes the first C++ arg.
        thir = _lower("def f(s: str) -> bytes:\n    return s.encode()\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret.value, THIRMethodCall)
        assert ret.value.native_function_name == "tpy::bytes_from_str"
        assert _emit_expr(ret.value) == "::tpy::bytes_from_str(s)"

    def test_owned_str_result_routes(self):
        # An owned-str method result (`s.upper()` -> std::string) is STORAGE.
        thir = _lower("def f(s: str) -> str:\n    return s.upper()\n")
        assert _fn(thir, "f") is not None

    def test_str_arg_receiver(self):
        # Both the receiver and the arg are str params -- each renders bare.
        thir = _lower("def f(s: str, t: str) -> bool:\n    return s.startswith(t)\n")
        assert _emit_expr(_fn(thir, "f").body[0].value) \
            == "::tpy::str_startswith(s, t)"

    def test_str_field_receiver_routes(self):
        # A str FIELD receiver (`self.name.startswith(...)`): the shared
        # receiver helper's view-family widening routes it over the bare
        # member read.
        src = (
            "class Box:\n"
            "    name: str\n"
            "    def __init__(self, n: str):\n        self.name = n\n"
            "    def check(self) -> bool:\n        return self.name.startswith(\"p\")\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "check") is not None
        _assert_byte_identical(src)

    def test_split_list_result_routes_at_decl(self):
        # A container result (`s.split()` -> Own[list[str]]) is admitted at
        # the storage DECL sink (the view arm's storage_ret_ok escape).
        thir = _lower("def f(s: str) -> None:\n    xs = s.split()\n    print(xs)\n")
        assert _fn(thir, "f") is not None

    def test_split_print_arg_routes(self):
        # A container-returning call IS admitted as a print arg now: the
        # kind-keyed printer wraps the inline call
        # (`ListPrinter(::tpy::str_split_whitespace(s))`), lowered under
        # STORAGE use like the decl sink.
        src = "def f(s: str) -> None:\n    print(s.split())\n"
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_str_literal_receiver_routes(self):
        # A str-LITERAL receiver renders bare into the resolved template
        # (`::tpy::str_split("a,b", ",")`).
        thir = _lower(
            "from tpy import Int32\n"
            "def f() -> Int32:\n"
            "    parts = \"a,b\".split(\",\")\n"
            "    return len(parts)\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].init, THIRMethodCall)

    def test_str_literal_receiver_witnesses_face(self):
        _thir, w = _lower_ctx_witnessed(
            "def f() -> bool:\n    return \"hi there\".startswith(\"hi\")\n")
        assert w.get("method.recv.str_literal", 0) > 0


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

    def test_conv_repr_wraps(self):
        # !r overrides every mirrored type row with repr_of (the AST chain's
        # conv row precedes the type rows), including the rows that otherwise
        # carry their own wrap (bool, float, BigInt).
        thir = _lower(
            "from tpy import Int32\n"
            "def f(a: str, n: Int32, b: bool, x: float, m: int) -> str:\n"
            '    return f"{a!r}{n!r}{b!r}{x!r}{m!r}{\'q\'!r}"\n')
        fstr = _fn(thir, "f").body[0].value
        args = [p for p in fstr.parts if isinstance(p, THIRFStringArg)]
        assert [a.wrap for a in args] == ["::tpy::repr_of({0})"] * 6
        assert _emit_expr(fstr) == (
            'std::format("{}{}{}{}{}{}", ::tpy::repr_of(a), ::tpy::repr_of(n), '
            "::tpy::repr_of(b), ::tpy::repr_of(x), ::tpy::repr_of(m), "
            '::tpy::repr_of("q"))')

    def test_conv_str_noop(self):
        # !s on non-user types is a no-op in the AST chain (the __str__ row
        # only fires for user types): each arg keeps its normal type row.
        thir = _lower(
            "from tpy import Int32\n"
            "def f(a: str, n: Int32, b: bool, x: float) -> str:\n"
            '    return f"{a!s}{n!s}{b!s}{x!s}"\n')
        fstr = _fn(thir, "f").body[0].value
        args = [p for p in fstr.parts if isinstance(p, THIRFStringArg)]
        assert [a.wrap for a in args] == [
            None, None, "::tpy::bool_to_str({0})", "::tpy::float_to_str({0})"]

    def test_spec_placeholder(self):
        # A constant format spec splices into the placeholder verbatim; the
        # str/wide-int rows stay bare and std::format applies the spec.
        thir = _lower(
            "from tpy import Int32\n"
            "def f(s: str, n: Int32) -> str:\n"
            '    return f"{s:<10}|{n:>8}"\n')
        fstr = _fn(thir, "f").body[0].value
        args = [p for p in fstr.parts if isinstance(p, THIRFStringArg)]
        assert [a.format_spec for a in args] == ["<10", ">8"]
        assert _emit_expr(fstr) == 'std::format("{:<10}|{:>8}", s, n)'

    def test_spec_flips_bool_and_float_rows(self):
        # With a spec, bool takes static_cast<int> (bool_to_str would defeat
        # numeric specs) and the float rows go bare (std::format handles the
        # spec on double/float directly); the int8 cast applies spec-or-not.
        thir = _lower(
            "from tpy import Int8, Float32\n"
            "def f(b: bool, x: float, y: Float32, m: Int8) -> str:\n"
            '    return f"{b:>4}{x:.2f}{y:.3f}{m:04}"\n')
        fstr = _fn(thir, "f").body[0].value
        assert _emit_expr(fstr) == (
            'std::format("{:>4}{:.2f}{:.3f}{:04}", static_cast<int>(b), '
            "x, y, static_cast<int>(m))")

    def test_conv_and_spec_combined(self):
        # `{s!r:>10}`: repr_of wrap + spec placeholder; the spec then formats
        # the repr'd string.
        thir = _lower('def f(s: str) -> str:\n    return f"{s!r:>10}"\n')
        fstr = _fn(thir, "f").body[0].value
        assert _emit_expr(fstr) == 'std::format("{:>10}", ::tpy::repr_of(s))'

    def test_spec_in_nul_vformat_arm(self):
        # A NUL literal segment routes through vformat; the runtime-length
        # count includes the spliced spec's bytes.
        thir = _lower(
            "from tpy import Int32\n"
            'def f(n: Int32) -> str:\n    return f"a\\x00{n:>3}"\n')
        fstr = _fn(thir, "f").body[0].value
        assert _emit_expr(fstr) == (
            'std::vformat(std::string_view{"a\\000{:>3}", 7}, '
            "std::make_format_args(n))")

    def test_bigint_arg_routes(self):
        # A runtime-BigInt arg takes the `({0}).to_string()` row.
        thir = _lower('def f(n: int) -> str:\n    return f"n={n}"\n')
        assert _fn(thir, "f") is not None

    def test_char_arg_routes(self):
        # Char formats bare (char has no int_traits, so the AST's int8 cast
        # row never fires); spec and !r compose like any mirrored row.
        thir = _lower(
            "from tpy import Char\n"
            "def f(c: Char) -> str:\n"
            '    return f"{c}{c:>3}{c!r}"\n')
        fstr = _fn(thir, "f").body[0].value
        assert _emit_expr(fstr) == (
            'std::format("{}{:>3}{}", c, c, ::tpy::repr_of(c))')

    def test_container_arg_conversion_uses_to_str(self):
        # A conversion does NOT override a container: the AST's
        # _container_to_str arm precedes the conv rows, so !r/!s still render
        # via list_to_str (Python str/repr of a container coincide).
        thir = _lower(
            "from tpy import Int32\n"
            "def f() -> str:\n"
            "    xs: list[Int32] = [1, 2]\n"
            '    return f"{xs!r}"\n')
        fstr = _fn(thir, "f").body[1].value
        args = [p for p in fstr.parts if isinstance(p, THIRFStringArg)]
        assert [a.wrap for a in args] == ["::tpy::list_to_str({0})"]

    def test_container_arg_routes(self):
        # Containers format via _container_to_str (`::tpy::list_to_str(xs)`).
        # (An ANNOTATED local: container params and unannotated container
        # locals in f-strings are pre-existing sema rejections -- Ref[list] /
        # PendingList are not unwrapped by _analyze_fstring; see BUGS.md.)
        thir = _lower(
            "from tpy import Int32\n"
            "def f() -> str:\n"
            "    xs: list[Int32] = [1, 2]\n"
            '    return f"{xs}"\n')
        fstr = _fn(thir, "f").body[1].value
        assert _emit_expr(fstr) == 'std::format("{}", ::tpy::list_to_str(xs))'

    def test_ineligible_inner_expr_rejects(self):
        # The interpolated expr itself must be in the slice (the iterator-
        # combinator machinery -- `list(map(...))` -- is avoid-list
        # territory; a container global seeds as a pointer slot now and
        # routes).
        thir = _lower(
            "from tpy import Int32\n"
            "def f(xs: list[Int32]) -> str:\n"
            '    return f"{list(map(lambda v: v + 1, xs))}"\n')
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

    CONV_SRC = (
        "from tpy import Char, Int8, Int32, Float32\n"
        "def f(s: str, n: Int32, m: Int8, b: bool, x: float, y: Float32,"
        " c: Char, w: int) -> str:\n"
        '    a = f"{s!r} {n!s} {b!r:>6} {w!r} {c!r}"\n'
        '    a = f"{n:>8}|{x:.2f}|{y:.3f}|{b:>4}|{m:04}|{s:<10}|{c:>3}"\n'
        '    return f"{a!s}{s!r:>12}nul\\x00{n:>3}"\n'
        "def main() -> None:\n"
        "    print(f(\"bob\", 3, 2, True, 1.5, Float32(0.5), 'x', 7))\n"
        "main()\n"
    )

    def test_fstring_conv_spec_byte_identical(self):
        thir = _lower(self.CONV_SRC)
        assert _fn(thir, "f") is not None
        assert (self._cpp(self.CONV_SRC, thir=True)
                == self._cpp(self.CONV_SRC, thir=False))

    def test_fstring_faces_witnessed(self):
        # Pin that the conv/spec/char rows reach their faces -- a refactor
        # can silently un-witness a face while routing and the byte-diff
        # both stay green.
        src = (
            "from tpy import Char, Int32\n"
            "def f(s: str, n: Int32, c: Char) -> str:\n"
            '    return f"{s!r}{n!s}{c}{n:>4}"\n')
        thir, wit = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        for face in ("fstr.conv_repr", "fstr.conv_str", "fstr.char_arg",
                     "fstr.spec"):
            assert wit.get(face, 0) > 0, face



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

    def test_str_repeat_routes(self):
        # `s * n` / `n * s` resolve __mul__/__rmul__ (str_repeat) with a str
        # result; the operator arm renders the cpp_template, is_reverse pinning
        # the str into {self} for the reversed form.
        thir = _lower(
            "from tpy import Int32\n"
            "def f(a: str, n: Int32) -> str:\n    return a * n\n")
        ret = _fn(thir, "f").body[0].value
        assert isinstance(ret, THIRBinOp)
        assert _emit_expr(ret) == "(::tpy::str_repeat(a, n))"
        rev = _lower(
            "from tpy import Int32\n"
            "def f(a: str, n: Int32) -> str:\n    return n * a\n")
        assert _emit_expr(_fn(rev, "f").body[0].value) == "(::tpy::str_repeat(a, n))"

    def test_unused_string_param_routes(self):
        thir = _lower(
            "from tpy import String\n"
            "def f(s: String, a: str) -> str:\n    return a\n")
        assert _fn(thir, "f") is not None

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

    def test_slice_object_ctor_local_routes(self):
        # A slice-object LOCAL (`sl = basic_slice(1, 3)`): the ctor is the same
        # pure-@cpp_template expansion as the scalar ctors; a `None` bound in
        # the value-repr `Int32 | None` slot renders `std::nullopt`, an int
        # literal / fixed-int name renders bare.
        thir = _lower_ctx(
            "from tpy import Int32, basic_slice\n"
            "def f(s: str) -> None:\n"
            "    sl = basic_slice(1, 3)\n    print(s[sl])\n"
            "def g(s: str, n: Int32) -> None:\n"
            "    st = slice(None, n, 2)\n    print(s[st])\n")
        f_decl = _fn(thir, "f").body[0]
        assert isinstance(f_decl, THIRVarDecl)
        assert f_decl.resolved_type.to_cpp() == "::tpy::BasicSlice"
        assert isinstance(f_decl.init, THIRCall)
        assert _emit_expr(f_decl.init) == "::tpy::BasicSlice{1, 3}"
        g_decl = _fn(thir, "g").body[0]
        assert g_decl.resolved_type.to_cpp() == "::tpy::Slice"
        assert _emit_expr(g_decl.init) == "::tpy::Slice{std::nullopt, n, 2}"

    def test_slice_object_ctor_global_bound_routes(self):
        # A same-module scalar-global bound seeds read-only and renders bare,
        # like a param bound. (A BigInt bound never arises: sema rejects it
        # at the ctor.)
        thir = _lower(
            "from tpy import Int32, basic_slice\n"
            "G: Int32 = 2\n"
            "def f(s: str) -> None:\n"
            "    sl = basic_slice(G, 3)\n    print(s[sl])\n")
        assert _fn(thir, "f") is not None

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
        # BORROW-form THIRFormConvert). A call-result ITERABLE is an rvalue,
        # captured owning (`auto __obj_N = full(s);`, iterable_lvalue=False);
        # a name/field iterable stays the `auto&` alias.
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
        h_loop = _fn(thir, "h").body[0]
        assert isinstance(h_loop, THIRForEach)
        assert not h_loop.iterable_lvalue
        assert _emit_expr(h_loop.iterable) == "full(s)"

    def test_char_subscript_nonname_receivers_route(self):
        # `s[i]` char reads off the shared receiver set: a str-family field
        # off an F1-record receiver and an eligible str-returning call (both
        # render bare into the checked dunder). A global-name receiver stays
        # out (the standard name reject in `_lower_expr`).
        thir = _lower_ctx(
            "from tpy import Char, Int32\n"
            "class H:\n"
            "    name: str\n"
            "    def __init__(self, name: str):\n        self.name = name\n"
            "def full(s: str) -> str:\n    return s\n"
            "def f(h: H, i: Int32) -> Char:\n    return h.name[i]\n"
            "def g(s: str) -> Char:\n    return full(s)[0]\n")
        f_ret = _fn(thir, "f").body[0].value
        assert isinstance(f_ret, THIRSubscript)
        assert _emit_expr(f_ret) == "::tpy::__getitem__(h.name, i)"
        g_ret = _fn(thir, "g").body[0].value
        assert _emit_expr(g_ret) == "::tpy::__getitem__(full(s), 0)"

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

    def test_str_literal_iteration_routes(self):
        # `for ch in "abc"`: the literal iterable is captured as an rvalue
        # `std::string_view("abc")` (the wrap trims the C literal's NUL),
        # Char elements.
        thir = _lower(
            'def f() -> None:\n    for ch in "abc":\n        print(ch)\n')
        loop = _fn(thir, "f").body[0]
        assert isinstance(loop, THIRForEach)
        assert loop.str_literal_iterable
        assert not loop.iterable_lvalue
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

    STR_LIT_ITER = (
        'def f() -> None:\n    for ch in "abc":\n        print(ch)\n'
        'def g() -> None:\n    for ch in "":\n        print(ch)\n'
        "f()\ng()\n")

    def test_str_literal_iter_byte_identical(self):
        assert (self._cpp(self.STR_LIT_ITER, thir=True)
                == self._cpp(self.STR_LIT_ITER, thir=False))

    def test_str_literal_iter_emit_wrap(self):
        cpp = self._cpp(self.STR_LIT_ITER, thir=True)
        assert 'auto __obj_0 = std::string_view("abc");' in cpp
        assert 'auto __obj_0 = std::string_view("");' in cpp

    # S4 leftovers (increment 45): stepped slices, slice-typed variable
    # indices, Char-targeted literal decls / call args, non-name receivers.
    SRC2 = (
        "from tpy import Char, Int32, basic_slice\n"
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
        "def ctor_locals(s: str, n: Int32) -> None:\n"
        "    sl = basic_slice(1, n)\n"
        "    st = slice(None, None, 2)\n"
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
        "def iter_call(s: str) -> None:\n"
        "    for ch in full(s):\n"
        "        print(ch)\n"
        "def char_at(h: H, s: str, n: Int32) -> None:\n"
        "    print(h.name[n], full(s)[0])\n"
        "def main() -> None:\n"
        '    print(stepped("abcdefgh"))\n'
        '    var_index("hello", basic_slice(1, 3), slice(0, 5, 2))\n'
        '    ctor_locals("greeting", 4)\n'
        "    chars()\n"
        '    non_name(H("greetings"), "abcdef")\n'
        '    iter_call("xyz")\n'
        '    char_at(H("hi"), "jk", 1)\n'
        "main()\n"
    )

    def test_s4_leftovers_routed(self):
        thir = _lower_ctx(self.SRC2)
        for name in ("stepped", "var_index", "ctor_locals", "take", "chars",
                     "non_name", "iter_call", "char_at", "main"):
            assert _fn(thir, name) is not None, name

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
        # slice-object ctor locals: literal / name / None bounds
        assert "::tpy::BasicSlice sl = ::tpy::BasicSlice{1, n};" in cpp
        assert ("::tpy::Slice st = "
                "::tpy::Slice{std::nullopt, std::nullopt, 2};") in cpp
        assert "char c = 'x';" in cpp                           # Char decl
        assert "take('a');" in cpp                              # Char call arg
        assert "::tpy::str_slice(h.name, ::tpy::BasicSlice{1, 3})" in cpp
        assert "::tpy::str_slice(full(s), ::tpy::BasicSlice{0, 2})" in cpp
        assert "auto& __obj_0 = h.name;" in cpp                 # field iterable
        assert "auto __obj_0 = full(s);" in cpp                 # rvalue iterable
        # non-name char-subscript receivers (field / call)
        assert "::tpy::__getitem__(h.name, n)" in cpp
        assert "::tpy::__getitem__(full(s), 0)" in cpp



# --- S4 leftover: slice-object ctor rvalues as call args
# --- (`use(s, basic_slice(1, 3))` -- the bare template expansion into a
# --- by-value slice-object param slot) ---

class TestSliceCtorCallArg:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import Int32, basic_slice\n"
        "def use(s: str, sl: basic_slice) -> None:\n    print(s[sl])\n"
        "def use_full(s: str, st: slice) -> None:\n    print(s[st])\n"
        "def f(s: str, n: Int32) -> None:\n"
        "    use(s, basic_slice(1, 3))\n"
        "    use_full(s, slice(None, n, 2))\n"
        "def main() -> None:\n"
        '    f("greetings", 5)\n'
        "main()\n"
    )

    def test_ctor_rvalue_arg_routes(self):
        thir = _lower_ctx(self.SRC)
        fn = _fn(thir, "f")
        assert fn is not None
        call = fn.body[0].expr
        assert isinstance(call, THIRCall)
        assert _emit_expr(call) == "use(s, ::tpy::BasicSlice{1, 3})"
        assert (_emit_expr(fn.body[1].expr)
                == "use_full(s, ::tpy::Slice{std::nullopt, n, 2})")

    def test_own_slot_ineligible(self):
        # An Own[...] slot rides gen_call_arg's auto-move cascade -> AST
        # (_slice_object_type does not peel Own).
        thir = _lower_ctx(
            "from tpy import Own, basic_slice\n"
            "def use(s: str, sl: Own[basic_slice]) -> None:\n    pass\n"
            "def f(s: str) -> None:\n    use(s, basic_slice(1, 3))\n")
        assert _fn(thir, "f") is None

    def test_union_slot_ineligible(self):
        # An `Int32 | basic_slice` slot lifts the ctor rvalue into the
        # variant (_gen_union_arg) -> AST.
        thir = _lower_ctx(
            "from tpy import Int32, basic_slice\n"
            "def use(items: list[Int32], index: Int32 | basic_slice) -> None:\n"
            "    print(len(items))\n"
            "def f(items: list[Int32]) -> None:\n    use(items, basic_slice(1, 3))\n")
        assert _fn(thir, "f") is None

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)


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

    def test_annotated_view_resolved_local_peels_stale_coerce(self):
        # `label: str = sv` never mutated resolves VIEW: the annotation's
        # strview_to_str coerce is stale (materializing would dangle the view
        # off a dying temporary) -- _peel_stale_view_owned_coerce lowers the
        # bare source, mirroring gen_expr's identity arm. The decl and the
        # view-source reassign both peel; the mutated sibling stays owned and
        # still materializes.
        src = (
            "from tpy import StrView\n"
            "def f(sv: StrView, sv2: StrView) -> None:\n"
            "    label: str = sv\n"
            "    label = sv2\n"
            "    print(label)\n"
            "def g(sv: StrView) -> str:\n"
            "    m: str = sv\n"
            '    m += "!"\n'
            "    return m\n")
        thir = _lower(src)
        fn = _fn(thir, "f")
        assert fn is not None
        assert _emit_expr(fn.body[0].init) == "sv"
        assert _emit_expr(fn.body[1].value) == "sv2"
        decl_g = _fn(thir, "g").body[0]
        assert isinstance(decl_g.init, THIRFormConvert)
        assert _emit_expr(decl_g.init) == "std::string(sv)"

        def cpp_for(thir_on: bool) -> str:
            compiler, modules = _compile(src)
            entry = _entry(modules)
            _, cpp = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir_on))
            return cpp

        cpp = cpp_for(True)
        assert cpp == cpp_for(False)
        assert "std::string_view label = sv;" in cpp

    def test_own_slot_coerce_ineligible(self):
        # An Own[str] ARG slot crosses the gen_call_arg auto-move cascade (its
        # own deferred frontier) -> the coerce is rejected, the caller stays AST.
        thir = _lower(
            "from tpy import Own\n"
            "def take(p: Own[str]) -> None:\n    print(p)\n"
            "def f(s: str) -> None:\n    take(s[1:])\n")
        assert _fn(thir, "f") is None

    def test_coerced_literal_multi_overload_pin_routes(self):
        # A str literal (COERCE-PEELED) into a multi-overload callee's view
        # slot takes gen_call_arg's pin (`std::string_view("lit")`), mirrored
        # per-arg; a non-literal arg renders bare through the same loop.
        src = ("from typing import overload\n"
               "from tpy import Int32, StrView\n"
               "@overload\n"
               "def pick(x: StrView) -> Int32: ...\n"
               "@overload\n"
               "def pick(x: Int32) -> Int32: ...\n"
               "def pick(x: StrView | Int32) -> Int32:\n    return 1\n"
               'def caller() -> Int32:\n    return pick("lit")\n'
               "def caller2(s: str) -> Int32:\n    return pick(s)\n")
        thir = _lower(src)
        assert _fn(thir, "caller") is not None
        assert _fn(thir, "caller2") is not None
        _assert_byte_identical(src)



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
        # Signatures stay AST-emitted; every body routes.
        for name in ("ret_slice", "init_slice", "cmp_concat", "fstr_concat",
                     "calls", "slice_compare", "string_arg_pass", "take_view",
                     "take_string"):
            assert _fn(thir, name) is not None, name
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

    def test_own_str_slot_param_arg_routes(self):
        # xs.append(s) where s is a str PARAM: a view (std::string_view) that
        # resolves `str`, not `StrView` -- the arg gate reads `param_names` to
        # tell it from an owned str local (which stays AST), then materializes an
        # owned copy `std::string(s)` at the Own[str] element slot, like a view
        # local. Node shape checked in test_thir_containers.
        thir = _lower(
            "def f(xs: list[str], s: str) -> None:\n    xs.append(s)\n")
        assert _fn(thir, "f") is not None

    def test_view_and_bytes_container_params_route_without_element_use(self):
        # View-keyed/valued and bytes-keyed container PARAMS are admitted by the
        # compositional gate (their by-ref signatures are AST-emitted and
        # element-neutral). A body that does not TOUCH the divergent element
        # (`return i`) routes; the view-key static-storage pin / bytes element
        # reads stay rejected in their own body-use gates, so a body reading such
        # an element would reject there, not at the param.
        thir = _lower(
            _PRELUDE
            + "from tpy import StrView\n"
            + "def f(d: dict[Int32, StrView], i: Int32) -> Int32:\n    return i\n"
            + "def g(d: dict[bytes, Int32], i: Int32) -> Int32:\n    return i\n"
            + "def h(xs: list[StrView], i: Int32) -> Int32:\n    return i\n")
        for name in ("f", "g", "h"):
            assert _fn(thir, name) is not None, name

    def test_subscript_write_routes(self):
        # d[k] = v routes as a THIRSetItem (`::tpy::__setitem__(d, k, 3);`)
        # -- the str key renders bare, like the read side.
        thir = _lower(
            _PRELUDE
            + "def f(d: dict[str, Int32], k: str) -> None:\n    d[k] = 3\n")
        assert isinstance(_fn(thir, "f").body[0], THIRSetItem)



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


class TestStrFieldBinopOperands:
    """A str-family FIELD read as a binop operand renders the bare member
    read into the concat / compare / membership templates on both paths
    (the field_owned_str_ok plumb) -- `return "[base] " + self.message`."""

    _RECORDS = (
        "from tpy import Int32\n"
        "class E:\n"
        "    message: str\n"
        "    def __init__(self, message: str):\n"
        "        self.message = message\n"
        "    def describe(self) -> str:\n"
        "        return \"[base] \" + self.message\n"
        "    def matches(self, s: str) -> bool:\n"
        "        return s == self.message\n"
    )

    def test_concat_operand_routes(self):
        thir = _lower_ctx(self._RECORDS)
        assert _fn(thir, "describe") is not None

    def test_compare_operand_routes(self):
        thir = _lower_ctx(self._RECORDS)
        assert _fn(thir, "matches") is not None

    def test_emit_byte_identical(self):
        src = (
            self._RECORDS
            + "def main():\n"
            + "    e = E(\"boom\")\n"
            + "    print(e.describe(), e.matches(\"boom\"))\n"
            + "main()\n"
        )
        compiler, modules = _compile(src)
        entry = _entry(modules)

        def cpp(thir: bool):
            hpp, out = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir))
            return hpp + out

        thir_cpp = cpp(True)
        assert thir_cpp == cpp(False)
        assert ('return (::tpy::str_concat("[base] ", this->message));'
                in thir_cpp)
        assert "return (s == this->message);" in thir_cpp
