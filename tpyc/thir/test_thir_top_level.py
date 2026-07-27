"""Module-init (`__tpy_init`) lowering: the global variable model, the
import chain, and the shapes that keep the whole top level on the AST path.

Every routing pin here asserts on `ctx.thir_top_level` rather than on emitted
text: a fallback emits byte-identical AST, so a text-only assertion passes
either way.
"""

from __future__ import annotations

import pytest

from ..diagnostics import SemanticError
from .testutil import (
    _assert_byte_identical, _fn, _lower_ctx, _top_level,
)

PRELUDE = "from tpy import Int32\n"


class TestScalarGlobals:
    """A value global is pre-declared at namespace scope, so its write is a
    plain assign -- the ordinary reassign arm, no slot."""

    SRC = PRELUDE + (
        "x = 0\n"
        "print(x)\n"
        "x = 5\n"
        "print(x)\n"
    )

    def test_routes(self):
        top, _w, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestGlobalSlot:
    """A non-value global's initializing write allocates the static slot:
    `static std::vector<int32_t> __global_slot_1 = {1, 2};` +
    `xs = &__global_slot_1;`. Reads deref through the pointer arms."""

    SRC = PRELUDE + (
        "xs: list[Int32] = [1, 2, 3]\n"
        "print(len(xs))\n"
        "print(xs[0])\n"
    )

    def test_routes_and_witnesses(self):
        top, w, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("top_level.global_slot", 0) >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_record_global_slot(self):
        src = PRELUDE + (
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "\n"
            "p: P = P(7)\n"
            "print(p.a)\n"
        )
        top, w, _f = _top_level(src)
        assert top is not None
        assert w.get("top_level.global_slot", 0) >= 1
        _assert_byte_identical(src)


class TestGlobalSlotBoundaries:
    """The renders `_gen_pointer_local_rebind` reaches through OTHER branches
    must keep the whole module init on the AST path."""

    def _rejects(self, src: str) -> None:
        top, _w, fallback = _top_level(src)
        assert top is None
        assert [k for k in fallback if k.startswith("top_level:")], fallback

    def test_deep_lvalue_source_rejects(self):
        # Only a NAME lvalue source is mirrored: `g = other.field` would have
        # to re-derive the AST's own `gen_expr` render under `&(...)` at a
        # position with no committed witness.
        self._rejects(PRELUDE + (
            "class Holder:\n"
            "    xs: list[Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.xs = [1, 2]\n"
            "\n"
            "h: Holder = Holder()\n"
            "ys: list[Int32] = h.xs\n"
            "print(len(ys))\n"
        ))

    def test_branch_write_rejects(self):
        # A slot declared inside a branch is block-scoped where the AST gives
        # it namespace lifetime.
        self._rejects(PRELUDE + (
            "flag = True\n"
            "xs: list[Int32] = [0]\n"
            "if flag:\n"
            "    xs = [1, 2]\n"
            "print(len(xs))\n"
        ))


class TestGlobalSlotSiblingWrites:
    """The other three module-scope faces of `_gen_pointer_local_rebind`:
    slot reuse on a later rvalue write, the `None` source, and a
    pointer-slot-global source (already a `T*`, so it copies bare)."""

    def test_second_rvalue_write_reuses_the_slot(self):
        src = PRELUDE + (
            "xs: list[Int32] = [1, 2]\n"
            "print(len(xs))\n"
            "xs = [3, 4, 5]\n"
            "print(len(xs))\n"
        )
        top, w, fallback = _top_level(src)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("top_level.global_slot_reuse", 0) >= 1
        _assert_byte_identical(src)

    def test_none_source_assigns_nullptr(self):
        src = PRELUDE + (
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "\n"
            "q: P | None = None\n"
            "print(q is None)\n"
            "q = P(1)\n"
            "print(q is None)\n"
        )
        top, w, fallback = _top_level(src)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("top_level.global_null", 0) >= 1
        # The None TEST reads the slot pointer raw, not the value deref.
        assert w.get("isnone.global_slot", 0) >= 1
        _assert_byte_identical(src)

    def test_pointer_global_source_copies_bare(self):
        # The write copies the raw pointer, so the two globals ALIAS -- a
        # mutation through one is visible through the other, as in CPython.
        # A deep-copy regression would change the emitted line, which the
        # byte-identity assert below pins.
        src = PRELUDE + (
            "xs: list[Int32] = [1, 2]\n"
            "ys: list[Int32] = xs\n"
            "xs.append(3)\n"
            "print(len(ys))\n"
        )
        top, w, fallback = _top_level(src)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("top_level.global_ptr_copy", 0) >= 1
        _assert_byte_identical(src)

    def test_optional_record_global_from_a_ctor_rvalue(self):
        # The general rvalue arm at an Optional[record] slot: the slot
        # carries the INNER spelling (`static P __global_slot_N = P(7);`).
        src = PRELUDE + (
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "\n"
            "q: P | None = P(7)\n"
            "print(q is None)\n"
        )
        top, w, fallback = _top_level(src)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("top_level.global_slot", 0) >= 1
        _assert_byte_identical(src)

    def test_borrow_returning_optional_call_passes_through(self):
        # A BORROW-returning ptr-repr Optional result IS the `T*` the slot
        # holds, so the write is a bare pass-through -- no slot, no lift.
        src = PRELUDE + (
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "\n"
            "def find(ps: list[P], a: Int32) -> P | None:\n"
            "    for p in ps:\n"
            "        if p.a == a:\n"
            "            return p\n"
            "    return None\n"
            "\n"
            "ps: list[P] = [P(1), P(2)]\n"
            "hit: P | None = find(ps, 2)\n"
            "print(hit is None)\n"
        )
        top, w, fallback = _top_level(src)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("top_level.global_opt_passthrough", 0) >= 1
        assert w.get("call.ptr_opt_passthrough", 0) >= 1
        _assert_byte_identical(src)

    def test_borrow_returning_optional_method_passes_through(self):
        # The METHOD twin of the row above; both call gates carry it.
        src = PRELUDE + (
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "class Box:\n"
            "    p: P\n"
            "    def __init__(self) -> None:\n"
            "        self.p = P(1)\n"
            "    def get(self, a: Int32) -> P | None:\n"
            "        if self.p.a == a:\n"
            "            return self.p\n"
            "        return None\n"
            "\n"
            "b: Box = Box()\n"
            "hit: P | None = b.get(1)\n"
            "print(hit is None)\n"
        )
        top, w, fallback = _top_level(src)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("method.ptr_opt_passthrough", 0) >= 1
        _assert_byte_identical(src)

    def test_own_returning_optional_call_still_rejects(self):
        # An `Own[Optional[T]]` callee returns STORAGE form, which takes the
        # slot + `optional_to_ptr` lift -- a different render.
        src = PRELUDE + (
            "from tpy import Own\n"
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "\n"
            "def make(a: Int32) -> Own[P] | None:\n"
            "    if a < 0:\n"
            "        return None\n"
            "    return P(a)\n"
            "\n"
            "hit: P | None = make(2)\n"
            "print(hit is None)\n"
        )
        top, _w, fallback = _top_level(src)
        assert top is None
        assert [k for k in fallback if k.startswith("top_level:")], fallback

    def test_optional_global_truthiness_reads_the_raw_pointer(self):
        src = PRELUDE + (
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "\n"
            "q: P | None = None\n"
            "assert q\n"
        )
        top, w, _fallback = _top_level(src)
        assert top is not None
        assert w.get("truthy.global_slot", 0) >= 1
        _assert_byte_identical(src)


class TestAnnotationOnlyGlobals:
    """A global's annotation-only decl emits NOTHING in module init -- the
    name already exists at namespace scope (`_gen_var_decl_code`'s
    `global_declared_vars` no-init arm returns None)."""

    SRC = PRELUDE + (
        "class P:\n"
        "    a: Int32\n"
        "    def __init__(self, a: Int32) -> None:\n"
        "        self.a = a\n"
        "\n"
        "# leading comment on the scalar\n"
        "i: Int32\n"
        "s: str\n"
        "q: P | None\n"
        "i = 3\n"
        "s = \"x\"\n"
        "q = P(4)\n"
        "print(i)\n"
        "print(s)\n"
    )

    def test_routes_and_witnesses(self):
        top, w, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("top_level.global_no_init", 0) >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_function_local_annotation_only_decl_still_declares(self):
        # The arm is GLOBAL-scoped: a function-local annotation-only decl
        # still default-constructs its slot.
        src = PRELUDE + (
            "def f() -> Int32:\n"
            "    n: Int32\n"
            "    n = 2\n"
            "    return n\n"
            "print(f())\n"
        )
        _assert_byte_identical(src)


class TestLoopVarShadowingAGlobal:
    """A module-scope loop variable that is ALSO a global: the AST emits a
    fresh loop-scoped binding shadowing it (`was_declared` changes only
    post-loop bookkeeping, never the render), so the shadow is admitted --
    but only when the global's type IS the element type."""

    SRC = PRELUDE + (
        "i: Int32\n"
        "total: Int32 = 0\n"
        "for i in range(0, 5):\n"
        "    total += i\n"
        "xs: list[Int32] = [1, 2, 3]\n"
        "for i in xs:\n"
        "    total += i\n"
        "print(total)\n"
    )

    def test_routes(self):
        top, _w, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_type_mismatched_shadow_is_a_sema_error(self):
        # The element-type guard in the admission is DEFENSIVE: sema already
        # rejects a rebinding loop whose element type differs (and any
        # reference-typed rebind), so the shadow can never retype a later
        # read. Pinned here so the guard is not mistaken for dead weight.
        src = PRELUDE + (
            "i: str = \"\"\n"
            "xs: list[Int32] = [1, 2, 3]\n"
            "for i in xs:\n"
            "    print(i)\n"
        )
        with pytest.raises(SemanticError):
            _top_level(src)

    def test_function_local_shadow_rides_the_hoist_arm(self):
        # There is no writable "local shadow rejects" boundary: sema marks a
        # loop var that rebinds an existing LOCAL as `hoist_loop_var`, which
        # `_for_loop_shape_ok` answers from `allow_hoist` before the shadow
        # check runs. Only a module GLOBAL reaches the new admission (it is
        # pre-seeded into `declared` without being sema-hoisted), so the
        # `top_level` scoping is belt-and-braces. Pinned so the scoping is
        # not mistaken for dead weight -- and so a sema change that stops
        # hoisting local rebinds shows up here as a divergence.
        src = PRELUDE + (
            "def f(xs: list[Int32]) -> Int32:\n"
            "    i: Int32 = 0\n"
            "    print(i)\n"
            "    total: Int32 = 0\n"
            "    for i in xs:\n"
            "        total += i\n"
            "    return total\n"
            "print(f([1, 2]))\n"
        )
        _assert_byte_identical(src)


class TestLoopVarShadowingAnImportedGlobal:
    """A loop variable may shadow only a BARE-reading module global. An
    imported (or native-linkage) global renders a fixed qualified name, so
    admitting the shadow would make the body read the SHADOWED global
    instead of the loop binding -- a wrong-VALUE miscompile."""

    LIB = "from tpy import Int32\ncounter: Int32 = 7\n"
    SRC = PRELUDE + (
        "from gmod import counter\n"
        "total: Int32 = 0\n"
        "for counter in range(0, 3):\n"
        "    total += counter\n"
        "print(total)\n"
    )

    def _dirs(self, tmp_path):
        (tmp_path / "gmod.py").write_text(self.LIB)
        return [tmp_path]

    def test_rejects(self, tmp_path):
        top, _w, fallback = _top_level(self.SRC,
                                       extra_lib_dirs=self._dirs(tmp_path))
        assert top is None
        assert [k for k in fallback if k.startswith("top_level:")], fallback

    def test_byte_identical(self, tmp_path):
        _assert_byte_identical(self.SRC,
                               extra_lib_dirs=self._dirs(tmp_path))


class TestNativeGlobalDecl:
    """A `native_global(...)` binding emits no line in module init -- the
    definition lives in C/C++, so only leading trivia survives."""

    SRC = (
        "# tpy: include(\"native_types.hpp\")\n"
        "from tpy import Int32\n"
        "from tpy.extern import native_global\n"
        "# leading comment\n"
        "tick: Int32 = native_global(binding=\"C\")\n"
        "print(tick)\n"
    )

    def test_routes_and_witnesses(self):
        top, w, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("top_level.native_global_skip", 0) >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestImportedGlobalReads:
    """A global this module IMPORTS but never redefines reads through its
    fixed qualified spelling at module scope, exactly as it does inside a
    function body -- the shared `_seed_imported_globals` keeps the two
    entries from drifting."""

    LIB = "from tpy import Int32\nPUBLIC_VAL: Int32 = 7\n"
    SRC = "from lib import PUBLIC_VAL\nprint(PUBLIC_VAL)\n"

    def _dirs(self, tmp_path):
        (tmp_path / "lib.py").write_text(self.LIB)
        return [tmp_path]

    def test_routes(self, tmp_path):
        top, _w, fallback = _top_level(self.SRC,
                                       extra_lib_dirs=self._dirs(tmp_path))
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]

    def test_byte_identical(self, tmp_path):
        _assert_byte_identical(self.SRC,
                               extra_lib_dirs=self._dirs(tmp_path))

    def test_locally_redefined_import_stays_unseeded(self, tmp_path):
        # A name this module REDEFINES is never seeded: the local definition
        # wins from its decl line on, and the reads before it ride
        # `pre_decl_import_cpp`. Byte-identity is the whole contract here --
        # seeding it would spell the qualified name past the redefinition.
        src = ("from lib import PUBLIC_VAL\n"
               "print(PUBLIC_VAL)\n"
               "PUBLIC_VAL = 9\n"
               "print(PUBLIC_VAL)\n")
        _assert_byte_identical(src, extra_lib_dirs=self._dirs(tmp_path))


class TestCharGlobal:
    """A `Char` global's initializing write is a "reassign" here only because
    the name is pre-seeded; the AST renders the same char literal it would at
    a first decl. No boundary pin: both shapes the gate still rejects (a
    multi-char literal, a local Char reassign from a str literal) are SEMA
    errors, so the remaining checks are defensive only."""

    SRC = (
        "from tpy import Char\n"
        "a: Char = \"a\"\n"
        "b: Char = \"b\"\n"
        "print(a)\n"
        "print(b)\n"
    )

    def test_routes(self):
        top, _w, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestImportChain:
    """An import's `__tpy_init()` chain is resolved at lowering by the same
    `module_init_targets` the AST arm calls; a chainless import keeps only its
    source comment."""

    def test_stdlib_import_produces_no_statement_at_all(self):
        # An implicit-stdlib import never reaches `top_level_stmts`, so there
        # is no import statement to chain OR to comment -- only the synthetic
        # `__name__` Final decl, which the final-skip arm drops.
        src = "from tpy import Int32\nn: Int32 = 1\nprint(n)\n"
        top, w, _f = _top_level(src)
        assert top is not None
        assert not any(getattr(s, "calls", ()) for s in top.body)
        assert w.get("top_level.final_skip", 0) >= 1
        _assert_byte_identical(src)


class TestFinalGlobals:
    """A `Final` global is defined at namespace scope -- `__tpy_init` emits
    nothing for it, not even its source comment."""

    SRC = (
        "from typing import Final\n"
        "from tpy import Int32\n"
        "LIMIT: Final[Int32] = 10\n"
        "print(LIMIT)\n"
    )

    def test_routes_and_witnesses(self):
        top, w, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("top_level.final_skip", 0) >= 1

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)


class TestPointerSlotGlobalConsumers:
    """A pointer-slot global derefs `(*g)` at value positions -- but every
    consumer that binds the raw `T*` must NOT deref. Each shape here broke
    exactly once; the last one has no corpus witness at all (the corpus
    byte-diff cannot catch it, and a fallback emits byte-identical AST, so
    only a routing pin can), which is why they are pinned individually."""

    RECORDS = (
        "from tpy import Int32\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "class Holder:\n"
        "    v: P | None\n"
        "    def __init__(self) -> None:\n"
        "        self.v = None\n"
    )

    def _routes(self, src: str) -> None:
        top, _w, fallback = _top_level(src)
        assert top is not None, fallback
        assert not [k for k in fallback if k.startswith("top_level:")], fallback
        _assert_byte_identical(src)

    def test_storage_optional_field_write_takes_the_pointer(self):
        # `h.v = g` lifts through `ptr_to_optional(g)`, which takes the
        # POINTER -- a value-position deref would pass `(*g)`.
        self._routes(self.RECORDS + (
            "g: P = P(1)\n"
            "h: Holder = Holder()\n"
            "h.v = g\n"
        ))

    def test_field_read_through_a_slot_receiver_uses_the_arrow(self):
        # The receiver reaches its member via `->`; a deref here would
        # compose into `(*h)->v`.
        self._routes(self.RECORDS + (
            "def take(p: P | None) -> Int32:\n"
            "    if p:\n"
            "        return p.x\n"
            "    return 0\n"
            "h: Holder = Holder()\n"
            "print(take(h.v))\n"
        ))

    def test_optional_ptr_arg_slot_passes_bare(self):
        # The slot binds the pointer itself (`optptr.pass`).
        self._routes(self.RECORDS + (
            "def take(p: P | None) -> Int32:\n"
            "    if p:\n"
            "        return p.x\n"
            "    return 0\n"
            "g: P = P(2)\n"
            "print(take(g))\n"
        ))

    def test_cpp_template_receiver_derefs(self):
        # The mirror image: a cpp_template / @native free function consumes
        # the receiver as an ARGUMENT, so there it DOES deref.
        self._routes(
            "from tpy import Int32\n"
            "xs: list[Int32] = [1, 2, 3]\n"
            "print(xs.__len__())\n"
        )

    def test_value_reads_still_deref(self):
        self._routes(
            "from tpy import Int32\n"
            "xs: list[Int32] = [1, 2]\n"
            "print(len(xs))\n"
            "print(xs[0])\n"
        )


class TestModuleTopLevelField:
    """`THIRModule.top_level` is populated only when a caller supplies the
    global-type map -- production seeds `ctx.thir_top_level` at the seam
    instead, so this opt-in path needs its own exercise."""

    def test_lower_module_populates_top_level(self):
        from ..compilation_context import activate_compiler
        from .lower import lower_module
        from .testutil import _compile, _entry

        src = "from tpy import Int32\nn: Int32 = 1\nprint(n)\n"
        compiler, modules = _compile(src)
        entry = _entry(modules)
        gt = {"n": entry.analyzer.ctx.global_scope.lookup("n")}
        with activate_compiler(compiler):
            plain = lower_module(entry.ast, entry.analyzer)
            seeded = lower_module(entry.ast, entry.analyzer, global_types=gt)
        assert plain.top_level is None
        assert seeded.top_level is not None
        assert seeded.top_level.name == "__tpy_init"


class TestDumpThir:
    """`--dump-thir` must SHOW the module-init body. It walked callables and
    constructors only, so a fully-routed top level rendered as nothing at all
    -- which reads as "top-level does not lower", the opposite of the truth --
    and the `THIRImportInit` dump arm was unreachable from the CLI. Node-level
    dump coverage (`test_dump.py`) cannot catch that: it checks every node has
    an arm, not that the driver reaches the node."""

    def _dump(self, src: str, extra_lib_dirs=None) -> str:
        from ..codegen_cpp.context import CodeGenOptions
        from .dump import dump_codegen_thir
        from .testutil import _compile, _entry

        compiler, modules = _compile(src, extra_lib_dirs)
        entry = _entry(modules)
        ctx = compiler.collect_thir(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        return dump_codegen_thir(entry.ast, entry.analyzer, ctx,
                                 compiler.thir_reject_by_node)

    def test_routed_top_level_is_dumped(self):
        out = self._dump(PRELUDE + (
            "xs: list[Int32] = [1, 2, 3]\n"
            "print(len(xs))\n"
        ))
        assert "fn __tpy_init() -> None:" in out
        assert "ptr_decl[global_rvalue] %xs" in out

    def test_unrouted_top_level_names_its_reason(self):
        # Silence was the original defect -- an un-routed top level must say
        # so, and say why, exactly like an un-routed callable does.
        out = self._dump(PRELUDE + (
            "class Holder:\n"
            "    xs: list[Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.xs = [1, 2]\n"
            "\n"
            "h: Holder = Holder()\n"
            "ys: list[Int32] = h.xs\n"
            "print(len(ys))\n"
        ))
        assert "top-level __tpy_init: <fell back to AST" in out
        assert "top_level.global_slot_shape" in out

    def test_import_init_arm_is_reachable(self, tmp_path):
        # The chain render only exists inside the module-init body, so before
        # top-level was dumped this arm could not be reached from the CLI.
        (tmp_path / "helper.py").write_text(
            "from tpy import Int32\nn: Int32 = 1\n")
        out = self._dump("import helper\nprint(helper.n)\n",
                         extra_lib_dirs=[tmp_path])
        assert "import-init [" in out
        assert "helper" in out

    def test_module_without_top_level_is_unchanged(self):
        out = self._dump(PRELUDE + "def f() -> Int32:\n    return 1\n")
        assert "__tpy_init" in out  # the synthetic __name__ decl still counts
        assert "fn f(" in out


class TestSlotAllocatingShapesReject:
    """Anything else that would allocate a `__slot_N` needs `static` +
    the `__global_slot` prefix at this scope; only GLOBAL_RVALUE is wired,
    so the rest reject the whole body rather than emit a block-scoped slot."""

    def test_dyn_protocol_global_rejects(self):
        # A @dynamic protocol global takes the adapter-slot `.emplace` rebind
        # (`_gen_dynamic_protocol_rebind`), a different render.
        src = PRELUDE + (
            "from typing import Protocol\n"
            "from tpy import dynamic\n"
            "@dynamic\n"
            "class Pet(Protocol):\n"
            "    def speak(self) -> Int32: ...\n"
            "class Dog:\n"
            "    def speak(self) -> Int32:\n"
            "        return 1\n"
            "\n"
            "pet: Pet = Dog()\n"
            "print(pet.speak())\n"
        )
        top, _w, fallback = _top_level(src)
        assert top is None
        assert [k for k in fallback if k.startswith("top_level:")], fallback


class TestEmptyContainerInstantiation:
    """The zero-arg `list()` / `dict()` / `set()` render is the default ctor
    spelled off `call_type` -- it never reads the ELEMENT type, so the arm
    does not key on the element-typed `_storage_call_ret` verdict (which
    exists for the downstream READ shapes). A record-element container
    therefore routes; anything that DOES read the element keeps that
    verdict."""

    SRC = PRELUDE + (
        "class P:\n"
        "    a: Int32\n"
        "    def __init__(self, a: Int32) -> None:\n"
        "        self.a = a\n"
        "\n"
        "ps: list[P] = list()\n"
        "print(len(ps))\n"
    )

    def test_record_element_empty_instantiation_routes(self):
        top, _w, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]

    def test_byte_identical(self):
        _assert_byte_identical(self.SRC)

    def test_argument_carrying_instantiation_still_keys_on_the_element(self):
        # `list(it)` DOES render over its element slots, so it keeps the
        # `_storage_call_ret` verdict -- a record element still rejects.
        src = (
            "from tpy import Int32, Own\n"
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "\n"
            "def src_of() -> Own[list[P]]:\n"
            "    return [P(1)]\n"
            "\n"
            "ps: list[P] = list(src_of())\n"
            "print(len(ps))\n"
        )
        top, _w, fallback = _top_level(src)
        assert top is None
        assert [k for k in fallback if k.startswith("top_level:")], fallback
