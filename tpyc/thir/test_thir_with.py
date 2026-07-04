"""THIR sync `with` statements: the manager/target arms, the fixed
try/catch emit (suppress / exc_val / cleanup-only), the emit-side
finally-frame chain for return/break/continue, `__ctx_N` counter
continuity across bodies, and the gate rejections (already-declared
targets, first-declaring bodies, str-arg managers)."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import THIRWith, WithTargetArm
from .testutil import (
    _compile, _entry, _fn, _lower_ctor, _lower_ctx, _lower_ctx_witnessed,
)


def _cpp(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return cpp


# Cleanup-only manager (typed None exc params: takes_exc_val=False,
# can_suppress=False -- the elided-catch shape) with a value enter.
_CM = (
    "class CM:\n"
    "    n: int\n"
    "    def __init__(self, n: int) -> None:\n"
    "        self.n = n\n"
    "    def __enter__(self) -> int:\n"
    "        return self.n\n"
    "    def __exit__(self, t: None, v: None, tb: None) -> None:\n"
    "        pass\n"
)

# Suppressing manager (bool __exit__, untyped exc params: takes_exc_val).
_SUP = (
    "class Sup:\n"
    "    def __enter__(self) -> int:\n"
    "        return 1\n"
    "    def __exit__(self, t, v, tb) -> bool:\n"
    "        return True\n"
)


class TestWithBasic:
    SRC = (
        _CM
        + "def owned() -> None:\n"
        + "    with CM(3) as x:\n"
        + "        print(x)\n"
        + "def borrowed(cm: CM) -> None:\n"
        + "    with cm:\n"
        + "        print(1)\n"
        + "owned()\n"
        + "borrowed(CM(2))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "owned") is not None
        assert _fn(thir, "borrowed") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_item_facts(self):
        thir = _lower_ctx(self.SRC)
        w = _fn(thir, "owned").body[0]
        assert isinstance(w, THIRWith)
        item = w.items[0]
        assert not item.manager_borrowed
        assert item.target_arm is WithTargetArm.VALUE
        assert not item.can_suppress and not item.takes_exc_val
        b = _fn(thir, "borrowed").body[0]
        assert b.items[0].manager_borrowed
        assert b.items[0].target_arm is WithTargetArm.NONE

    def test_cleanup_only_elides_tpy_catch(self):
        # can_suppress=False + takes_exc_val=False -> single catch(...).
        cpp = _cpp(self.SRC, thir=True)
        assert "catch (::tpy::BaseException&" not in cpp

    def test_witnesses(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        for face in ("with.manager_owned", "with.manager_borrowed",
                     "with.as_value", "with.no_target", "with.cleanup_only"):
            assert w.get(face, 0) > 0, face

    def test_ctx_counter_continuity(self):
        # Module-cumulative __ctx_N: the second routed body keeps counting.
        cpp = _cpp(self.SRC, thir=True)
        assert "__ctx_1" in cpp and "__ctx_2" in cpp


class TestWithSuppressAndMulti:
    SRC = (
        _SUP
        + "def sup() -> None:\n"
        + "    with Sup() as a:\n"
        + "        print(a)\n"
        + "def multi() -> None:\n"
        + "    with Sup() as a, Sup() as b:\n"
        + "        print(a + b)\n"
        + "sup()\n"
        + "multi()\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_suppress_arms(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "if (!__ctx_1.__exit__({}, &__exc_1, {})) throw;" in cpp
        # Foreign-exception catch still cleans up without suppression.
        assert "__ctx_1.__exit__({}, nullptr, {});" in cpp

    def test_multi_nests_lifo(self):
        # Inner manager's __exit__ closes first (its catch block appears
        # before the outer's in the emitted text).
        cpp = _cpp(self.SRC, thir=True)
        i3 = cpp.index("__ctx_3.__exit__")
        i2 = cpp.index("__ctx_2.__exit__")
        assert i3 < i2
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("with.multi", 0) > 0
        assert w.get("with.suppress", 0) > 0


class TestWithFinallyChain:
    # NB the loop-exit with is target-free: a `with ... as x` inside a loop
    # body is a branch-scope first-declare, outside the slice like any
    # loop-body var decl.
    SRC = (
        _CM
        + "def ret(cm: CM) -> int:\n"
        + "    with cm:\n"
        + "        return 7\n"
        + "def loop_exit(cm: CM) -> None:\n"
        + "    for i in range(3):\n"
        + "        with cm:\n"
        + "            if i == 1:\n"
        + "                continue\n"
        + "            print(i)\n"
        + "def inner_loop(cm: CM) -> None:\n"
        + "    with cm:\n"
        + "        for i in range(3):\n"
        + "            if i == 1:\n"
        + "                break\n"
        + "ret(CM(1))\n"
        + "loop_exit(CM(2))\n"
        + "inner_loop(CM(3))\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_return_captures_value_before_exit(self):
        # Python evaluates the return expression before __exit__ runs: the
        # value lands in the signature-typed temp, then the chain, then the
        # temp returns (mirrors _make_return's finally arm).
        cpp = _cpp(self.SRC, thir=True)
        i_tmp = cpp.index("::tpy::BigInt __tpy_ret_0 = ::tpy::BigInt(7);")
        i_exit = cpp.index("__ctx_1.__exit__", i_tmp)
        i_ret = cpp.index("return __tpy_ret_0;")
        assert i_tmp < i_exit < i_ret

    def test_continue_walks_with_frame(self):
        # The with sits inside the loop: continue emits its __exit__ first.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void loop_exit"):cpp.index("void inner_loop")]
        i_exit = body.index("__ctx_2.__exit__({}, {}, {});")
        assert "continue;" in body[i_exit:]

    def test_break_inside_inner_loop_skips_frame(self):
        # The loop sits inside the with: break stays inside the frame, so no
        # __exit__ chain precedes it (the with's normal exit still runs later).
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void inner_loop"):]
        brk = body.index("break;")
        assert "__exit__" not in body[body.index("if ((i == 1))"):brk]

    def test_witnesses(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        # Witness counts for the emit-side faces are recorded at code
        # generation, not lowering -- pin them via the generated-cpp path.
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        w2 = compiler._thir_face_witnesses
        assert w2.get("with.finally_return", 0) > 0
        assert w2.get("with.finally_loop_exit", 0) > 0


class TestWithBodyTerminates:
    SRC = (
        _CM
        + "def term(cm: CM) -> int:\n"
        + "    with cm:\n"
        + "        return 5\n"
        + "term(CM(1))\n"
    )

    def test_no_normal_exit_when_body_terminates(self):
        thir = _lower_ctx(self.SRC)
        w = _fn(thir, "term").body[0]
        assert isinstance(w, THIRWith) and w.body_terminates
        # The try block holds only the finally-chain return; the trailing
        # normal-path __exit__ is elided (one __exit__ on the return path,
        # one in the catch -- `term` is the only with in the module).
        cpp = _cpp(self.SRC, thir=True)
        assert cpp.count("__exit__") == 2
        assert cpp == _cpp(self.SRC, thir=False)


class TestWithFinallyChainMultiFrame:
    SRC = (
        _CM
        + "def bare_ret(cm: CM) -> None:\n"
        + "    with cm:\n"
        + "        return\n"
        + "def nested(a: CM, b: CM) -> int:\n"
        + "    with a:\n"
        + "        with b:\n"
        + "            return 7\n"
        + "def partial(a: CM, b: CM) -> None:\n"
        + "    with a:\n"
        + "        for i in range(3):\n"
        + "            with b:\n"
        + "                if i == 1:\n"
        + "                    break\n"
        + "                print(i)\n"
        + "bare_ret(CM(1))\n"
        + "print(nested(CM(1), CM(2)))\n"
        + "partial(CM(1), CM(2))\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_bare_return_chains_then_returns(self):
        # The value-less _emit_finally_return arm: chain, then `return;`.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void bare_ret"):cpp.index("nested(")]
        assert body.index("__ctx_1.__exit__({}, {}, {});") < body.index("return;")

    def test_nested_return_walks_both_frames_innermost_first(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("nested("):cpp.index("void partial")]
        i_tmp = body.index("__tpy_ret_0")
        i_inner = body.index("__ctx_3.__exit__", i_tmp)
        i_outer = body.index("__ctx_2.__exit__", i_tmp)
        assert i_tmp < i_inner < i_outer < body.index("return __tpy_ret_0;")

    def test_partial_boundary_break_exits_inner_frame_only(self):
        # Outer with sits outside the loop, inner inside: break walks only the
        # inner frame (the boundary stops mid-stack, not at 0); the outer
        # frame's __exit__ still runs on the normal path later.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void partial"):]
        guard = body.index("if ((i == 1))")
        chain = body[guard:body.index("break;", guard)]
        assert "__ctx_5.__exit__" in chain
        assert "__ctx_4.__exit__" not in chain


class TestWithInCtorBody:
    # A with inside __init__ reaches THIR through the shared ctor body
    # machinery (records.py wires the ctx-backed __ctx_N sink into the ctor
    # tail emit).
    SRC = (
        _CM
        + "class K:\n"
        + "    n: int\n"
        + "    def __init__(self, cm: CM) -> None:\n"
        + "        self.n = 0\n"
        + "        with cm:\n"
        + "            self.n = 5\n"
        + "K(CM(1))\n"
    )

    def test_ctor_with_routes_byte_identical(self):
        ctor = _lower_ctor(self.SRC, "K")
        assert ctor is not None
        assert any(isinstance(s, THIRWith) for s in ctor.body)
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)


class TestImplicitCtorCallSites:
    # The no-__init__ _ctor_shape_ok widening applies at every ctor-call
    # face, not just with managers: pin a non-manager site (a zero-arg
    # implicit-ctor rvalue as a record call arg).
    SRC = (
        "class P:\n"
        "    def ping(self) -> int:\n"
        "        return 1\n"
        "def use(p: P) -> int:\n"
        "    return p.ping()\n"
        "def f() -> int:\n"
        "    return use(P())\n"
        "print(f())\n"
    )

    def test_implicit_ctor_arg_routes_byte_identical(self):
        thir, w = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "f") is not None
        assert w.get("argtemp.record_rvalue", 0) > 0 or w.get("ctor.call", 0) > 0
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_inherited_init_ctor_call_stays_ast(self):
        # A derived record with only an INHERITED param-ful __init__ has no
        # OWN overloads; the widening must not admit its call. Today sema
        # attaches no synthetic ctor fi for it, so the fi-None gate rejects
        # first -- this pin keeps the smuggle shut if sema ever grows
        # inherited-init fis (the widening would then admit a NON-zero-arg
        # call whose AST render disagrees).
        src = (
            "class Base:\n"
            "    n: int\n"
            "    def __init__(self, n: int) -> None:\n"
            "        self.n = n\n"
            "class Sub(Base):\n"
            "    pass\n"
            "def use(s: Sub) -> int:\n"
            "    return s.n\n"
            "def f() -> int:\n"
            "    return use(Sub(5))\n"
            "print(f())\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestWithGateRejections:
    def test_already_declared_target_stays_ast(self):
        src = (
            _CM
            + "def f() -> None:\n"
            + "    x = 0\n"
            + "    with CM(1) as x:\n"
            + "        print(x)\n"
            + "f()\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_body_first_declare_stays_ast(self):
        # A body-declared var visible after the block needs the
        # _emit_branch_decls hoist -> whole function stays AST.
        src = (
            _CM
            + "def f(cm: CM) -> None:\n"
            + "    with cm:\n"
            + "        y = 1\n"
            + "    print(y)\n"
            + "f(CM(1))\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_str_arg_manager_stays_ast(self):
        # A str ctor arg is outside every ctor-call face's scalar-slot rule.
        src = (
            "class M:\n"
            "    name: str\n"
            "    def __init__(self, name: str) -> None:\n"
            "        self.name = name\n"
            "    def __enter__(self) -> int:\n"
            "        return 1\n"
            "    def __exit__(self, t: None, v: None, tb: None) -> None:\n"
            "        pass\n"
            "def f() -> None:\n"
            '    with M("a") as x:\n'
            "        print(x)\n"
            "f()\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_walrus_manager_stays_ast(self):
        # The temp-registering manager shape behind the BUGS.md pre-decl
        # flush-ordering bug -- gate-rejected, never mirrored.
        src = (
            _CM
            + "def f() -> None:\n"
            + "    with (m := CM(1)):\n"
            + "        print(m.n)\n"
            + "f()\n"
        )
        assert _fn(_lower_ctx(src), "f") is None

    def test_target_inside_branch_stays_ast(self):
        # A first-declaring as-target inside an if branch (the var-decl
        # branch rule).
        src = (
            _CM
            + "def f(b: bool) -> None:\n"
            + "    if b:\n"
            + "        with CM(1) as x:\n"
            + "            print(x)\n"
            + "f(True)\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestWithRefTarget:
    SRC = (
        "from typing import Self\n"
        "class G:\n"
        "    n: int\n"
        "    def __init__(self) -> None:\n"
        "        self.n = 0\n"
        "    def __enter__(self) -> Self:\n"
        "        self.n += 1\n"
        "        return self\n"
        "    def __exit__(self, t: None, v: None, tb: None) -> None:\n"
        "        self.n -= 1\n"
        "def f() -> None:\n"
        "    with G() as g:\n"
        "        print(g.n)\n"
        "f()\n"
    )

    def test_ref_arm_routes_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        fn = _fn(thir, "f")
        assert fn is not None
        w = fn.body[0]
        assert isinstance(w, THIRWith)
        assert w.items[0].target_arm is WithTargetArm.REF
        cpp = _cpp(self.SRC, thir=True)
        assert "auto& g = __ctx_1.__enter__();" in cpp
        assert cpp == _cpp(self.SRC, thir=False)
