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
        # An IF-scoped slot write keeps `static` on the AST (no namespace
        # push outside for-each bodies) -- an unmirrored flavor, fenced.
        # Only FOR-body writes route (TestGlobalSlotBranchWrites).
        self._rejects(PRELUDE + (
            "flag = True\n"
            "xs: list[Int32] = [0]\n"
            "if flag:\n"
            "    xs = [1, 2]\n"
            "print(len(xs))\n"
        ))


class TestStructuralProtocolGlobal:
    """`it = iter(d)` at module scope: the structural-protocol global's
    `static auto __global_slot_N = ::tpy::__iter__((*d));` + addr assign,
    slot REUSE on reassign (`it = &(__global_slot_N = ...);`), and the
    for-each over the global name (`auto& __src_N = (*it);` deref capture
    into the universal loop) -- inside a function body and at top level."""

    _SRC = (
        'd = {"a": 1, "b": 2}\n'
        "it = iter(d)\n"
        "def use_global_iter() -> None:\n"
        "    for k in it:\n"
        "        print(k)\n"
        "use_global_iter()\n"
        'd2 = {"x": 10}\n'
        "it = iter(d2)\n"
        "for k in it:\n"
        "    print(k)\n")

    def test_proto_global_slot_and_reuse_route(self):
        top, wit, fallback = _top_level(self._SRC)
        assert top is not None
        assert not fallback
        assert wit.get("top_level.global_slot_proto", 0) >= 2
        # The reuse half must actually fire (the second `it =` write).
        assert wit.get("top_level.global_slot_reuse", 0) >= 1
        _assert_byte_identical(self._SRC)

    def test_function_reads_proto_global(self):
        thir = _lower_ctx(self._SRC)
        assert _fn(thir, "use_global_iter") is not None

    def test_user_record_iter_source_stays_ast(self):
        # BOUNDARY (BUGS.md `iter(c)` self-copy family): a USER-record
        # source's `static auto` slot would copy when `__iter__` returns
        # self -- the broken oracle keeps falling back.
        src = (
            "from tpy import Int32\n"
            "from typing import Iterator\n"
            "class Cyc:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n        self.n = 0\n"
            "    def __iter__(self) -> Iterator[Int32]:\n"
            "        i = 0\n"
            "        while i < 2:\n"
            "            yield i\n"
            "            i += 1\n"
            "c = Cyc()\n"
            "it = iter(c)\n"
            "for v in it:\n"
            "    print(v)\n")
        top, _w, fallback = _top_level(src)
        assert top is None
        assert fallback.get("top_level:stmt.var_decl:"
                            "top_level.global_slot_protocol")
        _assert_byte_identical(src)


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


class TestDocstringTrivia:
    """A docstring emits no code and no source comment -- but it still owes its
    leading `#`-comment trivia, which the AST's gen_stmt emits before the
    None-code suppression. A module's `# tpy:` directive header is exactly that
    shape, and no user case in the corpus has one: the whole-corpus byte-diff
    was green while every stdlib module carrying a directive diverged."""

    SRC = (
        '# tpy: cpp_namespace("probe::ns")\n'
        '"""Module docstring."""\n'
        "from tpy import Int32\n"
        "n: Int32 = 1\n"
        "print(n)\n"
    )

    def test_routes_and_witnesses(self):
        top, w, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert w.get("stmt.trivia", 0) >= 1

    def test_byte_identical_with_comments(self):
        # Comments OFF cannot see this class at all -- the arm only differs in
        # trivia, so the pin has to run the corpus's comment setting.
        hpp_cpp = _assert_byte_identical(self.SRC, comments=True)
        assert '// # tpy: cpp_namespace("probe::ns")' in "".join(hpp_cpp)

    def test_docstring_keeps_no_source_comment_of_its_own(self):
        # The boundary: trivia rides through, the docstring's OWN source line
        # does not (`_gen_simple_stmt` returns None, suppressing it).
        hpp_cpp = "".join(_assert_byte_identical(self.SRC, comments=True))
        assert '// """Module docstring."""' not in hpp_cpp


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

    def test_dyn_protocol_global_routes_static_rebind(self):
        # A @dynamic protocol global takes the adapter-slot `.emplace`
        # rebind (`_gen_dynamic_protocol_rebind`); its DYN_PROTOCOL slot
        # hoist spells `static std::optional<T> __global_slot_N;` at this
        # scope, so the shape ROUTES (the one rebind-slot kind wired for
        # module scope; full render pinned in test_thir_wave_dynret).
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
        top, wit, fallback = _top_level(src)
        assert top is not None, fallback
        assert wit.get("top_level.global_dyn_rebind", 0) >= 1


class TestEmptyContainerInstantiation:
    """Neither instantiation form reads the ELEMENT type: the zero-arg render
    is the default ctor spelled off `call_type`, and the arg-carrying one is
    the ctor template with the whole container already substituted in. So
    neither keys on the element-typed `_storage_call_ret` verdict, which
    exists for the downstream READ shapes."""

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

    def test_argument_carrying_instantiation_is_element_blind_too(self):
        # The arg-carrying form does not read the element either: sema has
        # already substituted the whole container into the ctor template
        # (`::tpy::construct<std::vector<P>>({0})`), so a record element
        # renders what a scalar one does -- same as the empty form above.
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
        top, witnessed, fallback = _top_level(src)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert witnessed["call.instantiation_template"] >= 1
        _assert_byte_identical(src)


class TestGlobalContainerPrint:
    # A pointer-SLOT container global printed at top level rides the
    # hoisted-container print row (`ListPrinter((*nums))` -- the same
    # kind-keyed wrap over the slot deref).
    SRC = (
        "nums = list(range(5))\n"
        "print(nums)\n"
    )

    def test_routes_byte_identical(self):
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert "::tpy::ListPrinter((*nums))" in cpp

    def test_optional_container_global_stays_ast(self):
        # BOUNDARY: an Optional[container] global's whole-name print is a
        # DIFFERENT render (the null-safe optional print, not the
        # kind-keyed wrap over a bare deref -- a broken exclusion here
        # would deref a null slot). The OptionalType exclusion must keep
        # it out of the wrap row.
        from ..codegen_cpp import CodeGenOptions
        from .testutil import _assert_byte_identical, _compile, _entry
        src = (
            "from typing import Optional\n"
            "from tpy import Own\n"
            "def maybe(flag: bool) -> Own[list[int] | None]:\n"
            "    if flag:\n"
            "        return [1, 2]\n"
            "    return None\n"
            "xs = maybe(True)\n"
            "print(xs)\n"
        )
        _assert_byte_identical(src)
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=True,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        assert not compiler._thir_face_witnesses.get(
            "print.hoisted_container_arg")


class TestGlobalAddrLocal:
    """The address-of catch-all for a plain LOCAL lvalue at a global slot
    write -- the desugared top-level tuple unpack (`a = &(__unpack_0_0);`).
    A SUBCLASS source is NOT this arm: a pointer-slot GLOBAL source copies
    bare through `global_ptr_copy`, which is type-blind because the C++
    upcast is implicit."""

    def test_unpack_alias_routes(self):
        src = (
            "from tpy import Int32\n"
            "class Outer:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "a, b = Outer(1), Outer(2)\n"
            "print(a.n + b.n)\n")
        top, wit, _fb = _top_level(src)
        assert top is not None
        assert wit.get("top_level.global_addr_local", 0) >= 2
        _hpp, cpp = _assert_byte_identical(src)
        assert "a = &(__unpack_0_0);" in cpp

    def test_subclass_global_source_copies_bare(self):
        # `g: Base = s0` binds a SECOND name to `s0`, which hoists `s0` --
        # so this fixture pinned the whole top level as deferred while the
        # subclass write itself was never the reason. Both writes route:
        # the hoisted slot for `s0`, the bare pointer copy for `g`
        # (`Sub*` -> `Base*` upcasts implicitly).
        src = (
            "from tpy import Int32\n"
            "class Base:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "class Sub(Base):\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        super().__init__(n)\n"
            "s0 = Sub(1)\n"
            "g: Base = s0\n"
            "print(g.n)\n")
        _hpp, cpp = _assert_byte_identical(src)
        assert "g = s0;" in cpp
        top, wit, fb = _top_level(src)
        assert top is not None
        assert fb == {}
        assert wit.get("top_level.global_ptr_copy", 0) >= 1
        assert wit.get("top_level.global_hoist_slot", 0) >= 1


class TestGlobalSlotBranchWrites:
    """In-branch top-level global-slot writes: the first RVALUE write's
    in-place slot drops `static` (current_ns leaves global_ns inside the
    branch), a pointer-NAME source copies bare (`saved = p;`), and a
    branch-hoisted record local's slot hoists as
    `static __global_slot_N` (the RECORD_HOISTED module flavor). An
    rvalue-REASSIGNED hoisted local keeps the slot_alloc fence."""

    def test_loop_rvalue_and_ptr_copy_route(self):
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "x = None\n"
            "for i in range(0, 2):\n"
            "    x = P(i)\n"
            "if x is not None:\n"
            "    print(x.n)\n"
            "saved: P = P(0)\n"
            "for j in range(3):\n"
            "    p: P = P(j)\n"
            "    saved = p\n"
            "print(saved.n)\n")
        top, wit, _fb = _top_level(src)
        assert top is not None
        _hpp, cpp = _assert_byte_identical(src)
        # The in-loop first-rvalue slot has NO static.
        assert "        P __global_slot_" in cpp
        # The hoisted record local's slot is static at init top.
        assert "static std::optional<P> __global_slot_" in cpp
        assert "saved = p;" in cpp

    def test_rvalue_reassigned_hoisted_local_defers(self):
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "g: P = P(0)\n"
            "for i in range(2):\n"
            "    q: P = P(i)\n"
            "    q = P(i + 10)\n"
            "    g = q\n"
            "print(g.n)\n")
        _assert_byte_identical(src)
        top, _wit, fb = _top_level(src)
        assert top is None
        assert any("slot_alloc" in k for k in fb), fb


class TestGlobalSlotBranchBoundaries:
    """The for-body-only key: if/while-scoped first rvalue writes keep
    `static` on the AST (no namespace push there) and stay fenced; an
    IMPORTED pointer-global source stays out of the bare-copy row (the
    AST qualifies its spelling)."""

    _P = (
        "from tpy import Int32\n"
        "class P:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n")

    def test_if_scoped_rvalue_write_defers(self):
        src = self._P + (
            "flag = True\n"
            "g: P = P(0)\n"
            "if flag:\n"
            "    g = P(7)\n"
            "print(g.n)\n")
        _assert_byte_identical(src)
        top, _w, fb = _top_level(src)
        assert top is None
        assert any("global_slot_branch" in k for k in fb), fb

    def test_imported_pointer_global_copy_defers(self, tmp_path):
        (tmp_path / "othermod.py").write_text(self._P + "gp: P = P(3)\n")
        src = (
            "from othermod import gp, P\n"
            "saved: P = P(0)\n"
            "saved = gp\n"
            "print(saved.n)\n")
        _assert_byte_identical(src, extra_lib_dirs=[tmp_path])
        top, _w, fb = _top_level(src, extra_lib_dirs=[tmp_path])
        assert top is None


class TestOptionalPtrGlobalReads:
    """A ptr-repr Optional GLOBAL seeds as the same `T* g{};` slot as its
    plain sibling: bare pointer copies (`q = g;`), null tests, and
    narrowed derefs all ride the seeded pointer binding."""

    def test_reads_route(self):
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.x = 1\n"
            "g: P | None = P()\n"
            "def null_test() -> Int32:\n"
            "    if g is None:\n"
            "        return 0\n"
            "    return 1\n"
            "def copy_read() -> Int32:\n"
            "    q: P | None\n"
            "    q = g\n"
            "    if q is not None:\n"
            "        return q.x\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(null_test())\n"
            "    print(copy_read())\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "q = g;" in cpp

    def test_direct_narrowed_deref_and_imported_route(self, tmp_path):
        # The review-flagged flavors: a DIRECT narrowed deref of the
        # global (`if g is not None: g.x`) and an IMPORTED Optional
        # global's read -- both ride the seeded (qualified) binding.
        from .testutil import _assert_routes_byte_identical
        pre = (
            "from tpy import Int32\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.x = 1\n")
        src = pre + (
            "g: P | None = P()\n"
            "def direct_narrow() -> Int32:\n"
            "    if g is not None:\n"
            "        return g.x\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(direct_narrow())\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        (tmp_path / "othermod.py").write_text(pre + "gp: P | None = P()\n")
        src2 = (
            "from tpy import Int32\n"
            "from othermod import gp\n"
            "def read_it() -> Int32:\n"
            "    if gp is not None:\n"
            "        return gp.x\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(read_it())\n"
            "main()\n")
        from .testutil import _assert_byte_identical
        _assert_byte_identical(src2, extra_lib_dirs=[tmp_path])


class TestGlobalSlotCompInit:
    """A comprehension init at a container global slot renders its
    stmt-expr inside the static slot line (the frame-emplace comp
    branch's top-level twin); unrouted comp SHAPES still gate inside
    the comp machinery."""

    def test_scalar_comp_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (
            "r2 = [i * 2 for i in range(3)]\n"
            "print(r2)\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "static std::vector<int32_t> __global_slot_" in cpp

    def test_str_elem_comp_routes(self):
        # The str-element literal-iterable comp now routes (the
        # list-literal comp-route row: the braced init-list capture);
        # this pin used to record the shape deferring, in bodies and at
        # the global slot alike.
        src = (
            "r1 = [len(x) for x in [\"a\", \"bb\"]]\n"
            "print(r1)\n")
        _assert_byte_identical(src)
        top, _w, fb = _top_level(src)
        assert top is not None


class TestStrGlobalWrites:
    """Write-seeded STR globals: `global label` + literal rebinds and
    view reads ride the str-local arms; the `x = x + y` self-append
    peephole is LOCAL-keyed on the AST side (the global write renders
    the plain concat-assign), so seeded globals must not fold; a bytes
    global stays unseeded (deferring)."""

    def test_str_global_writes_route(self):
        from .testutil import _assert_routes_byte_identical
        src = (
            "label = \"start\"\n"
            "def rewrite() -> int:\n"
            "    global label\n"
            "    label = \"changed\"\n"
            "    return len(label)\n"
            "def concat() -> int:\n"
            "    global label\n"
            "    label = label + \"!\"\n"
            "    return len(label)\n"
            "def main() -> None:\n"
            "    print(rewrite())\n"
            "    print(concat())\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        # The concat is the PLAIN assign, never the local += fold.
        assert "label = (::tpy::str_concat(label, \"!\"));" in cpp
        assert "label += " not in cpp

    def test_bytes_global_write_defers(self):
        from .testutil import _fn, _lower_ctx
        src = (
            "data = b\"raw\"\n"
            "def bw() -> int:\n"
            "    global data\n"
            "    data = b\"new\"\n"
            "    return len(data)\n"
            "print(bw())\n")
        _assert_byte_identical(src)
        assert _fn(_lower_ctx(src), "bw") is None

    def test_string_global_aug_and_dict_comp_route(self):
        # Review flavors: a String-typed global write, its aug-assign
        # (the += arm's seeded-global flavor), and a dict comp at a
        # global slot -- all byte-identical.
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import String\n"
            "s2: String = String(\"a\")\n"
            "d1 = {k: k * 2 for k in range(3)}\n"
            "def sw() -> int:\n"
            "    global s2\n"
            "    s2 = String(\"bb\")\n"
            "    return len(s2)\n"
            "def aug() -> int:\n"
            "    global s2\n"
            "    s2 += String(\"!\")\n"
            "    return len(s2)\n"
            "def main() -> None:\n"
            "    print(sw())\n"
            "    print(aug())\n"
            "    print(len(d1))\n"
            "main()\n")
        _assert_routes_byte_identical(src)


class TestListFromArrayGlobal:
    """`list(arr)` over an Array pointer-slot global at a top-level slot:
    the construct<...> wrap over the deref'd name; the function-body
    flavor defers downstream (honest)."""

    def test_top_level_routes_fn_body_defers(self):
        from .testutil import (_assert_byte_identical, _fn, _lower_ctx,
                               _assert_routes_byte_identical)
        src = (
            "from tpy import Int32, Array\n"
            "arr: Array[Int32, 3] = [5, 6, 7]\n"
            "from_arr = list(arr)\n"
            "print(from_arr)\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert ("::tpy::construct<std::vector<int32_t>>((*arr))"
                in cpp)
        src2 = (
            "from tpy import Int32, Array\n"
            "def fn_body() -> None:\n"
            "    arr: Array[Int32, 3] = [1, 2, 3]\n"
            "    xs = list(arr)\n"
            "    print(len(xs))\n"
            "fn_body()\n")
        _assert_byte_identical(src2)
        assert _fn(_lower_ctx(src2), "fn_body") is None
        from .testutil import _compile, _entry
        from ..codegen_cpp.context import CodeGenOptions
        c, mods = _compile(src2)
        c.generate_code_to_strings(
            _entry(mods), options=CodeGenOptions(thir_codegen=True))
        # The defer's tag stays pinned so a moved reject site is visible.
        assert any("expr.call" in k for k in c._thir_fallback), \
            c._thir_fallback

    def test_set_from_array_routes(self):
        # The set(arr) sibling of the list(arr) admission.
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import Int32, Array\n"
            "arr: Array[Int32, 3] = [5, 6, 7]\n"
            "from_arr = set(arr)\n"
            "print(len(from_arr))\n")
        _assert_routes_byte_identical(src)


class TestGlobalSlotUnpackTarget:
    """A module-level `n, p = f()` whose Own element lands in a pointer-slot
    global: `static T __global_slot_N = std::move(std::get<i>(__tup_N));`
    then `p = &__global_slot_N;` (the AST's pointer_locals reassign tail).
    The scalar sibling takes the plain declared-name assign."""

    HDR = (
        "from tpy import Own, Int32\n"
        "class Point:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def make_pair() -> tuple[Int32, Own[Point]]:\n"
        "    return (Int32(42), Point(Int32(1)))\n"
    )

    SRC = HDR + (
        "n, p = make_pair()\n"
        "print(n)\n"
        "print(p.x)\n"
    )

    def test_routes(self):
        top, wit, fallback = _top_level(self.SRC)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        assert wit.get("stmt.tuple_unpack.global_slot_target") == 1

    def test_byte_identical_and_emits_static_slot(self):
        _hpp, cpp = _assert_byte_identical(self.SRC)
        assert "auto __tup_1 = make_pair();" in cpp
        assert "n = std::get<0>(__tup_1);" in cpp
        assert ("static Point __global_slot_1 = "
                "std::move(std::get<1>(__tup_1));") in cpp
        assert "p = &__global_slot_1;" in cpp

    def test_two_slot_targets_number_consecutively(self):
        # Slot numbering is module-init-wide; two owned targets in one
        # unpack draw two consecutive slots.
        src = self.HDR + (
            "def make_two() -> tuple[Own[Point], Own[Point]]:\n"
            "    return (Point(Int32(3)), Point(Int32(5)))\n"
            "g1, g2 = make_two()\n"
            "print(g1.x + g2.x)\n")
        top, _wit, fallback = _top_level(src)
        assert top is not None
        assert not [k for k in fallback if k.startswith("top_level:")]
        _hpp, cpp = _assert_byte_identical(src)
        assert "static Point __global_slot_1 = " in cpp
        assert "static Point __global_slot_2 = " in cpp

    def test_slot_reuse_after_earlier_write_defers(self):
        # An earlier rvalue write already owns the slot, so this unpack owes
        # the REUSE render (`p = &(__global_slot_1 = ..)`), not a fresh slot.
        src = self.HDR + (
            "p = Point(Int32(0))\n"
            "n, p = make_pair()\n"
            "print(n, p.x)\n")
        top, _wit, fallback = _top_level(src)
        assert top is None
        assert any(k.startswith("top_level:stmt.tuple_unpack")
                   for k in fallback), fallback
        _assert_byte_identical(src)

    def test_optional_global_target_defers(self):
        # A ptr-repr Optional global's slot carries the INNER spelling plus
        # an optional_to_ptr lift -- a different line.
        src = self.HDR + (
            "def make_opt() -> tuple[Int32, Own[Point | None]]:\n"
            "    return (Int32(1), Point(Int32(2)))\n"
            "n, opt = make_opt()\n"
            "print(n)\n"
            "if opt is not None:\n"
            "    print(opt.x)\n")
        top, _wit, fallback = _top_level(src)
        assert top is None
        assert any(k.startswith("top_level:stmt.tuple_unpack")
                   for k in fallback), fallback
        _assert_byte_identical(src)

    def test_branch_scoped_slot_defers(self):
        # A first slot write inside an if body is the branch-scoped render.
        src = self.HDR + (
            "def flag() -> bool:\n"
            "    return True\n"
            "br = Point(Int32(0))\n"
            "if flag():\n"
            "    n, br = make_pair()\n"
            "print(br.x)\n")
        top, _wit, fallback = _top_level(src)
        assert top is None
        assert any(k.startswith("top_level:")
                   for k in fallback), fallback
        _assert_byte_identical(src)

    def test_for_body_slot_defers(self):
        # The for body is codegen's namespace push, which DROPS `static` from
        # the slot line -- a divergent render this arm must not mirror.
        src = self.HDR + (
            "for i in range(1):\n"
            "    n, fp = make_pair()\n"
            "print(fp.x)\n")
        top, _wit, fallback = _top_level(src)
        assert top is None
        assert any(k.startswith("top_level:")
                   for k in fallback), fallback
        _assert_byte_identical(src)
