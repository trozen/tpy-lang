"""THIR @error_return: the function-body side (return-tier raise ->
`make_unexpected`, bare-return `{}`, the trailing void success `return {};`,
the expected pass-through return), the caller side (the statement-level
`__try_tmp_N` bind/discard blocks, the expression-level `__er_N`
statement-expression unwrap in its three dispositions), the return-tier
try goto dispatch (`__except_N` / `__after_try_N`, the `__err_opt_N` as
capture, the bare-raise re-raise), and the gate rejections that keep the
un-mirrored shapes (coerce-wrapped inits, container bind slots) on the AST
path."""

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
from .testutil import (
    _assert_rejects_at, _assert_routes_byte_identical, _compile, _entry,
    _fn, _lower_ctx, _lower_ctx_witnessed, _thir_ctx,
)


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

    def test_borrow_result_bind_routes(self):
        # An @error_return free function returning a borrow (`-> Item`, a
        # cpp-ref result): the alias-bind arm mirrors the pointer-local
        # bind (`it = &(::tpy::unwrap_ref(*__try_tmp_N));`) -- formerly a
        # fence, converted when call.er_ref_bind landed.
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
        from .testutil import _assert_byte_identical
        _assert_byte_identical(src)
        assert not self._rejected(src, "use")

    def test_expression_position_method_callee_routes(self):
        # A METHOD @error_return callee OUTSIDE the statement handlers (here
        # a call argument) takes the `__er_N` statement-expression unwrap --
        # the free-call arm's mirror, same THIRErrorReturnUnwrap render.
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
            + "def twice(n: int) -> int:\n"
            + "    return n * 2\n"
            + "def use(s: Store) -> int:\n"
            + "    try:\n"
            + "        v = twice(s.get())\n"
            + "    except Err:\n"
            + "        return -1\n"
            + "    return v\n"
            + "print(use(Store(2)))\n"
        )
        _assert_routes_byte_identical(src)
        assert "auto __er_" in _cpp(src, thir=True)

    def test_coerce_wrapped_bind_rejected(self):
        # `_error_return_stmt_fi` peels a TpyCoerce, but the bind/discard/
        # return arms admit the bare call node only -- a coerced init keeps
        # falling back (`error_return.stmt_shape`).
        src = (
            _ERR
            + "from tpy import Int32\n"
            + "@error_return(Err)\n"
            + "def small(n: Int32) -> Int32:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    return n\n"
            + "def use(n: Int32) -> int:\n"
            + "    try:\n"
            + "        v: int = small(n)\n"
            + "    except Err:\n"
            + "        return -1\n"
            + "    return v\n"
            + "print(use(2))\n"
        )
        assert self._rejected(src, "use")

    def test_container_bind_slot_rejected(self):
        # A container success type has no default-constructible predecl slot
        # whose later reads route off the declared type -- the
        # `error_return.bind_slot` gate still falls the body back.
        src = (
            _ERR
            + "from tpy import Int32, Own\n"
            + "@error_return(Err)\n"
            + "def items(n: Int32) -> Own[list[Int32]]:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    return [n]\n"
            + "@error_return(Err)\n"
            + "def caller(n: Int32) -> Int32:\n"
            + "    xs = items(n)\n"
            + "    print(len(xs))\n"
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

    def test_ptr_variant_bind_target_gated(self):
        # A ptr-variant union local reseats through `to_ptr_variant`, not a
        # bare slot address -- the rebind-slot bind arm excludes it.
        src = (
            _ERR32
            + "class Cat:\n"
            + "    n: Int32\n"
            + "    def __init__(self) -> None:\n"
            + "        self.n = 1\n"
            + "class Dog:\n"
            + "    n: Int32\n"
            + "    def __init__(self) -> None:\n"
            + "        self.n = 2\n"
            + "class Maker:\n"
            + "    @staticmethod\n"
            + "    @error_return(Err)\n"
            + "    def make(n: Int32) -> Own[Cat]:\n"
            + "        if n < 0:\n"
            + "            raise Err\n"
            + "        return Cat()\n"
            + "@error_return(Err)\n"
            + "def caller(n: Int32) -> Int32:\n"
            + "    p: Cat | Dog = Dog()\n"
            + "    p = Maker.make(n)\n"
            + "    if isinstance(p, Cat):\n"
            + "        return p.n\n"
            + "    return 0\n"
            + "def main() -> None:\n"
            + "    try:\n"
            + "        print(caller(2))\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "main()\n"
        )
        tags = _fallback_tags(src)
        assert tags.get("body:stmt.var_decl:error_return.bind_target") == 1

    def test_rebind_slot_bind_target_routes(self):
        # The rebind-slot pointer-local reseat DOES route: the bind line
        # takes `_ptr_from_rvalue_slot`'s `&*(__slot_N = <unwrap>)` render,
        # slot reused across reseats.
        src = (
            _ERR32
            + "class Box:\n"
            + "    v: Int32\n"
            + "    def __init__(self, v: Int32) -> None:\n"
            + "        self.v = v\n"
            + "class Maker:\n"
            + "    @staticmethod\n"
            + "    @error_return(Err)\n"
            + "    def make(n: Int32) -> Own[Box]:\n"
            + "        if n < 0:\n"
            + "            raise Err\n"
            + "        return Box(n)\n"
            + "@error_return(Err)\n"
            + "def caller(n: Int32) -> Int32:\n"
            + "    b: Box | None = None\n"
            + "    b = Maker.make(n)\n"
            + "    print(b.v)\n"
            + "    b = Maker.make(n + 1)\n"
            + "    if b is None:\n"
            + "        raise Err\n"
            + "    return b.v\n"
            + "def main() -> None:\n"
            + "    try:\n"
            + "        print(caller(2))\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "main()\n"
        )
        assert _fn(_lower_ctx(src), "caller") is not None
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert cpp.count("b = &*(__slot_1 = ") == 2
        assert _emit_witnesses(src).get("er.bind_ptr_rebind", 0) > 0

    def test_assign_target_field_routes(self):
        # A field assign target now rides the er field-target bind arm
        # (_error_return_target_assign's non-name branch, scalar flavor):
        # `this->v = ::tpy::unwrap_ref_move(*__try_tmp_N);`.
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
        _hpp, cpp = _assert_routes_byte_identical(src)
        joined = _hpp + cpp
        assert "this->v = ::tpy::unwrap_ref_move(*__try_tmp_" in joined

    def test_method_callee_statement_sites_route(self):
        # A METHOD fallible callee at all three statement-handled sites
        # (pass-through return, bind, discard): the enclosing statement owns
        # the unwrap, so the call renders as the bare member call.
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
            + "    @error_return(Err)\n"
            + "    def step(self) -> None:\n"
            + "        if self.v < 0:\n"
            + "            raise Err\n"
            + "@error_return(Err)\n"
            + "def read(s: Store) -> Int32:\n"
            + "    return s.get()\n"
            + "@error_return(Err)\n"
            + "def bind_and_discard(s: Store) -> Int32:\n"
            + "    s.step()\n"
            + "    v = s.get()\n"
            + "    return v\n"
            + "def main() -> None:\n"
            + "    try:\n"
            + "        print(read(Store(2)))\n"
            + "        print(bind_and_discard(Store(3)))\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "main()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "read") is not None
        assert _fn(thir, "bind_and_discard") is not None
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "return s.get();" in cpp
        w = _emit_witnesses(src)
        assert w.get("er.bind", 0) > 0
        assert w.get("er.discard", 0) > 0

    def test_str_family_bind_slot_routes(self):
        # An owned-str / bytes success predecls `std::string s;` before the
        # unwrap block; every later read routes off the declared type.
        src = (
            _ERR32
            + "@error_return(Err)\n"
            + "def name_of(n: Int32) -> str:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    return \"x\"\n"
            + "@error_return(Err)\n"
            + "def blob_of(n: Int32) -> bytes:\n"
            + "    if n < 0:\n"
            + "        raise Err\n"
            + "    return b\"xy\"\n"
            + "@error_return(Err)\n"
            + "def caller(n: Int32) -> Int32:\n"
            + "    s = name_of(n)\n"
            + "    print(s)\n"
            + "    print(len(s))\n"
            + "    b = blob_of(n)\n"
            + "    print(len(b))\n"
            + "    return n\n"
            + "def main() -> None:\n"
            + "    try:\n"
            + "        print(caller(1))\n"
            + "    except Err:\n"
            + "        print(\"err\")\n"
            + "main()\n"
        )
        assert _fn(_lower_ctx(src), "caller") is not None
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "std::string s;" in cpp

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


class TestPtrOptionalRecordReturn:
    """`return maybe;` where `maybe` is a ptr-repr `Optional[record]` LOCAL
    at a record return slot: the AST's indirect-name arm derefs the pointer
    and moves at a movable last use (`return std::move((*maybe));`). The
    binding sits outside `admission_pointers`, so it reaches the record
    ladder as a plain name -- the shape every `@model` decoder ends on."""

    SRC = (
        "from tpy import Int32, Own\n"
        "class P:\n"
        "    a: Int32\n"
        "    def __init__(self, a: Int32) -> None:\n"
        "        self.a = a\n"
        "def make(a: Int32) -> Own[P]:\n"
        "    return P(a)\n"
        "def pick(flag: bool) -> Own[P]:\n"
        "    hit: P | None = None\n"
        "    if flag:\n"
        "        hit = make(1)\n"
        "    if hit is None:\n"
        "        return P(0)\n"
        "    return hit\n"
        "print(pick(True).a)\n"
    )

    def test_routes_and_witnesses(self):
        assert _fn(_lower_ctx(self.SRC), "pick") is not None
        w = _emit_witnesses(self.SRC)
        assert w.get("ret.record_ptr_opt_local", 0) >= 1

    def test_byte_identical_and_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        assert cpp == _cpp(self.SRC, thir=False)
        assert "return std::move((*hit));" in cpp

    def test_non_record_inner_still_rejects(self):
        # The arm is RECORD-inner only: a ptr-repr Optional over a container
        # is a different return slot family.
        src = (
            "from tpy import Int32, Own\n"
            "def pick(flag: bool) -> Own[list[Int32]]:\n"
            "    hit: list[Int32] | None = None\n"
            "    if flag:\n"
            "        hit = [1, 2]\n"
            "    if hit is None:\n"
            "        return [0]\n"
            "    return hit\n"
            "print(len(pick(True)))\n"
        )
        assert _fn(_lower_ctx(src), "pick") is None


class TestPlainPtrLocalRecordReturn:
    """`return best;` where `best` is a PLAIN F1 pointer-local (a `T*`
    alias) at a record BORROW return slot: the same indirect-name arm as
    the ptr-Optional sibling, `return (*best);` -- no move (an alias local
    is never promoted movable; the render mirrors _maybe_move's verdict)."""

    SRC = (
        "from tpy import Int32\n"
        "class P:\n"
        "    a: Int32\n"
        "    def __init__(self, a: Int32) -> None:\n"
        "        self.a = a\n"
        "def find_max(points: list[P]) -> P:\n"
        "    best = points[0]\n"
        "    for p in points:\n"
        "        if p.a > best.a:\n"
        "            best = p\n"
        "    return best\n"
        "def main() -> None:\n"
        "    pts = [P(1), P(9), P(4)]\n"
        "    m = find_max(pts)\n"
        "    m.a += 1\n"
        "    print(pts[1].a)\n"
        "main()\n"
    )

    def test_routes_and_witnesses(self):
        assert _fn(_lower_ctx(self.SRC), "find_max") is not None
        w = _emit_witnesses(self.SRC)
        assert w.get("ret.record_ptr_local", 0) >= 1

    def test_byte_identical_and_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        assert cpp == _cpp(self.SRC, thir=False)
        assert "return (*best);" in cpp
        assert "std::move((*best))" not in cpp

    def test_own_return_rebind_slot_local_moves(self):
        # The STORAGE flavor: a reassigned (F2d rebind-slot) pointer local
        # at an Own return moves at its last use -- the arm covers both
        # slot kinds and both pointer-local classifications.
        src = (
            "from tpy import Int32, Own\n"
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "def pick(flag: bool) -> Own[P]:\n"
            "    best = P(1)\n"
            "    if flag:\n"
            "        best = P(9)\n"
            "    return best\n"
            "print(pick(True).a)\n"
        )
        assert _fn(_lower_ctx(src), "pick") is not None
        w = _emit_witnesses(src)
        assert w.get("ret.record_ptr_local", 0) >= 1
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "return std::move((*best));" in cpp

    def test_narrowed_ptr_name_still_rejects(self):
        # A NARROWED pointer name keeps its alias-rename render -- out of
        # this arm (the ladder excludes narrowed names).
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "def pick(hit: P | None, fallback: P) -> P:\n"
            "    if hit is not None:\n"
            "        return hit\n"
            "    return fallback\n"
            "def main() -> None:\n"
            "    f = P(2)\n"
            "    r = pick(None, f)\n"
            "    r.a += 1\n"
            "    print(f.a)\n"
            "main()\n"
        )
        # The narrowed-param return either routes via its own narrowed arm
        # or falls back -- but never through ret.record_ptr_local.
        w = _emit_witnesses(src)
        assert w.get("ret.record_ptr_local", 0) == 0


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


class TestErrorReturnBindIsNotAutoMovable:
    """The AST's `movable_locals` is a WORKING set grown at the var-decl arms,
    and `_gen_error_return_var_decl` is not one of them. THIR seeds the whole
    sema fact up front, so the unwrap bind has to drop the name -- otherwise
    its last use picks up a `std::move` the AST never emits."""

    SRC = _ERR + (
        "from tpy import Int32, Own\n"
        "class Rec:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.v = v\n"
        "@error_return(Err)\n"
        "def make(n: Int32) -> Own[Rec]:\n"
        "    if n < 0:\n"
        "        raise Err\n"
        "    return Rec(n)\n"
        "def collect() -> Int32:\n"
        "    out: list[Rec] = []\n"
        "    try:\n"
        "        for i in range(3):\n"
        "            r = make(i)\n"
        "            out.append(r)\n"
        "    except Err:\n"
        "        return -1\n"
        "    return len(out)\n"
        "def main() -> None:\n"
        "    print(collect())\n"
        "main()\n"
    )

    def test_routes(self):
        # The load-bearing pin: THIR is byte-identical by design, so if this
        # body fell back the AST would emit the same C++ and every render
        # assertion below would pass vacuously.
        from .testutil import _lower_ctx, _fn
        assert _fn(_lower_ctx(self.SRC), "collect") is not None

    def test_the_unwrap_bound_local_is_appended_without_a_move(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "Rec r;" in cpp
        assert "out.push_back(r);" in cpp
        assert "out.push_back(std::move(r));" not in cpp

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)


class TestErrorReturnRefBind:
    """The er-bind of a REF-returning @error_return callee: the alias-bind
    arm renders `r = &(::tpy::unwrap_ref(*__try_tmp_N));` into a hoisted
    pointer local (call.er_ref_bind); reads then arrow through it."""

    SRC = (
        "from tpy import Int32, error_return, ReturnException\n"
        "class E(Exception, ReturnException):\n"
        "    pass\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32):\n"
        "        self.x = x\n"
        "@error_return(E)\n"
        "def positive(p: Point) -> Point:\n"
        "    if p.x < 0:\n"
        "        raise E\n"
        "    return p\n"
        "def main() -> None:\n"
        "    p = Point(1)\n"
        "    try:\n"
        "        r = positive(p)\n"
        "    except E:\n"
        "        print(\"err\")\n"
        "    else:\n"
        "        r.x = 9\n"
        "        print(p.x)\n"
        "main()\n")

    def test_ref_bind_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        _thir, wit = _lower_ctx_witnessed(self.SRC)
        assert wit.get("call.er_ref_bind", 0) >= 1
        assert "r = &(::tpy::unwrap_ref(*__try_tmp_" in cpp

    def test_ref_discard_routes(self):
        # The DISCARD flavor of the same rung: the try block renders the
        # bare call with no bind (`auto __try_tmp_N = positive(a);` +
        # the goto), ref-ness-blind on the AST side.
        src = self.SRC.replace(
            "    try:\n"
            "        r = positive(p)\n",
            "    try:\n"
            "        positive(p)\n").replace(
            "    else:\n"
            "        r.x = 9\n"
            "        print(p.x)\n",
            "    else:\n"
            "        print(p.x)\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "auto __try_tmp_" in cpp


class TestErrorReturnAliasFirstDecl:
    """The FIRST-DECL alias bind (no try hoist -- the auto-propagate body):
    `_error_return_decl_prefix`'s aliases arm predecls `T* v;`, the block
    alias-assigns, and later reads arrow through the pointer local. Routed
    slice is the provably non-const flavor; a readonly callee stays AST."""

    _H = (
        "from tpy import Int32, error_return, ReturnException, readonly\n"
        "class E(Exception, ReturnException):\n"
        "    pass\n"
        "class H:\n"
        "    items: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self.items = [1, 2]\n"
        "    @error_return(E)\n"
        "    def view(self) -> list[Int32]:\n"
        "        return self.items\n"
        "    @error_return(E)\n"
        "    @readonly\n"
        "    def rview(self) -> list[Int32]:\n"
        "        return self.items\n")

    def test_first_decl_alias_bind_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = (
            self._H
            + "@error_return(E)\n"
            + "def use_mut(h: H) -> Int32:\n"
            + "    v = h.view()\n"
            + "    v.append(8)\n"
            + "    return len(h.items)\n"
            + "def main() -> None:\n"
            + "    h = H()\n"
            + "    try:\n"
            + "        print(use_mut(h))\n"
            + "    except E:\n"
            + "        print(\"error\")\n"
            + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("er.alias_first_decl", 0) >= 1
        assert "std::vector<int32_t>* v;" in cpp
        assert "v = &(::tpy::unwrap_ref(*__try_tmp_" in cpp
        assert "v->push_back(8);" in cpp

    def test_readonly_callee_first_decl_still_defers(self):
        # The const flavor (`const std::vector<int32_t>* r;`) is outside
        # the slice -- a readonly er callee's alias bind keeps the AST path.
        src = (
            self._H
            + "@error_return(E)\n"
            + "def use_ro(h: H) -> Int32:\n"
            + "    r = h.rview()\n"
            + "    return len(r)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use_ro") is None


class TestErrorReturnAliasRebindSlotTarget:
    """A REBIND-SLOT pointer local at an aliasing er bind still takes the
    address-of alias, never the slot: the AST keys that arm on the pointer
    local alone, and materializing into the slot would copy and sever the
    borrow. The slot stays unallocated."""

    _H = (
        "from tpy import Int32, error_return, ReturnException\n"
        "class E(Exception, ReturnException):\n"
        "    pass\n"
        "class Source:\n"
        "    items: list[Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self.items = [1, 2, 3]\n"
        "class H:\n"
        "    a: Source\n"
        "    b: Source\n"
        "    def __init__(self) -> None:\n"
        "        self.a = Source()\n"
        "        self.b = Source()\n"
        "    @error_return(E)\n"
        "    def va(self) -> Source:\n"
        "        return self.a\n"
        "    @error_return(E)\n"
        "    def vb(self) -> Source:\n"
        "        return self.b\n")

    def test_rebind_slot_pointer_target_alias_binds(self):
        src = (
            self._H
            + "def main() -> None:\n"
            + "    h = H()\n"
            + "    q: Source | None = None\n"
            + "    try:\n"
            + "        q = h.va()\n"
            + "        q.items.append(4)\n"
            + "        q = h.vb()\n"
            + "    except E:\n"
            + "        print(\"error\")\n"
            + "    if q is not None:\n"
            + "        q.items.append(5)\n"
            + "    print(len(h.a.items))\n"
            + "    print(len(h.b.items))\n"
            + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "Source* q = nullptr;" in cpp
        assert cpp.count("q = &(::tpy::unwrap_ref(*__try_tmp_") == 2
        assert "__slot_" not in cpp

    def test_ptr_variant_union_target_still_defers(self):
        # A `Cat | Dog` local binds `std::variant<Cat*, Dog*>`, not a bare
        # pointer: its reseat goes through `to_ptr_variant`, so the alias
        # arm must not claim it.
        src = (
            "from tpy import Int32, error_return, ReturnException\n"
            "class E(Exception, ReturnException):\n"
            "    pass\n"
            "class Cat:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n        self.n = 1\n"
            "class Dog:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n        self.n = 2\n"
            "class H:\n"
            "    c: Cat\n"
            "    def __init__(self) -> None:\n        self.c = Cat()\n"
            "    @error_return(E)\n"
            "    def vc(self) -> Cat:\n        return self.c\n"
            "def main() -> None:\n"
            "    h = H()\n"
            "    d = Dog()\n"
            "    p: Cat | Dog = d\n"
            "    try:\n"
            "        p = h.vc()\n"
            "    except E:\n"
            "        print(\"error\")\n"
            "    if isinstance(p, Cat):\n"
            "        print(p.n)\n"
            "main()\n")
        assert (_fallback_tags(src)
                .get("body:stmt.var_decl:error_return.alias_bind") == 1)


class TestRefCallAtStorageReturn:
    """A ref-returning call at the Own[record] STORAGE return slot renders
    bare and the slot copies from the reference (`return p.updated();`,
    `return positive(x, y).updated()` -- the er-receiver flavor)."""

    _POINT = (
        "from tpy import Int32, Own, Self\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "    def updated(self) -> Self:\n"
        "        self.x += 1\n"
        "        return self\n")

    def test_method_ref_call_storage_return_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        src = (
            self._POINT
            + "def bump(p: Point) -> Own[Point]:\n"
            + "    return p.updated()\n"
            + "def main() -> None:\n"
            + "    p = Point(1)\n"
            + "    q = bump(p)\n"
            + "    q.x = 10\n"
            + "    print(p.x, q.x)\n"
            + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("ret.record_ref_call_storage", 0) >= 1
        assert "return p.updated();" in cpp

    def test_er_receiver_ref_call_storage_return_routes(self):
        # The er-unwrap receiver composes under the same arm:
        # `return ({ ...unwrap_ref_move(*__er_N); }).updated();`.
        src = (
            "from tpy import Int32, Own, Self, error_return, "
            "ReturnException\n"
            "class E(Exception, ReturnException):\n"
            "    pass\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "    def updated(self) -> Self:\n"
            "        self.x += 1\n"
            "        return self\n"
            "@error_return(E)\n"
            "def positive(x: Int32) -> Own[Point]:\n"
            "    if x < 0:\n"
            "        raise E\n"
            "    return Point(x)\n"
            "@error_return(E)\n"
            "def modify(x: Int32) -> Own[Point]:\n"
            "    return positive(x).updated()\n"
            "def main() -> None:\n"
            "    try:\n"
            "        p = modify(3)\n"
            "        print(p.x)\n"
            "    except E:\n"
            "        print(\"err\")\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert ").updated();" in cpp

    def test_free_ref_call_storage_return_routes(self):
        # The free-call twin (`return first(xs);` at Own[Point]) rides the
        # same arm via the borrow_ret_passthrough call admission.
        src = (
            self._POINT
            + "def first(xs: list[Point]) -> Point:\n"
            + "    return xs[0]\n"
            + "def grab(xs: list[Point]) -> Own[Point]:\n"
            + "    return first(xs)\n"
            + "def main() -> None:\n"
            + "    xs = [Point(5)]\n"
            + "    g = grab(xs)\n"
            + "    g.x = 7\n"
            + "    print(xs[0].x, g.x)\n"
            + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "return first(xs);" in cpp


class TestErFieldTargetBind:
    """`self.p = make(n)` inside try: the er-assign FIELD target renders the
    bare lvalue and the bind line moves the unwrap into it
    (`this->p = ::tpy::unwrap_ref_move(*__try_tmp_N);`)."""

    _SRC = (
        "from tpy import Int32, error_return, ReturnException, Own\n"
        "class E(Exception, ReturnException):\n"
        "    pass\n"
        "class Payload:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.v = v\n"
        "@error_return(E)\n"
        "def make(n: Int32) -> Own[Payload]:\n"
        "    if n < 0:\n"
        "        raise E\n"
        "    return Payload(n)\n"
        "class Sink:\n"
        "    p: Payload\n"
        "    def __init__(self) -> None:\n"
        "        self.p = Payload(0)\n"
        "    def fill(self, n: Int32) -> None:\n"
        "        try:\n"
        "            self.p = make(n)\n"
        "        except E:\n"
        "            print(\"fill error\")\n"
        "def main() -> None:\n"
        "    s = Sink()\n"
        "    s.fill(9)\n"
        "    print(s.p.v)\n"
        "main()\n")

    def test_field_target_er_bind_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("er.field_target_bind", 0) >= 1
        joined = _hpp + cpp
        assert "this->p = ::tpy::unwrap_ref_move(*__try_tmp_" in joined

    def test_container_field_target_still_defers(self):
        # A container-field target's RECEIVER-use lowering is outside the
        # slice (field.result_type) -- the body keeps the AST path.
        src = self._SRC.replace(
            "class Sink:\n"
            "    p: Payload\n",
            "class Sink:\n"
            "    p: Payload\n"
            "    xs: list[Int32]\n").replace(
            "    def __init__(self) -> None:\n"
            "        self.p = Payload(0)\n",
            "    def __init__(self) -> None:\n"
            "        self.p = Payload(0)\n"
            "        self.xs = []\n").replace(
            "    def fill(self, n: Int32) -> None:\n"
            "        try:\n"
            "            self.p = make(n)\n",
            "    @error_return(E)\n"
            "    def fill2(self, n: Int32) -> None:\n"
            "        self.xs = make_list(n)\n"
            "    def fill(self, n: Int32) -> None:\n"
            "        try:\n"
            "            self.p = make(n)\n")
        src = src.replace(
            "@error_return(E)\n"
            "def make(n: Int32) -> Own[Payload]:\n",
            "@error_return(E)\n"
            "def make_list(n: Int32) -> Own[list[Int32]]:\n"
            "    if n < 0:\n"
            "        raise E\n"
            "    return [n, n]\n"
            "@error_return(E)\n"
            "def make(n: Int32) -> Own[Payload]:\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "fill2") is None

    def test_nested_er_arg_field_target_still_defers(self):
        # A nested @error_return call in the RHS's argument subtree
        # (`self.p = make(inner(n))`) keeps the AST path -- the same
        # `_reject_nested_error_return_arg` gate the name-target bind
        # applies (the AST's own render for this shape is a tracked bug;
        # THIR must not silently diverge from it).
        src = self._SRC.replace(
            "@error_return(E)\n"
            "def make(n: Int32) -> Own[Payload]:\n",
            "@error_return(E)\n"
            "def inner(n: Int32) -> Int32:\n"
            "    if n > 99:\n"
            "        raise E\n"
            "    return n\n"
            "@error_return(E)\n"
            "def make(n: Int32) -> Own[Payload]:\n").replace(
            "            self.p = make(n)\n",
            "            self.p = make(inner(n))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "fill") is None


class TestRefCallStorageReturnBoundary:
    def test_ternary_source_at_storage_return_still_defers(self):
        # The ladder admits CALL sources only: a ternary over two
        # ref-returning method calls at the same Own[record] storage slot
        # keeps the AST path.
        src = (
            "from tpy import Int32, Own, Self\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "    def updated(self) -> Self:\n"
            "        self.x += 1\n"
            "        return self\n"
            "def pick(a: Point, b: Point, c: bool) -> Own[Point]:\n"
            "    return a.updated() if c else b.updated()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "pick") is None


class TestErrorReturnMethodExprUnwrap:
    """An @error_return METHOD call in an expression position takes the same
    `__er_N` statement-expression unwrap as the free-call arm."""

    SRC = (
        _ERR
        + "from tpy import Int32\n"
        + "class H:\n"
        + "    items: list[Int32]\n"
        + "    def __init__(self) -> None:\n"
        + "        self.items = [1, 2]\n"
        + "    @error_return(Err)\n"
        + "    def er_val(self) -> Int32:\n"
        + "        return 3\n"
        + "    @error_return(Err)\n"
        + "    def er_view(self) -> list[Int32]:\n"
        + "        return self.items\n"
        + "def in_binop(h: H) -> Int32:\n"
        + "    try:\n"
        + "        return h.er_val() + 1\n"
        + "    except Err:\n"
        + "        return -1\n"
        + "def borrow_alias(h: H) -> Int32:\n"
        + "    try:\n"
        + "        if len(u := h.er_view()) > 0:\n"
        + "            u.append(7)\n"
        + "    except Err:\n"
        + "        return -1\n"
        + "    return h.items[len(h.items) - 1]\n"
        + "print(in_binop(H()))\n"
        + "print(borrow_alias(H()))\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_borrow_flavor_keeps_its_address_of_lift(self):
        # The lift is what makes `u` ALIAS `h.items`: losing it would copy
        # the list, and `u.append(7)` would not be observable on `h` -- a
        # silent CPython divergence the byte-diff alone would not name.
        cpp = _cpp(self.SRC, thir=True)
        assert "&::tpy::unwrap_ref(*__er_" in cpp        # borrow: pointer form
        assert "::tpy::unwrap_ref_move(*__er_" in cpp    # value: move form

    def test_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("method.er_expr_unwrap", 0) >= 2
        assert w.get("er.unwrap_ptr", 0) >= 1
        assert w.get("er.unwrap", 0) >= 1


class TestErrorReturnWrapperUnionSlot:
    """A recursive-alias WRAPPER union at the first-binding predecl slot: the
    wrapper is a by-value struct with a defaulted ctor, so `JV val;` predecls
    and the unwrap block assigns into it exactly like an F1-record slot.
    Downstream, the bound name is a plain wrapper value the container insert
    takes bare -- the predecl arm withholds the movable promotion, so nothing
    moves out of it."""

    SRC = ("from tpy import Int32, Own, ReturnException, error_return\n"
           "class Err(Exception, ReturnException):\n"
           "    pass\n"
           "type JV = None | int | str | list[JV]\n"
           "@error_return(Err)\n"
           "def one(src: list[JV], n: Int32) -> Own[JV]:\n"
           "    if n < 0:\n"
           '        raise Err("neg")\n'
           "    return src.pop()\n"
           "@error_return(Err)\n"
           "def build(src: list[JV], n: Int32) -> Own[list[JV]]:\n"
           "    a: list[JV] = []\n"
           "    i = 0\n"
           "    while i < n:\n"
           "        item = one(src, i)\n"
           "        a.append(item)\n"
           "        i += 1\n"
           "    return a\n"
           "def main() -> None:\n"
           "    s: list[JV] = [1, 2]\n"
           "    try:\n"
           "        print(len(build(s, 2)))\n"
           "    except Err:\n"
           '        print("err")\n'
           "main()\n")

    def test_predecl_and_insert_route_byte_identical(self):
        _, cpp = _assert_routes_byte_identical(self.SRC)
        assert "JV item;" in cpp
        assert "item = ::tpy::unwrap_ref_move(*__try_tmp_" in cpp
        # The insert copies: the unwrap-bound name never joins the movable
        # working set, so a `std::move` here would be a divergence.
        assert "a.push_back(item);" in cpp

    def test_predecl_slot_witnesses_the_wrapper_row(self):
        _, faces = _lower_ctx_witnessed(self.SRC)
        assert faces["er.bind_slot_wrapper_union"] >= 1

    def test_call_rvalue_at_the_same_element_slot_keeps_rejecting(self):
        # BOUNDARY: the widened leg is a same-wrapper NAME. An owning CALL
        # rvalue at the element slot is a different render family and keeps
        # the named reject.
        src = ("from tpy import Int32\n"
               "type JV = None | int | str | list[JV]\n"
               "def f(a: list[JV], src: list[JV]) -> Int32:\n"
               "    a.append(src.pop())\n"
               "    return Int32(len(a))\n"
               "def main() -> None:\n"
               "    a: list[JV] = []\n"
               "    s: list[JV] = [1]\n"
               "    print(f(a, s))\n"
               "main()\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.method_call",
                           shape="method.arg_shape")


class TestWrapperUnionElementMove:
    """A MOVABLE wrapper-union name at a container element slot is an insert
    position like any other: `_maybe_move` wraps it, so the bare render would
    silently copy where the AST moves."""

    SRC = ("from tpy import Int32\n"
           "type JV = None | int | str | list[JV]\n"
           "def push(a: list[JV], src: list[JV]) -> Int32:\n"
           "    item = src.pop()\n"
           "    a.append(item)\n"
           "    return Int32(len(a))\n"
           "def store(d: dict[str, JV], src: list[JV]) -> Int32:\n"
           "    item = src.pop()\n"
           '    d["k"] = item\n'
           "    return Int32(len(d))\n"
           "def main() -> None:\n"
           "    a: list[JV] = []\n"
           "    s: list[JV] = [1, 2]\n"
           "    print(push(a, s))\n"
           "    print(store({}, s))\n"
           "main()\n")

    def test_last_use_moves_at_both_insert_sinks(self):
        _, cpp = _assert_routes_byte_identical(self.SRC)
        assert "a.push_back(std::move(item));" in cpp
        assert '::tpy::__setitem__(d, "k", std::move(item));' in cpp

    def test_move_faces_fire(self):
        _, faces = _lower_ctx_witnessed(self.SRC)
        assert faces["move.wrapper_union_elem"] >= 1
        assert faces["setitem.ru_move"] >= 1

    def test_non_movable_binding_stays_a_copy(self):
        # BOUNDARY: a borrowed PARAM is never movable, so the same two sinks
        # must keep rendering the bare copy.
        src = ("from tpy import Int32\n"
               "type JV = None | int | str | list[JV]\n"
               "def push(a: list[JV], item: JV) -> Int32:\n"
               "    a.append(item)\n"
               "    return Int32(len(a))\n"
               "def main() -> None:\n"
               "    a: list[JV] = []\n"
               "    s: list[JV] = [1, 2]\n"
               "    for v in s:\n"
               "        print(push(a, v))\n"
               "main()\n")
        _, cpp = _assert_routes_byte_identical(src)
        assert "a.push_back(item);" in cpp
