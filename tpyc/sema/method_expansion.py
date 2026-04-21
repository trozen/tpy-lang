"""Method expansion sema pass.

Runs between `_resolve_pending_type_refs` and `register_enum` on the
module, and owns four transformations for record methods (free functions
are not expanded here):

1. Self-flag derivation -- walks `method.self_annotation` to set
   `is_consuming` (`Own[Self]`), `auto_own` (`AutoOwnType(Self)`),
   `auto_readonly` (`AutoReadonlyType(Self)`).
2. Self-annotation validation -- raises `SemanticError` for
   `Own[Self]` / `auto_own[Self]` / `auto_readonly[Self]` on
   `__init__` / `__del__`, plus the `@readonly` / `@auto_readonly`
   combinability checks.
3. Wrapping -- applies `AutoReadonlyType` to eligible params when the
   method carries `@auto_readonly`, detects per-param
   `auto_readonly[T]` to set the flag, and wraps the first non-self
   param of a `@property` setter with `Own[T]` when non-value.
4. Cloning -- expands `auto_readonly` methods into mutable + const
   overload pairs (`strip_auto_readonly` / `apply_auto_readonly`) and
   `auto_own` methods into borrowing + consuming pairs (`strip_auto_own`
   / `apply_auto_own`).  Clones replace the original entry in
   `record.methods` so registration sees the expanded list.

Parser state consumed here:
  - `TpyFunction.self_annotation`: resolved self type.
  - `TpyFunction.has_auto_readonly_decorator`: true iff `@auto_readonly`
    was attached.  Cleared on every clone once expanded.
  - `TpyFunction.auto_readonly`: pre-set to True for `@property` getters
    by `_parse_class` so cloning covers getters uniformly.
"""

from __future__ import annotations

import copy
import dataclasses
from typing import Iterator

from ..parse import TpyFunction, TpyRecord
from ..typesys import (
    AutoOwnType,
    AutoReadonlyType,
    OwnType,
    ReadonlyType,
    SelfType,
    apply_auto_own,
    apply_auto_readonly,
    has_auto_readonly,
    strip_auto_own,
    strip_auto_readonly,
)
from .diagnostics import SemanticError


_SELF_BARRED_METHODS = ("__init__", "__del__")


def expand_methods_for_record(record: TpyRecord) -> None:
    """Expand methods on a single record in place.

    Safe to call after macros have added methods: `_expand_one` is
    idempotent on already-expanded clones (their `self_annotation` is
    cleared and the transient `auto_readonly` / `auto_own` flags are
    already False), so methods added by the module-level run are left
    untouched when the per-record run visits them again.
    """
    record.methods = list(_expand_record_methods(record))


def _expand_record_methods(record: TpyRecord) -> Iterator[TpyFunction]:
    for method in record.methods:
        yield from _expand_one(method)


def _expand_one(method: TpyFunction) -> list[TpyFunction]:
    """Expand a single method into one or more TpyFunction entries.

    Order matters:
      1. Self-flag derivation -- populates `is_consuming` / `auto_own` /
         `auto_readonly` from `self_annotation` before validation reads
         the combined state.
      2. Validation -- runs against freshly-derived flags.
      3. Wrapping -- applies AutoReadonlyType / Own[T] mutations to
         params. Must run before cloning because the cloner walks the
         AutoReadonlyType markers to produce mutable / const halves.
      4. Cloning -- replaces the single method with its expansion.
    """
    if method.is_staticmethod or not method.is_method:
        # Not a regular method -- nothing to expand.
        return [method]

    _derive_self_flags(method)
    _validate_self_annotation(method)
    _apply_auto_readonly_decorator_wrapping(method)
    _detect_per_param_auto_readonly(method)
    _check_auto_readonly_generic_method(method)
    _apply_property_setter_wrapping(method)

    if method.auto_readonly:
        return _clone_auto_readonly(method)
    if method.auto_own:
        return _clone_auto_own(method)
    # Consuming method (Own[Self]) isn't cloned, but clear self_annotation
    # for symmetry with the cloned paths: a second `_expand_one` pass
    # shouldn't re-derive flags. Idempotent today either way, but keeps
    # the "expansion done -> self_annotation is None" invariant uniform.
    method.self_annotation = None
    return [method]


def _derive_self_flags(method: TpyFunction) -> None:
    stype = method.self_annotation
    if stype is None:
        return
    if isinstance(stype, OwnType) and isinstance(stype.wrapped, SelfType):
        method.is_consuming = True
    elif isinstance(stype, AutoOwnType) and isinstance(stype.wrapped, SelfType):
        method.auto_own = True
    elif isinstance(stype, AutoReadonlyType) and isinstance(stype.wrapped, SelfType):
        method.auto_readonly = True


def _validate_self_annotation(method: TpyFunction) -> None:
    """Raise SemanticError for invalid self annotations.

    Includes the catch-all shape check that used to live in the parser:
    only `Own[Self]`, `auto_own[Self]`, and `auto_readonly[Self]` are
    accepted on `self`.
    """
    stype = method.self_annotation
    if stype is None:
        return

    shape_ok = (
        isinstance(stype, (OwnType, AutoOwnType, AutoReadonlyType))
        and isinstance(stype.wrapped, SelfType)
    )
    if not shape_ok:
        raise SemanticError(
            f"Only 'Own[Self]', 'auto_own[Self]', or 'auto_readonly[Self]' "
            f"is allowed as a type annotation for 'self', got '{stype}'",
            loc=method.loc,
        )

    if isinstance(stype, OwnType) and isinstance(stype.wrapped, SelfType):
        if method.name in _SELF_BARRED_METHODS:
            raise SemanticError(
                f"Own[Self] is not allowed on '{method.name}'",
                loc=method.loc,
            )
        if method.is_readonly:
            raise SemanticError(
                f"Own[Self] cannot be combined with @readonly on method "
                f"'{method.name}'",
                loc=method.loc,
            )
        return

    if isinstance(stype, AutoOwnType) and isinstance(stype.wrapped, SelfType):
        if method.name in _SELF_BARRED_METHODS:
            raise SemanticError(
                f"auto_own[Self] is not allowed on '{method.name}'",
                loc=method.loc,
            )
        if method.is_readonly:
            raise SemanticError(
                f"auto_own[Self] cannot be combined with @readonly on method "
                f"'{method.name}'",
                loc=method.loc,
            )
        return

    if (isinstance(stype, AutoReadonlyType)
            and isinstance(stype.wrapped, SelfType)):
        if method.name in _SELF_BARRED_METHODS:
            raise SemanticError(
                f"auto_readonly[Self] is not allowed on '{method.name}'",
                loc=method.loc,
            )
        if method.is_readonly:
            raise SemanticError(
                f"auto_readonly[Self] cannot be combined with @readonly on "
                f"method '{method.name}'",
                loc=method.loc,
            )
        if method.has_auto_readonly_decorator:
            raise SemanticError(
                f"'self: auto_readonly[Self]' cannot be combined with the "
                f"@auto_readonly decorator on method '{method.name}'",
                loc=method.loc,
            )
        return


def _apply_auto_readonly_decorator_wrapping(method: TpyFunction) -> None:
    """Sugar for @auto_readonly: wrap all eligible params with AutoReadonlyType.

    The decorator also implies `method.auto_readonly`, so cloning picks it
    up even when the method takes no non-self params to wrap.
    """
    if not method.has_auto_readonly_decorator:
        return
    method.auto_readonly = True
    method.params = [
        (n,
         AutoReadonlyType(t)
         if (not t.is_value_type()
             and not isinstance(t, (AutoReadonlyType, ReadonlyType)))
         else t)
        for n, t in method.params
    ]


def _detect_per_param_auto_readonly(method: TpyFunction) -> None:
    """Per-param `auto_readonly[T]` annotations imply the auto_readonly flag."""
    if method.auto_readonly:
        return
    for _, ptype in method.params:
        if has_auto_readonly(ptype):
            method.auto_readonly = True
            return


def _check_auto_readonly_generic_method(method: TpyFunction) -> None:
    if method.auto_readonly and method.type_params:
        raise SemanticError(
            f"auto_readonly on methods with method-level type parameters "
            f"is not yet supported ('{method.name}')",
            loc=method.loc,
        )


def _apply_property_setter_wrapping(method: TpyFunction) -> None:
    """@property setter: first non-self param is an ownership transfer."""
    if not method.is_property_setter or not method.params:
        return
    pname, ptype = method.params[0]
    if not isinstance(ptype, OwnType) and not ptype.is_value_type():
        method.params[0] = (pname, OwnType(ptype))


def _clone_auto_readonly(method: TpyFunction) -> list[TpyFunction]:
    """Expand an auto_readonly method into mutable + const overloads.

    - Mutable: params/return stripped of AutoReadonlyType, is_readonly=False.
    - Const:   params/return with AutoReadonlyType -> ReadonlyType, is_readonly=True.

    AutoReadonlyType nodes mark where readonly is applied in the const
    overload. The @auto_readonly decorator path wraps all eligible params
    first, so both decorator-driven and per-param annotations converge
    on the same cloning logic.
    """
    mutable_params = [(n, strip_auto_readonly(t)) for n, t in method.params]
    const_params = [(n, apply_auto_readonly(t)) for n, t in method.params]
    mutable_return = strip_auto_readonly(method.return_type)
    const_return = apply_auto_readonly(method.return_type)
    mutable = dataclasses.replace(
        method,
        params=mutable_params,
        return_type=mutable_return,
        is_readonly=False,
        auto_readonly=False,
        has_auto_readonly_decorator=False,
        auto_readonly_params_resolved=True,
        is_auto_readonly_mutable_clone=True,
        # Clear the derivation source so a second expand pass (per-record
        # run after macros) doesn't re-derive auto_readonly and re-clone.
        self_annotation=None,
    )
    const = dataclasses.replace(
        method,
        params=const_params,
        return_type=const_return,
        body=copy.deepcopy(method.body),
        is_readonly=True,
        auto_readonly=False,
        has_auto_readonly_decorator=False,
        auto_readonly_params_resolved=True,
        defaults=copy.deepcopy(method.defaults),
        self_annotation=None,
    )
    return [mutable, const]


def _clone_auto_own(method: TpyFunction) -> list[TpyFunction]:
    """Expand a `self: auto_own[Self]` method into borrowing + consuming overloads.

    - Borrowing: is_consuming=False, return stripped of AutoOwnType.
    - Consuming: is_consuming=True, return with AutoOwnType -> OwnType.
    """
    borrowing_return = strip_auto_own(method.return_type)
    consuming_return = apply_auto_own(method.return_type)
    borrowing = dataclasses.replace(
        method,
        return_type=borrowing_return,
        is_consuming=False,
        auto_own=False,
        is_auto_own_borrowing_clone=True,
        self_annotation=None,
    )
    consuming = dataclasses.replace(
        method,
        return_type=consuming_return,
        body=copy.deepcopy(method.body),
        is_consuming=True,
        auto_own=False,
        defaults=copy.deepcopy(method.defaults),
        self_annotation=None,
    )
    return [borrowing, consuming]
