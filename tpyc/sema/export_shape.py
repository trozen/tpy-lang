"""Shared shape validation for the CPython `@export` boundary.

Two homes reject the method/dunder shapes the extension glue cannot emit: the
exposed-class dunder validator (sema, `analyzer._validate_export_class_dunders`)
and the exposed-class body/free-function validator (`compiler._validate_exposed_
class` / `_validate_ext_module_exports`). They must agree on WHICH decorator,
kind, and argument forms don't cross -- when the two implementations drifted, an
`async` dunder slipped the home missing the async check and an `@error_return`
method slipped the home missing the error-return check, each reaching codegen and
failing the `.so` build with an opaque C++ template error instead of a located
diagnostic. Detection lives here so the set can't diverge; each home keeps its
own message prefix (`exposed class 'R':` vs `@export class 'R':`).

The supported-dunder catalog derives from the same `modules/defs.py` operator
tables the codegen slot emitter (`codegen_cpp/extension.py`) derives from, so the
validator's supported set stays in lockstep with what codegen actually emits.
"""
from typing import TYPE_CHECKING
from ..modules import (
    BINOP_TO_METHOD, BINOP_TO_RMETHOD, AUGOP_TO_IMETHOD, UNARYOP_TO_METHOD,
)
from ..parse.nodes import (
    TpyCoerce, TpyFieldAccess, TpyName, TpyReturn,
)
from ..type_def_registry import _boundary_inner
from ..typesys import NominalType, ReadonlyType
from .expressions import _walk_body_stmts

if TYPE_CHECKING:
    from ..parse.nodes import TpyExpr, TpyFunction
    from ..typesys import RecordInfo

# The 6 rich-comparison dunders share BINOP_TO_METHOD with the 12 binary
# arithmetic ops but are semantically distinct (one shared richcompare slot,
# operand pinned to the record's own type), so they are named directly and
# subtracted out; every other set is derived from the defs.py tables.
EXPORT_CLASS_REPR_STR_DUNDERS = frozenset({"__repr__", "__str__"})
EXPORT_CLASS_COMPARE_DUNDERS = frozenset(
    {"__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__"})
EXPORT_CLASS_BINARY_ARITH_DUNDERS = (
    frozenset(BINOP_TO_METHOD.values()) - EXPORT_CLASS_COMPARE_DUNDERS)
EXPORT_CLASS_REFLECTED_ARITH_DUNDERS = frozenset(BINOP_TO_RMETHOD.values())
EXPORT_CLASS_UNARY_ARITH_DUNDERS = frozenset(UNARYOP_TO_METHOD.values())
EXPORT_CLASS_INPLACE_ARITH_DUNDERS = frozenset(AUGOP_TO_IMETHOD.values())
EXPORT_CLASS_ARITH_DUNDERS = (
    EXPORT_CLASS_BINARY_ARITH_DUNDERS | EXPORT_CLASS_REFLECTED_ARITH_DUNDERS
    | EXPORT_CLASS_UNARY_ARITH_DUNDERS | EXPORT_CLASS_INPLACE_ARITH_DUNDERS)
EXPORT_CLASS_LEN_DUNDERS = frozenset({"__len__"})
EXPORT_CLASS_GETITEM_DUNDERS = frozenset({"__getitem__"})
EXPORT_CLASS_SETITEM_DUNDERS = frozenset({"__setitem__"})
EXPORT_CLASS_DELITEM_DUNDERS = frozenset({"__delitem__"})
EXPORT_CLASS_CONTAINS_DUNDERS = frozenset({"__contains__"})
EXPORT_CLASS_ITER_DUNDERS = frozenset({"__iter__"})
EXPORT_CLASS_NEXT_DUNDERS = frozenset({"__next__"})
EXPORT_CLASS_CONTAINER_DUNDERS = (
    EXPORT_CLASS_LEN_DUNDERS | EXPORT_CLASS_GETITEM_DUNDERS
    | EXPORT_CLASS_SETITEM_DUNDERS | EXPORT_CLASS_DELITEM_DUNDERS
    | EXPORT_CLASS_CONTAINS_DUNDERS | EXPORT_CLASS_ITER_DUNDERS
    | EXPORT_CLASS_NEXT_DUNDERS)
EXPORT_CLASS_SUPPORTED_DUNDERS = (
    EXPORT_CLASS_REPR_STR_DUNDERS | EXPORT_CLASS_COMPARE_DUNDERS
    | {"__hash__"} | EXPORT_CLASS_ARITH_DUNDERS
    | EXPORT_CLASS_CONTAINER_DUNDERS)


def _peel_coerce(expr: 'TpyExpr') -> 'TpyExpr':
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    return expr


def boundary_alias_records(params, registry,
                           rec_info: 'RecordInfo | None' = None
                           ) -> 'dict[str, RecordInfo]':
    """name -> RecordInfo of the glue's boundary-crossed candidates at an
    emit site: the exposed-class-typed params, plus `self` when a receiver
    record is given. The ONE builder both ends of the identity/view
    contract use (sema's warning pass and the glue's view emission), so
    the candidate sets can't drift."""
    out: 'dict[str, RecordInfo]' = {}
    if rec_info is not None:
        out["self"] = rec_info
    for n, t in params:
        if n == "self":
            continue
        cinfo = registry.get_record_for_type(_boundary_inner(t))
        if cinfo is not None:
            out[n] = cinfo
    return out


def view_safe_attr_source(expr: 'TpyExpr',
                          alias_records: 'dict[str, RecordInfo]',
                          return_info: 'RecordInfo',
                          registry) -> bool:
    """Whether a return-site value is a bare `<name>.<field>` access the
    glue's borrow-view fallback hands back as an aliasing view: the base
    name is a boundary-crossed candidate (self / an exposed-class param),
    and the field is a never-rebound reference-class field declared as
    EXACTLY the return class (an upcast field view would lie about the
    dynamic type, so it stays on the copy path; a rebindable field's view
    would alias the storage SLOT through the rebind, so it does too)."""
    expr = _peel_coerce(expr)
    if not isinstance(expr, TpyFieldAccess):
        return False
    base = _peel_coerce(expr.obj)
    if not isinstance(base, TpyName) or base.name not in alias_records:
        return False
    holder = alias_records[base.name]
    for rec in (holder, *registry.iter_ancestor_records(holder)):
        fld = next((f for f in rec.fields if f.name == expr.field), None)
        if fld is not None:
            declarer = rec
            break
    else:
        return False
    if expr.field in declarer.fields_rebound_outside_init:
        return False
    ftype = fld.type
    while isinstance(ftype, ReadonlyType):
        ftype = ftype.wrapped
    if not isinstance(ftype, NominalType):
        return False
    finfo = registry.get_record(ftype.name)
    return (finfo is return_info and not return_info.is_value_type)


def exposed_view_field(fld, declarer_info: 'RecordInfo',
                       registry) -> 'RecordInfo | None':
    """The exposed reference-class RecordInfo a PUBLIC field crosses as a
    READ-ONLY borrow-view getset (attribute reads alias the live field;
    attribute writes raise -- a Python-side rebind would defeat the
    never-reassigned gate), or None when the field stays on its other
    path (scalar getset, value-type copy-out, or the located reject for a
    reassignable/@nocopy reference-class field). Consulted by BOTH the
    exposed-class validator and the getset emit -- one answer."""
    ftype = fld.type
    while isinstance(ftype, ReadonlyType):
        ftype = ftype.wrapped
    if not isinstance(ftype, NominalType):
        return None
    finfo = registry.get_record(ftype.name)
    if (finfo is None or not finfo.exposed_to_host or finfo.is_value_type
            or finfo.is_nocopy):
        return None
    if fld.name in declarer_info.fields_rebound_outside_init:
        return None
    return finfo


def view_safe_borrow_returns(fn: 'TpyFunction',
                             alias_records: 'dict[str, RecordInfo]',
                             return_info: 'RecordInfo',
                             registry) -> bool:
    """Both ends of the identity/view contract consult this ONE classifier:
    sema suppresses the borrow-return copy warning exactly when it returns
    True, and the glue emits the borrow-view fallback (address-range owner
    scan + borrow_to_py) for the same functions -- so a suppressed warning
    always has a runtime aliasing path behind it, and a warned body never
    silently aliases.

    True when every return site is either a bare name in `alias_records`
    (identity path: the original PyObject crosses back) or a view-safe
    field access of such a name (borrow-view path)."""
    returns: list[TpyReturn] = []
    _walk_body_stmts(
        fn.body, lambda _e: None,
        lambda s: returns.append(s)
        if isinstance(s, TpyReturn) and s.value is not None else None)
    if not returns:
        return False
    for r in returns:
        src = _peel_coerce(r.value)
        if isinstance(src, TpyName) and src.name in alias_records:
            continue
        if not view_safe_attr_source(r.value, alias_records, return_info,
                                     registry):
            return False
    return True


def unsupported_boundary_param_form(fn: 'TpyFunction') -> 'str | None':
    """The argument forms the glue's keyword-aware unpack cannot cross (only
    plain positional-or-keyword params marshal): defaults (PyArg unpack has no
    optional slot), *args/**kwargs (dropped), positional-only, keyword-only.
    Returns the message tail for the first form present, else None."""
    if fn.vararg_name is not None:
        return "*args is not supported at the CPython boundary yet"
    if fn.kwarg_name is not None:
        return "**kwargs is not supported at the CPython boundary yet"
    if any(d is not None for d in (fn.defaults or [])):
        return ("default parameter values are not supported at the CPython "
                "boundary yet (every parameter must be required)")
    if fn.num_posonly_params:
        return ("positional-only parameters (/) are not supported at the "
                "CPython boundary yet")
    if fn.keyword_only_start is not None:
        return ("keyword-only parameters (*) are not supported at the "
                "CPython boundary yet")
    return None


def export_method_shape_error(fn: 'TpyFunction', *, allow_error_return: bool = False,
                              allow_property: bool = False) -> 'str | None':
    """The decorator/kind/async forms a method or dunder cannot take to cross
    the CPython boundary as a PyType slot or PyMethodDef wrapper. Returns a
    message tail (`'name' cannot ...`) for the first violated form, else None.
    Argument forms are a separate axis -- see `unsupported_boundary_param_form`.

    `allow_error_return` exempts the one dunder whose slot wrapper unwraps the
    std::expected itself (`__next__`, implicitly @error_return(StopIteration));
    every other callable is rejected, since the plain method/free-function glue
    marshals the raw C++ return with no expected-unwrap step.

    `allow_property` exempts property accessors: an exposed class's @property
    crosses as a computed getset (its accessors still need every OTHER check
    here); everywhere else -- free functions, dunders -- a property is rejected.
    """
    if fn.is_staticmethod:
        return f"'{fn.name}' cannot be a @staticmethod"
    if (fn.is_property_getter or fn.is_property_setter) and not allow_property:
        return f"'{fn.name}' cannot be a @property"
    if fn.is_overload_stub:
        return f"'{fn.name}' cannot be @overload"
    if fn.type_params:
        return (f"'{fn.name}' cannot be generic (a template can't cross the "
                f"CPython boundary, which needs one concrete method)")
    if fn.is_async:
        return (f"'{fn.name}' cannot be async (a coroutine frame can't cross "
                f"the CPython boundary; the wrapper needs a plain return value)")
    if fn.is_generator:
        return f"'{fn.name}' cannot be a generator (no `yield` in body)"
    if fn.error_return and not allow_error_return:
        return f"'{fn.name}' cannot use @error_return"
    return None
