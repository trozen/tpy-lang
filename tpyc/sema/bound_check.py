"""Class-shadowed method type-param bound enforcement.

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

from ..typesys import TpyType, FunctionInfo


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
    tp, bound, concrete = violation
    raise error_fn(
        f"Method '{method_info.name}' requires type parameter '{tp}' to satisfy "
        f"'{bound}', but '{concrete}' does not conform",
        loc_node,
    )
