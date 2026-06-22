"""Per-compilation state access via ``ContextVar``.

``Compiler.compile()`` wraps its body in ``with activate_compiler(self):``;
leaf modules (``typesys``, ``type_def_registry``, ``codegen_cpp.context``)
reach the active instance via ``get_current_compiler()`` (soft) or
``require_current_compiler()`` (strict). See ``docs/IR_DESIGN.md`` and the
``Compiler Front-end Performance`` section of ``CLAUDE.md`` for the
broader migration. Parallel in-process compilation is not yet safe --
``_dynamic_attached_qnames`` (type_def_registry) and the
``_evaluating_send`` / ``_evaluating_sync`` / ``_evaluating_movable``
cycle-guard sets (typesys) remain module-level.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .compiler import Compiler


_current_compiler: ContextVar["Compiler | None"] = ContextVar(
    "tpyc_current_compiler", default=None
)


@contextmanager
def activate_compiler(compiler: "Compiler") -> Iterator["Compiler"]:
    """Set ``compiler`` as the active instance for the duration of the block.

    Restores the previous value on exit (re-entrant safe).
    """
    token = _current_compiler.set(compiler)
    try:
        yield compiler
    finally:
        _current_compiler.reset(token)


def get_current_compiler() -> "Compiler | None":
    """Return the currently active compiler, or ``None`` if no compilation is in flight."""
    return _current_compiler.get()


def require_current_compiler() -> "Compiler":
    """Return the active compiler; raise if no compilation is in flight.

    Use at call sites that are unreachable outside ``Compiler.compile()``.
    """
    compiler = _current_compiler.get()
    if compiler is None:
        raise RuntimeError(
            "No active compiler. This helper requires an active "
            "Compiler.compile() context."
        )
    return compiler
