"""Entry points: the function-level gate, function/constructor/module
lowering, and the module iteration helpers the codegen seam calls.
"""

from __future__ import annotations
from dataclasses import field, fields
from ...parse.nodes import (
    FunctionLinkage,
    TpyAssign,
    TpyCall,
    TpyCoerce,
    TpyExpr,
    TpyFieldAccess,
    TpyForEach,
    TpyFunction,
    TpyGlobal,
    TpyIf,
    TpyMethodCall,
    TpyModule,
    TpyName,
    TpyNoneLiteral,
    TpyPassStmt,
    TpyStmt,
    TpyStrLiteral,
    TpyTry,
    TpyVarDecl,
    TpyWhile,
    TpyWith,
    VarLinkage,
    expr_reads_self_field,
    is_base_init_call,
    is_docstring,
)
from ...typesys import (
    CONST_PARAMS_METHODS,
    NominalType,
    OptionalType,
    TpyType,
    TypeParamKind,
    TypeParamRef,
    VoidType,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...codegen_cpp.context import escape_cpp_name
from ..fallback import note
from ..validate import validate_constructor, validate_function
from ..nodes import (
    Form,
    THIRBaseInit,
    THIRConstructor,
    THIRExpr,
    THIRFormConvert,
    THIRFunction,
    THIRFunctionLayout,
    THIRLiteral,
    THIRMilInit,
    THIRModule,
    THIRParam,
    THIRStmt,
    THIRTry,
)
from .predicates import (
    _container_record_iter,
    _container_scalar_read,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_ptr_value,
    _eligible_return,
    _eligible_scalar,
    _eligible_value_union,
    _f1_record,
    _f1_tuple,
    _field_receiver_ok,
    _is_borrow_ptr_local,
    _is_type_param_slot,
    _optional_ptr_borrow,
    _own_type_param_slot,
    _resolved_bytes_value,
    _resolved_str_value,
    _slice_object_type,
    _value_scalar_tuple,
)
from .context import (
    _LowerCtx,
    _WalkState,
)
from .expressions import (
    _expr_eligible,
    _is_move_source,
    _is_record_rvalue_source,
    _lower_expr,
    _slot_literal_retype,
)
from .statements import (
    _body_eligible,
    _container_scalar_tuple_iter,
    _lower_stmts,
)

def _f1_param_eligible(ptype: TpyType | None, analyzer) -> bool:
    """An F1-eligible param: a value scalar, an F1-record passed by reference
    (`T&` / `const T&`, accessed `.`), an F3 borrow-form pointer-repr tuple
    (`std::tuple<..., T*>`, a borrow source for a `tuple_to_storage` field write), a
    pure value-scalar tuple (`const std::tuple<...>&`, read by subscript), a
    by-value slice object (`basic_slice` / `slice`, a str subscript index), a
    value-element container (`list[scalar|str]` / `Array[scalar|str, N]` /
    `dict[fixed-int|str, scalar|str]`, read by subscript),
    a record-element list (`list[record]`, iterated by `for x in c` -- the signature
    stays on the AST path per M1), or a pointer-repr `Optional[F1-record]`
    (`A | None` -> a borrow `A*` / `const A*`; sema rejects its reassignment, so
    no rebind machinery arises). Own-optional/view-keyed-container/cross-module/
    native record params stay on the AST path."""
    return (_eligible_scalar(ptype) or _eligible_char(ptype)
            or _is_type_param_slot(ptype)
            or _own_type_param_slot(ptype)
            or _eligible_ptr_value(ptype, analyzer)
            or _f1_record(ptype, analyzer)
            or _optional_ptr_borrow(ptype, analyzer) is not None
            or _resolved_str_value(ptype, analyzer) is not None
            or _resolved_bytes_value(ptype, analyzer) is not None
            or _slice_object_type(ptype)
            or _eligible_enum(ptype, analyzer) is not None
            or _f1_tuple(ptype, analyzer) is not None
            or _value_scalar_tuple(ptype)
            or _eligible_value_union(ptype) is not None
            or _eligible_ptr_union(ptype, analyzer) is not None
            or _container_scalar_read(ptype, analyzer)
            or _container_record_iter(ptype, analyzer)
            or _container_scalar_tuple_iter(ptype, analyzer))

def _function_eligible(func: TpyFunction, analyzer,
                       self_type: 'TpyType | None' = None) -> bool:
    # A record-owned callable is admitted when its owning record is an
    # F1-record (`self_type` passed by the caller). All method kinds funnel
    # their bodies through gen_body, so only the receiver model differs:
    # instance methods
    # (M1/M2, dunders included -- the C++ operator wrappers delegating to them
    # are structural emission, not body emission) and property getters/setters
    # lower with a `self` (`this`) receiver; static methods lower like free
    # functions (no receiver -- the `static` prefix, the setter's `set_` rename
    # and the getter's ref-return arm are all signature-only; the getter's
    # body-side return arm needs a pointer-repr Optional/union return, which
    # _eligible_return rejects). A record param's const verdict comes from the
    # method's FunctionInfo on the owning record -- see `_param_is_const`.
    if func.is_method:
        if self_type is None or not _f1_record(self_type, analyzer):
            return note("sig.receiver_record")
        # Inplace dunders (__iadd__ ...): the AST forces const params on them
        # (CONST_PARAMS_METHODS), a verdict `_param_is_const` does not mirror;
        # their mandatory `return self` (`return *this;`) is outside the slice
        # anyway.
        if func.name in CONST_PARAMS_METHODS:
            return note("sig.inplace_dunder")
        # @readonly on a @staticmethod is not sema-rejected but emits with the
        # readonly verdicts dropped (no const overload, no forced-const
        # params) -- an asymmetry the mirror does not reproduce.
        if func.is_staticmethod and func.is_readonly:
            return note("sig.readonly_static")
    elif func.is_staticmethod:
        # Defensive: the parser sets is_method=True on staticmethods, so a free
        # function should never carry the flag.
        return note("sig.staticmethod_flag")
    if func.is_overload_stub or func.native_function or func.is_consuming:
        return note("sig.special_callable")
    # An overload IMPL body is emitted once per stub with per-stub dead-branch
    # facts (literal_overload_facts / overload_param_types), but gen_body's THIR
    # interception keys on id(func) -- routing the shared impl would hijack
    # every specialization with the unspecialized body. Reject any callable in
    # a multi-entry overload set (functions and methods alike). Sole carve-out:
    # a property getter+setter pair shares one method name in the registry but
    # each has its own body (no shared-impl hijack possible).
    if func.is_method:
        ri = analyzer.registry.get_record_for_type(self_type)
        overloads = ri.get_method_overloads(func.name) if ri is not None else []
        if len(overloads) > 1:
            is_property_pair = (
                len(overloads) == 2
                and any(fi.is_property_getter for fi in overloads)
                and any(fi.is_property_setter for fi in overloads))
            if not is_property_pair:
                return note("sig.overload_set")
    else:
        fis = analyzer.registry.get_function(func.name)
        if fis is not None and len(fis) > 1:
            return note("sig.overload_set")
    if func.builtin_decorator_key is not None:
        return note("sig.builtin_decorator")
    if func.is_async:
        return note("sig.async")
    if func.is_generator:
        return note("sig.generator")
    if func.error_return is not None:
        return note("sig.error_return")
    if func.type_params:
        # A generic callable routes its body via the same TypeParamRef T-value
        # arms F5 built for generic-record methods: the resolver spells each
        # `[T]` param/return as a TypeParamRef, `_is_type_param_slot` gates it
        # as a form-neutral value pass-through (`val_or_ref_t<T>` resolves
        # value-vs-ref per instantiation), and the template signature stays
        # AST. A method's OWN type params (`def m[U](self, x: U)`) spell the
        # same way -- on a generic record the record's T rides the F5
        # self-feed while the method's U rides these slots, so both compose.
        # Still rejected: INT-kind params (`[N: int]` -- N read as a value has
        # no T-slot arm yet).
        if any(k != TypeParamKind.TYPE for k in func.type_param_kinds):
            return note("sig.generic_fn")
    if func.linkage != FunctionLinkage.DEFAULT:
        return note("sig.linkage")
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
            return note("sig.param_type")
    # A reassigned param of a type flagged param_needs_copy_for_reassign (owned
    # str/bytes/String, BigInt -- const-ref params that cannot reassign in
    # place) gets a mutable owned copy hoisted by the AST prologue
    # (`std::string p_ = std::string(p);` + body-wide rename) -- a shape the
    # slice does not reproduce. Reject the function; the flag is the exact AST
    # trigger (gen_function's scan.reassigned check), so by-value params
    # (scalars, Char, StrView) reassign in place on both paths and stay
    # routed. Non-value params cannot be reassigned at all (sema rejects the
    # rebind), so no pointer-local prologue arises here either.
    scan = analyzer.function_scan_results.get(id(func))
    if scan is not None and scan.reassigned:
        for name, ptype in func.params:
            pt = ptype if isinstance(ptype, TpyType) else None
            if (name in scan.reassigned and pt is not None
                    and pt.param_needs_copy_for_reassign()):
                return note("sig.param_reassign_copy")
    rt = func.return_type if isinstance(func.return_type, TpyType) else None
    if func.return_type is not None and not _eligible_return(rt, analyzer):
        return note("sig.return_type")
    return True

def _try_hoisted_names(body: list[TpyStmt], analyzer) -> set[str]:
    """Names hoisted by `if_branch_decls` on `try` statements anywhere in
    `body` -- the subset of `function_hoisted_vars` the try gate arm can
    mirror. Recurses only through the compound shapes the slice admits;
    a try inside anything else keeps its names out of the set, which
    (safely) keeps the function on the AST path."""
    out: set[str] = set()
    for s in body:
        if isinstance(s, TpyTry):
            out |= set(analyzer.if_branch_decls.get(id(s), {}))
            out |= _try_hoisted_names(s.try_body, analyzer)
            for h in s.handlers:
                out |= _try_hoisted_names(h.body, analyzer)
            out |= _try_hoisted_names(s.else_body, analyzer)
            out |= _try_hoisted_names(s.finally_body, analyzer)
        elif isinstance(s, TpyIf):
            out |= _try_hoisted_names(s.then_body, analyzer)
            out |= _try_hoisted_names(s.else_body, analyzer)
        elif isinstance(s, (TpyWhile, TpyForEach)):
            out |= _try_hoisted_names(s.body, analyzer)
            out |= _try_hoisted_names(s.orelse, analyzer)
        elif isinstance(s, TpyWith):
            out |= _try_hoisted_names(s.body, analyzer)
    return out

def lower_function(func: TpyFunction, analyzer, render_type=None,
                   self_type: 'TpyType | None' = None,
                   native_globals: frozenset[str] = frozenset()) -> THIRFunction | None:
    """Lower one function to THIR, or None if it falls outside the slice.

    `render_type` (codegen's `TypeResolver.type_to_cpp`) renders F1 borrow-local
    decl types byte-identically; omit it only when no non-value local can arise
    (dump / value-scalar standalone lowering). `self_type` is the owning record's
    type when `func` is a record method: for kinds with a receiver (instance /
    property / dunder) `self` is seeded as an F1-record receiver (a `this`
    pointer) so its field reads route the same as a param's; a static method
    keeps only the record for its param-const lookups."""
    if not _function_eligible(func, analyzer, self_type):
        return None
    # Branch-local hoisting is not reproduced, with one carve-out: try-
    # statement predecls, mirrored as THIRTry.hoist_decls (the try gate arm
    # re-checks each name and type). A hoisted name NOT accounted for by a
    # try's if_branch_decls (the loop-body storage hoist) keeps the function
    # on the AST path.
    hoisted = analyzer.function_hoisted_vars.get(id(func))
    if hoisted and (hoisted - _try_hoisted_names(func.body, analyzer)):
        note("body.hoisted_vars")
        return None
    is_record_method = self_type is not None and func.is_method
    # A static method has no receiver -- it lowers like a free function, but
    # keeps `record_name` so `_param_is_const` resolves its param verdicts from
    # the method's FunctionInfo on the owning record (the same lookup codegen's
    # `_get_method_mutated_params` uses).
    has_self = is_record_method and not func.is_staticmethod
    self_receiver = "self" if has_self else None
    record_name = (self_type.name
                   if is_record_method and isinstance(self_type, NominalType)
                   else None)
    lc = _LowerCtx(func, analyzer, render_type, self_receiver=self_receiver,
                   record_name=record_name)
    params_set: dict[str, TpyType] = {n: t for n, t in func.params}
    if has_self:
        params_set["self"] = self_type  # the record receiver, a field source
        if func.is_readonly:
            # A readonly method's `this` is const, so a borrow local off `self.opt`
            # lifts to `const T*` (the OPTIONAL_TO_PTR const bump keys on the
            # receiver being in const_locals -- see _f1_is_const).
            lc.const_locals.add("self")
    # `global`-declared names seed the scope like params: their writes then
    # lower as reassignments (the AST's global-write arm emits `g = v;`) and
    # reads render bare -- the same-module plain-scalar-global spelling. The
    # seeding is WHOLE-function, mirroring the AST exactly: both paths key on
    # `function_global_decls`, so even a write textually BEFORE its `global`
    # statement (which sema accepts -- a CPython-parity gap, see BUGS.md)
    # renders the same global assign. Only eligible scalars seed; an unseeded
    # name keeps its `global` statement ineligible, which rejects the WHOLE
    # body regardless of statement order (the TpyGlobal arm reads
    # `prescan.global_seeded`, not walk state), so no unseeded-global write
    # can survive to misroute as a fresh local decl. Native-linkage globals
    # render through `native_global_names` -- never seeded.
    global_seeded: set[str] = set()
    for n in analyzer.function_global_decls.get(id(func), set()):
        if n in params_set or n in native_globals:
            continue
        gt = analyzer.ctx.global_scope.lookup(n)
        if gt is None:
            continue
        gt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(gt)))
        if _eligible_scalar(gt):
            params_set[n] = gt
            global_seeded.add(n)
    lc.prescan.global_seeded = frozenset(global_seeded)
    lc.prescan.native_globals = native_globals
    if not _body_eligible(func.body, analyzer, _WalkState(params_set),
                          lc.prescan, in_branch=False):
        return None
    params = tuple(THIRParam(name=n, type=t) for n, t in func.params)
    rt = func.return_type if isinstance(func.return_type, TpyType) else VoidType()
    # Seeded with params (and `self`): a write to such a name is a reassignment.
    declared: dict[str, TpyType] = dict(params_set)
    body = _lower_stmts(func.body, lc, declared)
    fn = THIRFunction(
        name=func.name,
        params=params,
        return_type=rt,
        body=body,
        layout=THIRFunctionLayout(),
    )
    validate_function(fn)
    return fn

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

def _is_record_value_source(source: TpyExpr, declared: dict[str, TpyType],
                            own_param_names: set[str], lc: _LowerCtx) -> bool:
    """A record-producing source that constructs an F1-record field (or its
    pointer-repr `Optional`) *directly* via an implicit copy/construct -- as opposed
    to a borrow `T*` that must lift through `ptr_to_optional`. Three shapes:

      * a non-own **F1-record param name** (`other`) -- an implicit MIL copy;
      * an **F1-record ctor-call rvalue** (`Inner(scalars)`) -- the F2d
        `_is_record_rvalue_source` shape, emitted as the bare `Name(args)` prvalue;
      * an **F1-record field-read off a param** receiver (`other.g`) -- a field copy.

    Own params (which move) and `self.<field>` reads (their pointee may be
    uninitialized at MIL time -- ordering-sensitive, deferred) are excluded."""
    analyzer = lc.analyzer
    if isinstance(source, TpyName):
        return (source.name not in own_param_names
                and _f1_record(declared.get(source.name), analyzer))
    if isinstance(source, TpyCall):
        return _is_record_rvalue_source(source, declared, analyzer)
    if isinstance(source, TpyFieldAccess):
        return (isinstance(source.obj, TpyName)
                and source.obj.name != lc.self_receiver
                and _field_receiver_ok(source, declared, analyzer)
                and _f1_record(analyzer.get_expr_type(source), analyzer))
    return False

def _ctor_field_init_ok(stmt: TpyStmt, own_field_names: set[str],
                        own_param_names: set[str], declared: dict[str, TpyType],
                        lc: _LowerCtx) -> bool:
    """A hoistable own-field initializer the ctor MIL slice admits -- a
    `self`-targeted own-field assign whose (field type, source) pair the tail
    emitter reproduces byte-for-byte.

    The `obj.name == "self"` guard is load-bearing -- `_field_receiver_ok` alone
    would also admit `other_record.field = ...`, which is not a member init. The
    own-field test matches `_extract_field_inits`. Routed shapes:

      * **scalar** (M3a): an eligible-scalar or Char value (`f(value)`).
      * **own-param move** (M3b-move): an `Own[...]` source consumed at its last use
        moves into a record / Optional[record] field (`f(std::move(p))`); checked
        before the copy arms because the cascade applies the move first and never
        also lifts via `ptr_to_optional`.
      * **pointer-repr `Optional[F1-record]`** (M3b-copy / -rvalue): a `None`
        (`f(std::nullopt)`), a non-own borrow source (`f(::tpy::ptr_to_optional(p))`),
        or a record-value source that constructs the optional directly (`f(Inner(v))`
        / `f(other.g)` / `f(other)`).
      * **plain F1-record** (M3b-copy / -rvalue): a record-value source --
        `copy()`-unwrapped param copy, ctor-call rvalue, or param field-read.

    `copy()` is unwrapped before the Optional check too (so `self.opt = copy(m)`
    routes like the record arm). A pointer-repr UNION field admits only the
    own-param move source (F4 U2). Field types beyond those (bytes / tuple /
    str / list -> F3+; cross-module / native / generic records) leave the ctor
    on the AST path."""
    analyzer = lc.analyzer
    if not (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field in own_field_names
            and _field_receiver_ok(stmt.target, declared, analyzer)):
        return False
    ftype = analyzer.get_expr_type(stmt.target)
    if (_eligible_scalar(ftype) or _eligible_char(ftype)
            or _eligible_enum(ftype, analyzer) is not None
            or _eligible_ptr_value(ftype, analyzer)):
        # A str-literal source into a Char field is a sema type error; the
        # reject is defensive (the target-typed `'x'` render would diverge).
        if _eligible_char(ftype) and isinstance(stmt.value, TpyStrLiteral):
            return False
        return _expr_eligible(stmt.value, declared, analyzer)
    if isinstance(ftype, TypeParamRef):
        # Stage B: a generic record's `T` field. An `Own[T]` param moves; a bare
        # `T` param copies (`first(a)`). The source renders by name only -- a
        # TypeParamRef slot takes no borrow/storage lift -- so the MIL is
        # byte-identical to the AST's `gen_expr(name, T)`. A non-param source
        # (`self.<field>` read, ctor rvalue) rides a later cell.
        source = _unwrap_copy(stmt.value, analyzer)
        if _is_move_source(source, lc, own_param_names):
            return True
        if isinstance(source, TpyName):
            return _is_type_param_slot(declared.get(source.name))
        return False
    pu = _eligible_ptr_union(ftype, analyzer)
    if pu is not None:
        # F4 U2: an `Own[A | B]` param moves into the value-variant field
        # (`u(std::move(v))` -- the M3b-move arm verbatim, type-agnostic at
        # lowering). Borrow lifts / member-value sources ride later cells.
        return _is_move_source(_unwrap_copy(stmt.value, analyzer), lc,
                               own_param_names)
    is_opt = (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()
              and _f1_record(ftype.inner, analyzer))
    if not (is_opt or _f1_record(ftype, analyzer)):
        return False
    source = _unwrap_copy(stmt.value, analyzer)
    # M3b-move: an own-param at its last use moves into the field.
    if _is_move_source(source, lc, own_param_names):
        return True
    if is_opt:
        # None / a non-own borrow `T*` (pointer-repr Optional param, lifts via
        # ptr_to_optional) / a record-value source (constructs the optional directly).
        # pointers empty: a ctor MIL has no locals.
        return (isinstance(source, TpyNoneLiteral)
                or _is_borrow_ptr_local(source, declared, set())
                or _is_record_value_source(source, declared, own_param_names, lc))
    return _is_record_value_source(source, declared, own_param_names, lc)

def _ctor_param_eligible(ptype: TpyType | None, analyzer) -> bool:
    """A ctor param the MIL slice can reference: the method-param set (value scalar /
    F1-record / pointer-repr `Optional[F1-record]` -- the borrow source for
    `ptr_to_optional` -- incl. plain `Own`) plus the **own-optional** shape
    (`Own[Inner | None]` / `Optional[Own[Inner]]`, which moves into an
    `Optional[F1-record]` field via the move arm). The raw types match what
    `declared` holds and `_is_borrow_ptr_local` tests. The field-init gate decides
    per-field whether the param is used in an admitted way; an unhandled use rejects
    the whole ctor (-> AST path). A generic record's OWN ctor (stage B) also
    admits a `TypeParamRef` param (`Pair[T].__init__(self, a: T)`) -- a bare `T`
    copies into a `T` field, an `Own[T]` moves; the AST signature spells it
    `param_val_or_ref_t<T>` / `T&&`, both signature-only, so the MIL just
    references the name."""
    # `_f1_param_eligible` already admits a bare `T` param (the type-param slot).
    if _f1_param_eligible(ptype, analyzer):
        return True
    # Own-optional: peel Own (and the inner/outer Optional) to the underlying
    # record / type param -- or, for the F4 U2 move cell, an eligible
    # pointer-repr union (`Own[A | B]` moves into the value-variant field,
    # M3b-move).
    own = unwrap_optional_own(unwrap_readonly(ptype)) if isinstance(ptype, TpyType) else None
    if own is not None:
        inner = own.wrapped
        if isinstance(inner, OptionalType):
            inner = inner.inner
        return (isinstance(inner, TypeParamRef)
                or _f1_record(inner, analyzer)
                or _eligible_ptr_union(inner, analyzer) is not None)
    return False

def lower_constructor(record, init_method: TpyFunction, analyzer,
                      render_type=None,
                      self_type: 'TpyType | None' = None) -> THIRConstructor | None:
    """Lower a constructor to a THIRConstructor, or None if outside the slice.

    Same-module non-generic record, flat or with same-module F1 base(s) (M3d: each
    `super().__init__` / `BaseN.__init__` call lowers to a base initializer, sorted by
    parent declaration order; a direct inherited-field write goes to the body). The
    leading run of hoistable own-field
    initializers (the M3a/M3b field-source slice) goes to the member-init-list; the rest
    of the body -- docstring / `pass` trivia (M3c-trivia), non-init statements, and field
    inits that cannot hoist or follow a chain break (M3c-demotion) -- lowers through the
    shared statement machinery (`_body_eligible` / `_lower_stmt`), the same path method
    bodies use. The ctor routes only when every non-trivia body statement is in the slice;
    otherwise it stays on the AST path, byte-identical. The signature stays on the AST path
    (the M1 method precedent); only the MIL + body tail routes here."""
    if self_type is None or not _f1_record(self_type, analyzer):
        note("ctor.non_f1_record")
        return None
    # M3d: same-module F1 base(s) route -- each `super().__init__` / `BaseN.__init__`
    # call lowers to a base initializer (sorted by parent declaration order), and a
    # direct inherited-field write goes to the body. A non-F1 base (cross-module /
    # generic / native -- its `to_cpp()` would not match) keeps the ctor on the AST
    # path. Reject overloaded / native / generator / generic __init__ -- those take
    # emit paths the tail emitter does not reproduce.
    ri = analyzer.registry.get_record(record.name)
    if ri is None:
        note("ctor.unregistered")
        return None
    if any(not _f1_record(p, analyzer) for p in ri.parents):
        note("ctor.non_f1_base")
        return None
    if (init_method.is_overload_stub or init_method.native_function
            or init_method.is_async or init_method.is_generator
            or init_method.type_params):
        note("ctor.special_init")
        return None
    # Params must be value scalars, F1-records, or pointer-repr Optional[F1-record]
    # (see `_ctor_param_eligible`). This keeps the AST-emitted signature a plain ctor
    # (no protocol/dynamic template) so it pairs with the THIR tail; a param used in
    # an unhandled way is caught by the per-field init gate below.
    for _name, ptype in init_method.params:
        pt = ptype if isinstance(ptype, TpyType) else None
        if not _ctor_param_eligible(pt, analyzer):
            note("ctor.param_type")
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
    # Base initializers (`super().__init__` / `BaseN.__init__`), sorted by parent
    # declaration order (M3d); None if any is outside the slice -> AST path.
    base_inits = _lower_base_inits(init_method, ri, declared, lc)
    if base_inits is None:
        note("ctor.base_init")
        return None
    field_inits: list[TpyAssign] = []
    body_stmts: list[TpyStmt] = []  # demoted inits + non-init stmts + trivia, source order
    body_written_self_fields: set[str] = set()
    chain_broken = False
    for stmt in init_method.body:
        if is_base_init_call(stmt):  # handled above; breaks no chain
            continue
        # Docstring / `pass` (M3c-trivia): emit no code and break no hoist chain,
        # but stay in the body so its braces are non-empty (` {\n    }`, not ` {}`).
        if is_docstring(stmt) or isinstance(stmt, TpyPassStmt):
            body_stmts.append(stmt)
            continue
        # An inherited-field write (`self.<base field> = expr`, M3d) goes to the body --
        # the base ctor owns the MIL slot -- WITHOUT breaking the hoist chain. It is
        # tracked so a later own-field hoist that reads it demotes (below). A property
        # setter (also a non-own self field) lands here too and rejects via body
        # ineligibility (`_field_receiver_ok`). NB the AST checks this only on a live
        # chain (after `chain_broken` it demotes instead, skipping the tracking set); the
        # divergence is inert -- once the chain is broken every later own-field init
        # demotes regardless, so the set is never consulted.
        if _is_self_nonown_field_assign(stmt, own_field_names):
            body_written_self_fields.add(stmt.target.field)
            body_stmts.append(stmt)
            continue
        # A leading own-field init whose (field, source) the MIL reproduces hoists.
        # `_ctor_field_init_ok` already returns False for a non-init statement / a field
        # init with a non-hoistable source (body-local / bare-name RHS / ineligible
        # value), so the gate distinguishes hoist from demote. An init reading an
        # inherited field written earlier in the body must demote (the MIL runs first,
        # before that write) -- the `expr_reads_self_field` trigger (no-op until an
        # inherited-field write populates the set).
        if (not chain_broken
                and _ctor_field_init_ok(stmt, own_field_names, own_param_names,
                                        declared, lc)
                and not expr_reads_self_field(stmt.value, body_written_self_fields)):
            field_inits.append(stmt)
            continue
        # A clean leading own-field init (live chain, not reading an earlier
        # inherited-field write) the AST hoists into the MIL but THIR can't reproduce
        # there -- an F3+ field type (tuple / str / list / union) or a source outside
        # the MIL slice -- must keep the whole ctor on the AST path. Demoting it into
        # the body would diverge from the AST's MIL hoist (the AST never demotes a
        # clean leading own-field init). After a chain break, or when the init reads an
        # earlier inherited-field write, the AST demotes too -- those fall through.
        if (not chain_broken
                and _is_self_own_field_assign(stmt, own_field_names)
                and not expr_reads_self_field(stmt.value, body_written_self_fields)):
            note("ctor.mil_field")
            return None
        # Demote to the body. Demoting breaks the chain (mirrors `_extract_field_inits`'s
        # `demote()`): the MIL runs before the body, so a later otherwise-hoistable init
        # must also demote to preserve source evaluation order.
        chain_broken = True
        body_stmts.append(stmt)
    # The demoted inits + non-init statements lower through THIR's statement machinery
    # (the trivia are admitted directly); a body statement outside the slice keeps the
    # whole ctor on the AST path. The trivia carry no `declared`-scope growth, so the
    # gate runs over the non-trivia subset.
    body_non_trivia = [s for s in body_stmts
                       if not (is_docstring(s) or isinstance(s, TpyPassStmt))]
    if not _body_eligible(body_non_trivia, analyzer, _WalkState(declared),
                          lc.prescan, in_branch=False):
        return None
    body_declared = dict(declared)
    ctor = THIRConstructor(
        record_name=record.name,
        params=tuple(THIRParam(name=n, type=t) for n, t in init_method.params),
        mil_inits=tuple(_lower_ctor_mil_init(s, own_param_names, declared, lc)
                        for s in field_inits),
        base_inits=tuple(base_inits),
        body=_lower_stmts(body_stmts, lc, body_declared),
    )
    validate_constructor(ctor)
    return ctor

def _is_self_nonown_field_assign(stmt: TpyStmt, own_field_names: set[str]) -> bool:
    """A `self.<field> = expr` whose field is not an own field -- an inherited-field
    write or a property setter (M3d). The base ctor owns its slot, so the write goes
    to the body (not the MIL), tracked so a later own-field hoist that reads it demotes."""
    return (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field not in own_field_names)

def _is_self_own_field_assign(stmt: TpyStmt, own_field_names: set[str]) -> bool:
    """A `self.<own field> = expr` -- a member initializer the AST hoists into the
    MIL. THIR must hoist it too or keep the whole ctor on the AST path; demoting it
    into the body (when THIR's MIL slice can't reproduce its field type / source)
    would diverge from the AST's MIL hoist."""
    return (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field in own_field_names)

def _lower_base_inits(init_method: TpyFunction, ri, declared: dict[str, TpyType],
                      lc: _LowerCtx) -> 'list[THIRBaseInit] | None':
    """Mirror `_extract_base_inits`: lower every `super().__init__` / `BaseN.__init__`
    call to a THIRBaseInit, sorted by parent declaration order (so a multi-base list
    emits in the order C++ runs the base ctors, avoiding -Wreorder). None if any base
    init is outside the slice -- the whole ctor then stays on the AST path."""
    analyzer = lc.analyzer
    parent_order: dict[int, int] = {}
    for idx, parent in enumerate(ri.parents):
        p_info = analyzer.registry.get_record_for_type(parent)
        if p_info is not None:
            parent_order[id(p_info)] = idx
    entries: list[tuple[int, THIRBaseInit]] = []
    for src_idx, stmt in enumerate(init_method.body):
        if not is_base_init_call(stmt):
            continue
        lowered = _lower_base_init(stmt, declared, lc)
        if lowered is None:
            return None
        bi, parent_type = lowered
        p_info = analyzer.registry.get_record_for_type(parent_type)
        rank = (parent_order.get(id(p_info), len(parent_order) + src_idx)
                if p_info is not None else len(parent_order) + src_idx)
        entries.append((rank, bi))
    entries.sort(key=lambda e: e[0])
    return [bi for _, bi in entries]

def _lower_base_init(stmt: TpyStmt, declared: dict[str, TpyType],
                     lc: _LowerCtx) -> 'tuple[THIRBaseInit, TpyType] | None':
    """Lower one base-init call to `(THIRBaseInit, parent_type)`, or None outside the
    slice (the caller reuses `parent_type` for the parent-order rank). Mirrors
    `_extract_base_inits`'s `{parent_type.to_cpp()}({args})` render for both the
    `super().__init__(args)` and the explicit `BaseN.__init__(self, args)` forms (sema
    strips `self` from the latter's args). The base must be F1 (so `to_cpp()` is
    byte-identical) and the args eligible scalars; kwargs / star args are out."""
    analyzer = lc.analyzer
    expr = stmt.expr
    # The only narrowing of `stmt.expr` to a TpyMethodCall (is_base_init_call holds at
    # the call site, but the type system doesn't carry that) -- guards `.super_parent_type`.
    if not isinstance(expr, TpyMethodCall):
        return None
    parent_type = expr.super_parent_type or expr.unbound_self_parent_type
    if parent_type is None or not _f1_record(parent_type, analyzer):
        return None
    if expr.kwargs or expr.double_star_unpack is not None:
        return None
    if not all(_eligible_scalar(analyzer.get_expr_type(a))
               and _expr_eligible(a, declared, analyzer) for a in expr.args):
        return None
    return (THIRBaseInit(base_cpp=parent_type.to_cpp(),
                         args=tuple(_lower_expr(a, lc) for a in expr.args)),
            parent_type)

def _lower_ctor_mil_init(stmt: TpyAssign, own_param_names: set[str],
                         declared: dict[str, TpyType], lc: _LowerCtx) -> THIRMilInit:
    """Build one member-init-list entry from a hoisted field initializer (the gate
    already admitted it). Mirrors the record/Optional arms of `_extract_field_inits`:

      * an **own-param at last use** moves (`move=True`, plain source -- never
        `ptr_to_optional`, per the cascade) [M3b-move];
      * a **scalar / Char** -> the lowered value [M3a];
      * a pointer-repr **Optional[F1-record]** -> a STORAGE `None` literal
        (`std::nullopt`), a non-own borrow `T*` lifted via `ptr_to_optional` [M3b-copy],
        or a record-value source (ctor-call / field-read / param copy) that constructs
        the optional directly [M3b-rvalue];
      * a plain **F1-record** -> the `copy()`-unwrapped record-value source [M3b-copy/-rvalue]."""
    analyzer = lc.analyzer
    ftype = analyzer.get_expr_type(stmt.target)
    loc = getattr(stmt, "loc", None)
    field_cpp = escape_cpp_name(stmt.target.field)
    source = _unwrap_copy(stmt.value, analyzer)
    if _is_move_source(source, lc, own_param_names):
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc), move=True)
    if (_eligible_scalar(ftype) or _eligible_char(ftype)
            or _eligible_enum(ftype, analyzer) is not None):
        return THIRMilInit(field_cpp=field_cpp,
                           value=_slot_literal_retype(
                               _lower_expr(stmt.value, lc), ftype))
    if isinstance(ftype, OptionalType):
        if isinstance(source, TpyNoneLiteral):
            v: THIRExpr = THIRLiteral(result_type=ftype, value=None,
                                      form=Form.STORAGE, loc=loc)
        elif _is_borrow_ptr_local(source, declared, set()):
            v = THIRFormConvert(result_type=ftype, value=_lower_expr(source, lc),
                                form=Form.STORAGE, move=False, loc=loc)
        else:
            # A record-value source constructs the optional directly -- no
            # ptr_to_optional (that lifts a borrow `T*`, not a record prvalue/copy).
            v = _lower_expr(source, lc)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc))

def _method_self_type(record, analyzer) -> 'TpyType | None':
    """The `self` receiver type for an M1 method / ctor feed. The qname is
    load-bearing -- a bare `NominalType(name)` has no registry entry, so
    `is_user_record` (hence `_f1_record`) is False; `_f1_record` applies the
    remaining native / cross-module / generic-arg gates at lowering. For a
    generic record the self is `Record[T, ...]` (a `TypeParamRef` per type
    param, kinds per-index like sema's own self-type mirror), which `_f1_record`
    admits via `_f1_record_type_arg_ok` -- opening the sig/ctor gate for the
    record's templated bodies (per-cell T-slot gating still falls a body back)."""
    ri = analyzer.registry.get_record(record.name)
    if ri is None:
        return None
    if record.type_params:
        kinds = record.type_param_kinds
        args = tuple(
            TypeParamRef(name=p, kind=kinds[i] if i < len(kinds) else TypeParamKind.TYPE)
            for i, p in enumerate(record.type_params))
        return NominalType(record.name, type_args=args,
                           _module_qname=ri.qualified_name())
    return NominalType(record.name, _module_qname=ri.qualified_name())

def iter_module_callables(module: TpyModule, analyzer):
    """Yield `(callable, self_type)` for every function / method the slice may
    admit -- the single feed list shared by `lower_module` and codegen so the
    two never drift. The eligibility gate still has the final say; this only
    enumerates candidates. Free functions yield `self_type=None`; record methods
    (instance / static / property / dunder) yield the owning record's type
    (None-skipped for generic records). The constructor is excluded -- its body
    is emitted via the member-init-list driver (the M3 ctor frontier), not
    gen_method_def."""
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

def module_native_globals(module: TpyModule) -> frozenset[str]:
    """Module-level vars with non-DEFAULT linkage: their reads/writes render
    through codegen's `native_global_names` mapping, so a `global` declaration
    naming one never seeds (see lower_function). Keep the predicate in sync
    with generator.py's native_globals collection (the same linkage filter,
    re-derived here because THIR lowering runs before generator populates
    `ctx.native_global_names`). Drift is conservative by construction: this
    mirror skips generator's extra module_init_local/dedup filtering, so it
    can only OVER-include -- an over-included name merely fails to seed and
    the body stays AST (routing lost, never a byte divergence)."""
    return frozenset(s.name for s in module.top_level_stmts
                     if isinstance(s, TpyVarDecl)
                     and s.linkage != VarLinkage.DEFAULT)

def lower_module(module: TpyModule, analyzer, render_type=None) -> THIRModule:
    """Lower every eligible function and instance method in `module`; skip the rest."""
    out = THIRModule(module_name=getattr(analyzer.ctx, "module_name", "generated"))
    ng = module_native_globals(module)
    for func, self_type in iter_module_callables(module, analyzer):
        thir_fn = lower_function(func, analyzer, render_type, self_type=self_type,
                                 native_globals=ng)
        if thir_fn is not None:
            out.functions.append(thir_fn)
    return out
