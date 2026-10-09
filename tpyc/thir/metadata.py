"""Fail-closed inspection of metadata outside a consumer's supported slice."""

from dataclasses import MISSING, fields


def unsupported_metadata(node: object, allowed: set[str]) -> str | None:
    for member in fields(node):
        if member.name in allowed | {"loc", "result_type", "form", "source",
                                     "normalized", "binding", "bindings"}:
            continue
        default = member.default
        if default is MISSING and member.default_factory is not MISSING:
            default = member.default_factory()
        if default is MISSING or getattr(node, member.name) != default:
            return member.name
    return None
