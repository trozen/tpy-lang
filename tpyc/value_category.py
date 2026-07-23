"""Shared expression value-category predicates (borrow-alias vs rvalue).

Both sema (to classify a local's binding as owned-movable vs borrow-alias)
and codegen (to render a local as an owned value vs a `T&` reference) must
answer the same question: "does this initializer produce a fresh rvalue, or
does it alias existing storage?" Keeping the answer in one place avoids the
divergence that lets sema mark a `C&` borrow-alias as movable -- which then
moves out of the alias and corrupts the source.

The `analyzer` argument is duck-typed: it only needs `get_expr_type(expr)`
and `registry`. Both the sema `AnalyzerContext` and the codegen
`SemanticAnalyzer` satisfy this.
"""

from typing import Any, Protocol

from .typesys import (
    FunctionInfo, TpyType, TypeParamRef, OwnType, OptionalType, UnionType,
    is_protocol_type, unwrap_ref_type,
)
from .parse import (
    TpyExpr, TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyDictLiteral, TpySetLiteral, TpyDictComprehension, TpySetComprehension,
    TpyGeneratorExpression, TpyCoerce, TpyBinOp, TpyUnaryOp, TpyMethodCall,
    TpySubscript, TpyCall, TpyName, TpyFieldAccess, TpyIfExpr,
)
from .type_def_registry import is_bool_type


def wants_move(t: TpyType) -> bool:
    """Whether moving a value of this type beats copying it: reference
    types always, value types only when the copy is expensive (String,
    BigInt, ...). For trivial types std::move is just noise."""
    return not t.is_value_type() or t.is_expensive_copy()


class ValueCategoryAnalyzer(Protocol):
    """The slice of sema/codegen state these predicates need. The sema
    `AnalyzerContext` and the codegen `SemanticAnalyzer` both satisfy it."""
    registry: Any

    def get_expr_type(self, expr: TpyExpr) -> 'TpyType | None': ...


# Container/generator-shaped expressions whose gen_expr emits a value
# (`vector<T>{...}`, `ordered_map<K, V>{...}`, generator state struct, ...)
# regardless of the target type. Distinct from pointer-emit rvalues
# (function calls returning T*, pointer-local names) which already yield
# stable pointer storage. Codegen sites initializing a pointer-form slot
# from an Optional source use this to decide whether to materialize a
# named slot before taking address.
_CONTAINER_LITERAL_NODES: tuple = (
    TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyDictLiteral, TpySetLiteral,
    TpyDictComprehension, TpySetComprehension,
    TpyGeneratorExpression,
)


def call_returns_cpp_ref(analyzer: ValueCategoryAnalyzer, fi: 'FunctionInfo | None') -> bool:
    """True if this function/method call returns a C++ lvalue reference (T&).

    A function/method returns T& iff its declared return is a concrete
    (non-generic) reference type that is not owned/nullable: copy is spelled
    `Own[T]`, nullability `Optional[T]`, an unresolved generic stays
    `TypeParamRef` (rendered val_or_ref_t<T>), and protocol returns are
    value-shaped concrete structs -- all of which use value semantics. This
    holds uniformly for user-defined AND `@native` record methods: a native
    binding declares its C++ return convention through the same `-> V` vs
    `-> Own[V]` contract, so no per-native special case is needed.

    Free `@native` functions still take value semantics -- the same `-> V`
    reference-return asymmetry exists for them but is not yet closed (see
    BUGS.md). Native record METHODS, by contrast, honor the contract and
    fall through to the shape check (so dict.setdefault / items aliasing
    holds).
    """
    if fi is None:
        return False
    # Free native functions default to value semantics (unknown C++ return
    # convention). Native METHODS are excluded: they honor the `-> V` /
    # `Own[V]` contract via the shape check below, like user methods -- so the
    # `not is_method` gate, not `is_native_import` alone, is what keeps
    # native-method aliasing.
    if fi.is_native_import and not fi.is_method:
        return False
    # Record constructors return rvalue temporaries, never C++ T&.
    if fi.is_constructor:
        return False
    rt = unwrap_ref_type(fi.return_type)
    return (rt is not None
            and not rt.is_value_type()
            and not isinstance(rt, (TypeParamRef, OwnType, OptionalType, UnionType))
            and not is_protocol_type(rt))


def is_rvalue_source(analyzer: ValueCategoryAnalyzer, expr: TpyExpr) -> bool:
    """Check if an expression produces an rvalue (a fresh value / temporary).

    Rvalues: constructor calls, Own[T] returns, literals, binop/unop results,
    field access on rvalue objects (member of temporary).
    Lvalues (borrow-aliases): variable names, field access on lvalues,
    subscript, function/method returning T&, ternary with two lvalue arms.
    """
    # Names are lvalues (either pointer-locals, params, or globals)
    if isinstance(expr, TpyName):
        return False
    # Field access: rvalue iff the object is rvalue (member of temporary)
    if isinstance(expr, TpyFieldAccess):
        return is_rvalue_source(analyzer, expr.obj)
    # Subscript into containers is an lvalue (returns T&).
    # Exceptions: slice calls (e.g. list_stepped_slice) and user-record
    # __getitem__ calls may return by value (e.g. a pointer-repr Optional
    # returns T*) -- both follow the resolved callee's convention.
    if isinstance(expr, TpySubscript):
        if expr.slice_function_info is not None:
            return not call_returns_cpp_ref(analyzer, expr.slice_function_info)
        if expr.getitem_function_info is not None:
            return not call_returns_cpp_ref(analyzer, expr.getitem_function_info)
        return False
    # Ternary: lvalue iff both arms are lvalues (C++ ternary with two lvalue
    # arms is itself an lvalue). Uses OR semantics: rvalue if either arm is
    # rvalue, since _gen_if_expr emits arms inline with no temp materialization.
    if isinstance(expr, TpyIfExpr):
        result_type = analyzer.get_expr_type(expr)
        if result_type and not result_type.is_value_type():
            return (is_rvalue_source(analyzer, expr.then_expr)
                    or is_rvalue_source(analyzer, expr.else_expr))
    # Logical and/or with operand-return semantics: rvalue temps are
    # materialized into named variables by _gen_logical_value, so the
    # result is only an rvalue when both operands are rvalues.
    if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
        result_type = analyzer.get_expr_type(expr)
        if not is_bool_type(result_type):
            return (is_rvalue_source(analyzer, expr.left)
                    and is_rvalue_source(analyzer, expr.right))
    # An arithmetic binop/unaryop resolved to a dunder follows the method's
    # return convention, like the method-call and subscript arms: a
    # borrow-returning `__add__`/`__neg__` aliases an operand, it does not
    # create a value.
    if isinstance(expr, TpyBinOp) and expr.resolved_binop is not None:
        return not call_returns_cpp_ref(analyzer, expr.resolved_binop.method)
    if isinstance(expr, TpyUnaryOp) and expr.resolved_unaryop is not None:
        return not call_returns_cpp_ref(analyzer, expr.resolved_unaryop.method)
    # Constructor calls, literals, ops are rvalues.
    if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                         TpyBoolLiteral, TpyNoneLiteral,
                         TpyBinOp, TpyUnaryOp)):
        return True
    if isinstance(expr, _CONTAINER_LITERAL_NODES):
        return True
    if isinstance(expr, TpyMethodCall):
        return not call_returns_cpp_ref(analyzer, expr.resolved_function_info)
    # Coercions: depends on inner expr
    if isinstance(expr, TpyCoerce):
        return is_rvalue_source(analyzer, expr.expr)
    # Function calls
    if isinstance(expr, TpyCall):
        # Expression callees -> rvalue
        if not isinstance(expr.func, TpyName):
            return True
        # Record constructors -> rvalue
        if analyzer.registry.get_record(expr.func_name):
            return True
        # Generic type constructors -> rvalue
        if expr.call_type is not None:
            return True
        if analyzer.registry.get_function(expr.func_name) is not None:
            return not call_returns_cpp_ref(analyzer, expr.resolved_function_info)
        return True  # Default: treat unknown calls as rvalue
    return True  # Default: rvalue
