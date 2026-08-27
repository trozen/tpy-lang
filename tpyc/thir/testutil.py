"""Shared helpers for the THIR scaffold test files: compile/lower
wrappers, ctor lowering, and common source fixtures."""

from __future__ import annotations

import io

from .. import get_lib_dir
from ..compiler import Compiler
from .emit import emit_thir_constructor_tail
from .lower import lower_module

_STDLIB_DIRS = [get_lib_dir() / "tpy"]


def _compile(source: str, extra_lib_dirs=None, default_int: str = "Int32"):
    dirs = list(extra_lib_dirs or []) + _STDLIB_DIRS
    compiler = Compiler.from_source(source, lib_dirs=dirs,
                                    default_int=default_int)
    return compiler, compiler.compile()


def _entry(modules):
    return [m for m in modules if m.is_entry_point][0]


def _lower(source: str, default_int: str = "Int32"):
    compiler, modules = _compile(source, default_int=default_int)
    entry = _entry(modules)
    return lower_module(entry.ast, entry.analyzer)


def _lower_ctx(source: str):
    """Lower inside the compiler context -- required once non-value records are
    involved: `NominalType.is_user_record` / `.to_cpp()` resolve through the
    active Compiler (the registry / native-name maps), unlike the value-scalar
    types `_lower` covers."""
    from ..compilation_context import activate_compiler
    compiler, modules = _compile(source)
    entry = _entry(modules)
    with activate_compiler(compiler):
        return lower_module(entry.ast, entry.analyzer)


def _assert_no_fallback(compiler, source: str) -> None:
    """Fail unless every body in `source` ROUTED.

    A pin that claims routing but whose body silently fell back cannot fail:
    the AST re-emits that body, so render-string and byte-identity assertions
    pass either way. That is how five dead pins reached review on one branch.

    Module-wide, deliberately blunt: it cannot tell WHICH body fell back, so a
    fixture carrying a deliberately-unroutable sibling trips it. Use it via
    `_assert_routes_byte_identical` on a fixture written to route end to end."""
    fell = getattr(compiler, "_thir_fallback", {})
    total = sum(fell.values())
    assert total == 0, (
        f"the pin claims routing but {total} body/bodies fell back "
        f"({dict(fell)}) -- the assertions would pass on AST output alone.\n"
        f"source:\n{source}")


def _rejects_at(reasons, landmark: str) -> bool:
    """Whether any fallback reason rejects at `landmark`.

    A reason may carry the blocking shape as a `:`-suffix, so testing a
    landmark by exact key or exact equality goes VACUOUS the moment its gate
    starts recording a detail: the bare string no longer appears in any form,
    an absence assertion can never fail again, and the pin keeps passing while
    testing nothing. Match on the landmark and let the suffix vary.

    Takes either full keys (`body:stmt.assert`) or bare reasons (`expr.call`)
    -- pass the landmark in whichever form the caller's helper returns."""
    return any(r == landmark or r.startswith(landmark + ":") for r in reasons)


def _assert_rejects_at(fallback, landmark: str, shape: str | None = None,
                       count: int | None = None) -> None:
    """Assert a boundary pin's body stayed on the AST path, at `landmark` and
    -- when `shape` is given -- for exactly that blocking shape.

    Pass the shape wherever the pin claims a NAMED reject. Landmark alone is
    satisfied when the gate under test happily admitted and something
    unrelated further down rejected instead, so a pin without it survives the
    exact regression it exists to catch, and cannot distinguish "stays AST for
    the reason claimed" from "stays AST at all". Omit it only where the claim
    really is just "this lands on the fallback boundary rather than escaping".

    Spelling the whole tally instead (`== {key: n}`) makes the pin fail
    whenever any unrelated body in the fixture changes status, and the cheap
    repair is to re-record whatever the run printed -- which is how a pin
    stops asserting what it was written for. Claim only the landmark under
    test.

    `fallback` is a tally, a bare collection of reasons, or one reason;
    `count` claims the number of rejecting bodies, and is worth spelling only
    where multiplicity is itself the point."""
    reasons = [fallback] if isinstance(fallback, str) else list(fallback)
    matched = [r for r in reasons if _rejects_at([r], landmark)]
    assert matched, (
        f"nothing rejects at {landmark!r} -- the shape either routed or moved "
        f"its reject elsewhere: {fallback!r}")
    if shape is not None:
        want = f"{landmark}:{shape}"
        assert set(matched) == {want}, (
            f"the reject at {landmark!r} is not {shape!r}, so the pin's "
            f"named boundary is not what held: {fallback!r}")
    if count is not None:
        got = (sum(fallback[r] for r in matched)
               if isinstance(fallback, dict) else len(matched))
        assert got == count, (
            f"expected {count} body/bodies rejecting at {landmark!r}, "
            f"got {got}: {fallback!r}")


def _lower_ctx_witnessed(source: str, extra_lib_dirs=None,
                         default_int: str = "Int32"):
    """_lower_ctx plus the per-face witness counts the run recorded
    (faces.py, `compiler._thir_face_witnesses`). Lets a unit pin that its
    shape actually reaches the gate/lowering face it exercises -- without
    the pin, a refactor can silently un-witness a face while routing and
    the byte-diff both stay green."""
    from ..compilation_context import activate_compiler
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    entry = _entry(modules)
    with activate_compiler(compiler):
        thir = lower_module(entry.ast, entry.analyzer)
    return thir, compiler._thir_face_witnesses


def _fn(thir, name):
    return next((f for f in thir.functions if f.name == name), None)


def _assert_byte_identical(source: str, default_int: str = "Int32",
                           extra_lib_dirs=None, comments: bool = True):
    """Compile `source` through both codegen paths and assert the emitted
    (.hpp, .cpp) are byte-identical.

    Byte-identity ALONE proves nothing about routing -- a fallback emits the
    AST verbatim, so this passes either way. That is fine for a reject-unit
    (where identity IS the claim); a pin that claims its shape ROUTES must use
    `_assert_routes_byte_identical` instead.

    The source-comment echo is ON by default, matching what the corpus runs
    with: comment TRIVIA divergences are invisible without it, since a
    statement that emits no code can still owe its leading `#`-comment lines.
    Pass `comments=False` only for a shape whose comment rendering is itself
    under test elsewhere."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    entry = _entry(modules)
    ast = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=comments,
                                      comment_line_numbers=False,
                                      thir_codegen=False))
    thir = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=comments,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    assert thir == ast
    return thir


def _assert_routes_byte_identical(source: str, default_int: str = "Int32",
                                  extra_lib_dirs=None, comments: bool = True):
    """`_assert_byte_identical` PLUS the routing claim: every body in `source`
    lowered through THIR. The pair is the minimum honest pin for a new arm --
    identity alone is satisfied by a whole-body fallback, so without the
    routing half a pin cannot fail. Write the fixture to route end to end; put
    the shapes that must keep rejecting in their own reject-unit."""
    from ..codegen_cpp.context import CodeGenOptions
    thir = _assert_byte_identical(source, default_int, extra_lib_dirs,
                                  comments)
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=comments,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    _assert_no_fallback(compiler, source)
    return thir


def _constant_positions(source: str, default_int: str = "Int32",
                        extra_lib_dirs=None, comments: bool = True):
    """The routing lens for the two NON-BODY constant positions (class
    constants, `Final` global initializers).

    Returns `(routed, ast_rendered, fallback)`: the constant names THIR
    rendered, the names the AST `gen_expr` fallback rendered, and the
    fallback tally. The AST set comes from spying on the exact `gen_expr`
    call each position falls back to, so a pin built on it fails when the
    skeleton stops calling THIR -- byte-identity and an empty fallback dict
    are both satisfied by a fallback, and so can claim routing without
    being able to fail.

    The emitted (.hpp, .cpp) are asserted byte-identical across the two
    paths on the way, since a routed constant that renders differently is
    the failure this position exists to prevent."""
    from ..codegen_cpp.context import CodeGenOptions
    from ..codegen_cpp.expressions import ExpressionGenerator
    opts = dict(emit_source_comments=comments, comment_line_numbers=False)
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    entry = _entry(modules)
    ast = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(thir_codegen=False, **opts))
    seen: set[int] = set()
    original = ExpressionGenerator.gen_expr

    def spy(self, expr, target_type=None):
        seen.add(id(expr))
        return original(self, expr, target_type)

    ExpressionGenerator.gen_expr = spy
    try:
        thir = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(thir_codegen=True, **opts))
    finally:
        ExpressionGenerator.gen_expr = original
    assert thir == ast, "routed constant diverged from the AST oracle"
    routed: set[str] = set()
    ast_rendered: set[str] = set()
    for name, init in _constant_inits(entry):
        (ast_rendered if id(init) in seen else routed).add(name)
    return routed, ast_rendered, dict(compiler._thir_fallback)


def _constant_inits(entry):
    """(name, initializer expression) for every constant position in the
    entry module -- `Final` globals first, then each record's class
    constants."""
    from ..parse.nodes import TpyVarDecl
    out = []
    for stmt in entry.ast.top_level_stmts:
        if isinstance(stmt, TpyVarDecl) and stmt.is_final and stmt.init:
            out.append((stmt.name, stmt.init))
    for record in entry.ast.all_records():
        info = entry.analyzer.registry.get_record(record.name)
        if info is None:
            continue
        for cc_name, cc_fld in info.class_constants.items():
            if cc_fld.default_expr is not None:
                out.append((cc_name, cc_fld.default_expr))
    return out


def _top_level(source: str, default_int: str = "Int32",
               extra_lib_dirs=None):
    """Lower a module's `__tpy_init` body through the REAL generator seeding
    (the global-type map only the generator builds) and return
    (thir_top_level_or_None, face witnesses, fallback tally).

    A routing pin asserts the first is not None; a boundary pin asserts it IS
    None and names the `top_level:` reject in the tally."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    entry = _entry(modules)
    ctx = compiler.collect_thir(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=True))
    return (ctx.thir_top_level, dict(compiler._thir_face_witnesses),
            dict(compiler._thir_fallback))


def _thir_ctx(source: str, default_int: str = "Int32", extra_lib_dirs=None):
    """The seeded codegen ctx (`thir_functions` / `thir_simple_gens` /
    `thir_resumables`) plus the fallback tally.

    The routing view for a body the plain `_fn` lens cannot see: the generator
    leaf seams key their own maps, and neither they nor `_thir_routed_bodies`
    (functions + constructors only) move when such a body routes."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    ctx = compiler.collect_thir(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                thir_codegen=True))
    return ctx, dict(compiler._thir_fallback)


def _thir_ctx_witnessed(source: str, default_int: str = "Int32",
                        extra_lib_dirs=None):
    """`_thir_ctx` plus the face witnesses -- the CONSTRUCTOR-side sibling of
    `_lower_ctx_witnessed`, which lowers free functions only and so cannot see
    a face a ctor MIL row records."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    ctx = compiler.collect_thir(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                thir_codegen=True))
    return (ctx, dict(compiler._thir_face_witnesses),
            dict(compiler._thir_fallback))


def _lower_ctor(source: str, record_name: str, extra_lib_dirs=None):
    """Lower one record's constructor to its THIRConstructor (or None if outside
    the M3 slice). Within the compiler context -- records resolve through the live
    registry / native-name maps, like `_lower_ctx`. `extra_lib_dirs` lets the
    entry import records from sibling modules (cross-module ctor-field tests)."""
    from ..compilation_context import activate_compiler
    from .lower import iter_module_constructors, lower_constructor
    compiler, modules = _compile(source, extra_lib_dirs)
    entry = _entry(modules)
    with activate_compiler(compiler):
        for rec, init, self_type in iter_module_constructors(entry.ast, entry.analyzer):
            if rec.name == record_name:
                return lower_constructor(rec, init, entry.analyzer,
                                         self_type=self_type)
    return None


def _ctor_tail(ctor) -> str:
    buf = io.StringIO()
    emit_thir_constructor_tail(buf, ctor)
    return buf.getvalue()


def _emit_expr(e) -> str:
    """Render one expression standalone, over a fresh emit state (tests
    only): the real `_emit_expr` threads the per-body state for the arg-temp
    sink, which a single-expression assertion doesn't exercise."""
    from .emit import _emit_expr as emit_expr, _EmitState, _NO_COMMENTS
    return emit_expr(e, _EmitState(_NO_COMMENTS))


_PRELUDE = "from tpy import Int32, UInt8, UInt64\n"

# Shared F1-record fixture (records need `_lower_ctx` / a full compile -- see its
# docstring). Defined here so class-body-level source builders can reference it.
_F1_RECORDS = (
    "from tpy import Int32, Own, readonly\n"
    "class Leaf:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "class Inner:\n"
    "    value: Int32\n"
    "    opt: Leaf | None\n"
    "    def __init__(self, value: Int32):\n        self.value = value\n        self.opt = None\n"
    "class Box:\n"
    "    inner: Inner\n"
    "    opt: Inner | None\n"
    "    n: Int32\n"
    "    def __init__(self, inner: Own[Inner]):\n"
    "        self.inner = inner\n        self.opt = None\n        self.n = 0\n"
)


