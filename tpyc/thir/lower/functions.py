"""Entry points: the function-level gate, function/constructor/module
lowering, and the module iteration helpers the codegen seam calls.
"""

from __future__ import annotations
from collections.abc import Mapping
from dataclasses import field, fields, replace
from ...parse.nodes import (
    FunctionLinkage,
    TpyArrayLiteral,
    TpyAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBytesLiteral,
    TpyCall,
    TpyCoerce,
    TpyDictLiteral,
    TpyExpr,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyForEach,
    TpyFunction,
    TpyGlobal,
    TpyIf,
    TpyIntLiteral,
    TpyMatch,
    TpyMethodCall,
    TpyModule,
    TpyName,
    TpyNestedDef,
    TpyNoneLiteral,
    TpyPassStmt,
    TpySetLiteral,
    TpyStmt,
    TpyStrLiteral,
    TpyTry,
    TpyTupleLiteral,
    TpyWhile,
    TpyWith,
    collect_name_refs,
    collect_top_level_local_names,
    expr_reads_self_field,
    is_base_init_call,
    is_docstring,
    iter_capture_bindings,
)
from ...namespace import BindingKind
from ...prescan import scan_reassigned_vars
from ...typesys import (
    CONST_PARAMS_METHODS,
    IntLiteralType,
    NominalType,
    OptionalType,
    OwnType,
    PtrType,
    UnionType,
    TpyType,
    TypeParamKind,
    TypeParamRef,
    VoidType,
    contains_type_param,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...codegen_cpp.context import (
    escape_cpp_name,
    imported_variable_cpp,
    module_native_global_names,
    qualify_native_name,
)
from ...codegen_cpp.gen_generators import GeneratorCodegen
from ...type_def_registry import (
    is_array,
    is_bytes_type,
    is_bytes_view_type,
    is_dict,
    is_list,
    is_set,
)
from ..fallback import _walk as _fallback_walk, note
from ..faces import witness as _witness
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
    _bytes_elem_container,
    _coerce_disposition,
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
    _is_borrow_tuple_source,
    _is_string_owned,
    _is_type_param_slot,
    _optional_ptr_borrow,
    _optional_ptr_borrow_name,
    _own_type_param_slot,
    _readonly_global_type,
    _resolved_bytes_value,
    _resolved_str_value,
    _slice_object_type,
    _template_init_call_fi,
    _value_tuple,
)
from .context import (
    _LowerCtx,
    _WalkState,
)
from .expr_gates import (
    _container_literal_ok,
    _expr_eligible,
    _is_record_rvalue_source,
    _nondef_ctor_field,
    _ptr_union_source_ok,
    _tuple_literal_ok,
)
from .expressions import (
    _is_move_source,
    _lower_expr,
    _lower_tuple_literal,
    _slot_literal_retype,
)
from .statements import (
    _body_eligible,
    _container_scalar_tuple_iter,
    _lower_stmts,
)

def _overload_reject_detail(func: TpyFunction, stubs) -> str:
    """Sub-classify an overload-set reject by WHICH per-stub emission fact
    the impl body is sensitive to -- the slice-1 routing frontier. First
    match wins, ordered by disqualification severity; `plain` marks the
    candidates whose per-stub specializations are the same body modulo the
    (AST-owned) signature:

    - `generic_stub`: a stub carries type params (template specializations);
    - `arity`: a stub is shorter than the impl (missing-param default locals);
    - `ret_mismatch`: stub return types differ from the impl's (the return
      arm strips/validates per-stub coercions);
    - `db_isinstance`: isinstance/match anywhere in the body (the if-chain
      dead-branch elimination can rewrite it per stub);
    - `db_compare`: an equality compare on a bare param name (literal-stub
      equality elimination) -- conservative: any param, literal or not;
    - `narrow_param`: a union/Optional impl param (the narrowing extraction
      skips differently under overload_param_types);
    - `plain`: none of the above.

    Tags extend the dot-hierarchical drilldown convention (like
    `call.ret_type.*`), not the `stmt.<shape>:<detail>` colon composition
    (which is fallback.py's auto-composed form, never hand-built)."""
    if any(getattr(fi, "type_params", None) for fi in stubs):
        return "sig.overload_set.generic_stub"
    if any(len(fi.params) != len(func.params) for fi in stubs):
        return "sig.overload_set.arity"
    rt = func.return_type if isinstance(func.return_type, TpyType) else None
    for fi in stubs:
        if fi.return_type != rt:
            return "sig.overload_set.ret_mismatch"
    param_names = {n for n, _t in func.params}
    detail = None
    for stmt in func.body:
        for node in _fallback_walk(stmt):
            if isinstance(node, TpyMatch) or (
                    isinstance(node, TpyCall)
                    and getattr(node, "isinstance_var", None) is not None):
                return "sig.overload_set.db_isinstance"
            if (detail is None and isinstance(node, TpyBinOp)
                    and node.op in ("==", "!=")
                    and any(isinstance(s, TpyName) and s.name in param_names
                            for s in (node.left, node.right))):
                detail = "sig.overload_set.db_compare"
    if detail is not None:
        return detail
    for _n, pt in func.params:
        u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt))) \
            if isinstance(pt, TpyType) else None
        if isinstance(u, (UnionType, OptionalType)):
            return "sig.overload_set.narrow_param"
    return "sig.overload_set.plain"


def _f1_param_eligible(ptype: TpyType | None, analyzer) -> bool:
    """An F1-eligible param: a value scalar, an F1-record passed by reference
    (`T&` / `const T&`, accessed `.`), an F3 borrow-form pointer-repr tuple
    (`std::tuple<..., T*>`, a borrow source for a `tuple_to_storage` field write), a
    pure value-scalar tuple (`const std::tuple<...>&`, read by subscript), a
    by-value slice object (`basic_slice` / `slice`, a str subscript index), a
    value-element container (`list[scalar|str]` / `Array[scalar|str, N]` /
    `dict[fixed-int|str, scalar|str]`, read by subscript),
    a bytes-element container (`list[bytes]` / `dict[fixed-int|str, bytes]`,
    read by subscript -- writes/iteration keep rejecting per-construct),
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
            or _value_tuple(ptype, analyzer) is not None
            or _eligible_value_union(ptype) is not None
            or _eligible_ptr_union(ptype, analyzer) is not None
            or _container_scalar_read(ptype, analyzer)
            or _bytes_elem_container(ptype, analyzer)
            or _container_record_iter(ptype, analyzer)
            or _container_scalar_tuple_iter(ptype, analyzer))

def _function_eligible(func: TpyFunction, analyzer,
                       self_type: 'TpyType | None' = None,
                       *, allow_resumable: bool = False) -> bool:
    # `allow_resumable` is passed by `lower_resumable`: the async/generator
    # arms below are that entry's whole point, but every other signature
    # check (overloads, linkage, shadowing, param/return families) gates a
    # resumable body exactly like a sync one.
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
    # An overload IMPL body is emitted once per stub with per-stub facts
    # (overload_param_types / literal_overload_facts driving dead-branch
    # elimination, missing-param default locals, and return-coercion
    # stripping), but gen_body's THIR interception keys on id(func) --
    # routing the shared impl would hijack every specialization with the
    # unspecialized body. Reject any callable in a multi-entry overload set
    # (functions and methods alike), sub-classified by WHICH per-stub fact
    # the body is sensitive to (the slice-1 routing frontier: an impl
    # sensitive to none of them lowers identically per stub). Sole
    # carve-out: a property getter+setter pair shares one method name in
    # the registry but each has its own body (no shared-impl hijack).
    if func.is_method:
        ri = analyzer.registry.get_record_for_type(self_type)
        overloads = ri.get_method_overloads(func.name) if ri is not None else []
        if len(overloads) > 1:
            is_property_pair = (
                len(overloads) == 2
                and any(fi.is_property_getter for fi in overloads)
                and any(fi.is_property_setter for fi in overloads))
            # An @auto_readonly / auto_own[Self] clone pair: method_expansion
            # split one source method into two independent TpyFunctions (the
            # second clone even deep-copies the body), so each entry owns its
            # body and keys its own id(func) -- no shared-impl hijack, same
            # argument as the property pair. Detected on the ATTEMPTED func:
            # both auto_readonly clones carry auto_readonly_params_resolved;
            # an auto_own pair's consuming half is already rejected as
            # is_consuming, leaving the flagged borrowing clone. A COMPOSED
            # set (a clone pair over genuine @overload stubs, 4+ entries)
            # keeps rejecting.
            is_clone_pair = (
                len(overloads) == 2
                and (func.auto_readonly_params_resolved
                     or func.is_auto_own_borrowing_clone))
            if not (is_property_pair or is_clone_pair):
                return note(_overload_reject_detail(func, overloads))
        # A member shadowing a same-named local type forces the AST path to
        # render that type fully-qualified inside the record's scope (the
        # member-name/type-name collision fix). THIR renders local ctor callees
        # as the raw name, so a colliding record's body would diverge -- reject.
        if ri is not None and ri.shadows_local_type:
            return note("sig.member_shadows_type")
    else:
        fis = analyzer.registry.get_function(func.name)
        if fis is not None and len(fis) > 1:
            return note(_overload_reject_detail(func, fis))
    if func.builtin_decorator_key is not None:
        return note("sig.builtin_decorator")
    if not allow_resumable:
        if func.is_async:
            return note("sig.async")
        if func.is_generator:
            # Sub-tagged by the AST router's own peephole predicate (one
            # shared routing fact): the two populations are different
            # emitters, so each residue must be measurable separately.
            return note("sig.generator_simple"
                        if GeneratorCodegen.is_simple_generator(func)
                        else "sig.generator_resumable")
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
    # A resumable generator's declared return type is the `Iterator[T]`
    # wrapper, not a value slot -- `_eligible_return` would reject it. Its
    # real value slot is the yield type, checked by `lower_resumable`
    # (`res.yield_type`); skip the wrapper here.
    if not (allow_resumable and func.is_generator):
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

def _shadow_bound_names(stmts: list[TpyStmt]) -> set[str]:
    """Names bound by the binder forms `scan_reassigned_vars` does not record:
    except-`as` bindings and match captures. A candidate read-only global one
    of these shadows must not seed -- the AST may hoist/predecl the binder at
    function scope while the seeded walk state would keep treating later reads
    of the name as the global."""
    out: set[str] = set()

    def walk(body: list[TpyStmt]) -> None:
        for s in body:
            if isinstance(s, TpyTry):
                for h in s.handlers:
                    if h.binding is not None:
                        out.add(h.binding)
            elif isinstance(s, TpyMatch):
                for case in s.cases:
                    for b in iter_capture_bindings(case.pattern):
                        out.add(b.name)
            for sub in s.sub_bodies():
                walk(sub)

    walk(stmts)
    return out

def _seed_readonly_globals(
        func: TpyFunction, analyzer, scope: dict[str, TpyType],
        native_globals: 'Mapping[str, str]',
) -> tuple[frozenset[str], dict[str, str]]:
    """Seed the VALUE globals `func` only ever READS into `scope` (mutated
    in place); returns `(bare, spelled)`: the same-module names that render
    bare, and the native/imported names mapped to their pre-rendered
    spelling (THIRName.cpp).

    Sema resolves an unassigned name to the module global, and the AST
    renders a value global's read bare (`is_indirect_name` is False for
    value globals, and `_maybe_convert_opt_view_param` is param-keyed) --
    or, for a native-linkage / imported global, as a fixed spelling
    (`qualify_native_name` / `imported_variable_cpp`) -- so a seeded name
    routes through every existing name-read arm unchanged, the spelled ones
    differing only in the verbatim-`cpp` render. Same-module candidates
    come from `top_level_decls` (an imported name REDEFINED there reads
    bare in functions, matching the AST's top_level_decls precedence over
    the import qualification -- so it seeds as a same-module global, and
    the imported loop skips it); imported candidates from the
    `imported_names` history via the shared detection. Excluded: params
    (they shadow), `global`-declared names (the write-seeding path above
    owns them), sema-hoisted names (a branch-local shadow with a
    function-scope predecl), and -- via a prescan with the candidates
    pre-declared, plus the binder walk -- any name the function assigns or
    shadow-binds ANYWHERE (Python scoping makes every such name a local; a
    seeded one would misroute its first local decl as a bare global
    reassign)."""
    cands: dict[str, TpyType] = {}
    spelled: dict[str, str] = {}
    global_decls = analyzer.function_global_decls.get(id(func), set())
    hoisted = analyzer.function_hoisted_vars.get(id(func), set())
    for n in analyzer.ctx.top_level_decls:
        if n in scope or n in global_decls or n in hoisted:
            continue
        gt = analyzer.ctx.global_scope.lookup(n)
        if gt is None:
            # Unannotated top-level decls bind in global_ns only (the
            # register_globals pass covers annotated ones in global_scope).
            nb = analyzer.global_ns.lookup_local(n)
            gt = (nb.type if nb is not None
                  and nb.kind is BindingKind.VARIABLE else None)
        st = _readonly_global_type(gt, analyzer)
        if st is None:
            continue
        cands[n] = st
        if n in native_globals:
            spelled[n] = qualify_native_name(native_globals[n])
    for n in analyzer.imported_names:
        if (n in scope or n in cands or n in global_decls or n in hoisted
                or n in analyzer.ctx.top_level_decls):
            continue
        cpp = imported_variable_cpp(analyzer.registry,
                                    analyzer.imported_names, n)
        if cpp is None:
            continue
        src_mod, orig = analyzer.imported_names[n]
        vi = analyzer.registry.get_module(src_mod).variables[orig]
        st = _readonly_global_type(vi.type, analyzer)
        if st is None:
            continue
        cands[n] = st
        spelled[n] = cpp
    if not cands:
        return frozenset(), {}
    scan = scan_reassigned_vars(func.body, pre_declared=set(cands))
    for n in (scan.reassigned | scan.aug_assigned
              | _shadow_bound_names(func.body)):
        cands.pop(n, None)
        spelled.pop(n, None)
    scope.update(cands)
    return frozenset(n for n in cands if n not in spelled), spelled

def lower_function(func: TpyFunction, analyzer, render_type=None,
                   self_type: 'TpyType | None' = None,
                   native_globals: 'Mapping[str, str]' = {},
                   render_type_stored=None) -> THIRFunction | None:
    """Lower one function to THIR, or None if it falls outside the slice.

    `render_type` (codegen's `TypeResolver.type_to_cpp`) renders F1 borrow-local
    decl types byte-identically; omit it only when no non-value local can arise
    (dump / value-scalar standalone lowering). `render_type_stored`
    (`TypeResolver.type_to_cpp_stored`) is its stored-form sibling for the
    slots the AST spells that way (explicit template args on generic calls).
    `self_type` is the owning record's
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
                   record_name=record_name,
                   render_type_stored=render_type_stored)
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
    # renders the same global assign. Only eligible scalars and `Ptr[T]`
    # values seed (a Ptr global is a `T*` VALUE slot: writes render `g = v;`
    # / `g = nullptr;` exactly like a Ptr local reassign); an unseeded
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
        if _eligible_scalar(gt) or _eligible_ptr_value(gt, analyzer):
            params_set[n] = gt
            global_seeded.add(n)
    lc.prescan.global_seeded = frozenset(global_seeded)
    lc.prescan.native_globals = native_globals
    lc.prescan.global_readonly, lc.prescan.global_cpp = _seed_readonly_globals(
        func, analyzer, params_set, native_globals)
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

def _ctor_viewfam_source_ok(value: TpyExpr, fam_t: TpyType,
                            declared: dict[str, TpyType],
                            lc: _LowerCtx) -> bool:
    """A (str / StrView / bytes field, source) pair whose MIL render the tail
    emitter reproduces byte-for-byte. The probed contract per field family:

      * **str / StrView** (owned `std::string` / `std::string_view`): a str
        literal (position-neutral const char[N], lands bare); a str-family
        param name (bare -- std::string's EXPLICIT string_view ctor fires in
        the MIL direct-init, so even a view source takes no wrap); a str-family
        coerce over a param name or a str literal (identity passthrough, or
        the ASSIGN-context `strview_to_str` materialization --
        `std::string(name)`).
      * **bytes** (owned `std::vector<uint8_t>`): a bytes literal (the owned
        `bytes_literal_owned` / empty-vector render); a bytes-family param name
        (the span lifts via the AST's `_view_source_to_owned` -->
        `::tpy::bytes_copy(name)`); the same name under the sema
        `bytesview_to_bytes` coerce (its codegen lambda IS that copy); or the
        zero-arg `bytes()` @cpp_template __init__ (`std::vector<uint8_t>()`,
        an owned rvalue landing bare). Arg-taking ctor overloads are
        @native-function emits the call slice does not spell -> AST.
      * **BytesView fields** and `copy()`-wrapped sources are unprobed -> AST.
    """
    analyzer = lc.analyzer
    if is_bytes_view_type(fam_t):
        return False
    if _unwrap_copy(value, analyzer) is not value:
        return False
    if is_bytes_type(fam_t):
        if isinstance(value, TpyBytesLiteral):
            return True
        if isinstance(value, TpyCall):
            return _template_init_call_fi(value) is not None and not value.args
        src = value
        if isinstance(src, TpyCoerce):
            if src.coercion.name != "bytesview_to_bytes":
                return False
            src = src.expr
        return (isinstance(src, TpyName)
                and _resolved_bytes_value(declared.get(src.name),
                                          analyzer) is not None)
    if isinstance(value, TpyStrLiteral):
        return True
    src = value
    if isinstance(src, TpyCoerce):
        if _coerce_disposition(src) not in ("identity", "materialize"):
            return False
        src = src.expr
        # A StrView field's literal arrives under the identity str_to_strview
        # coerce (probe: renders bare on both paths).
        if isinstance(src, TpyStrLiteral):
            return True
    return (isinstance(src, TpyName)
            and _resolved_str_value(declared.get(src.name),
                                    analyzer) is not None)

def _mil_reject_detail(stmt: 'TpyAssign', analyzer) -> str:
    """`ctor.mil_field.<field-fam>.<source-kind>` -- sizes the generics
    frontier's ctor-MIL buckets; delete the split when the bucket empties."""
    t = analyzer.get_expr_type(stmt.target)
    if t is not None:
        t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(t, OwnType):
        fam = "own"
    elif isinstance(t, TypeParamRef):
        fam = "tparam"
    elif is_list(t) or is_dict(t) or is_set(t) or is_array(t):
        fam = "container"
    elif isinstance(t, NominalType) and t.type_args:
        fam = "genrec_open" if contains_type_param(t) else "genrec_concrete"
    elif isinstance(t, NominalType):
        fam = "record" if t.is_user_record else "nominal"
    elif isinstance(t, OptionalType):
        fam = "optional"
    elif isinstance(t, UnionType):
        fam = "union"
    else:
        fam = type(t).__name__.removesuffix("Type").lower() if t is not None else "untyped"
    src = stmt.value
    while isinstance(src, TpyCoerce):
        src = src.expr
    if isinstance(src, TpyCall):
        fi = src.resolved_function_info
        kind = ("native_call" if fi is not None
                and (fi.native_function or fi.native_name) else "call")
    elif isinstance(src, TpyMethodCall):
        kind = "method"
    elif isinstance(src, TpyFieldAccess):
        kind = "field"
    elif isinstance(src, TpyName):
        kind = "name"
    else:
        kind = type(src).__name__.removeprefix("Tpy").lower()
    return f"ctor.mil_field.{fam}.{kind}"


def _mil_container_field(t) -> bool:
    """A builtin-container field type whose MIL init the container slice
    admits: a list / dict / set / Array instantiation. Span stays out (a
    Span field aliasing a MIL source is a lifetime shape this slice does
    not open; sema rejects the useful forms anyway)."""
    if not isinstance(t, TpyType) or not getattr(t, "type_args", None):
        return False
    return is_list(t) or is_dict(t) or is_set(t) or is_array(t)

def _ctor_field_init_ok(stmt: TpyStmt, own_field_names: set[str],
                        own_param_names: set[str], declared: dict[str, TpyType],
                        body_local_names: set[str], lc: _LowerCtx) -> bool:
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

      * **str / StrView / bytes** fields: the probed literal / param-name /
        str-family-coerce / zero-arg-`bytes()` sources -- see
        `_ctor_viewfam_source_ok` for the per-family contract.

    `copy()` is unwrapped before the Optional check too (so `self.opt = copy(m)`
    routes like the record arm). Further small value families:

      * **Ptr[T]** -- a `None` source (`p(nullptr)`) on top of the
        `_expr_eligible` sources.
      * **pointer-repr union** (F4 U2): the own-param move, `None`
        (`u(std::monostate{})`), a borrow ptr-variant name
        (`to_value_variant`), or a member-record ctor rvalue (`u(A(3))`).
      * **value union** (F4 U1): `None`, a scalar literal, or an eligible name
        -- all bare renders.
      * **tuple**: a borrow pointer-repr tuple param (`tuple_to_storage`); a
        value tuple's same-type name copy or spelled literal.
      * **any Optional** -- a `None` source (`f(std::nullopt)`), inner- and
        repr-independent.

    Field types beyond those (BytesView; cross-module / native / generic
    records) leave the ctor on the AST path."""
    analyzer = lc.analyzer
    if not (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field in own_field_names
            and _field_receiver_ok(stmt.target, declared, analyzer)):
        return False
    # The AST DEMOTES a bare-name RHS that is not a param (`blocked_by_bare_name`
    # in _extract_field_inits -- a conservative "not in scope at MIL time" that
    # covers read-only-seeded globals too), so hoisting one here would diverge.
    # Returning False routes it to the demote mirror in lower_constructor
    # (`_ast_demotes_init`), which sends it to the body like the AST does.
    src_peeled = stmt.value
    while isinstance(src_peeled, TpyCoerce):
        src_peeled = src_peeled.expr
    if (isinstance(src_peeled, TpyName)
            and src_peeled.name not in lc.prescan.param_names):
        return False
    # The AST also DEMOTES an init whose RHS references any top-level body
    # binding (`blocked_by_body_local`) -- including a PARAM reassigned later
    # in the body, which the param-name check above admits. Hoisting one here
    # would diverge, so return False (whole ctor stays AST, the same
    # conservative verdict as the bare-name arm). `body_local_names` is
    # computed once per ctor by the caller (shared with `_ast_demotes_init`).
    if body_local_names and (collect_name_refs(stmt.value) & body_local_names):
        return False
    ftype = analyzer.get_expr_type(stmt.target)
    if (_eligible_scalar(ftype) or _eligible_char(ftype)
            or _eligible_enum(ftype, analyzer) is not None
            or _eligible_ptr_value(ftype, analyzer)):
        # A str-literal source into a Char field is a sema type error; the
        # reject is defensive (the target-typed `'x'` render would diverge).
        if _eligible_char(ftype) and isinstance(stmt.value, TpyStrLiteral):
            return False
        # `self.p = None` into a Ptr[T] field renders `p(nullptr)` --
        # pointee-independent, so it needs no _expr_eligible source arm.
        if (_eligible_ptr_value(ftype, analyzer)
                and isinstance(_unwrap_copy(stmt.value, analyzer),
                               TpyNoneLiteral)):
            return True
        return _expr_eligible(stmt.value, declared, analyzer)
    view_t = _resolved_str_value(ftype, analyzer)
    if view_t is None:
        view_t = _resolved_bytes_value(ftype, analyzer)
    if view_t is not None:
        return _ctor_viewfam_source_ok(stmt.value, view_t, declared, lc)
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
    if _mil_container_field(ftype):
        source = _unwrap_copy(stmt.value, analyzer)
        if isinstance(source, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
            # The MIL is a target-threaded position like a decl init (the AST
            # renders `gen_expr(source, fld_type)`), so the shared container-
            # literal gate applies verbatim. Admitted element rows are all
            # temps_ok=False shapes, so the AST's temps-rollback demote cannot
            # fire on an admitted literal. A TpyListRepeat / comprehension /
            # coerce-wrapped source falls through to the reject.
            return _container_literal_ok(source, ftype, declared, analyzer,
                                         threaded=True)
        if isinstance(source, TpyName):
            # A container param copies bare into the field (`f(p)`); an Own
            # container param at its last use moves (`f(std::move(p))`, the
            # M3b-move arm; the param gate admits any Own payload). Exact-shape
            # pin: a family or element-type mismatch could carry a conversion
            # the bare-name MIL render does not, so only a to_cpp-identical
            # container param is admitted.
            pt = declared.get(source.name)
            if not isinstance(pt, TpyType):
                return False
            pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
            own = unwrap_optional_own(pt)
            if own is not None:
                pt = own.wrapped
            return _mil_container_field(pt) and pt.to_cpp() == ftype.to_cpp()
        return False
    pu = _eligible_ptr_union(ftype, analyzer)
    if pu is not None:
        # F4 U2: an `Own[A | B]` param moves into the value-variant field
        # (`u(std::move(v))` -- the M3b-move arm verbatim, type-agnostic at
        # lowering); a `None` stores the monostate member
        # (`u(std::monostate{})`); a borrow ptr-variant param name lifts via
        # `to_value_variant`; a member-record ctor rvalue constructs the
        # variant directly (`u(A(3))`). Field / member-name sources ride
        # later cells.
        source = _unwrap_copy(stmt.value, analyzer)
        if _is_move_source(source, lc, own_param_names):
            return True
        if isinstance(source, TpyNoneLiteral):
            return True
        if _ptr_union_source_ok(source, declared, analyzer, pu,
                                allow_field=False):
            return True
        return (_is_record_rvalue_source(source, declared, analyzer)
                and analyzer.get_expr_type(source) in pu.members)
    vu = _eligible_value_union(ftype)
    if vu is not None:
        # F4 U1: bare renders only -- the variant converting ctor absorbs a
        # same-union param name, a member-typed param name, a scalar literal
        # (`u(u)` / `u(x)` / `u(5)`; lowering retypes a top-level literal to
        # the union so the BigInt/Float32 slot wraps never fire -- the AST
        # threads the union as the render target, which takes neither), and
        # the monostate `None`. Classification peels sema coerces (a literal
        # source arrives coerce-wrapped); eligibility checks the full expr.
        peeled = _unwrap_copy(stmt.value, analyzer)
        while isinstance(peeled, TpyCoerce):
            peeled = peeled.expr
        if isinstance(peeled, TpyNoneLiteral):
            return True
        return (isinstance(peeled, (TpyIntLiteral, TpyFloatLiteral,
                                    TpyBoolLiteral, TpyName))
                and _expr_eligible(stmt.value, declared, analyzer))
    ft_tuple = _f1_tuple(ftype, analyzer)
    if ft_tuple is not None:
        # F3: a borrow pointer-repr tuple param stores via `tuple_to_storage`
        # (the body field-write arm's MIL sibling). No copy()-unwrap: a
        # `copy()` of a pointer-repr tuple takes the AST's storage-form
        # `_gen_copy_expr` render, which the MIL slice does not mirror.
        if _unwrap_copy(stmt.value, analyzer) is not stmt.value:
            return False
        return _is_borrow_tuple_source(stmt.value, declared, set(), analyzer)
    vt = _value_tuple(ftype, analyzer)
    if vt is not None:
        # A value tuple copies bare (`t(t)` -- borrow and storage coincide;
        # copy() unwraps to the same render) or spells its literal
        # (`t(std::tuple<...>{...})`, the shared return/decl render). Exact
        # type match keeps a convertible-but-differently-spelled tuple out.
        source = _unwrap_copy(stmt.value, analyzer)
        if isinstance(source, TpyName):
            dt = declared.get(source.name)
            dt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
                  if dt is not None else None)
            return dt == vt
        if isinstance(source, TpyTupleLiteral):
            return _tuple_literal_ok(source, vt, declared, analyzer)
        return False
    if isinstance(ftype, OptionalType) and isinstance(
            _unwrap_copy(stmt.value, analyzer), TpyNoneLiteral):
        # `self.f = None` renders `f(std::nullopt)` for EVERY Optional field
        # (pointer-repr or value-repr) -- inner-independent, so the F1-record
        # inner gate below does not apply.
        return True
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
    """A ctor param TYPE that cannot by itself break the pairing of the
    AST-emitted signature with the THIR MIL+body tail. Unlike the function
    gate (`_f1_param_eligible`, whose bodies must carry every param READ),
    the ctor gate only decides whether the whole ctor MAY route: every USE
    is still gated per-site (the MIL field arms, `_body_eligible`'s
    statement/expression gates, and the `_unrouted_binding_read` name-read
    guard), so an unhandled use rejects the whole ctor -> AST path.

    The method-param set routes fully (scalar / F1-record / pointer-repr
    `Optional[F1-record]` / str / bytes / containers / routed unions / `T`);
    on top of it the ctor admits, per-use-gated:

    - any `Own[...]` (the record / ptr-union / `T` / own-optional payloads
      feed the M3b-move MIL arm; every other payload's reads reject via
      `_unrouted_binding_read`);
    - an owned `String` (`const std::string&` -- reads render bare like an
      owned local; the mutated/reassigned shapes reject in
      `lower_constructor`);
    - a value-repr Optional (`std::optional<T>` by value; reads reject via
      `_unrouted_binding_read` -- the AST renders narrowed reads `(*p)`);
    - any `Ptr` / union (bare `T*` / variant values; the non-routed
      pointees/members reject at each sink, incl. the protocol-member union
      whose reads have no arm at all).

    Still rejected: protocol / callable / Waker / reference-container params
    (their SIGNATURE takes the protocol-template / adapter emit paths) and
    pointer-repr optionals of non-F1 inners."""
    # `_f1_param_eligible` already admits a bare `T` param (the type-param slot).
    if _f1_param_eligible(ptype, analyzer):
        return True
    if not isinstance(ptype, TpyType):
        return False
    if unwrap_optional_own(unwrap_readonly(ptype)) is not None:
        return True
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
    if _is_string_owned(u):
        return True
    if isinstance(u, OptionalType) and not u.uses_pointer_repr():
        return True
    return isinstance(u, (PtrType, UnionType))

def lower_constructor(record, init_method: TpyFunction, analyzer,
                      render_type=None,
                      self_type: 'TpyType | None' = None,
                      native_globals: 'Mapping[str, str]' = {},
                      render_type_stored=None,
                      ) -> THIRConstructor | None:
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
    # A colliding record qualifies local type references (incl. a base name in
    # the member-init list) on the AST path; THIR renders them raw, so reject
    # the whole ctor -- mirrors the method-body gate (sig.member_shadows_type).
    if ri.shadows_local_type:
        note("ctor.member_shadows_type")
        return None
    if any(not _f1_record(p, analyzer) for p in ri.parents):
        note("ctor.non_f1_base")
        return None
    if (init_method.is_overload_stub or init_method.native_function
            or init_method.is_async or init_method.is_generator
            or init_method.type_params):
        note("ctor.special_init")
        return None
    # Param TYPES that pair with the THIR tail (see `_ctor_param_eligible`);
    # a param used in an unhandled way is caught per-use (the field-init gate,
    # the body statement gates, the name-read guard).
    for _name, ptype in init_method.params:
        pt = ptype if isinstance(ptype, TpyType) else None
        if not _ctor_param_eligible(pt, analyzer):
            note("ctor.param_type")
            return None
    # A reassigned param needing the owned-copy prologue (String/BigInt/owned
    # bytes...): gen_body emits the `T name = __param_name;` body local for a
    # ctor too -- while the ctor signature never takes the `__param_` rename
    # (a pre-existing AST defect; both halves stay AST-owned). The THIR tail
    # reproduces neither -> AST path. Mirrors sig.param_reassign_copy.
    scan = analyzer.function_scan_results.get(id(init_method))
    if scan is not None and scan.reassigned:
        for pname, ptype in init_method.params:
            pt = ptype if isinstance(ptype, TpyType) else None
            if (pname in scan.reassigned and pt is not None
                    and pt.param_needs_copy_for_reassign()):
                note("ctor.param_reassign_copy")
                return None
    # A MUTATED `String` param: the AST emits the mutation against the
    # untouched `const std::string&` param (ill-formed C++, see BUGS.md) --
    # keep the whole shape AST-owned rather than mirror it. Mirrors
    # `_record_ctor_call_eligible`'s mutated-String slot reject; the indices
    # are the same synthetic-ctor mutation facts the AST signature reads.
    init_fis = ri.get_method_overloads("__init__")
    mut = init_fis[-1].mutated_params if init_fis else None
    if mut:
        for i, (_n, ptype) in enumerate(init_method.params):
            if i in mut and isinstance(ptype, TpyType) and _is_string_owned(ptype):
                note("ctor.param_mutated_string")
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
                   record_name=record.name,
                   render_type_stored=render_type_stored)
    # Read-only value-global seeding, like lower_function's (ctors read module
    # globals too). No `global`-write seeding here: a ctor's `global` names
    # stay unseeded, so its TpyGlobal statement rejects the body -> AST path.
    lc.prescan.native_globals = native_globals
    lc.prescan.global_readonly, lc.prescan.global_cpp = _seed_readonly_globals(
        init_method, analyzer, declared, native_globals)
    # Base initializers (`super().__init__` / `BaseN.__init__`), sorted by parent
    # declaration order (M3d); None if any is outside the slice -> AST path.
    base_inits = _lower_base_inits(init_method, ri, declared, lc)
    if base_inits is None:
        note("ctor.base_init")
        return None
    field_inits: list[TpyAssign] = []
    body_stmts: list[TpyStmt] = []  # demoted inits + non-init stmts + trivia, source order
    body_written_self_fields: set[str] = set()
    # The AST's demote triggers (`_extract_field_inits`): a nested-def-name /
    # bare non-param-name source, or any body-local reference in the RHS.
    nested_def_names = {s.func.name for s in init_method.body
                        if isinstance(s, TpyNestedDef)}
    body_local_names = collect_top_level_local_names(init_method.body)
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
                                        declared, body_local_names, lc)
                and not expr_reads_self_field(stmt.value, body_written_self_fields)):
            field_inits.append(stmt)
            continue
        # A clean leading own-field init (live chain, not reading an earlier
        # inherited-field write) the AST hoists into the MIL but THIR can't reproduce
        # there -- an F3+ field type (str / list / dict) or a source outside
        # the MIL slice -- must keep the whole ctor on the AST path. Demoting it into
        # the body would diverge from the AST's MIL hoist (the AST never demotes a
        # clean leading own-field init). After a chain break, when the init reads an
        # earlier inherited-field write, or when the AST itself demotes the source
        # (`_ast_demotes_init`), the AST demotes too -- those fall through.
        is_own_init = _is_self_own_field_assign(stmt, own_field_names)
        ast_demotes = is_own_init and _ast_demotes_init(
            stmt, lc.prescan.param_names, nested_def_names, body_local_names)
        if (not chain_broken and is_own_init
                and not expr_reads_self_field(stmt.value, body_written_self_fields)
                and not ast_demotes):
            note(_mil_reject_detail(stmt, analyzer))
            return None
        # A demoted own-field init of a non-default-constructible field type
        # raises CodeGenError on the AST path (_reject_nondef_ctor_field_in_body,
        # the MIL would default-init an uncompilable state) -- reject so the AST
        # path still raises it.
        if is_own_init:
            if _nondef_ctor_field(analyzer.get_expr_type(stmt.target), analyzer):
                note("ctor.demote_nondefault_field")
                return None
            if not chain_broken and ast_demotes:
                _witness("mil.demote_mirror")
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

def _ast_demotes_init(stmt: TpyAssign, param_names: set[str],
                      nested_def_names: set[str],
                      body_local_names: set[str]) -> bool:
    """Whether the AST demotes this own-field init to the ctor body regardless
    of the chain state -- `_extract_field_inits`' source triggers: a nested-def
    name, a bare name that is not a param (not in scope at MIL time; covers
    module globals and `self`), or any body-local reference in the RHS. THIR
    must demote identically -- rejecting the ctor here would be safe but
    needlessly conservative; hoisting would diverge. The temps trigger
    (`temps.rollback` -> demote) is NOT mirrored: the gate cannot predict it
    exactly, so temp-registering sources reject the whole ctor instead."""
    src = stmt.value
    while isinstance(src, TpyCoerce):
        src = src.expr
    if isinstance(src, TpyName) and (src.name in nested_def_names
                                     or src.name not in param_names):
        return True
    return bool(body_local_names
                and (collect_name_refs(stmt.value) & body_local_names))

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

def _base_init_arg_ok(a: TpyExpr, declared: dict[str, TpyType], lc: _LowerCtx) -> bool:
    """One base-init arg the tail emitter mirrors. The AST renders every arg
    via TARGET-LESS `gen_expr(a)` -- no retype, no deref, no `_maybe_move` --
    so the admitted rows are exactly the shapes whose bare render matches:

      * an eligible-scalar value expression (the M3d-1 row);
      * a str literal (`"lit"`) / a `None` literal (`nullptr`);
      * an int literal still typed `IntLiteralType` (a BigInt base slot:
        the target-less render is the bare digits), pinned to the +-2^31-1
        literal range like the sibling literal gates;
      * a declared PARAM name of a str-family / F1-record / pointer-repr
        Optional[F1-record] type, incl. `Own[...]` params -- all render as
        the bare name. NB an `Own` param arg renders bare (a COPY into the
        base slot, no `std::move`) on the AST path; mirrored, not fixed.

    Anything that could register a codegen temp is out -- a base-init cell
    has no flush point (same contract as the MIL)."""
    analyzer = lc.analyzer
    at = analyzer.get_expr_type(a)
    if _eligible_scalar(at):
        return _expr_eligible(a, declared, analyzer)
    if isinstance(a, (TpyStrLiteral, TpyNoneLiteral)):
        return True
    if isinstance(a, TpyIntLiteral):
        return (isinstance(at, IntLiteralType)
                and -(2**31 - 1) <= a.value <= 2**31 - 1)
    if not (isinstance(a, TpyName) and a.name in declared
            and a.name != "self"):
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[a.name])))
    if _resolved_str_value(vt, analyzer) is not None:
        return True
    if _optional_ptr_borrow_name(a, declared, analyzer) is not None:
        return True
    own = unwrap_optional_own(vt)
    if own is not None:
        vt = own.wrapped
        if isinstance(vt, OptionalType):
            vt = vt.inner
    return _f1_record(vt, analyzer)

def _lower_base_init_arg(a: TpyExpr, lc: _LowerCtx) -> THIRExpr:
    """Lower one admitted base-init arg. A bare `None` has no generic
    `_lower_expr` arm (its render is always slot-derived elsewhere), so it
    lowers here to the VALUE-form None literal (`nullptr` -- the AST's
    target-less `gen_expr(None)`); everything else takes `_lower_expr`'s
    name/literal arms."""
    if isinstance(a, TpyNoneLiteral):
        return THIRLiteral(result_type=lc.analyzer.get_expr_type(a),
                           value=None, loc=getattr(a, "loc", None))
    return _lower_expr(a, lc)

def _lower_base_init(stmt: TpyStmt, declared: dict[str, TpyType],
                     lc: _LowerCtx) -> 'tuple[THIRBaseInit, TpyType] | None':
    """Lower one base-init call to `(THIRBaseInit, parent_type)`, or None outside the
    slice (the caller reuses `parent_type` for the parent-order rank). Mirrors
    `_extract_base_inits`'s `{parent_type.to_cpp()}({args})` render for both the
    `super().__init__(args)` and the explicit `BaseN.__init__(self, args)` forms (sema
    strips `self` from the latter's args). The base must be F1 (so `to_cpp()` is
    byte-identical) and every arg in `_base_init_arg_ok`'s target-less bare-render
    rows; kwargs / star args are out."""
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
    if not all(_base_init_arg_ok(a, declared, lc) for a in expr.args):
        return None
    for a in expr.args:
        if not _eligible_scalar(analyzer.get_expr_type(a)):
            _witness("baseinit.nonscalar_arg")
            break
    return (THIRBaseInit(base_cpp=parent_type.to_cpp(),
                         args=tuple(_lower_base_init_arg(a, lc) for a in expr.args)),
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
      * a plain **F1-record** -> the `copy()`-unwrapped record-value source [M3b-copy/-rvalue];
      * an owned **bytes** field -> a view (span) source copies via the S6
        STORAGE convert (`::tpy::bytes_copy(...)`, the AST's
        `_view_source_to_owned` / the `bytesview_to_bytes` coerce lambda);
        an owned source (bytes literal / `bytes()` rvalue) lands bare;
      * a **str / StrView** field -> the bare lowered source (std::string's
        EXPLICIT string_view ctor fires in the MIL direct-init; a
        `strview_to_str` coerce materializes itself);
      * a builtin **container** (list / dict / set / Array) -> the shared
        container-literal lowering (the MIL is target-threaded like a decl
        init), a bare container-param copy, or the Own-param move above;
      * the small value families: `Ptr[T]` `None` (`nullptr`), pointer-repr
        union `None`/lift/rvalue (`std::monostate{}` / `to_value_variant` /
        direct construct), value-union bare renders (a top literal retyped to
        the union), pointer-repr tuple `tuple_to_storage`, value-tuple bare
        copy / spelled literal, and `None` into any other Optional
        (`std::nullopt`)."""
    analyzer = lc.analyzer
    ftype = analyzer.get_expr_type(stmt.target)
    loc = getattr(stmt, "loc", None)
    field_cpp = escape_cpp_name(stmt.target.field)
    source = _unwrap_copy(stmt.value, analyzer)
    if _mil_container_field(ftype):
        _witness("mil.container_literal"
                 if isinstance(source, (TpyArrayLiteral, TpyDictLiteral,
                                        TpySetLiteral))
                 else "mil.container_name")
    if _is_move_source(source, lc, own_param_names):
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc), move=True)
    if (_eligible_ptr_value(ftype, analyzer)
            and isinstance(source, TpyNoneLiteral)):
        # None into a `Ptr[T]` cell: a non-STORAGE None renders `nullptr`.
        _witness("mil.ptr_none")
        return THIRMilInit(field_cpp=field_cpp,
                           value=THIRLiteral(result_type=ftype, value=None,
                                             form=Form.VALUE, loc=loc))
    if (_eligible_scalar(ftype) or _eligible_char(ftype)
            or _eligible_enum(ftype, analyzer) is not None):
        return THIRMilInit(field_cpp=field_cpp,
                           value=_slot_literal_retype(
                               _lower_expr(stmt.value, lc), ftype))
    bytes_t = _resolved_bytes_value(ftype, analyzer)
    if bytes_t is not None:
        _witness("mil.bytes_field")
        if (isinstance(source, TpyCoerce)
                and source.coercion.name == "bytesview_to_bytes"):
            # The coerce's codegen lambda IS the view->owned bytes copy;
            # spell it through the S6 STORAGE convert (the identical
            # `::tpy::bytes_copy(...)` render) over the inner name.
            v = THIRFormConvert(result_type=bytes_t,
                                value=_lower_expr(source.expr, lc),
                                form=Form.STORAGE, move=False, loc=loc)
        else:
            v = _lower_expr(source, lc)
            # A view (span) source into the owned vector field copies via the
            # AST's `_view_source_to_owned` chokepoint -- vector has no span
            # ctor; owned sources (literal / `bytes()` rvalue) land bare.
            if v.form is Form.BORROW:
                v = THIRFormConvert(result_type=bytes_t, value=v,
                                    form=Form.STORAGE, move=False, loc=loc)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    if _resolved_str_value(ftype, analyzer) is not None:
        # str/StrView fields take the BARE render: std::string's EXPLICIT
        # string_view ctor fires in the MIL direct-init (the AST adds no wrap
        # there); a sema `strview_to_str` coerce materializes itself.
        _witness("mil.str_field")
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc))
    pu = _eligible_ptr_union(ftype, analyzer)
    if pu is not None:
        # F4 U2 cells: monostate `None`; a borrow ptr-variant name lifting via
        # `to_value_variant` (STORAGE convert -- the body field-write arm's
        # MIL sibling); a member-record ctor rvalue constructing the variant
        # directly.
        if isinstance(source, TpyNoneLiteral):
            _witness("mil.union_none")
            v: THIRExpr = THIRLiteral(result_type=pu, value=None,
                                      form=Form.STORAGE, loc=loc)
        elif isinstance(source, TpyName):
            _witness("mil.union_lift")
            v = THIRFormConvert(result_type=ftype, value=_lower_expr(source, lc),
                                form=Form.STORAGE, move=False, loc=loc)
        else:
            _witness("mil.union_rvalue")
            v = _lower_expr(source, lc)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    vu = _eligible_value_union(ftype)
    if vu is not None:
        # F4 U1 cells render bare (the variant converting ctor does the work);
        # `None` is the monostate member. A top-level int/float literal
        # (possibly coerce-wrapped by sema) retypes to the union so the
        # BigInt wrap / Float32 suffix keyed on the literal's own scalar type
        # never fires -- the AST threads the union as the render target,
        # which takes neither.
        peeled = source
        while isinstance(peeled, TpyCoerce):
            peeled = peeled.expr
        if isinstance(peeled, TpyNoneLiteral):
            _witness("mil.union_none")
            return THIRMilInit(field_cpp=field_cpp,
                               value=THIRLiteral(result_type=vu, value=None,
                                                 form=Form.STORAGE, loc=loc))
        _witness("mil.value_union")
        v = _lower_expr(source, lc)
        if isinstance(v, THIRLiteral) and isinstance(v.value, (int, float)):
            v = replace(v, result_type=vu)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    if _f1_tuple(ftype, analyzer) is not None:
        # F3: the borrow pointer-repr tuple param stores via
        # `tuple_to_storage` (a STORAGE convert; the gate admitted only the
        # bare borrow-name source).
        _witness("mil.tuple_storage")
        return THIRMilInit(
            field_cpp=field_cpp,
            value=THIRFormConvert(result_type=ftype,
                                  value=_lower_expr(stmt.value, lc),
                                  form=Form.STORAGE, move=False, loc=loc))
    vt = _value_tuple(ftype, analyzer)
    if vt is not None:
        if isinstance(source, TpyTupleLiteral):
            _witness("mil.value_tuple_literal")
            return THIRMilInit(field_cpp=field_cpp,
                               value=_lower_tuple_literal(source, vt, lc))
        _witness("mil.value_tuple_name")
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc))
    if isinstance(ftype, OptionalType):
        if isinstance(source, TpyNoneLiteral):
            _witness("mil.optional_none")
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

def method_self_type_by_name(record_name: str, analyzer) -> 'TpyType | None':
    """`_method_self_type` from the record NAME (the resumable seam has the
    record name, not the AST node) -- reads `type_params` / `type_param_kinds`
    / the qname off the `RecordInfo`. None when the record is unregistered."""
    ri = analyzer.registry.get_record(record_name)
    if ri is None:
        return None
    if ri.type_params:
        kinds = ri.type_param_kinds
        args = tuple(
            TypeParamRef(name=p,
                         kind=kinds[i] if i < len(kinds) else TypeParamKind.TYPE)
            for i, p in enumerate(ri.type_params))
        return NominalType(record_name, type_args=args,
                           _module_qname=ri.qualified_name())
    return NominalType(record_name, _module_qname=ri.qualified_name())

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

def module_native_globals(module: TpyModule) -> dict[str, str]:
    """Module-level vars with non-DEFAULT linkage, name -> C/C++ symbol:
    the exact map codegen renders reads/writes through
    (`ctx.native_global_names`), via the shared helper -- THIR lowering
    runs before generator populates ctx, and read-only seeding stamps
    `qualify_native_name(map[name])` on THIRName.cpp, so the map must
    match byte-for-byte, not just over-approximate. A `global` declaration
    naming one never write-seeds (see lower_function)."""
    return module_native_global_names(module.top_level_stmts)

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
