"""Centralized const-decision for param emission and call-site lowering.

The compiler infers `const` for borrow-form params when the body provably does
not mutate through them (Phase-2 mutation propagation).

Historically this decision was open-coded at every call site that needed it
(signature emission, body-local typing, call-arg lowering, tuple-unpack typing).
The duplication caused subtle inconsistency: when the gate fired in one place
but not in another, generated code wouldn't compile. Centralizing the decision
into one helper, materialized as ABI facts on FunctionInfo after Phase 2,
removes the inconsistency by construction.

Two axes:
  * `signature_const`  -- emit the const spelling for the param itself.
  * `deep_borrow_const`-- inner pointers / refs / tuple slots are also const.

For most params the two axes move together (T& -> const T&; T* -> const T*;
tuple<T*, ...> -> tuple<const T*, ...>). They diverge for pointer-variant
unions where the existing pattern is shallow-const under inference and
deep-const only under explicit @readonly methods.

These are codegen ABI facts. They are computed AFTER Phase-2 mutation
propagation and never feed back into sema; the mutation lattice is
unaffected by them.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from ..typesys import (
    TpyType, ReadonlyType, OwnType, OptionalType, TupleType, TypeParamRef,
    UnionType, FunctionInfo,
    unwrap_readonly, unwrap_ref_type,
    param_has_mutable_borrow_surface,
)


@dataclass(frozen=True)
class ParamConstDecision:
    """How a param should be emitted in the generated C++."""
    signature_const: bool
    deep_borrow_const: bool


_NOT_CONST = ParamConstDecision(signature_const=False, deep_borrow_const=False)


def decide_param_const(
    ptype: TpyType,
    *,
    index: int,
    pname: str,
    mutated_params: 'frozenset[int] | None',
    addr_escapes_params: 'frozenset[int]' = frozenset(),
    reassigned_params: 'AbstractSet[str] | None' = None,
    is_ptr_variant_union: bool = False,
    const_params: bool = False,
    use_readonly_params: bool = False,
) -> ParamConstDecision:
    """Compute the const decision for a single param.

    Inputs:
      ptype                : declared param type (may be wrapped in Readonly/Ref).
      index                : 0-based position in the param list.
      pname                : param name (for reassigned_params lookup).
      mutated_params       : finalized Phase-2 facts. None = analysis not run;
                             inference returns False unless const_params forces.
      addr_escapes_params  : param indices whose address escapes into a
                             mutable Ptr field. Always disqualifies const.
      reassigned_params    : param names rebound in the body. Disqualifies
                             const for types that need a copy-for-reassign
                             rename (str, BigInt, ...).
      is_ptr_variant_union : whether ptype unwraps to a pointer-variant union.
                             Drives shallow-vs-deep handling.
      const_params         : caller forces const (e.g. const method body, ctor).
      use_readonly_params  : caller wants the deep-const variant for ptr-variant
                             unions (paired with const_params for @readonly).

    Decision rules:
      * Explicit `ReadonlyType` annotation: deep const.
      * Reassigned param with copy-for-reassign type: not const.
      * Direct mutation or address escape: not const.
      * `const_params=True`: const (deep when use_readonly_params or non-union).
      * Otherwise inference: requires `mutated_params is not None` and the
        param to have a mutable borrow surface; index must be absent from
        `mutated_params`. Top-level TypeParamRef is excluded from inference
        (matches existing gen_params behavior).
    """
    inner = unwrap_ref_type(ptype)
    if isinstance(inner, ReadonlyType):
        return ParamConstDecision(signature_const=True, deep_borrow_const=True)

    if reassigned_params and pname in reassigned_params:
        if ptype.param_needs_copy_for_reassign():
            return _NOT_CONST

    if index in addr_escapes_params:
        return _NOT_CONST

    directly_mutated = (mutated_params is not None and index in mutated_params)

    if const_params:
        if directly_mutated:
            return _NOT_CONST
        if is_ptr_variant_union and not use_readonly_params:
            # Shallow const for ptr-variant unions under forced const.
            return ParamConstDecision(signature_const=True, deep_borrow_const=False)
        return ParamConstDecision(signature_const=True, deep_borrow_const=True)

    if mutated_params is None:
        return _NOT_CONST
    if directly_mutated:
        return _NOT_CONST

    if isinstance(inner, TypeParamRef):
        # Generic param: signature spelling decided at instantiation time;
        # const-inference doesn't apply at the template level.
        return _NOT_CONST

    if not (param_has_mutable_borrow_surface(ptype) or is_ptr_variant_union):
        # Pure value type (e.g. int): not a candidate for const-ref shape.
        return _NOT_CONST

    if is_ptr_variant_union and not use_readonly_params:
        # Existing inference-time behavior: shallow const variant only.
        return ParamConstDecision(signature_const=True, deep_borrow_const=False)

    return ParamConstDecision(signature_const=True, deep_borrow_const=True)


def populate_const_borrow_params(fi: FunctionInfo) -> None:
    """Populate FunctionInfo.const_borrow_params / deep_const_borrow_params.

    Called after Phase-2 mutation propagation finalizes `mutated_params`,
    `addr_escapes_params`. The result is a frozen ABI fact -- call sites
    read it directly without re-deriving the decision.

    For now we only populate the inference path (no const_params /
    use_readonly_params context here). Forced-const paths (const methods,
    constructors) compute the decision per-emission, which is fine because
    they're driven by the emitter's frame.
    """
    if fi.mutated_params is None:
        # Phase-2 hasn't run for this function -- leave as None to signal
        # "not analyzed" downstream.
        return

    sig: set[int] = set()
    deep: set[int] = set()
    for i, p in enumerate(fi.params):
        # No `is_ptr_variant_union` info available here without ctx; the
        # shallow-vs-deep distinction for unions only matters at the
        # emitter, where ctx is available. We populate the union case as
        # signature_const=True (the typical inference outcome) and let
        # emitters pick deep vs shallow themselves.
        decision = decide_param_const(
            p.type,
            index=i,
            pname=p.name,
            mutated_params=fi.mutated_params,
            addr_escapes_params=fi.addr_escapes_params,
            reassigned_params=None,  # not tracked on FunctionInfo
        )
        if decision.signature_const:
            sig.add(i)
        if decision.deep_borrow_const:
            deep.add(i)

    fi.const_borrow_params = frozenset(sig)
    fi.deep_const_borrow_params = frozenset(deep)
