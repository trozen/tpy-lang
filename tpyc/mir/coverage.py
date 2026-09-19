"""Fail-closed checks shared by body and definition coverage."""

from dataclasses import MISSING, fields

from .nodes import MIRSlot, MIRValueKind


def scalar_wrapper(slot: MIRSlot) -> bool:
    match slot.value_kind:
        case MIRValueKind.OPTIONAL:
            return slot.optional_layout.kind is MIRValueKind.SCALAR
        case MIRValueKind.UNION:
            return all(m is None or m.kind is MIRValueKind.SCALAR for m in slot.union_layout.elements)
        case _:
            return False


class MIRUnsupported(Exception):
    def __init__(self, node: object, reason: str) -> None:
        self.node = node
        self.reason = reason


def require(node: object, condition: bool, reason: str) -> None:
    if not condition:
        raise MIRUnsupported(node, reason)


def plain(node: object, allowed: set[str]) -> None:
    # New non-default metadata must not silently acquire scalar semantics.
    for f in fields(node):
        if f.name in allowed | {"loc", "result_type", "form"}:
            continue
        default = f.default
        if default is MISSING and f.default_factory is not MISSING:
            default = f.default_factory()
        require(node, default is not MISSING and getattr(node, f.name) == default,
                f"unsupported metadata: {f.name}")
