"""Type-parameter bound enforcement: the bound diagnostics, and the
class-shadowed method type-param bound.

A method declared as `def m[T: Bound](...)` on `class C[T]` is interpreted as
"callable only when class T satisfies Bound" -- mirroring the C++ `requires`
clause codegen emits for such methods. Every sema dispatch path that resolves
a method on a generic record must apply this check, otherwise the C++ template
instantiation surfaces the violation as a cryptic `requires` error instead of
a clean TPy diagnostic.

Kept in a leaf module so every dispatch site can top-level-import it without
participating in the methods/expressions/operators/protocols import cycle.
"""

from __future__ import annotations
from typing import Callable, Collection, Optional

from .. import qnames
from ..typesys import NominalType, TpyType, FunctionInfo
from .send_chain import why_not_send, why_not_sync, render_chain


def find_method_class_param_bound_violation(
    method_info: FunctionInfo,
    class_type_params: Optional[Collection[str]],
    class_subst: dict[str, 'TpyType | int'],
    protocol_checker: Callable[[TpyType, TpyType], bool],
) -> Optional[tuple[str, TpyType, TpyType]]:
    """Return (param_name, bound, concrete_type) for the first class-shadowed
    method type-param whose bound the receiver instantiation doesn't satisfy,
    or None when every shadowed bound is satisfied.

    Callers that want to raise should prefer `raise_if_class_param_bound_violated`,
    which centralizes the diagnostic format.
    """
    if not method_info.type_param_bounds or not class_type_params:
        return None
    for tp in method_info.type_params or ():
        if tp not in method_info.type_param_bounds:
            continue
        if tp not in class_type_params:
            continue
        bound = method_info.type_param_bounds[tp]
        concrete = class_subst.get(tp)
        if not isinstance(concrete, TpyType):
            continue
        if not protocol_checker(concrete, bound):
            return (tp, bound, concrete)
    return None


def raise_if_class_param_bound_violated(
    method_info: FunctionInfo,
    class_type_params: Optional[Collection[str]],
    class_subst: dict[str, 'TpyType | int'],
    protocol_checker: Callable[[TpyType, TpyType], bool],
    error_fn: Callable[[str, object], Exception],
    loc_node: object,
    builtin_owner: bool = False,
) -> None:
    """Raise a sema diagnostic if the receiver doesn't satisfy a class-shadowed
    method type-param bound. The diagnostic format is canonical -- every
    dispatch site uses the same wording so users see one consistent message
    regardless of which path detected the violation.
    """
    violation = find_method_class_param_bound_violation(
        method_info, class_type_params, class_subst, protocol_checker,
    )
    if violation is None:
        return
    raise error_fn(bound_violation_text(method_info.name, violation,
                                        builtin_owner=builtin_owner),
                   loc_node)


def builtin_owned(method_info: FunctionInfo,
                  find_record: Callable[[str], object]) -> bool:
    """Whether the method is declared on a builtin type's stub."""
    qname = method_info.root.owning_type_qname
    record = find_record(qname) if qname else None
    return getattr(record, "builtin_type_key", None) is not None


def bound_violation_text(method_name: str,
                         violation: tuple[str, TpyType, TpyType], *,
                         builtin_owner: bool = False) -> str:
    """The one bound-violation diagnostic. A builtin type's stub names its
    type parameters for itself (`list[T]`), not for the user, so its
    parameter goes unnamed."""
    tp, bound, concrete = violation
    subject = "its type argument" if builtin_owner else f"type parameter '{tp}'"
    return (f"Method '{method_name}' requires {subject} to satisfy "
            f"'{bound}', but '{concrete}' does not conform")


def validate_type_param_bounds(
    type_subst: dict[str, TpyType],
    bounds: dict[str, TpyType],
    func_name: str,
    satisfies_bound,
    error_fn,
    substitute,
    detail: str = "",
) -> None:
    """Validate that resolved type args satisfy their type parameter bounds
    (`type_param_bound_error`), raising `error_fn` of the message."""
    msg = type_param_bound_error(type_subst, bounds, func_name,
                                 satisfies_bound, substitute, detail)
    if msg is not None:
        raise error_fn(msg)


def type_param_bound_error(
    type_subst: dict[str, TpyType],
    bounds: dict[str, TpyType],
    func_name: str,
    satisfies_bound,
    substitute,
    detail: str = "",
) -> str | None:
    """Why a resolved type arg breaks its type parameter's bound, or None.

    A bound may name sibling type parameters (`[R, T: Proto[R]]`); `substitute`
    resolves them against `type_subst` before the conformance check so `T`'s
    bound is checked as `Proto[<resolved R>]`, not the raw `Proto[R]`.

    `detail` is appended to the message by a call site whose type args are
    INFERRED from the argument slots, where the reason the bound has to hold is
    not obvious from the source.
    """
    for param_name, type_arg in type_subst.items():
        if param_name in bounds:
            bound = substitute(bounds[param_name], type_subst)
            if not satisfies_bound(type_arg, bound):
                return (
                    f"Type argument '{type_arg}' does not satisfy bound '{bound}' "
                    f"for type parameter '{param_name}' of '{func_name}'"
                    f"{detail}"
                    f"{_send_sync_bound_detail(bound, type_arg)}")
    return None


def _send_sync_bound_detail(bound: TpyType, type_arg: TpyType) -> str:
    """Why-not chain appended to a failing `T: Send` / `T: Sync` bound."""
    qname = bound.qualified_name() if isinstance(bound, NominalType) else None
    if qname not in qnames.SEMA_ONLY_MARKER_PROTOCOLS:
        return ""
    send = qname == qnames.SEND
    chain = why_not_send(type_arg) if send else why_not_sync(type_arg)
    return f"\n{render_chain(chain, send)}" if chain is not None else ""
