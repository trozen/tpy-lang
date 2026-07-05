"""Shared type/shape facts for the THIR gate and lowering.

Leaf predicates over resolved types and small expression shapes: the
eligible-scalar/str/bytes/enum/union/tuple families, F1/F2 record facts,
field/receiver/write facts, narrowing condition info, and the coercion
dispositions. No gate recursion and no lowering -- everything here is
callable from any other lower/ module.
"""

from __future__ import annotations
from dataclasses import field, fields
from ...parse.nodes import (
    TpyAssert,
    TpyAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBytesLiteral,
    TpyCall,
    TpyCoerce,
    TpyExpr,
    TpyFieldAccess,
    TpyFunction,
    TpyIf,
    TpyIntLiteral,
    TpyMethodCall,
    TpyName,
    TpyNoneLiteral,
    TpyReturn,
    TpyStrLiteral,
    TpySubscript,
    TpyUnaryOp,
    TpyVarDecl,
)
from ...typesys import (
    BYTES_FAMILY,
    CHAR,
    FLOAT,
    FloatLiteralType,
    INT32,
    IntLiteralType,
    LiteralType,
    NominalType,
    OptionalType,
    OwnType,
    PendingViewType,
    ReadonlyType,
    STR_FAMILY,
    TpyType,
    TupleType,
    TypeParamRef,
    UnionType,
    VoidType,
    is_float_type,
    is_protocol_type,
    is_void_like_type,
    resolve_int_literals,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...type_def_registry import (
    enum_info_of,
    int_traits_of,
    is_array,
    is_basic_slice_type,
    is_big_int_type,
    is_bool_type,
    is_bytes_type,
    is_bytes_view_type,
    is_char_type,
    is_dict,
    is_enum_type,
    is_fixed_int_type,
    is_int_enum_type,
    is_list,
    is_set,
    is_slice_type,
    is_str_type,
    is_str_view_type,
    is_string_type,
)
from ...coercions import CoercionContext
from ...codegen_cpp.type_resolution import resolve_stmt_binding_type
from ...codegen_cpp.forms import (
    LocalBinding,
    is_ptr_variant_union,
    reads_storage_form_optional,
)
from ...codegen_cpp.context import enum_cpp_name
from ..faces import witness as _witness
from ..nodes import (
    Form,
    THIRArgTemp,
    THIRCoerce,
    THIRExpr,
    THIRFormConvert,
    THIRIsNone,
    THIRLiteral,
    THIRMove,
    THIRName,
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

# Logical and/or (the parser folds `a and b` to TpyBinOp("&&")). Admitted only
# with a bool result over bool operands, where the AST emits the bare C++
# operator (`(l && r)`); the non-bool Python value semantics (`x or default` ->
# temp + ternary via _gen_logical_value) stay on the AST path.
_LOGICAL_OPS = frozenset({"&&", "||"})

# Identity tests. Admitted only as the None test on a pointer-repr Optional
# borrow name (`p is None` / `p is not None` -> `(p ==|!= nullptr)`, the
# THIRIsNone render); every other identity shape (record-vs-record, storage /
# protocol / union operands) takes another _gen_binop identity arm -> AST path.
_IS_OPS = frozenset({"is", "is not"})

# Literal-into-typed-slot coercions the slice reproduces, both pass-throughs on
# the C++ side (the inner literal renders directly in the slot's type): a literal
# into a fixed-int slot, and a float literal into a double `float` slot.
_INT_LIT_COERCION = "int_literal_to_fixed_int"

_FLOAT_LIT_COERCION = "float_literal_to_float"

# `String` (a concat result) into a `str` slot: identity in EVERY position (the
# Coercion carries no codegen lambda -- both sides spell std::string, and a str
# ARG slot's std::string_view converts implicitly), so it lowers as a
# THIRCoerce passthrough. The other str-family coercions are position-dependent
# (materializing at some sinks) -- see _coerce_disposition.
_STRING_TO_STR_COERCION = "string_to_str"

# Str-family coercions that are identity in EVERY position (no codegen lambda):
# both sides of string_to_str spell std::string; the two *_to_strview arms feed
# a std::string_view slot every source converts into implicitly.
_IDENTITY_STR_COERCIONS = frozenset(
    {_STRING_TO_STR_COERCION, "str_to_strview", "string_to_strview"})

# Scalar-cast coercions whose codegen lambda is a fixed template around the
# inner render, position-independent -- mirrored here as `{0}` templates and
# carried on `THIRCoerce.wrap` (computed at lowering, formatted at emit).
# `fixed_int_widening`'s target-typed cast is derived from the coerce's
# expected type at lowering (see `_coerce_wrap`), not listed here.
_TEMPLATE_COERCIONS: dict[str, str] = {
    "int_literal_to_float": "static_cast<double>({0})",
    "fixed_int_to_float": "static_cast<double>({0})",
    "float32_to_float": "static_cast<double>({0})",
    "int_literal_to_float32": "static_cast<float>({0})",
    "fixed_int_to_float32": "static_cast<float>({0})",
    "float_to_float32": "static_cast<float>({0})",
    "fixed_int_to_bigint": "::tpy::BigInt({0})",
    "bigint_to_float": "static_cast<double>({0})",
    "bigint_to_float32": "static_cast<float>({0})",
}

# The Float32-targeted float literal: an identity lambda whose `f`-suffix
# render comes from the literal seeing the coerce TARGET (the AST forwards it
# into gen_expr; lowering mirrors by retyping the THIRLiteral to Float32).
_FLOAT32_LIT_COERCION = "float_literal_to_float32"

# Its BigInt twin: `int_literal_to_bigint` is an identity lambda whose
# `::tpy::BigInt(...)` wrap comes from the literal's BigInt resolution --
# lowering retypes the THIRLiteral so the emitter picks the ctor arms.
_BIGINT_LIT_COERCION = "int_literal_to_bigint"

def _coerce_wrap(e: TpyCoerce) -> 'str | None':
    """The `{0}` render template mirroring the coercion's codegen lambda for
    the scalar-cast family, else None. `fixed_int_widening`'s and
    `bigint_to_fixed_int`'s target-typed spellings are derived from the
    coerce's expected type verbatim (the lambdas read `b.to_cpp()` off the
    same node field)."""
    name = e.coercion.name
    if name in _TEMPLATE_COERCIONS:
        return _TEMPLATE_COERCIONS[name]
    if name == "fixed_int_widening":
        return f"static_cast<{e.expected_type.to_cpp()}>({{0}})"
    if name == "bigint_to_fixed_int":
        return f"({{0}}).to_fixed_check<{e.expected_type.to_cpp()}>()"
    return None

def _coerce_disposition(e: TpyCoerce) -> 'str | None':
    """'identity' (emit passthrough), 'materialize' (`std::string(x)`, lowered
    to the S1 view->owned THIRFormConvert), 'template' (a scalar cast rendered
    through `_coerce_wrap`'s `{0}` template), or None (outside the slice).

    Mirrors the tpyc/coercions.py codegen lambdas exactly, reading the same
    facts off the node: `strview_to_str` is identity at a plain ARG slot (a
    `str` param spells std::string_view) and materializes at INIT/ASSIGN/
    RETURN; an `Own[...]` ARG slot is rejected -- the surrounding gen_call_arg
    auto-move cascade is its own deferred frontier. `str_to_string`
    materializes only at ARG for a non-literal source; a NUL-free literal is
    const char[N], binding const std::string& directly (the lambda's
    startswith('"') token check made structural: cpp_string_literal_expr emits
    the bare-quote form exactly when the value is NUL-free).
    `strview_to_string` (const std::string& slot / owned String target)
    materializes in every position. The Optional and Char arms have their own
    renders -> AST path."""
    name = e.coercion.name
    if name in (_INT_LIT_COERCION, _FLOAT_LIT_COERCION,
                _FLOAT32_LIT_COERCION, _BIGINT_LIT_COERCION):
        return "identity"
    if name in _IDENTITY_STR_COERCIONS:
        return "identity"
    if isinstance(e.expected_type, OwnType):
        return None
    # Scalar casts stay below the Own reject: at an `Own[...]` slot the arg
    # cascade owns the decision (conservative under-routing, not a mirror gap).
    if _coerce_wrap(e) is not None:
        return "template"
    if name == "strview_to_str":
        return ("identity" if e.context_kind == CoercionContext.ARG
                else "materialize")
    if name == "str_to_string":
        if e.context_kind != CoercionContext.ARG:
            return "identity"
        if isinstance(e.expr, TpyStrLiteral) and "\x00" not in e.expr.value:
            return "identity"
        return "materialize"
    if name == "strview_to_string":
        return "materialize"
    return None

def _peel_stale_view_owned_coerce(init: TpyExpr, binding_t: TpyType | None,
                                  analyzer) -> TpyExpr:
    """gen_expr's stale-coerce identity arm, mirrored at the decl/reassign
    sink: a str/bytes view->owned coerce is attached against the ANNOTATED
    owned type, but the binding's storage is decided later by the pending
    view resolution -- when the binding resolved VIEW, materializing would
    bind the view to an owned temporary dying at end of statement (dangling),
    so the AST renders the source bare. Returns the peeled source (to lower
    in the coerce's place) or the original init."""
    if not isinstance(init, TpyCoerce):
        return init
    if init.coercion.name == "strview_to_str":
        rt = _resolved_str_value(binding_t, analyzer)
        if rt is not None and is_str_view_type(rt):
            return init.expr
    if init.coercion.name == "bytesview_to_bytes":
        rt = _resolved_bytes_value(binding_t, analyzer)
        if rt is not None and is_bytes_view_type(rt):
            return init.expr
    return init

def _eligible_scalar(t: TpyType | None) -> bool:
    """A type the emitter can render and reason about without form facts.

    Fixed-width ints, `bool`, both float widths (`double` / `float`), and
    BigInt (`::tpy::BigInt` -- heap-backed but a value type with overloaded
    operators, so reads/writes/ops render bare like any scalar): borrow/
    storage form never arises and the C++ spelling comes straight from
    `TpyType.to_cpp()`. Target-typed literal renders (the Float32 `f`
    suffix, the BigInt ctor wraps) ride `_slot_literal_retype` at the slot
    sites plus the literal coerce arms, which retype the literal so the
    emitter picks the wrapped render.
    """
    return t is not None and (is_fixed_int_type(t) or is_bool_type(t)
                              or is_float_type(t) or is_big_int_type(t))

def _eligible_value_union(t: TpyType | None) -> 'UnionType | None':
    """The F4 U1 slice: a value-form union of eligible scalar members
    (`Int32 | Float64 [| None]`) -- `std::variant<...>` with no borrow/storage
    duality, so reads/writes/returns/same-type args render bare (the variant
    converting ctor does the work) and a `None` source renders
    `std::monostate{}`. Unions with str/view members (form-relevant per slot),
    Char members (target-typed literal renders), records (pointer-variant,
    U2), or a recursive-alias wrapper ride later F4 cells."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, UnionType) or t.needs_wrapper():
        return None
    if not all(_eligible_scalar(m) or is_void_like_type(m) for m in t.members):
        return None
    return t

def _eligible_ptr_union(t: TpyType | None, analyzer) -> 'UnionType | None':
    """The F4 U2 slice: a pointer-repr union of record members (`A | B
    [| None]` -> borrow `std::variant<[std::monostate, ]A*, B*>` / storage
    `std::variant<[std::monostate, ]A, B>`). Non-None members must be
    `_f1_record`-renderable (any non-generic user record -- native / cross-module
    type spelling agrees with the resolver; only generics stay off), so both C++
    spellings render byte-identically; a None member is the monostate slot --
    both spellings render it `std::monostate`, so it adds no form question.
    Recursive-alias wrappers and protocol unions ride later cells."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not (isinstance(t, UnionType) and is_ptr_variant_union(t)):
        return None
    if not all(_f1_record(m, analyzer) or is_void_like_type(m)
               for m in t.members):
        return None
    return t

def _union_binding_divergent(e: 'TpyName', locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A union-declared name whose read type is NOT that union: sema's
    assignment narrowing retyped the read to a member (`x: I | F = k; v = x`
    reads `x` as Int32), but the AST renders the bare variant name into the
    member-typed sink -- invalid C++ without a `std::get` (a pre-existing AST
    miscompile, see BUGS.md). No green corpus case can exercise it, so any
    narrowing-divergent union read stays on the AST path."""
    bt = locals_.get(e.name)
    if bt is None:
        return False
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
    if not isinstance(bt, UnionType):
        return False
    rt = analyzer.get_expr_type(e)
    rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
          if rt is not None else None)
    return rt != bt

def _isinstance_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, tuple[TpyType, ...], bool] | None':
    """The F4 U3 isinstance-condition shape: `isinstance(v, A)` /
    `isinstance(v, (A, B))` on a declared local/param of a routed union (U1
    value / U2 pointer-variant). Returns `(var, union, check_members,
    folded_true)` or None. `folded_true` is sema's exhaustiveness constant-fold
    (`macro_expansion == True`, the last elif of an exhausted union): the
    condition renders `true` and the AST suppresses the dead implicit-else
    (`_condition_static_true`). Out of the slice: Any / polymorphic /
    deref-view / type-param subjects (different extraction machinery),
    readonly-qualified subjects (the `ptr_variant_to_const` chain stays AST,
    the U2 verdict), and indirect / frame-slot names (no globals or resumable
    frames route)."""
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if cond.isinstance_type_param or cond.isinstance_deref_depth:
        return None
    me = cond.macro_expansion
    folded = isinstance(me, TpyBoolLiteral) and me.value is True
    if me is not None and not folded:
        return None
    var = cond.isinstance_var
    dt = declared.get(var)
    if dt is None or unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt))) is not dt:
        return None
    u = _eligible_value_union(dt) or _eligible_ptr_union(dt, analyzer)
    if u is None:
        return None
    ct = cond.isinstance_type
    members = tuple(ct.members) if isinstance(ct, UnionType) else (ct,)
    # Each check member must be a member of the subject union (sema enforces;
    # kept as a slice guard so a fact mismatch gate-rejects, never mis-lowers).
    if not all(any(m == cm for m in u.members) for cm in members):
        return None
    return var, u, members, folded

def _narrow_fact_member(u: UnionType, facts: dict[str, TpyType],
                        var: str) -> TpyType | None:
    """The concrete-member extraction fact for `var` in a branch's type-facts
    map, or None when the branch keeps the variant un-extracted (no fact, or a
    remaining-union / void fact -- mirrors `_emit_isinstance_extractions`'
    union/void skip). A fact that is neither a member of `u` nor union/void has
    no mirrored emit; the caller gate-rejects on it via `_narrow_facts_ok`."""
    ft = facts.get(var)
    if ft is None or isinstance(ft, UnionType) or is_void_like_type(ft):
        return None
    return ft if any(m == ft for m in u.members) else None

def _narrow_facts_ok(u: UnionType, facts: dict[str, TpyType], var: str) -> bool:
    """A branch facts map the slice can mirror: facts describe only the checked
    var (a compound condition or deref-view key would carry other entries), and
    each fact is a member of the union, a remaining union, or void."""
    for k, ft in facts.items():
        if k != var:
            return False
        if not (isinstance(ft, UnionType) or is_void_like_type(ft)
                or any(m == ft for m in u.members)):
            return False
    return True

def _facts_have_concrete(facts: dict[str, TpyType]) -> bool:
    """Mirror of `_has_concrete_isinstance_facts`: whether a facts map would
    emit an extraction -- the AST's elif-flattening gate (a concrete else-fact
    forces `} else {` + a nested if instead of a flat `else if`)."""
    return any(
        not (isinstance(ty, (UnionType, LiteralType)) or is_void_like_type(ty))
        and not is_protocol_type(ty)
        for ty in facts.values()
    )

def _is_elif_link(outer: TpyIf, inner: TpyIf) -> bool:
    """Mirror of StatementGenerator._is_elif (and emit._is_elif): an elif keeps
    the outer's column; a nested `else: if` sits deeper. Both-locs-None (macro
    fragments) counts as elif."""
    if outer.loc is None and inner.loc is None:
        return True
    if outer.loc is None or inner.loc is None:
        return False
    return inner.loc.column == outer.loc.column

def _elif_link(stmt: TpyIf) -> 'TpyIf | None':
    """The single elif continuation in `stmt`'s else body (the AST's
    is_elif_continuation shape: one same-column TpyIf), or None for a genuine
    else block. Whether the link then FLATTENS to `else if` additionally
    requires no concrete else-fact (`_facts_have_concrete`) -- the callers
    that flatten check that separately, mirroring _gen_if's chain collect."""
    if (len(stmt.else_body) == 1 and isinstance(stmt.else_body[0], TpyIf)
            and _is_elif_link(stmt, stmt.else_body[0])):
        return stmt.else_body[0]
    return None

def _post_if_narrow_fact(
        stmt: TpyIf, info, narrowed: 'set[str] | dict[str, str]',
) -> TpyType | None:
    """The early-return implicit-else fact: when the then-body terminates with
    a return and there is no else block, code after the if is the else branch,
    and the AST emits a persistent statement-level extraction (`_gen_if`'s
    post-narrowing arm, `_narrows_to_union_member` + not-already-narrowed).
    `narrowed` is the active narrowing scope (eligibility's set / lowering's
    alias map -- only membership is read). Returns the member fact or None."""
    var, u, _members, folded = info
    if folded or stmt.else_body or not stmt.else_type_facts:
        return None
    if var in narrowed:
        return None
    if not (stmt.then_body and isinstance(stmt.then_body[-1], TpyReturn)):
        return None
    return _narrow_fact_member(u, stmt.else_type_facts, var)

def _chain_post_if_fact(
        stmt: TpyIf, declared: dict[str, TpyType],
        narrowed: 'set[str] | dict[str, str]', analyzer,
) -> 'tuple[str, UnionType, TpyType] | None':
    """The post-if extraction for a whole if statement: the AST collects the
    flat elif chain first and runs post-narrowing on `chain[-1]`, emitting the
    persistent alias at the ENCLOSING scope -- so the fact belongs to the last
    FLATTENABLE link (same column, no concrete intermediate else-fact),
    whatever the head condition's kind (a plain-headed chain can still end in
    a narrowing elif). A nested `else: if` is a body statement of its else
    block and handles its own post-if there. Returns `(var, union, member)`
    or None."""
    last = stmt
    while ((nxt := _elif_link(last)) is not None
           and not _facts_have_concrete(last.else_type_facts)):
        last = nxt
    info = _isinstance_narrow_info(last.condition, declared, analyzer)
    if info is None:
        return None
    post = _post_if_narrow_fact(last, info, narrowed)
    if post is None:
        return None
    return info[0], info[1], post

def _reassert_bump_info(
        stmt: TpyAssert, declared: dict[str, TpyType],
        persistent_narrowed, analyzer,
) -> 'tuple[str, TpyType] | None':
    """The U4 re-assert: `assert isinstance(v, A)` on a subject already
    PERSISTENTLY extracted to that same member. Sema folds the condition
    (`macro_expansion == True`, so the AST emits `if (!(true)) ...`) and the
    extraction re-runs with a suffix-bumped alias (`__v` -> `__v_2`)
    targeting the original variant. `persistent_narrowed` answers whether
    the subject's live alias is statement-level (eligibility's set /
    lowering's derived var set) -- after a branch/loop-scoped extraction the
    same AST emit REDECLARES the alias in the same C++ block (the BUGS.md
    `_gen_while` collision), so those reject. Returns `(var, member)` or
    None."""
    cond = stmt.condition
    if not (isinstance(cond, TpyCall) and cond.isinstance_var is not None
            and cond.isinstance_type is not None):
        return None
    if cond.isinstance_type_param or cond.isinstance_deref_depth:
        return None
    me = cond.macro_expansion
    if not (isinstance(me, TpyBoolLiteral) and me.value is True):
        return None
    var = cond.isinstance_var
    if var not in persistent_narrowed:
        return None
    member = declared.get(var)
    facts = stmt.then_type_facts
    if member is None or list(facts) != [var] or facts[var] != member:
        return None
    return var, member

def _folded_neg_int_literal(e: TpyExpr, analyzer) -> int | None:
    """The negated value of a unary-minus int literal (`-3`), or None.

    Mirrors `_gen_unaryop`'s literal-negation fold exactly (op `-`, a
    `TpyIntLiteral` operand of `IntLiteralType`): the AST renders
    `_gen_int_literal_value(-v, target)`, which is the bare `-v` token for
    every value in the +-int32 literal range the slice admits (targets are
    fixed-int slots the literal provably fits, or target-less positions) --
    the same `str(v)` a `THIRLiteral` emits. Values whose negation falls
    outside the range return None (the wide-literal suffix/cast renders)."""
    if not (isinstance(e, TpyUnaryOp) and e.op == "-"
            and isinstance(e.operand, TpyIntLiteral)
            and isinstance(analyzer.get_expr_type(e.operand), IntLiteralType)):
        return None
    v = -e.operand.value
    return v if -2**31 <= v <= 2**31 - 1 else None

def _resolved_scalar(t: TpyType | None, analyzer) -> bool:
    """`_eligible_scalar` over a type that may still be an IntLiteralType: a
    literal-seeded container leaves IntLiteral element types on its use sites
    (the sema-resolved method fi's slots, a `pop`/subscript result, print args of
    its loop var) -- the AST path resolves these through TypeResolver/default-int
    at emit; the emitted value is the same bare literal either way. Readonly/Ref
    wrappers are peeled first: a scalar coerced into a `readonly[K]` slot carries
    the wrapper on its expr type, and a readonly scalar is representationally
    the same C++ value.

    Companion convention: a RECEIVER gate reads the declared/`locals_` BINDING
    type, never `get_expr_type` on the name -- a literal-seeded local's use sites
    carry the pre-resolution pending container type (see `_is_len_call`,
    `_method_call_eligible`, `_container_subscript_value_read`,
    `_for_each_container_eligible`)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return _eligible_scalar(
        resolve_int_literals(t, analyzer.ctx.default_int_for_literal))

def _resolve_pending_view(t: TpyType | None, analyzer) -> TpyType | None:
    """A `PendingStrType`/`PendingBytesType` resolved to its concrete view/owned
    type through the family's ViewVarInfo (sema's usage resolution is FINAL
    pre-lowering), else None. Mirrors codegen's `_resolve_view_storage`: no
    registry entry (or an unresolved one) falls back to the owned type."""
    if not isinstance(t, PendingViewType):
        return None
    info = analyzer.ctx.view_vars(t.family).get(t.var_id)
    return (info.resolved_type if info is not None and info.resolved_type
            else t.family.owned_type)

def _resolved_str_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The sema-RESOLVED str-slice type -- owned `str` (`std::string` storage /
    `std::string_view` param) or `StrView` (`std::string_view`) -- or None
    outside the slice. A str local's binding type stays `PendingStrType` on the
    AST/sema side; resolve it like `_resolve_pending_view` does. `String`,
    `Char`, `Literal[str]`-annotated bindings, and the bytes family stay on the
    AST path (later cells)."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return _resolve_pending_view(t, analyzer) if t.family is STR_FAMILY else None
    if isinstance(t, NominalType) and (is_str_type(t) or is_str_view_type(t)):
        return t
    return None

def _resolved_bytes_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The bytes twin of `_resolved_str_value`: the sema-RESOLVED bytes-slice
    type -- owned `bytes` (`std::vector<uint8_t>` storage / `std::span<const
    uint8_t>` param) or `BytesView` (span) -- or None outside the slice. A
    bytes local's binding stays `PendingBytesType`; resolve it through the
    family's ViewVarInfo. `bytearray` (a reference type, different axis) and
    `Literal[bytes]` bindings stay on the AST path."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return (_resolve_pending_view(t, analyzer)
                if t.family is BYTES_FAMILY else None)
    if isinstance(t, NominalType) and (is_bytes_type(t) or is_bytes_view_type(t)):
        return t
    return None

def _resolved_viewfam_value(t: TpyType | None, analyzer) -> TpyType | None:
    """The resolved str- OR bytes-family slice value -- the two view families
    share the slice/iteration receiver shapes and emit machinery -- or None."""
    st = _resolved_str_value(t, analyzer)
    return st if st is not None else _resolved_bytes_value(t, analyzer)

def _bytes_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A bytes-slice comparison operand: a bytes literal (rendered OWNED --
    `_comparison_targets` threads no target for bytes, so the AST's
    `gen_expr(lit, None)` takes the owned arm) or a bytes/BytesView value.
    Guards the compare arm's operand pin -- see `_binop_eligible`."""
    if isinstance(e, TpyBytesLiteral):
        return True
    return _resolved_bytes_value(t, analyzer) is not None

def _str_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A str-slice comparison operand: a str literal (its expr type is
    `LiteralType[str]`, but the const char[N] emit is position-independent),
    a str/StrView value, or a `String` value (a concat result -- std::string
    takes the same compare templates / bare operators, rendered bare). Guards
    the compare arm's operand pin -- see `_binop_eligible`."""
    if isinstance(e, TpyStrLiteral):
        return True
    return (_resolved_str_value(t, analyzer) is not None
            or _is_string_owned(t))

def _is_string_owned(t: TpyType | None) -> bool:
    """A `tpy.String` value -- the owned std::string type a str-family concat
    produces (and the resulting type of a local bound to one). Kept separate
    from `_resolved_str_value` deliberately: String PARAMS spell
    `const std::string&` in the signature, a shape the S1 param emit does not
    reproduce, so the param/return/call gates must keep rejecting String while
    the concat slice admits it for operands, locals, len and print args."""
    if t is None:
        return False
    return is_string_type(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))))

def _str_concat_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A str-concat operand the slice renders bare into the resolved dunder's
    template: a str literal (const char[N]), a str/StrView value (param, local,
    or owned-str call result), or a `String` value (a concat-result local /
    nested concat). A `Char` operand's overload wraps it in `char_to_str` with
    its own operand render -- out of the slice (S4 introduces Char values)."""
    if isinstance(e, TpyStrLiteral):
        return True
    return (_resolved_str_value(t, analyzer) is not None
            or _is_string_owned(t))

def _bytes_concat_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A bytes-concat operand rendered bare into `::tpy::bytes_concat(l, r)`:
    a bytes literal (rendered OWNED -- the resolved `__add__` overload's
    receiver/param target is owned `bytes`, never `BytesView`, so the AST's
    target-threaded render takes the owned arm) or a bytes/BytesView value
    (param, local, nested concat, or owned-bytes call result -- names and
    spans render bare; the vector->span conversion at the span params is
    implicit). A `bytearray` operand also resolves to the native
    `bytes_concat` dunder but is not a bytes-slice value -> AST path."""
    if isinstance(e, TpyBytesLiteral):
        return True
    return _resolved_bytes_value(t, analyzer) is not None

def _peel_coerce(e: TpyExpr) -> TpyExpr:
    """The expression under any stack of TpyCoerce wrappers."""
    while isinstance(e, TpyCoerce):
        e = e.expr
    return e

def _str_self_append_rhs(target_name: str, value: TpyExpr) -> 'TpyExpr | None':
    """The `x = x + y` self-append trigger, mirroring the AST's
    `_try_str_inplace_append` shape test exactly: peel any TpyCoerce wrappers,
    then match `TpyBinOp("+", TpyName(target), rhs)`. Returns the rhs (`y`) the
    peephole appends, or None when the shape does not match (the assignment
    then lowers as a plain reassign). The TARGET-type half of the AST condition
    is checked by the caller via `_owned_str_append_target`."""
    inner = _peel_coerce(value)
    if (isinstance(inner, TpyBinOp) and inner.op == "+"
            and isinstance(inner.left, TpyName)
            and inner.left.name == target_name):
        return inner.right
    return None

def _owned_str_append_target(t: TpyType | None, analyzer) -> bool:
    """The target-type half of the AST's in-place-append conditions (the str
    `+=` branch and `_try_str_inplace_append`): the binding is owned-str-family
    -- `str`, `String`, or a `PendingStrType`. The AST admits ANY pending
    binding; here the pending must RESOLVE owned, which is equivalent for every
    shape that reaches lowering (the very reassign/aug-assign being checked
    forces the owned resolution) and keeps the emitted `t += v;` honest."""
    st = _resolved_str_value(t, analyzer)
    if st is not None:
        return is_str_type(st)
    return _is_string_owned(t)

def _str_name_form(name: str, resolved: TpyType, param_names: set[str]) -> Form:
    """The C++ shape of a str-slice NAME read -- mirrors the AST's
    `_is_str_view_source`: a `StrView`-resolved binding and a `str`-typed param
    (the signature spells `std::string_view`) are view/BORROW; an owned local is
    `std::string` (STORAGE). The owned-sink copy (`std::string(x)` at a decl
    init / return) fires only on a BORROW source; a str literal is const
    char[N] (implicitly convertible both ways) and stays VALUE, never wrapped."""
    if is_str_view_type(resolved) or name in param_names:
        return Form.BORROW
    return Form.STORAGE

def _bytes_name_form(name: str, resolved: TpyType, param_names: set[str]) -> Form:
    """The bytes twin of `_str_name_form`, mirroring the AST's
    `_is_bytes_view_source`: a `BytesView`-resolved binding and a `bytes`-typed
    param (the signature spells `std::span<const uint8_t>`) are view/BORROW --
    they drive the owned-sink `::tpy::bytes_copy(x)` -- while an owned local is
    `std::vector<uint8_t>` (STORAGE)."""
    if is_bytes_view_type(resolved) or name in param_names:
        return Form.BORROW
    return Form.STORAGE

def _eligible_char(t: TpyType | None) -> bool:
    """A `Char` value (C++ `char`): value-scalar-like for the shapes this slice
    routes -- str subscript results, str-iteration loop vars, params/returns,
    compare operands, print args (streamed raw; Char has no int_traits, so no
    int8 cast arises). Kept out of `_eligible_scalar` deliberately: a str
    literal in a Char-typed slot renders as a target-typed C++ char literal
    (`'x'`), which the scalar positions do not thread -- each Char position
    gates its literal shape explicitly (single-char literals route at compare
    operands, annotated decl inits, and Char-slot call args; the reassign /
    return guards stay as defensive rejects -- sema type-errors those)."""
    if t is None:
        return False
    return is_char_type(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t))))

def _runtime_bigint(t: TpyType | None, analyzer) -> bool:
    """`TypeResolver.is_runtime_bigint`'s type half: a concrete BigInt value,
    or an IntLiteralType whose module default int is BigInt. Guards the
    positions whose AST render NARROWS a BigInt (`.to_fixed_check<int32_t>()`
    at subscript indices / slice bounds, the range-counter machinery) -- the
    slice pins those to fixed-int operands and defers the narrow arms."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return is_big_int_type(
        resolve_int_literals(t, analyzer.ctx.default_int_for_literal))

_BIGINT_NARROW = "bigint_narrow"  # synthetic THIRCoerce tag (not a sema coercion)

_BIGINT_INDEX_NARROW_WRAP = "{0}.to_fixed_check<int32_t>()"

def _unwrap_lit_coerce(e: TpyExpr) -> TpyExpr:
    """Strip sema's int-literal slot coercions (fixed-int / BigInt targets) so
    literal-shape checks see the digit token the AST renders."""
    while (isinstance(e, TpyCoerce)
           and e.coercion.name in (_INT_LIT_COERCION, _BIGINT_LIT_COERCION)):
        e = e.expr
    return e

def _bigint_index_disposition(index: TpyExpr, analyzer) -> str:
    """How a subscript index / str-slice-adjacent int position renders when its
    type half is a runtime BigInt -- gen_index_expr's decision, written once so
    the gates and the wrap sites cannot drift:

      * 'bare' -- not runtime-BigInt, or an int32-range (possibly negated) int
        literal: `_is_int_constant` exempts those from the narrow, and the
        emitter renders an unresolved IntLiteralType literal as the bare token
        on both paths;
      * 'narrow' -- the `{0}.to_fixed_check<int32_t>()` wrap (no outer parens:
        any composite render already carries its own);
      * 'reject' -- an out-of-int32-range literal: the AST renders the BigInt
        ctor wrap inside the narrow, a shape the literal emit does not
        reproduce."""
    if not _runtime_bigint(analyzer.get_expr_type(index), analyzer):
        return "bare"
    c = _const_index(index)
    if c is not None:
        return "bare" if -(2**31) <= c <= 2**31 - 1 else "reject"
    if _const_index(_unwrap_lit_coerce(index)) is not None:
        # A coerce-wrapped literal fails `_is_int_constant` on the AST path, so
        # the narrow would wrap the literal's target-typed render -- a shape
        # not observed at index positions (sema leaves indices unwrapped);
        # defensive reject rather than a guessed mirror.
        return "reject"
    return "narrow"

def _narrow_bigint_index(idx: 'THIRExpr', e: TpyExpr, analyzer,
                         loc) -> 'THIRExpr':
    """Wrap a lowered runtime-BigInt index in the `.to_fixed_check<int32_t>()`
    narrow when its disposition says so (reads, del-item); 'reject' never
    reaches lowering (the gates exclude it)."""
    if _bigint_index_disposition(e, analyzer) != "narrow":
        return idx
    _witness("narrow.subscript_index")
    return THIRCoerce(result_type=INT32, expr=idx,
                      coercion_name=_BIGINT_NARROW,
                      wrap=_BIGINT_INDEX_NARROW_WRAP, loc=loc)

def _eligible_enum(t: TpyType | None, analyzer) -> 'TpyType | None':
    """A registered enum value type of any flavor: same-module (bare name),
    cross-module (qualified `::tpyapp::m::E`), @native (user qname + member
    rename map + the `__repr__` print arm), or nested (`Outer.Kind`, the
    record-scoped `A::B` spelling). Value-position spellings all route
    through `enum_cpp_name` at lowering (member access, `E(x)`) or through
    the shared `native_cpp_names`-aware `to_cpp()` / `render_type` (decl
    types), so no per-flavor emit split is needed here. Returns the
    unwrapped enum type, or None."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not is_enum_type(t):
        return None
    if enum_info_of(t) is None:
        return None
    return t

def _enum_member_cpp(e: TpyFieldAccess, analyzer) -> str:
    """The rendered `E::A` spelling for a type-level enum member access --
    gen_expr's BindingKind.ENUM arm (and the chained nested-access arm):
    enum_cpp_name over the current module (bare / `Outer::Kind` /
    `::tpyapp::m::E` / @native qname) plus the @native member rename map."""
    enum_type = e.enum_member_of
    einfo = enum_info_of(enum_type)
    member_cpp = (einfo.cpp_member_name_map.get(e.field, e.field)
                  if einfo is not None else e.field)
    spelled = enum_cpp_name(enum_type, analyzer.ctx.module_name, einfo=einfo)
    return f"{spelled}::{member_cpp}"

def _enum_compare_pair(e: TpyBinOp, lt: TpyType | None, rt: TpyType | None,
                       analyzer) -> bool:
    """Enum comparison operands: the same eligible enum on both sides (the
    bare/templated compare -- `(c == Color::RED)`, enum class operators), or
    -- when sema set `int_enum_coercion` (ordering over IntEnums, or an
    IntEnum against an int) -- each int-enum side casting to the underlying
    type at emit (`static_cast<int32_t>(p) >= static_cast<int32_t>(...)`,
    the AST's per-side post-generation casts)."""
    ie = e.int_enum_coercion
    if ie is not None:
        if _eligible_enum(ie, analyzer) is None:
            return False
        return all(_eligible_enum(t, analyzer) is not None
                   or _resolved_scalar(t, analyzer) for t in (lt, rt))
    el, er = _eligible_enum(lt, analyzer), _eligible_enum(rt, analyzer)
    return el is not None and er is not None and el == er

def _binop_operand_casts(e: TpyBinOp, analyzer) -> 'tuple[str | None, str | None]':
    """The per-side post-generation operand casts of _gen_binop's comparison
    path, as `{0}` wraps:

      * `int_enum_coercion` set -> whichever operand is IntEnum-typed casts
        to the underlying type (`static_cast<int32_t>(p)`);
      * a mixed BigInt/float comparison -> the BigInt operand casts to the
        float operand's C++ type (BigInt has no implicit conversion to
        double, `static_cast<double>(b) < x`).
    """
    def operand_t(operand):
        t = analyzer.get_expr_type(operand)
        return (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
                if t is not None else None)

    ie = e.int_enum_coercion
    if ie is not None:
        u_cpp = enum_info_of(ie).underlying_type.to_cpp()
        wrap = f"static_cast<{u_cpp}>({{0}})"

        def enum_side(operand):
            t = operand_t(operand)
            return wrap if t is not None and is_int_enum_type(t) else None

        return (enum_side(e.left), enum_side(e.right))
    if e.op in _COMPARE_OPS:
        lt, rt = operand_t(e.left), operand_t(e.right)
        if lt is not None and rt is not None:
            if is_big_int_type(lt) and is_float_type(rt):
                return (f"static_cast<{rt.to_cpp()}>({{0}})", None)
            if is_big_int_type(rt) and is_float_type(lt):
                return (None, f"static_cast<{lt.to_cpp()}>({{0}})")
    return (None, None)

def _enum_truthy_wrap(t: TpyType | None, analyzer) -> 'str | None':
    """The truthiness render for an enum-typed operand, as a `{0}` wrap --
    gen_truthy_expr's enum arms: an IntEnum tests its underlying value
    (`(static_cast<U>({0}) != 0)`); a plain enum is ALWAYS truthy and renders
    the literal `true` with the operand dropped. None for non-enum types."""
    et = _eligible_enum(t, analyzer)
    if et is None:
        return None
    if is_int_enum_type(et):
        u_cpp = enum_info_of(et).underlying_type.to_cpp()
        return f"(static_cast<{u_cpp}>({{0}}) != 0)"
    return "true"

def _enum_prop_wrap(e: TpyFieldAccess, analyzer) -> 'str | None':
    """An enum instance property read, as a `{0}` wrap over the receiver
    render (gen_expr's enum property arms): `c.value` -> `static_cast<U>({0})`
    (U = the underlying type), `c.name` -> `::tpy::EnumUtil<E>::name({0})`
    (a `string_view` into EnumUtil's static member-name storage; sema types
    it StrView, so lowering tags it BORROW and owned-str sinks fire the S1
    view->owned copy like any other view source). None when `e` is not such
    a read. The receiver is an enum VALUE (never a pointer-local), so the
    bare receiver render composes in any position."""
    if e.enum_member_of is not None or e.field not in ("name", "value"):
        return None
    et = _eligible_enum(analyzer.get_expr_type(e.obj), analyzer)
    if et is None:
        return None
    if e.field == "name":
        return f"::tpy::EnumUtil<{et.to_cpp()}>::name({{0}})"
    einfo = enum_info_of(et)
    return f"static_cast<{einfo.underlying_type.to_cpp()}>({{0}})"

def _enum_neg_wrap(e: TpyUnaryOp, analyzer) -> 'str | None':
    """IntEnum unary negation `-p` -> `(-static_cast<U>({0}))` (_gen_unaryop's
    IntEnum arm; sema leaves resolved_unaryop None there). None otherwise."""
    if e.op != "-" or e.resolved_unaryop is not None:
        return None
    et = _eligible_enum(analyzer.get_expr_type(e.operand), analyzer)
    if et is None or not is_int_enum_type(et):
        return None
    u_cpp = enum_info_of(et).underlying_type.to_cpp()
    return f"(-static_cast<{u_cpp}>({{0}}))"

def _slice_object_type(t: TpyType | None) -> bool:
    """A slice-object value (`basic_slice` -> `::tpy::BasicSlice`, `slice` ->
    `::tpy::Slice`): a by-value C++ type whose only admitted use is as a str
    subscript index (`s[sl]`, rendered bare into the resolved slice
    `__getitem__` template). Admitted as a param and as a ctor-initialized
    local (`sl = basic_slice(1, 3)`, the `_slice_ctor_call_eligible` shape)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return is_basic_slice_type(t) or is_slice_type(t)

def _char_compare_operand(e: TpyExpr, t: TpyType | None, analyzer) -> bool:
    """A Char comparison operand: a Char-typed value, or a single-char str
    literal (the AST threads target=CHAR into its render -> `'x'`,
    `_comparison_targets`' char arm). A multi-char literal never renders as a
    char literal -> AST path. The str-pair arm is checked first, so a
    literal-vs-literal compare stays a plain string compare (no char target
    arises without a Char-typed operand)."""
    if isinstance(e, TpyStrLiteral):
        return len(e.value) == 1
    return _eligible_char(t)

def _eligible_return(t: TpyType | None, analyzer) -> bool:
    return (t is None or isinstance(t, VoidType) or _eligible_scalar(t)
            or _eligible_char(t)
            or _eligible_enum(t, analyzer) is not None
            or _resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None
            or _storage_optional_return_type(t, analyzer) is not None
            or _optional_ptr_borrow(t, analyzer) is not None
            or _borrow_tuple_return_type(t, analyzer) is not None
            or _eligible_value_union(t) is not None
            or _eligible_ptr_union(t, analyzer) is not None)

def _f1_record(t: TpyType | None, analyzer) -> bool:
    """The byte-identical THIR record slice: any NON-GENERIC concrete user
    record whose `TpyType.to_cpp()` == `TypeResolver.type_to_cpp()` and whose
    field / method names THIR reproduces. Three record kinds all satisfy that
    and fall out of the single non-generic check below:

    - same-module records -- no qualification / rename at all;
    - `@native` records -- type spelled via `Compiler.native_cpp_names` (the same
      map the resolver reads), field renames via the AST's `native_field_name`
      (stamped into THIR field access by `_field_cpp`), methods via
      `fi.native_name` (already honored); the module axis is irrelevant
      (native_cpp_names qualifies a cross-module native record too);
    - cross-module non-native records -- `native_cpp_names` qualifies them by
      qname exactly as the resolver's `imported_record_qualification_for_type`
      does (corpus-verified byte-identical).

    Only GENERIC records (type-arg recursion, overlaps the F5 rung) stay on the
    AST path. A future native-specific carve-out would re-split this predicate by
    kind (none is needed today -- native records cannot carry a TPy-emitted ctor
    body, so the one native-only asymmetry, the ctor-MIL field name, is
    unreachable)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        t = t.wrapped
    if not (isinstance(t, NominalType) and t.is_user_record):
        return False
    if t.type_args:
        return False
    return analyzer.registry.get_record_for_type(t) is not None

def _unwrap_own(t: TpyType) -> TpyType:
    """The payload of an `Own[T]` wrapper, else `t` unchanged -- the recurring unwrap
    the `Optional`-inner helpers apply before an `_f1_record` check."""
    return t.wrapped if isinstance(t, OwnType) else t

def _optional_ptr_borrow(t: TpyType | None, analyzer) -> 'OptionalType | None':
    """The pointer-repr `Optional[F1-record]` BORROW binding type -- the C++
    shape of an `A | None` param or an OPTIONAL_TO_PTR local (a bare
    `A*` / `const A*`), or None. An own-optional (`Optional[Own[A]]` /
    `Own[A | None]`) is storage-repr (`std::optional<A>`) and excluded --
    the OwnType check is defensive on top of `uses_pointer_repr` (an Own
    inner must never slip in via `_f1_record`'s own Own-unwrap)."""
    if not isinstance(t, TpyType):
        return None
    t = unwrap_readonly(unwrap_send_sync(t))
    if not (isinstance(t, OptionalType) and t.uses_pointer_repr()):
        return None
    inner = unwrap_readonly(t.inner)
    if isinstance(inner, OwnType):
        return None
    return t if _f1_record(inner, analyzer) else None

def _optional_ptr_borrow_name(e: TpyExpr, declared: dict[str, TpyType],
                              analyzer) -> 'OptionalType | None':
    """`e` is a bare name whose DECLARED type is a pointer-repr
    `Optional[F1-record]` -- an `A | None` param or an OPTIONAL_TO_PTR local,
    both borrow `A*` bindings (a reassigned Optional local classifies OTHER
    at its decl, so no other Optional-ptr name can appear in a routed body).
    Keyed on the declared type, not the flow-narrowed expr type: the AST's
    None-test and field/method dispatch key on the C++ binding shape, which
    narrowing does not change."""
    if not (isinstance(e, TpyName) and e.name in declared):
        return None
    return _optional_ptr_borrow(declared[e.name], analyzer)

def _storage_optional_return_type(t: TpyType | None, analyzer) -> 'OptionalType | None':
    """The storage-form `Optional[F1-record]` return slot (F2c): `Own[T] | None`,
    which lowers to a `std::optional<T>` returned by value. `Inner | None` is
    pointer-repr (the function returns a borrow `Inner*`, a different direction)
    and is excluded -- it stays on the AST path. The caller passes None for a
    non-`TpyType` (unresolved) return annotation."""
    if not isinstance(t, OptionalType) or t.uses_pointer_repr():
        return None
    return t if _f1_record(_unwrap_own(t.inner), analyzer) else None

def _f1_tuple_element_ok(e: TpyType, analyzer) -> bool:
    """A tuple element that renders byte-identically off the F1 slice: an eligible
    value scalar (`T`, same in both forms), an F1-record (BORROW_REF: `T*` borrow /
    `T` storage), or a pointer-repr `Optional[F1-record]` (PTR_OPTIONAL: `T*` borrow
    / `std::optional<T>` storage). Each keeps `to_cpp_return()` / `to_cpp()`
    recursion off cross-module / native / generic / pending types, where bare
    `to_cpp()` would mis-spell. Union / container / generic elements ride later
    rungs."""
    if _eligible_scalar(e) or _f1_record(e, analyzer):
        return True
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(e)))
    if isinstance(inner, OptionalType) and inner.uses_pointer_repr():
        return _f1_record(_unwrap_own(inner.inner), analyzer)
    return False

def _f1_tuple(t: TpyType | None, analyzer) -> 'TupleType | None':
    """A pointer-repr tuple whose every element is F1-renderable -- the F3 tuple:
    borrow form `std::tuple<..., T*>` differs from storage form
    `std::tuple<..., std::optional<T>>` / `std::tuple<..., T>`, so a storage source
    lifts via `tuple_to_pointer` and a borrow source stores via `tuple_to_storage`.
    `has_pointer_repr_element` ensures the two forms genuinely differ (an all-value
    tuple needs no conversion). Other tuples stay on the AST path."""
    if t is None:
        return None
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if not isinstance(t, TupleType) or not t.has_pointer_repr_element():
        return None
    if not all(_f1_tuple_element_ok(e, analyzer) for e in t.element_types):
        return None
    return t

def _borrow_tuple_return_type(t: TpyType | None, analyzer) -> 'TupleType | None':
    """The function's borrow-form pointer-repr tuple return slot (F3): a
    `tuple[..., Ref]` returned as `std::tuple<..., T*>`, into which a `return
    <storage tuple lvalue>` lifts via `tuple_to_pointer`."""
    return _f1_tuple(t, analyzer)

def _is_borrow_form_name(t: TpyType | None) -> bool:
    """Whether a bare name read renders in borrow form: a non-value type (record /
    Optional / etc. -- a pointer / reference) or a pointer-repr tuple (`std::tuple<
    ..., T*>`, value-typed yet with distinct borrow and storage forms). Used to keep
    a THIRName's form tag honest so a convert source is never mislabeled VALUE.

    Precondition: callers must first exclude a STORAGE-form pointer-repr tuple (an F3
    `auto&&` alias local), which has the same type but reads as STORAGE -- this query
    keys on the type alone and would mistag it BORROW. The name-read call site checks
    `storage_tuple_locals` before falling through here. The other call site -- the
    `TpySubscript` branch tagging a subscript *result* -- is safe without that check
    because an admitted element is only ever a value scalar or a plain record, never
    itself a pointer-repr tuple (a nested-tuple element is not in the admitted set), so
    the storage-alias ambiguity cannot arise there."""
    if t is None:
        return False
    if not t.is_value_type():
        return True
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return isinstance(inner, TupleType) and inner.has_pointer_repr_element()

def _value_scalar_tuple(t: TpyType | None) -> bool:
    """A pure value-scalar tuple (`tuple[int, bool, ...]`): a value type rendered
    `std::tuple<...>` where borrow and storage forms coincide, so a subscript read
    of any element needs no lift. Every element is an eligible value scalar -- a
    non-value element makes it pointer-repr (the `_f1_tuple` family), and a
    str/view/nested-tuple element rides a later cell. Admitting it as a param (whose
    signature stays on the AST path) routes functions that read it by subscript."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return (isinstance(t, TupleType)
            and all(_eligible_scalar(e) for e in t.element_types))

def _const_index(index: TpyExpr) -> 'int | None':
    """The compile-time integer index of a tuple subscript, mirroring the AST's
    `_extract_compile_time_index`: a bare int literal or a negated int literal. A
    non-constant tuple index never reaches lowering (sema rejects it); the
    eligibility gate uses this to confirm the literal form regardless."""
    if isinstance(index, TpyIntLiteral):
        return index.value
    if (isinstance(index, TpyUnaryOp) and index.op == "-"
            and isinstance(index.operand, TpyIntLiteral)):
        return -index.operand.value
    return None

def _subscript_index_and_tuple(sub: TpySubscript,
                               analyzer) -> 'tuple[TupleType, int] | None':
    """`(tuple_type, normalized_idx)` for a tuple subscript with a compile-time-const,
    in-bounds index (a negative literal folded by the tuple arity), or None if the
    receiver is not a tuple or the index is not such a constant. The receiver-type
    resolution + index fold written once, shared by the eligibility gate, the arrow
    decision, and lowering so the three can never drift."""
    recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(sub.obj))))
    if not isinstance(recv_t, TupleType):
        return None
    idx = _const_index(sub.index)
    if idx is None:
        return None
    n = len(recv_t.element_types)
    if idx < 0:
        idx += n
    if not (0 <= idx < n):
        return None
    return recv_t, idx

def _subscript_recv_tuple(e: TpyExpr, locals_: dict[str, TpyType],
                          analyzer) -> 'tuple[TupleType, int] | None':
    """`(tuple_type, normalized_idx)` for a subscript `t[N]` off an in-scope
    eligible-tuple name (a value-scalar tuple or an already-routed pointer-repr
    `_f1_tuple`); else None. Shared by the value-element and record-element read gates
    -- non-name receivers, ineligible tuples, and non-const indices stay on the AST
    path."""
    if not isinstance(e, TpySubscript):
        return None
    recv = e.obj
    if not isinstance(recv, TpyName) or recv.name not in locals_:
        return None
    res = _subscript_index_and_tuple(e, analyzer)
    if res is None:
        return None
    recv_t, _idx = res
    if not (_value_scalar_tuple(recv_t) or _f1_tuple(recv_t, analyzer) is not None):
        return None
    return res

def _tuple_subscript_value_read(e: TpyExpr, locals_: dict[str, TpyType],
                                analyzer) -> 'int | None':
    """A value-result tuple subscript read `t[N]` -> `std::get<N>(t)` (value form, no
    lift): element N is a value scalar. Returns the normalized index, or None -- record
    / `Optional` (borrow) elements ride the field-receiver path (`t[N].field`)."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    return idx if _eligible_scalar(recv_t.element_types[idx]) else None

def _subscript_record_field_recv(e: TpyExpr, locals_: dict[str, TpyType],
                                 analyzer) -> 'int | None':
    """`t[N]` whose element is a plain F1-record (a `BORROW_REF` pointer-repr slot) --
    a borrow result usable as a scalar-field-read receiver (`t[N].field`). Returns the
    normalized index, or None. `Optional[record]` elements take the null-check member
    path (`_subscript_optional_field_recv`) and are excluded here (`_f1_record` rejects
    them)."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    return idx if _f1_record(recv_t.element_types[idx], analyzer) else None

def _subscript_optional_field_recv(e: TpyExpr, locals_: dict[str, TpyType],
                                   analyzer) -> 'int | None':
    """`t[N]` whose element is a pointer-repr `Optional[F1-record]` (PTR_OPTIONAL) -- a
    nullable borrow usable as a runtime-null-checked field receiver (`t[N].field` ->
    `deref_check(...)`). Returns the normalized index, or None. Mirrors the Optional arm
    of `_f1_tuple_element_ok`."""
    res = _subscript_recv_tuple(e, locals_, analyzer)
    if res is None:
        return None
    recv_t, idx = res
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_t.element_types[idx])))
    if not (isinstance(inner, OptionalType) and inner.uses_pointer_repr()):
        return None
    return idx if _f1_record(_unwrap_own(inner.inner), analyzer) else None

def _field_over_subscript_ok(e: TpyExpr, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A scalar field access off a record-element tuple subscript (`t[N].field`): the
    receiver `t[N]` is a plain-record borrow, the field a value scalar (checked by the
    caller). Position-neutral -- valid as a read (RHS) or a scalar-field write target
    (LHS), since both render `std::get<N>(t)->field` / `.field` off the same receiver.
    The borrow-local-binding source path keeps its own name-receiver gate, so `b = t[N]`
    stays on the AST path. Markers-clean excludes the Optional null-check / property /
    setattr shapes, so an Optional-element write and a property-setter write stay on the
    AST path."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _subscript_record_field_recv(e.obj, locals_, analyzer) is not None)

def _optional_field_over_subscript_ok(e: TpyExpr, locals_: dict[str, TpyType],
                                      analyzer) -> bool:
    """A scalar field read off an `Optional[record]`-element tuple subscript with an
    unproven None (`t[N].field` -> `deref_check(...).field`, the
    `needs_optional_runtime_check` path). The receiver `t[N]` is a nullable borrow, the
    field a value scalar (checked by the caller). Read only -- writes / binds keep the
    name-receiver gate."""
    return (isinstance(e, TpyFieldAccess) and e.needs_optional_runtime_check
            and _field_markers_clean(e, allow_optional_check=True)
            and _subscript_optional_field_recv(e.obj, locals_, analyzer) is not None)

def _owned_str_slot(t: TpyType | None, analyzer) -> bool:
    """An owned `str` container element/key/value slot (S5). Only the owned
    nominal is admitted: a `StrView`/`BytesView` slot makes the container hold
    views, whose literal keys/elements the AST pins to static storage
    (`view_key_target` threads the key type into the literal render) -- a shape
    this slice does not reproduce; the bytes family rides S6."""
    st = _resolved_str_value(t, analyzer)
    return st is not None and is_str_type(st)

def _container_scalar_read(t: TpyType | None, analyzer) -> bool:
    """A container whose element/value read renders as a bare value via the
    container subscript emit: `list[scalar|str]`, `Array[scalar|str, N]` (sema's
    read-only list-literal demotion -- subscript/len/iteration emit identically
    to list), or `dict[fixed-int|str key, scalar|str value]`. `set` has no
    `__getitem__`. Owned-`str` keys read identically to a fixed-int index
    (`::tpy::__getitem__(c, k)` -- the static-storage literal pin fires only for
    VIEW-typed keys, which `_owned_str_slot` excludes); a BigInt-KEYED dict is
    admitted like a fixed-int-keyed one -- every render this predicate feeds is
    key-type-neutral (bare receiver name, `__len__`, dict-literal elements via
    the slot retype) except the index positions, which apply
    `_narrow_bigint_index` on the INDEX type alone (gen_index_expr narrows a
    runtime-BigInt key even into a BigInt-keyed map -- the int32 round-trip is
    mirrored, not endorsed). An `Own[container]` (move-in
    `T&&` param) is excluded explicitly -- its ABI differs from the borrow shape
    this slice's emit assumes, and it rides a later cell (mirrors the Own unwrap
    in `_f1_record`, which admits Own where this deliberately does not)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        return False
    args = getattr(t, "type_args", None)
    if is_list(t) or is_array(t):
        return bool(args) and (_eligible_scalar(args[0])
                               or _owned_str_slot(args[0], analyzer))
    if is_dict(t):
        if not args or len(args) < 2:
            return False
        key, val = args[0], args[1]
        return ((is_fixed_int_type(key) or _runtime_bigint(key, analyzer)
                 or _owned_str_slot(key, analyzer))
                and (_eligible_scalar(val) or _owned_str_slot(val, analyzer)))
    return False

def _container_record_iter(t: TpyType | None, analyzer) -> bool:
    """A `list[F1-record]` container -- iterated (`for x in c`) with a record loop var
    (`auto&&` / `const auto&`, a borrow alias). The iteration counterpart to
    `_container_scalar_read` (scalar-element containers read by subscript). A dict's
    record VALUES need `for k, v in d.items()` (tuple-unpack, a later cell); `for k in d`
    yields keys, which is the scalar path. `set` / `Span` / `Array` record params ride a
    later cell (their params aren't admitted). `Own[list]` is excluded (mirrors
    `_container_scalar_read`)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        return False
    args = getattr(t, "type_args", None)
    if is_list(t):
        return bool(args) and _f1_record(args[0], analyzer)
    return False

def _field_receiver_ok(e: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """`e` is a plain field access `recv.field` off an F1-record receiver (a record
    param, REF_ALIAS local, or F2 plain `T*` pointer-local in `declared`) or a
    PROVEN-non-None pointer-repr `Optional[F1-record]` borrow name (an `A | None`
    param / OPTIONAL_TO_PTR local whose access sema proved -- the runtime-check
    marker is clear, so the AST renders the plain indirect access `p->field`),
    with no special-emit marker -- read or write position. The receiver's
    pointer-vs-reference shape (`->` vs `.`) is decided at lowering from the
    pointer set; eligibility only needs the receiver binding to be in the slice.
    An UNPROVEN Optional access carries `needs_optional_runtime_check` and is
    rejected by the marker guard here -- its `deref_check` face rides
    `_optional_checked_field` at the positions that mirror it. The
    property-setter / `__setattr__` markers guard the write position (a
    property/setattr field assign takes a method-call emit path)."""
    if not isinstance(e, TpyFieldAccess) or not _field_markers_clean(e):
        return False
    recv = e.obj
    if not isinstance(recv, TpyName):
        return False
    return (_f1_record(declared.get(recv.name), analyzer)
            or _optional_ptr_borrow_name(recv, declared, analyzer) is not None)

def _const_exact_field_receiver_ok(e: TpyExpr, declared: dict[str, TpyType],
                                   analyzer) -> bool:
    """`_field_receiver_ok` minus Optional-ptr borrow-name receivers -- for the
    const-SPELLING sinks (borrow-local decls, storage-tuple aliases, union
    locals from fields, borrow-tuple returns), whose emitted decl spells the
    receiver's const verdict. THIR reads the inferred verdict
    (`_param_is_const` / `const_locals`), but the AST keys these decls on
    `const_indirect_locals`, which `seed_param_locals` populates for an
    Optional-ptr receiver only when it is `readonly[...]`-ANNOTATED -- an
    inferred-const narrowed receiver thus emits a non-compiling mutable decl
    (`A& g = h->g;` off `const H* h`; the BUGS.md "borrow locals off a
    narrowed Optional receiver drop inferred constness" entry). Mirroring
    would fork the verdict solely to reproduce the bug -> gate-reject per the
    ledger criterion. Render-const-blind consumers (field reads/writes, arg
    lifts, reseats, MIL copies) keep the plain `_field_receiver_ok`."""
    if not _field_receiver_ok(e, declared, analyzer):
        return False
    return _optional_ptr_borrow_name(e.obj, declared, analyzer) is None

def _optional_checked_field(e: TpyExpr, declared: dict[str, TpyType],
                            analyzer) -> bool:
    """An UNPROVEN field access off a pointer-repr `Optional[F1-record]` borrow
    name: `p.x` where sema could not prove `p` non-None, marked
    `needs_optional_runtime_check` -> `::tpy::deref_check(p).x`
    (_gen_field_access's runtime-check path over the already-`T*` receiver;
    `pointer_value_expr` is the identity for a non-global name). Serves read,
    write-target, and aug-assign-target positions -- the render is
    position-independent. Storage-form Optional sources (fields / subscripts /
    OPTIONAL_STORAGE names -- the `deref_optional_check` render) never bind a
    declared Optional-ptr name in a routed body, so the name check alone pins
    the `deref_check` spelling."""
    if not isinstance(e, TpyFieldAccess):
        return False
    if not e.needs_optional_runtime_check:
        return False
    if not _field_markers_clean(e, allow_optional_check=True):
        return False
    return _optional_ptr_borrow_name(e.obj, declared, analyzer) is not None

def _field_markers_clean(e: TpyFieldAccess, *,
                         allow_optional_check: bool = False) -> bool:
    """The field access carries no special-emit marker (module var / class constant /
    property / dyn attr / unbound-self / deref chain / Optional null-check) -- a plain
    `.field` read or write. Each marker takes its own AST emit path, out of the slice.
    `allow_optional_check` keeps `needs_optional_runtime_check` admissible for the
    Optional-element member path (which reproduces that runtime check)."""
    return not (e.module_var_access is not None or e.class_constant_owner is not None
                or e.property_getter_call is not None or e.dyn_getattr_call is not None
                or e.property_setter_call is not None or e.dyn_setattr_call is not None
                or e.unbound_self_parent_type is not None or e.deref_depth
                or e.deref_narrowed_to is not None
                or (e.needs_optional_runtime_check and not allow_optional_check))

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

def _is_borrow_tuple_source(e: TpyExpr, declared: dict[str, TpyType],
                            storage_tuple_locals: set[str], analyzer) -> bool:
    """A borrow-form tuple name (`std::tuple<..., T*>`) that lifts to storage form
    at a field write via `tuple_to_storage`: a borrow tuple PARAM. A storage-tuple
    alias local (`auto&&`, in `storage_tuple_locals`) is STORAGE form -- a direct
    copy, no wrap -- and is excluded; a storage-form field/subscript/global source
    is likewise a direct copy and is not this borrow source."""
    return (isinstance(e, TpyName)
            and e.name not in storage_tuple_locals
            and _f1_tuple(declared.get(e.name), analyzer) is not None)

def _f1_tuple_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                             storage_tuple_locals: set[str], analyzer) -> bool:
    """A tuple-field write `recv.field = <borrow tuple>`: an F3 tuple field off an
    F1-record receiver, written from a borrow tuple source -> the field-write lifts
    borrow->storage via `tuple_to_storage` (copy; the `Own[tuple]` move arm and the
    storage-source direct-copy ride later cells)."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    if _f1_tuple(analyzer.get_expr_type(target), analyzer) is None:
        return False
    return _is_borrow_tuple_source(stmt.value, declared, storage_tuple_locals, analyzer)

def _param_const_verdict(name: str, func: TpyFunction, analyzer,
                         record_name: str | None, attr: str) -> bool:
    """Whether param `name` is in the function's `attr` verdict set
    (`const_borrow_params` / `deep_const_borrow_params` -- both are
    param-index sets on the registry FunctionInfo). A method's FunctionInfo
    lives on the owning record (`record_name`), a free function's in the
    function registry -- the same lookup codegen's
    `_get_method_mutated_params` uses. A property pair shares one overload
    list (getter + setter); [-1] is safe only because a getter has no
    non-self params (this lookup is never consulted for it) and the setter's
    non-value param is forced Own[...] (routing around const entirely)."""
    if record_name is not None:
        ri = analyzer.registry.get_record(record_name)
        overloads = ri.get_method_overloads(func.name) if ri is not None else None
    else:
        overloads = analyzer.registry.get_function(func.name)
    fi = overloads[-1] if overloads else None
    verdict = getattr(fi, attr, None) if fi is not None else None
    if not verdict:
        return False
    idx = next((i for i, (n, _) in enumerate(func.params) if n == name), None)
    return idx is not None and idx in verdict

def _param_is_deep_const(name: str, func: TpyFunction, analyzer,
                         record_name: str | None = None) -> bool:
    """Whether param `name` carries the DEEP-const verdict
    (`FunctionInfo.deep_const_borrow_params` -- discriminant-only use, no
    address escape), which deep-consts a pointer-variant param's pointees
    (`std::variant<const A*, const B*>`) in the signature and every
    narrowed-member spelling."""
    return _param_const_verdict(name, func, analyzer, record_name,
                                "deep_const_borrow_params")

def _param_is_const(name: str, func: TpyFunction, analyzer,
                    record_name: str | None = None) -> bool:
    """Whether param `name` is emitted `const` -- read from the sema fact
    `FunctionInfo.const_borrow_params` (param indices), which equals codegen's
    `const_ref_params` for a function's plain F1-record (ref) params. This holds
    for a readonly callable (method or free function) too: a `@readonly` callable's
    non-value param is stored as `Ref(ReadonlyType(T))`, so `decide_param_const`
    takes the `ReadonlyType` early-exit -- the forced-const (codegen body) and
    inferred (`const_borrow_params`) verdicts traverse the same branch, making the
    inferred set exact. The verdict set is None when Phase-2 has not run --
    unreachable for an admitted function (Phase-1 always sets
    `mutated_params`), so the resulting not-const is a safe default, not a
    divergence."""
    return _param_const_verdict(name, func, analyzer, record_name,
                                "const_borrow_params")

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

def _union_compare_pair(lt: TpyType | None, rt: TpyType | None) -> bool:
    """Two SAME-TYPE value-union compare operands: `std::variant`'s own
    comparison operators, the rb=None bare-operator arm -- `(a == b)` on both
    paths. A union-vs-member compare renders the bare mixed pair on the AST
    path (invalid C++, the union-operand BUGS.md class) -> AST path; the
    equal-union requirement rejects it."""
    u = _eligible_value_union(lt)
    return u is not None and u == _eligible_value_union(rt)

def _optional_narrow_facts_ok(facts: dict[str, TpyType],
                              declared: dict[str, TpyType], analyzer) -> bool:
    """Assert-condition narrowing facts the slice needs no emit for: every
    fact narrows a pointer-repr `Optional[F1-record]` borrow name to its inner
    record (`assert p is not None` / `assert p`). Optional narrowing is
    sema-side only -- downstream reads arrive retyped with the runtime-check
    marker cleared, so the mirror is per-node and the assert emits just its
    condition (no extraction alias, unlike the isinstance facts)."""
    for var, fact in facts.items():
        opt = (_optional_ptr_borrow(declared.get(var), analyzer)
               if var in declared else None)
        if opt is None or unwrap_readonly(opt.inner) != fact:
            return False
    return True

def _is_none_compare_operand(e: TpyBinOp, locals_: dict[str, TpyType],
                             analyzer) -> 'TpyExpr | None':
    """The Optional operand of an admitted `is [not] None` test, or None. The
    shape is exactly one `None` literal against a pointer-repr
    `Optional[F1-record]` borrow NAME (the declared type, matching the AST's
    `get_resolved_type` -- a flow-narrowed `p` still renders the pointer
    compare). Storage-form / protocol / overload-folded operands never bind
    such a name in a routed body, so the name check pins the
    `(p ==|!= nullptr)` render. Shared by the gate (`_binop_eligible`) and
    the lowering (`_lower_expr`'s is-arm) so both key one verdict."""
    left_none = isinstance(e.left, TpyNoneLiteral)
    right_none = isinstance(e.right, TpyNoneLiteral)
    if left_none == right_none:  # both or neither
        return None
    operand = e.right if left_none else e.left
    if _optional_ptr_borrow_name(operand, locals_, analyzer) is None:
        return None
    return operand

def _nonvalue_container_ret(ret: TpyType | None) -> bool:
    """A non-value builtin-container call return (`list`/`dict`/`set`) admitted
    as a for-each iterable: an `Own[...]` return arrives Own-stripped from
    `get_expr_type` (a by-value rvalue), a borrow return Ref-stripped (a C++
    lvalue), a readonly borrow return ReadonlyType-wrapped -- all three capture
    into `__obj_N` and iterate identically past the `auto`-vs-`auto&` verdict
    (`_call_iterable_lvalue`). Array/Span returns (value types) and bytes
    (admitted by the value-position check already) stay gate-excluded."""
    if ret is None:
        return False
    t = unwrap_readonly(unwrap_send_sync(ret))
    return is_list(t) or is_dict(t) or is_set(t)

def _call_iterable_lvalue(e: TpyCall, analyzer) -> bool:
    """`is_lvalue_iterable`'s call arm over the admitted iterable calls: an
    `Own[...]` return is a by-value rvalue even though `get_expr_type` strips
    the Own -- consult the fi like the AST does; a value-type (str) return is
    an rvalue (both take the owning `auto __obj_N =` capture); the remaining
    admitted shape is a borrow return (`T&` / `const T&`), a C++ lvalue
    (`auto& __obj_N =`)."""
    rfi = e.resolved_function_info
    if rfi is not None and isinstance(rfi.return_type, OwnType):
        return False
    return not analyzer.get_expr_type(e).is_value_type()

def _member_valued_union_slot(a: TpyExpr, ptype: TpyType | None,
                              analyzer) -> bool:
    """A union param slot receiving a MEMBER-valued scalar arg (`take_vu(k)` /
    `take_vu(2.5)` -- the arg's expr type is a member, not the union):
    `_gen_union_arg`'s value branch hoists a `std::variant<...> __tmp_N = v;`
    temp -- the arg-temp row (`_value_union_temp_arg`) where the statement
    position flushes, AST otherwise. An
    already-union arg (a same-union name, the union-coerced literal) is NOT
    member-valued and rides its own pass-through arm. Guards the bare-scalar
    arg disjunct, which is slot-blind by construction."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    if not isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt))),
                      UnionType):
        return False
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return not isinstance(at, UnionType)

def _value_union_temp_slot(a: TpyExpr, ptype: TpyType | None,
                           analyzer) -> 'UnionType | None':
    """The value-union arg-temp row (`_gen_union_arg`'s value branch): a
    MEMBER-valued eligible-scalar arg into a non-pointer union slot hoists
    `std::variant<...> __tmp_N = <arg>;` and passes the bare temp name
    (`_maybe_move` is a no-op for the scalar shapes admitted). The slot
    unwraps Ref then readonly EXACTLY like the AST (`_gen_call` +
    `_gen_union_arg`) -- no Send/Sync peel, so a wrapped slot falls to the
    same default render on both paths. An already-union arg (a same-union
    name, the union-coerced int literal) is not member-valued and rides its
    pass-through arms; membership is a slice guard (sema already typed the
    arg against the union). Shared by the eligibility gate and
    `_lower_call_arg` so both key the temp on one verdict; the NAME-narrowing
    reject (a narrowed subject reads the extraction alias while the AST's
    `already_union` verdict renders it bare, temp-free) stays site-specific
    -- the gate reads `ws.narrowed`, lowering `lc.narrow`."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(unwrap_ref_type(pt))
    if not isinstance(pt, UnionType):
        return None
    ut = _eligible_value_union(pt)
    if ut is None:
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    # A bare float literal types as FloatLiteralType and lands in the union's
    # double member, rendering repr(v) -- resolve it like the f-string row
    # does. (An int literal never arrives bare: sema coerces it to the union,
    # the temp-free `_union_coerced_literal_arg` row.)
    if isinstance(at, FloatLiteralType):
        at = FLOAT
    if not _eligible_scalar(at):
        return None
    if not any(at == m for m in ut.members if not is_void_like_type(m)):
        return None
    return ut

def _record_rvalue_temp_slot(a: TpyExpr, ptype: TpyType | None,
                             analyzer) -> 'NominalType | None':
    """The record-rvalue arg-temp row (the free-call `is_ref_param() +
    is_temporary_expr` cascade arm): a same-module record-ctor RVALUE into a
    SAME-nominal plain record slot hoists `A __tmp_N = A(7);` and passes the
    temp name -- mutated (`A&`) and const (`const A&`) slots alike (the AST
    arm is mutation-blind). `TempState.create` renders the slot type's bare
    `to_cpp()`, which the F1 restriction keeps equal to the raw source name.
    A readonly slot (`const A` decl spelling) survives `unwrap_ref_type` as a
    ReadonlyType and rejects; a SUBCLASS-typed ctor (the upcast temp declares
    the CHILD's type) rejects on the same-nominal check. Shared by the gate
    and `_lower_call_arg`; the gate adds `_record_ctor_call_eligible` (the
    ctor's own arg/registry rejects) on top."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_ref_type(pt)
    if not (isinstance(pt, NominalType) and pt.is_user_record
            and pt.is_ref_param()):
        return None
    if not _f1_record(pt, analyzer):
        return None
    if not isinstance(a, TpyCall):
        return None
    fi = a.resolved_function_info
    if fi is None or not fi.is_constructor:
        return None
    if analyzer.get_expr_type(a) != pt:
        return None
    return pt

def _own_cascade_fires(ptype: TpyType | None) -> bool:
    """Whether gen_call_arg's ownership cascade fires for this slot: an
    `Own[T]` (or `Own[T] | None`) payload survives the cascade's own unwrap
    (readonly + Ref -- NOT Send/Sync: a Send/Sync-wrapped slot leaves the
    cascade inert, so both paths render bare). The scalar pass-through arms
    must reject such slots -- a bare name would silently skip the AST's
    copy+move temp (the latent slot-blind hole, the Own analog of
    `_member_valued_union_slot`)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    return unwrap_optional_own(unwrap_readonly(unwrap_ref_type(pt))) is not None

def _plain_own_slot(ptype: TpyType | None) -> TpyType | None:
    """The readonly-unwrapped payload of a PLAIN `Own[T]` call-arg slot, or
    None. The `Own[T] | None` face is excluded -- its indirect-name args take
    the `ptr_to_optional[_move]` wrap arm -> AST path."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(unwrap_ref_type(pt))
    if not isinstance(pt, OwnType):
        return None
    return unwrap_readonly(pt.wrapped)

def _own_lvalue_temp_slot(a: TpyExpr, ptype: TpyType | None,
                          analyzer) -> TpyType | None:
    """Slot/shape verdict for the Own-slot copy+move row -- a bare (un-coerced)
    NAME / eligible field read into a plain `Own[T]` slot of eligible-scalar
    or same-nominal F1-record payload. Renders `auto __tmp_N = <arg>;` +
    `f(std::move(__tmp_N))` -- or the temp-free `f(std::move(name))` when the
    name is movable at its last use (gen_call_arg's `_maybe_move` arm, decided
    at lowering from the same `movable_locals` + last-use facts; a scalar is
    never movable, a pointer-local is a non-owning borrow -- both always
    copy). Shared by the gate (`_own_lvalue_arg`, which adds the locals_/
    narrowing rejects) and `_lower_call_arg` (which adds the lc-side
    narrowing reject and picks `THIRMove` vs `THIRArgTemp`)."""
    w = _plain_own_slot(ptype)
    if w is None:
        return None
    if not isinstance(a, (TpyName, TpyFieldAccess)):
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if isinstance(at, OwnType):
        # An Own[T]-bound param name reads bare, like any owned local.
        at = unwrap_readonly(at.wrapped)
    if _eligible_scalar(w):
        return w if _eligible_scalar(at) else None
    if _f1_record(w, analyzer):
        # Same-nominal is a slice guard: sema rejects an upcast into an Own
        # slot outright, so no other pairing reaches codegen.
        return w if at == w else None
    return None

def _optional_ptr_arg_slot(ptype: TpyType | None, analyzer) -> 'OptionalType | None':
    """The pointer-repr `Optional[F1-record]` call-arg slot of
    `_gen_optional_ptr_arg`'s non-protocol tail, or None. Mirrors the AST's
    unwrap (readonly only -- the arm keys on the raw param type, no Send/Sync
    peel). A protocol inner (the typed-null / adapter faces) fails the F1
    check; an `Own[T] | None` slot is storage-repr and never reaches here."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_readonly(pt)
    if not (isinstance(pt, OptionalType) and pt.uses_pointer_repr()):
        return None
    if not _f1_record(unwrap_readonly(pt.inner), analyzer):
        return None
    return pt

def _optional_ptr_arg_face(a: TpyExpr, ptype: TpyType | None,
                           analyzer) -> str | None:
    """Classify a call arg against a pointer-repr `Optional[record]` slot into
    its `_gen_optional_ptr_arg` face: 'none' (the `nullptr` literal), 'pass'
    (a pointer-repr Optional binding -- already `T*`, renders bare), 'name'
    (a plain record name -- `&(name)`, or bare for an F2 pointer-local, split
    at LOWERING from `lc.pointers`), 'lift' (a storage-form Optional field
    read, `::tpy::optional_to_ptr(...)`), or 'ctor' (a same-nominal
    record-ctor rvalue -- the `&(__tmp_N)` temp face, flushable positions
    only). A NARROWED union subject classifies 'name' (its expr type arrives
    member-stamped): the read renames to the extraction alias / inline get
    inside `_lower_expr` and both paths wrap `&(...)` -- the render mirrors.
    None rejects: subscript sources, coerced args, `self`, non-ctor
    rvalues (`Ptr[T]`-typed calls etc. stay AST). Shared by the eligibility
    gate (which adds `_record_ctor_call_eligible` / receiver checks) and
    `_lower_call_arg` so both key one verdict."""
    ot = _optional_ptr_arg_slot(ptype, analyzer)
    if ot is None:
        return None
    inner = unwrap_readonly(ot.inner)
    if isinstance(a, TpyNoneLiteral):
        return 'none'
    if isinstance(a, TpyCall):
        fi = a.resolved_function_info
        if fi is None or not fi.is_constructor:
            return None
        return 'ctor' if analyzer.get_expr_type(a) == inner else None
    if isinstance(a, TpyFieldAccess):
        at = analyzer.get_expr_type(a)
        at = unwrap_readonly(at) if at is not None else None
        if (isinstance(at, OptionalType)
                and unwrap_readonly(at.inner) == inner
                and reads_storage_form_optional(analyzer, a)):
            return 'lift'
        return None
    if not isinstance(a, TpyName) or a.name == "self":
        return None
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if isinstance(at, OptionalType):
        # An unnarrowed pointer-repr Optional binding (a `T | None` param /
        # OPTIONAL_TO_PTR local) is already `T*` -- the bare pass face.
        return 'pass' if (at.uses_pointer_repr()
                          and unwrap_readonly(at.inner) == inner) else None
    if isinstance(at, OwnType):
        at = unwrap_readonly(at.wrapped)
    return 'name' if at == inner else None

def _arg_ptr_union_slot(ptype: TpyType | None, analyzer,
                        *, readonly_target: bool = False,
                        ) -> 'tuple[UnionType, bool] | None':
    """The pointer-variant union of a non-Own call-arg slot (the member/None
    inline-lift target) plus its deep-const verdict (a `readonly[...]`
    annotation or the callee's `deep_const_borrow_params` fact, threaded as
    `readonly_target` -- the AST's `is_readonly_target`), or None. A
    deep-const slot spells const pointees on the lift and takes the
    `ptr_variant_to_const` wrap on already-union args. An `Own[union]` slot
    is the value-variant auto-move cascade -> AST path. Shared by the
    eligibility gate and `_lower_call_arg` so both key the lift on one
    verdict."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return None
    pt = unwrap_send_sync(pt)
    deep_const = isinstance(pt, ReadonlyType) or readonly_target
    if isinstance(unwrap_readonly(pt), OwnType):
        return None
    ut = _eligible_ptr_union(pt, analyzer)
    if ut is None:
        return None
    return ut, deep_const

def _scalar_pass_through_slot(ptype: TpyType | None, analyzer) -> bool:
    """A method param slot the inline-template arg path passes a scalar into
    bare: a value scalar or `Own[scalar]`. Scalars are value types -- copied,
    never moved -- and `gen_call_arg`'s Own handling skips the copy+move temp for
    a template/native callee (`inline_template=True`), so the arg emits as the
    bare expression. Every other slot shape (record / Optional / union / tuple /
    Ptr / str / protocol / unsubstituted type param) takes a lift, move, or
    conversion the slice does not reproduce. A literal-seeded container's fi
    carries `Own[IntLiteral]` slots (resolved like every scalar-typed check)."""
    if ptype is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(ptype))
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    return _resolved_scalar(t, analyzer)

def _container_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                                locals_: dict[str, TpyType], analyzer) -> bool:
    """A container arg the AST passes as the bare name: a bare in-scope name
    with a builtin-container binding, into a NON-Own concrete builtin-container
    param (`std::vector<T>&` / `const ordered_map<K, V>&` / ...).
    `gen_call_arg`'s ownership cascade never fires for that slot shape (`own is
    None`), so the emit is the bare name on both paths. An `Own[container]`
    slot auto-moves at last use (`f(std::move(xs))`), a `Span` / protocol
    (`Iterable`) slot converts (`::tpy::as_mut_span(xs)` / adapter wrap), and an
    `Optional[container]` slot lifts (`&(xs)`), so those stay on the AST path.
    The binding type is read from `locals_`, per the receiver-gate convention on
    `_resolved_scalar`. NB unlike the sibling `_scalar_pass_through_slot` (slot
    check only; the arg shape is checked separately at its call sites), this
    predicate owns BOTH sides -- the container arg shape is inseparable from the
    slot shape it pairs with."""
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if not (is_list(at) or is_dict(at) or is_set(at) or is_array(at)):
        return False
    if ptype is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    # Explicit Own/Optional rejects (the move / address-of lift slots); the
    # concrete-container check below would also exclude them, but the invariant
    # should be self-evident, mirroring gen_call_arg's own Own detection.
    if isinstance(pt, (OwnType, OptionalType)):
        return False
    return is_list(pt) or is_dict(pt) or is_set(pt) or is_array(pt)

def _positional_only_template(tmpl: str, n_args: int) -> bool:
    """Whether a `@cpp_template` body contains only in-range positional
    placeholders (`{0}`, `{1}`, ...) and `{{`/`}}` literal-brace escapes --
    the subset `expand_cpp_template` can render with no receiver and no
    substitution context. A surviving named field (`{cpp}`, `{self}`, a type
    param) means sema's substitution did not fully resolve the template, so
    the call must stay on the AST path (which has the substitution machinery)."""
    i, n = 0, len(tmpl)
    while i < n:
        c = tmpl[i]
        if c == "{":
            if i + 1 < n and tmpl[i + 1] == "{":
                i += 2
                continue
            close = tmpl.find("}", i + 1)
            field = tmpl[i + 1:close] if close != -1 else ""
            if not field.isdigit() or int(field) >= n_args:
                return False
            i = close + 1
        elif c == "}":
            # Only the `}}` escape is admitted; a lone `}` (which expand would
            # pass through literally) never occurs in a real template -- reject
            # conservatively.
            if not (i + 1 < n and tmpl[i + 1] == "}"):
                return False
            i += 2
        else:
            i += 1
    return True

def _ctor_arg_slot_ok(ptype: TpyType | None, analyzer) -> bool:
    """A scalar-ctor param slot the arg passes bare: a value scalar /
    `Own[scalar]` (`_scalar_pass_through_slot`), or the generic conversion
    overload's unsubstituted method type param (`__init__[T: AnyFixedInt](self,
    x: T)`, the `int_cast_check` arm). `gen_call_arg`'s target hint is inert for
    a TypeParamRef slot -- not Own, not fixed-int, no borrow/storage lift -- so
    an eligible-scalar arg emits bare exactly as into a concrete scalar slot.
    The Ref/readonly peel mirrors gen_call_arg's own `ptype_inner` unwrap (the
    stub stores the generic param as `Ref(TypeParamRef)`)."""
    if ptype is not None and isinstance(
            unwrap_readonly(unwrap_ref_type(ptype)), TypeParamRef):
        return True
    return _scalar_pass_through_slot(ptype, analyzer)

def _template_init_call_fi(e: TpyCall) -> 'FunctionInfo | None':
    """The resolved `__init__` FunctionInfo of a bare-name `@cpp_template`
    type-constructor call in the pure-template-expansion shape (the `_gen_call`
    `fi.cpp_template and not call_type` branch), or None. Shared by the scalar
    and slice-object ctor gates; each adds its own result/arg checks."""
    if not isinstance(e.func, TpyName):
        return None
    if e.kwargs or e.double_star_unpack is not None:
        return None
    # Markers that take earlier / different _gen_call branches. Explicit
    # type_args are rejected; INFERRED type args (the generic int_cast_check
    # overload) are fine -- the positional-only template makes gen_call_from_fi's
    # substitution loop a no-op.
    if (e.call_type is not None or e.type_args
            or e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert
            or e.subscript_callee is not None):
        return None
    fi = e.resolved_function_info
    if fi is None or not (fi.is_method and fi.name == "__init__"):
        return None
    # Post-call wrappers / special member forms the bare template emit does not
    # reproduce (mirrors _method_call_eligible's fi rejects).
    if (fi.is_consuming or fi.error_return_type is not None
            or fi.native_cpp_return_type is not None
            or fi.is_async or fi.is_generator
            or any(isinstance(p.type, LiteralType) for p in fi.params)):
        return None
    if not fi.cpp_template or not _positional_only_template(fi.cpp_template,
                                                            len(e.args)):
        return None
    if len(e.args) != len(fi.params):
        return None
    return fi

def _plain_member_call_markers_ok(e: TpyMethodCall) -> bool:
    """No special-emit marker: every marker takes a different _gen_method_call
    path (static / super / module-qualified / typed-dict / nested-ctor /
    callable-field / macro / fstr / deref chain). The Optional runtime-check
    marker is NOT in this set -- callers dispose of it themselves (the
    optional-ptr borrow receiver mirrors it as the deref_check face; every
    other caller must reject it explicitly)."""
    if e.kwargs or e.double_star_unpack is not None:
        return False
    return not (e.is_static_call or e.super_parent_type is not None
                or e.unbound_self_parent_type is not None
                or e.user_module_call is not None
                or e.builtin_module_call is not None
                or e.typed_dict_get_field is not None
                or e.is_nested_constructor or e.is_nested_enum_constructor
                or e.is_callable_field or e.macro_expansion is not None
                or e.fstr_expansion is not None or e.type_args
                or e.inferred_type_args or e.deref_depth
                or e.deref_narrowed_to is not None)

def _plain_method_fi_ok(fi) -> bool:
    """Shared fi rejects. A consuming method moves the receiver
    (`std::move(xs)`); `cpp_return_type` wraps the call in a static_cast;
    @error_return unwraps via a statement expression; a LiteralType param
    mangles the member name. None are reproduced."""
    return not (fi.is_consuming or fi.error_return_type is not None
                or fi.native_cpp_return_type is not None
                or any(isinstance(p.type, LiteralType) for p in fi.params)
                or fi.is_async or fi.is_generator
                or fi.is_property_getter or fi.is_property_setter)

def _dict_view_iterable_ok(e: TpyMethodCall, locals_: dict[str, TpyType],
                           analyzer,
                           methods: tuple[str, ...] = ("values", "keys")) -> bool:
    """`d.values()` / `d.keys()` as a for-loop iterable: a zero-arg dict-view
    method on a bare-name eligible dict binding. The view result is an rvalue
    (the owning `auto __obj_N = ::tpy::dict_values(d);` capture); the loop var
    is the dict's value/key, checked by the caller's shared elem gate.
    `.items()` yields tuples -- the tuple-unpack gate passes it explicitly."""
    if e.method not in methods or e.args:
        return False
    if not isinstance(e.obj, TpyName) or e.obj.name not in locals_:
        return False
    # An Optional-checked receiver (`d.values()` on a narrowable Optional
    # dict) takes the deref_check method face, not the bare view call.
    if not _plain_member_call_markers_ok(e) or e.needs_optional_runtime_check:
        return False
    fi = e.resolved_function_info
    if fi is None or not _plain_method_fi_ok(fi) or fi.params:
        return False
    return _container_scalar_read(locals_[e.obj.name], analyzer)

def _plain_scalar_slot(ptype: TpyType | None, analyzer) -> bool:
    """A NON-Own value-scalar param slot. The user-record sibling of
    `_scalar_pass_through_slot`: a plain user method is not an inline
    template, so gen_call_arg's Own arm copies an `Own[scalar]` arg into a
    temp and moves it (`auto __tmp_N = n; ...(std::move(__tmp_N))`) -- Own
    slots stay on the AST path here."""
    if ptype is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    if isinstance(t, OwnType):
        return False
    return _resolved_scalar(t, analyzer)

def _is_bytes_family(t: TpyType | None) -> bool:
    """A bytes/BytesView value or a pending bytes binding -- an analyzer-free
    family check for positions where the view/owned resolution is irrelevant
    (the print wrapper)."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, PendingViewType):
        return t.family is BYTES_FAMILY
    return is_bytes_type(t) or is_bytes_view_type(t)

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
