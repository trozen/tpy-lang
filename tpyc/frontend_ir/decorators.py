"""Compiler-owned decorator registry for the frontend IR.

A plugin emits decorators as typed `Decorator` IR nodes
(`frontend_ir/nodes.py`). Lowering does not interpret a decorator by
name on the spot; it looks the name up here to learn the *route*:

- `BUILTIN_LOWERING` -- a TPy-builtin decorator (e.g. `tpy.native`) that
  lowering turns into typed flags / linkage on the resulting
  `TpyFunction` / `TpyRecord`, exactly as the parser does for the same
  decorator in hand-written source.
- `MACRO` -- a `@function_macro` / `@class_macro`; lowering threads it
  into `pending_macros` for the sema macro phase to run.

TPy core preloads its built-ins; each plugin contributes additional
entries via its `decorator_manifest` ClassVar. Two sources registering
the same name with a different route/target is a hard load-time error.
See `docs/FRONTEND_PLUGIN_DESIGN.md` "Decorator registry and plugin
manifest".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DecoratorRoute(Enum):
    BUILTIN_LOWERING = "BUILTIN_LOWERING"   # lowering -> TpyFunction/TpyRecord flags
    MACRO = "MACRO"                          # lowering -> pending_macros


@dataclass(frozen=True)
class DecoratorEntry:
    name: str                                # dotted, e.g. "tpy.native"
    route: DecoratorRoute
    target_kinds: tuple[str, ...]            # "function" | "record" | ...


class DecoratorRegistryError(Exception):
    """A plugin's `decorator_manifest` conflicts with an existing
    registry entry (same name, different route/target)."""


# Valid decoration targets, mirroring the IR nodes that carry a
# `decorators` tuple. Methods lower through the function path, so they
# count as "function".
_TARGET_KINDS = frozenset(
    ("function", "record", "field", "enum", "enum_value", "constant"))


# TPy-builtin decorators a plugin may emit, preloaded into every registry.
CORE_DECORATORS: tuple[DecoratorEntry, ...] = (
    DecoratorEntry("tpy.native", DecoratorRoute.BUILTIN_LOWERING,
                   ("function", "record")),
)


def build_registry(
    manifest: tuple[DecoratorEntry, ...] = (),
) -> dict[str, DecoratorEntry]:
    """Merge `CORE_DECORATORS` with a plugin's `decorator_manifest` into a
    name -> entry map. Raises `DecoratorRegistryError` on a conflicting
    redefinition or a malformed/unknown-target entry."""
    registry: dict[str, DecoratorEntry] = {}
    for entry in (*CORE_DECORATORS, *manifest):
        if not isinstance(entry, DecoratorEntry):
            raise DecoratorRegistryError(
                f"decorator_manifest entries must be DecoratorEntry, "
                f"got {entry!r}")
        bad = tuple(k for k in entry.target_kinds if k not in _TARGET_KINDS)
        if bad:
            raise DecoratorRegistryError(
                f"decorator {entry.name!r}: unknown target kind(s) {bad}")
        existing = registry.get(entry.name)
        if existing is not None and existing != entry:
            raise DecoratorRegistryError(
                f"decorator {entry.name!r} registered twice with conflicting "
                f"definitions ({existing.route.value}/{existing.target_kinds} "
                f"vs {entry.route.value}/{entry.target_kinds})")
        registry[entry.name] = entry
    return registry
