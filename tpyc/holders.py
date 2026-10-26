"""Who holds a loan: a binding, by its name, a running iterator, or a
holder outside the function.

A loan's holder is a binding name (`str`), an `IterHolder`, the running
iterator of ONE `for` statement or ONE comprehension clause, or `External`,
a holder the function does not see end. The kind of a
synthetic holder is its type, never decoded from a spelling, so no program
name can stand for an iterator and two loops never share one. A binding
holder stays a name because the borrow tracker's chain walks read a binding
borrower back as storage (`StorageKey.of_name`).

A leaf module: liveness and sema both key on the same holders.
"""

from __future__ import annotations

from enum import Enum


class IterHolder:
    """The running iterator of one loop statement or comprehension clause.

    Equal and hashed by the node's identity, so every place that names the
    iterator of one loop -- the tracker's registration and loop exit,
    liveness's live sets, the alias-rebind replay -- names the same holder.
    The node is kept, so its address cannot be recycled while the holder
    lives (`identity_map`'s rule). Copies are the holder itself: a nested
    def's analysis deep-copies the function state, which must not copy the
    AST node and break the identity.

    A holder carries no order of its own: code that must order holders
    whose loops share a location (macro-built loops have none) numbers the
    loops itself, as the alias-rebind replay does in walk order.
    """

    __slots__ = ("_node",)

    def __init__(self, node: object) -> None:
        object.__setattr__(self, "_node", node)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("IterHolder is immutable")

    @property
    def node(self) -> object:
        return self._node

    def __eq__(self, other: object) -> bool:
        return isinstance(other, IterHolder) and other._node is self._node

    def __hash__(self) -> int:
        return hash((IterHolder, id(self._node)))

    def __copy__(self) -> IterHolder:
        return self

    def __deepcopy__(self, memo: dict) -> IterHolder:
        return self

    def __repr__(self) -> str:
        loc = getattr(self._node, "loc", None)
        where = f"{loc.line}:{loc.column}" if loc is not None else "no loc"
        return f"IterHolder({type(self._node).__name__} at {where})"


class External(Enum):
    """A holder outside the function, which keeps what it holds for as long
    as it likes: nothing in the body ends its loans -- no loop exit, no
    rebind, no scope. One holder for every such escape, so no escape is told
    apart from another by the code that asks whether a loan is live."""
    # A pointer that left through a call argument (the alias-rebind replay).
    ESCAPED = "escaped"


# Who holds a loan in the borrow tracker, and what a live set holds.
Holder = str | IterHolder | External
