"""Generic why-not-<trait> chain walker, shared by the Send/Sync (`send_chain`)
and movability (`move_chain`) diagnostics.

The trait-specific parts are supplied by the caller: a `conforms` predicate
(the trait's single source of truth -- the walker never re-derives it), an
`attribute` function that enumerates the non-conforming sub-components of a
type, and a `leaf_reason` for a type whose own shape is the cause. This module
owns the parts that are identical across traits: the cycle-guarded recursion
driver (conforming sub-trees pruned, a back-edge assumed conforming so
self-referential generics terminate) and the indented-tree renderer.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

from tpyc.typesys import TpyType

# Recurse into a sub-component: `(sub_type, label) -> ChainNode | None`. An
# `attribute` implementation calls this for each sub-component and keeps the
# non-None results; it never threads the cycle-guard set itself.
Recurse = Callable[[TpyType, str], Optional["ChainNode"]]


@dataclass(frozen=True)
class ChainNode:
    """One node of a why-not-<trait> derivation tree. `reason` is set only on
    leaves (the type whose own shape disqualifies it); interior nodes carry the
    failure down through `children`."""
    label: str
    reason: Optional[str] = None
    children: tuple["ChainNode", ...] = field(default_factory=tuple)


def build_chain(
    t: TpyType,
    label: str,
    conforms: Callable[[TpyType], bool],
    attribute: Callable[[TpyType, Recurse], list["ChainNode"]],
    leaf_reason: Callable[[TpyType], str],
    seen: frozenset = frozenset(),
) -> Optional[ChainNode]:
    """Derivation tree for why `t` fails the trait, or None if it conforms."""
    if conforms(t):
        return None
    if t in seen:
        return None
    next_seen = seen | {t}

    def recurse(sub: TpyType, sub_label: str) -> Optional[ChainNode]:
        return build_chain(sub, sub_label, conforms, attribute, leaf_reason, next_seen)

    children = attribute(t, recurse)
    if children:
        return ChainNode(label, None, tuple(children))
    return ChainNode(label, leaf_reason(t), ())


def render_chain(node: ChainNode, trait_name: str) -> str:
    """Render a derivation tree as an indented tree, e.g.

        list[Order] is not Send
        +-- field 'handler: Ptr[Buf]' is not Send (raw pointer)
    """
    lines: list[str] = []
    _render(node, trait_name, prefix="", is_root=True, out=lines)
    return "\n".join(lines)


def _render(node: ChainNode, trait_name: str, prefix: str,
            is_root: bool, out: list[str]) -> None:
    if is_root:
        head = f"{node.label} is not {trait_name}"
        if not node.children and node.reason:
            head += f" ({node.reason})"
        out.append(head)
        child_prefix = ""
    else:
        text = node.label
        if not node.children:
            text += f" is not {trait_name}"
            if node.reason:
                text += f" ({node.reason})"
        out.append(f"{prefix}+-- {text}")
        child_prefix = prefix + "    "
    for c in node.children:
        _render(c, trait_name, child_prefix, False, out)
