"""Shared helpers for the THIR scaffold test files: compile/lower
wrappers, ctor lowering, and common source fixtures."""

from __future__ import annotations

import io

from .. import get_lib_dir
from ..compilation_context import activate_compiler
from ..compiler import Compiler
from ..type_def_registry import declared_native_facts, restore_declared_native_facts
from ..parse.nodes import TpyFunction
from .emit import emit_thir_constructor_tail
from .lower import bindings, iter_module_callables, lower_function
from .lower import resumable as _resumable
from .lower.bindings import BindingTable
from .lower.context import _LowerCtx
from .nodes import THIRResumableBody
from .reject import is_bodyless_binding

_STDLIB_DIRS = [get_lib_dir() / "tpy"]


def _compile(source: str, extra_lib_dirs=None, default_int: str = "int32"):
    dirs = list(extra_lib_dirs or []) + _STDLIB_DIRS
    compiler = Compiler.from_source(source, lib_dirs=dirs,
                                    default_int=default_int)
    return compiler, compiler.compile()


_builtin_stub_facts: dict[str, dict[str, object]] | None = None


def latch_builtin_stub_facts() -> None:
    """Declare every fact the builtin stubs declare, as a compilation does. A
    unit test models the bodies of a compilation, which always has the
    stubs, but many build types and MIR by hand without running one, and
    the per-test reset clears what a compilation latched. The stubs are
    compiled once per process."""
    global _builtin_stub_facts
    if _builtin_stub_facts is None:
        _compile("pass\n")
        _builtin_stub_facts = declared_native_facts()
    restore_declared_native_facts(_builtin_stub_facts)


def _entry(modules):
    return [m for m in modules if m.is_entry_point][0]


def _body_key(record_name: str | None, func: TpyFunction) -> str:
    # The two clones of an @auto_readonly method share their name; the
    # const one is a body of its own.
    name = (f"{func.name}[const]" if func.auto_readonly_polarity == "apply"
            else func.name)
    return f"{record_name}.{name}" if record_name else name


def lower_bodies(source: str) -> tuple[dict[str, object],
                                       dict[str, BindingTable]]:
    """Lower every callable of `source`'s entry module the way a build
    does -- an ordinary body through `lower_function`, an @overload body
    once per stub, a generator or coroutine frame through codegen's frame
    lowering -- and return `({body: lowered body}, {body: the binding table
    it planned})`, both keyed by the body's qualified name (`f`, `Rec.f`
    for a method, `f@i` for an @overload body specialized to its i-th
    stub, `Rec.f[const]` for the const clone of an @auto_readonly method).
    A frame of an @overload generator is keyed `f@i` by its lowering order.
    Codegen re-lowers the ordinary bodies on its way to the frames; each
    body keeps its first lowering and that lowering's table."""
    compiler, modules = _compile(source)
    entry = _entry(modules)
    tables: dict[str, BindingTable] = {}
    frames: dict[str, THIRResumableBody | None] = {}
    lowering: list[str | None] = [None]
    install = bindings.install
    lower_frame = _resumable.lower_resumable

    def capture_table(lc: _LowerCtx, table: BindingTable) -> None:
        key = lowering[0] or _body_key(lc.record_name, lc.func)
        tables.setdefault(key, table)
        install(lc, table)

    def capture_frame(func: TpyFunction, *args: object, **kwargs: object
                      ) -> THIRResumableBody | None:
        key = _body_key(kwargs.get("record_name"), func)
        if func in entry.analyzer.overload_groups:
            key = f"{key}@{sum(k.startswith(key + '@') for k in frames)}"
        lowering[0] = key
        try:
            body = lower_frame(func, *args, **kwargs)
        finally:
            lowering[0] = None
        frames.setdefault(key, body)
        return body
    bindings.install = capture_table
    _resumable.lower_resumable = capture_frame
    try:
        bodies: dict[str, object] = {}
        with activate_compiler(compiler):
            callables = list(iter_module_callables(entry.ast, entry.analyzer))
            for func, self_type in callables:
                if (func.is_generator or func.is_async
                        or is_bodyless_binding(func)):
                    continue
                key = _body_key(
                    self_type.name if self_type is not None else None, func)
                stubs = entry.analyzer.overload_groups.get(func) or [None]
                for i, stub in enumerate(stubs):
                    lowering[0] = key if stub is None else f"{key}@{i}"
                    bodies[lowering[0]] = lower_function(
                        func, entry.analyzer, self_type=self_type, stub=stub)
            lowering[0] = None
            if any(f.is_generator or f.is_async for f, _st in callables):
                compiler.generate_inl_and_code_to_strings(entry)
    finally:
        bindings.install = install
        _resumable.lower_resumable = lower_frame
    bodies.update(frames)
    return bodies, {k: t for k, t in tables.items() if k in bodies}


def _lower_fn(source: str, name: str, default_int: str = "int32"):
    """Compile `source` and lower ONE callable of the entry module through
    the same per-body entry codegen uses (`lower_function` under an active
    compiler), so a pin lowers the way a build does."""
    compiler, modules = _compile(source, default_int=default_int)
    entry = _entry(modules)
    func, self_type = next(
        (f, st) for f, st in iter_module_callables(entry.ast, entry.analyzer)
        if f.name == name)
    with activate_compiler(compiler):
        return lower_function(func, entry.analyzer, self_type=self_type)


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


def _assert_byte_identical(source: str, default_int: str = "int32",
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


def _strict_reject(source: str, default_int: str = "int32",
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


def _reject_reasons(err) -> list[str]:
    """The `component:reason` list a reject-observing helper hands to
    `_assert_rejects_at`. One element: a reject raises at the first body that
    fails, so at most one is ever recorded per emit."""
    if err.component is None or err.reason is None:
        return []
    return [f"{err.component}:{err.reason}"]

