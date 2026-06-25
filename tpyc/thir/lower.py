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

import math

from ..parse.nodes import (
    FunctionLinkage,
    TpyAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyCall,
    TpyCoerce,
    TpyExpr,
    TpyFloatLiteral,
    TpyForEach,
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
    LiteralType, OwnType, TpyType, VoidType, is_float_type,
    resolve_int_literals, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from ..type_def_registry import (
    int_traits_of, is_bool_type, is_fixed_int_type, is_float32_type,
)
from ..codegen_cpp.type_resolution import resolve_stmt_binding_type
from .nodes import (
    THIRAssign,
    THIRBinOp,
    THIRCall,
    THIRCoerce,
    THIRExpr,
    THIRForRange,
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

# Arithmetic operators whose dunders carry a `@cpp_template` (`add_check`, ...).
# NB the parser emits true-division as op `div`, not `/`, so the `/` token here
# is inert -- truediv stays on the AST path (see TODO: decide enable-or-drop).
# `in`/`is`/bitwise take other emit paths, out of the slice.
_ARITH_OPS = frozenset({"+", "-", "*", "/", "//", "%"})
# Comparison operators -- `<`/`==` dunders carry a `{self} OP {0}` template (the
# derived ones emit as a bare C++ operator); the result is bool. Admitted both
# as `if`/`while` conditions and as values (`x = a < b`).
_COMPARE_OPS = frozenset({"<", "<=", ">", ">=", "==", "!="})
# Literal-into-typed-slot coercions the slice reproduces, both pass-throughs on
# the C++ side (the inner literal renders directly in the slot's type): a literal
# into a fixed-int slot, and a float literal into a double `float` slot.
_INT_LIT_COERCION = "int_literal_to_fixed_int"
_FLOAT_LIT_COERCION = "float_literal_to_float"


def _eligible_scalar(t: TpyType | None) -> bool:
    """A type the emitter can render and reason about without form facts.

    Fixed-width ints, `bool`, and double `float` (-> `double`): borrow/storage
    form never arises and the C++ spelling comes straight from `TpyType.to_cpp()`.
    Float32 is excluded -- its literals need a `f` suffix the slice does not emit.
    """
    return t is not None and (is_fixed_int_type(t) or is_bool_type(t)
                              or (is_float_type(t) and not is_float32_type(t)))


def _eligible_return(t: TpyType | None) -> bool:
    return t is None or isinstance(t, VoidType) or _eligible_scalar(t)


def _operand_type(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> TpyType | None:
    # The operand's resolved type for the mixed-sign comparison gate. For a
    # local/param name use the tracked resolved type -- codegen's get_resolved_type
    # reads ctx.var_types, which holds e.g. a retro-widened literal-seeded local's
    # final type (UInt64), whereas analyzer.get_expr_type returns the pre-widen
    # seed (Int32). Using the seed would over-exclude same-sign-after-widen loops.
    if isinstance(e, TpyName):
        t = locals_.get(e.name)
        if t is not None:
            return t
    return analyzer.get_expr_type(e)


def _mixed_sign_compare(left: TpyType | None, right: TpyType | None) -> bool:
    # Mirror of codegen's _mixed_sign_fixed_int (expressions.py): a signed-vs-
    # unsigned fixed-int comparison emits std::cmp_* (and a mixed-sign one with a
    # coercion target emits a cast), never the bare `(l op r)` the slice emits --
    # so exclude it. Built on the same int_traits_of primitive; the byte-identical
    # net gates any drift from the codegen predicate.
    if not (is_fixed_int_type(left) and is_fixed_int_type(right)):
        return False
    lt, rt = int_traits_of(left), int_traits_of(right)
    return lt is not None and rt is not None and lt.signed != rt.signed


def _binop_eligible(e: TpyBinOp, locals_: dict[str, TpyType], analyzer) -> bool:
    rb = e.resolved_binop
    rt = analyzer.get_expr_type(e)
    if e.op in _ARITH_OPS:
        # Same-width arithmetic: a templated dunder, scalar result. Excludes any
        # mixed/widening result the slice can't render without a coercion node.
        if rb is None or not getattr(rb.method, "cpp_template", None):
            return False
        if not _eligible_scalar(rt):
            return False
    elif e.op in _COMPARE_OPS:
        # A scalar comparison -> bool, usable as a value (`x = a < b`) or an
        # `if`/`while` condition. `<`/`==` carry a `{self} OP {0}` template; the
        # derived comparisons (`<= > >= !=`) have rb=None and emit as a bare C++
        # operator. A rb *with* a non-template would emit some other way -> reject.
        if rt is None or not is_bool_type(rt):
            return False
        if rb is not None and not getattr(rb.method, "cpp_template", None):
            return False
        if _mixed_sign_compare(_operand_type(e.left, locals_, analyzer),
                               _operand_type(e.right, locals_, analyzer)):
            return False
    else:
        # Logical &&/|| (narrowing + short-circuit-slot emit) and is/in/bitwise
        # are out of the slice -> AST path.
        return False
    return (_expr_eligible(e.left, locals_, analyzer)
            and _expr_eligible(e.right, locals_, analyzer))


def _call_eligible(e: TpyCall, locals_: dict[str, TpyType], analyzer) -> bool:
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


def _expr_eligible(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
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
    if isinstance(e, TpyFloatLiteral):
        # A finite float literal renders as repr(value) in a double slot, byte
        # for byte (the AST path's _gen_float_literal_value double branch). inf/
        # nan only arise from float(...) calls, never a bare literal, but guard
        # anyway -- repr(inf)/repr(nan) are not valid C++.
        return math.isfinite(e.value)
    if isinstance(e, TpyBoolLiteral):
        return True  # True/False -> true/false; no target-type dependence
    if isinstance(e, TpyBinOp):
        return _binop_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCall):
        return _call_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCoerce):
        # Only the literal-into-typed-slot passthroughs; other coercions
        # (widening, bigint, int<->float, float32, optional-wrap, ...) take
        # their own emit paths.
        return (e.coercion.name in (_INT_LIT_COERCION, _FLOAT_LIT_COERCION)
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


def _condition_eligible(cond: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    # An `if`/`while` condition: a bare bool local/param (`if flag:`) or a single
    # scalar comparison (bool result), the latter routed through _binop_eligible
    # so it gets the same mixed-sign gate as comparison-as-value. and/or/not,
    # chained compares, and a bool-literal condition (the AST path may
    # dead-branch-eliminate it) are not handled here -> AST path.
    if isinstance(cond, TpyName):
        rt = analyzer.get_expr_type(cond)
        return cond.name in declared and rt is not None and is_bool_type(rt)
    if isinstance(cond, TpyBinOp) and cond.op in _COMPARE_OPS:
        return _binop_eligible(cond, declared, analyzer)
    return False


def _range_bound_literal_value(arg: TpyExpr) -> int | None:
    # The AST's inline-vs-hoist decision for a range bound (_is_literal_range_arg):
    # an inlinable bare int literal (possibly behind the int_literal coerce) vs a
    # name/expr hoisted to a temp. Only the bare-literal subset the slice admits is
    # mirrored, so it agrees with _extract_int_literal regardless of that helper's
    # evolution. The int32 bound keeps the value a bare token (no `ull`/cast).
    while isinstance(arg, TpyCoerce) and arg.coercion.name == _INT_LIT_COERCION:
        arg = arg.expr
    if isinstance(arg, TpyIntLiteral) and -2**31 <= arg.value <= 2**31 - 1:
        return arg.value
    return None


def _range_bound_eligible(arg: TpyExpr, declared: dict[str, TpyType]) -> bool:
    # Tight slice: an inlinable int literal, or a bare name of an
    # already-declared fixed-int local/param (hoisted to a __start/__stop temp).
    # Binop/call bounds are deferred -- they need the byte-identical net to
    # confirm gen_range_args' _gen_expr_deref(arg, ptype) matches _emit_expr.
    if isinstance(arg, TpyName):
        # `declared` holds bool/float locals too, so the bound's resolved type
        # must be checked fixed-int (it renders into a `cpp_elem` temp).
        return is_fixed_int_type(declared.get(arg.name))
    return _range_bound_literal_value(arg) is not None


def _for_range_eligible(stmt: TpyForEach, analyzer, declared: dict[str, TpyType]) -> bool:
    # Only a plain `for v in range(stop | start, stop)` with step 1 over a
    # fixed-int counter, loop var not used after the loop. Every richer for-shape
    # (async, tuple-unpack, enum/container iteration, consuming, for/else,
    # 3-arg/stepped range) stays on the AST path.
    if (stmt.is_async or stmt.is_tuple_unpack or stmt.orelse
            or stmt.enum_iterable is not None
            or stmt.consuming_iter_fi is not None or stmt.hoist_loop_var):
        return False
    it = stmt.iterable
    if not (isinstance(it, TpyCall) and it.func_name == "range"):
        return False
    if it.kwargs or it.double_star_unpack is not None or len(it.args) not in (1, 2):
        return False
    et = unwrap_ref_type(stmt.elem_type) if stmt.elem_type is not None else None
    if not _eligible_scalar(et):
        return False
    # The AST path's _emit_branch_decls pre-declares any name sema put in
    # if_branch_decls[id(stmt)] -- keyed on a loop stmt by _promote_pending_loop_var
    # when a body-local/loop-var is hoisted for post-loop use. The THIR emitter has
    # no equivalent, so reject: a direct guard on the exact byte-identity condition
    # (the hoist_loop_var + in_branch gates also exclude the hoisting causes).
    if analyzer.if_branch_decls.get(id(stmt)):
        return False
    # The loop var must be loop-scoped -- a name shadowing an outer local hits
    # the AST path's was_declared handling, which the emitter does not reproduce.
    if stmt.var in declared:
        return False
    nargs = len(it.args)
    if nargs == 2 and not _range_bound_eligible(it.args[0], declared):
        return False
    stop_arg = it.args[0] if nargs == 1 else it.args[1]
    if not _range_bound_eligible(stop_arg, declared):
        return False
    body_declared = dict(declared)
    body_declared[stmt.var] = et  # loop var's resolved (fixed-int) type
    return _body_eligible(stmt.body, analyzer, body_declared, in_branch=True)


def _stmt_eligible(stmt: TpyStmt, analyzer, declared: dict[str, TpyType], *, in_branch: bool) -> bool:
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
    if isinstance(stmt, TpyForEach):
        return _for_range_eligible(stmt, analyzer, declared)
    return False


def _body_eligible(body, analyzer, declared: dict[str, TpyType], *, in_branch: bool) -> bool:
    """Walk a statement list in source order, mirroring lowering's declared-scope
    growth: a top-level new-name var-decl extends scope; branch bodies don't. The
    map carries each name's resolved type (for the mixed-sign comparison gate)."""
    declared = dict(declared)  # local copy -- sibling branches must not see each other
    for stmt in body:
        if not _stmt_eligible(stmt, analyzer, declared, in_branch=in_branch):
            return False
        if (not in_branch and isinstance(stmt, TpyVarDecl)
                and stmt.name not in declared):  # first decl -- keep retro-widened type
            declared[stmt.name] = _var_decl_type(stmt, analyzer)
    return True


def _lower_expr(e: TpyExpr, analyzer) -> THIRExpr:
    rtype = analyzer.get_expr_type(e)
    loc = getattr(e, "loc", None)
    if isinstance(e, TpyName):
        return THIRName(result_type=rtype, name=e.name, loc=loc)
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
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


def _lower_stmt(stmt: TpyStmt, analyzer, declared: dict[str, TpyType]) -> THIRStmt:
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
        declared[stmt.name] = vtype
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
    if isinstance(stmt, TpyForEach):
        it = stmt.iterable
        nargs = len(it.args)
        if nargs == 1:
            start = None
            start_is_literal = True
            stop_arg = it.args[0]
        else:
            start_arg = it.args[0]
            start = _lower_expr(start_arg, analyzer)
            start_is_literal = _range_bound_literal_value(start_arg) is not None
            stop_arg = it.args[1]
        # Loop var is C++-for-scoped: visible in the body but not the outer scope
        # (a fresh declared copy, so a body decl can't leak past the loop).
        et = unwrap_ref_type(stmt.elem_type)
        body_declared = dict(declared)
        body_declared[stmt.var] = et
        return THIRForRange(
            var=stmt.var,
            elem_type=et,
            stop=_lower_expr(stop_arg, analyzer),
            start=start,
            start_is_literal=start_is_literal,
            stop_is_literal=_range_bound_literal_value(stop_arg) is not None,
            body=tuple(_lower_stmt(s, analyzer, body_declared) for s in stmt.body),
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
    params_set: dict[str, TpyType] = {n: t for n, t in func.params}
    if not _body_eligible(func.body, analyzer, params_set, in_branch=False):
        return None
    params = tuple(THIRParam(name=n, type=t) for n, t in func.params)
    rt = func.return_type if isinstance(func.return_type, TpyType) else VoidType()
    # Seeded with params: a write to a param name is a reassignment, not a decl.
    declared: dict[str, TpyType] = dict(params_set)
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
