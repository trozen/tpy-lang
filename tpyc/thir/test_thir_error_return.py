"""THIR @error_return: the function-body side (return-tier raise ->
`make_unexpected`, bare-return `{}`, the trailing void success `return {};`,
the expected pass-through return), the caller side (the statement-level
`__try_tmp_N` bind/discard blocks, the expression-level `__er_N`
statement-expression unwrap in its three dispositions), the return-tier
try goto dispatch (`__except_N` / `__after_try_N`, the `__err_opt_N` as
capture, the bare-raise re-raise), and the gate rejections that keep the
un-mirrored shapes (aliasing borrow results, method-call callees,
owned-str first-decl binds) on the AST path."""

from __future__ import annotations

import pytest

from ..parse.nodes import ParseError
from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    THIRErrorReturnBind,
    THIRErrorReturnDiscard,
    THIRRaise,
    THIRReturn,
    THIRTry,
)
from .testutil import _compile, _entry, _fn, _lower_ctx


def _cpp(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return cpp


def _emit_witnesses(src: str):
    # The er.* faces record at EMIT (the render fired), so the witness
    # capture must run codegen, not just lowering.
    compiler, modules = _compile(src)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return compiler._thir_face_witnesses


_ERR = (
    "from tpy import error_return, ReturnException\n"
    "class Err(Exception, ReturnException):\n"
    "    pass\n"
)


class TestErrorReturnFunctionBody:
    SRC = (
        _ERR
        + "@error_return(Err)\n"
        + "def parse(n: int) -> int:\n"
        + "    if n < 0:\n"
        + "        raise Err\n"
        + "    return n\n"
        + "try:\n"
        + "    print(parse(3))\n"
        + "except Err:\n"
        + "    print(\"err\")\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        fn = _fn(thir, "parse")
        assert fn is not None
        assert fn.error_return_cpp == "Err"
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_raise_lowers_return_tier(self):
        thir = _lower_ctx(self.SRC)
        fn = _fn(thir, "parse")
        r = fn.body[0].then_body[0]
        assert isinstance(r, THIRRaise) and r.return_tier
        assert r.cpp_type == "Err"

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "return ::tpy::make_unexpected(Err{});" in cpp

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("er.raise", 0) > 0


class TestErrorReturnVoidTail:
    # The caller lives in a def: top-level code is not a THIR candidate, so
    # a top-level try would leave er.discard unwitnessed.
    SRC = (
        _ERR
        + "@error_return(Err)\n"
        + "def check(n: int) -> None:\n"
        + "    if n < 0:\n"
        + "        raise Err\n"
        + "def main() -> None:\n"
        + "    try:\n"
        + "        check(1)\n"
        + "    except Err:\n"
        + "        print(\"err\")\n"
        + "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "check") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_void_success_tail(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("check("):]
        assert "return {};" in body

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("er.void_tail", 0) > 0
        assert w.get("er.discard", 0) > 0


class TestErrorReturnBareReturn:
    SRC = (
        _ERR
        + "@error_return(Err)\n"
        + "def check(n: int) -> None:\n"
        + "    if n == 0:\n"
        + "        return\n"
        + "    if n < 0:\n"
        + "        raise Err\n"
        + "try:\n"
        + "    check(0)\n"
        + "except Err:\n"
        + "    print(\"err\")\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "check") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_bare_return_constructs_success(self):
        # `return` inside the branch renders `return {};` (the expected's
        # implicit success), not a bare `return;`.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("check("):cpp.index("__tpy_init")]
        assert body.count("return {};") == 2  # the branch + the void tail

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("er.bare_return", 0) > 0


class TestErrorReturnPropagation:
    SRC = (
        _ERR
        + "@error_return(Err)\n"
        + "def parse(n: int) -> int:\n"
        + "    if n < 0:\n"
        + "        raise Err\n"
        + "    return n\n"
        + "@error_return(Err)\n"
        + "def validate(n: int) -> None:\n"
        + "    if n > 99:\n"
        + "        raise Err\n"
        + "@error_return(Err)\n"
        + "def combine(a: int, b: int) -> int:\n"
        + "    validate(a)\n"
        + "    x = parse(a)\n"
        + "    y = parse(b)\n"
        + "    return x + y\n"
        + "@error_return(Err)\n"
        + "def forward(a: int) -> int:\n"
        + "    return parse(a)\n"
        + "try:\n"
        + "    print(combine(1, 2))\n"
        + "    print(forward(3))\n"
        + "except Err:\n"
        + "    print(\"err\")\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        for name in ("parse", "validate", "combine", "forward"):
            assert _fn(thir, name) is not None, name
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_shapes(self):
        thir = _lower_ctx(self.SRC)
        combine = _fn(thir, "combine")
        assert isinstance(combine.body[0], THIRErrorReturnDiscard)
        bind = combine.body[1]
        assert isinstance(bind, THIRErrorReturnBind)
        assert bind.name == "x" and bind.decl_cpp == "::tpy::BigInt"
        forward = _fn(thir, "forward")
        ret = forward.body[0]
        assert isinstance(ret, THIRReturn)  # raw expected pass-through

    def test_emit_shapes(self):
        cpp = _cpp(self.SRC, thir=True)
        # Propagate checks inside @error_return bodies...
        assert ("if (!__try_tmp_1.has_value()) return "
                "::tpy::make_unexpected(__try_tmp_1.error());" in cpp)
        # ...the predecl + unwrap of the bind...
        assert "::tpy::BigInt x;" in cpp
        assert "x = ::tpy::unwrap_ref_move(*__try_tmp_2);" in cpp
        # ...and the pass-through return (no unwrap+rewrap).
        assert "return parse(a);" in cpp

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("er.bind", 0) >= 2
        assert w.get("er.discard", 0) >= 1
        assert w.get("er.return_passthrough", 0) >= 1


class TestErrorReturnExprUnwrap:
    SRC = (
        _ERR
        + "@error_return(Err)\n"
        + "def parse(n: int) -> int:\n"
        + "    if n < 0:\n"
        + "        raise Err\n"
        + "    return n\n"
        + "@error_return(Err)\n"
        + "def add(a: int, b: int) -> int:\n"
        + "    return parse(a) + parse(b)\n"
        + "try:\n"
        + "    print(add(1, 2))\n"
        + "except Err:\n"
        + "    print(\"err\")\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "add") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_unwrap_emit(self):
        cpp = _cpp(self.SRC, thir=True)
        assert ("({ auto __er_1 = parse(a); if (!__er_1.has_value()) "
                "return ::tpy::make_unexpected(__er_1.error()); "
                "::tpy::unwrap_ref_move(*__er_1); })" in cpp)

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("er.unwrap", 0) >= 2


class TestErrorReturnTryReturnTier:
    SRC = (
        _ERR
        + "@error_return(Err)\n"
        + "def parse(n: int) -> int:\n"
        + "    if n < 0:\n"
        + "        raise Err\n"
        + "    return n\n"
        + "def main() -> None:\n"
        + "    try:\n"
        + "        v = parse(3)\n"
        + "    except Err:\n"
        + "        print(\"err\")\n"
        + "    else:\n"
        + "        print(v)\n"
        + "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        main = _fn(thir, "main")
        assert main is not None
        t = main.body[0]
        assert isinstance(t, THIRTry) and t.tier == "return"
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_goto_dispatch_emit(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "if (!__try_tmp_2.has_value()) goto __except_1;" in cpp
        assert "__except_1:;" in cpp
        assert "goto __after_try_1;" in cpp
        assert "__after_try_1:;" in cpp
        assert "// except Err:" in cpp

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("er.try_return", 0) > 0
        assert w.get("try.hoist_decl", 0) > 0  # `v` hoisted (used in else)


class TestErrorReturnTryBindingAndReraise:
    SRC = (
        _ERR
        + "@error_return(Err)\n"
        + "def parse(n: int) -> int:\n"
        + "    if n < 0:\n"
        + "        raise Err\n"
        + "    return n\n"
        + "def outer(n: int) -> int:\n"
        + "    try:\n"
        + "        try:\n"
        + "            return parse(n)\n"
        + "        except Err as e:\n"
        + "            raise\n"
        + "    except Err:\n"
        + "        return -1\n"
        + "try:\n"
        + "    print(outer(5))\n"
        + "except Err:\n"
        + "    print(\"outer\")\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "outer") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_binding_and_reraise_emit(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "std::optional<Err> __err_opt_" in cpp
        assert "auto& e = *__err_opt_" in cpp
        # The bare `raise` in the return-tier handler re-returns the capture.
        assert "return ::tpy::make_unexpected(std::move(*__err_opt_" in cpp

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("er.try_binding", 0) > 0
        assert w.get("er.reraise", 0) > 0


class TestErrorReturnGateRejections:
    def _rejected(self, src: str, name: str) -> bool:
        thir = _lower_ctx(src)
        return _fn(thir, name) is None

    def test_borrow_result_bind_rejected(self):
        # An @error_return free function returning a borrow (`-> Item`, a
        # cpp-ref result): the unwrap must ALIAS live storage
        # (_error_return_result_aliases) -- the pointer-local bind shape is
        # not mirrored (`error_return.alias_bind`); the caller falls back.
        src = (
            _ERR
            + "class Item:\n"
            + "    def __init__(self, v: int) -> None:\n"
            + "        self.v = v\n"
            + "class Store:\n"
            + "    item: Item\n"
            + "    def __init__(self) -> None:\n"
            + "        self.item = Item(1)\n"
            + "@error_return(Err)\n"
            + "def get_item(s: Store) -> Item:\n"
            + "    return s.item\n"
            + "def use(s: Store) -> int:\n"
            + "    try:\n"
            + "        it = get_item(s)\n"
            + "    except Err:\n"
            + "        return -1\n"
            + "    return it.v\n"
            + "print(use(Store()))\n"
        )
        assert self._rejected(src, "use")

    def test_method_callee_bind_rejected(self):
        # A METHOD @error_return callee at a statement bind: the AST
        # statement handler covers it, so lowering must fall the body back
        # (`error_return.stmt_shape`), never route it through the
        # expression-unwrap face.
        src = (
            _ERR
            + "class Store:\n"
            + "    def __init__(self, v: int) -> None:\n"
            + "        self.v = v\n"
            + "    @error_return(Err)\n"
            + "    def get(self) -> int:\n"
            + "        if self.v < 0:\n"
            + "            raise Err\n"
            + "        return self.v\n"
            + "def use(s: Store) -> int:\n"
            + "    try:\n"
            + "        v = s.get()\n"
            + "    except Err:\n"
            + "        return -1\n"
            + "    return v\n"
            + "print(use(Store(2)))\n"
        )
        assert self._rejected(src, "use")

    def test_str_first_decl_bind_rejected(self):
        # An owned-str success bound at a NON-hoisted first decl: the
        # owned-local read model keys on the hoist machinery, so the
        # `error_return.bind_slot` gate falls the body back.
        src = (
            _ERR
            + "@error_return(Err)\n"
            + "def name_of(n: int) -> str:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    return \"x\"\n"
            + "@error_return(Err)\n"
            + "def caller(n: int) -> int:\n"
            + "    s = name_of(n)\n"
            + "    print(s)\n"
            + "    return n\n"
            + "try:\n"
            + "    print(caller(1))\n"
            + "except Err:\n"
            + "    print(\"err\")\n"
        )
        assert self._rejected(src, "caller")

    def test_async_error_return_parser_rejected(self):
        # The combo never reaches THIR: the parser rejects
        # `async def + @error_return` outright, which is why lowering's
        # is_async/is_generator error_return guard is defensive-only.
        src = (
            _ERR
            + "import asyncio\n"
            + "@error_return(Err)\n"
            + "async def fetch(n: int) -> int:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    return n\n"
            + "async def main() -> None:\n"
            + "    try:\n"
            + "        print(await fetch(1))\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "asyncio.run(main())\n"
        )
        with pytest.raises(ParseError):
            _compile(src)


def _fallback_tags(src: str) -> dict:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_ERR32 = (
    "from tpy import Int32, Own, error_return, ReturnException\n"
    "class Err(Exception, ReturnException):\n"
    "    pass\n"
)


class TestErrorReturnUnwrapPtr:
    # The POINTER-form expression unwrap (a borrow-returning fallible
    # callee): no corpus case reaches it, so this is its only pin. The
    # for-each source capture binds `auto&` to the deref'd statement
    # expression -- `auto& __obj_0 = (*({ ...; &unwrap_ref(*__er_1); }));`.
    SRC = (
        _ERR32
        + "items: list[Int32] = [1, 2, 3]\n"
        + "@error_return(Err)\n"
        + "def view(n: Int32) -> list[Int32]:\n"
        + "    if n < 0:\n"
        + "        raise Err\n"
        + "    return items\n"
        + "@error_return(Err)\n"
        + "def total(n: Int32) -> Int32:\n"
        + "    s = 0\n"
        + "    for x in view(n):\n"
        + "        s = s + x\n"
        + "    return s\n"
        + "def main() -> None:\n"
        + "    try:\n"
        + "        print(total(3))\n"
        + "    except Err:\n"
        + "        print(\"err\")\n"
        + "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "total") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_pointer_form_emit(self):
        cpp = _cpp(self.SRC, thir=True)
        assert ("(*({ auto __er_1 = view(n); if (!__er_1.has_value()) "
                "return ::tpy::make_unexpected(__er_1.error()); "
                "&::tpy::unwrap_ref(*__er_1); }))" in cpp)

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("er.unwrap_ptr", 0) > 0


class TestErrorReturnRaiseArgs:
    SRC = (
        "from tpy import Int32, error_return, ReturnException\n"
        "class ParseErr(Exception, ReturnException):\n"
        "    code: Int32\n"
        "    def __init__(self, code: Int32) -> None:\n"
        "        self.code = code\n"
        "@error_return(ParseErr)\n"
        "def parse(n: Int32) -> Int32:\n"
        "    if n < 0:\n"
        "        raise ParseErr(7)\n"
        "    return n\n"
        "def main() -> None:\n"
        "    try:\n"
        "        print(parse(3))\n"
        "    except ParseErr as e:\n"
        "        print(e.code)\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "parse") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_raise_args_emit(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "return ::tpy::make_unexpected(ParseErr(7));" in cpp

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("er.raise_args", 0) > 0


class TestErrorReturnDeferredGateDetails:
    """Each deferred error_return.* gate rejects with its named detail and
    falls the WHOLE body back (never a partial route)."""

    def test_nested_call_gated(self):
        # `return f(g(n))`, both fallible: the AST's first-consumer
        # `error_return_stmt_handled` flag mis-binds to the inner call and
        # emits ill-formed C++ -- gate-rejected, never mirrored.
        src = (
            _ERR32
            + "@error_return(Err)\n"
            + "def g(n: Int32) -> Int32:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    return n\n"
            + "@error_return(Err)\n"
            + "def f(n: Int32) -> Int32:\n"
            + "    return g(n) * 2\n"
            + "@error_return(Err)\n"
            + "def h(n: Int32) -> Int32:\n"
            + "    return f(g(n))\n"
            + "def main() -> None:\n"
            + "    try:\n"
            + "        print(h(3))\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "main()\n"
        )
        tags = _fallback_tags(src)
        assert tags.get("body:stmt.return:error_return.nested_call") == 1
        thir = _lower_ctx(src)
        assert _fn(thir, "h") is None
        assert _fn(thir, "f") is not None  # expression unwrap still routes

    def test_bind_target_gated(self):
        # A rebind-slot pointer-local target: the unwrap assign would need
        # the slot re-point machinery.
        src = (
            _ERR32
            + "class Box:\n"
            + "    v: Int32\n"
            + "    def __init__(self, v: Int32) -> None:\n"
            + "        self.v = v\n"
            + "@error_return(Err)\n"
            + "def make(n: Int32) -> Own[Box]:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    return Box(n)\n"
            + "@error_return(Err)\n"
            + "def caller(n: Int32) -> Int32:\n"
            + "    b = Box(1)\n"
            + "    print(b.v)\n"
            + "    b = make(n)\n"
            + "    return b.v\n"
            + "def main() -> None:\n"
            + "    try:\n"
            + "        print(caller(2))\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "main()\n"
        )
        tags = _fallback_tags(src)
        assert tags.get("body:stmt.var_decl:error_return.bind_target") == 1

    def test_assign_target_gated(self):
        # A field assign target: _error_return_target_assign's non-name arm.
        src = (
            _ERR32
            + "@error_return(Err)\n"
            + "def make_v(n: Int32) -> Int32:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    return n\n"
            + "class C:\n"
            + "    v: Int32\n"
            + "    def __init__(self) -> None:\n"
            + "        self.v = 0\n"
            + "    @error_return(Err)\n"
            + "    def fill(self, n: Int32) -> None:\n"
            + "        self.v = make_v(n)\n"
            + "def main() -> None:\n"
            + "    c = C()\n"
            + "    try:\n"
            + "        c.fill(3)\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "    print(c.v)\n"
            + "main()\n"
        )
        tags = _fallback_tags(src)
        assert tags.get("body:stmt.assign:error_return.assign_target") == 1

    def test_ret_shape_gated(self):
        # A METHOD fallible callee in a pass-through return.
        src = (
            _ERR32
            + "class Store:\n"
            + "    v: Int32\n"
            + "    def __init__(self, v: Int32) -> None:\n"
            + "        self.v = v\n"
            + "    @error_return(Err)\n"
            + "    def get(self) -> Int32:\n"
            + "        if self.v < 0:\n"
            + "            raise Err\n"
            + "        return self.v\n"
            + "@error_return(Err)\n"
            + "def read(s: Store) -> Int32:\n"
            + "    return s.get()\n"
            + "def main() -> None:\n"
            + "    try:\n"
            + "        print(read(Store(2)))\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "main()\n"
        )
        tags = _fallback_tags(src)
        assert tags.get("body:stmt.return:error_return.ret_shape") == 1

    def test_ret_slot_gated(self):
        # An Optional return slot: the AST's Optional return arms run BEFORE
        # its pass-through check, so the mirror rejects the composition.
        src = (
            _ERR32
            + "@error_return(Err)\n"
            + "def pick(n: Int32) -> Int32 | None:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    if n == 0:\n"
            + "        return None\n"
            + "    return n\n"
            + "@error_return(Err)\n"
            + "def relay(n: Int32) -> Int32 | None:\n"
            + "    return pick(n)\n"
            + "def main() -> None:\n"
            + "    try:\n"
            + "        v = pick(3)\n"
            + "        if v is not None:\n"
            + "            print(v)\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "main()\n"
        )
        tags = _fallback_tags(src)
        assert tags.get("body:stmt.return:error_return.ret_slot") == 1


class TestErrorReturnNestedDef:
    # A nested def is never @error_return (gated at lowering), so the
    # enclosing @error_return function's emit state must not leak into the
    # lambda body: its bare `return` is a plain `return;`, never the
    # error_return `return {};`.
    SRC = (
        _ERR
        + "@error_return(Err)\n"
        + "def outer(n: int) -> int:\n"
        + "    def helper(x: int) -> None:\n"
        + "        if x < 0:\n"
        + "            return\n"
        + "        print(x)\n"
        + "    helper(n)\n"
        + "    if n < 0:\n"
        + "        raise Err\n"
        + "    return n\n"
        + "try:\n"
        + "    print(outer(3))\n"
        + "except Err:\n"
        + "    print(\"err\")\n"
    )

    def test_routes(self):
        assert _fn(_lower_ctx(self.SRC), "outer") is not None

    def test_nested_bare_return_stays_void(self):
        thir_cpp = _cpp(self.SRC, thir=True)
        lambda_body = thir_cpp[thir_cpp.index("auto helper"):
                               thir_cpp.index("helper(n)")]
        assert "return {};" not in lambda_body
        assert "return;" in lambda_body

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)


class TestErrorReturnMethod:
    # The sig gate admits sync @error_return METHODS, not just free
    # functions; pin the method body's routing and render.
    SRC = (
        _ERR
        + "class Store:\n"
        + "    def __init__(self, v: int) -> None:\n"
        + "        self.v = v\n"
        + "    @error_return(Err)\n"
        + "    def get(self) -> int:\n"
        + "        if self.v < 0:\n"
        + "            raise Err\n"
        + "        return self.v\n"
        + "s = Store(2)\n"
        + "try:\n"
        + "    print(s.get())\n"
        + "except Err:\n"
        + "    print(\"err\")\n"
    )

    def test_routes(self):
        assert _fn(_lower_ctx(self.SRC), "get") is not None

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)
