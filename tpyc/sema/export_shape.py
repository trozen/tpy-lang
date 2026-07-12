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

if TYPE_CHECKING:
    from ..parse.nodes import TpyFunction

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
