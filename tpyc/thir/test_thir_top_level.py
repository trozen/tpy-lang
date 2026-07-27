"""Module-init (`__tpy_init`) lowering: the global variable model, the
import chain, and the shapes that keep the whole top level on the AST path.

Every routing pin here asserts on `ctx.thir_top_level` rather than on emitted
text: a fallback emits byte-identical AST, so a text-only assertion passes
either way.
"""

from __future__ import annotations

from .testutil import _assert_byte_identical, _top_level

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

    def test_lvalue_source_rejects(self):
        # `ys = xs` is the address-of catch-all (`ys = &(*xs);`), not a slot.
        self._rejects(PRELUDE + (
            "xs: list[Int32] = [1, 2]\n"
            "ys: list[Int32] = xs\n"
            "print(len(ys))\n"
        ))

    def test_second_rvalue_write_rejects(self):
        # The second write REUSES the slot allocated at the first
        # (`xs = &(__global_slot_1 = {...})`), a render this slice omits.
        self._rejects(PRELUDE + (
            "xs: list[Int32] = [1, 2]\n"
            "print(len(xs))\n"
            "xs = [3, 4, 5]\n"
            "print(len(xs))\n"
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
            "xs: list[Int32] = [1, 2]\n"
            "ys: list[Int32] = xs\n"
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

    def test_optional_record_slot_rejects(self):
        src = PRELUDE + (
            "class P:\n"
            "    a: Int32\n"
            "    def __init__(self, a: Int32) -> None:\n"
            "        self.a = a\n"
            "\n"
            "q: P | None = P(1)\n"
            "if q is not None:\n"
            "    print(q.a)\n"
        )
        top, _w, fallback = _top_level(src)
        assert top is None
        assert [k for k in fallback if k.startswith("top_level:")], fallback
