"""Send/Sync diagnostic chain-walker (docs/SEND_SYNC_DESIGN.md OQ6).

`why_not_send` / `why_not_sync` explain *why* a type fails the Send / Sync
trait. They never re-derive the boolean answer -- `t.is_send()` / `t.is_sync()`
stay the single source of truth. The walker only attributes an already-False
answer to the sub-components that carry it (record fields and bases, container
type-args, union members, tuple elements, wrapper payloads, frame slots),
bottoming out in a per-form leaf reason. Adding a new Send/Sync-bearing TpyType
therefore needs one arm here, not a second copy of the rule.

The cycle-guarded recursion driver and the indented-tree renderer are shared
with the movability diagnostic in `trait_chain`; this module supplies only the
Send/Sync-specific predicate, sub-component enumeration, and leaf reasons.
`render_chain` is re-exported (parameterized by the trait) for enforcement
sites, `assert_send[T]()` failures, and `--explain-send`.
"""
from typing import Optional

from tpyc.typesys import (
    TpyType, NominalType, UnionType, TupleType, OptionalType, PtrType,
    OwnType, ReadonlyType, RefType, SendType, SyncType, FrameType,
    CallableType, TypeParamRef, AliasRef, RecursiveAliasInstanceType,
    substitute_type_params_structural,
)
from .trait_chain import ChainNode, Recurse, build_chain, render_chain as _render_chain

# Back-compat alias: callers that referenced the chain node type by name.
SendChain = ChainNode


def _funcs(send: bool):
    """The (conforms, attribute, leaf_reason) triple for build_chain, with the
    send/sync selector bound in."""
    return (
        (lambda t: t.is_send() if send else t.is_sync()),
        (lambda t, recurse: _attribute(t, recurse, send)),
        (lambda t: _leaf_reason(t, send)),
    )


def why_not_send(t: TpyType) -> Optional[ChainNode]:
    """Return a derivation tree for why `t` is not Send, or None if it is."""
    return build_chain(t, str(t), *_funcs(True))


def why_not_sync(t: TpyType) -> Optional[ChainNode]:
    """Return a derivation tree for why `t` is not Sync, or None if it is."""
    return build_chain(t, str(t), *_funcs(False))


def why_not_frame(frame: FrameType, label: str, send: bool) -> Optional[ChainNode]:
    """Derivation tree for a concrete value's captured-state `frame`, rooted
    at `label` (the value's static type). Used at Send[T] / Sync[T] conversion
    sites where the static type is an erased callable carrying no slots, but
    the lambda / function-ref value has a classified FrameType that names the
    offending capture. Returns None if the frame conforms; a childless chain
    means the cause is a sub-frame (not an own slot) the caller should ignore
    in favour of the static-type chain."""
    return build_chain(frame, label, *_funcs(send))


def render_chain(chain: ChainNode, send: bool) -> str:
    return _render_chain(chain, "Send" if send else "Sync")


# -- internals --

def _attribute(t: TpyType, recurse: Recurse, send: bool) -> list[ChainNode]:
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
                        and (n := recurse(arg, str(arg))) is not None]
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
        out: list[ChainNode] = []
        for f in rec.fields:
            ft = substitute_type_params_structural(f.type, subst) if subst else f.type
            node = recurse(ft, f"field '{f.name}: {ft}'")
            if node is not None:
                out.append(node)
        for p in rec.parents:
            pt = substitute_type_params_structural(p, subst) if subst else p
            node = recurse(pt, f"base '{pt}'")
            if node is not None:
                out.append(node)
        return out

    if isinstance(t, UnionType):
        return [n for m in t.members
                if (n := recurse(m, str(m))) is not None]

    if isinstance(t, TupleType):
        return [n for i, e in enumerate(t.element_types)
                if (n := recurse(e, f"element {i}: {e}")) is not None]

    if isinstance(t, OptionalType):
        node = recurse(t.inner, str(t.inner))
        return [node] if node is not None else []

    if isinstance(t, PtrType):
        # Ptr[readonly[T]] is Sync iff T is; the inner pointee is the cause.
        if not send and t.is_readonly:
            node = recurse(t.inner_pointee, str(t.inner_pointee))
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
                node = recurse(s.type, f"captured '{s.name}: {s.type}'")
                if node is not None:
                    out.append(node)
                    continue
            out.append(ChainNode(
                f"captured '{s.name}'",
                "borrow/reference capture aliases originating-thread memory",
            ))
        return out

    if isinstance(t, OwnType):
        # Own[T].is_send delegates to T, so the wrapped type is the cause; but
        # Own[T] is never Sync (single-owner move slot), independent of T -- an
        # inherent leaf, not a pass-through.
        if send:
            node = recurse(t.wrapped, str(t.wrapped))
            return [node] if node is not None else []
        return []

    if isinstance(t, (ReadonlyType, RefType, SendType, SyncType)):
        node = recurse(t.wrapped, str(t.wrapped))
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
