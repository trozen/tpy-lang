"""Fail-closed inspection of metadata outside a consumer's supported slice."""

from dataclasses import MISSING, fields

# Facts every consumer may pass over: where a node sits and what it binds,
# and the rebind slot a write fills and the statement it is declared
# before (`own_slot`, `anchored_slots`) -- the slot's storage is the
# site's own either way.
_ALWAYS_ALLOWED = frozenset({"loc", "result_type", "form", "source",
                             "normalized", "binding", "bindings",
                             "own_slot", "anchored_slots"})


def unsupported_metadata(node: object, allowed: set[str]) -> str | None:
    for member in fields(node):
        if member.name in allowed or member.name in _ALWAYS_ALLOWED:
            continue
        default = member.default
        if default is MISSING and member.default_factory is not MISSING:
            default = member.default_factory()
        if default is MISSING or getattr(node, member.name) != default:
            return member.name
    return None
