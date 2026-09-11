"""Entry points: function/constructor/module
lowering, and the module iteration helpers the codegen seam calls.
"""

from __future__ import annotations
from collections.abc import Mapping
from dataclasses import replace
from ...parse.nodes import (
    FunctionLinkage,
    SourceLocation,
    TpyArrayLiteral,
    TpyAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBytesLiteral,
    TpyCall,
    TpyCoerce,
    TpyDictComprehension,
    TpyDictLiteral,
    TpyExpr,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyFString,
    TpyFunction,
    TpyIfExpr,
    TpyImport,
    TpyIntLiteral,
    TpyLambda,
    TpyListComprehension,
    TpyListRepeat,
    TpyMatch,
    TpyMethodCall,
    TpyModule,
    TpyName,
    TpyNestedDef,
    TpyNoneLiteral,
    TpyPassStmt,
    TpySetComprehension,
    TpySetLiteral,
    TpySlice,
    TpyStmt,
    TpyStrLiteral,
    TpySubscript,
    TpyTry,
    TpyTupleLiteral,
    TupleElemCapture,
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
    AnyType,
    IntLiteralType,
    RecursiveAliasInstanceType,
    is_dyn_protocol,
    is_fn_type,
    is_protocol_type,
    LiteralType,
    NominalType,
    NoneType,
    OptionalType,
    OwnType,
    UnionType,
    TpyType,
    TypeParamKind,
    TypeParamRef,
    VoidType,
    contains_type_param,
    error_return_to_cpp,
    none_default_cpp_spelling,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
    view_family_for_type,
)
from ...codegen_cpp import emit_prims
from ...codegen_cpp.context import (
    contains_named_expr,
    escape_cpp_name,
    imported_variable_cpp,
    module_init_targets,
    module_native_global_names,
    qualified_cpp_name,
    qualify_native_name,
)
from ...codegen_cpp.functions import (
    build_overload_narrowing,
    default_to_cpp_from_analyzer,
    overload_stubs_are_literal_only,
)
from ...codegen_cpp.forms import LocalBinding
from ...codegen_cpp.gen_generators import GeneratorCodegen
from ...type_def_registry import (
    is_array,
    view_to_owned_conv,
    is_bytes_type,
    is_bytes_view_type,
    is_dict,
    is_list,
    is_set,
)
from ..reject import ThirUnsupported, _walk as _fallback_walk, note
from ..faces import witness as _witness
from ..validate import _iter_children, validate_constructor, validate_function
from ..nodes import (
    THIRCall,
    Form,
    THIRBaseInit,
    THIRConstructor,
    THIRContainerLiteral,
    THIRExpr,
    THIRFormConvert,
    THIRFunction,
    THIRFunctionLayout,
    THIRGenExpr,
    THIRIf,
    THIRLiteral,
    THIRMilInit,
    THIROptViewArg,
    THIRParam,
    THIRAssign,
    THIRName,
    THIRNestedDef,
    THIRPtrLocalDecl,
    THIRPtrLocalRebind,
    THIRVarDecl,
    PtrSlotKind,
    THIROverloadDefault,
    THIRParamCopy,
    THIRMove,
    THIRRecordCopy,
    THIRTupleLiteral,
)
from ...value_category import is_rvalue_source
from .predicates import (
    _peel_coerce,
    _str_literal_value_opt_arg,
    _IDENTITY_STR_COERCIONS,
    _callable_value,
    _ru_instance_literal_ok,
    _span_value,
    _coerce_disposition,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_value_union,
    _f1_container_ref,
    _f1_record,
    _method_rvalue_f1_record,
    _f1_tuple,
    _mixed_own_storage_source,
    _nested_storage_tuple,
    _field_receiver_ok,
    _is_borrow_ptr_local,
    _is_borrow_tuple_source,
    _is_string_owned,
    _is_type_param_slot,
    _own_type_param_slot,
    _optional_ptr_borrow_name,
    _readonly_global_type,
    _pointer_slot_global_type,
    _resolved_bytes_value,
    _resolved_str_value,
    _template_init_call_fi,
    _value_opt_scalar,
    _value_opt_view,
    _opt_view_arg_shim,
    _value_tuple,
)
from .context import (
    _LowerCtx,
    _ONLY_BTUPLE_SLOT,
    _ONLY_RECORD_PRVALUE,
    SinkPos,
    ValueOptKind,
)
from .checks import (
    _container_lit_elem_ok,
    _container_storage_field,
    _optional_container_storage_inner,
    _builtin_container_type,
    _container_literal_shape_ok,
    _ctor_shape_ok,
    _storage_form_tuple_return,
    _lambda_routable,
    _record_rvalue_source_shape,
    _stub_template_param,
    _nondef_ctor_field,
    _ptr_union_source_ok,
)
from .expressions import (
    _ExprResultUse,
    _ExprUse,
    _is_move_source,
    _lower_expr,
    _lower_ru_literal,
    _lower_tuple_literal,
    _slot_literal_retype,
)
from .statements import (
    _lower_stmts,
)

def _overload_reject_detail(func: TpyFunction, stubs, *,
                            allow_arity: bool = False,
                            allow_narrow_params: frozenset = frozenset()
                            ) -> str:
    """Sub-classify an overload-set reject by WHICH per-stub emission fact
    the impl body is sensitive to -- the slice-1 routing frontier. First
    match wins, ordered by disqualification severity; `plain` marks the
    candidates whose per-stub specializations are the same body modulo the
    signature:

    - `arity`: a stub is shorter than the impl (missing-param default locals);
    - `ret_mismatch`: stub return types differ from the impl's (the return
      arm strips/validates per-stub coercions);
    - `db_isinstance`: isinstance/match anywhere in the body (the if-chain
      dead-branch elimination can rewrite it per stub);
    - `narrow_param`: a union/Optional impl param (the narrowing extraction
      skips differently under overload_param_types);
    - `plain`: none of the above.

    Literal folds (equality, truthiness, membership, chain coverage) need
    no fence rows: `_overload_resolve_static` folds all four.

    Tags extend the dot-hierarchical drilldown convention (like
    `call.ret_type.*`), not the `stmt.<shape>:<detail>` colon composition
    (which is auto-composed, never hand-built)."""
    if not allow_arity and any(len(fi.params) != len(func.params)
                               for fi in stubs):
        return "sig.overload_set.arity"
    rt = func.return_type if isinstance(func.return_type, TpyType) else None
    for fi in stubs:
        if fi.return_type != rt:
            return "sig.overload_set.ret_mismatch"
    for stmt in func.body:
        for node in _fallback_walk(stmt):
            if isinstance(node, TpyMatch) or (
                    isinstance(node, TpyCall)
                    and getattr(node, "isinstance_var", None) is not None):
                return "sig.overload_set.db_isinstance"
    for _n, pt in func.params:
        if _n in allow_narrow_params:
            continue
        u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt))) \
            if isinstance(pt, TpyType) else None
        if isinstance(u, (UnionType, OptionalType)):
            return "sig.overload_set.narrow_param"
    return "sig.overload_set.plain"


def _stub_has_template_param(fi) -> bool:
    """A stub whose PARAMS force the template-header path: a protocol- or
    Fn-typed param (bare or under readonly/Own/Optional shells) synthesizes
    type params.

    The caller only WITNESSES this (`fn.overload_template_stub`): the header
    is signature -- printed by the signature emitter, like declared type
    params -- and the stub body lowers against its protocol-typed params
    through the ordinary arms.
    The CALL side is where the shape still rejects (checks.py's
    `_stub_template_param`): a template stub is not the plain named call the
    call gate admits.

    Declared type params are NOT part of this: `template<...>` is signature,
    written by the signature emitter, and the specialization bodies lower
    through the ordinary arms."""
    return any(_stub_template_param(pt) for _n, pt in fi.params)


def _overload_missing_params(func: TpyFunction, stub: TpyFunction) -> list:
    """The impl params a SHORT stub omits (`impl.params[len(stub.params):]`),
    whose defaults emit as prologue locals."""
    return list(func.params[len(stub.params):])


def _short_stub_missing_ok(func: TpyFunction, stub: TpyFunction) -> bool:
    """Whether a short @overload stub's omitted impl params need NO prologue
    local -- the only arity shape admitted.

    Each missing param needs one local (`T x = <default>;`) EXCEPT
    when the param narrows to NoneType and the body never reassigns it: the
    `is not None` guard then folds to False and dead-branch elim strips
    every use. Any other missing param (a literal/typed default that stays
    live, or a reassigned one) needs that prologue render."""
    if len(stub.params) > len(func.params):
        return False
    impl_names = [n for n, _t in func.params]
    if impl_names[:len(stub.params)] != [n for n, _t in stub.params]:
        return False
    missing = _overload_missing_params(func, stub)
    if not missing:
        return True
    narrowing = build_overload_narrowing(func, stub, missing,
                                         func.defaults or [])
    # Params pre-declared, like the sema scan codegen reads: a write to a
    # param name is a REASSIGNMENT, not a first decl.
    reassigned = scan_reassigned_vars(
        list(func.body),
        pre_declared={n for n, _t in func.params}).reassigned
    return all((isinstance(narrowing.get(pname), NoneType)
                and pname not in reassigned)
               # A live missing param takes the default-local prologue;
               # only plain value-scalar locals are supported (a non-value
               # local would need the pointer/binding registrations the
               # prologue node does not carry).
               or _default_local_ok(pt)
               for pname, pt in missing)


def _default_local_ok(pt) -> bool:
    if not isinstance(pt, TpyType):
        return False
    b = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    return bool(_eligible_scalar(b) or _eligible_char(b))


def _stub_default_locals(func: TpyFunction, stub: TpyFunction, analyzer,
                         narrowing) -> 'tuple[THIROverloadDefault, ...]':
    """The short stub's omitted-impl-param prologue: one comment-free
    `{to_cpp} {name} = <default>;` local per LIVE missing param
    (NoneType-narrowed unreassigned params are skipped; dead-branch elim
    strips their every use)."""
    missing = _overload_missing_params(func, stub)
    if not missing:
        return ()
    defaults = func.defaults or []
    reassigned = scan_reassigned_vars(
        list(func.body),
        pre_declared={n for n, _t in func.params}).reassigned
    start = len(stub.params)
    out: list[THIROverloadDefault] = []
    for off, (pname, ptype) in enumerate(missing):
        if (isinstance(narrowing.get(pname), NoneType)
                and pname not in reassigned):
            continue
        default_expr = defaults[start + off]
        cpp_default = default_to_cpp_from_analyzer(analyzer, default_expr,
                                                   ptype)
        if (cpp_default == "0"
                and not isinstance(default_expr, (TpyIntLiteral, TpyCall))):
            # Value-initialize instead, for unrecognized default exprs.
            cpp_default = "{}"
        out.append(THIROverloadDefault(name=escape_cpp_name(pname),
                                       cpp_type=ptype.to_cpp(),
                                       cpp_default=cpp_default))
    return tuple(out)


def _literal_stub_facts(func: TpyFunction, stub: TpyFunction) -> dict:
    """The literal-fact map a literal-only stub injects: impl param names
    zipped to the stub's Literal types (a SHORT stub pairs as a prefix)."""
    return {pname: stub_pt
            for (pname, _), (_, stub_pt) in zip(func.params, stub.params)
            if isinstance(stub_pt, LiteralType)}


def _admit_literal_only_stub(func: TpyFunction, analyzer,
                             stub: TpyFunction) -> None:
    """Admission for a literal-only @overload group (the mangled-name
    path): per-stub emission binds the IMPL's params + the STUB's return
    type and folds if-chains via the injected literal facts (equality,
    truthiness, membership, chain coverage -- `_overload_resolve_static`).

    A SHORT stub (the impl carries defaulted trailing params) needs no
    prologue here, unlike the non-literal path: the emitted signature IS the
    impl's, defaults included, so the omitted params stay C++ params. Its
    names must be a positional PREFIX of the impl's, which is what makes the
    fact pairing below a plain zip.

    A body that WRITES a fact-carrying param rejects
    (`sig.overload_set.literal_fact_write`): a literal fact dies at the
    reassign, while the injected map here is frozen -- folding past the
    write would decide compares that must stay runtime code."""
    impl_names = [n for n, _t in func.params]
    if (len(stub.params) > len(func.params)
            or impl_names[:len(stub.params)] != [n for n, _t in stub.params]):
        raise ThirUnsupported("sig.overload_set.arity")
    facts = _literal_stub_facts(func, stub)
    if facts:
        reassigned = scan_reassigned_vars(
            list(func.body),
            pre_declared={n for n, _t in func.params}).reassigned
        if any(n in reassigned for n in facts):
            raise ThirUnsupported("sig.overload_set.literal_fact_write")


def _admit_overload_stub(func: TpyFunction, group, analyzer,
                         stub: 'TpyFunction | None' = None) -> None:
    """Per-stub lowering admission for a multi-entry @overload set.

    The db_isinstance and ret_mismatch families route (the per-stub fold /
    return-coercion increments); every other sub-reason keeps rejecting
    with its tag. Literal-only groups take their own reduced admission
    (impl-signature emission, so the arity/narrow/ret classifiers here
    don't apply).

    A protocol-param (template) stub is NOT a disqualifier: the template
    header is signature (like declared type params), and the
    stub's body lowers against the stub's protocol-typed params through
    the ordinary arms (the protocol-param loop included)."""
    stubs = analyzer.overload_groups.get(func) or []
    if any(_stub_has_template_param(fi) for fi in stubs):
        _witness("fn.overload_template_stub")
    if overload_stubs_are_literal_only(stubs, func):
        if stub is not None:
            _admit_literal_only_stub(func, analyzer, stub)
        return
    short_ok = stub is not None and _short_stub_missing_ok(func, stub)
    # An Optional impl param SHADOWED by a stub's concrete type (or omitted
    # by a short stub) narrows through `build_overload_narrowing`, which both
    # paths now share -- so the narrowing extraction cannot diverge for it.
    # Union params (multi-member) keep the family reject.
    narrow_ok = frozenset(
        n for n, pt in func.params
        if isinstance(unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
                      if isinstance(pt, TpyType) else None, OptionalType)
    ) if stub is not None else frozenset()
    detail = _overload_reject_detail(
        func, group, allow_arity=short_ok, allow_narrow_params=narrow_ok)
    if detail not in ("sig.overload_set.db_isinstance",
                      "sig.overload_set.ret_mismatch",
                      "sig.overload_set.plain"):
        raise ThirUnsupported(detail)


def _check_callable_structure(func: TpyFunction, analyzer,
                              self_type: 'TpyType | None' = None,
                              *, allow_resumable: bool = False,
                              stub: 'TpyFunction | None' = None) -> None:
    # `allow_resumable` is passed by `lower_resumable` and
    # `lower_simple_generator`: the async/generator arms below are those
    # entries' whole point, but every other signature check (overloads,
    # linkage, shadowing) applies to their bodies exactly like a sync one.
    # A record-owned callable is admitted when its owning record is an
    # F1-record (`self_type` passed by the caller). All method kinds funnel
    # their bodies through the same lowering, so only the receiver model differs:
    # instance methods
    # (M1/M2, dunders included -- the C++ operator wrappers delegating to them
    # are structural emission, not body emission) and property getters/setters
    # lower with a `self` (`this`) receiver; static methods lower like free
    # functions (no receiver -- the `static` prefix, the setter's `set_` rename
    # and the getter's ref-return arm are all signature-only; the getter's
    # body-side return arm determines whether a return shape routes. A record
    # param's const verdict comes from the method's FunctionInfo on the owning
    # record -- see `_param_is_const`.
    #
    # A macro-authored staticmethod reaches here with `is_method` FALSE:
    # `FragmentParser.parse_fragment` sets method-ness by "first param named
    # self", so a `quote_fun` fragment without one parses as a free function,
    # and `quote_fun`'s docstring prescribes setting `is_staticmethod = True`
    # on the result. `self_type` is what makes it record-owned; a genuinely
    # free callable carrying the flag has no owner and still rejects.
    is_record_callable = func.is_method or (func.is_staticmethod
                                            and self_type is not None)
    if is_record_callable:
        if self_type is None or not _f1_record(self_type, analyzer):
            raise ThirUnsupported("sig.receiver_record")
        # Inplace dunders (__iadd__ ...) admit: the forced-const param
        # verdict (CONST_PARAMS_METHODS) is applied by `_param_is_const`'s
        # forced arm -- including the slices decide_param_const drops the
        # force for (`_forced_const_dropped`) -- and the mandatory `return
        # self` renders `return *this;` through the record-self return arm
        # (the T& return type is SKELETON -- the signature emitter's
        # is_inplace_dunder branch).
        # @readonly on a @staticmethod emits with the readonly verdicts dropped
        # (`gen_method_def` branches on `is_const and not is_static`): the const
        # overload and forced-const params are signature-only, emitted by the
        # structural path. The static body has no `self`, so the only
        # readonly-keyed body effect (`const_locals.add("self")`) is unreachable
        # -- the body lowers identically to a plain static.
    elif func.is_staticmethod:
        raise ThirUnsupported("sig.staticmethod_flag")
    # A bodied `@dispatch` variant is self-contained: it has no trailing
    # implementation, so it owns its body, keys its own
    # id(func), and the function driver emits it standalone. Only the bodyless
    # stub -- emitted per-specialization off a shared impl -- is special here.
    bodied_overload = func.is_overload_stub and not func.is_stub
    if (func.is_overload_stub and not bodied_overload) or func.native_function:
        raise ThirUnsupported("sig.special_callable")
    # An overload IMPL body is emitted once per stub with per-stub facts
    # (overload_param_types / literal_overload_facts driving dead-branch
    # elimination, missing-param default locals, and return-coercion
    # stripping), but body emission keys on id(func) --
    # routing the shared impl would hijack every specialization with the
    # unspecialized body. Reject any callable in a multi-entry overload set
    # (functions and methods alike), sub-classified by WHICH per-stub fact
    # the body is sensitive to (the slice-1 routing frontier: an impl
    # sensitive to none of them lowers identically per stub). Two carve-outs
    # on the same argument -- each entry owns its body, so there is no
    # shared-impl to hijack: a property getter+setter pair (one registry name,
    # two bodies) and a bodied `@dispatch` variant.
    #
    # A generator is a second carve-out on the same argument, one tier up:
    # the function driver diverts every generator to its frame emitter (the
    # simple peephole lambda or the resumable frame) before the per-stub seeding
    # loop, so its overload set emits ONE body -- one frame plus one factory
    # carrying the impl signature and its defaults -- and there is no
    # specialization to hijack. Keyed on the frame/peephole ENTRY, not on the
    # shape: the plain function entry reaching the same func must keep its
    # own reject.
    #
    # The async twin does NOT join it: an `async def` STUB is still async
    # (a `...` body is not a generator, which is what keeps generator stubs
    # off this entry), so it reaches the frame emitter itself, which emits
    # one frame plus one factory PER OVERLOAD ENTRY -- three bodies,
    # not one. That emission is broken independently of routing (every frame
    # takes the same struct name), so the shape keeps the overload-set reject
    # until that is fixed.
    single_body = allow_resumable and func.is_generator
    if is_record_callable:
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
            # an auto_own pair carries the flag on BOTH halves, so the
            # consuming one is exempt on the same argument as its twin (it is
            # the half that deep-copies the body). Deriving that here instead
            # is not available: FunctionInfo does not carry the clone flags,
            # and `is_consuming` alone would exempt any consuming method in a
            # 2-entry set. A COMPOSED
            # set (a clone pair over genuine @overload stubs, 4+ entries)
            # keeps rejecting.
            is_clone_pair = (
                len(overloads) == 2
                and (func.auto_readonly_params_resolved
                     or func.is_auto_own_borrowing_clone
                     or func.is_auto_own_consuming_clone))
            if not (is_property_pair or is_clone_pair or bodied_overload):
                if stub is not None:
                    _admit_overload_stub(func, overloads, analyzer, stub)
                else:
                    raise ThirUnsupported(
                        _overload_reject_detail(func, overloads))
    else:
        fis = analyzer.registry.get_function(func.name)
        if fis is not None and len(fis) > 1:
            if stub is not None:
                _admit_overload_stub(func, fis, analyzer, stub)
            elif not (single_body or bodied_overload):
                raise ThirUnsupported(_overload_reject_detail(func, fis))
    if func.builtin_decorator_key is not None:
        raise ThirUnsupported("sig.builtin_decorator")
    if not allow_resumable:
        if func.is_async:
            raise ThirUnsupported("sig.async")
        if func.is_generator:
            # Sub-tagged by the peephole predicate: the two populations
            # are different emitters, so each rejects under its own tag.
            raise ThirUnsupported(
                "sig.generator_simple"
                if GeneratorCodegen.is_simple_generator(func)
                else "sig.generator_resumable")
    if func.error_return is not None and (func.is_async or func.is_generator):
        # The sync @error_return body routes (the return-tier renders live on
        # THIRReturn/THIRRaise + the bind/discard/unwrap nodes); the resumable
        # emitters have no expected-return handling, so those reject.
        raise ThirUnsupported("sig.error_return")
    # A generic callable routes its body via the same TypeParamRef T-value
    # arms F5 built for generic-record methods: the resolver spells each
    # `[T]` param/return as a TypeParamRef, `_is_type_param_slot` checks it
    # as a form-neutral value pass-through (`val_or_ref_t<T>` resolves
    # value-vs-ref per instantiation), and the template header is
    # signature. A method's OWN type params (`def m[U](self, x: U)`) spell the
    # same way -- on a generic record the record's T rides the F5
    # self-feed while the method's U rides these slots, so both compose.
    # An INT-kind param (`[N: int]`) is a template VALUE param
    # (`std::size_t N`); its name is seeded as an INT TypeParamRef
    # binding so body reads render bare `N`.
    if func.linkage != FunctionLinkage.DEFAULT:
        if func.linkage is not FunctionLinkage.EXPORT_C:
            raise ThirUnsupported("sig.linkage")
        # An @export(binding="C") BODY renders like a plain function's --
        # the driver owns the extern "C" signature, and sema has already
        # confined its params to types with a C spelling, none of which the
        # body reads differently from their plain-function form.
    # A reassigned param of a type flagged param_needs_copy_for_reassign (owned
    # str/bytes/String, BigInt -- const-ref params that cannot reassign in
    # place) gets a mutable owned copy hoisted into the prologue
    # (`::tpy::BigInt x = __param_x;` + signature rename). Sync bodies emit
    # that prologue via `_param_reassign_copies` in lower_function.
    # ASYNC resumables need no gate: the frame member respells owned at the
    # SKELETON (`std::string t;` -- gen_async owns the member spelling), no
    # prologue arises, and the body reads ride the frame-field arms.
    # GENERATOR bodies KEEP the reject: the simple-gen peephole respells the
    # reassigned param in its lambda capture, a render the leaves do not
    # produce.
    if allow_resumable and func.is_generator:
        scan = analyzer.function_scan_results.get(func)
        if scan is not None and scan.reassigned:
            for name, ptype in func.params:
                pt = ptype if isinstance(ptype, TpyType) else None
                if (name in scan.reassigned and pt is not None
                        and pt.param_needs_copy_for_reassign()):
                    raise ThirUnsupported("sig.param_reassign_copy")

def _param_reassign_copies(func: TpyFunction,
                           analyzer,
                           params=None) -> 'tuple[THIRParamCopy, ...]':
    """The mutable-owned-copy prologue for reassigned const-ref params, in
    param order: `{to_cpp} {name} = __param_{name};`, no source comment. The
    signature-side rename (`gen_params`' `__param_` branch) keys on the same
    scan.reassigned + param_needs_copy_for_reassign facts, so the renamed
    param and the prologue stay paired. Two init shapes: the plain
    `__param_x` read (BigInt, String), and the view-family respell
    (str/bytes, Optional thereof) -- the copy respells the LOCAL owned
    (`std::string(__param_p)` / the make_optional split) while body reads
    keep their view-form renders. Non-value params cannot be reassigned at
    all (sema rejects the rebind), so no other copy shape arises."""
    scan = analyzer.function_scan_results.get(func)
    if scan is None or not scan.reassigned:
        return ()
    copies: list[THIRParamCopy] = []
    # `params` overrides the type source for a per-@overload-stub lowering
    # (the prologue keys on the STUB's param types); the scan facts stay
    # the impl's.
    for name, ptype in (params if params is not None else func.params):
        pt = ptype if isinstance(ptype, TpyType) else None
        if not (name in scan.reassigned and pt is not None
                and pt.param_needs_copy_for_reassign()):
            continue
        opt_inner = pt.inner if isinstance(pt, OptionalType) else None
        fam = view_family_for_type(opt_inner if opt_inner is not None else pt)
        init_cpp = None
        if fam is not None:
            # A view-family param's copy respells the LOCAL owned
            # (`std::string p = std::string(__param_p);` / the Optional
            # make_optional split). Body reads keep their view-form
            # renders -- the owned local converts implicitly at every
            # view sink (the read model is param-type-keyed, never
            # respelled).
            conv = view_to_owned_conv(fam.owned_type)
            pref = f"__param_{escape_cpp_name(name)}"
            if opt_inner is not None:
                init_cpp = (f"{pref} ? std::make_optional("
                            f"{conv}(*{pref})) : std::nullopt")
            else:
                init_cpp = f"{conv}({pref})"
            _witness("fn.param_copy_viewfam")
        copies.append(THIRParamCopy(name=escape_cpp_name(name),
                                    cpp_type=pt.to_cpp(),
                                    init_cpp=init_cpp))
    if copies:
        _witness("fn.param_copy")
    return tuple(copies)

def _shadow_bound_names(stmts: list[TpyStmt]) -> set[str]:
    """Names bound by the binder forms `scan_reassigned_vars` does not record:
    except-`as` bindings and match captures. A candidate read-only global one
    of these shadows must not seed -- the binder may be hoisted/predeclared at
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

def _seed_imported_globals(analyzer, cands: dict[str, TpyType],
                           spelled: dict[str, str], slots: set[str],
                           *, skip) -> None:
    """Seed every IMPORTED module variable that reads through a fixed
    qualified spelling (`imported_variable_cpp`, the render
    authority) into `cands`/`spelled`, and the pointer-slot ones into
    `slots` as well. Shared by function bodies and module init so the two
    cannot drift on which imported globals are readable and how they spell.

    A name REDEFINED in this module (`top_level_decls`) is never seeded:
    the local definition wins from its decl line onward, and the reads
    before it ride `pre_decl_import_cpp`. `skip` adds the caller's own
    exclusions (params / `global`-declared / sema-hoisted names)."""
    for n in analyzer.imported_names:
        if (n in cands or n in analyzer.ctx.top_level_decls or skip(n)):
            continue
        cpp = imported_variable_cpp(analyzer.registry,
                                    analyzer.imported_names, n)
        if cpp is None:
            continue
        src_mod, orig = analyzer.imported_names[n]
        vi = analyzer.registry.get_module(src_mod).variables[orig]
        st = _readonly_global_type(vi.type, analyzer)
        if st is not None and _value_opt_scalar(st, analyzer) is not None:
            # An IMPORTED value-opt global stays unseeded: the narrowed
            # (*qualified) render is unverified at imported-global deref
            # sites -- same-module only for now.
            continue
        if st is None:
            # An imported pointer-slot global reads through the qualified
            # spelling with the same slot renders (`(*::tpyapp::mod::g)`);
            # `vi.is_pointer` is the render authority
            # (is_indirect_name's imported branch).
            if (not getattr(vi, "is_pointer", False)
                    or _pointer_slot_global_type(vi.type, analyzer) is None):
                continue
            cands[n] = _pointer_slot_global_type(vi.type, analyzer)
            slots.add(n)
            spelled[n] = cpp
            continue
        cands[n] = st
        spelled[n] = cpp


def _seed_readonly_globals(
        func: TpyFunction, analyzer, scope: dict[str, TpyType],
        native_globals: 'Mapping[str, str]',
) -> tuple[frozenset[str], dict[str, str], frozenset[str]]:
    """Seed the globals `func` only ever READS into `scope` (mutated in
    place); returns `(bare, spelled, slots)`: the same-module VALUE names
    that render bare, the native/imported names mapped to their
    pre-rendered spelling (THIRName.cpp), and the POINTER-SLOT names
    (non-value record/container globals -- `T* g{};` slots whose reads
    ride the pointer-local arms via lc.pointers; Final and native-linkage
    names are excluded, matching the generator's pointer_globals set).

    Sema resolves an unassigned name to the module global, and a value
    global's read renders bare (`is_indirect_name` is False for
    value globals, and `_maybe_convert_opt_view_param` is param-keyed) --
    or, for a native-linkage / imported global, as a fixed spelling
    (`qualify_native_name` / `imported_variable_cpp`) -- so a seeded name
    routes through every existing name-read arm unchanged, the spelled ones
    differing only in the verbatim-`cpp` render. Same-module candidates
    come from `top_level_decls` (an imported name REDEFINED there reads
    bare in functions -- `top_level_decls` takes precedence over
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
    slots: set[str] = set()
    global_decls = analyzer.function_global_decls.get(func, set())
    hoisted = analyzer.function_hoisted_vars.get(func, set())
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
            # A same-module POINTER-SLOT global (the predicate excludes
            # Final and native-linkage names -- namespace-scope values,
            # not slots -- like the generator's pointer_globals set).
            st = _pointer_slot_global_type(gt, analyzer, name=n,
                                           native_globals=native_globals)
            if st is None:
                continue
            cands[n] = st
            slots.add(n)
            continue
        cands[n] = st
        if n in native_globals:
            spelled[n] = qualify_native_name(native_globals[n])
    _seed_imported_globals(analyzer, cands, spelled, slots,
                           skip=lambda n: (n in scope or n in global_decls
                                           or n in hoisted))
    if not cands:
        return frozenset(), {}, frozenset()
    scan = scan_reassigned_vars(func.body, pre_declared=set(cands))
    for n in (scan.reassigned | scan.aug_assigned
              | _shadow_bound_names(func.body)):
        cands.pop(n, None)
        spelled.pop(n, None)
        slots.discard(n)
    scope.update(cands)
    return (frozenset(n for n in cands
                      if n not in spelled and n not in slots),
            spelled, frozenset(slots))

def _seed_int_kind_tparams(func: TpyFunction, record_name: 'str | None',
                           analyzer, params_set: dict[str, TpyType]) -> None:
    """Template VALUE params (`[N: int]`, a C++ `std::size_t N`) read as
    bare names in the body (`return Int32(N * 2)` -> `mul_check(N, 2)`);
    seed each -- the function's own and the owning record's -- as an
    INT-kind TypeParamRef binding so the name reads route."""
    if record_name:
        ri = analyzer.registry.get_record(record_name)
        if ri is not None:
            for p, k in zip(ri.type_params, ri.type_param_kinds):
                if k is not TypeParamKind.TYPE and p not in params_set:
                    params_set[p] = TypeParamRef(p, kind=k)
    for p, k in zip(func.type_params, func.type_param_kinds):
        if k is not TypeParamKind.TYPE and p not in params_set:
            params_set[p] = TypeParamRef(p, kind=k)

def _seed_global_scope(func: TpyFunction, analyzer, lc: '_LowerCtx',
                       params_set: dict[str, TpyType],
                       native_globals: 'Mapping[str, str]') -> None:
    """Seed module-global names into the walk scope + prescan (shared by the
    sync and simple-generator entries; resumables use frame fields instead).

    `global`-declared names seed the scope like params: their writes then
    lower as reassignments (`g = v;`) and
    reads render bare -- the same-module plain-scalar-global spelling. The
    seeding is WHOLE-function, keyed on
    `function_global_decls`, so even a write textually BEFORE its `global`
    statement (which sema accepts -- a CPython-parity gap, see BUGS.md)
    renders the same global assign. Only eligible scalars and `Ptr[T]`
    values seed (a Ptr global is a `T*` VALUE slot: writes render `g = v;`
    / `g = nullptr;` exactly like a Ptr local reassign); an unseeded
    name keeps its `global` statement ineligible, which rejects the WHOLE
    body regardless of statement order (the TpyGlobal arm reads
    `prescan.global_seeded`, not walk state), so no unseeded-global write
    can survive to misroute as a fresh local decl. Native-linkage globals
    seed like same-module ones, with the split spelling recorded in
    `global_write_cpp` (bare C-name write target) / `global_cpp`
    (`::`-qualified reads)."""
    global_seeded: set[str] = set()
    global_write_cpp: dict[str, str] = {}
    decl_slots: set[str] = set()
    for n in analyzer.function_global_decls.get(func, set()):
        if n in params_set:
            continue
        gt = analyzer.ctx.global_scope.lookup(n)
        if gt is None:
            nb = analyzer.global_ns.lookup_local(n)
            gt = (nb.type if nb is not None
                  and nb.kind is BindingKind.VARIABLE else None)
            if gt is None:
                continue
        gt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(gt)))
        if (_eligible_scalar(gt) or _eligible_ptr_value(gt, analyzer)
                or _value_opt_scalar(gt, analyzer) is not None
                # A str global writes the plain owned assign
                # (`label = "longer";`) and reads through the same
                # view/owned duality as a str LOCAL's binding. NATIVE-
                # linkage str globals stay out: THIRStrAppend spells the
                # bare TPy name, not the C spelling the aug-assign would
                # need there.
                or (_resolved_str_value(gt, analyzer) is not None
                    and n not in native_globals)):
            params_set[n] = gt
            global_seeded.add(n)
            if _value_opt_scalar(gt, analyzer) is not None:
                # Value-opt globals read/write like value-opt locals
                # (`= std::nullopt`, `.has_value()`, narrowed `(*g)`).
                lc.value_opt_bindings[n] = ValueOptKind.SCALAR
            if n in native_globals:
                # A native-linkage global writes through its BARE C name
                # (`g_counter = val;` -- the native_global_names target,
                # unqualified) and reads through the `::`-qualified
                # spelling like any other native-global read.
                global_write_cpp[n] = native_globals[n]
        elif _pointer_slot_global_type(
                gt, analyzer, name=n,
                native_globals=native_globals) is not None:
            # A `global`-declared POINTER-SLOT global: sema forbids
            # rebinding a non-value global, so the name is only ever read /
            # mutated in place -- seed it like the read-only slots.
            params_set[n] = _pointer_slot_global_type(
                gt, analyzer, name=n, native_globals=native_globals)
            global_seeded.add(n)
            decl_slots.add(n)
    lc.prescan.global_seeded = frozenset(global_seeded)
    lc.prescan.global_write_cpp = global_write_cpp
    lc.prescan.native_globals = native_globals
    (lc.prescan.global_readonly, lc.prescan.global_cpp,
     lc.prescan.global_slots) = _seed_readonly_globals(
        func, analyzer, params_set, native_globals)
    lc.prescan.global_slots = lc.prescan.global_slots | frozenset(decl_slots)
    # Pointer-slot globals ride every pointer-local render arm (`->`
    # receivers, `(*g)` derefs, alias binds); read-only / rebind-forbidden
    # seeding means no write/reseat arm can ever fire on them.
    lc.pointers.update(lc.prescan.global_slots)
    for n in lc.prescan.global_readonly:
        if _value_opt_scalar(params_set.get(n), analyzer) is not None:
            # Read-only value-opt globals ride the value-opt local read
            # arms (bare whole-optional, narrowed `(*g)`, unproven
            # deref_optional_check).
            lc.value_opt_bindings[n] = ValueOptKind.SCALAR
        elif _f1_tuple(params_set.get(n), analyzer) is not None:
            # A read-only F3 tuple global is a namespace-scope STORAGE
            # lvalue: register it with the storage-form tuple names so its
            # reads tag STORAGE (bare copy at storage sinks, the
            # tuple_to_pointer lift at borrow-tuple param slots) instead of
            # the borrow default a declared ptr-repr tuple name gets.
            lc.storage_tuple_locals.add(n)
    for n, cname in global_write_cpp.items():
        # Reads of a write-seeded native global keep the ordinary
        # `::`-qualified native-read spelling (the read arm is
        # global-decl-blind).
        lc.prescan.global_cpp.setdefault(n, qualify_native_name(cname))

def lower_function(func: TpyFunction, analyzer, render_type=None,
                   self_type: 'TpyType | None' = None,
                   native_globals: 'Mapping[str, str]' = {},
                   render_type_stored=None,
                   render_resolve=None,
                   stub: 'TpyFunction | None' = None,
                   render_concept=None) -> THIRFunction | None:
    """Lower one function to THIR, or None if it falls outside the slice.

    `render_type` (codegen's `TypeResolver.type_to_cpp`) renders F1 borrow-local
    decl types; omit it only when no non-value local can arise
    (dump / value-scalar standalone lowering). `render_type_stored`
    (`TypeResolver.type_to_cpp_stored`) is its stored-form sibling for the
    slots spelled that way (explicit template args on generic calls).
    `self_type` is the owning record's
    type when `func` is a record method: for kinds with a receiver (instance /
    property / dunder) `self` is seeded as an F1-record receiver (a `this`
    pointer) so its field reads route the same as a param's; a static method
    keeps only the record for its param-const lookups. `stub` is the
    @overload stub whose per-stub facts this lowering specializes (the
    driver seeds one entry per (impl, stub) pair); None for an ordinary
    single-signature body."""
    try:
        _check_callable_structure(func, analyzer, self_type, stub=stub)
        if stub is not None:
            # The body references impl param names while the signature binds
            # the stub's; the two coincide in practice (zip-keyed narrowing
            # depends on it) -- reject the divergent spelling rather than
            # lower reads against the wrong names. A SHORT stub binds a
            # prefix; its omitted params are admitted only when they need no
            # prologue local. A stub cannot re-spell @error_return.
            _impl_names = [n for n, _t in func.params]
            _stub_names = [n for n, _t in stub.params]
            if (_impl_names[:len(_stub_names)] != _stub_names
                    or func.error_return is not None):
                raise ThirUnsupported("sig.overload_set.param_names")
    except ThirUnsupported as ex:
        note(ex.reason, ex.loc)
        return None
    # A static method has no receiver -- it lowers like a free function, but
    # keeps `record_name` so `_param_is_const` resolves its param verdicts from
    # the method's FunctionInfo on the owning record (the same lookup codegen's
    # `_get_method_mutated_params` uses). That holds for either spelling of a
    # static: the parser's (`is_method` set) and the macro API's (`is_method`
    # clear, ownership carried only by `self_type`).
    is_record_method = self_type is not None and (func.is_method
                                                  or func.is_staticmethod)
    if is_record_method and not func.is_method:
        _witness("fn.macro_staticmethod")
    has_self = is_record_method and not func.is_staticmethod
    self_receiver = "self" if has_self else None
    record_name = (self_type.name
                   if is_record_method and isinstance(self_type, NominalType)
                   else None)
    # A literal-only group's per-stub emission binds the IMPL's signature:
    # only the return type and the injected literal facts are per-stub, so
    # no params override applies.
    literal_group = (stub is not None
                     and overload_stubs_are_literal_only(
                         analyzer.overload_groups.get(func) or [], func))
    lc = _LowerCtx(func, analyzer, render_type, self_receiver=self_receiver,
                   record_name=record_name,
                   render_type_stored=render_type_stored,
                   render_resolve=render_resolve,
                   render_concept=render_concept,
                   params_override=(stub.params
                                    if stub is not None and not literal_group
                                    else None),
                   return_type_override=(
                       stub.return_type
                       if stub is not None
                       and isinstance(stub.return_type, TpyType) else None))
    if func.error_return is not None:
        lc.error_return_cpp = error_return_to_cpp(
            func.error_return, analyzer.ctx.module_name, analyzer.registry)
    params_set: dict[str, TpyType] = {n: t for n, t in func.params}
    default_locals: 'tuple[THIROverloadDefault, ...]' = ()
    if stub is not None and literal_group:
        # Literal-only specialization: impl params bind as-is; the stub
        # contributes its return type + the literal facts driving the
        # if-chain dead-branch fold. The facts also merge into the live
        # literal_facts map at entry, so expression-level consumers (the
        # compare-fold fence) see them too.
        lc.overload_literal_facts = _literal_stub_facts(func, stub)
        lc.literal_facts.update(lc.overload_literal_facts)
        lc.overload_stub_return = (stub.return_type
                                   if isinstance(stub.return_type, TpyType)
                                   else None)
    elif stub is not None:
        # Per-stub specialization: the STUB's param types bind, so every
        # binding class, borrow
        # form, and narrowing decision keys on them -- a union impl param
        # narrowed to a concrete stub member is a plain record param here,
        # not a ptr-variant (the _LowerCtx/_Prescan overrides re-key the
        # signature-derived facts the same way).
        params_set = {n: t for n, t in stub.params}
        # A short stub's omitted params narrow from the impl's defaults, so
        # the dead-branch fold sees the per-stub facts.
        lc.overload_narrowing = build_overload_narrowing(
            func, stub, _overload_missing_params(func, stub),
            func.defaults or [])
        # LIVE missing params become prologue locals; seed their bindings
        # so body reads/reassignments resolve against them.
        default_locals = _stub_default_locals(func, stub, analyzer,
                                              lc.overload_narrowing)
        for dl_name, dl_type in _overload_missing_params(func, stub):
            params_set[dl_name] = dl_type
        lc.overload_stub_return = (stub.return_type
                                   if isinstance(stub.return_type, TpyType)
                                   else None)
    _seed_int_kind_tparams(func, record_name, analyzer, params_set)
    if has_self:
        params_set["self"] = self_type  # the record receiver, a field source
        if func.is_readonly:
            # A readonly method's `this` is const, so a borrow local off `self.opt`
            # lifts to `const T*` (the OPTIONAL_TO_PTR const bump keys on the
            # receiver being in const_locals -- see _f1_is_const).
            lc.const_locals.add("self")
    _seed_global_scope(func, analyzer, lc, params_set, native_globals)
    src_params = (func.params if stub is None or literal_group
                  else stub.params)
    src_rt = func.return_type if stub is None else stub.return_type
    params = tuple(THIRParam(name=n, type=t) for n, t in src_params)
    rt = src_rt if isinstance(src_rt, TpyType) else VoidType()
    # Seeded with params (and `self`): a write to such a name is a reassignment.
    declared: dict[str, TpyType] = dict(params_set)
    try:
        param_copies = _param_reassign_copies(func, analyzer, params=src_params)
        if stub is not None and default_locals:
            param_copies = param_copies + default_locals
        body = _lower_stmts(func.body, lc, declared, top_level=True)
        if lc.unhandled_hoists:
            raise ThirUnsupported("body.hoisted_vars")
        fn = THIRFunction(
            name=func.name,
            params=params,
            return_type=rt,
            body=param_copies + body,
            layout=THIRFunctionLayout(),
            error_return_cpp=lc.error_return_cpp,
            suppress_trailing_comments=lc.overload_terminated,
        )
        if _rejects_lambda_hoist(fn.body):
            raise ThirUnsupported("nested_def.rebind_slot_hoist")
        validate_function(fn)
        return fn
    except ThirUnsupported as ex:
        note(ex.reason, ex.loc)
        return None

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
                            own_param_names: set[str], lc: _LowerCtx,
                            ftype: TpyType) -> bool:
    """A record-producing source that constructs an F1-record field (or its
    pointer-repr `Optional`) *directly* via an implicit copy/construct -- as opposed
    to a borrow `T*` that must lift through `ptr_to_optional`. Three shapes:

      * a non-own **F1-record param name** (`other`) -- an implicit MIL copy;
      * an **F1-record ctor-call rvalue** (`Inner(scalars)`) -- the F2d
        `_record_rvalue_source_shape`, emitted as the bare `Name(args)` prvalue;
      * an **F1-record field-read off a param** receiver (`other.g`) -- a field copy.

    An own param at its LAST use moves and never reaches here; at a non-last
    use it is a warned copy (sema's `copies ... field`) and takes the same
    bare `field(param)` MIL as a plain record param -- admitted only when the
    `Own[...]` payload is exactly `ftype`, so the pointer-repr `Optional`
    field (where the payload is the Optional's inner) keeps rejecting.
    `self.<field>` reads stay out: their pointee may be uninitialized at MIL
    time, which is ordering-sensitive."""
    analyzer = lc.analyzer
    if isinstance(source, TpyName):
        dt = declared.get(source.name)
        if source.name in own_param_names:
            # `ftype` is REQUIRED, not defaulted: a caller that omitted it
            # would silently un-fire this arm rather than fail.
            own = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
                   if dt is not None else None)
            return (isinstance(own, OwnType) and own.wrapped == ftype
                    and _f1_record(own.wrapped, analyzer)
                    and _witness("mil.own_param_copy"))
        return _f1_record(dt, analyzer)
    if isinstance(source, TpyCall):
        # The MIL is the second position-gated sink for a plain `@native`
        # record ctor (`self._logger = LogHandle(name)` ->
        # `_logger(::mylog::LogHandle(name))`): the field-type side already
        # admits native records (`_f1_record`), only the source shape gated.
        return (_record_rvalue_source_shape(source, analyzer)
                or (_ctor_shape_ok(source, analyzer, native_ok=True)
                    and _witness("mil.native_ctor")))
    # An Own-returning METHOD-call rvalue (`self.shared = Rc.new(Val(0))` ->
    # `shared(Rc<Val>::new_<Val>(Val(0)))`): the same direct construct; the
    # method-call lowering validates callee/args recursively.
    if _method_rvalue_f1_record(source, analyzer):
        return True
    if isinstance(source, TpyFieldAccess):
        return (isinstance(source.obj, TpyName)
                and source.obj.name != lc.self_receiver
                and _field_receiver_ok(source, declared, analyzer)
                and _f1_record(analyzer.get_expr_type(source), analyzer))
    return False

def _ctor_viewfam_source_ok(value: TpyExpr, fam_t: TpyType,
                            declared: dict[str, TpyType],
                            lc: _LowerCtx) -> bool:
    """A (str / StrView / bytes field, source) pair the tail emitter can
    render into the MIL. The contract per field family:

      * **str / StrView** (owned `std::string` / `std::string_view`): a str
        literal (position-neutral const char[N], lands bare); a str-family
        param name (bare -- std::string's EXPLICIT string_view ctor fires in
        the MIL direct-init, so even a view source takes no wrap); a str-family
        coerce over a param name or a str literal (identity passthrough, or
        the ASSIGN-context `strview_to_str` materialization --
        `std::string(name)`).
      * **bytes** (owned `std::vector<uint8_t>`): a bytes literal (the owned
        `bytes_literal_owned` / empty-vector render); a bytes-family param name
        (the span lifts to owned: `::tpy::Bytes(name)`); the same name under the sema
        `bytesview_to_bytes` coerce (its codegen lambda IS that copy); or the
        zero-arg `bytes()` @cpp_template __init__ (`std::vector<uint8_t>()`,
        an owned rvalue landing bare). Arg-taking ctor overloads are
        @native-function emits the call slice does not spell, so they reject.
      * **BytesView fields** and `copy()`-wrapped sources reject.
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
    if (isinstance(src, (TpyCall, TpyMethodCall))
            and is_rvalue_source(analyzer, src)
            and _resolved_str_value(analyzer.get_expr_type(src),
                                    analyzer) is not None):
        # An owned-str-returning call rvalue (`message(s.speak())`): the
        # prvalue lands bare in the MIL direct-init; the call
        # re-validates itself during the source's lowering.
        return True
    if isinstance(src, TpyCoerce):
        if _coerce_disposition(src) not in ("identity", "materialize"):
            return False
        src = src.expr
        # A StrView field's literal arrives under the identity str_to_strview
        # coerce, which renders bare.
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


def _mil_ptr_tuple_elem_ok(elem: TpyExpr, slot: TpyType,
                           declared: dict[str, TpyType], lc: _LowerCtx, *,
                           own_params: 'set[str]' = frozenset()) -> bool:
    """One pointer-repr-tuple-literal element the MIL cell admits, per SLOT
    family (`_f1_tuple_element_ok` families):

      * value-scalar slot <- a same-family scalar param name or an int/float/
        bool literal (the shared `_slot_literal_retype` render);
      * F1-record slot <- a same-typed record param name (capture VALUE, the
        brace-init copies) or an explicit `copy(param)` (renders the copy-ctor
        call `T(p)`);
      * pointer-repr `Optional[F1-record]` slot <- a bare record param name of
        the INNER type (the optional's converting ctor absorbs the lvalue).

    A pointer-repr-optional param source (a `T*` binding) stays out of every
    slot -- optional<T> takes no T* implicitly, and the per-element lift
    logic that would be needed is not lowered here."""
    analyzer = lc.analyzer
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))

    def _param_type(name: str) -> 'TpyType | None':
        dt = declared.get(name)
        if dt is None:
            return None
        return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))

    if _eligible_scalar(bare) or _eligible_char(bare):
        if isinstance(elem, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
            return True
        if isinstance(elem, TpyName):
            pt = _param_type(elem.name)
            return pt is not None and (_eligible_scalar(pt)
                                       or _eligible_char(pt)) and pt == bare
        return False
    if _f1_record(bare, analyzer):
        src = _unwrap_copy(elem, analyzer)
        if (src is elem and isinstance(src, TpyName)
                and isinstance((_pt := _param_type(src.name)), OwnType)
                and unwrap_readonly(_pt.wrapped) == bare
                and _is_move_source(src, lc, own_params)):
            # An Own[T] param at its LAST USE moves into the element slot
            # (`{1, std::move(b)}` inside the tuple_to_storage wrap).
            return True
        if (_record_rvalue_source_shape(src, analyzer)
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(src)))) == bare):
            # A record ctor-call RVALUE (`Box(5)`) constructs straight into
            # the element slot -- the spelled literal's brace-init absorbs
            # the prvalue, no move and no lift.
            return True
        return (isinstance(src, TpyName)
                and _param_type(src.name) == bare)
    if (isinstance(bare, OptionalType) and bare.uses_pointer_repr()
            and _f1_record(bare.inner, analyzer)):
        # `None` stores the storage-form nullopt (`{std::nullopt, ..}` --
        # the spelled literal is the STORAGE tuple, so the element is the
        # optional's own empty value, not the borrow nullptr).
        if isinstance(elem, TpyNoneLiteral):
            return True
        return (isinstance(elem, TpyName)
                and _param_type(elem.name) == bare.inner)
    return False


def _ctor_field_init_ok(stmt: TpyStmt, own_field_names: set[str],
                        own_param_names: set[str], declared: dict[str, TpyType],
                        lc: _LowerCtx) -> bool:
    """A hoistable own-field initializer the ctor MIL slice admits -- a
    `self`-targeted own-field assign whose (field type, source) pair the tail
    emitter reproduces byte-for-byte.

    The `obj.name == "self"` guard is load-bearing -- `_field_receiver_ok` alone
    would also admit `other_record.field = ...`, which is not a member init. The
    own-field test is the same one the ctor driver applies. Routed shapes:

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

      * **Ptr[T]** -- a `None` source (`p(nullptr)`).
      * **pointer-repr union** (F4 U2): the own-param move, `None`
        (`u(std::monostate{})`), a borrow ptr-variant name
        (`to_value_variant`), or a member-record ctor rvalue (`u(A(3))`).
      * **value union** (F4 U1): `None`, a scalar literal, or an eligible name
        -- all bare renders.
      * **tuple**: a borrow pointer-repr tuple param (`tuple_to_storage`); a
        value tuple's same-type name copy or spelled literal.
      * **any Optional** -- a `None` source (`f(std::nullopt)`), inner- and
        repr-independent; and for a POINTER-REPR Optional the own-param move
        plus the borrow-`T*` `ptr_to_optional` lift, likewise inner-agnostic
        (the lift keys on the field's repr, never on the inner).

    Then a tail for families the cascade above claims for no arm: the
    type-agnostic own-param move, an `Any` field's `into_any` coerce, and a
    `bytearray` field's same-typed param copy. Field types beyond all of that
    (BytesView; cross-module / native / generic records outside the move row)
    reject the whole ctor."""
    analyzer = lc.analyzer
    if not (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field in own_field_names
            and _field_receiver_ok(stmt.target, declared, analyzer)):
        return False
    # A bare-name RHS that is not a param is DEMOTED to the ctor body -- a
    # conservative "not in scope at MIL time" that covers read-only-seeded
    # globals too -- so it must not hoist here. Returning False routes it to
    # `_ast_demotes_init` in lower_constructor, which sends it to the body.
    src_peeled = stmt.value
    while isinstance(src_peeled, TpyCoerce):
        src_peeled = src_peeled.expr
    if (isinstance(src_peeled, TpyName)
            and src_peeled.name not in lc.prescan.param_names):
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
        # pointee-independent.
        if (_eligible_ptr_value(ftype, analyzer)
                and isinstance(_unwrap_copy(stmt.value, analyzer),
                               TpyNoneLiteral)):
            return True
        return True
    if isinstance(unwrap_readonly(ftype), NoneType):
        # `self.slot = None` on a NoneType field: `slot(std::monostate{})`.
        # Literal source only -- the corpus has no name-source witness.
        return isinstance(_unwrap_copy(stmt.value, analyzer), TpyNoneLiteral)
    view_t = _resolved_str_value(ftype, analyzer)
    if view_t is None:
        view_t = _resolved_bytes_value(ftype, analyzer)
    if view_t is not None:
        # An `Own[str]` / `Own[bytes]` param consumed at its LAST USE moves
        # into the owned field like any other own-param source -- the tail
        # emitter runs that check ahead of its view arm, so admitting it here
        # is the whole gap. Move sources only: at a NON-last use the
        # family's copy renders (`::tpy::Bytes(name)`), which the
        # view arm below does not spell for an Own-wrapped declaration.
        if _is_move_source(_unwrap_copy(stmt.value, analyzer), lc,
                           own_param_names):
            return True
        return _ctor_viewfam_source_ok(stmt.value, view_t, declared, lc)
    if isinstance(ftype, TypeParamRef):
        # Stage B: a generic record's `T` field. An `Own[T]` param moves; a bare
        # `T` param copies (`first(a)`). The source renders by name only -- a
        # TypeParamRef slot takes no borrow/storage lift -- so the MIL is
        # that bare render. A non-param source
        # (`self.<field>` read, ctor rvalue) rides a later cell.
        source = _unwrap_copy(stmt.value, analyzer)
        if _is_move_source(source, lc, own_param_names):
            return True
        if isinstance(source, TpyName):
            # `Own[T]` at a NON-last use is a warned copy (sema's `may copy
            # ... field`), and copies through the same bare `item(item)` slot
            # as a plain `T` param -- the Own only ever selected the move.
            dt = declared.get(source.name)
            return _is_type_param_slot(dt) or _own_type_param_slot(dt)
        return False
    if _container_storage_field(ftype):
        source = _unwrap_copy(stmt.value, analyzer)
        if isinstance(source, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral,
                               TpyListRepeat)):
            # The MIL is a target-threaded position like a decl init (the field
            # type is the render target), so the shared container-
            # literal classifier applies verbatim. Admitted element rows are all
            # temps_ok=False shapes, so the temps-rollback demote cannot
            # fire on an admitted literal. `[e] * n` joins them: it
            # materializes its own container off the threaded field type,
            # the same value the field-write prvalue row assigns. A
            # comprehension / coerce-wrapped source falls through to the
            # reject.
            return _container_literal_shape_ok(
                source, ftype, analyzer, threaded=True)
        if isinstance(source, (TpyListComprehension, TpySetComprehension,
                               TpyDictComprehension)):
            # A comprehension builds the field's container in place: its
            # `({...})` statement-expression is self-describing and
            # position-independent, so the member-init takes it verbatim.
            # The comprehension's own route owns every shape reject; reads
            # of a field the ctor BODY writes are already demoted by the
            # caller's `expr_reads_self_field` check.
            return True
        if isinstance(source, TpyName):
            # A container param copies bare into the field (`f(p)`); an Own
            # container param at its last use moves (`f(std::move(p))`, the
            # M3b-move arm; the param classifier admits any Own payload). Exact-shape
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
            return _container_storage_field(pt) and pt.to_cpp() == ftype.to_cpp()
        if (isinstance(source, TpyCall) and len(source.args) <= 1
                and not source.kwargs
                and isinstance(source.func, TpyName)
                and source.func.name in ("list", "dict", "set", "Array",
                                         "bytearray")):
            # The container ctor call (`self.items = list()` ->
            # `items(std::vector<T>())`, `self.items = list(src)` ->
            # `items(::tpy::construct<std::vector<T>>(src))`): the
            # target-threaded render spells the FIELD's
            # container type either way, so the element types come from the
            # field, not from the call. `Array()` joins them:
            # `data(std::array<T, N>())`. The single argument rides the
            # ordinary call lowering, which raises on any arg shape it does
            # not support.
            return True
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
        return (_record_rvalue_source_shape(source, analyzer)
                and analyzer.get_expr_type(source) in pu.members)
    if isinstance(ftype, RecursiveAliasInstanceType):
        # A generic-instance wrapper field (`self.t = t` at `t:
        # Own[Tree[Int32]]`): the own-param move rides the type-agnostic
        # M3b-move arm verbatim (`t(std::move(t))`); a container literal
        # takes the ru-instance spelled render (the decl row's
        # `_lower_ru_literal` twin). Other sources ride later cells.
        source = _unwrap_copy(stmt.value, analyzer)
        if _is_move_source(source, lc, own_param_names):
            return True
        return (isinstance(source, (TpyArrayLiteral, TpyDictLiteral))
                and _ru_instance_literal_ok(source, analyzer))
    if (isinstance(ftype, NominalType) and ftype.is_user_record
            and ftype.type_args and not ftype.is_value_type()):
        # A generic-record field whose instantiation `_f1_record` rejects
        # (a genrec / open-T type arg -- `Box[Tree[T]]`): the Own-param
        # move rides the same type-agnostic M3b-move arm
        # (`data(std::move(data))`). Move sources ONLY -- call/literal
        # sources keep their parked cells (the Rc MIL family), and the
        # arm falls through so other generic-record shapes keep their
        # own rows.
        source = _unwrap_copy(stmt.value, analyzer)
        if _is_move_source(source, lc, own_param_names):
            _witness("mil.generic_record_move")
            return True
    vu = _eligible_value_union(ftype)
    if vu is not None:
        # F4 U1: bare renders only -- the variant converting ctor absorbs a
        # same-union param name, a member-typed param name, a scalar literal
        # (`u(u)` / `u(x)` / `u(5)`; lowering retypes a top-level literal to
        # the union so the BigInt/Float32 slot wraps never fire -- the union
        # is the render target, which takes neither), and
        # the monostate `None`. Classification peels sema coerces (a literal
        # source arrives coerce-wrapped); eligibility checks the full expr.
        peeled = _unwrap_copy(stmt.value, analyzer)
        while isinstance(peeled, TpyCoerce):
            peeled = peeled.expr
        if isinstance(peeled, TpyNoneLiteral):
            return True
        return isinstance(peeled, (TpyIntLiteral, TpyFloatLiteral,
                                   TpyBoolLiteral, TpyName))
    ft_tuple = _f1_tuple(ftype, analyzer)
    if ft_tuple is not None:
        # A spelled tuple LITERAL builds the borrow-form brace-init and wraps
        # it in `tuple_to_storage` (per-element admission in
        # `_mil_ptr_tuple_elem_ok`; all-VALUE captures only -- a ref capture
        # takes a slot_info path this cell does not render). Checked
        # on the coerce-peeled RHS, NOT the copy-unwrapped one: a `copy()` of
        # a WHOLE tuple takes the storage-form copy render.
        lit = stmt.value
        while isinstance(lit, TpyCoerce):
            lit = lit.expr
        if isinstance(lit, TpyTupleLiteral):
            if (len(lit.elements) != len(ft_tuple.element_types)
                    or (lit.elem_capture
                        and any(c is not TupleElemCapture.VALUE
                                for c in lit.elem_capture))):
                return False
            return all(
                _mil_ptr_tuple_elem_ok(e, s, declared, lc,
                                       own_params=own_param_names)
                for e, s in zip(lit.elements, ft_tuple.element_types))
        # A storage-form-tuple-returning CALL stores bare
        # (`t(make_pair(5))` -- no tuple_to_storage lift, per the
        # needs_tuple_storage_lift call verdict).
        if (isinstance(stmt.value, (TpyCall, TpyMethodCall))
                and _storage_form_tuple_return(
                    stmt.value.resolved_function_info)):
            return True
        # A whole storage-tuple ELEMENT read stores bare too
        # (`pair(::tpy::__getitem__(items, 0))` -- a subscript is a
        # storage-form source, same needs_tuple_storage_lift verdict).
        if (isinstance(stmt.value, TpySubscript)
                and not isinstance(stmt.value.index, TpySlice)
                and unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    analyzer.get_expr_type(stmt.value)))) == ft_tuple):
            return True
        # A MIXED-own-tuple call stores via the NON-move tuple_to_storage
        # (`t(::tpy::tuple_to_storage<S>(make_mixed(b)))`).
        if _mixed_own_storage_source(stmt.value, ft_tuple, frozenset(),
                                     analyzer) is not None:
            return True
        # F3: a borrow pointer-repr tuple param stores via `tuple_to_storage`
        # (the body field-write arm's MIL sibling). No copy()-unwrap: a
        # `copy()` of a pointer-repr tuple takes the storage-form copy
        # render, which the MIL slice does not spell.
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
            return True
        return False
    nt = _nested_storage_tuple(ftype, analyzer)
    if nt is not None:
        # A NESTED-storage tuple literal stores its bare spelled brace-init
        # (`q(std::tuple<...>{1, ::tpy::tuple_to_storage<S2>(..)})`); the
        # nested members replay the wrap decision per level through the
        # container-literal elem recursion.
        lit = stmt.value
        while isinstance(lit, TpyCoerce):
            lit = lit.expr
        return (isinstance(lit, TpyTupleLiteral)
                and _container_lit_elem_ok(
                    lit, ftype, declared, analyzer, threaded=True,
                    forced=True, allow_record=True, allow_nested=True,
                    allow_optional=True))
    oc_inner = _optional_container_storage_inner(ftype)
    if oc_inner is not None:
        source = _unwrap_copy(stmt.value, analyzer)
        if isinstance(source, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
            # A container literal into an `Optional[container]` field. The
            # field type is threaded and the literal render unwraps the
            # Optional itself, so the literal is classified against the INNER
            # -- element targets derived from the Optional would diverge.
            # Other sources keep the Optional rows below.
            return _container_literal_shape_ok(
                source, oc_inner, analyzer, threaded=True)
    if isinstance(ftype, OptionalType) and isinstance(
            _unwrap_copy(stmt.value, analyzer), TpyNoneLiteral):
        # `self.f = None` renders `f(std::nullopt)` for EVERY Optional field
        # (pointer-repr or value-repr) -- inner-independent, so the F1-record
        # inner classifier below does not apply.
        return True
    if isinstance(ftype, OptionalType) and not ftype.uses_pointer_repr():
        # A value-repr Optional field (`std::optional<T>`) from an optional param
        # name. Scalar inner: the bare same-typed copy (`f(value)`) -- param slot
        # and field storage spell the same std::optional<T>. View inner
        # (str/bytes): the arg-split shim (`_opt_view_arg_shim`) -- the param's
        # borrow `optional<view>` -> the field's owned `optional<owned>`
        # (`f(v ? std::make_optional(<conv>(*v)) : std::nullopt)`).
        source = _unwrap_copy(stmt.value, analyzer)
        if (_value_opt_scalar(ftype, analyzer) is not None
                and isinstance(_peel_coerce(source),
                               (TpyIntLiteral, TpyFloatLiteral,
                                TpyBoolLiteral))):
            # `self.slot = 1` on `int | None` -> `slot(1)`: the converting
            # ctor absorbs the bare (target-retyped) literal.
            return True
        if _str_literal_value_opt_arg(_peel_coerce(source), ftype):
            # `self.s = "xy"` on `str | None` -> `s("xy")`: the bare
            # literal renders at a value-repr Optional[str] slot.
            # str ONLY -- `_value_opt_view` also covers bytes, whose owned
            # field needs the view->owned copy this bare render omits.
            return True
        if not isinstance(source, TpyName):
            return False
        dt = declared.get(source.name)
        if dt is None:
            return False
        dt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
        if _value_opt_scalar(ftype, analyzer) is not None:
            # Either the same `std::optional<T>` (a plain copy) or the bare
            # INNER scalar, which `std::optional<T>`'s converting ctor absorbs
            # -- both render bare (`value(value)`), no wrap either
            # way. The inner is a cheap scalar by `_value_opt_scalar`'s own
            # verdict, so matching it is enough to stay in the routed family.
            return dt == ftype or dt == unwrap_readonly(
                unwrap_ref_type(unwrap_send_sync(ftype.inner)))
        if _value_opt_view(ftype, analyzer) is not None:
            return _opt_view_arg_shim(dt, ftype, analyzer)
        if _value_tuple(unwrap_readonly(ftype.inner), analyzer) is not None:
            # A value-TUPLE inner copies bare from the same-typed param
            # (`tup(tup)`): borrow and storage coincide for a value tuple, so
            # the param slot and the field spell the same
            # `std::optional<std::tuple<...>>` and no lift renders.
            return dt == ftype
        return False
    if _span_value(ftype):
        # A `std::span<T>` field copies bare from a same-typed span param
        # name (`items(items)`) -- a view, so borrow and storage coincide and
        # no lift renders. Other sources (array locals taking the implicit
        # span conversion, slices) keep their own renders.
        source = _unwrap_copy(stmt.value, analyzer)
        if not isinstance(source, TpyName):
            return False
        dt = declared.get(source.name)
        if dt is None:
            return False
        dt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
        return _span_value(dt) and dt == ftype
    ft_bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ftype)))
    if _callable_value(ft_bare):
        # A `std::function<...>` field copies bare from a same-typed callable
        # param name (`on_event(cb)`) or a routable LAMBDA literal
        # (`self.action = lambda: print(0)` -> `action([]() { ... })` -- the
        # closure converts implicitly in the member direct-init). A
        # self-capturing lambda stays out (self_this defaults False: the MIL
        # never confirmed the `this` receiver spelling). The
        # `Send[...]` wrapper is erased in storage form on BOTH sides, so it
        # is peeled off the field type as well as the param's -- comparing a
        # peeled param against an unpeeled field would reject a pair that
        # spells identically (`std::function<void(int32_t)> cb : cb(cb)`).
        source = _unwrap_copy(stmt.value, analyzer)
        if isinstance(source, TpyLambda):
            return _lambda_routable(source, analyzer)
        if not isinstance(source, TpyName):
            return False
        dt = declared.get(source.name)
        if dt is None:
            return False
        dt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(dt)))
        return _callable_value(dt) and dt == ft_bare
    if isinstance(ftype, OptionalType) and ftype.uses_pointer_repr():
        # The move and the `ptr_to_optional` lift are both INNER-AGNOSTIC: the
        # lift keys on the field's pointer repr alone, never on
        # what is inside, so a record / container / bytearray / type-param
        # inner all take the same wrap. An OWN param -- the same storage
        # optional by rvalue-ref -- MOVES bare (`tag(std::move(tag))`, the
        # type-agnostic M3b-move arm). Record inners additionally take the
        # `None` and record-value sources below; literals keep their own
        # renders unwitnessed.
        source = _unwrap_copy(stmt.value, analyzer)
        if (_is_move_source(source, lc, own_param_names)
                or _is_borrow_ptr_local(source, declared, set())):
            return True
    is_opt = (isinstance(ftype, OptionalType) and ftype.uses_pointer_repr()
              and _f1_record(ftype.inner, analyzer))
    if not (is_opt or _f1_record(ftype, analyzer)):
        # Tail rows for field families no arm of the cascade above claims.
        # Each rides a render the tail emitter already spells type-agnostically,
        # so admission is the only site that distinguishes them -- hence the
        # classifier-row faces here rather than at the shared render.
        tail_src = _unwrap_copy(stmt.value, analyzer)
        if _is_move_source(tail_src, lc, own_param_names):
            # The M3b-move arm is type-agnostic (`p(std::move(p))`); the
            # cascade simply never reaches it for a tuple of type params or a
            # cross-module recursive-alias union. Placed BELOW the cascade so
            # a family with its own copy row keeps deciding for itself.
            _witness("mil.unclaimed_family_move")
            return True
        if (isinstance(ftype, AnyType) and isinstance(tail_src, TpyCoerce)
                and tail_src.coercion.name == "into_any"):
            # An `Any` field from sema's into_any coerce: the coercion node
            # carries its own `::tpy::make_any(...)` render, so the tail
            # lowers the source bare.
            _witness("mil.any_coerce")
            return True
        return False
    source = _unwrap_copy(stmt.value, analyzer)
    # M3b-move: an own-param at its last use moves into the field.
    if _is_move_source(source, lc, own_param_names):
        return True
    if not is_opt and isinstance(stmt.value, TpyIfExpr):
        # A record TERNARY of prvalue arms direct-initializes the field
        # (`_ctx((c != nullptr) ? Ctx((*c)) : make_ctx())`); the arms gate
        # themselves at lowering, so admission here only claims the slot.
        return True
    if is_opt:
        # None / a non-own borrow `T*` (pointer-repr Optional param, lifts via
        # ptr_to_optional) / a record-value source (constructs the optional directly).
        # pointers empty: a ctor MIL has no locals.
        return (isinstance(source, TpyNoneLiteral)
                or _is_borrow_ptr_local(source, declared, set())
                or _is_record_value_source(source, declared, own_param_names,
                                           lc, ftype))
    return _is_record_value_source(source, declared, own_param_names, lc,
                                   ftype)

def lower_constructor(record, init_method: TpyFunction, analyzer,
                      render_type=None,
                      self_type: 'TpyType | None' = None,
                      native_globals: 'Mapping[str, str]' = {},
                      render_type_stored=None,
                      render_resolve=None,
                      render_concept=None,
                      ) -> THIRConstructor | None:
    """Lower a constructor to a THIRConstructor, or None if outside the slice.

    Same-module non-generic record, flat or with same-module F1 base(s) (M3d: each
    `super().__init__` / `BaseN.__init__` call lowers to a base initializer, sorted by
    parent declaration order; a direct inherited-field write goes to the body). The
    leading run of hoistable own-field
    initializers (the M3a/M3b field-source slice) goes to the member-init-list; the rest
    of the body -- docstring / `pass` trivia (M3c-trivia), non-init statements, and field
    inits that cannot hoist or follow a chain break (M3c-demotion) -- lowers through the
    shared statement machinery (`_lower_stmt`), the same path method
    bodies use. The ctor routes only when every non-trivia body statement is in the slice;
    otherwise it rejects. The signature belongs to the printer layer
    (the M1 method precedent); only the MIL + body tail lowers here."""
    if self_type is None or not _f1_record(self_type, analyzer):
        note("ctor.non_f1_record")
        return None
    # M3d: same-module F1 base(s) route -- each `super().__init__` / `BaseN.__init__`
    # call lowers to a base initializer (sorted by parent declaration order), and a
    # direct inherited-field write goes to the body. A non-F1 base (cross-module /
    # generic / native -- its `to_cpp()` would not match) rejects the whole
    # ctor. Reject overloaded / native / generator / generic __init__ -- those take
    # emit paths the tail emitter does not reproduce.
    ri = analyzer.registry.get_record(record.name)
    if ri is None:
        note("ctor.unregistered")
        return None
    if any(not _f1_record(p, analyzer) for p in ri.parents):
        # A builtin-CONTAINER base (`class MyList(list[Int32])`) is not F1 --
        # its `to_cpp()` is the formatter spelling -- but it only ever reaches
        # the emitted ctor through a base initializer, and the struct header
        # naming it belongs to the printer layer. With no base-init call in the
        # body there is nothing for the MIL to spell, so the tail emits
        # identically. `super().__init__([1, 2, 3])` DOES reach one (the
        # element-seeding spelling compiles and runs), so the base-init reject
        # is load-bearing, not defensive: the MIL would have to spell the
        # container base's own initializer, which the tail cannot render.
        if (any(not _builtin_container_type(p, analyzer)
                and not _f1_record(p, analyzer) for p in ri.parents)
                or any(is_base_init_call(s) for s in init_method.body)):
            note("ctor.non_f1_base")
            return None
    if (init_method.is_overload_stub or init_method.native_function
            or init_method.is_async or init_method.is_generator
            or init_method.type_params):
        note("ctor.special_init")
        return None
    # A reassigned param needing the owned-copy prologue (String/BigInt/owned
    # bytes...) rejects (`ctor.param_reassign_copy`, the ctor sibling of
    # sig.param_reassign_copy): the prologue local `T name = __param_name;`
    # has no matching `__param_` rename in the ctor signature -- a
    # pre-existing defect the tail does not reproduce.
    scan = analyzer.function_scan_results.get(init_method)
    if scan is not None and scan.reassigned:
        for pname, ptype in init_method.params:
            pt = ptype if isinstance(ptype, TpyType) else None
            if (pname in scan.reassigned and pt is not None
                    and pt.param_needs_copy_for_reassign()):
                note("ctor.param_reassign_copy")
                return None
    # A MUTATED `String` param would emit the mutation against the untouched
    # `const std::string&` param (ill-formed C++, see BUGS.md), so the whole
    # shape rejects (`ctor.param_mutated_string`).
    # TpyCall lowering rejects the same mutated-String slots using these
    # synthetic-constructor mutation facts.
    init_fis = ri.get_method_overloads("__init__")
    mut = init_fis[-1].mutated_params if init_fis else None
    if mut:
        for i, (_n, ptype) in enumerate(init_method.params):
            if i in mut and isinstance(ptype, TpyType) and _is_string_owned(ptype):
                note("ctor.param_mutated_string")
                return None
    # Own[T] / Own[T]|None params: their MIL sources move (M3b-move), so M3b-copy
    # rejects them as record-field sources.
    own_param_names = {pname for pname, ptype in init_method.params
                       if isinstance(ptype, TpyType)
                       and unwrap_optional_own(unwrap_readonly(
                           unwrap_send_sync(ptype))) is not None}
    declared: dict[str, TpyType] = {n: t for n, t in init_method.params}
    declared["self"] = self_type
    own_field_names = {f.name for f in record.fields}
    # lc is built before the lowering loop: the move check (`_is_move_source`) reads
    # `analyzer.ctx.all_last_uses` through it.
    lc = _LowerCtx(init_method, analyzer, render_type, self_receiver="self",
                   record_name=record.name,
                   render_type_stored=render_type_stored,
                   render_resolve=render_resolve,
                   render_concept=render_concept)
    # Global seeding, like lower_function's: read-only value globals plus
    # `global`-declared write names (the global-write render is
    # function-kind-blind, so a ctor's `g = v;` renders exactly like a
    # sync function's).
    _seed_global_scope(init_method, analyzer, lc, declared, native_globals)
    # Every lowering call sits inside this boundary: expression admission can
    # raise, and a raise outside here would escape this reject boundary.
    try:
        # Base initializers (`super().__init__` / `BaseN.__init__`), sorted by parent
        # declaration order (M3d); None if any is outside the slice -> reject.
        base_inits = _lower_base_inits(init_method, ri, declared, lc)
        if base_inits is None:
            note("ctor.base_init")
            return None
        field_inits: list[THIRMilInit] = []
        mil_done_fields: set[str] = set()  # own fields already hoisted
        body_done_fields: set[str] = set()  # own fields whose init went to the body
        # The EMITTED member order, which decides the order the member inits
        # run in -- not the `__init__` assignment order they are written in.
        # The two agree only where `reorder_fields_by_init` applied.
        field_index = {f.name: i for i, f in enumerate(record.fields)}
        # An own field never assigned at the top level of `__init__` keeps its
        # class-level default. That is an NSDMI, which the ctor body sees in
        # place, so a source reading one demotes instead of rejecting. A field
        # with BOTH a default and a ctor assign takes the assign: its NSDMI
        # does not run, so it is not in this set.
        ctor_assigned_fields = {s.target.field for s in init_method.body
                                if _is_self_own_field_assign(s, own_field_names)}
        nsdmi_only_fields = {
            f.name for f in record.fields
            if f.name not in ctor_assigned_fields
            and (f.default_value is not None or f.default_expr is not None)}
        body_stmts: list[TpyStmt] = []  # demoted inits + non-init stmts + trivia, source order
        body_written_self_fields: set[str] = set()
        # The demote triggers: a nested-def-name / bare non-param-name
        # source, or any body-local reference in the RHS.
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
            # ineligibility (`_field_receiver_ok`). The tracking set matters only on a
            # live chain -- once the chain is broken every later own-field init
            # demotes regardless, so the set is never consulted.
            if _is_self_nonown_field_assign(stmt, own_field_names):
                body_written_self_fields.add(stmt.target.field)
                body_stmts.append(stmt)
                continue
            is_own_init = _is_self_own_field_assign(stmt, own_field_names)
            ast_demotes = is_own_init and _ast_demotes_init(
                stmt, lc.prescan.param_names, nested_def_names, body_local_names)
            # A second assignment to an already-hoisted field is a
            # re-assignment, not a member init: the list holds one entry per
            # member, so this one runs in the body over the value it set.
            reassigns_hoisted = (is_own_init
                                 and stmt.target.field in mil_done_fields)
            # The demote triggers, decided BEFORE the ordering check: they say
            # where the init runs, and the two destinations do not have the
            # same fields in place. A read of an inherited field the body
            # writes, or of a field with only a class-level default, is exactly
            # a source the body can serve and the list cannot.
            reads_inherited = (
                is_own_init
                and expr_reads_self_field(stmt.value, body_written_self_fields))
            reads_default_only = (
                is_own_init and not reads_inherited
                and _reads_self_fields(stmt.value, nsdmi_only_fields))
            reads_written_field = reads_inherited or reads_default_only
            demotes = (chain_broken or ast_demotes or reassigns_hoisted
                       or reads_written_field)
            # Which own fields hold a value where this init will actually run.
            # In the member init list that is the fields laid out BEFORE the
            # target (C++ runs member inits in declaration order) that the
            # chain has also already hoisted -- source order and layout order
            # can disagree, since the assignment-order reorder does not reach a
            # record with bases. In the body it is every field the list set,
            # every class-level default (an NSDMI runs before the body), and
            # every body init already emitted.
            unready: set[str] = set()
            if is_own_init:
                if demotes:
                    readable = (mil_done_fields | nsdmi_only_fields
                                | body_done_fields)
                else:
                    target_idx = field_index[stmt.target.field]
                    readable = {f for f in mil_done_fields
                                if field_index[f] < target_idx}
                unready = own_field_names - readable
            if is_own_init and _reads_self_fields(stmt.value, unready):
                raise ThirUnsupported("ctor.mil_reads_unready_field", loc=stmt.loc)
            if (not chain_broken and is_own_init and not reassigns_hoisted
                    and not reads_written_field and not ast_demotes):
                mil_node = _attempt_ctor_mil_init(
                    stmt, own_param_names, own_field_names, declared, lc)
                if mil_node is not None:
                    field_inits.append(mil_node)
                    mil_done_fields.add(stmt.target.field)
                    continue
                # DYNAMIC demote (the probe-registers-a-temp trigger,
                # e.g. a varargs std::array in the init): the init goes to
                # the body like the static demotes below.
                _reject_nondef_ctor_field(stmt, analyzer,
                                          emit_prims.CTOR_DEMOTE_NEEDS_TEMP)
                _witness("mil.demote_probe")
                chain_broken = True
                body_done_fields.add(stmt.target.field)
                body_stmts.append(stmt)
                continue
            if is_own_init:
                # A re-assignment needs no default-construction check: the
                # member initializer list already gave the field its value.
                if not reassigns_hoisted:
                    _reject_nondef_ctor_field(
                        stmt, analyzer,
                        _ctor_demote_reason(stmt, chain_broken, nested_def_names,
                                            lc.prescan.param_names,
                                            body_local_names,
                                            reads_default_only))
                if not chain_broken and ast_demotes:
                    _witness("mil.demote_mirror")
                body_done_fields.add(stmt.target.field)
            # Demote to the body. Demoting breaks the chain: the MIL runs
            # before the body, so a later otherwise-hoistable init
            # must also demote to preserve source evaluation order.
            chain_broken = True
            body_stmts.append(stmt)
        body_declared = dict(declared)
        ctor = THIRConstructor(
            record_name=record.name,
            params=tuple(THIRParam(name=n, type=t) for n, t in init_method.params),
            mil_inits=tuple(field_inits),
            base_inits=tuple(base_inits),
            body=_lower_stmts(body_stmts, lc, body_declared),
        )
        if _rejects_lambda_hoist(ctor.body):
            raise ThirUnsupported("nested_def.rebind_slot_hoist")
        validate_constructor(ctor)
        return ctor
    except ThirUnsupported as ex:
        note(ex.reason, ex.loc)
        return None

_MIL_DEMOTE_TAGS = frozenset({
    # The probe-temp class ONLY: a reject at an init that belongs in the MIL
    # must fail the whole ctor -- demoting one would emit body code where a
    # MIL entry belongs. Extend this whitelist one witnessed shape at a time.
    "call.vararg_pack_flush",
})


def _attempt_ctor_mil_init(stmt, own_param_names, own_field_names,
                           declared: dict, lc) -> 'THIRExpr | None':
    """Try one MIL field-init lowering; None = dynamically demote (the
    whitelisted probe-temp rejects only -- every other ThirUnsupported
    re-raises and rejects the whole ctor). The attempt's side effects
    roll back locally: `declared` by copy, the branch-scoped lc name-sets
    via branch_scope, the FUNCTION-scoped walrus sets by explicit copy
    (branch_scope deliberately skips them, and a walrus ARG can lower
    before the whitelisted reject fires -- `f(y := g(), *rest)`), and the
    faces / move-verdict journals by delta subtraction (the flat
    one-window journal design forbids a nested begin/rollback here)."""
    from ...compilation_context import get_current_compiler
    compiler = get_current_compiler()
    decl_snap = dict(declared)
    walrus_pre_snap = set(lc.walrus_predeclared)
    walrus_slot_snap = set(lc.walrus_slot_locals)
    fw_snap = (dict(compiler._thir_face_witnesses)
               if compiler is not None else None)
    fj = getattr(compiler, "_thir_face_journal", None)
    fj_snap = dict(fj) if fj is not None else None
    mj = getattr(compiler, "_move_verdict_journal", None)
    mj_snap = set(mj) if mj is not None else None
    try:
        with lc.branch_scope():
            return _lower_ctor_mil_init(
                stmt, own_param_names, own_field_names, declared, lc)
    except ThirUnsupported as ex:
        if ex.reason not in _MIL_DEMOTE_TAGS:
            # A member-init never reaches the statement chokepoint, so this is
            # the innermost frame that knows which source line rejected.
            if ex.loc is None:
                ex.loc = getattr(stmt, "loc", None)
            raise
        declared.clear()
        declared.update(decl_snap)
        lc.walrus_predeclared.clear()
        lc.walrus_predeclared.update(walrus_pre_snap)
        lc.walrus_slot_locals.clear()
        lc.walrus_slot_locals.update(walrus_slot_snap)
        if compiler is not None:
            compiler._thir_face_witnesses = fw_snap
            if fj is not None:
                compiler._thir_face_journal = fj_snap
            if mj is not None:
                for key in mj - mj_snap:
                    compiler._move_verdict_thir.pop(key, None)
                compiler._move_verdict_journal = mj_snap
        return None


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
    """Whether this own-field init demotes to the ctor body regardless
    of the chain state -- the source triggers: a nested-def
    name, a bare name that is not a param (not in scope at MIL time; covers
    module globals and `self`), or any body-local reference in the RHS.
    Rejecting the ctor here would be safe but needlessly conservative;
    hoisting one would spell a name not yet in scope. The temps trigger
    (`temps.rollback` -> demote) is NOT handled here: lowering rejects
    temp-registering sources instead."""
    src = stmt.value
    while isinstance(src, TpyCoerce):
        src = src.expr
    if isinstance(src, TpyName) and (src.name in nested_def_names
                                     or src.name not in param_names):
        return True
    return bool(body_local_names
                and (collect_name_refs(stmt.value) & body_local_names))


def _ctor_demote_reason(stmt: TpyAssign, chain_broken: bool,
                        nested_def_names: set[str], param_names: set[str],
                        body_local_names: set[str],
                        reads_default_only: bool = False) -> str:
    """Which demote trigger fired for this own-field init, in the order the
    triggers are tested.

    The trigger is part of the user-visible sentence, so a later one standing in
    for an earlier one is a wrong message, not a differently-worded right one.
    Reached only for an init that IS demoted, so the last arm needs no test of
    its own -- nothing else can have sent it here."""
    if chain_broken:
        return emit_prims.CTOR_DEMOTE_PRIOR_STATEMENT
    src = stmt.value
    while isinstance(src, TpyCoerce):
        src = src.expr
    if isinstance(src, TpyName):
        if src.name in nested_def_names:
            return emit_prims.CTOR_DEMOTE_NESTED_DEF
        if src.name not in param_names:
            return emit_prims.CTOR_DEMOTE_BODY_LOCAL
    if body_local_names and (collect_name_refs(stmt.value) & body_local_names):
        return emit_prims.CTOR_DEMOTE_BODY_LOCAL
    if reads_default_only:
        return emit_prims.CTOR_DEMOTE_READS_DEFAULT_ONLY
    return emit_prims.CTOR_DEMOTE_READS_INHERITED


def _reject_nondef_ctor_field(stmt: TpyAssign, analyzer, reason: str) -> None:
    """Raise when a DEMOTED own-field init targets a field whose type has a
    suppressed default constructor.

    Such a field has no default state, so leaving it out of the member
    initializer list is not an option the emitters have. Own-field-ness is the
    caller's guard -- an inherited field of the same type belongs to the base
    constructor's list and is not this diagnostic."""
    ftype = analyzer.get_expr_type(stmt.target)
    if not _nondef_ctor_field(ftype, analyzer):
        return
    emit_prims.reject_nondef_ctor_field_in_body(
        stmt.target.field, analyzer.registry.get_record_for_type(ftype).name,
        reason, stmt.loc)


def _reads_self_fields(expr: TpyExpr, fields: set[str]) -> bool:
    """True if `expr` reads `self.X` for any X in `fields`.

    The field-read half of `expr_reads_self_field`, without its
    treat-any-self-method-call-as-a-read rule: sema already warns on a
    method call in the init section with fields still uninitialized, and
    escalating that warned shape to a lowering reject is a separate call.
    """
    if not fields:
        return False
    stack: list[TpyExpr] = [expr]
    while stack:
        node = stack.pop()
        if (isinstance(node, TpyFieldAccess)
                and isinstance(node.obj, TpyName) and node.obj.name == "self"
                and node.field in fields):
            return True
        stack.extend(node.children())
    return False


def _is_self_own_field_assign(stmt: TpyStmt, own_field_names: set[str]) -> bool:
    """A `self.<own field> = expr` -- a member initializer that belongs in the
    MIL. Either it hoists there or the whole ctor rejects; demoting it
    into the body (when the MIL slice can't render its field type / source)
    is not an alternative."""
    return (isinstance(stmt, TpyAssign)
            and isinstance(stmt.target, TpyFieldAccess)
            and isinstance(stmt.target.obj, TpyName)
            and stmt.target.obj.name == "self"
            and stmt.target.field in own_field_names)

def _lower_base_inits(init_method: TpyFunction, ri, declared: dict[str, TpyType],
                      lc: _LowerCtx) -> 'list[THIRBaseInit] | None':
    """Lower every `super().__init__` / `BaseN.__init__`
    call to a THIRBaseInit, sorted by parent declaration order (so a multi-base list
    emits in the order C++ runs the base ctors, avoiding -Wreorder). None if any base
    init is outside the slice -- the whole ctor then rejects."""
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
    """One base-init arg the tail emitter can render. Every arg renders
    TARGET-LESS -- no retype, no deref, no auto-move --
    so the admitted rows are exactly the shapes whose bare render is right:

      * an eligible-scalar value expression (the M3d-1 row);
      * a str literal (`"lit"`) / a `None` literal, the latter only where the
        slot renders it `nullptr` -- a value-form optional/union slot spells it
        `std::nullopt` / `{}`, which a target-less render cannot reproduce, so
        the caller rejects that pairing;
      * an int literal still typed `IntLiteralType` (a BigInt base slot:
        the target-less render is the bare digits), pinned to the +-2^31-1
        literal range like the sibling literal checks;
      * a declared PARAM name of a str-family / F1-record / pointer-repr
        Optional[F1-record] type, incl. `Own[...]` params -- all render as
        the bare name. NB an `Own` param arg renders bare (a COPY into the
        base slot, no `std::move`) -- a known gap, not fixed here.

    Anything that could register a codegen temp is out -- a base-init cell
    has no flush point (same contract as the MIL)."""
    analyzer = lc.analyzer
    # An identity str-family coerce renders its inner bare in EVERY position
    # (`super().__init__(message)` on a `String` param into a `str` base
    # slot: both sides spell std::string), so the shape rows key the inner.
    while (isinstance(a, TpyCoerce)
           and a.coercion.name in _IDENTITY_STR_COERCIONS):
        a = a.expr
    at = analyzer.get_expr_type(a)
    if _eligible_scalar(at):
        return True
    if isinstance(a, (TpyStrLiteral, TpyNoneLiteral)):
        return True
    if (isinstance(a, TpyCall) and not a.kwargs
            and a.double_star_unpack is None
            and _f1_record(at, analyzer)
            and _ctor_shape_ok(a, analyzer)
            and all(
                isinstance(_peel_coerce(sub),
                           (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
                            TpyStrLiteral))
                or (isinstance(sub, TpyName)
                    and _eligible_scalar(analyzer.get_expr_type(sub)))
                for sub in a.args)):
        # A materialized-default CTOR rvalue (`: Base(a, Fixed(5), 2)`):
        # the target-less render is the bare prvalue; scalar-literal/name
        # ctor args cannot register a temp, keeping the no-flush contract.
        return True
    if isinstance(a, TpyIntLiteral):
        return (isinstance(at, IntLiteralType)
                and -(2**31 - 1) <= a.value <= 2**31 - 1)
    # A walrus inside the argument would need a flush point for its
    # binding, and the cell has none: the part order the format call
    # evaluates in is unspecified, so the write and a later read race.
    if (isinstance(a, (TpyBinOp, TpyFString))
            and contains_named_expr(a)):
        return False
    if (isinstance(a, TpyBinOp) and a.op == "+"
            and _resolved_str_value(at, analyzer) is not None):
        # A str CONCAT (`super().__init__("tag" + str(n))` ->
        # `::tpy::Exception((::tpy::str_concat("tag", ...)))`): str_concat is
        # a pure expression, so the target-less bare render holds -- provided
        # each operand is itself a temp-free row, which keeps the cell's
        # no-flush contract.
        return (_base_init_str_operand_ok(a.left, declared, lc)
                and _base_init_str_operand_ok(a.right, declared, lc))
    if ((_conv := _base_init_str_conversion(a)) is not None
            and _resolved_str_value(at, analyzer) is not None):
        # A bare conversion (`super().__init__(str(n))`): the same pure
        # render the concat admits as an operand, standing alone.
        return _base_init_arg_ok(_conv, declared, lc)
    if isinstance(a, TpyFString):
        # An f-string (`super().__init__(f"tag{n}")` ->
        # `::tpy::Exception(std::format("tag{}", n))`): std::format is a pure
        # expression like str_concat, so the same operand rule applies to
        # each interpolated value. A conversion or format spec is excluded
        # -- those spell their own render, which this row has not read.
        for part in a.parts:
            if isinstance(part, str):
                continue
            if part.conversion != -1 or part.format_spec is not None:
                return False
            if not _base_init_str_operand_ok(part.expr, declared, lc):
                return False
        return True
    if not (isinstance(a, TpyName) and a.name in declared
            and a.name != "self"):
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[a.name])))
    if _resolved_str_value(vt, analyzer) is not None:
        return True
    # The remaining bare-name param families. Each binds a `const T&` (never a
    # pointer-local), so the target-less render is
    # the bare name and nothing here can register a temp:
    #   * a builtin CONTAINER (`: Parent(store)`),
    #   * an open type param inside a generic record (`: Base<T>(val)`),
    #   * an owned `String` (`: ::tpy::OSError(message)`) -- excluded from
    #     `_resolved_str_value`, which is the view-family predicate.
    if (_f1_container_ref(vt)
            or _is_type_param_slot(vt) or _is_string_owned(vt)):
        return True
    # A value-repr Optional param (`note: str | None` ->
    # `std::optional<std::string_view>`, or an Optional scalar): the whole
    # optional passes bare into the matching base slot (`: Tagged(tag,
    # note)`); the lowering reads it allow_whole_optional.
    if (_value_opt_scalar(vt, analyzer) is not None
            or _value_opt_view(vt, analyzer) is not None):
        return True
    if _optional_ptr_borrow_name(a, declared, analyzer) is not None:
        return True
    own = unwrap_optional_own(vt)
    if own is not None:
        vt = own.wrapped
        if isinstance(vt, OptionalType):
            vt = vt.inner
    return _f1_record(vt, analyzer)

def _base_init_str_conversion(a: TpyExpr) -> 'TpyExpr | None':
    """The subject of a `str(x)` / `repr(x)` conversion call, or None. The
    render is a pure expression (`::tpy::fixed_to_str<int32_t>(n)`,
    `(n).to_string()`, `::tpy::repr_of(n)`), so it registers no temp and the
    cell's no-flush contract survives it."""
    if (isinstance(a, TpyCall) and isinstance(a.func, TpyName)
            and a.func.name in ("str", "repr") and len(a.args) == 1
            and not a.kwargs and a.double_star_unpack is None):
        return a.args[0]
    return None


def _base_init_str_operand_ok(a: TpyExpr, declared: dict[str, TpyType],
                              lc: _LowerCtx) -> bool:
    """One operand of an admitted base-init str concat or f-string: any row
    `_base_init_arg_ok` admits, plus a `str(x)` / `repr(x)` conversion over
    one."""
    subject = _base_init_str_conversion(a)
    if subject is not None:
        return _base_init_arg_ok(subject, declared, lc)
    return _base_init_arg_ok(a, declared, lc)


def _lower_base_init_arg(a: TpyExpr, lc: _LowerCtx,
                         declared: dict[str, TpyType],
                         none_cpp: 'str | None' = None) -> THIRExpr:
    """Lower one admitted base-init arg. A bare `None` has no generic
    `_lower_expr` arm (its render is always slot-derived elsewhere), so it
    lowers here to the None literal carrying the SLOT's spelling
    (`none_default_cpp_spelling` -- `{}` for a variant slot, `std::nullopt`
    for a value optional, the bare `nullptr` default otherwise -- the
    slot-aware default render); everything else takes `_lower_expr`'s
    name/literal arms."""
    if isinstance(a, TpyNoneLiteral):
        return THIRLiteral(result_type=lc.analyzer.get_expr_type(a),
                           value=None, loc=getattr(a, "loc", None),
                           none_cpp=none_cpp)
    # A value-opt param passes WHOLE into the base slot (bare name).
    return _lower_expr(a, lc, declared, allow_whole_optional=True)

def _lower_base_init(stmt: TpyStmt, declared: dict[str, TpyType],
                     lc: _LowerCtx) -> 'tuple[THIRBaseInit, TpyType] | None':
    """Lower one base-init call to `(THIRBaseInit, parent_type)`, or None outside the
    slice (the caller reuses `parent_type` for the parent-order rank). Renders
    `{parent_type.to_cpp()}({args})` for both the
    `super().__init__(args)` and the explicit `BaseN.__init__(self, args)` forms (sema
    strips `self` from the latter's args). The base must be F1 (so `to_cpp()`
    spells the struct) and every arg in `_base_init_arg_ok`'s target-less bare-render
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
    args: list[THIRExpr] = []
    base_info = analyzer.registry.get_record_for_type(parent_type)
    base_params = base_info.init_params if base_info else []
    for i, a in enumerate(expr.args):
        if not _base_init_arg_ok(a, declared, lc):
            return None
        # A `None` arg needs its SLOT's spelling (`{}` for a variant slot,
        # `std::nullopt` for a value optional, bare `nullptr` otherwise) --
        # rendered by `default_to_cpp` off the base's own param type.
        none_cpp = None
        if isinstance(a, TpyNoneLiteral) and i < len(base_params):
            none_cpp = default_to_cpp_from_analyzer(analyzer, a,
                                                    base_params[i][1])
            _witness("baseinit.none_slot_spelling")
        if not _eligible_scalar(analyzer.get_expr_type(a)):
            _witness("baseinit.nonscalar_arg")
        args.append(_lower_base_init_arg(a, lc, declared, none_cpp=none_cpp))
    return (THIRBaseInit(base_cpp=parent_type.to_cpp(),
                         args=tuple(args)),
            parent_type)

def _lower_ctor_mil_init(
        stmt: TpyAssign, own_param_names: set[str], own_field_names: set[str],
        declared: dict[str, TpyType], lc: _LowerCtx) -> THIRMilInit:
    """Lower one hoisted field initializer into a member-init-list entry.

    The record/Optional arms:

      * an **own-param at last use** moves (`move=True`, plain source -- never
        `ptr_to_optional`, per the cascade) [M3b-move];
      * a **scalar / Char** -> the lowered value [M3a];
      * a pointer-repr **Optional[F1-record]** -> a STORAGE `None` literal
        (`std::nullopt`), a non-own borrow `T*` lifted via `ptr_to_optional` [M3b-copy],
        or a record-value source (ctor-call / field-read / param copy) that constructs
        the optional directly [M3b-rvalue];
      * a plain **F1-record** -> the `copy()`-unwrapped record-value source [M3b-copy/-rvalue];
      * an owned **bytes** field -> a view (span) source copies via the S6
        STORAGE convert (`::tpy::Bytes(...)`, or the
        `bytesview_to_bytes` coerce lambda);
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
    if not _ctor_field_init_ok(
            stmt, own_field_names, own_param_names, declared, lc):
        raise ThirUnsupported(_mil_reject_detail(stmt, analyzer))
    ftype = analyzer.get_expr_type(stmt.target)
    loc = getattr(stmt, "loc", None)
    field_cpp = escape_cpp_name(stmt.target.field)
    source = _unwrap_copy(stmt.value, analyzer)
    if (_container_storage_field(ftype) and isinstance(source, TpyCall)
            and not source.args and not source.kwargs):
        # `self.items = list()` -> `items(std::vector<T>())`: the FIELD type
        # is the render target, so the spelling comes from the field
        # and the call default-constructs it. Spelled here rather than lowered
        # as a call -- the callee is a builtin type name, not a function.
        _witness("mil.container_default")
        return THIRMilInit(
            field_cpp=field_cpp,
            value=THIRCall(result_type=ftype, callee=source.func.name,
                           args=(), cpp_template=f"{lc.render_type(ftype)}()",
                           loc=loc))
    if (_container_storage_field(ftype)
            and isinstance(source, (TpyListComprehension, TpySetComprehension,
                                    TpyDictComprehension))):
        # The comprehension's stmt-expr goes straight into the member-init
        # (`buffer(({ std::vector<V3> __result; ...; std::move(__result); }))`)
        # -- the same render the decl-init position emits. Late import:
        # comprehensions imports this module.
        from .comprehensions import _lower_comprehension
        _witness("mil.container_comp")
        return THIRMilInit(
            field_cpp=field_cpp,
            value=_lower_comprehension(source, ftype, lc, declared,
                                       lc.pointers))
    if _container_storage_field(ftype):
        if isinstance(source, TpyListRepeat):
            _witness("mil.container_repeat")
        else:
            _witness("mil.container_literal"
                     if isinstance(source, (TpyArrayLiteral, TpyDictLiteral,
                                            TpySetLiteral))
                     else "mil.container_name")
    if _is_move_source(source, lc, own_param_names):
        return THIRMilInit(
            field_cpp=field_cpp,
            value=_lower_expr(
                source, lc, declared, allow_unrouted_name=True),
            move=True)
    if (isinstance(ftype, RecursiveAliasInstanceType)
            and isinstance(source, (TpyArrayLiteral, TpyDictLiteral))):
        # A container literal into a generic-instance wrapper field: the
        # ru-instance spelled render, target-threaded like the decl init.
        _witness("mil.genrec_literal")
        return THIRMilInit(
            field_cpp=field_cpp,
            value=_lower_ru_literal(source, analyzer.get_expr_type(source),
                                    lc, declared))
    if (_eligible_ptr_value(ftype, analyzer)
            and isinstance(source, TpyNoneLiteral)):
        # None into a `Ptr[T]` cell: a non-STORAGE None renders `nullptr`.
        _witness("mil.ptr_none")
        return THIRMilInit(field_cpp=field_cpp,
                           value=THIRLiteral(result_type=ftype, value=None,
                                             form=Form.VALUE, loc=loc))
    if (_eligible_ptr_value(ftype, analyzer)
            and isinstance(source, TpyCall) and source.call_type is not None):
        # `Ptr[T]()` into a `Ptr[T]` cell: the MIL direct-init IS a storage
        # sink, so thread STORAGE use past the call-use gate; the render is
        # the position-independent typed nullptr (ctor.ptr_null).
        return THIRMilInit(
            field_cpp=field_cpp,
            value=_lower_expr(source, lc, declared,
                              use=_ExprUse(result=_ExprResultUse.STORAGE)))
    if (isinstance(unwrap_readonly(ftype), NoneType)
            and isinstance(source, TpyNoneLiteral)):
        # None into the NoneType cell: the STORAGE literal spells the
        # `std::monostate{}` the MIL direct-init needs.
        _witness("mil.none_unit")
        return THIRMilInit(field_cpp=field_cpp,
                           value=THIRLiteral(result_type=ftype, value=None,
                                             form=Form.STORAGE, loc=loc))
    if (_eligible_scalar(ftype) or _eligible_char(ftype)
            or _eligible_enum(ftype, analyzer) is not None):
        return THIRMilInit(field_cpp=field_cpp,
                           value=_slot_literal_retype(
                               _lower_expr(stmt.value, lc, declared), ftype,
                               lc))
    bytes_t = _resolved_bytes_value(ftype, analyzer)
    if bytes_t is not None:
        _witness("mil.bytes_field")
        if (isinstance(source, TpyCoerce)
                and source.coercion.name == "bytesview_to_bytes"):
            # The coerce's codegen lambda IS the view->owned bytes copy;
            # spell it through the S6 STORAGE convert (the identical
            # `::tpy::Bytes(...)` render) over the inner name.
            v = THIRFormConvert(result_type=bytes_t,
                                value=_lower_expr(source.expr, lc, declared),
                                form=Form.STORAGE, move=False, loc=loc)
        else:
            v = _lower_expr(source, lc, declared)
            # A view (span) source into the owned vector field copies to
            # owned -- vector has no span ctor; owned sources (literal /
            # `bytes()` rvalue) land bare.
            if v.form is Form.BORROW:
                v = THIRFormConvert(result_type=bytes_t, value=v,
                                    form=Form.STORAGE, move=False, loc=loc)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    if _resolved_str_value(ftype, analyzer) is not None:
        # str/StrView fields take the BARE render: std::string's EXPLICIT
        # string_view ctor fires in the MIL direct-init (no wrap is added
        # there); a sema `strview_to_str` coerce materializes itself.
        _witness("mil.str_field")
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc, declared))
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
            v = THIRFormConvert(result_type=ftype, value=_lower_expr(source, lc, declared),
                                form=Form.STORAGE, move=False, loc=loc)
        else:
            _witness("mil.union_rvalue")
            v = _lower_expr(source, lc, declared)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    vu = _eligible_value_union(ftype)
    if vu is not None:
        # F4 U1 cells render bare (the variant converting ctor does the work);
        # `None` is the monostate member. A top-level int/float literal
        # (possibly coerce-wrapped by sema) retypes to the union so the
        # BigInt wrap / Float32 suffix keyed on the literal's own scalar type
        # never fires -- the union is the render target, which takes neither.
        peeled = source
        while isinstance(peeled, TpyCoerce):
            peeled = peeled.expr
        if isinstance(peeled, TpyNoneLiteral):
            _witness("mil.union_none")
            return THIRMilInit(field_cpp=field_cpp,
                               value=THIRLiteral(result_type=vu, value=None,
                                                 form=Form.STORAGE, loc=loc))
        _witness("mil.value_union")
        v = _lower_expr(source, lc, declared)
        if isinstance(v, THIRLiteral) and isinstance(v.value, (int, float)):
            v = replace(v, result_type=vu)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    if _f1_tuple(ftype, analyzer) is not None:
        lit = stmt.value
        while isinstance(lit, TpyCoerce):
            lit = lit.expr
        if isinstance(lit, TpyTupleLiteral):
            # The spelled borrow-form brace-init wrapped in `tuple_to_storage`
            # (`t(::tpy::tuple_to_storage<S>(S{e1, e2}))`): the inner literal
            # spells the SAME storage tuple type S (to_cpp of the field slot),
            # elements per `_mil_ptr_tuple_elem_ok` -- a bare param name
            # (brace-init copies / optional's converting ctor absorbs), an
            # explicit `copy(p)` as the copy-ctor call `T(p)`, or a scalar.
            _witness("mil.ptr_tuple_literal")
            ft_tuple = _f1_tuple(ftype, analyzer)

            def elem(i: int) -> THIRExpr:
                e = lit.elements[i]
                slot = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
                    ft_tuple.element_types[i])))
                if isinstance(e, TpyNoneLiteral):
                    # `std::nullopt` in the storage-spelled inner literal.
                    return THIRLiteral(result_type=slot, value=None,
                                       form=Form.STORAGE,
                                       loc=getattr(e, "loc", None))
                src = _unwrap_copy(e, analyzer)
                if (src is e and isinstance(e, TpyName)
                        and _is_move_source(e, lc, own_param_names)):
                    # The Own-param element's last-use move
                    # (`{1, std::move(b)}`).
                    return THIRMove(
                        result_type=slot,
                        value=_lower_expr(e, lc, declared,
                                          allow_unrouted_name=True),
                        form=Form.STORAGE, loc=getattr(e, "loc", None))
                if src is not e and isinstance(src, TpyName):
                    st = declared.get(src.name)
                    st = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(st)))
                          if st is not None else slot)
                    return THIRRecordCopy(
                        result_type=slot, cpp_type=st.to_cpp(),
                        value=_lower_expr(src, lc, declared),
                        form=Form.STORAGE, loc=getattr(e, "loc", None))
                el = _lower_expr(e, lc, declared)
                if _eligible_scalar(slot) or _eligible_char(slot):
                    el = _slot_literal_retype(el, slot, lc)
                return el

            inner = THIRTupleLiteral(
                result_type=ftype,
                elements=tuple(elem(i)
                               for i in range(len(lit.elements))),
                loc=loc)
            return THIRMilInit(
                field_cpp=field_cpp,
                value=THIRFormConvert(result_type=ftype, value=inner,
                                      form=Form.STORAGE, move=False, loc=loc))
        if (isinstance(stmt.value, (TpyCall, TpyMethodCall))
                and _storage_form_tuple_return(
                    stmt.value.resolved_function_info)):
            # The storage-form-tuple call stores bare (`t(make_pair(5))`).
            _witness("mil.tuple_storage_call")
            return THIRMilInit(
                field_cpp=field_cpp,
                value=_lower_expr(
                    stmt.value, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 pos=SinkPos.MIL_INIT,
                                 allow_temps=True)))
        if isinstance(stmt.value, TpySubscript):
            # The storage-tuple element read stores bare
            # (`pair(::tpy::__getitem__(items, 0))`).
            _witness("mil.tuple_storage_subscript")
            return THIRMilInit(
                field_cpp=field_cpp,
                value=_lower_expr(
                    stmt.value, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 pos=SinkPos.MIL_INIT)))
        _mil_mixed = _mixed_own_storage_source(
            stmt.value, _f1_tuple(ftype, analyzer), frozenset(), analyzer)
        if _mil_mixed is not None:
            # The MIXED-own-tuple call: the same NON-move materialization
            # (`t(::tpy::tuple_to_storage<std::tuple<Box, Box>>(
            # make_mixed(b)))`).
            _witness("mil.tuple_storage_mixed_call")
            return THIRMilInit(
                field_cpp=field_cpp,
                value=THIRFormConvert(
                    result_type=ftype,
                    value=_lower_expr(
                        _mil_mixed, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.VALUE,
                                     pos=SinkPos.MIL_INIT, forms=_ONLY_BTUPLE_SLOT)),
                    form=Form.STORAGE, move=False, loc=loc))
        # F3: the borrow pointer-repr tuple param stores via
        # `tuple_to_storage` (a STORAGE convert; lowering admitted only the
        # bare borrow-name source).
        _witness("mil.tuple_storage")
        return THIRMilInit(
            field_cpp=field_cpp,
            value=THIRFormConvert(result_type=ftype,
                                  value=_lower_expr(stmt.value, lc, declared),
                                  form=Form.STORAGE, move=False, loc=loc))
    vt = _value_tuple(ftype, analyzer)
    if vt is not None:
        if isinstance(source, TpyTupleLiteral):
            _witness("mil.value_tuple_literal")
            try:
                value = _lower_tuple_literal(source, vt, lc, declared)
            except ThirUnsupported as ex:
                raise ThirUnsupported(
                    f"{_mil_reject_detail(stmt, analyzer)}:{ex.reason}"
                ) from None
            return THIRMilInit(field_cpp=field_cpp, value=value)
        _witness("mil.value_tuple_name")
        return THIRMilInit(field_cpp=field_cpp, value=_lower_expr(source, lc, declared))
    nt = _nested_storage_tuple(ftype, analyzer)
    if nt is not None and isinstance(source, TpyTupleLiteral):
        # The nested-storage tuple literal: the bare spelled brace-init,
        # per-level lifts inside (the shared literal render).
        _witness("mil.nested_tuple_literal")
        try:
            value = _lower_tuple_literal(source, nt, lc, declared)
        except ThirUnsupported as ex:
            raise ThirUnsupported(
                f"{_mil_reject_detail(stmt, analyzer)}:{ex.reason}") from None
        return THIRMilInit(field_cpp=field_cpp, value=value)
    if isinstance(ftype, OptionalType):
        oc_inner = _optional_container_storage_inner(ftype)
        if isinstance(source, TpyNoneLiteral):
            _witness("mil.optional_none")
            v: THIRExpr = THIRLiteral(result_type=ftype, value=None,
                                      form=Form.STORAGE, loc=loc)
        elif oc_inner is not None and isinstance(
                source, (TpyArrayLiteral, TpyDictLiteral, TpySetLiteral)):
            # A container literal into `std::optional<C>`: lowered against the
            # INNER, since the container-literal render unwraps the Optional
            # itself -- threading the Optional would derive the element
            # targets from it. An ARRAY literal additionally self-describes
            # (`lst(std::vector<int32_t>{1, 2, 3})`) via the type prefix on
            # the same branch: a bare brace-init has no
            # deducible type for the optional's ctor. Dict/set literals spell
            # their own container type already.
            _witness("mil.optional_container_literal")
            v = _lower_expr(source, lc, declared,
                            use=_ExprUse(slot_target=oc_inner))
            if isinstance(source, TpyArrayLiteral):
                v = _spell_mil_brace_literal(v, oc_inner, stmt, lc)
        elif _str_literal_value_opt_arg(_peel_coerce(source), ftype):
            # `s("xy")` -- the bare str literal at a value-repr Optional[str]
            # slot; C++'s `const char*` -> `optional<string>` chain absorbs it.
            _witness("mil.optional_str_literal")
            v = _lower_expr(source, lc, declared)
        elif not ftype.uses_pointer_repr() and _value_opt_view(
                ftype, analyzer) is not None:
            # A value-repr Optional[str/bytes] field <- a borrow
            # `optional<view>` param: the arg-split shim, the same
            # `view_to_owned_conv` render the call-arg THIROptViewArg emits.
            # The shim names its source, so the render carries its own
            # shape precondition rather than trusting the gate's.
            if not isinstance(source, TpyName):
                raise ThirUnsupported(_mil_reject_detail(stmt, analyzer))
            _witness("mil.optview_shim")
            v = THIROptViewArg(result_type=ftype, name=source.name,
                               form=Form.VALUE, loc=loc)
        elif not ftype.uses_pointer_repr():
            # The gate admitted only a same-typed value-repr optional param
            # name: the whole-optional bare copy (`f(value)`). The read is a
            # deliberate whole-binding copy, not an unwrap -- hence the
            # allow_whole_optional pass.
            _witness("mil.optional_value_copy")
            v = _lower_expr(source, lc, declared, allow_whole_optional=True)
        elif _is_borrow_ptr_local(source, declared, set()):
            v = THIRFormConvert(result_type=ftype, value=_lower_expr(source, lc, declared),
                                form=Form.STORAGE, move=False, loc=loc)
            _witness("mil.optional_ptr_lift")
        else:
            # A record-value source constructs the optional directly -- no
            # ptr_to_optional (that lifts a borrow `T*`, not a record prvalue/copy).
            v = _lower_expr(source, lc, declared)
        return THIRMilInit(field_cpp=field_cpp, value=v)
    if _span_value(ftype):
        # Same-typed span param name: the bare view copy (`items(items)`) --
        # borrow and storage coincide for a view, so no lift renders.
        _witness("mil.span_copy")
        return THIRMilInit(field_cpp=field_cpp,
                           value=_lower_expr(source, lc, declared))
    if _callable_value(unwrap_readonly(unwrap_ref_type(
            unwrap_send_sync(ftype)))):
        # Same-typed callable param name: the bare `std::function` copy
        # (`on_event(cb)`), per the gate's exact-type pin. A routable LAMBDA
        # literal renders its closure into the same direct-init slot.
        _witness("mil.callable_lambda" if isinstance(source, TpyLambda)
                 else "mil.callable_copy")
        return THIRMilInit(field_cpp=field_cpp,
                           value=_lower_expr(source, lc, declared))
    if isinstance(stmt.value, TpyIfExpr) and _f1_record(ftype, analyzer):
        # The prvalue record ternary: the MIL direct-init IS the value sink,
        # so the arms lower as prvalues and the whole `?:` lands bare.
        return THIRMilInit(
            field_cpp=field_cpp,
            value=_lower_expr(stmt.value, lc, declared,
                              use=_ExprUse(
                                  pos=SinkPos.MIL_INIT, forms=_ONLY_RECORD_PRVALUE)))
    if _method_rvalue_f1_record(source, analyzer):
        # An Own-returning method-call rvalue constructs the field directly
        # (`shared(Rc<Val>::new_<Val>(Val(0)))`): the MIL slot is a storage
        # sink, so the method's record result rides the storage escape.
        _witness("mil.record_method_rvalue")
        return THIRMilInit(
            field_cpp=field_cpp,
            value=_lower_expr(
                source, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE)))
    value = _lower_expr(
        source, lc, declared,
        use=_ExprUse(slot_target=(ftype
                                  if _container_storage_field(ftype)
                                  else None)),
        field_prechecked=isinstance(source, TpyFieldAccess))
    if _container_storage_field(ftype) and isinstance(source, TpyArrayLiteral):
        value = _spell_mil_brace_literal(value, ftype, stmt, lc)
    return THIRMilInit(field_cpp=field_cpp, value=value)


def _spell_mil_brace_literal(v: THIRExpr, slot: TpyType, stmt: TpyAssign,
                             lc: '_LowerCtx') -> THIRExpr:
    """Self-describe a list/Array literal at a member-init cell.

    The cell is a paren direct-init of the field, so a bare brace there is an
    ARGUMENT to the field type's constructor overload set, not a list-init of
    the field: `xs({1})` into `std::vector<BigInt>` resolves to the size
    constructor (int -> size_t is a standard conversion, int -> BigInt a
    user-defined one) and builds one zero. Spelling the slot type
    (`xs(std::vector<BigInt>{1})`) makes the literal a real list-init, the
    same render the call-arg slot uses. A bracket literal at a container
    cell lowers to a container literal or raises, so anything else here is
    a lowering that lost the prefix's home: reject loudly rather than let
    the bare brace (or a `replace` TypeError) through."""
    if not isinstance(v, THIRContainerLiteral):
        raise ThirUnsupported(_mil_reject_detail(stmt, lc.analyzer))
    return replace(v, typed_brace_cpp=lc.render_type(slot))


def _method_self_type(record, analyzer) -> 'TpyType | None':
    """The `self` receiver type for an M1 method / ctor feed. The qname is
    load-bearing -- a bare `NominalType(name)` has no registry entry, so
    `is_user_record` (hence `_f1_record`) is False; `_f1_record` applies the
    remaining native / cross-module / generic-arg checks at lowering. For a
    generic record the self is `Record[T, ...]` (a `TypeParamRef` per type
    param, kinds per-index like sema's own self-type mirror), which `_f1_record`
    admits via `_f1_record_type_arg_ok`, opening lowering for the record's
    templated bodies. Unsupported T-slot cells still reject the body."""
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
    """`_method_self_type` from the record NAME (the resumable entry has the
    record name, not the parse-tree node) -- reads `type_params` / `type_param_kinds`
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

def unemitted_overload_clones(module: TpyModule, analyzer) -> set[int]:
    """ids of the auto_readonly / auto_own CLONE of an @overload impl whose
    body is never emitted.

    `_collect_method_overload_groups` hands a name's stubs to the FIRST
    non-stub method it sees (`pending_stubs.pop`), and method expansion has
    already split the impl into a mutable and a const clone -- so only one of
    the two is registered. THAT one is emitted once per stub, taking each
    specialization's const-ness from the stub, and the twin is never named:
    every `thir_overload_key` for the group points at the registered impl.
    Attempting the twin would lower a body that does not exist."""
    out: set[int] = set()
    for record in module.records:
        registered = {m.name for m in record.methods
                      if analyzer.overload_groups.get(m)}
        if not registered:
            continue
        for m in record.methods:
            if (m.name in registered and not m.is_overload_stub
                    and not analyzer.overload_groups.get(m)
                    and (m.auto_readonly_params_resolved
                         or m.is_auto_own_borrowing_clone
                         or m.is_auto_own_consuming_clone)):
                out.add(id(m))
    return out


def iter_module_callables(module: TpyModule, analyzer):
    """Yield `(callable, self_type)` for every function / method the slice may
    admit -- the one feed list codegen and the lowering pins share, so the
    two never drift. Lowering still has the final say; this only enumerates
    candidates. Free functions yield `self_type=None`; record methods
    (instance / static / property / dunder) yield the owning record's type
    (None-skipped for generic records). The constructor is excluded -- its body
    is emitted via the member-init-list driver (the M3 ctor frontier), not
    gen_method_def; so is the unemitted @overload clone, which has no body to
    emit, and the `skip_codegen` (@inline) callable, whose body sema
    never analyzed -- lowering it would walk a body with empty `expr_types`.
    Excluding them HERE rather than at each driver is what keeps a third
    consumer (the dump) from reporting them as un-routed."""
    dead_clones = unemitted_overload_clones(module, analyzer)
    for func in module.functions:
        if func.skip_codegen:
            continue
        yield func, None
    for record in module.all_records():
        self_type = _method_self_type(record, analyzer)
        if self_type is None:
            continue
        init = record.init_method
        for method in record.methods:
            if (method is not init and not method.skip_codegen
                    and id(method) not in dead_clones):
                yield method, self_type

def iter_module_constructors(module: TpyModule, analyzer):
    """Yield `(record, init_method, self_type)` for every record that defines an
    `__init__` -- the ctor feed for the M3 frontier, the sibling of
    `iter_module_callables` (which excludes the ctor because its body is emitted by
    the member-init-list driver). `self_type` is the owning
    record's F1-record receiver (None-skipped for generic records, which
    `lower_constructor` also rejects). Constructor lowering has the final say;
    this only enumerates candidates."""
    for record in module.all_records():
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

def _iter_thir(roots):
    stack = list(roots)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(_iter_children(node))


def _needs_held_back_slot(node) -> bool:
    """A THIR statement whose emit PRE-declares a rebind slot -- one reserved at
    a declaration whose C++ declaration is held back until a rebind consumes it
    (`_declare_rebind_slot`).

    Such a declaration is drained at the prologue of the body being emitted. A
    body rendered into a C++ lambda needs that drain INSIDE the lambda (the
    enclosing prologue is outside its capture list): the nested-def emitter
    buffers and prepends, and the SGEN leaf routes into the ctx's nested
    hoist scope (its hoist_sink); what remains guarded here is the
    cross-scope `nonlocal` hazard (`_rejects_lambda_hoist`). Mirrors
    `_rejects_global_slot`: every emit site calling `_declare_rebind_slot`
    must be represented here."""
    if isinstance(node, THIRPtrLocalDecl):
        return node.needs_rebind_slot
    if isinstance(node, THIRVarDecl):
        return node.cpp_local_representation is LocalBinding.REBIND_SLOT
    if isinstance(node, THIRIf):
        return bool(node.hoist_slots)
    return False


def _slot_owning_name(node) -> 'str | None':
    """The local a pre-declared rebind slot belongs to, if `node` reserves one."""
    return getattr(node, "name", None) if _needs_held_back_slot(node) else None


def _rebound_name(node) -> 'str | None':
    """The local `node` rebinds, for the nodes whose emit consumes a slot."""
    if isinstance(node, THIRPtrLocalRebind):
        return node.name
    if isinstance(node, THIRAssign) and isinstance(node.target, THIRName):
        return node.target.name
    return None


def cross_scope_rebind_site(
        outer, inner) -> 'tuple[str, SourceLocation | None] | None':
    """The `(name, loc)` of a rebind in a LAMBDA-rendered body that CONSUMES a
    rebind slot the ENCLOSING scope reserved, or None.

    The slot is declared at the enclosing body's prologue, outside the lambda's
    capture list: inside the lambda it dies each invocation while the pointer
    aliasing it is captured and outlives it, and outside it the lambda cannot
    name it. Nothing checks it at emit time, so this predicate is
    its entire protection. A lambda body reserving its OWN slot is not a hazard
    -- the emitters drain it inside the lambda.

    The offending rebind is returned rather than a bare verdict because the
    user-facing diagnostic points at the name's REBIND, not at the body that
    holds it.

    A rebind whose name the lambda body itself reserves a slot for is a SHADOW,
    not a cross-scope consume, but the reason differs per caller and only one of
    the two is the local-vs-`nonlocal` dichotomy. For a NESTED DEF the two
    operands are two Python scopes, and within one of them a name is either local
    or `nonlocal`, never both. For the SIMPLE-GENERATOR peephole they are ONE scope
    -- the lambda is a render, not a Python scope -- and what carries `rb in own`
    there is that a local is DECLARED once per scope: a name whose slot is
    reserved inside the loop reserved none in the prologue, so the drain is
    inside the lambda either way.

    EVERY lambda-rendered body must be checked, not just nested defs: the
    simple-generator peephole renders its loop into a lambda too, with the
    pre-loop statements left in the enclosing function.
    """
    outer_slot_names = {n for n in (_slot_owning_name(x) for x in _iter_thir(outer))
                        if n is not None}
    if not outer_slot_names:
        return None
    own = {n for n in (_slot_owning_name(x) for x in _iter_thir(inner))
           if n is not None}
    for n in _iter_thir(inner):
        rb = _rebound_name(n)
        if rb is not None and rb in outer_slot_names and rb not in own:
            return rb, n.loc
    return None


def _rejects_lambda_hoist(body) -> bool:
    """The nested-def flavor of `cross_scope_rebind_site`.

    `own` is deliberately not computed across a further nesting level -- sema
    rejects a nested def inside a nested def, so there is none.

    A verdict only, never the diagnostic: a local first declared in a branch
    rides `THIRIf.hoist_slots`, whose owner `_slot_owning_name` cannot read, so
    the shadow exemption misses it and this over-rejects -- a valid shape can
    land on the `nested_def.rebind_slot_hoist` reject.
    """
    return any(cross_scope_rebind_site(body, nd.body) is not None
               for nd in _iter_thir(body)
               if isinstance(nd, THIRNestedDef))


def _rejects_global_slot(node) -> bool:
    """A THIR statement whose emit allocates a `__slot_N` off the shared
    counter. At module scope every slot must spell `static __global_slot_N`,
    and only GLOBAL_RVALUE is wired for that -- its three sibling writes
    (`GLOBAL_REBIND` reuses that same slot, `GLOBAL_NULL`,
    `GLOBAL_PTR_COPY` and the PTR_ADDR RESEAT allocate none) call
    `next_slot()` nowhere. NB the PTR_ADDR *decl* flavor does draw a slot
    (its rvalue-reseat rebind slot), which is why the THIRPtrLocalDecl arm
    below stays a blanket reject rather than exempting the kind by name.

    THE INVARIANT THIS GUARDS IS MEMORY SAFETY, not byte-identity: a
    block-scoped `__slot_N` at namespace scope leaves the global pointing at a
    dead frame the moment `__tpy_init` returns. Every emit site that calls
    `_EmitState.next_slot()` must be represented here. The traversal is
    generic (`validate._iter_children` walks dataclass fields), so only this
    predicate needs maintaining; inverting it to an allowlist would make the
    failure mode a spurious reject instead of a dangling pointer."""
    if isinstance(node, THIRPtrLocalDecl):
        # RECORD_HOISTED joins GLOBAL_RVALUE: its slot rides the hoist
        # lines, which spell `static __global_slot_N` at module scope
        # (state.slot_static + slot_prefix), so nothing block-scoped
        # outlives __tpy_init.
        if (node.kind is PtrSlotKind.RECORD_HOISTED
                and not node.needs_rebind_slot):
            return False
        return node.kind is not PtrSlotKind.GLOBAL_RVALUE
    if isinstance(node, THIRPtrLocalRebind):
        return node.kind not in (PtrSlotKind.GLOBAL_REBIND,
                                 PtrSlotKind.GLOBAL_NULL,
                                 PtrSlotKind.GLOBAL_PTR_COPY,
                                 # `g = &(<borrow call>);` points AT
                                 # callee-owned storage -- no slot at all,
                                 # so nothing can outlive `__tpy_init`.
                                 PtrSlotKind.PTR_ADDR,
                                 # The @dynamic rebind's slot spells
                                 # `static std::optional<T> __global_slot_N`
                                 # at module scope (slot_static/slot_prefix
                                 # in the emit arm), so it survives
                                 # `__tpy_init` like GLOBAL_RVALUE's.
                                 PtrSlotKind.DYN_PROTOCOL,
                                 # A hoisted global's optional slot rides
                                 # the hoist lines, spelled with the same
                                 # slot_static/slot_prefix pair.
                                 PtrSlotKind.GLOBAL_HOIST_RVALUE)
    if isinstance(node, THIRIf):
        return bool(node.hoist_slots)
    if isinstance(node, THIRVarDecl):
        # The F2d two-slot rvalue pointer-local allocates an init AND a rebind
        # slot; it carries no `slot_cpp`, so the tail below cannot see it.
        return node.cpp_local_representation is LocalBinding.REBIND_SLOT
    if isinstance(node, THIRGenExpr):
        # `slot_cpp` here is the make_generator YIELD-slot spelling
        # (`optional<slot>`), not a `__slot_N` allocation -- _emit_genexpr
        # calls next_slot() nowhere.
        return False
    return bool(getattr(node, "slot_cpp", None))


def lower_top_level(module: TpyModule, analyzer, global_types, *,
                    final_types=None,
                    render_type=None, render_type_stored=None,
                    render_resolve=None, render_concept=None,
                    user_module_imports=None,
                    all_user_modules=frozenset()) -> 'THIRFunction | None':
    """Lower a module's top-level statements -- the `__tpy_init` body -- or
    None if any of them falls outside the slice.

    Top-level names are MODULE GLOBALS, not function locals: the generator has
    already declared each at namespace scope, so a write is an assignment (a
    non-value global's initializing write allocates a `static __global_slot_N`
    instead), and `global_types` is the generator's own `seen_globals` map
    (Final globals excluded -- they live at namespace scope). Everything below
    that seeding reuses the ordinary statement/expression arms.
    """
    carrier = TpyFunction(name="__tpy_init", params=[],
                          return_type=VoidType(),
                          body=list(module.top_level_stmts))
    lc = _LowerCtx(carrier, analyzer, render_type,
                   render_type_stored=render_type_stored,
                   render_resolve=render_resolve,
                   render_concept=render_concept,
                   scan_override=analyzer.top_level_scan_result,
                   hoisted_override=analyzer.top_level_hoisted_vars,
                   move_through_override=analyzer.top_level_move_through_vars,
                   top_level_scope=True)
    lc.prescan.native_globals = module_native_globals(module)
    declared: dict[str, TpyType] = {}
    for name, gt in global_types.items():
        if gt is None:
            continue
        declared[name] = gt
        if _value_opt_scalar(gt, analyzer) is not None:
            # A value-repr `Optional[scalar]` global IS a `std::optional<T>`
            # binding at namespace scope, so its reads/writes take the
            # value-opt LOCAL arms (bare whole-optional pass, narrowed `(*g)`,
            # `= std::nullopt`) -- the same seeding a function body gives the
            # same global.
            lc.value_opt_bindings[name] = ValueOptKind.SCALAR
        if not gt.is_value_type() and not gt.needs_wrapper():
            # `std::vector<T>* g{}` at namespace scope: reads deref through
            # the pointer-local arms, writes take the static-slot render.
            # `global_slots` too, not just `pointers`: a pointer-slot GLOBAL
            # derefs at EVERY value position whatever its family (the
            # indirect-name render), where a pointer LOCAL of
            # record type stays bare and reaches its members via `->`.
            lc.global_ptr_slots.add(name)
            lc.pointers.add(name)
            lc.prescan.global_slots = lc.prescan.global_slots | {name}
        elif _f1_tuple(gt, analyzer) is not None:
            # A pointer-repr F3 tuple global is a namespace-scope STORAGE
            # value (`std::tuple<std::optional<T>, ..> g;` -- tuples are
            # value types, never pointer slots). Register it with the
            # storage-form tuple names so the btuple-local reseat cannot
            # mis-key it as a borrow local (which would emit the borrow
            # literal bare, skipping the tuple_to_storage lift -- ill-formed
            # C++); its top-level write takes the storage-global arm.
            lc.storage_tuple_locals.add(name)
    for name, ft in (final_types or {}).items():
        # A `Final` global lives at namespace scope as a `const T` and is
        # never assignable, so it only needs to READ bare -- seeded like the
        # read-only value globals a function body gets. A non-value Final
        # keeps rejecting (no verified render for its reads here).
        if ft is not None and ft.is_value_type():
            declared[name] = ft
            lc.prescan.global_readonly = lc.prescan.global_readonly | {name}
    for name, decl_line in analyzer.ctx.top_level_decls.items():
        imp = imported_variable_cpp(analyzer.registry, analyzer.imported_names,
                                    name)
        if imp is not None:
            lc.pre_decl_import_cpp[name] = (decl_line, imp)
    # Imported globals this module never redefines read through their fixed
    # qualified spelling here exactly as they do inside a function body --
    # the shared seeding keeps the two in step.
    imported: dict[str, TpyType] = {}
    imported_cpp: dict[str, str] = {}
    imported_slots: set[str] = set()
    _seed_imported_globals(analyzer, imported, imported_cpp, imported_slots,
                           skip=lambda n: n in declared)
    declared.update(imported)
    # Every imported global carries a spelling (`_seed_imported_globals`
    # writes `spelled[n]` on every branch), so none of them join the
    # bare-reading `global_readonly` set.
    lc.prescan.global_cpp = {**lc.prescan.global_cpp, **imported_cpp}
    lc.prescan.global_slots = lc.prescan.global_slots | frozenset(imported_slots)
    lc.pointers.update(imported_slots)
    # A native-linkage global splits its spelling exactly as it does inside a
    # function body: writes take the BARE C name, reads the `::`-qualified
    # one (the `native_global_names` write target vs the qualified read).
    for name, cname in lc.prescan.native_globals.items():
        if name in declared:
            lc.prescan.global_write_cpp[name] = cname
            lc.prescan.global_cpp.setdefault(name, qualify_native_name(cname))
    emitted: set[str] = set()
    for stmt in carrier.body:
        if isinstance(stmt, TpyImport):
            lc.import_calls[id(stmt)] = tuple(
                qualified_cpp_name(target, "__tpy_init")
                for target in module_init_targets(
                    stmt, registry=analyzer.registry,
                    module_name=analyzer.ctx.module_name,
                    user_module_imports=(module.user_module_imports
                                         if user_module_imports is None
                                         else user_module_imports),
                    all_user_modules=all_user_modules, emitted=emitted))
    try:
        body = _lower_stmts(carrier.body, lc, declared, top_level=True)
        if lc.unhandled_hoists:
            raise ThirUnsupported("body.hoisted_vars")
        fn = THIRFunction(name="__tpy_init", params=(),
                          return_type=VoidType(), body=body,
                          layout=THIRFunctionLayout())
        slot_node = next((n for n in _iter_thir(fn.body)
                          if _rejects_global_slot(n)), None)
        if slot_node is not None:
            raise ThirUnsupported("top_level.slot_alloc",
                                  loc=getattr(slot_node, "loc", None))
        validate_function(fn)
        return fn
    except ThirUnsupported as ex:
        note(ex.reason, ex.loc)
        return None
