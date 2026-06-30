"""AST + sema -> THIR lowering.

`lower_function` converts one analyzed `TpyFunction` to a `THIRFunction`,
returning None when the function falls outside the supported slice (the
eligibility gate). Lowering reads the analyzer here so codegen never has to;
every fact codegen consumes is materialized onto the returned THIR nodes.

Eligible slice: non-generic, non-generator/async, plain-linkage free functions
-- and plain instance methods of same-module non-generic records (`self` as an
F1-record `this` receiver) -- whose params/locals/return are fixed-width-int
scalars (plus the F1/F2 non-value record forms for locals/returns), with
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
    TpyAugAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyCall,
    TpyCoerce,
    TpyExpr,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyForEach,
    TpyFunction,
    TpyIf,
    TpyIntLiteral,
    TpyModule,
    TpyName,
    TpyNoneLiteral,
    TpyReturn,
    TpyStmt,
    TpyVarDecl,
    TpyWhile,
    VarLinkage,
)
from ..typesys import (
    LiteralType, NominalType, OptionalType, OwnType, ReadonlyType, TpyType,
    VoidType, is_float_type, resolve_int_literals, unwrap_optional_own,
    unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from ..type_def_registry import (
    int_traits_of, is_big_int_type, is_bool_type, is_fixed_int_type,
    is_float32_type,
)
from ..codegen_cpp.type_resolution import resolve_stmt_binding_type
from ..codegen_cpp.forms import (
    LocalBinding, classify_local_binding, reads_storage_form_optional,
)
from ..value_category import is_rvalue_source
from ..codegen_cpp.context import escape_cpp_name
from .nodes import (
    Form,
    THIRAssign,
    THIRBinOp,
    THIRCall,
    THIRCoerce,
    THIRConstructor,
    THIRExpr,
    THIRFieldAccess,
    THIRForRange,
    THIRFormConvert,
    THIRFunction,
    THIRFunctionLayout,
    THIRIf,
    THIRLiteral,
    THIRMilInit,
    THIRModule,
    THIRName,
    THIRParam,
    THIRReturn,
    THIRSelf,
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


def _eligible_return(t: TpyType | None, analyzer) -> bool:
    return (t is None or isinstance(t, VoidType) or _eligible_scalar(t)
            or _storage_optional_return_type(t, analyzer) is not None)


# --- F1 form slice: single-assignment non-value record locals + field reads ---

class _Prescan:
    """Per-function prescan facts the binding classifier reads -- the same sets
    codegen seeds into ctx (see setup_body_scope), recomputed here from the
    analyzer so lowering classifies identically without a CodeGenContext."""
    __slots__ = ("reassigned", "rvalue_reassigned", "hoisted", "move_through",
                 "ret_storage_opt")

    def __init__(self, func: TpyFunction, analyzer) -> None:
        scan = analyzer.function_scan_results.get(id(func))
        global_decls = analyzer.function_global_decls.get(id(func), set())
        self.reassigned = (scan.reassigned - global_decls) if scan else set()
        # F2d: the subset reassigned with an rvalue source (the rebind-slot
        # trigger -- mirrors codegen's `ctx.rvalue_reassigned_vars` seeding).
        self.rvalue_reassigned = (
            (scan.rvalue_reassigned - global_decls) if scan else set())
        self.hoisted = analyzer.function_hoisted_vars.get(id(func), set())
        self.move_through = analyzer.function_move_through_vars.get(id(func), set())
        # F2c: the function's storage-form Optional[F1-record] return slot, if any
        # (`Own[T] | None` -> `std::optional<T>`), so a `return None` /
        # `return <borrow T*>` lowers to `std::nullopt` / `ptr_to_optional`. None
        # for every other return type (the value-scalar/pointer-repr paths).
        rt = func.return_type if isinstance(func.return_type, TpyType) else None
        self.ret_storage_opt = _storage_optional_return_type(rt, analyzer)


def _f1_record(t: TpyType | None, analyzer) -> bool:
    """A same-module, non-native, non-generic concrete user record -- the F1
    record slice where `TpyType.to_cpp()` == `TypeResolver.type_to_cpp()` (no
    cross-module qualification, no native name/field rename, no generic-arg
    recursion). Other records stay on the AST path."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = t.wrapped
    if not (isinstance(t, NominalType) and t.is_user_record):
        return False
    if t.type_args:
        return False
    ri = analyzer.registry.get_record_for_type(t)
    if ri is None or ri.is_native:
        return False
    return analyzer.registry.imported_record_qualification_for_type(
        t, analyzer.ctx.module_name) is None


def _storage_optional_return_type(t: TpyType | None, analyzer) -> 'OptionalType | None':
    """The storage-form `Optional[F1-record]` return slot (F2c): `Own[T] | None`,
    which lowers to a `std::optional<T>` returned by value. `Inner | None` is
    pointer-repr (the function returns a borrow `Inner*`, a different direction)
    and is excluded -- it stays on the AST path. The caller passes None for a
    non-`TpyType` (unresolved) return annotation."""
    if not isinstance(t, OptionalType) or t.uses_pointer_repr():
        return None
    inner = t.inner.wrapped if isinstance(t.inner, OwnType) else t.inner
    return t if _f1_record(inner, analyzer) else None


def _field_receiver_ok(e: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """`e` is a plain field access `recv.field` off an F1-record receiver (a record
    param, REF_ALIAS local, or F2 plain `T*` pointer-local in `declared`) with no
    special-emit marker -- read or write position. The receiver's pointer-vs-
    reference shape (`->` vs `.`) is decided at lowering from the pointer-local set;
    eligibility only needs the receiver to be an F1-record. Pointer-local Optional
    reads would need narrowing (the marker guards below reject those), so only plain
    non-null receivers pass. The property-setter / `__setattr__` markers guard the
    write position (a property/setattr field assign takes a method-call emit path)."""
    if not isinstance(e, TpyFieldAccess):
        return False
    if (e.module_var_access is not None or e.class_constant_owner is not None
            or e.property_getter_call is not None or e.dyn_getattr_call is not None
            or e.property_setter_call is not None or e.dyn_setattr_call is not None
            or e.unbound_self_parent_type is not None or e.deref_depth
            or e.deref_narrowed_to is not None or e.needs_optional_runtime_check):
        return False
    recv = e.obj
    return (isinstance(recv, TpyName)
            and _f1_record(declared.get(recv.name), analyzer))


def _borrow_local_binding(stmt: TpyVarDecl, target_type: TpyType | None,
                          declared: dict[str, TpyType], prescan: _Prescan,
                          analyzer) -> 'LocalBinding | None':
    """The binding for a non-value local var-decl's *first* declaration, or None
    if it is outside the emit slice. The form decision comes from the shared
    classifier; the slice additionally requires a field-access source off an
    F1-record receiver and an F1-record local (REF_ALIAS / POINTER) / inner
    (OPTIONAL_TO_PTR) type. REF_ALIAS and POINTER are the single-assignment and
    reassigned shapes of the same plain-record lvalue lift; POINTER's reseats are
    gated separately in `_stmt_eligible`."""
    binding = classify_local_binding(
        target_type, stmt.init, analyzer, name=stmt.name,
        reassigned=prescan.reassigned, rvalue_reassigned=prescan.rvalue_reassigned,
        hoisted=prescan.hoisted, move_through=prescan.move_through)
    if binding is LocalBinding.OTHER:
        return None
    if binding is LocalBinding.REBIND_SLOT:
        # F2d: the source is an rvalue F1-record ctor / by-value call (not a field
        # read), so it bypasses the field-receiver check the lvalue bindings need.
        return binding if (_f1_record(target_type, analyzer)
                           and _is_record_rvalue_source(stmt.init, declared, analyzer)) else None
    if not _field_receiver_ok(stmt.init, declared, analyzer):
        return None
    if binding is LocalBinding.REF_ALIAS or binding is LocalBinding.POINTER:
        return binding if _f1_record(target_type, analyzer) else None
    # OPTIONAL_TO_PTR: the borrow `T*` points at the optional's inner record.
    inner = target_type.inner if isinstance(target_type, OptionalType) else None
    return binding if _f1_record(inner, analyzer) else None


def _is_record_rvalue_source(init: TpyExpr, declared: dict[str, TpyType],
                             analyzer) -> bool:
    """An F2d rebind-slot source: an rvalue call producing an F1-record (a ctor
    `Inner(...)` or a by-value record-returning call) with eligible scalar args.
    It emits as the bare `Name(args)` the two-slot init / reseat wraps. kwargs /
    star-unpack args take other emit paths and stay on the AST path."""
    if not isinstance(init, TpyCall):
        return False
    if init.kwargs or init.double_star_unpack is not None:
        return False
    if not (_f1_record(analyzer.get_expr_type(init), analyzer)
            and is_rvalue_source(analyzer, init)):
        return False
    # Args must be eligible SCALARS (mirror _call_eligible): a non-scalar arg
    # (a record pointer-local / Own[T]) needs the AST's `(*q)` deref or auto-move
    # `std::move(q)`, neither of which the bare THIRCall arg emit reproduces.
    return all(_eligible_scalar(analyzer.get_expr_type(a))
               and _expr_eligible(a, declared, analyzer) for a in init.args)


def _f2_reseat_ok(init: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """A pointer-local reseat value: an lvalue field read off an F1-record receiver
    whose field is itself an F1-record (the new pointee), so it reseats as
    `p = &(recv.field);`. rvalue / `None` / name-alias reseats need the rebind-slot
    (`__slot_N`) machinery and stay on the AST path."""
    return (_field_receiver_ok(init, declared, analyzer)
            and _f1_record(analyzer.get_expr_type(init), analyzer))


def _is_borrow_ptr_local(e: TpyExpr, declared: dict[str, TpyType],
                         pointers: set[str]) -> bool:
    """`e` is a bare borrow `T*` local that lifts to a storage `optional<T>` at a
    write/return: an F2a POINTER / F2d REBIND_SLOT (plain-record, in `pointers`) or
    an F1 OPTIONAL_TO_PTR (a pointer-repr `Optional` local, known by its declared
    type). All render `T*`; the copy-vs-move choice (`ptr_to_optional` vs
    `ptr_to_optional_move`) is decided at lowering from movability + last-use, not
    here (a non-owning borrow is never movable, so it always copies). A `T&`
    REF_ALIAS is excluded -- not a pointer, so the AST path emits it differently."""
    if not isinstance(e, TpyName):
        return False
    if e.name in pointers:
        return True
    t = declared.get(e.name)
    return isinstance(t, OptionalType) and t.uses_pointer_repr()


def _f2b_optional_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                                 pointers: set[str], analyzer) -> bool:
    """An optional-field write `recv.field = <value>`: the target is a pointer-repr
    `Optional[record]` field off an F1-record receiver and the value is either a
    bare borrow `T*` local (`recv.field = ::tpy::ptr_to_optional[_move](p)`, copy
    or move per last-use) or a `None` literal (`recv.field = std::nullopt`). The
    `copy()`-acknowledged and call/rvalue value sources stay on the AST path."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    ftype = analyzer.get_expr_type(target)
    if not (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()):
        return False
    if not _f1_record(ftype.inner, analyzer):
        return False
    return (isinstance(stmt.value, TpyNoneLiteral)
            or _is_borrow_ptr_local(stmt.value, declared, pointers))


def _scalar_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                           analyzer) -> bool:
    """A scalar-field write `recv.field = <scalar>`: a value-scalar field off an
    F1-record receiver (`_field_receiver_ok` also rejects the property-setter /
    __setattr__ write target), written with an eligible scalar expression. The
    scalar sibling of `_f2b_optional_field_write_ok` -- it emits as the AST's
    default field-assign path (`recv.field = <value>;`, no borrow<->storage lift)."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    if not _eligible_scalar(analyzer.get_expr_type(target)):
        return False
    return _expr_eligible(stmt.value, declared, analyzer)


def _scalar_aug_assign_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                          analyzer) -> bool:
    """A scalar augmented assignment `x += y` / `recv.field += y` that is
    byte-identical to `target = (target OP value)` -- the plain
    `_gen_binop_from_result` branch of `_gen_aug_assign_code`, with every
    preprocessing branch of the AST path gated out:

      - an in-place dunder (`resolved_inplace`) mutates the target via a method
        call, not the binop substitution;
      - a missing/non-template `resolved_binop` emits a bare C++ `op=` fallback;
      - `str +=` takes the in-place-append optimization (excluded for free: a
        str target is not an eligible scalar);
      - `FixedInt += BigInt` wraps the value in `.to_fixed_check<T>()` the
        synthetic binop cannot reproduce;
      - a subscript / class-constant / narrowed-optional target is not a plain
        name-or-field eligible-scalar lvalue.

    The target is a declared scalar local or an F1-record scalar field; the value
    is an eligible scalar expression. Lowering synthesizes the binop with
    `divisor_non_zero=False` -- the AST aug-assign path never swaps
    `div_check`->`div_floor` (no `TpyBinOp` node carries the flag)."""
    if stmt.resolved_inplace is not None:
        return False
    rb = stmt.resolved_binop
    if rb is None or not getattr(rb.method, "cpp_template", None):
        return False
    target = stmt.target
    if isinstance(target, TpyName):
        if target.name not in declared:
            return False
    elif not _field_receiver_ok(target, declared, analyzer):
        return False
    # A narrowed-Optional or non-scalar target is rejected here (the AST unwraps
    # the former and never reaches the binop branch for the latter).
    target_type = analyzer.get_expr_type(target)
    if not _eligible_scalar(target_type):
        return False
    # FixedInt += BigInt: the AST converts the value via `.to_fixed_check<T>()`
    # before the binop -- the synthetic THIRBinOp would emit a bare `t + b`.
    if is_fixed_int_type(target_type) and is_big_int_type(analyzer.get_expr_type(stmt.value)):
        return False
    return _expr_eligible(stmt.value, declared, analyzer)


def _param_is_const(name: str, func: TpyFunction, analyzer,
                    record_name: str | None = None) -> bool:
    """Whether param `name` is emitted `const` -- read from the sema fact
    `FunctionInfo.const_borrow_params` (param indices), which equals codegen's
    `const_ref_params` for a function's plain F1-record (ref) params. This holds
    for a readonly callable (method or free function) too: a `@readonly` callable's
    non-value param is stored as `Ref(ReadonlyType(T))`, so `decide_param_const`
    takes the `ReadonlyType` early-exit -- the forced-const (codegen body) and
    inferred (`const_borrow_params`) verdicts traverse the same branch, making the
    inferred set exact. `cbp` is None when Phase-2 has not run -- unreachable for an
    admitted function (Phase-1 always sets `mutated_params`), so the resulting
    not-const is a safe default, not a divergence. A method's FunctionInfo lives on
    the owning record (`record_name`) -- the same lookup codegen's
    `_get_method_mutated_params` uses; a free function (record_name None) reads the
    function registry."""
    if record_name is not None:
        ri = analyzer.registry.get_record(record_name)
        overloads = ri.get_method_overloads(func.name) if ri is not None else None
    else:
        overloads = analyzer.registry.get_function(func.name)
    fi = overloads[-1] if overloads else None
    cbp = fi.const_borrow_params if fi is not None else None
    if not cbp:
        return False
    idx = next((i for i, (n, _) in enumerate(func.params) if n == name), None)
    return idx is not None and idx in cbp


def _f1_is_const(binding: 'LocalBinding', target_type: TpyType | None,
                 stmt: TpyVarDecl, func: TpyFunction, analyzer,
                 const_locals: set[str], record_name: str | None = None) -> bool:
    """The const-ness of an F1 borrow local's decl (`const T&` / `const T*`).

    Mirrors `_is_const_indirect` for the field source (ReadonlyType reads on the
    optional inner / the init's raw sema type / the var_types entry) plus, for
    OPTIONAL_TO_PTR, the storage-optional const bump (`_is_const_union_source`:
    the receiver in const_ref_params (param) or const_indirect_locals (a const F1
    local, tracked in `const_locals`)). The name/method-call const branches of
    `_is_const_indirect` do not apply to a field source."""
    if isinstance(target_type, OptionalType) and isinstance(target_type.inner, ReadonlyType):
        return True
    if isinstance(analyzer.get_expr_type(stmt.init), ReadonlyType):  # raw sema type
        return True
    svt = analyzer.var_types.get(id(stmt))
    if isinstance(svt, OptionalType) and isinstance(svt.inner, ReadonlyType):
        return True
    if binding is LocalBinding.OPTIONAL_TO_PTR:
        recv = stmt.init.obj  # TpyName (validated by _field_receiver_ok)
        if (recv.name in const_locals
                or _param_is_const(recv.name, func, analyzer, record_name)):
            return True
    return False


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
    # Every argument must be an eligible SCALAR. A non-value arg (a record /
    # Own[record] param passed positionally) crosses an ownership boundary --
    # an Own param at its last use auto-moves (`f(std::move(p))`), a borrow param
    # may lift -- which the bare-name THIRCall emit does not reproduce. Scalars
    # are value types: copied, never moved, so the bare call is byte-identical.
    return all(_eligible_scalar(analyzer.get_expr_type(a))
               and _expr_eligible(a, locals_, analyzer) for a in e.args)


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
    if isinstance(e, TpyFieldAccess):
        # A scalar field read off an F1-record receiver (`recv.field`, value
        # form). The non-value field source for a borrow-local binding is handled
        # in the var-decl branch, not here -- a non-value field read is not a
        # value expression.
        return (_field_receiver_ok(e, locals_, analyzer)
                and _eligible_scalar(analyzer.get_expr_type(e)))
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


def _f1_param_eligible(ptype: TpyType | None, analyzer) -> bool:
    """An F1-eligible param: a value scalar, or an F1-record passed by reference
    (`T&` / `const T&`, accessed `.`). Optional/container/cross-module/native
    record params stay on the AST path."""
    return _eligible_scalar(ptype) or _f1_record(ptype, analyzer)


def _function_eligible(func: TpyFunction, analyzer,
                       self_type: 'TpyType | None' = None) -> bool:
    # A plain instance method is admitted when its receiver is an F1-record
    # (`self_type` passed by the caller from the owning record). Static/property
    # methods take other emit paths. An instance method takes value-scalar and
    # F1-record params; a record param's const verdict comes from the method's own
    # const_borrow_params, resolved at lowering -- see the param loop below and
    # `_param_is_const`.
    is_instance_method = (func.is_method and not func.is_staticmethod
                          and not func.is_property_getter
                          and not func.is_property_setter)
    if func.is_method and not is_instance_method:
        return False
    # Defensive: the parser sets is_method=True on staticmethods too, so the
    # check above already excludes them -- this guards against a free function
    # ever carrying is_staticmethod without is_method.
    if func.is_staticmethod:
        return False
    if is_instance_method and (self_type is None
                               or not _f1_record(self_type, analyzer)):
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
        pt = ptype if isinstance(ptype, TpyType) else None
        # Free functions and instance methods both take F1-record params; the const
        # verdict comes from the function's own const_borrow_params (a method's read
        # via the owning record at lowering). This holds for readonly callables too:
        # for a plain F1-record (ref) param the readonly forced-const verdict and the
        # inferred const_borrow_params verdict coincide (both const iff the param is
        # not directly mutated / address-escaped -- see decide_param_const), so no
        # readonly carve-out is needed (and a readonly callable cannot mutate a param
        # anyway, so its record params are uniformly const).
        if not _f1_param_eligible(pt, analyzer):
            return False
    rt = func.return_type if isinstance(func.return_type, TpyType) else None
    return _eligible_return(rt, analyzer) if func.return_type is not None else True


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


def _for_range_eligible(stmt: TpyForEach, analyzer, declared: dict[str, TpyType],
                        prescan: _Prescan, pointers: set[str],
                        rebind_slots: set[str]) -> bool:
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
    return _body_eligible(stmt.body, analyzer, body_declared, prescan,
                          in_branch=True, pointers=pointers,
                          rebind_slots=rebind_slots)


def _stmt_eligible(stmt: TpyStmt, analyzer, declared: dict[str, TpyType],
                   prescan: _Prescan, *, in_branch: bool,
                   pointers: set[str], rebind_slots: set[str]) -> bool:
    if isinstance(stmt, TpyVarDecl):
        if stmt.linkage != VarLinkage.DEFAULT or stmt.init is None:
            return False
        is_reassign = stmt.name in declared
        # A var-decl inside a branch must reassign an already-declared local --
        # a name first-declared in a branch needs scope snapshot/restore (and
        # may hoist), which the slice does not reproduce.
        if in_branch and not is_reassign:
            return False
        if not is_reassign:
            # First decl of a non-value local (REF_ALIAS / OPTIONAL_TO_PTR /
            # POINTER), bound from a field read off an F1-record receiver. Its
            # non-value field init is not a value expression, so it is admitted
            # here, not via _expr_eligible (which rejects it). A POINTER local is
            # recorded so its later reassignments lower as reseats.
            binding = _borrow_local_binding(
                stmt, _var_decl_type(stmt, analyzer), declared, prescan, analyzer)
            if binding is not None:
                if binding is LocalBinding.POINTER:
                    pointers.add(stmt.name)
                elif binding is LocalBinding.REBIND_SLOT:
                    # An F2d rebind-slot local: in `pointers` for its `->` reads,
                    # in `rebind_slots` so its reseats lower as rvalue rebinds.
                    pointers.add(stmt.name)
                    rebind_slots.add(stmt.name)
                return True
        elif stmt.name in rebind_slots:
            # F2d rebind-slot reseat: an rvalue F1-record ctor / by-value source.
            return _is_record_rvalue_source(stmt.init, declared, analyzer)
        elif stmt.name in pointers:
            # F2a pointer-local reseat: an lvalue F1-record field source only.
            return _f2_reseat_ok(stmt.init, declared, analyzer)
        if not _expr_eligible(stmt.init, declared, analyzer):
            return False
        # First declaration: the local's type must be an eligible scalar (a
        # bare-literal init analyzes as IntLiteralType, pinning no width -> out).
        # A reassignment targets an already-validated local (its value just
        # renders into the existing slot), so the type check does not apply.
        return is_reassign or _eligible_scalar(_var_decl_type(stmt, analyzer))
    if isinstance(stmt, TpyAssign):
        if isinstance(stmt.target, TpyName):
            return (stmt.target.name in declared
                    and _expr_eligible(stmt.value, declared, analyzer))
        # F2b/F2c/F2e: an optional-field write `recv.field = <borrow>` / `= None`;
        # or a plain scalar-field write `recv.field = <scalar>`.
        return (_f2b_optional_field_write_ok(stmt, declared, pointers, analyzer)
                or _scalar_field_write_ok(stmt, declared, analyzer))
    if isinstance(stmt, TpyReturn):
        if stmt.value is None:
            return True
        if prescan.ret_storage_opt is not None:
            # A storage-form Optional[F1-record] return admits `None`
            # (-> std::nullopt, F2c) or a borrow `T*` lift (-> ptr_to_optional[_move],
            # copy or move per last-use; F2c copy / F2e move). Storage-field / call
            # sources stay on the AST path.
            return (isinstance(stmt.value, TpyNoneLiteral)
                    or _is_borrow_ptr_local(stmt.value, declared, pointers))
        return _expr_eligible(stmt.value, declared, analyzer)
    if isinstance(stmt, TpyIf):
        if not _condition_eligible(stmt.condition, declared, analyzer):
            return False
        # Branches do not extend the outer scope (no new-name decls allowed in
        # them), so each is checked against the same declared-so-far set.
        return (_body_eligible(stmt.then_body, analyzer, declared, prescan,
                               in_branch=True, pointers=pointers,
                               rebind_slots=rebind_slots)
                and _body_eligible(stmt.else_body, analyzer, declared, prescan,
                                   in_branch=True, pointers=pointers,
                                   rebind_slots=rebind_slots))
    if isinstance(stmt, TpyWhile):
        # No while/else, and a comparison condition. break/continue are not in
        # the stmt set, so a body containing them is rejected by _body_eligible.
        if stmt.orelse or not _condition_eligible(stmt.condition, declared, analyzer):
            return False
        return _body_eligible(stmt.body, analyzer, declared, prescan,
                              in_branch=True, pointers=pointers,
                              rebind_slots=rebind_slots)
    if isinstance(stmt, TpyAugAssign):
        return _scalar_aug_assign_ok(stmt, declared, analyzer)
    if isinstance(stmt, TpyForEach):
        return _for_range_eligible(stmt, analyzer, declared, prescan, pointers,
                                   rebind_slots)
    return False


def _body_eligible(body, analyzer, declared: dict[str, TpyType],
                   prescan: _Prescan, *, in_branch: bool,
                   pointers: set[str], rebind_slots: set[str]) -> bool:
    """Walk a statement list in source order, mirroring lowering's declared-scope
    growth: a top-level new-name var-decl extends scope; branch bodies don't. The
    map carries each name's resolved type (for the mixed-sign comparison gate and
    F1 field-receiver lookup); `pointers` carries the F2 pointer-local names a
    reseat reads, `rebind_slots` the F2d rebind-slot subset whose reseats are
    rvalue rebinds. All copied so sibling branches don't see each other."""
    declared = dict(declared)  # local copy -- sibling branches must not see each other
    pointers = set(pointers)
    rebind_slots = set(rebind_slots)
    for stmt in body:
        if not _stmt_eligible(stmt, analyzer, declared, prescan,
                              in_branch=in_branch, pointers=pointers,
                              rebind_slots=rebind_slots):
            return False
        if (not in_branch and isinstance(stmt, TpyVarDecl)
                and stmt.name not in declared):  # first decl -- keep retro-widened type
            declared[stmt.name] = _var_decl_type(stmt, analyzer)
    return True


def _field_is_arrow(e: TpyFieldAccess, lc: '_LowerCtx') -> bool:
    """`recv->field` vs `recv.field`: a plain `T*` pointer-local (F2) or the `self`
    receiver (a `this` pointer) renders `->`; a record param / `T&` alias receiver
    renders `.`. Decided from the pointer-local set lowering tracks (the same names
    eligibility recorded) plus the method receiver."""
    return (isinstance(e.obj, TpyName)
            and (e.obj.name in lc.pointers or e.obj.name == lc.self_receiver))


def _lower_expr(e: TpyExpr, lc: '_LowerCtx') -> THIRExpr:
    analyzer = lc.analyzer
    rtype = analyzer.get_expr_type(e)
    loc = getattr(e, "loc", None)
    if isinstance(e, TpyName):
        if e.name == lc.self_receiver:
            # The method receiver -> `this`. A borrow (pointer) receiver; only
            # ever reached as a field-access receiver (other `self` positions are
            # gated out), so its form tag is informational.
            return THIRSelf(result_type=rtype, form=Form.BORROW, loc=loc)
        # A non-value name (a record param / REF_ALIAS / POINTER local used as a
        # field receiver) is a borrow; scalars are value form. The receiver's form
        # is not consumed by the field-access emit, but the tag is kept honest.
        form = Form.VALUE if rtype is None or rtype.is_value_type() else Form.BORROW
        return THIRName(result_type=rtype, name=e.name, form=form, loc=loc)
    if isinstance(e, TpyFieldAccess):
        # Scalar field read off a borrow receiver (value-form result). A plain
        # non-null `T*` pointer-local receiver renders `recv->field`; the non-value
        # field source for a borrow-local binding is built in _lower_field_source.
        return THIRFieldAccess(
            result_type=rtype,
            receiver=_lower_expr(e.obj, lc),
            field_cpp=escape_cpp_name(e.field),
            is_arrow=_field_is_arrow(e, lc),
            loc=loc,
        )
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
        return THIRLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyBinOp):
        return THIRBinOp(
            result_type=rtype,
            left=_lower_expr(e.left, lc),
            op=e.op,
            right=_lower_expr(e.right, lc),
            resolved=e.resolved_binop,
            divisor_non_zero=e.divisor_non_zero,
            loc=loc,
        )
    if isinstance(e, TpyCall):
        return THIRCall(
            result_type=rtype,
            callee=e.func_name,
            args=tuple(_lower_expr(a, lc) for a in e.args),
            loc=loc,
        )
    if isinstance(e, TpyCoerce):
        return THIRCoerce(
            result_type=rtype,
            expr=_lower_expr(e.expr, lc),
            coercion_name=e.coercion.name,
            loc=loc,
        )
    raise AssertionError(f"ineligible expr reached lowering: {type(e).__name__}")


def _lower_field_source(e: TpyFieldAccess, lc: '_LowerCtx') -> THIRFieldAccess:
    """The storage-form field read backing a borrow-local binding: `recv.field`
    where the field is a record (REF_ALIAS / POINTER) or a storage-form
    `optional<T>` (OPTIONAL_TO_PTR). form=STORAGE -- the bridge to the local's
    borrow form is the `T&` reference bind (REF_ALIAS) or the wrapping
    THIRFormConvert (`&(...)` for POINTER, `optional_to_ptr` for OPTIONAL_TO_PTR).
    The receiver itself may be a pointer-local (a chained borrow), so `->` vs `.`
    is decided the same way as a value read."""
    return THIRFieldAccess(
        result_type=lc.analyzer.get_expr_type(e),
        receiver=_lower_expr(e.obj, lc),
        field_cpp=escape_cpp_name(e.field),
        is_arrow=_field_is_arrow(e, lc),
        form=Form.STORAGE,
        loc=getattr(e, "loc", None),
    )


class _LowerCtx:
    """Per-function lowering state threaded through `_lower_stmt`.

    `render_type` renders a decl C++ type the way codegen does
    (`TypeResolver.type_to_cpp`): it qualifies cross-module records and resolves
    the live module, which `TpyType.to_cpp()` does not, so it -- not `to_cpp()` --
    is the byte-identical source for an F1 borrow local's cpp_type. The default
    (`to_cpp`) is for analyzer-only callers (dump / standalone lowering) that
    never hit a non-value local."""
    __slots__ = ("analyzer", "func", "prescan", "render_type", "const_locals",
                 "pointers", "rebind_slot_locals", "movable_locals",
                 "self_receiver", "record_name")

    def __init__(self, func: TpyFunction, analyzer, render_type,
                 self_receiver: str | None = None,
                 record_name: str | None = None) -> None:
        self.analyzer = analyzer
        self.func = func
        self.prescan = _Prescan(func, analyzer)
        self.render_type = render_type or (lambda t: t.to_cpp())
        # The receiver name (`self`) when `func` is an instance method, else
        # None: it lowers to a THIRSelf (`this`) and renders `->` field reads
        # like a pointer-local, but unlike `pointers` it is not a liftable
        # borrow source (`_is_borrow_ptr_local` must never treat it as one).
        self.self_receiver = self_receiver
        # The owning record's name when `func` is a method: `_param_is_const`
        # resolves a record param's const verdict from the method's FunctionInfo
        # on this record, not the free-function registry.
        self.record_name = record_name
        self.const_locals: set[str] = set()
        # F2 pointer-local names (reseatable `T*`), recorded at first decl so a
        # later reseat and any `->` read off them lower correctly.
        self.pointers: set[str] = set()
        # F2d rebind-slot subset of `pointers`: their reseats lower as rvalue
        # rebinds (`p = &*(__slot_N = ...)`), not lvalue `&(...)` reseats.
        self.rebind_slot_locals: set[str] = set()
        # F2e: sema's movable (owned) locals -- a borrow write/return source that
        # is one of these at last use moves (`ptr_to_optional_move`). The set only
        # grows during the body walk, so the final sema set matches the working
        # set at any post-decl write/return (see _is_move_source).
        self.movable_locals: set[str] = analyzer.function_movable_locals.get(
            id(func), set())


def _is_move_source(value: TpyExpr, lc: _LowerCtx,
                    movable_names: 'set[str] | None' = None) -> bool:
    """Whether a write / return / MIL source moves rather than copies: the last use
    of a movable (owned) name. Mirrors the AST's `_is_last_use_movable(expr,
    movable_names)` (peel `TpyCoerce`; a `TpyName` in the movable set whose node is a
    last use). `movable_names` defaults to the function's `movable_locals` (the
    F2b/F2e write/return case -- only an F2d REBIND_SLOT local is owned there); the
    ctor MIL passes `own_param_names` instead (M3b-move), since `movable_locals` is
    empty for a ctor and the MIL's movable sources are its Own params."""
    names = lc.movable_locals if movable_names is None else movable_names
    inner = value
    while isinstance(inner, TpyCoerce):
        inner = inner.expr
    return (isinstance(inner, TpyName)
            and inner.name in names
            and id(inner) in lc.analyzer.ctx.all_last_uses)


def _lower_borrow_local(stmt: TpyVarDecl, vtype: TpyType, binding: 'LocalBinding',
                        is_const: bool, lc: _LowerCtx, loc) -> THIRVarDecl:
    """Lower a non-value borrow local's first declaration. REF_ALIAS binds a `T&`
    alias of the field's storage directly (no conversion node). POINTER lifts a
    plain-record lvalue to a reseatable `T*` via THIRFormConvert (`&(...)`).
    OPTIONAL_TO_PTR lifts the storage `optional<Inner>` to a borrow `Inner*` via
    THIRFormConvert (`::tpy::optional_to_ptr`), so its decl type is the inner.
    REBIND_SLOT (F2d) binds a plain-record rvalue (a ctor / by-value call) and
    the emitter materializes the two-slot `__slot_N` machinery; the init lowers
    as a plain value-form call (no conversion node)."""
    if binding is LocalBinding.REBIND_SLOT:
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=_lower_expr(stmt.init, lc),
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    field = _lower_field_source(stmt.init, lc)
    if binding is LocalBinding.REF_ALIAS:
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=field,
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    if binding is LocalBinding.POINTER:
        convert = THIRFormConvert(result_type=vtype, value=field, form=Form.BORROW,
                                  is_const=is_const, loc=loc)
        return THIRVarDecl(
            name=stmt.name, resolved_type=vtype, init=convert,
            cpp_type=lc.render_type(vtype), form=Form.BORROW, is_const=is_const,
            cpp_local_representation=binding, loc=loc)
    inner = vtype.inner  # OptionalType(Inner) -- the borrow points at Inner
    convert = THIRFormConvert(result_type=vtype, value=field, form=Form.BORROW,
                              is_const=is_const, loc=loc)
    return THIRVarDecl(
        name=stmt.name, resolved_type=vtype, init=convert,
        cpp_type=lc.render_type(inner), form=Form.BORROW, is_const=is_const,
        cpp_local_representation=binding, loc=loc)


def _lower_stmt(stmt: TpyStmt, lc: _LowerCtx, declared: dict[str, TpyType]) -> THIRStmt:
    analyzer = lc.analyzer
    loc = getattr(stmt, "loc", None)
    if isinstance(stmt, TpyVarDecl):
        vtype = _var_decl_type(stmt, analyzer)
        # First decl of a non-value borrow local (REF_ALIAS / OPTIONAL_TO_PTR /
        # POINTER).
        if stmt.name not in declared:
            binding = _borrow_local_binding(stmt, vtype, declared, lc.prescan, analyzer)
            if binding is not None:
                if binding is LocalBinding.REBIND_SLOT:
                    # rvalue ctor source: an owned, mutable pointer-local (never
                    # const). Recorded in both sets, as eligibility did.
                    lc.pointers.add(stmt.name)
                    lc.rebind_slot_locals.add(stmt.name)
                    declared[stmt.name] = vtype
                    return _lower_borrow_local(stmt, vtype, binding, False, lc, loc)
                is_const = _f1_is_const(binding, vtype, stmt, lc.func, analyzer,
                                        lc.const_locals, lc.record_name)
                if is_const:
                    lc.const_locals.add(stmt.name)
                if binding is LocalBinding.POINTER:
                    lc.pointers.add(stmt.name)  # later assignments reseat this `T*`
                declared[stmt.name] = vtype
                return _lower_borrow_local(stmt, vtype, binding, is_const, lc, loc)
        # F2d rebind-slot reseat: an rvalue ctor / by-value source. It lowers as a
        # plain value-form call; emit wraps it as `p = &*(__slot_N = <value>)`
        # using the rebind slot allocated at the decl. Checked before the lvalue
        # POINTER reseat -- a rebind-slot local is in both `pointers` sets.
        if stmt.name in lc.rebind_slot_locals:
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=_lower_expr(stmt.init, lc), loc=loc)
        # F2a pointer-local reseat: lift the new lvalue field source to `T*` via
        # `&(...)` (the same storage->borrow convert as the first decl). Eligibility
        # admitted only an F1-record field source here. result_type is the stripped
        # `vtype` (the pointee), matching the first-decl path -- `get_expr_type`
        # would leave a ReadonlyType wrapper the THIR fully-resolved-type invariant
        # forbids (emit strips it either way, so this stays byte-identical).
        if stmt.name in lc.pointers:
            convert = THIRFormConvert(
                result_type=vtype,
                value=_lower_field_source(stmt.init, lc), form=Form.BORROW,
                is_const=stmt.name in lc.const_locals, loc=loc)
            return THIRAssign(
                target=THIRName(result_type=vtype, name=stmt.name, loc=loc),
                value=convert, loc=loc)
        init = _lower_expr(stmt.init, lc) if stmt.init else None
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
        if isinstance(stmt.target, TpyFieldAccess):
            # A borrow `T*` stored into a storage `optional<T>` field lifts
            # borrow->storage via THIRFormConvert (`ptr_to_optional`, F2b); a
            # `None` literal stores as a STORAGE-form None (`std::nullopt`, F2c).
            # The target field-access renders `recv.field` / `recv->field`.
            ftype = analyzer.get_expr_type(stmt.target)
            # A scalar field is a plain value assign -- no borrow<->storage lift.
            if _eligible_scalar(ftype):
                return THIRAssign(target=_lower_expr(stmt.target, lc),
                                  value=_lower_expr(stmt.value, lc), loc=loc)
            if isinstance(stmt.value, TpyNoneLiteral):
                fvalue: THIRExpr = THIRLiteral(result_type=ftype, value=None,
                                               form=Form.STORAGE, loc=loc)
            else:
                fvalue = THIRFormConvert(result_type=ftype,
                                         value=_lower_expr(stmt.value, lc),
                                         form=Form.STORAGE,
                                         move=_is_move_source(stmt.value, lc), loc=loc)
            return THIRAssign(target=_lower_expr(stmt.target, lc), value=fvalue, loc=loc)
        return THIRAssign(
            target=_lower_expr(stmt.target, lc),
            value=_lower_expr(stmt.value, lc),
            loc=loc,
        )
    if isinstance(stmt, TpyAugAssign):
        # `target OP= value` lowers to `target = (target OP value)`, matching the
        # AST's `_gen_aug_assign_code` scalar branch. The target expr is lowered
        # twice (once as the assign lvalue, once as the binop's left operand) --
        # the AST likewise substitutes the same target string into both slots.
        # `divisor_non_zero=False`: the AST aug-assign path never swaps the
        # checked div/mod helper (no source `TpyBinOp` node carries the flag).
        target = _lower_expr(stmt.target, lc)
        binop = THIRBinOp(
            result_type=analyzer.get_expr_type(stmt.target),
            left=_lower_expr(stmt.target, lc),
            op=stmt.op,
            right=_lower_expr(stmt.value, lc),
            resolved=stmt.resolved_binop,
            paren_wrap=False,
            loc=loc,
        )
        return THIRAssign(target=target, value=binop, loc=loc)
    if isinstance(stmt, TpyReturn):
        ret_opt = lc.prescan.ret_storage_opt
        if stmt.value is not None and ret_opt is not None:
            # Lift into a storage-form Optional[record] return slot. `None` lowers
            # to a STORAGE-form None literal (`std::nullopt`, F2c); a borrow `T*`
            # to the borrow->storage THIRFormConvert the F2b write uses --
            # `ptr_to_optional` (copy, F2c) or `ptr_to_optional_move` when the
            # source is an owned local at last use (move, F2e, via _is_move_source).
            if isinstance(stmt.value, TpyNoneLiteral):
                value: THIRExpr = THIRLiteral(result_type=ret_opt, value=None,
                                              form=Form.STORAGE, loc=loc)
            else:
                value = THIRFormConvert(result_type=ret_opt,
                                        value=_lower_expr(stmt.value, lc),
                                        form=Form.STORAGE,
                                        move=_is_move_source(stmt.value, lc), loc=loc)
            return THIRReturn(value=value, loc=loc)
        return THIRReturn(
            value=_lower_expr(stmt.value, lc) if stmt.value else None,
            loc=loc,
        )
    if isinstance(stmt, TpyIf):
        # Branches share `declared`: eligibility guarantees they only reassign
        # already-declared locals (lowered to THIRAssign), so neither branch
        # extends the scope and order stays consistent with the AST path.
        return THIRIf(
            condition=_lower_expr(stmt.condition, lc),
            then_body=tuple(_lower_stmt(s, lc, declared) for s in stmt.then_body),
            else_body=tuple(_lower_stmt(s, lc, declared) for s in stmt.else_body),
            loc=loc,
        )
    if isinstance(stmt, TpyWhile):
        return THIRWhile(
            condition=_lower_expr(stmt.condition, lc),
            body=tuple(_lower_stmt(s, lc, declared) for s in stmt.body),
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
            start = _lower_expr(start_arg, lc)
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
            stop=_lower_expr(stop_arg, lc),
            start=start,
            start_is_literal=start_is_literal,
            stop_is_literal=_range_bound_literal_value(stop_arg) is not None,
            body=tuple(_lower_stmt(s, lc, body_declared) for s in stmt.body),
            loc=loc,
        )
    raise AssertionError(f"ineligible stmt reached lowering: {type(stmt).__name__}")


def lower_function(func: TpyFunction, analyzer, render_type=None,
                   self_type: 'TpyType | None' = None) -> THIRFunction | None:
    """Lower one function to THIR, or None if it falls outside the slice.

    `render_type` (codegen's `TypeResolver.type_to_cpp`) renders F1 borrow-local
    decl types byte-identically; omit it only when no non-value local can arise
    (dump / value-scalar standalone lowering). `self_type` is the owning record's
    type when `func` is an instance method (M1): `self` is seeded as an F1-record
    receiver (a `this` pointer) so its field reads route the same as a param's."""
    if not _function_eligible(func, analyzer, self_type):
        return None
    # Branch-local hoisting is not reproduced -- a function that hoists any
    # local out of a branch stays on the AST path.
    if analyzer.function_hoisted_vars.get(id(func)):
        return None
    is_method = self_type is not None and func.is_method
    self_receiver = "self" if is_method else None
    record_name = self_type.name if is_method and isinstance(self_type, NominalType) else None
    lc = _LowerCtx(func, analyzer, render_type, self_receiver=self_receiver,
                   record_name=record_name)
    params_set: dict[str, TpyType] = {n: t for n, t in func.params}
    if is_method:
        params_set["self"] = self_type  # the record receiver, a field source
        if func.is_readonly:
            # A readonly method's `this` is const, so a borrow local off `self.opt`
            # lifts to `const T*` (the OPTIONAL_TO_PTR const bump keys on the
            # receiver being in const_locals -- see _f1_is_const).
            lc.const_locals.add("self")
    if not _body_eligible(func.body, analyzer, params_set, lc.prescan,
                          in_branch=False, pointers=set(), rebind_slots=set()):
        return None
    params = tuple(THIRParam(name=n, type=t) for n, t in func.params)
    rt = func.return_type if isinstance(func.return_type, TpyType) else VoidType()
    # Seeded with params (and `self`): a write to such a name is a reassignment.
    declared: dict[str, TpyType] = dict(params_set)
    body = tuple(_lower_stmt(s, lc, declared) for s in func.body)
    return THIRFunction(
        name=func.name,
        params=params,
        return_type=rt,
        body=body,
        layout=THIRFunctionLayout(),
    )


def _unwrap_copy(expr: TpyExpr, analyzer) -> TpyExpr:
    """Mirror of `CodeGenContext.unwrap_copy`: peel a `tpy.copy(x)` (the explicit
    field-copy acknowledgment) to `x`, so a `self.f = copy(p)` initializer lowers
    to the same `f(p)` direct-init the bare `self.f = p` does (the MIL copies
    implicitly). Analyzer-pure (reads `imported_names`), so lowering classifies
    without a CodeGenContext."""
    if isinstance(expr, TpyCoerce):
        inner = _unwrap_copy(expr.expr, analyzer)
        return inner if inner is not expr.expr else expr
    if (isinstance(expr, TpyCall) and len(expr.args) == 1
            and isinstance(expr.func, TpyName)
            and expr.func_name in analyzer.imported_names):
        mod, fn = analyzer.imported_names[expr.func_name]
        if mod == "tpy" and fn == "copy":
            return expr.args[0]
    return expr


def _ctor_field_init_ok(stmt: TpyStmt, own_field_names: set[str],
                        own_param_names: set[str], declared: dict[str, TpyType],
                        lc: _LowerCtx) -> bool:
    """A hoistable own-field initializer the ctor MIL slice admits -- a
    `self`-targeted own-field assign whose (field type, source) pair the tail
    emitter reproduces byte-for-byte.

    The `obj.name == "self"` guard is load-bearing -- `_field_receiver_ok` alone
    would also admit `other_record.field = ...`, which is not a member init. The
    own-field test matches `_extract_field_inits`. Routed shapes:

      * **scalar** (M3a): an eligible-scalar value (`f(value)`).
      * **own-param move** (M3b-move): an `Own[...]` source consumed at its last use
        moves into a record / Optional[record] field (`f(std::move(p))`); checked
        before the copy arms because the cascade applies the move first and never
        also lifts via `ptr_to_optional`.
      * **pointer-repr `Optional[F1-record]`** copy (M3b-copy): a `None`
        (`f(std::nullopt)`) or a non-own borrow source (`f(::tpy::ptr_to_optional(p))`)
        -- reuses the F2b optional-write helper.
      * **plain F1-record** copy (M3b-copy): a non-own record param, `copy()`-unwrapped
        (`f(p)`, an implicit MIL copy).

    Ctor-call / field-read sources (M3b-rvalue / a later rung) and every other field
    type (bytes / tuple / union / str / list -> F3+; cross-module / native / generic
    records) leave the ctor on the AST path."""
    analyzer = lc.analyzer
    if not (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field in own_field_names
            and _field_receiver_ok(stmt.target, declared, analyzer)):
        return False
    ftype = analyzer.get_expr_type(stmt.target)
    if _eligible_scalar(ftype):
        return _expr_eligible(stmt.value, declared, analyzer)
    is_opt = (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()
              and _f1_record(ftype.inner, analyzer))
    if not (is_opt or _f1_record(ftype, analyzer)):
        return False
    source = _unwrap_copy(stmt.value, analyzer)
    # M3b-move: an own-param at its last use moves into the field.
    if _is_move_source(source, lc, own_param_names):
        return True
    if is_opt:
        # pointers empty: a ctor MIL has no locals, so the only borrow source the
        # helper admits is a pointer-repr Optional[record] param (or None).
        return _f2b_optional_field_write_ok(stmt, declared, set(), analyzer)
    # Plain record field <- non-own record param (implicit copy).
    return (isinstance(source, TpyName) and source.name not in own_param_names
            and _f1_record(declared.get(source.name), analyzer))


def _ctor_param_eligible(ptype: TpyType | None, analyzer) -> bool:
    """A ctor param the MIL slice can reference: the method-param set (value scalar /
    F1-record, incl. `Own`) plus a pointer-repr `Optional[F1-record]` -- the borrow
    source for an `Optional[record]` field's `ptr_to_optional` (the M3b cell). The
    raw `OptionalType` shape matches what `declared` holds and `_is_borrow_ptr_local`
    tests. The field-init gate decides per-field whether the param is used in an
    admitted way; an unhandled use rejects the whole ctor (-> AST path)."""
    if _f1_param_eligible(ptype, analyzer):
        return True
    return (isinstance(ptype, OptionalType) and ptype.uses_pointer_repr()
            and _f1_record(ptype.inner, analyzer))


def lower_constructor(record, init_method: TpyFunction, analyzer,
                      render_type=None,
                      self_type: 'TpyType | None' = None) -> THIRConstructor | None:
    """Lower a constructor to a THIRConstructor, or None if outside the slice.

    M3a slice (pure-MIL scalar, flat record): a same-module non-generic record with
    no base class whose `__init__` body is entirely hoistable own-scalar field
    initializers (`self.<scalar field> = <eligible scalar>`); a docstring or `pass`
    makes it ineligible (those land in the AST's non-init body, which M3a does not
    emit). Every initializer hoists to the member-init-list (no demotion arises), so
    the emitted C++ ctor body is empty. The signature stays on the AST path (the M1
    method precedent); only the MIL + body tail routes here."""
    if self_type is None or not _f1_record(self_type, analyzer):
        return None
    # Flat records only: a base class needs the base-init list + inherited-field
    # demotion (the M3d rung). Reject overloaded / native / generator / generic
    # __init__ -- those take emit paths the tail emitter does not reproduce.
    ri = analyzer.registry.get_record(record.name)
    if ri is None or ri.parents:
        return None
    if (init_method.is_overload_stub or init_method.native_function
            or init_method.is_async or init_method.is_generator
            or init_method.type_params):
        return None
    # Params must be value scalars, F1-records, or pointer-repr Optional[F1-record]
    # (see `_ctor_param_eligible`). This keeps the AST-emitted signature a plain ctor
    # (no protocol/dynamic template) so it pairs with the THIR tail; a param used in
    # an unhandled way is caught by the per-field init gate below.
    for _name, ptype in init_method.params:
        pt = ptype if isinstance(ptype, TpyType) else None
        if not _ctor_param_eligible(pt, analyzer):
            return None
    # Own[T] / Own[T]|None params: their MIL sources move (M3b-move), so M3b-copy
    # rejects them as record-field sources (mirror `_extract_field_inits`'s set).
    own_param_names = {pname for pname, ptype in init_method.params
                       if isinstance(ptype, TpyType)
                       and unwrap_optional_own(unwrap_readonly(ptype)) is not None}
    declared: dict[str, TpyType] = {n: t for n, t in init_method.params}
    declared["self"] = self_type
    own_field_names = {f.name for f in record.fields}
    # lc is built before the gate loop: the move check (`_is_move_source`) reads
    # `analyzer.ctx.all_last_uses` through it.
    lc = _LowerCtx(init_method, analyzer, render_type, self_receiver="self",
                   record_name=record.name)
    field_inits: list[TpyAssign] = []
    for stmt in init_method.body:
        # Every statement must be a hoistable own-field init. A docstring or `pass`
        # (or any non-init statement) lands in the AST's non_init_stmts -- a codeless
        # but non-empty body, not `{}` -- breaking the empty-body invariant, so it is
        # rejected (the M3c body rung), keeping the emitted body byte-identical.
        if not _ctor_field_init_ok(stmt, own_field_names, own_param_names,
                                   declared, lc):
            return None
        field_inits.append(stmt)
    return THIRConstructor(
        record_name=record.name,
        params=tuple(THIRParam(name=n, type=t) for n, t in init_method.params),
        mil_inits=tuple(_lower_ctor_mil_init(s, own_param_names, lc)
                        for s in field_inits),
    )


def _lower_ctor_mil_init(stmt: TpyAssign, own_param_names: set[str],
                         lc: _LowerCtx) -> THIRMilInit:
    """Build one member-init-list entry from a hoisted field initializer (the gate
    already admitted it). Mirrors the record/Optional arms of `_extract_field_inits`:

      * an **own-param at last use** moves (`move=True`, plain source -- never
        `ptr_to_optional`, per the cascade) [M3b-move];
      * a **scalar** -> the lowered value [M3a];
      * a pointer-repr **Optional[F1-record]** -> a STORAGE `None` literal
        (`std::nullopt`) or the borrow->storage `ptr_to_optional` convert (copy) [M3b-copy];
      * a plain **F1-record** -> the `copy()`-unwrapped source (implicit copy) [M3b-copy]."""
    analyzer = lc.analyzer
    ftype = analyzer.get_expr_type(stmt.target)
    loc = getattr(stmt, "loc", None)
    field_cpp = escape_cpp_name(stmt.target.field)
    source = _unwrap_copy(stmt.value, analyzer)
    if _is_move_source(source, lc, own_param_names):
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc), move=True)
    if _eligible_scalar(ftype):
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(stmt.value, lc))
    if isinstance(ftype, OptionalType):
        if isinstance(stmt.value, TpyNoneLiteral):
            v: THIRExpr = THIRLiteral(result_type=ftype, value=None,
                                      form=Form.STORAGE, loc=loc)
        else:
            v = THIRFormConvert(result_type=ftype, value=_lower_expr(stmt.value, lc),
                                form=Form.STORAGE, move=False, loc=loc)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc))


def _method_self_type(record, analyzer) -> 'TpyType | None':
    """The `self` receiver type for an M1 method feed: the record's canonical
    qualified `NominalType` for a non-generic record, else None (a generic
    record's `self` is templated, outside the F1-record slice). The qname is
    load-bearing -- a bare `NominalType(name)` has no registry entry, so
    `is_user_record` (hence `_f1_record`) is False. `_f1_record` applies the
    remaining native / cross-module gates at lowering."""
    if record.type_params:
        return None
    ri = analyzer.registry.get_record(record.name)
    if ri is None:
        return None
    return NominalType(record.name, _module_qname=ri.qualified_name())


def iter_module_callables(module: TpyModule, analyzer):
    """Yield `(callable, self_type)` for every function / instance method the slice
    may admit -- the single feed list shared by `lower_module` and codegen so the
    two never drift. The eligibility gate still has the final say; this only
    enumerates candidates. Free functions yield `self_type=None`; instance methods
    yield the owning record's type (None-skipped for generic records). The
    constructor is excluded -- its body is emitted via the member-init-list driver
    (the M3 ctor frontier), not gen_method_def."""
    for func in module.functions:
        yield func, None
    for record in module.records:
        self_type = _method_self_type(record, analyzer)
        if self_type is None:
            continue
        init = record.init_method
        for method in record.methods:
            if method is not init:
                yield method, self_type


def iter_module_constructors(module: TpyModule, analyzer):
    """Yield `(record, init_method, self_type)` for every record that defines an
    `__init__` -- the ctor feed for the M3 frontier, the sibling of
    `iter_module_callables` (which excludes the ctor because its body is emitted by
    the member-init-list driver, not `gen_body`). `self_type` is the owning
    record's F1-record receiver (None-skipped for generic records, which
    `lower_constructor` also rejects). The eligibility gate in `lower_constructor`
    has the final say; this only enumerates candidates."""
    for record in module.records:
        init = record.init_method
        if init is None:
            continue
        self_type = _method_self_type(record, analyzer)
        if self_type is None:
            continue
        yield record, init, self_type


def lower_module(module: TpyModule, analyzer, render_type=None) -> THIRModule:
    """Lower every eligible function and instance method in `module`; skip the rest."""
    out = THIRModule(module_name=getattr(analyzer.ctx, "module_name", "generated"))
    for func, self_type in iter_module_callables(module, analyzer):
        thir_fn = lower_function(func, analyzer, render_type, self_type=self_type)
        if thir_fn is not None:
            out.functions.append(thir_fn)
    return out
