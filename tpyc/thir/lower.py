"""AST + sema -> THIR lowering.

`lower_function` converts one analyzed `TpyFunction` to a `THIRFunction`,
returning None when the function falls outside the supported slice (the
eligibility gate). Lowering reads the analyzer here so codegen never has to;
every fact codegen consumes is materialized onto the returned THIR nodes.

Eligible slice: non-method, non-generic, non-generator/async, plain-linkage
free functions whose params/locals/return are fixed-width-int scalars, with
straight-line bodies (var-decl / assign / return) over names and literals.
Anything else -> None (stays on the AST codegen path). The gate is the safety
boundary: it must reject every construct the emitter cannot reproduce byte-
for-byte.
"""

from __future__ import annotations

from ..parse.nodes import (
    FunctionLinkage,
    TpyAssign,
    TpyBinOp,
    TpyCall,
    TpyCoerce,
    TpyExpr,
    TpyFunction,
    TpyIf,
    TpyIntLiteral,
    TpyModule,
    TpyName,
    TpyReturn,
    TpyStmt,
    TpyVarDecl,
    TpyWhile,
    VarLinkage,
)
from ..typesys import (
    LiteralType, OwnType, TpyType, VoidType,
    resolve_int_literals, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from ..type_def_registry import is_bool_type, is_fixed_int_type
from ..codegen_cpp.type_resolution import resolve_stmt_binding_type
from .nodes import (
    THIRAssign,
    THIRBinOp,
    THIRCall,
    THIRCoerce,
    THIRExpr,
    THIRFunction,
    THIRFunctionLayout,
    THIRIf,
    THIRLiteral,
    THIRModule,
    THIRName,
    THIRParam,
    THIRReturn,
    THIRStmt,
    THIRVarDecl,
    THIRWhile,
)

# Arithmetic operators whose fixed-int dunders carry a `@cpp_template`
# (`add_check`, ...). `in`/`is`/bitwise take other emit paths, out of the slice.
_ARITH_OPS = frozenset({"+", "-", "*", "/", "//", "%"})
# Comparison operators -- their dunders also carry a `{self} OP {0}` template,
# so they emit through the same binop path; the result is bool. Admitted only
# as `if` conditions (not as general value-scalar exprs).
_COMPARE_OPS = frozenset({"<", "<=", ">", ">=", "==", "!="})
# The one coercion the slice reproduces: a literal landing in a fixed-int slot,
# which emits as the bare inner literal (a passthrough on the C++ side).
_INT_LIT_COERCION = "int_literal_to_fixed_int"


def _eligible_scalar(t: TpyType | None) -> bool:
    """A type the emitter can render and reason about without form facts.

    Fixed-width ints only for now: borrow/storage form never arises, and the
    C++ spelling (`int32_t`, ...) comes straight from `TpyType.to_cpp()`.
    """
    return t is not None and is_fixed_int_type(t)


def _eligible_return(t: TpyType | None) -> bool:
    return t is None or isinstance(t, VoidType) or _eligible_scalar(t)


def _binop_eligible(e: TpyBinOp, locals_: set[str], analyzer) -> bool:
    rb = e.resolved_binop
    if e.op not in _ARITH_OPS or rb is None or not getattr(rb.method, "cpp_template", None):
        return False
    # Result must stay fixed-int -- excludes comparisons (bool) and any
    # mixed/widening result the slice can't render without a coercion node.
    if not _eligible_scalar(analyzer.get_expr_type(e)):
        return False
    return (_expr_eligible(e.left, locals_, analyzer)
            and _expr_eligible(e.right, locals_, analyzer))


def _call_eligible(e: TpyCall, locals_: set[str], analyzer) -> bool:
    # Only a bare-name call to a same-module plain user free function emits as
    # `name(args)`. Every special form (constructor, generic, cast, isinstance,
    # macro, **kwargs, expression callee) or imported/builtin callee takes a
    # different emit path the slice does not reproduce.
    if not isinstance(e.func, TpyName):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if (e.call_type is not None or e.type_args or e.inferred_type_args
            or e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert
            or e.subscript_callee is not None):
        return False
    if e.func_name in analyzer.imported_names:  # cross-module/builtin -> qualified
        return False
    fi = e.resolved_function_info
    if fi is None or fi.cpp_template or fi.native_function or fi.native_name:
        return False
    # A literal-specialized overload emits a mangled name (`f__lit_N`) the
    # bare-name call does not reproduce. The error_return guard is defense in
    # depth: sema already forces an @error_return call into a try/except or a
    # propagating (@error_return) caller, both of which are ineligible anyway.
    if fi.error_return_type is not None:
        return False
    if any(isinstance(p.type, LiteralType) for p in fi.params):
        return False
    if (fi.type_params or fi.is_method or fi.is_staticmethod or fi.is_async
            or fi.is_generator or fi.is_property_getter or fi.is_property_setter):
        return False
    # Exact positional arity -- no omitted defaults, no varargs (the AST would
    # synthesize the missing/packed args, which the slice does not).
    if len(e.args) != len(fi.params):
        return False
    if not _eligible_scalar(analyzer.get_expr_type(e)):
        return False
    return all(_expr_eligible(a, locals_, analyzer) for a in e.args)


def _expr_eligible(e: TpyExpr, locals_: set[str], analyzer) -> bool:
    if isinstance(e, TpyName):
        # A name outside the local/param set is a module/native/cross-module
        # global: the AST path resolves it to a qualified C++ symbol, which the
        # slice does not yet materialize. Reject -> stays on the AST path.
        return e.name in locals_
    if isinstance(e, TpyIntLiteral):
        # Only literals that emit as a bare value in any fixed-int slot. Wider
        # values need a `ull` suffix / `static_cast` that the slice's emitter
        # does not reproduce (see ExpressionGenerator._gen_int_literal_value).
        return -2**31 <= e.value <= 2**31 - 1
    if isinstance(e, TpyBinOp):
        return _binop_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCall):
        return _call_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCoerce):
        # Only the literal-into-typed-slot passthrough; other coercions
        # (widening, bigint, optional-wrap, ...) take their own emit paths.
        return (e.coercion.name == _INT_LIT_COERCION
                and _expr_eligible(e.expr, locals_, analyzer))
    return False


def _function_eligible(func: TpyFunction) -> bool:
    if func.is_method or func.is_staticmethod:
        return False
    if func.is_property_getter or func.is_property_setter:
        return False
    if func.is_overload_stub or func.native_function or func.is_consuming:
        return False
    if func.builtin_decorator_key is not None:
        return False
    if func.is_async or func.is_generator:
        return False
    if func.error_return is not None or func.type_params:
        return False
    if func.linkage != FunctionLinkage.DEFAULT:
        return False
    for _name, ptype in func.params:
        if not _eligible_scalar(ptype if isinstance(ptype, TpyType) else None):
            return False
    rt = func.return_type if isinstance(func.return_type, TpyType) else None
    return _eligible_return(rt) if func.return_type is not None else True


def _var_decl_type(stmt: TpyVarDecl, analyzer) -> TpyType | None:
    # Mirror codegen's _resolve_target_type (value-scalar subset): the binding
    # type captures sema's local deduction -- e.g. a literal-seeded local that
    # retro-widens to UInt64 from later usage -- which the init's type alone
    # (IntLiteralType) does not. Fall back to the init type, then resolve any
    # remaining int literal to the module default int.
    target = resolve_stmt_binding_type(stmt, analyzer, include_global_binding=False)
    if target is None and stmt.init is not None:
        target = analyzer.get_expr_type(stmt.init)
    if target is None:
        return None
    target = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(target)))
    if isinstance(target, OwnType):
        target = target.wrapped
    return resolve_int_literals(target, analyzer.ctx.default_int_for_literal)


def _condition_eligible(cond: TpyExpr, declared: set[str], analyzer) -> bool:
    # An `if` condition: a single fixed-int comparison (bool result, dunder
    # template). Bare bool names, and/or/not, and chained compares take other
    # emit paths and stay on the AST path.
    if not isinstance(cond, TpyBinOp) or cond.op not in _COMPARE_OPS:
        return False
    rt = analyzer.get_expr_type(cond)
    if rt is None or not is_bool_type(rt):
        return False
    # `<`/`==` carry a `{self} OP {0}` template; the derived comparisons
    # (`<= > >= !=`) have no resolved_binop and emit as a bare C++ operator.
    # A resolved_binop *without* a template would emit some other way -> reject.
    rb = cond.resolved_binop
    if rb is not None and not getattr(rb.method, "cpp_template", None):
        return False
    return (_expr_eligible(cond.left, declared, analyzer)
            and _expr_eligible(cond.right, declared, analyzer))


def _stmt_eligible(stmt: TpyStmt, analyzer, declared: set[str], *, in_branch: bool) -> bool:
    if isinstance(stmt, TpyVarDecl):
        if stmt.linkage != VarLinkage.DEFAULT or stmt.init is None:
            return False
        is_reassign = stmt.name in declared
        # A var-decl inside a branch must reassign an already-declared local --
        # a name first-declared in a branch needs scope snapshot/restore (and
        # may hoist), which the slice does not reproduce.
        if in_branch and not is_reassign:
            return False
        if not _expr_eligible(stmt.init, declared, analyzer):
            return False
        # First declaration: the local's type must be an eligible scalar (a
        # bare-literal init analyzes as IntLiteralType, pinning no width -> out).
        # A reassignment targets an already-validated local (its value just
        # renders into the existing slot), so the type check does not apply.
        return is_reassign or _eligible_scalar(_var_decl_type(stmt, analyzer))
    if isinstance(stmt, TpyAssign):
        return (isinstance(stmt.target, TpyName)
                and stmt.target.name in declared
                and _expr_eligible(stmt.value, declared, analyzer))
    if isinstance(stmt, TpyReturn):
        return stmt.value is None or _expr_eligible(stmt.value, declared, analyzer)
    if isinstance(stmt, TpyIf):
        if not _condition_eligible(stmt.condition, declared, analyzer):
            return False
        # Branches do not extend the outer scope (no new-name decls allowed in
        # them), so each is checked against the same declared-so-far set.
        return (_body_eligible(stmt.then_body, analyzer, declared, in_branch=True)
                and _body_eligible(stmt.else_body, analyzer, declared, in_branch=True))
    if isinstance(stmt, TpyWhile):
        # No while/else, and a comparison condition. break/continue are not in
        # the stmt set, so a body containing them is rejected by _body_eligible.
        if stmt.orelse or not _condition_eligible(stmt.condition, declared, analyzer):
            return False
        return _body_eligible(stmt.body, analyzer, declared, in_branch=True)
    return False


def _body_eligible(body, analyzer, declared: set[str], *, in_branch: bool) -> bool:
    """Walk a statement list in source order, mirroring lowering's declared-set
    growth: a top-level new-name var-decl extends scope; branch bodies don't."""
    declared = set(declared)  # local copy -- sibling branches must not see each other
    for stmt in body:
        if not _stmt_eligible(stmt, analyzer, declared, in_branch=in_branch):
            return False
        if not in_branch and isinstance(stmt, TpyVarDecl):
            declared.add(stmt.name)
    return True


def _lower_expr(e: TpyExpr, analyzer) -> THIRExpr:
    rtype = analyzer.get_expr_type(e)
    loc = getattr(e, "loc", None)
    if isinstance(e, TpyName):
        return THIRName(result_type=rtype, name=e.name, loc=loc)
    if isinstance(e, TpyIntLiteral):
        return THIRLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyBinOp):
        return THIRBinOp(
            result_type=rtype,
            left=_lower_expr(e.left, analyzer),
            op=e.op,
            right=_lower_expr(e.right, analyzer),
            resolved=e.resolved_binop,
            divisor_non_zero=e.divisor_non_zero,
            loc=loc,
        )
    if isinstance(e, TpyCall):
        return THIRCall(
            result_type=rtype,
            callee=e.func_name,
            args=tuple(_lower_expr(a, analyzer) for a in e.args),
            loc=loc,
        )
    if isinstance(e, TpyCoerce):
        return THIRCoerce(
            result_type=rtype,
            expr=_lower_expr(e.expr, analyzer),
            coercion_name=e.coercion.name,
            loc=loc,
        )
    raise AssertionError(f"ineligible expr reached lowering: {type(e).__name__}")


def _lower_stmt(stmt: TpyStmt, analyzer, declared: set[str]) -> THIRStmt:
    loc = getattr(stmt, "loc", None)
    if isinstance(stmt, TpyVarDecl):
        vtype = _var_decl_type(stmt, analyzer)
        init = _lower_expr(stmt.init, analyzer) if stmt.init else None
        # The parser emits TpyVarDecl for every `name = expr`; the AST codegen
        # treats a write to an already-declared name as a reassignment, not a
        # re-declaration. Mirror that here so first-decl emits `T x = ...` and a
        # reassignment emits `x = ...`.
        if stmt.name in declared:
            assert init is not None  # eligibility requires a var-decl init
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=init,
                loc=loc,
            )
        declared.add(stmt.name)
        return THIRVarDecl(name=stmt.name, resolved_type=vtype, init=init, loc=loc)
    if isinstance(stmt, TpyAssign):
        return THIRAssign(
            target=_lower_expr(stmt.target, analyzer),
            value=_lower_expr(stmt.value, analyzer),
            loc=loc,
        )
    if isinstance(stmt, TpyReturn):
        return THIRReturn(
            value=_lower_expr(stmt.value, analyzer) if stmt.value else None,
            loc=loc,
        )
    if isinstance(stmt, TpyIf):
        # Branches share `declared`: eligibility guarantees they only reassign
        # already-declared locals (lowered to THIRAssign), so neither branch
        # extends the scope and order stays consistent with the AST path.
        return THIRIf(
            condition=_lower_expr(stmt.condition, analyzer),
            then_body=tuple(_lower_stmt(s, analyzer, declared) for s in stmt.then_body),
            else_body=tuple(_lower_stmt(s, analyzer, declared) for s in stmt.else_body),
            loc=loc,
        )
    if isinstance(stmt, TpyWhile):
        return THIRWhile(
            condition=_lower_expr(stmt.condition, analyzer),
            body=tuple(_lower_stmt(s, analyzer, declared) for s in stmt.body),
            loc=loc,
        )
    raise AssertionError(f"ineligible stmt reached lowering: {type(stmt).__name__}")


def lower_function(func: TpyFunction, analyzer) -> THIRFunction | None:
    """Lower one function to THIR, or None if it falls outside the slice."""
    if not _function_eligible(func):
        return None
    # Branch-local hoisting is not reproduced -- a function that hoists any
    # local out of a branch stays on the AST path.
    if analyzer.function_hoisted_vars.get(id(func)):
        return None
    params_set = {n for n, _ in func.params}
    if not _body_eligible(func.body, analyzer, params_set, in_branch=False):
        return None
    params = tuple(THIRParam(name=n, type=t) for n, t in func.params)
    rt = func.return_type if isinstance(func.return_type, TpyType) else VoidType()
    # Seeded with params: a write to a param name is a reassignment, not a decl.
    declared: set[str] = set(params_set)
    body = tuple(_lower_stmt(s, analyzer, declared) for s in func.body)
    return THIRFunction(
        name=func.name,
        params=params,
        return_type=rt,
        body=body,
        layout=THIRFunctionLayout(),
    )


def lower_module(module: TpyModule, analyzer) -> THIRModule:
    """Lower every eligible function in `module`; skip the rest."""
    out = THIRModule(module_name=getattr(analyzer.ctx, "module_name", "generated"))
    for func in module.functions:
        thir_fn = lower_function(func, analyzer)
        if thir_fn is not None:
            out.functions.append(thir_fn)
    return out
