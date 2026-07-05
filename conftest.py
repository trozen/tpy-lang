"""Root-level pytest conftest.

Per-test setup: clears the few remaining module-level globals (see
``clear_all_compilation_state``) and activates a fresh
``_TestCompilerContext`` for unit tests that don't construct a real
``Compiler``. ``Compiler.compile()`` activates its own instance on top
during compilation; the activation stack is re-entrant.

Tests that inspect post-compile state still need to wrap the
inspection in ``activate_compiler(compiler)`` -- see the
``_protocol_registry`` fixture for the canonical pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tpyc.compilation_context import activate_compiler
from tpyc.typesys import BUILTIN_RETURN_EXCEPTIONS, clear_all_compilation_state


@dataclass
class _TestCompilerContext:
    """Stand-in for ``Compiler`` carrying just the per-compilation fields
    that ``activate_compiler``-aware helpers read or write. Mirror new
    ``Compiler._init_shared`` fields here when adding them."""
    namespace_map: dict[str, str] = field(default_factory=dict)
    include_path_map: dict[str, str] = field(default_factory=dict)
    return_exception_names: set[str] = field(
        default_factory=lambda: set(BUILTIN_RETURN_EXCEPTIONS)
    )
    protocol_modules: dict[str, str] = field(default_factory=dict)
    union_alias_names: dict[Any, str] = field(default_factory=dict)
    union_display_names: dict[Any, str] = field(default_factory=dict)
    union_wrapper_index: dict[Any, Any] = field(default_factory=dict)
    native_cpp_names: dict[str, str] = field(default_factory=dict)
    dynamic_type_defs: dict[str, Any] = field(default_factory=dict)
    dynamic_created_qnames: set[str] = field(default_factory=set)
    _thir_face_witnesses: dict[str, int] = field(default_factory=dict)
    _thir_reject_reason: str | None = None
    _thir_fallback: dict[str, int] = field(default_factory=dict)


@pytest.fixture(autouse=True)
def _reset_compilation_globals():
    clear_all_compilation_state()
    with activate_compiler(_TestCompilerContext()):  # type: ignore[arg-type]
        yield
