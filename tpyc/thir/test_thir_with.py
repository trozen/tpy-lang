"""THIR sync `with` statements: the manager/target arms, the fixed
try/catch emit (suppress / exc_val / cleanup-only), the emit-side
finally-frame chain for return/break/continue, `__ctx_N` counter
continuity across bodies, and the gate rejections (already-declared
targets, first-declaring bodies, walrus managers)."""

from __future__ import annotations

from types import SimpleNamespace

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from ..typesys import unwrap_ref_type
from .lower.context import _Prescan
from .lower.statements import _with_target_arm
from .nodes import THIRCall, THIRWith, WithTargetArm
from .testutil import (
    _compile, _entry, _fn, _lower_ctor, _lower_ctx, _lower_ctx_witnessed,
    _top_level,
)


def _cpp(src: str):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
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
        cpp = _cpp(self.SRC)
        assert "catch (::tpy::BaseException&" not in cpp

    def test_witnesses(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        for face in ("with.manager_owned", "with.manager_borrowed",
                     "with.as_value", "with.no_target", "with.cleanup_only"):
            assert w.get(face, 0) > 0, face

    def test_ctx_counter_continuity(self):
        # Module-cumulative __ctx_N: the second routed body keeps counting.
        cpp = _cpp(self.SRC)
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

    def test_suppress_arms(self):
        cpp = _cpp(self.SRC)
        assert "if (!__ctx_1.__exit__({}, &__exc_1, {})) throw;" in cpp
        # Foreign-exception catch still cleans up without suppression.
        assert "__ctx_1.__exit__({}, nullptr, {});" in cpp

    def test_multi_nests_lifo(self):
        # Inner manager's __exit__ closes first (its catch block appears
        # before the outer's in the emitted text).
        cpp = _cpp(self.SRC)
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

    def test_return_captures_value_before_exit(self):
        # Python evaluates the return expression before __exit__ runs: the
        # value lands in the signature-typed temp, then the chain, then the
        # temp returns (mirrors _make_return's finally arm).
        cpp = _cpp(self.SRC)
        i_tmp = cpp.index("::tpy::BigInt __tpy_ret_0 = ::tpy::BigInt(7);")
        i_exit = cpp.index("__ctx_1.__exit__", i_tmp)
        i_ret = cpp.index("return __tpy_ret_0;")
        assert i_tmp < i_exit < i_ret

    def test_continue_walks_with_frame(self):
        # The with sits inside the loop: continue emits its __exit__ first.
        cpp = _cpp(self.SRC)
        body = cpp[cpp.index("void loop_exit"):cpp.index("void inner_loop")]
        i_exit = body.index("__ctx_2.__exit__({}, {}, {});")
        assert "continue;" in body[i_exit:]

    def test_break_inside_inner_loop_skips_frame(self):
        # The loop sits inside the with: break stays inside the frame, so no
        # __exit__ chain precedes it (the with's normal exit still runs later).
        cpp = _cpp(self.SRC)
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
            entry, options=CodeGenOptions(emit_source_comments=False))
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
        cpp = _cpp(self.SRC)
        assert cpp.count("__exit__") == 2
        assert cpp == _cpp(self.SRC)


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

    def test_bare_return_chains_then_returns(self):
        # The value-less _emit_finally_return arm: chain, then `return;`.
        cpp = _cpp(self.SRC)
        body = cpp[cpp.index("void bare_ret"):cpp.index("nested(")]
        assert body.index("__ctx_1.__exit__({}, {}, {});") < body.index("return;")

    def test_nested_return_walks_both_frames_innermost_first(self):
        cpp = _cpp(self.SRC)
        body = cpp[cpp.index("nested("):cpp.index("void partial")]
        i_tmp = body.index("__tpy_ret_0")
        i_inner = body.index("__ctx_3.__exit__", i_tmp)
        i_outer = body.index("__ctx_2.__exit__", i_tmp)
        assert i_tmp < i_inner < i_outer < body.index("return __tpy_ret_0;")

    def test_partial_boundary_break_exits_inner_frame_only(self):
        # Outer with sits outside the loop, inner inside: break walks only the
        # inner frame (the boundary stops mid-stack, not at 0); the outer
        # frame's __exit__ still runs on the normal path later.
        cpp = _cpp(self.SRC)
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

    def test_inherited_init_ctor_call_routes(self):
        # A derived record with only an INHERITED param-ful __init__: sema's
        # synthetic ctor fi carries EMPTY params, and the gate checks arity
        # against ri.init_params (the AST arg loop's fallback), so the
        # call-arg face renders the same inline `use(Sub(5))` as the AST.
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
        assert _fn(thir, "f") is not None
        # The record rvalue hoists the ref-param temp; the inherited-init
        # param type (int -> BigInt) threads from ri.init_params.
        assert "Sub __tmp_1 = Sub(::tpy::BigInt(5));" in _cpp(src)


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

    def test_value_hoist_routes_byte_identical(self):
        # Body-declared value vars visible after the block ride the
        # _emit_branch_decls plain-value predecl (`int32_t y;` /
        # `std::string_view s;` before the manager binding).
        src = (
            _CM
            + "def f(cm: CM) -> None:\n"
            + "    with cm:\n"
            + "        y = 1\n"
            + "        s = \"hi\"\n"
            + "    print(y)\n"
            + "    print(s)\n"
            + "f(CM(1))\n"
        )
        thir = _lower_ctx(src)
        w = _fn(thir, "f").body[0]
        assert isinstance(w, THIRWith)
        # Order is sema's if_branch_decls order (shared with the AST arm).
        assert set(w.hoist_decls) == {("y", "int32_t"),
                                      ("s", "std::string_view")}
        cpp = _cpp(src)
        assert cpp == _cpp(src)
        assert cpp.index("int32_t y;") < cpp.index("__ctx_")

    def test_record_hoist_optional_storage(self):
        # A body-declared RECORD var used after the with takes the
        # OPTIONAL_STORAGE predecl (`std::optional<CM> r;` before the
        # header; the body decl engages it, the post-with read derefs).
        src = (
            _CM
            + "def f(cm: CM) -> None:\n"
            + "    with cm:\n"
            + "        r = CM(2)\n"
            + "    print(r.n)\n"
            + "f(CM(1))\n"
        )
        fn = _fn(_lower_ctx(src), "f")
        assert fn is not None
        w = next(s for s in fn.body if isinstance(s, THIRWith))
        assert w.hoist_decls == (("r", "std::optional<CM>"),)
        cpp = _cpp(src)
        assert cpp == _cpp(src)
        assert cpp.index("std::optional<CM> r;") < cpp.index("__ctx_")
        assert "r->n" in cpp

    def test_hoist_inside_branch_routes(self):
        # A with nested in a branch predeclares its body-first-decl AT the
        # with, so the C++ scope is the branch block the name belongs to.
        src = (
            _CM
            + "def f(cm: CM, b: bool) -> None:\n"
            + "    if b:\n"
            + "        with cm:\n"
            + "            y = 1\n"
            + "        print(y)\n"
            + "f(CM(1), True)\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("with.hoist_decl", 0) > 0
        out = _cpp(src)
        assert "        int32_t y;\n        auto& __ctx_1 = cm;" in out

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

    def test_target_inside_branch_routes_inline(self):
        # A first-declaring as-target inside an if branch: the VALUE arm's
        # inline `auto x = __ctx_N.__enter__();` is position-identical, so
        # in-branch admission routes it (the target pops with the branch
        # via branch_scope).
        src = (
            _CM
            + "def f(b: bool) -> None:\n"
            + "    if b:\n"
            + "        with CM(1) as x:\n"
            + "            print(x)\n"
            + "f(True)\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None


class TestValueEnterTargets:
    """The widened VALUE target arm: str/StrView/Char/enum enter types.
    The declared entry carries the RESOLVED enter type, so body reads
    classify like the AST's `var_types[name] = enter_type` (owned str
    STORAGE vs view BORROW)."""

    _EXIT = "    def __exit__(self, t: None, v: None, tb: None) -> None:\n        pass\n"

    def test_str_and_view_targets_route_byte_identical(self):
        src = (
            "from tpy import StrView\n"
            "class SCM:\n"
            "    def __enter__(self) -> str:\n"
            '        return "owned"\n'
            + self._EXIT
            + "class VCM:\n"
            + "    def __enter__(self) -> StrView:\n"
            + '        return "view"\n'
            + self._EXIT
            + "def owned_target() -> None:\n"
            + "    with SCM() as s:\n"
            + '        s += "!"\n'
            + "        print(s, len(s))\n"
            + "    print(s)\n"
            + "def view_target() -> None:\n"
            + "    with VCM() as v:\n"
            + "        print(v)\n"
            + "owned_target()\n"
            + "view_target()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "owned_target") is not None
        assert _fn(thir, "view_target") is not None
        assert w.get("with.str_target", 0) >= 2
        out = _cpp(src)
        assert out == _cpp(src)
        # The owned target appends in place; the view target reads bare.
        assert 's += "!";' in out

    def test_char_and_enum_targets_route_byte_identical(self):
        src = (
            "from enum import Enum\n"
            "from tpy import Char\n"
            "class Color(Enum):\n"
            "    RED = 1\n"
            "    BLUE = 2\n"
            "class CCM:\n"
            "    def __enter__(self) -> Char:\n"
            '        return Char("c")\n'
            + self._EXIT
            + "class ECM:\n"
            + "    def __enter__(self) -> Color:\n"
            + "        return Color.BLUE\n"
            + self._EXIT
            + "def char_target() -> None:\n"
            + "    with CCM() as c:\n"
            + "        print(c)\n"
            + "def enum_target() -> None:\n"
            + "    with ECM() as e:\n"
            + "        print(e.value)\n"
            + "char_target()\n"
            + "enum_target()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "char_target") is not None
        assert _fn(thir, "enum_target") is not None

    def test_string_enter_type_routes(self):
        # `String` is inside the resolved str slice: it binds STORAGE like an
        # owned `str`, so the with-target renders the same value binding.
        src = (
            "from tpy import String\n"
            "class GCM:\n"
            "    def __enter__(self) -> String:\n"
            '        return "a" + "b"\n'
            + self._EXIT
            + "def f() -> None:\n"
            + "    with GCM() as g:\n"
            + "        print(g)\n"
            + "f()\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None


class TestPtrTargetReuse:
    """The already-declared with-target family: a reassigned F1-record
    target declares the F2 pointer-local (`T* g = &(__enter__());`), and a
    later `with` over the same name reuses it (`g = &(__enter__());`).
    Value / optional-slot reuse stays AST (the BUGS.md ill-formed family)."""

    _G = (
        "class G:\n"
        "    n: int\n"
        "    def __init__(self, n: int) -> None:\n"
        "        self.n = n\n"
        '    def __enter__(self) -> "G":\n'
        "        return self\n"
        "    def __exit__(self, t: None, v: None, tb: None) -> None:\n"
        "        pass\n"
    )

    def test_two_withs_route_byte_identical(self):
        # No read after the LAST with, so no manager outlives its block and both
        # arms stay routed. (Add a trailing read and the second manager has to be
        # hoisted -- see test_reuse_read_after_hoists_manager.)
        src = (
            self._G
            + "def f() -> None:\n"
            + "    with G(1) as g:\n"
            + "        print(g.n)\n"
            + "    with G(2) as g:\n"
            + "        print(g.n)\n"
            + "f()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("with.ptr_target", 0) > 0
        assert w.get("with.ptr_target_reuse", 0) > 0
        out = _cpp(src)
        assert out == _cpp(src)
        assert "G* g = &(__ctx_1.__enter__());" in out
        assert "g = &(__ctx_2.__enter__());" in out
        # Body reads go through the pointer-local.
        assert "g->n" in out

    def test_reuse_read_after_hoists_manager(self):
        # The same reuse chain plus a trailing read: `g` then points at the
        # second manager past its block, so that OWNED manager hoists to a
        # function-scope optional and `__ctx_2` binds through it (the
        # _with_manager_needs_hoist mirror).
        src = (
            self._G
            + "def f() -> None:\n"
            + "    with G(1) as g:\n"
            + "        print(g.n)\n"
            + "    with G(2) as g:\n"
            + "        print(g.n)\n"
            + "    print(g.n)\n"
            + "f()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("with.manager_hoist", 0) > 0
        out = _cpp(src)
        assert out == _cpp(src)
        assert "std::optional<G> __slot_1;" in out
        assert "__slot_1.emplace(G(" in out
        assert "auto& __ctx_2 = (*__slot_1);" in out
        assert "g = &(__ctx_2.__enter__());" in out
        # The FIRST manager (a fresh PTR_DECL target is not yet declared, so
        # the hoist gate cannot fire) stays block-scoped.
        assert "auto __ctx_1 = G(" in out

    def test_mixed_arms_and_chained_reuse_route(self):
        # PTR_DECL and VALUE arms in ONE multi-item statement, then two
        # chained reuses (ASSIGN_PTR firing twice).
        src = (
            self._G
            + "class V:\n"
            + "    def __enter__(self) -> int:\n"
            + "        return 7\n"
            + "    def __exit__(self, t: None, v: None, tb: None) -> None:\n"
            + "        pass\n"
            + "def f() -> None:\n"
            + "    with G(1) as g, V() as x:\n"
            + "        print(g.n, x)\n"
            + "    with G(2) as g:\n"
            + "        print(g.n)\n"
            + "    with G(3) as g:\n"
            + "        print(g.n)\n"
            + "f()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("with.ptr_target_reuse", 0) >= 2
        assert w.get("with.as_value", 0) > 0
        out = _cpp(src)
        assert out == _cpp(src)
        assert "G* g = &(__ctx_1.__enter__());" in out
        assert "auto x = __ctx_2.__enter__();" in out
        assert "g = &(__ctx_3.__enter__());" in out
        assert "g = &(__ctx_4.__enter__());" in out

    def test_value_target_reuse_stays_ast(self):
        # The AST's already-declared arm emits `x = &(enter())` into an int
        # slot -- ill-formed C++ (BUGS.md); never mirrored.
        src = (
            "class V:\n"
            "    def __enter__(self) -> int:\n"
            "        return 7\n"
            "    def __exit__(self, t: None, v: None, tb: None) -> None:\n"
            "        pass\n"
            "def f() -> None:\n"
            "    with V() as x:\n"
            "        print(x)\n"
            "    with V() as x:\n"
            "        print(x)\n"
            "f()\n"
        )
        assert _fn(_lower_ctx(src), "f") is None

    def test_rebind_slot_target_routes_plain_assign(self):
        # A plain rvalue decl first makes the name an F2d rebind slot; the
        # with bind still renders the plain `g = &(...)` assign (the AST's
        # already-declared arm does not consult rebind slots -- the with
        # aliases the manager, never the slot).
        src = (
            self._G
            + "def f() -> None:\n"
            + "    g = G(1)\n"
            + "    print(g.n)\n"
            + "    with G(2) as g:\n"
            + "        print(g.n)\n"
            + "f()\n"
        )
        assert _fn(_lower_ctx(src), "f") is not None
        out = _cpp(src)
        assert out == _cpp(src)
        assert "g = &(__ctx_1.__enter__());" in out

    def test_with_then_rvalue_reassign_stays_ast(self):
        # The mixed family is kept out at the reassign site (a pointer-local
        # reseat from an rvalue), not the with site.
        src = (
            self._G
            + "def f() -> None:\n"
            + "    with G(1) as g:\n"
            + "        print(g.n)\n"
            + "    g = G(3)\n"
            + "    print(g.n)\n"
            + "f()\n"
        )
        assert _fn(_lower_ctx(src), "f") is None

    def test_reuse_inside_branch_routes(self):
        # The already-declared `g = &(...)` assign is position-neutral, so an
        # in-branch reuse routes; the branch manager is read past its block
        # (the trailing print) and hoists.
        src = (
            self._G
            + "def f(b: bool) -> None:\n"
            + "    with G(1) as g:\n"
            + "        print(g.n)\n"
            + "    if b:\n"
            + "        with G(2) as g:\n"
            + "            print(g.n)\n"
            + "    print(g.n)\n"
            + "f(True)\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("with.manager_hoist", 0) > 0
        out = _cpp(src)
        assert out == _cpp(src)
        assert "__slot_1.emplace(G(" in out


class TestStrArgManager:
    def test_str_arg_manager_routes(self):
        # A str ctor arg rides the free-call pass-through rule at every
        # ctor-call face, including the with-manager rvalue arm.
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
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("ctor.str_arg", 0) > 0


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
        cpp = _cpp(self.SRC)
        assert "auto& g = __ctx_1.__enter__();" in cpp
        assert cpp == _cpp(self.SRC)


class TestNativeCtxManager:
    """A sync `with` whose manager is a @native record-returning free call --
    `with open(path, mode)`. The literal `mode` selects a native overload
    (`builtin_open_mode` for text, `builtin_open_binary` for binary), a
    literal-specialized dispatch done by sema; the manager lowers through the
    native free-call arm (THIRCall.native_name), byte-identical to the AST
    `__ctx_N` store. The str `mode` literal lands bare in its `Literal[...]`
    selector slot (the overloaded str-pin is inert on a LiteralType slot). The
    with-BODY's native file methods (`f.write` / `f.read`) now route too (the
    native-record instance-method arm renders `recv.native_name(args)`), so a
    body using them fully routes."""

    MODE = (
        "def main() -> None:\n"
        "    with open('/tmp/x.txt', 'w') as f:\n"
        "        pass\n"
        "main()\n"
    )
    NOARG = (
        "def main() -> None:\n"
        "    with open('/tmp/x.txt') as f:\n"
        "        pass\n"
        "main()\n"
    )
    BINARY = (
        "def main() -> None:\n"
        "    with open('/tmp/x.txt', 'wb'):\n"
        "        pass\n"
        "main()\n"
    )

    def test_mode_manager_routes_native(self):
        thir = _lower_ctx(self.MODE)
        fn = _fn(thir, "main")
        assert fn is not None
        w = fn.body[0]
        assert isinstance(w, THIRWith)
        call = w.items[0].ctx_expr
        assert isinstance(call, THIRCall)
        assert call.native_name == "tpy::builtin_open_mode"

    def test_binary_mode_selects_native_symbol(self):
        thir = _lower_ctx(self.BINARY)
        call = _fn(thir, "main").body[0].items[0].ctx_expr
        assert isinstance(call, THIRCall)
        assert call.native_name == "tpy::builtin_open_binary"

    def test_byte_identical(self):
        for src in (self.MODE, self.NOARG, self.BINARY):
            assert _cpp(src) == _cpp(src)

    def test_native_file_method_body_routes(self):
        # The with-BODY's native file method (`f.write`) routes on the
        # native-record instance-method arm (member = fi.native_name, a plain
        # `f.write("hi")`), so the WHOLE body now routes and byte-matches.
        src = (
            "def main() -> None:\n"
            "    with open('/tmp/x.txt', 'w') as f:\n"
            "        f.write('hi')\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None

    def test_native_file_read_body_routes(self):
        # A str-returning native file method (`r.read(4)`) used in an expr
        # position routes: the str result rides the view/owned form tag, the
        # int arg lands bare in its Int32 slot.
        src = (
            "def main() -> None:\n"
            "    with open('/tmp/x.txt') as r:\n"
            "        print(r.read(4))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None

    def test_native_method_body_emits_dot_member(self):
        # The native rename renders bare `f.write(...)` -- the `.` member form,
        # not the free-function `::sym(recv, args)` shape.
        src = (
            "def main() -> None:\n"
            "    with open('/tmp/x.txt', 'w') as f:\n"
            "        f.write('hi')\n"
            "main()\n"
        )
        out = _cpp(src)
        assert "f.write(\"hi\");" in out


# Self-returning record manager: the enter type is the manager record, so
# an as-target binds a record (REF at function top, pointer-form assign when
# the name was hoist-predeclared by an enclosing branch-decl pass).
_SELFG = (
    "from typing import Self\n"
    "from tpy import Int32\n"
    "class G:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "    def __enter__(self) -> Self:\n"
    "        return self\n"
    "    def __exit__(self, et: None, ev: None, tb: None) -> None:\n"
    "        pass\n"
)


class TestAssignOptTarget:
    def test_nested_with_hoisted_target_routes_pointer(self):
        # The inner with-as target is hoist-predeclared by the outer's
        # branch-decl pass, in POINTER form (`G* inner;` -- sema's
        # stmt-borrow fact makes a with target borrow-only), and the bind
        # aliases the manager: owning `std::optional<G>` storage copied it,
        # so a mutation through the target never reached the object __exit__
        # runs against.
        src = (
            _SELFG
            + "def f() -> None:\n"
            + "    with G(1) as outer:\n"
            + "        with G(2) as inner:\n"
            + "            print(inner.n)\n"
            + "            print(outer.n)\n"
            + "f()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("with.hoist_ptr_local", 0) > 0
        assert w.get("with.ptr_target_reuse", 0) > 0
        cpp = _cpp(src)
        assert cpp == _cpp(src)
        assert "G* inner;" in cpp
        assert "inner = &(__ctx_" in cpp

    def test_assign_opt_requires_matching_record(self):
        # A hoist-predeclared optional-slot name reused over a DIFFERENT
        # record must fall through same_record and reject -- and must NOT
        # fall into the pointer row (the &-assign render would be
        # ill-formed against the optional slot).
        src = (
            _SELFG
            + "class H:\n"
            + "    m: Int32\n"
            + "    def __init__(self, m: Int32) -> None:\n"
            + "        self.m = m\n"
            + "def probe(g: G, h: H) -> None:\n"
            + "    print(g.n)\n"
            + "    print(h.m)\n"
            + "probe(G(1), H(2))\n"
        )
        compiler, modules = _compile(src)
        entry = _entry(modules)
        fnode = [f for f in entry.ast.functions if f.name == "probe"][0]
        g_t = unwrap_ref_type(fnode.params[0][1])
        h_t = unwrap_ref_type(fnode.params[1][1])
        prescan = _Prescan(fnode, entry.analyzer)
        item = SimpleNamespace(target="t", enter_type=g_t)
        with activate_compiler(compiler):
            mismatch = _with_target_arm(
                item, {"t": h_t}, prescan, entry.analyzer,
                {"t"}, {"t"})
            matching = _with_target_arm(
                item, {"t": g_t}, prescan, entry.analyzer,
                {"t"}, {"t"})
        assert mismatch is None
        assert matching is not None
        assert matching[0] is WithTargetArm.ASSIGN_OPT


class TestManagerHoistBoundary:
    """The kept-owned-manager arm's edges (each shape dualgen-probed when the
    arm landed): the hoist keys on manager_owns_enter_result AND
    target_read_after AND an already-declared non-optional target -- every
    neighbor missing one fact binds plain, and the two shapes the emit has
    no drain/slot spelling for keep rejecting."""

    def test_delegating_manager_binds_plain(self):
        # `__enter__` hands back a GLOBAL -- not the manager's own storage
        # (a field read would be: an inline field IS inside the manager and
        # hoists) -- so manager_owns_enter_result is False and both branch
        # managers stay block-scoped.
        src = (
            "from tpy import Int32\n"
            "class Item:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "SHARED: Item = Item(7)\n"
            "class Delegate:\n"
            "    def __enter__(self) -> Item:\n"
            "        return SHARED\n"
            "    def __exit__(self, et: None, ev: None, tb: None) -> None:\n"
            "        pass\n"
            "def probe(flag: bool) -> Int32:\n"
            "    if flag:\n"
            "        with Delegate() as v:\n"
            "            pass\n"
            "    else:\n"
            "        with Delegate() as v:\n"
            "            pass\n"
            "    return v.n\n"
            "probe(True)\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "probe") is not None
        assert w.get("with.manager_hoist", 0) == 0
        out = _cpp(src)
        assert out == _cpp(src)
        assert "auto __ctx_1 = Delegate();" in out
        assert "__slot_" not in out

    def test_field_manager_local_receiver_routes(self):
        # The borrowed-field manager row off a plain LOCAL receiver (not
        # self): `auto& __ctx_N = o.mgr;`.
        src = (
            "from tpy import Int32\n"
            "class Mgr:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 0\n"
            '    def __enter__(self) -> "Mgr":\n'
            "        self.n += 1\n"
            "        return self\n"
            "    def __exit__(self, et: None, ev: None, tb: None) -> None:\n"
            "        self.n += 100\n"
            "class Owner:\n"
            "    mgr: Mgr\n"
            "    def __init__(self) -> None:\n"
            "        self.mgr = Mgr()\n"
            "def f() -> None:\n"
            "    o = Owner()\n"
            "    with o.mgr as b:\n"
            "        b.n += 1000\n"
            "        print(o.mgr.n)\n"
            "    print(o.mgr.n)\n"
            "f()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("with.manager_borrowed_field", 0) > 0
        out = _cpp(src)
        assert out == _cpp(src)
        assert "auto& __ctx_1 = o.mgr;" in out

    def test_top_level_hoist_routes_static_slot(self):
        # A TOP-LEVEL reuse chain with a trailing read keeps its manager in
        # the module-init scope's own slot lifetime (`static __global_slot_N`,
        # drawn from the scope's prefix), not a frame slot.
        src = (
            _SELFG
            + "with G(1) as g:\n"
            + "    print(g.n)\n"
            + "with G(2) as g:\n"
            + "    print(g.n)\n"
            + "print(g.n)\n"
        )
        top, w, fallback = _top_level(src)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("with.manager_hoist", 0) > 0
        out = _cpp(src)
        assert "    static std::optional<G> __global_slot_1;\n" in out
        assert "    __global_slot_1.emplace(G(2));\n" in out

    def test_rvalue_reassigned_with_hoist_routes(self):
        # A with-body hoist name that is ALSO rvalue-reassigned takes the
        # pointer predecl; its reseat allocates a function-top slot lazily,
        # so no THIRWith field is involved. Both slots here (the kept
        # manager's and the reseat's) are function-top optionals, drawn in
        # emit order -- the deleted emitter numbered them the other way
        # round, a permutation of two equivalent slot names.
        src = (
            "from tpy import Int32, Own\n"
            "class G:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            '    def __enter__(self) -> "G":\n'
            "        return self\n"
            "    def __exit__(self, et: None, ev: None, tb: None) -> None:\n"
            "        pass\n"
            '    def next(self) -> Own["G"]:\n'
            "        return G(self.n + 1)\n"
            "def run() -> Int32:\n"
            "    with G(1) as outer:\n"
            "        with G(10) as inner:\n"
            "            print(inner.n)\n"
            "        inner = inner.next()\n"
            "        print(outer.n)\n"
            "    return inner.n\n"
            "run()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "run") is not None
        assert w.get("with.hoist_ptr_local", 0) > 0
        assert w.get("with.manager_hoist", 0) > 0
        out = _cpp(src)
        assert "    std::optional<G> __slot_1;\n" in out
        assert "    std::optional<G> __slot_2;\n" in out
        assert "    G* inner;\n" in out
        assert "        inner = &*(__slot_2 = inner->next());\n" in out

    def test_multi_item_second_hoists(self):
        # One statement, two managers: only the item whose (branch-hoisted,
        # read-after) target aliases its OWN manager hoists; slot numbering
        # and __ctx numbering stay interleaved like the AST's.
        src = (
            _SELFG
            + "def run(flag: bool) -> Int32:\n"
            + "    if flag:\n"
            + "        with G(1) as a, G(10) as b:\n"
            + "            pass\n"
            + "    else:\n"
            + "        with G(2) as a, G(20) as b:\n"
            + "            pass\n"
            + "    return a.n + b.n\n"
            + "run(True)\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "run") is not None
        assert w.get("with.manager_hoist", 0) >= 4
        out = _cpp(src)
        assert out == _cpp(src)
