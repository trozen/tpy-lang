"""Module-name normalization helpers.

Leaf utility module: no other tpyc imports, so it can be imported
freely from parser, sema, typesys, and codegen without creating
cycles.
"""
from __future__ import annotations


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
