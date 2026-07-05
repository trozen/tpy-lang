"""THIR sync `try` statements: the finally_only tier (the unified
try/catch(...)/finally emit, sema-hoisted plain-value predecls, the
re-emitted finally body at return/break/continue sites, terminating
finally bodies -- [[maybe_unused]] capture, suppressed exits), the throw
tier (C++ catch arms, bare except, as-bindings, else labels off the
module-cumulative counter, except+finally wrapping), raise statements
(ctor/no-arg/bare forms), and the gate rejections (the return tier,
expression raise, non-value hoists, branch/loop-scoped fresh hoists)."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import THIRRaise, THIRTry
from .testutil import _compile, _entry, _fn, _lower_ctx, _lower_ctx_witnessed


def _cpp(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return cpp


def _emit_witnesses(src: str):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return compiler._thir_face_witnesses


class TestTryFinallyBasic:
    SRC = (
        "def plain(n: int) -> None:\n"
        "    try:\n"
        "        print(n)\n"
        "    finally:\n"
        "        print(0)\n"
        "    print(n + 1)\n"
        "plain(1)\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "plain") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        t = _fn(thir, "plain").body[0]
        assert isinstance(t, THIRTry)
        assert t.tier == "finally_only"
        assert not t.hoist_decls
        assert not t.body_terminates and not t.finally_terminates

    def test_emit_shape(self):
        # One finally copy in the catch(...) arm + one on the normal path,
        # both inside the extra brace scope; the catch rethrows.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void plain"):]
        assert body.count("std::cout << 0") == 2
        assert "} catch (...) {" in body
        assert "throw;" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("try.finally_only", 0) > 0


class TestTryHoistDecls:
    SRC = (
        "def hoisted() -> None:\n"
        "    try:\n"
        "        y = 5\n"
        "        s = \"in-try\"\n"
        "        print(y, s)\n"
        "    finally:\n"
        "        t = \"fin\"\n"
        "        print(t)\n"
        "    print(y, s, t)\n"
        "hoisted()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "hoisted") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_hoist_decls_render(self):
        # Sorted per sema's predecl order; first assigns become reassigns
        # against the predecl slot.
        thir = _lower_ctx(self.SRC)
        t = _fn(thir, "hoisted").body[0]
        assert [n for n, _ in t.hoist_decls] == ["s", "t", "y"]
        cpp = _cpp(self.SRC, thir=True)
        assert "int32_t y;" in cpp
        assert "y = 5;" in cpp

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("try.hoist_decl", 0) > 0


class TestTryOuterDeclSkip:
    # A loop-body try rebinding a pre-loop local: sema hoists the name (the
    # loop body is its own sema scope) but the AST skips the predecl via its
    # declared_vars check -- the gate must skip, not reject.
    SRC = (
        "def loop_reassign(k: int) -> int:\n"
        "    total = 0\n"
        "    for i in range(k):\n"
        "        try:\n"
        "            total = total + i\n"
        "        finally:\n"
        "            total = total + 100\n"
        "    return total\n"
        "print(loop_reassign(3))\n"
    )

    def test_routed_no_predecl(self):
        thir = _lower_ctx(self.SRC)
        fn = _fn(thir, "loop_reassign")
        assert fn is not None
        t = fn.body[1].body[0]
        assert isinstance(t, THIRTry) and not t.hoist_decls
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)


class TestTryFinallyReturn:
    SRC = (
        "def ret_through(n: int) -> int:\n"
        "    try:\n"
        "        if n > 2:\n"
        "            return n * 2\n"
        "        print(n)\n"
        "    finally:\n"
        "        print(9)\n"
        "    return 0\n"
        "def bare_ret(n: int) -> None:\n"
        "    try:\n"
        "        if n > 0:\n"
        "            return\n"
        "        print(n)\n"
        "    finally:\n"
        "        print(8)\n"
        "print(ret_through(5))\n"
        "bare_ret(1)\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "ret_through") is not None
        assert _fn(thir, "bare_ret") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_return_captures_then_reemits_finally(self):
        # Python evaluates the return expression before the finally body:
        # the value lands in __tpy_ret_N, the finally copy follows, then the
        # temp returns.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("::tpy::BigInt ret_through"):cpp.index("void bare_ret")]
        i_tmp = body.index("::tpy::BigInt __tpy_ret_0 = ((n) * (::tpy::BigInt(2)));")
        i_fin = body.index("std::cout << 9", i_tmp)
        assert body.index("return __tpy_ret_0;") > i_fin

    def test_emit_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("try.finally_return", 0) > 0


class TestTryFinallyTerminates:
    # A returning finally overrides the try body's return: the captured
    # value decl gets [[maybe_unused]] and the trailing return is suppressed.
    SRC = (
        "def override(n: int) -> int:\n"
        "    try:\n"
        "        if n > 0:\n"
        "            return n * 10\n"
        "        print(n)\n"
        "    finally:\n"
        "        return -1\n"
        "print(override(2))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        t = _fn(thir, "override").body[0]
        assert isinstance(t, THIRTry) and t.finally_terminates
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_maybe_unused_and_suppressed_return(self):
        cpp = _cpp(self.SRC, thir=True)
        assert ("[[maybe_unused]] ::tpy::BigInt __tpy_ret_0 = "
                "((n) * (::tpy::BigInt(10)));") in cpp
        assert "return __tpy_ret_0;" not in cpp
        # No rethrow after a terminating finally in the catch arm.
        body = cpp[cpp.index("::tpy::BigInt override"):]
        assert "throw;" not in body

    def test_witnesses(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("try.finally_terminates", 0) > 0
        w2 = _emit_witnesses(self.SRC)
        assert w2.get("try.chain_terminated", 0) > 0


class TestTryBodyTerminates:
    SRC = (
        "def term_body() -> int:\n"
        "    try:\n"
        "        return 4\n"
        "    finally:\n"
        "        print(7)\n"
        "print(term_body())\n"
    )

    def test_normal_path_copy_elided(self):
        thir = _lower_ctx(self.SRC)
        t = _fn(thir, "term_body").body[0]
        assert isinstance(t, THIRTry) and t.body_terminates
        # One copy on the return path, one in the catch arm -- no trailing
        # normal-path copy.
        cpp = _cpp(self.SRC, thir=True)
        assert cpp.count("std::cout << 7") == 2
        assert cpp == _cpp(self.SRC, thir=False)

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("try.body_terminates", 0) > 0


class TestTryLoopExit:
    SRC = (
        "def loop_exit(k: int) -> int:\n"
        "    total = 0\n"
        "    for i in range(k):\n"
        "        try:\n"
        "            if i == 1:\n"
        "                break\n"
        "            if i == 2:\n"
        "                continue\n"
        "            total = total + i\n"
        "        finally:\n"
        "            total = total + 10\n"
        "    return total\n"
        "print(loop_exit(4))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "loop_exit") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_break_reemits_finally(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("::tpy::BigInt loop_exit"):]
        brk = body.index("break;")
        # The finally copy precedes the break inside the frame.
        assert (body.rindex("total = ((total) + (::tpy::BigInt(10)));", 0, brk)
                > body.index("try {"))

    def test_emit_witnesses(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("try.finally_loop_exit", 0) > 0


class TestTryNestedAndMixed:
    SRC = (
        "class CM:\n"
        "    n: int\n"
        "    def __init__(self, n: int) -> None:\n"
        "        self.n = n\n"
        "    def __enter__(self) -> int:\n"
        "        return self.n\n"
        "    def __exit__(self, t: None, v: None, tb: None) -> None:\n"
        "        pass\n"
        "def nested() -> None:\n"
        "    try:\n"
        "        try:\n"
        "            print(1)\n"
        "        finally:\n"
        "            print(2)\n"
        "    finally:\n"
        "        print(3)\n"
        "def mixed(n: int) -> int:\n"
        "    with CM(n) as x:\n"
        "        try:\n"
        "            if x > 3:\n"
        "                return x\n"
        "            print(x)\n"
        "        finally:\n"
        "            print(0)\n"
        "    return -5\n"
        "nested()\n"
        "print(mixed(5))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "nested") is not None
        assert _fn(thir, "mixed") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_mixed_return_walks_both_frames(self):
        # The return inside try-inside-with re-emits the finally body, then
        # the with's __exit__, then returns the temp.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("::tpy::BigInt mixed"):]
        i_tmp = body.index("__tpy_ret_0 = x;")
        i_fin = body.index("std::cout << 0", i_tmp)
        i_exit = body.index("__exit__", i_fin)
        assert body.index("return __tpy_ret_0;") > i_exit

    def test_emit_witnesses_both_frame_kinds(self):
        w = _emit_witnesses(self.SRC)
        assert w.get("try.finally_return", 0) > 0
        assert w.get("with.finally_return", 0) > 0


_BOOM = (
    "class AppError(Exception):\n"
    "    code: int\n"
    "    def __init__(self, code: int) -> None:\n"
    "        self.code = code\n"
    "def boom(n: int) -> int:\n"
    "    if n < 0:\n"
    "        raise ValueError(\"negative\")\n"
    "    if n == 0:\n"
    "        raise AppError(7)\n"
    "    return n * 2\n"
)


class TestThrowTier:
    SRC = (
        _BOOM
        + "def catch_multi(n: int) -> int:\n"
        + "    try:\n"
        + "        v = boom(n)\n"
        + "        print(v)\n"
        + "        return v\n"
        + "    except ValueError:\n"
        + "        return -1\n"
        + "    except AppError as e:\n"
        + "        print(e.code)\n"
        + "        return -2\n"
        + "def catch_bare(n: int) -> int:\n"
        + "    try:\n"
        + "        return boom(n)\n"
        + "    except:\n"
        + "        return -9\n"
        + "print(catch_multi(3))\n"
        + "print(catch_multi(-1))\n"
        + "print(catch_multi(0))\n"
        + "print(catch_bare(-2))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "boom") is not None
        assert _fn(thir, "catch_multi") is not None
        assert _fn(thir, "catch_bare") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_catch_arms(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "} catch (const ::tpy::ValueError&) {" in cpp
        assert "catch (const AppError& e) {" in cpp
        assert "} catch (...) {" in cpp
        # The raise ctor forms.
        assert 'throw ::tpy::ValueError("negative");' in cpp
        assert "throw AppError(::tpy::BigInt(7));" in cpp

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        t = _fn(thir, "catch_multi").body[0]
        assert isinstance(t, THIRTry) and t.tier == "throw"
        assert len(t.handlers) == 2
        assert t.handlers[1].binding == "e"
        r = _fn(thir, "boom").body[0].then_body[0]
        assert isinstance(r, THIRRaise) and r.cpp_type == "::tpy::ValueError"

    def test_witnesses(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("try.throw_tier", 0) > 0
        assert w.get("try.multi_handler", 0) > 0
        assert w.get("try.bare_except", 0) > 0
        assert w.get("try.binding", 0) > 0
        assert w.get("raise.ctor", 0) > 0


class TestThrowTierElseFinally:
    SRC = (
        _BOOM
        + "def with_else(n: int) -> int:\n"
        + "    r = 0\n"
        + "    try:\n"
        + "        r = boom(n)\n"
        + "    except ValueError:\n"
        + "        r = -1\n"
        + "    else:\n"
        + "        print(r)\n"
        + "    return r\n"
        + "def with_finally(n: int) -> int:\n"
        + "    try:\n"
        + "        return boom(n)\n"
        + "    except ValueError:\n"
        + "        return -1\n"
        + "    finally:\n"
        + "        print(n)\n"
        + "def reraise(n: int) -> int:\n"
        + "    try:\n"
        + "        return boom(n)\n"
        + "    except ValueError:\n"
        + "        if n == -5:\n"
        + "            raise\n"
        + "        return -1\n"
        + "print(with_else(5))\n"
        + "print(with_finally(6))\n"
        + "print(reraise(2))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        for name in ("with_else", "with_finally", "reraise"):
            assert _fn(thir, name) is not None, name
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_else_label_and_goto(self):
        # The else label draws from the module-cumulative try_except_counter
        # sink; each handler jumps past the else body.
        cpp = _cpp(self.SRC, thir=True)
        assert "goto __after_else_1;" in cpp
        assert "__after_else_1:;" in cpp
        assert "// else:" in cpp

    def test_except_finally_wraps(self):
        # The whole try/except sits inside the finally frame's try; the
        # handler's return re-emits the finally body before returning.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("::tpy::BigInt with_finally"):
                   cpp.index("::tpy::BigInt reraise")]
        assert body.index("try {") < body.index("} catch (const ::tpy::ValueError&) {")
        # Finally copies: try-body return path, handler return path,
        # catch(...) rethrow path; the whole statement terminates, so no
        # normal-path copy.
        assert body.count("std::cout << n") == 3

    def test_bare_reraise(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("::tpy::BigInt reraise"):]
        assert "throw;" in body

    def test_witnesses(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("try.else", 0) > 0
        assert w.get("try.except_finally", 0) > 0
        assert w.get("raise.bare", 0) > 0


class TestRaiseTerminatedFinally:
    # A raise-ending finally: terminates, so the catch arm's rethrow and the
    # exit-site trailing statements are suppressed.
    SRC = (
        "def f(n: int) -> int:\n"
        "    try:\n"
        "        if n > 0:\n"
        "            return n\n"
        "        print(n)\n"
        "    finally:\n"
        "        if n == 0:\n"
        "            print(0)\n"
        "        raise ValueError(\"always\")\n"
        "try:\n"
        "    f(1)\n"
        "except ValueError:\n"
        "    print(\"caught\")\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        t = _fn(thir, "f").body[0]
        assert isinstance(t, THIRTry) and t.finally_terminates
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_suppressed_rethrow_and_return(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("::tpy::BigInt f"):cpp.index("void __tpy_init")]
        assert "throw;" not in body
        assert "[[maybe_unused]]" in body
        assert "return __tpy_ret_0;" not in body


class TestTerminatingFinallyLoopExit:
    # break through a RETURN-terminating finally: the chain's re-emitted copy
    # ends control flow, so the trailing `break;` is suppressed (the same arm
    # covers `continue`; both route through _emit_loop_exit).
    SRC = (
        "def f(k: int) -> int:\n"
        "    for i in range(k):\n"
        "        try:\n"
        "            if i == 1:\n"
        "                break\n"
        "            print(i)\n"
        "        finally:\n"
        "            return i * 10\n"
        "    return -1\n"
        "print(f(3))\n"
    )

    def test_break_suppressed_after_terminating_chain(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "f") is not None
        cpp = _cpp(self.SRC, thir=True)
        assert "break;" not in cpp
        assert cpp == _cpp(self.SRC, thir=False)


class TestNestedThrowTier:
    # A throw-tier try inside another try's body: label/counter allocation and
    # finally-frame stacking compose.
    SRC = (
        _BOOM
        + "def f(n: int) -> int:\n"
        + "    try:\n"
        + "        try:\n"
        + "            return boom(n)\n"
        + "        except ValueError:\n"
        + "            print(\"inner\")\n"
        + "            return -1\n"
        + "    finally:\n"
        + "        print(\"outer-fin\")\n"
        + "print(f(3))\n"
        + "print(f(-1))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        fn = _fn(thir, "f")
        assert fn is not None
        outer = fn.body[0]
        assert isinstance(outer, THIRTry) and outer.tier == "finally_only"
        inner = outer.try_body[0]
        assert isinstance(inner, THIRTry) and inner.tier == "throw"
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)


class TestTryGateRejections:
    def _rejected(self, src: str, name: str) -> bool:
        thir = _lower_ctx(src)
        return _fn(thir, name) is None

    def test_return_tier_rejected(self):
        # A ReturnException handler classifies the try as the return tier
        # (goto dispatch around the @error_return call) -- parked.
        src = (
            "from tpy import error_return, ReturnException\n"
            "class NotFound(Exception, ReturnException):\n"
            "    pass\n"
            "@error_return(NotFound)\n"
            "def find(n: int) -> int:\n"
            "    if n < 0:\n"
            "        raise NotFound\n"
            "    return n\n"
            "def f(n: int) -> int:\n"
            "    try:\n"
            "        return find(n)\n"
            "    except NotFound:\n"
            "        return -1\n"
            "print(f(1))\n"
        )
        assert self._rejected(src, "f")

    def test_expr_raise_rejected(self):
        # `raise e` -> `e.__raise__()` + deref chain: a deferred row.
        src = (
            _BOOM
            + "def f(n: int) -> int:\n"
            + "    try:\n"
            + "        return boom(n)\n"
            + "    except ValueError as e:\n"
            + "        raise e\n"
            + "try:\n"
            + "    print(f(1))\n"
            + "except ValueError:\n"
            + "    print(\"caught\")\n"
        )
        assert self._rejected(src, "f")

    def test_nonvalue_hoist_rejected(self):
        src = (
            "def f() -> None:\n"
            "    try:\n"
            "        xs = [1, 2]\n"
            "        print(len(xs))\n"
            "    finally:\n"
            "        print(0)\n"
            "f()\n"
        )
        assert self._rejected(src, "f")

    def test_fresh_hoist_in_branch_rejected(self):
        src = (
            "def f(n: int) -> None:\n"
            "    if n > 0:\n"
            "        try:\n"
            "            y = 1\n"
            "            print(y)\n"
            "        finally:\n"
            "            print(0)\n"
            "f(1)\n"
        )
        assert self._rejected(src, "f")

    def test_fresh_hoist_in_loop_rejected(self):
        src = (
            "def f(k: int) -> None:\n"
            "    for i in range(k):\n"
            "        try:\n"
            "            y = i\n"
            "            print(y)\n"
            "        finally:\n"
            "            print(0)\n"
            "f(2)\n"
        )
        assert self._rejected(src, "f")

    def test_native_global_hoist_collision_rejected(self):
        # Sema treats the unadorned assign as a local first-declare and
        # hoists it, but the AST emit skips the predecl (native_global_names)
        # and renames the assign to the C++ global -- a shape the slice does
        # not reproduce.
        src = (
            "from tpy.extern import native_global\n"
            "from tpy import Int32\n"
            "gval: Int32 = native_global(\"g_val\", binding=\"C\")\n"
            "def f() -> None:\n"
            "    try:\n"
            "        gval = 5\n"
            "        print(gval)\n"
            "    finally:\n"
            "        print(0)\n"
            "f()\n"
        )
        assert self._rejected(src, "f")
