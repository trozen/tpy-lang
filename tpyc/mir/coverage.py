"""Fail-closed checks shared by body and definition coverage."""

from dataclasses import MISSING, fields


class Unsupported(Exception):
    def __init__(self, node: object, reason: str) -> None:
        self.node = node
        self.reason = reason


def require(node: object, condition: bool, reason: str) -> None:
    if not condition:
        raise Unsupported(node, reason)


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
