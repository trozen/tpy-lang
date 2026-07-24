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
    FunctionInfo,
    unwrap_readonly, unwrap_ref_type,
    param_has_mutable_borrow_surface,
)
from .forms import is_ptr_variant_union as _forms_is_ptr_variant_union


@dataclass(frozen=True)
class ParamConstDecision:
    """How a param should be emitted in the generated C++."""
    signature_const: bool
    deep_borrow_const: bool


_NOT_CONST = ParamConstDecision(signature_const=False, deep_borrow_const=False)


def _is_ptr_variant_union(ptype: TpyType) -> bool:
    """Whether `ptype` (after stripping Ref/Readonly) is a non-value union that
    lowers to `std::variant<A*, B*>`. The unwrapping entry point onto the
    canonical `forms.is_ptr_variant_union` (a pure type query, no Compiler ctx),
    so the FunctionInfo verdict can be materialized at Phase-2 time."""
    return _forms_is_ptr_variant_union(unwrap_readonly(unwrap_ref_type(ptype)))


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

    `use_readonly_params=fi.is_readonly` mirrors the signature emitter
    (`gen_params(..., use_readonly_params=func.is_readonly)`): a readonly
    fn/method deep-consts its pointer-variant union params. Requires
    `fi.is_readonly` to be finalized first -- this runs after
    `infer_method_const` in the Phase-2 driver, not inside
    `propagate_mutation_facts`. `reassigned_params` is unavailable here (a
    body-scan fact), but it only flips copy-for-reassign value types (str,
    BigInt), never the borrow-shaped params whose verdict call sites read.
    """
    if fi.mutated_params is None:
        # Phase-2 hasn't run for this function -- leave as None to signal
        # "not analyzed" downstream.
        return

    sig: set[int] = set()
    deep: set[int] = set()
    for i, p in enumerate(fi.params):
        decision = decide_param_const(
            p.type,
            index=i,
            pname=p.name,
            mutated_params=fi.mutated_params,
            addr_escapes_params=fi.addr_escapes_params,
            reassigned_params=None,  # not tracked on FunctionInfo
            is_ptr_variant_union=_is_ptr_variant_union(p.type),
            use_readonly_params=fi.is_readonly,
        )
        if decision.signature_const:
            sig.add(i)
        if decision.deep_borrow_const:
            deep.add(i)

    fi.set_const_borrow_verdict(frozenset(sig), frozenset(deep))
