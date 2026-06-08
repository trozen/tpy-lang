"""Send/Sync diagnostic chain-walker (docs/SEND_SYNC_DESIGN.md OQ6).

`why_not_send` / `why_not_sync` explain *why* a type fails the Send / Sync
trait. They never re-derive the boolean answer -- `t.is_send()` / `t.is_sync()`
stay the single source of truth. The walker only attributes an already-False
answer to the sub-components that carry it (record fields and bases, container
type-args, union members, tuple elements, wrapper payloads, frame slots),
bottoming out in a per-form leaf reason. Adding a new Send/Sync-bearing TpyType
therefore needs one arm here, not a second copy of the rule.

`render_chain` turns the structured result into the indented tree shown at
enforcement sites, in `assert_send[T]()` failures, and under `--explain-send`.
"""
from dataclasses import dataclass, field
from typing import Optional

from tpyc.typesys import (
    TpyType, NominalType, UnionType, TupleType, OptionalType, PtrType,
    OwnType, ReadonlyType, RefType, SendType, SyncType, FrameType,
    CallableType, TypeParamRef, AliasRef, RecursiveAliasInstanceType,
    substitute_type_params_structural,
)


@dataclass(frozen=True)
class SendChain:
    """One node of a why-not-Send/Sync derivation tree.

    `reason` is set only on leaves (the type whose own shape disqualifies it);
    interior nodes carry the failure down through `children`.
    """
    label: str
    reason: Optional[str] = None
    children: tuple['SendChain', ...] = field(default_factory=tuple)


def why_not_send(t: TpyType) -> Optional[SendChain]:
    """Return a derivation tree for why `t` is not Send, or None if it is."""
    return _why_not(t, send=True, label=str(t), seen=frozenset())


def why_not_sync(t: TpyType) -> Optional[SendChain]:
    """Return a derivation tree for why `t` is not Sync, or None if it is."""
    return _why_not(t, send=False, label=str(t), seen=frozenset())


def render_chain(chain: SendChain, send: bool) -> str:
    """Render a SendChain as an indented tree.

        list[Order] is not Send
        +-- field 'handler: Ptr[Buf]' is not Send (raw pointer)
    """
    trait = "Send" if send else "Sync"
    lines: list[str] = []
    _render(chain, trait, prefix="", is_root=True, out=lines)
    return "\n".join(lines)


# -- internals --

def _conforms(t: TpyType, send: bool) -> bool:
    return t.is_send() if send else t.is_sync()


def _why_not(t: TpyType, send: bool, label: str,
             seen: frozenset) -> Optional[SendChain]:
    if _conforms(t, send):
        return None
    # Self-referential generic (e.g. `Node: next: list[Node]`): mirror the
    # oracle's greatest-fixed-point cycle guard -- a back-edge to a type
    # already on the walk stack is assumed conforming, so the real cause is
    # carried by the non-cyclic field and the walk terminates.
    if t in seen:
        return None
    children = _attribute(t, send, seen | {t})
    if children:
        return SendChain(label, None, tuple(children))
    return SendChain(label, _leaf_reason(t, send), ())


def _child(t: TpyType, send: bool, label: str,
           seen: frozenset) -> Optional[SendChain]:
    return _why_not(t, send, label, seen)


def _attribute(t: TpyType, send: bool, seen: frozenset) -> list[SendChain]:
    """Enumerate the non-conforming sub-components of `t`, mirroring the
    recursion in each type's `is_send` / `is_sync`. Empty list -> `t` is a
    leaf whose own shape is the reason."""
    from tpyc.type_def_registry import type_def_of, resolve_send_sync

    if isinstance(t, NominalType):
        td = type_def_of(t)
        # Mirror NominalType.is_send/is_sync precedence: a TypeDef rule
        # (builtin qname behavior, e.g. list[T] Send-iff-T) wins over the
        # record-level answer. When the rule fires, the cause -- if any -- is
        # a non-conforming type-arg; otherwise the type is an inherent leaf
        # (mutable container, borrowed view).
        if td is not None:
            rule = td.is_send if send else td.is_sync
            if resolve_send_sync(rule, t.type_args) is not None:
                return [n for arg in t.type_args if isinstance(arg, TpyType)
                        and (n := _child(arg, send, str(arg), seen)) is not None]
        rec = td.record if td is not None else None
        if rec is None:
            return []
        # @nosend / @nosync force the answer to False regardless of fields.
        override = rec.send_override if send else rec.sync_override
        if override is False:
            return []
        subst: dict[str, TpyType] = {}
        if rec.type_params and t.type_args:
            for name, arg in zip(rec.type_params, t.type_args):
                if isinstance(arg, TpyType):
                    subst[name] = arg
        out: list[SendChain] = []
        for f in rec.fields:
            ft = substitute_type_params_structural(f.type, subst) if subst else f.type
            node = _child(ft, send, f"field '{f.name}: {ft}'", seen)
            if node is not None:
                out.append(node)
        for p in rec.parents:
            pt = substitute_type_params_structural(p, subst) if subst else p
            node = _child(pt, send, f"base '{pt}'", seen)
            if node is not None:
                out.append(node)
        return out

    if isinstance(t, UnionType):
        return [n for m in t.members
                if (n := _child(m, send, str(m), seen)) is not None]

    if isinstance(t, TupleType):
        return [n for i, e in enumerate(t.element_types)
                if (n := _child(e, send, f"element {i}: {e}", seen)) is not None]

    if isinstance(t, OptionalType):
        node = _child(t.inner, send, str(t.inner), seen)
        return [node] if node is not None else []

    if isinstance(t, PtrType):
        # Ptr[readonly[T]] is Sync iff T is; the inner pointee is the cause.
        if not send and t.is_readonly:
            node = _child(t.inner_pointee, send, str(t.inner_pointee), seen)
            return [node] if node is not None else []
        return []

    if isinstance(t, FrameType):
        if t.unclassified:
            return []
        out = []
        for s in t.slots:
            if (s.send if send else s.sync):
                continue
            if s.type is not None:
                node = _child(s.type, send, f"captured '{s.name}: {s.type}'", seen)
                if node is not None:
                    out.append(node)
                    continue
            out.append(SendChain(
                f"captured '{s.name}'",
                "borrow/reference capture aliases originating-thread memory",
            ))
        return out

    if isinstance(t, OwnType):
        # Own[T].is_send delegates to T, so the wrapped type is the cause; but
        # Own[T] is never Sync (single-owner move slot), independent of T -- an
        # inherent leaf, not a pass-through.
        if send:
            node = _child(t.wrapped, send, str(t.wrapped), seen)
            return [node] if node is not None else []
        return []

    if isinstance(t, (ReadonlyType, RefType, SendType, SyncType)):
        node = _child(t.wrapped, send, str(t.wrapped), seen)
        return [node] if node is not None else []

    return []


def _leaf_reason(t: TpyType, send: bool) -> str:
    trait = "Send" if send else "Sync"
    if isinstance(t, OwnType) and not send:
        return "single-owner move slot, not shareable across threads"
    if isinstance(t, PtrType):
        return "raw pointer, no ownership guarantee"
    if isinstance(t, CallableType):
        return (f"erased callable may capture non-{trait} state -- "
                f"wrap with {trait}[Callable[...]]")
    if isinstance(t, FrameType) and t.unclassified:
        return "captures state the compiler cannot classify"
    if isinstance(t, TypeParamRef):
        return f"unresolved type parameter '{t.name}'"
    if isinstance(t, (AliasRef, RecursiveAliasInstanceType)):
        return f"recursive type, conservatively not {trait}"
    if isinstance(t, NominalType):
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(t)
        rec = td.record if td is not None else None
        if rec is not None and (rec.send_override if send else rec.sync_override) is False:
            return f"marked @no{trait.lower()}"
    # Builtin NominalType leaf: distinguish a borrowed view (Sync-but-not-Send)
    # from mutable shared state using the trait booleans themselves -- no qname
    # hardcoding.
    if send and t.is_sync():
        return "non-owning view borrows originating-thread storage"
    if not send and t.is_send():
        return "mutable container, aliased cross-thread mutation is unsynchronized"
    return f"not {trait}"


def _render(node: SendChain, trait: str, prefix: str, is_root: bool,
            out: list[str]) -> None:
    if is_root:
        head = f"{node.label} is not {trait}"
        if not node.children and node.reason:
            head += f" ({node.reason})"
        out.append(head)
        child_prefix = ""
    else:
        text = node.label
        if not node.children:
            text += f" is not {trait}"
            if node.reason:
                text += f" ({node.reason})"
        out.append(f"{prefix}+-- {text}")
        child_prefix = prefix + "    "
    for c in node.children:
        _render(c, trait, child_prefix, False, out)
