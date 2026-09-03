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


def _rejects_at(reasons, landmark: str) -> bool:
    """Whether any reject reason rejects at `landmark`.

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
    """Assert a boundary pin's body was REJECTED at `landmark` and -- when
    `shape` is given -- for exactly that blocking shape.

    Pass the shape wherever the pin claims a NAMED reject. Landmark alone is
    satisfied when the gate under test happily admitted and something
    unrelated further down rejected instead, so a pin without it survives the
    exact regression it exists to catch, and cannot distinguish "rejects for
    the reason claimed" from "rejects at all". Omit it only where the claim
    really is just "this lands on the reject boundary rather than escaping".

    `fallback` is a reason -> count mapping, a bare collection of reasons, or
    one reason; `count` claims the number of rejecting bodies, and is worth
    spelling only where multiplicity is itself the point."""
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
    the pin, a refactor can silently un-witness a face while the snapshots
    stay green."""
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
    """Compile `source` and return the emitted `(hpp, cpp)`, asserting that
    nothing rejected.

    The source-comment echo is ON by default, matching what the corpus runs
    with: comment TRIVIA divergences are invisible without it, since a
    statement that emits no code can still owe its leading `#`-comment lines.
    Pass `comments=False` only for a shape whose comment rendering is itself
    under test elsewhere."""
    from ..codegen_cpp.context import CodeGenOptions
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    return compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=comments,
                               comment_line_numbers=False))


# The two names are one helper now: with a single author "routes" and "emits
# without raising" are the same claim.
_assert_routes_byte_identical = _assert_byte_identical


def _strict_reject(source: str, default_int: str = "Int32",
                   extra_lib_dirs=None):
    """Emit `source` and return the `ThirRejectError` it raised, plus the
    one-element reason list `_assert_rejects_at` reads.

    Fails if nothing rejects: a shape that has since started lowering must
    break the pin rather than leave it asserting a diagnostic no program
    produces."""
    from ..codegen_cpp.context import CodeGenOptions, ThirRejectError
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    try:
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   comment_line_numbers=False))
    except ThirRejectError as err:
        return err, _reject_reasons(err)
    raise AssertionError(
        "nothing rejected -- the shape now lowers, so the pin no longer "
        f"covers the diagnostic it names.\nsource:\n{source}")


def _reject_tally(source: str, default_int: str = "Int32",
                  extra_lib_dirs=None) -> dict[str, int]:
    """The `component:reason` tags emitting `source` reports, counted.

    Empty when nothing rejects, so a pin can read it either way. The attempt
    driver raises at the FIRST rejecting body, so it never holds more than one
    key -- a pin that needs the reasons of several bodies at once has to drive
    lowering body by body instead."""
    from ..codegen_cpp.context import CodeGenOptions, ThirRejectError
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    try:
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   comment_line_numbers=False))
    except ThirRejectError as err:
        return {r: 1 for r in _reject_reasons(err)}
    return {}


def _reject_reasons(err) -> list[str]:
    """The `component:reason` list a reject-observing helper hands to
    `_assert_rejects_at`. One element: a reject raises at the first body that
    fails, so at most one is ever recorded per emit."""
    if err.component is None or err.reason is None:
        return []
    return [f"{err.component}:{err.reason}"]


def _top_level(source: str, default_int: str = "Int32",
               extra_lib_dirs=None):
    """Lower a module's `__tpy_init` body through the REAL generator seeding
    (the global-type map only the generator builds) and return
    `(thir_top_level_or_None, face witnesses, reasons)`.

    A routing pin asserts the first is not None; a boundary pin asserts it IS
    None and names the `top_level:` reject in `reasons`. The reject raises, so
    `reasons` holds at most one entry and the THIR is None exactly when it is
    non-empty."""
    from ..codegen_cpp.context import CodeGenOptions, ThirRejectError
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    entry = _entry(modules)
    try:
        ctx = compiler.collect_thir(
            entry, options=CodeGenOptions(emit_source_comments=False))
    except ThirRejectError as err:
        return None, dict(compiler._thir_face_witnesses), _reject_reasons(err)
    return ctx.thir_top_level, dict(compiler._thir_face_witnesses), []


def _thir_ctx(source: str, default_int: str = "Int32", extra_lib_dirs=None):
    """The seeded codegen ctx (`thir_functions` / `thir_simple_gens` /
    `thir_resumables`), or None when a body rejected, plus the reject reasons.

    The routing view for a body the plain `_fn` lens cannot see: the generator
    leaf seams key their own maps, and neither they nor `_thir_routed_bodies`
    (functions + constructors only) move when such a body lowers. The reject
    raises, so `reasons` holds at most one entry and the ctx is None exactly
    when it is non-empty."""
    from ..codegen_cpp.context import CodeGenOptions, ThirRejectError
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    try:
        ctx = compiler.collect_thir(
            _entry(modules), options=CodeGenOptions(emit_source_comments=False))
    except ThirRejectError as err:
        return None, _reject_reasons(err)
    return ctx, []


def _thir_ctx_witnessed(source: str, default_int: str = "Int32",
                        extra_lib_dirs=None):
    """`_thir_ctx` plus the face witnesses -- the CONSTRUCTOR-side sibling of
    `_lower_ctx_witnessed`, which lowers free functions only and so cannot see
    a face a ctor MIL row records. Returns `(ctx_or_None, witnesses,
    reasons)`."""
    from ..codegen_cpp.context import CodeGenOptions, ThirRejectError
    compiler, modules = _compile(source, extra_lib_dirs,
                                 default_int=default_int)
    try:
        ctx = compiler.collect_thir(
            _entry(modules), options=CodeGenOptions(emit_source_comments=False))
    except ThirRejectError as err:
        return None, dict(compiler._thir_face_witnesses), _reject_reasons(err)
    return ctx, dict(compiler._thir_face_witnesses), []


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


def _raised_in_lowering(err) -> bool:
    """Whether a THIR lowering frame raised `err` -- as opposed to the
    skeleton, which shares the same message builder."""
    tb = err.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__", "").startswith(
                "tpyc.thir.lower"):
            return True
        tb = tb.tb_next
    return False


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


