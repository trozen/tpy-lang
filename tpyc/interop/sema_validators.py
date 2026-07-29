"""Per-module `@export` boundary validation run on the semantic context.

These are sema-phase checks (located diagnostics via the context's
diagnostic sink), invoked per ext_module after body analysis. The bodies
live here beside the shared shape predicates (`export_shape.py`) and the
glue emitter (`extension.py`) so a check added to one validation home is
visible from its siblings; `SemanticAnalyzer` keeps thin delegating
methods passing its `ctx`.
"""
from typing import TYPE_CHECKING

from ..diagnostics import SemanticError
from ..parse.nodes import RecordLinkage, TpyReturn, walk_body_stmts
from ..type_def_registry import (
    _boundary_inner, is_bool_type, is_dict, is_exposed_class,
    is_fixed_int_type, is_function_boundary_marshallable,
    is_internal_boundary_field, is_list, is_set, is_str_type,
    is_str_view_type, type_def_of,
)
from ..typesys import ReadonlyType, RefType
from .export_shape import (
    EXPORT_CLASS_REPR_STR_DUNDERS as _EXPORT_CLASS_REPR_STR_DUNDERS,
    EXPORT_CLASS_COMPARE_DUNDERS as _EXPORT_CLASS_COMPARE_DUNDERS,
    EXPORT_CLASS_BINARY_ARITH_DUNDERS as _EXPORT_CLASS_BINARY_ARITH_DUNDERS,
    EXPORT_CLASS_REFLECTED_ARITH_DUNDERS as _EXPORT_CLASS_REFLECTED_ARITH_DUNDERS,
    EXPORT_CLASS_UNARY_ARITH_DUNDERS as _EXPORT_CLASS_UNARY_ARITH_DUNDERS,
    EXPORT_CLASS_INPLACE_ARITH_DUNDERS as _EXPORT_CLASS_INPLACE_ARITH_DUNDERS,
    EXPORT_CLASS_GETITEM_DUNDERS as _EXPORT_CLASS_GETITEM_DUNDERS,
    EXPORT_CLASS_SETITEM_DUNDERS as _EXPORT_CLASS_SETITEM_DUNDERS,
    EXPORT_CLASS_DELITEM_DUNDERS as _EXPORT_CLASS_DELITEM_DUNDERS,
    EXPORT_CLASS_CONTAINS_DUNDERS as _EXPORT_CLASS_CONTAINS_DUNDERS,
    EXPORT_CLASS_ITER_DUNDERS as _EXPORT_CLASS_ITER_DUNDERS,
    EXPORT_CLASS_NEXT_DUNDERS as _EXPORT_CLASS_NEXT_DUNDERS,
    EXPORT_CLASS_SUPPORTED_DUNDERS as _EXPORT_CLASS_SUPPORTED_DUNDERS,
    boundary_alias_records, export_method_shape_error,
    nocopy_borrow_return_error, unsupported_slot_param_form,
    view_safe_borrow_returns,
)

if TYPE_CHECKING:
    from ..parse import TpyModule
    from ..sema.context import SemanticContext
    from ..typesys import RecordInfo


def warn_export_class_return_alias(ctx: 'SemanticContext',
                                   module: 'TpyModule') -> None:
    """In an ext_module, warn when an @export function or an exposed class's
    method returns an exposed class or a list/dict/set *by borrow* --
    declared `-> Cls` / `-> list[T]` (sema lowers these to RefType) or
    `-> readonly[...]` (ReadonlyType-topped), not `-> Own[...]`. A class borrow return crosses aliasing when the
    returned reference is the receiver, a parameter (identity: the glue
    address-matches it against the boundary-crossed objects in scope and
    hands back the ORIGINAL PyObject), or a never-reassigned field of one
    (borrow view: a PyObject aliasing the live field with a keepalive ref
    on its holder) -- a body whose every return site is one of those
    shapes is not flagged (the shared view_safe_borrow_returns
    classifier; the glue emits from the same answer). Any other source
    (a reassignable field, a module global) has no live-object-preserving
    path, so it copies a caller-visible object into a fresh PyObject:
    identity (`is`) and write-through aliasing are not preserved there.
    A list/dict/set borrow return always copies (the container was
    copied IN, so no PyObject backs it). Returning `Own[...]` -- a
    freshly constructed or copy()'d owned value -- is a distinct object
    on both sides and is the acknowledged form (so it is not flagged).
    tuple is a value type (never RefType-lowered) and str crosses as a
    value; a borrow-form `bytes` return predates this warning and stays
    unflagged for now (tracked in TODO.md).
    """
    if not module.directives.ext_module:
        return

    def collect_returns(body) -> 'list[TpyReturn]':
        found: list[TpyReturn] = []
        walk_body_stmts(
            body, lambda _e: None,
            lambda s: found.append(s)
            if isinstance(s, TpyReturn) and s.value is not None else None)
        return found

    def first_return(body) -> 'TpyReturn | None':
        found = collect_returns(body)
        return found[0] if found else None

    def alias_records_of(params, rec_info=None) -> 'dict[str, RecordInfo]':
        return boundary_alias_records(params, ctx.registry, rec_info)

    def warn_if_borrow_return(fn, label: str,
                              alias_records: 'dict') -> None:
        """`alias_records` = name -> RecordInfo of the candidates the
        GLUE threads at this emit site (exposed-class params, plus
        `self` for method-family sites -- every marshalled-return emit
        site threads them: methods, free fns, property getters, and the
        dunder slots). Suppression and the glue's view emission share
        ONE classifier (view_safe_borrow_returns), so a suppressed
        warning always has a runtime aliasing path.

        Borrow form has two spellings: `-> Cls` lowers to RefType, but
        `-> readonly[Cls]` stays ReadonlyType-topped (make_ref no-ops
        on it) -- both cross identically (identity/view when provable,
        copy otherwise), so both must enter the classification."""
        if not isinstance(fn.return_type, (RefType, ReadonlyType)):
            return
        if is_exposed_class(fn.return_type):
            info = ctx.registry.get_record_for_type(
                _boundary_inner(fn.return_type))
            # A @nocopy class returned by reference is a hard error (the
            # copy is deleted), reported by the validator -- don't also warn.
            if info is not None and info.is_nocopy:
                return
            # Every return site a bare candidate name (identity) or a
            # view-safe field access of one (borrow view) -> nothing is
            # copied, nothing to warn about. (Conservative: a ternary or
            # a local alias of self still warns even though the runtime
            # address match preserves identity there too.)
            if (info is not None
                    and view_safe_borrow_returns(
                        fn, alias_records, info, ctx.registry)):
                return
            cls_name = getattr(fn.return_type.wrapped, 'name', '?')
            if info is not None and info.is_value_type:
                # A value class has no identity/view path at all (it
                # crosses by copy from every source), so the residual
                # wording's self/param identity advice would lie here.
                ctx.warning(
                    f"{label}: returns exposed value-type class "
                    f"'{cls_name}' by reference, and a value class "
                    f"always crosses the CPython boundary as a copy -- "
                    f"the copy is a new object (identity and "
                    f"write-through aliasing are not preserved, even "
                    f"for `return self`); return Own[{cls_name}] to "
                    f"make the copy explicit",
                    first_return(fn.body))
                return
            ctx.warning(
                f"{label}: returns exposed class '{cls_name}' by "
                f"reference from a source with no live object behind it "
                f"on at least one return path (not `self`/a parameter, "
                f"nor a never-reassigned field of one), so the instance "
                f"is copied across the CPython boundary there -- the copy "
                f"is a new object (identity and write-through aliasing "
                f"are not preserved; a copied derived instance is sliced "
                f"to the declared type); `self`/parameter returns cross "
                f"as the original object, never-reassigned field returns "
                f"as an aliasing view; return Own[...] to make the copy "
                f"explicit",
                first_return(fn.body))
            return
        inner = _boundary_inner(fn.return_type)
        kind = ("list" if is_list(inner) else
                "dict" if is_dict(inner) else
                "set" if is_set(inner) else None)
        if kind is None:
            return
        ctx.warning(
            f"{label}: returns a {kind} by reference, so it is copied "
            f"across the CPython boundary -- the result is a new object; "
            f"mutations to it are not visible on this side (write-through "
            f"aliasing is not preserved); return Own[...] to make the "
            f"copy explicit",
            first_return(fn.body))

    for func in module.functions:
        if func.exposed_to_host:
            # A free function has no receiver: only its params are glue
            # candidates (a module global named `self` is NOT one).
            warn_if_borrow_return(func, f"@export function '{func.name}'",
                                  alias_records_of(func.params))
    for record in module.records:
        if not record.exposed_to_host:
            continue
        rec_info = ctx.registry.get_record(record.name)
        for m in record.methods:
            is_dunder = m.name.startswith("__") and m.name.endswith("__")
            if m.name == "__init__" or (
                    is_dunder and m.name not in _EXPORT_CLASS_SUPPORTED_DUNDERS):
                continue  # only __init__ + supported dunders are exposed
            if m.name in _EXPORT_CLASS_INPLACE_ARITH_DUNDERS:
                # An in-place dunder's nb_inplace_* wrapper hands back the
                # SAME self PyObject (Py_IncRef; no instance_to_py copy),
                # so its by-reference `-> Cls` return (required shape --
                # CONST_PARAMS_METHODS rejects any other) never loses
                # identity; the borrow-return warning doesn't apply.
                continue
            if m.is_property_setter or (m.is_property_getter
                                        and m.is_readonly):
                # Setter returns None; the const getter clone shares the
                # mutable clone's return (one warning per property).
                continue
            if m.is_property_getter and is_internal_boundary_field(m.name):
                # A `_`-named property never crosses as an attribute, so
                # there is no boundary copy to warn about. (`_`-named
                # METHODS still cross -- the `_` rule gates attribute
                # sites only -- so this skip is getter-specific.)
                continue
            if m.name in (_EXPORT_CLASS_SETITEM_DUNDERS
                          | _EXPORT_CLASS_DELITEM_DUNDERS):
                # The mp_ass_subscript slot returns a status int; the
                # method's own return value is DISCARDED -- nothing
                # crosses, so nothing is copied.
                continue
            what = "property" if m.is_property_getter else "method"
            # Every marshalled-return dunder slot threads the same
            # candidates as a method wrapper (the branch's receiver +
            # its exposed-class operands), so dunders get the method
            # treatment: identity/view-safe bodies suppress, the
            # residue warns.
            warn_if_borrow_return(
                m, f"exposed class '{record.name}' {what} '{m.name}'",
                alias_records_of(m.params, rec_info))

def validate_export_class_dunders(ctx: 'SemanticContext',
                                  module: 'TpyModule') -> None:
    """In an ext_module, validate an @export class's repr/str/eq/ne/lt/le/
    gt/ge/hash dunders before codegen wires them into CPython type slots
    (Py_tp_repr/Py_tp_str/Py_tp_richcompare/Py_tp_hash). The decorator/
    kind/arg-form rejections are shared with the plain-method validator via
    `export_shape`; the rest is the per-dunder marshalling shape the slot
    wrapper needs: a comparison
    dunder's other-operand type must cross the boundary, __hash__ must
    return a fixed-width int (not BigInt -- no defined truncation rule
    yet), repr/str must return str.
    """
    if not module.directives.ext_module:
        return
    for record in module.records:
        if not record.exposed_to_host:
            continue
        for m in record.methods:
            if m.name not in _EXPORT_CLASS_SUPPORTED_DUNDERS:
                continue
            loc = m.loc
            if record.linkage != RecordLinkage.DEFAULT:
                raise SemanticError(
                    f"exposed class '{record.name}': '{m.name}' cannot be "
                    f"declared on an @native record", loc)
            # __next__ is implicitly @error_return(StopIteration) (the
            # parser default -- see parse/parser.py), so it is exempted
            # from the blanket @error_return reject: the container-slot
            # wrapper unwraps the resulting std::expected itself instead
            # of the normal boundary catch.
            shape = export_method_shape_error(
                m, allow_error_return=m.name in _EXPORT_CLASS_NEXT_DUNDERS)
            if shape is not None:
                raise SemanticError(
                    f"exposed class '{record.name}': {shape}", loc)
            # A defaulted/starred operand reaches codegen silently otherwise
            # -- e.g. a defaulted compare operand (`other: Vec2 = None`)
            # passes the count/type checks below (a default changes neither
            # the arity nor the marshal a param needs) and then fails the
            # C++ build with `could not convert 'nullptr' to 'const Vec2&'`.
            form = unsupported_slot_param_form(m)
            if form is not None:
                raise SemanticError(
                    f"exposed class '{record.name}': '{m.name}': {form}",
                    loc)
            params = [(pn, pt) for pn, pt in m.params if pn != "self"]
            takes_two = _EXPORT_CLASS_SETITEM_DUNDERS
            takes_one = (_EXPORT_CLASS_COMPARE_DUNDERS
                         | _EXPORT_CLASS_BINARY_ARITH_DUNDERS
                         | _EXPORT_CLASS_REFLECTED_ARITH_DUNDERS
                         | _EXPORT_CLASS_INPLACE_ARITH_DUNDERS
                         | _EXPORT_CLASS_GETITEM_DUNDERS
                         | _EXPORT_CLASS_DELITEM_DUNDERS
                         | _EXPORT_CLASS_CONTAINS_DUNDERS)
            if m.name in takes_two:
                if len(params) != 2:
                    raise SemanticError(
                        f"exposed class '{record.name}': '{m.name}' "
                        f"crossing the CPython boundary must take "
                        f"exactly two parameters beyond self", loc)
                for pname, ptype in params:
                    if not is_function_boundary_marshallable(ptype, False):
                        raise SemanticError(
                            f"exposed class '{record.name}': '{m.name}' "
                            f"parameter '{pname}' cannot cross the "
                            f"CPython boundary", loc)
            elif m.name in takes_one:
                if len(params) != 1:
                    raise SemanticError(
                        f"exposed class '{record.name}': '{m.name}' "
                        f"crossing the CPython boundary must take exactly "
                        f"one parameter beyond self", loc)
                other_name, other_type = params[0]
                if not is_function_boundary_marshallable(other_type, False):
                    raise SemanticError(
                        f"exposed class '{record.name}': '{m.name}' "
                        f"parameter '{other_name}' cannot cross the "
                        f"CPython boundary", loc)
                # Comparison dunders share ONE richcompare wrapper with a
                # single type-guard-then-switch shape (unlike arithmetic
                # operators, which marshal each op's operand per its own
                # declared type): the wrapper only accepts `other` being
                # an instance of THIS record, so the declared type must
                # match -- a differently-typed operand would pass this
                # marshallability check but the compiled richcompare slot
                # would reject it at the type guard (or, if the operand
                # were some other exposed class, produce a C++ type
                # mismatch at the call site).
                if m.name in _EXPORT_CLASS_COMPARE_DUNDERS:
                    own_info = ctx.registry.get_record(record.name)
                    other_td = type_def_of(_boundary_inner(other_type))
                    if own_info is None or other_td is None \
                            or other_td.record is not own_info:
                        raise SemanticError(
                            f"exposed class '{record.name}': '{m.name}' "
                            f"parameter '{other_name}' must be "
                            f"'{record.name}' -- a comparison dunder "
                            f"crossing the CPython boundary only "
                            f"supports comparing against the record's "
                            f"own type", loc)
            else:  # repr/str/hash/unary-arith/len/iter/next: no params
                if params:
                    raise SemanticError(
                        f"exposed class '{record.name}': '{m.name}' "
                        f"crossing the CPython boundary must take no "
                        f"parameters beyond self", loc)

            if m.name in (_EXPORT_CLASS_COMPARE_DUNDERS
                          | _EXPORT_CLASS_CONTAINS_DUNDERS):
                if not is_bool_type(m.return_type):
                    raise SemanticError(
                        f"exposed class '{record.name}': '{m.name}' "
                        f"crossing the CPython boundary must return bool",
                        loc)
            elif m.name in _EXPORT_CLASS_REPR_STR_DUNDERS:
                if not (is_str_type(m.return_type)
                        or is_str_view_type(m.return_type)):
                    raise SemanticError(
                        f"exposed class '{record.name}': '{m.name}' "
                        f"crossing the CPython boundary must return "
                        f"str", loc)
            elif m.name in ("__hash__", "__len__"):
                if not is_fixed_int_type(m.return_type):
                    raise SemanticError(
                        f"exposed class '{record.name}': '{m.name}' "
                        f"crossing the CPython boundary must return a "
                        f"fixed-width integer type (not BigInt)", loc)
            elif m.name in (_EXPORT_CLASS_BINARY_ARITH_DUNDERS
                            | _EXPORT_CLASS_REFLECTED_ARITH_DUNDERS
                            | _EXPORT_CLASS_UNARY_ARITH_DUNDERS
                            | _EXPORT_CLASS_GETITEM_DUNDERS
                            | _EXPORT_CLASS_ITER_DUNDERS
                            | _EXPORT_CLASS_NEXT_DUNDERS):
                if not is_function_boundary_marshallable(m.return_type, False):
                    raise SemanticError(
                        f"exposed class '{record.name}': '{m.name}' "
                        f"return type cannot cross the CPython boundary",
                        loc)
                # Same @nocopy borrow-return reject the plain-method
                # validator applies -- these are the marshalled-return
                # slots whose fallback copies the instance out. In-place
                # dunders are exempt by construction (their wrapper hands
                # back the same self PyObject, no copy).
                nocopy_err = nocopy_borrow_return_error(
                    m.return_type, ctx.registry)
                if nocopy_err is not None:
                    raise SemanticError(
                        f"exposed class '{record.name}': '{m.name}' "
                        f"return {nocopy_err}", loc)
            # Inplace dunders (__iadd__, ...) already have a general,
            # non-export-specific rule that they return self (the record
            # type) -- registration.py's CONST_PARAMS_METHODS check -- so
            # no extra return-type validation is needed here.
            # __setitem__/__delitem__ return values are discarded (the
            # mp_ass_subscript slot returns a status int, not the TPy
            # method's own return), so no return-type check applies.

def warn_export_class_unexposed_dunders(ctx: 'SemanticContext',
                                        module: 'TpyModule') -> None:
    """In an ext_module, warn when an exposed class defines a dunder not
    in `_EXPORT_CLASS_SUPPORTED_DUNDERS` (other than __init__). Such a
    dunder is silently absent from the host CPython type rather than
    rejected, so surface it -- otherwise the gap reads as "works" until
    someone calls it from Python.
    """
    if not module.directives.ext_module:
        return
    for record in module.records:
        if not record.exposed_to_host:
            continue
        for m in record.methods:
            if m.name != "__init__" and m.name.startswith("__") \
                    and m.name.endswith("__") \
                    and m.name not in _EXPORT_CLASS_SUPPORTED_DUNDERS:
                ctx.warning(
                    f"exposed class '{record.name}': '{m.name}' is not "
                    f"exposed to CPython; the host type will not have it",
                    m)
