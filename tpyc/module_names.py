"""Module-name normalization helpers."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .typesys import TypeRegistry


def module_from_qname(qname: str, registry: TypeRegistry) -> str | None:
    """Return the module whose generated header declares the named
    symbol, used to drive `#include` emission and cross-module lookups.

    A `@builtin_type` record's qname is a TPy-side label that need not
    match its defining file: `Poll` is tagged `tpy.coro.Poll` but
    declared in `tpy/_core/_types.py`. Prefix-walking the qname to
    `tpy.coro` would route includes through the wrong header. For
    records, consult the registry's qname indexes directly and return
    `RecordInfo.module`. The two indexes (`_qname_index` for builtins,
    `_user_qname_index` for user records) are accessed in preference to
    the public `find_record_by_qname` because its short-name fallback
    walks every module's records dict (O(workspace size)), wrong for a
    helper that runs on every nominal type during reach analysis.

    Falls back to a prefix walk for enums, protocols, parser
    placeholders, and any other qname not in the record indexes.
    """
    record = registry._qname_index.get(qname) or registry._user_qname_index.get(qname)
    if record is not None and record.module:
        return record.module
    parts = qname.split(".")
    for i in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:i])
        if registry.get_module(candidate) is not None:
            return candidate
    if len(parts) >= 2:
        return parts[0]
    return None


def public_module_name(module_name: str, cpp_namespace: str | None = None) -> str:
    """Map a private submodule name to its public module identity.

    e.g. "tpy._core._types" -> "tpy", "tpy._builtins._list" -> "tpy"

    When cpp_namespace is provided (e.g. "tpystd::typing" for tpy._typing),
    derives the public name from the namespace instead of the module path.
    This handles cross-package implementations like typing protocols defined
    in tpy._typing.
    """
    if cpp_namespace and "._" in module_name:
        # Derive from namespace: "tpystd::typing" -> "typing", "tpystd::tpy" -> "tpy"
        ns_parts = cpp_namespace.split("::")
        # Skip the common prefix (e.g. "tpystd") and join the rest
        if len(ns_parts) >= 2 and ns_parts[0] == "tpystd":
            return ".".join(ns_parts[1:])
    parts = module_name.split(".")
    # Keep only parts up to (but not including) the first private component
    public_parts = []
    for part in parts:
        if part.startswith("_"):
            break
        public_parts.append(part)
    return ".".join(public_parts) if public_parts else module_name
