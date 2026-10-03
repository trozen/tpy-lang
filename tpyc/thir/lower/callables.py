"""Semantic callable facts retained at the selected lowering operation."""

from dataclasses import replace
from typing import TYPE_CHECKING

from ...compilation_context import get_current_compiler
from ...parse.nodes import TpyFunction
from ...symbol_binding import SymbolKind
from ...type_def_registry import ParamPassing, get_type_def
from ...typesys import (
    AnyType, FunctionInfo, FunctionLinkage, IntLiteralType, NominalType, ReadonlyType, RefType, TpyType,
    contains_pending_leaf, contains_type_param, is_fn_type, is_protocol_type, return_const_projected,
    return_representation, unwrap_readonly, unwrap_ref_type,
    unwrap_send_sync,
)
from ..nodes import (
    THIRBorrowedRecord, THIRCallableSignature, THIRExpr, THIRFunctionIdentity, THIRMethodCall, THIRResolvedCallee,
    THIRStubCallee, THIRStubContract, THIRStubIdentity, THIRSubscript, declared_param_type, receiver_param,
)
from ..scalar_leaves import leaf_constant, native_container_subject
from .storage import borrowed_record

if TYPE_CHECKING:
    from ...sema.analyzer import SemanticAnalyzer


def _implicit_template_type(typ: TpyType) -> bool:
    return (isinstance(typ, AnyType) or is_fn_type(typ) or is_protocol_type(typ)
            or any(_implicit_template_type(inner) for inner in typ.inner_types()))


def _stale_reference(typ: TpyType) -> bool:
    # A cycle's placeholder can acquire value semantics after signature registration.
    return ((isinstance(typ, RefType) and typ.wrapped.is_value_type())
            or any(_stale_reference(inner) for inner in typ.inner_types()))


def resolved_definition(func: TpyFunction, analyzer: 'SemanticAnalyzer') -> THIRResolvedCallee | None:
    group = analyzer.registry.get_function(func.name)
    if group is None or len(group) != 1:
        return None
    fi = group[0]
    if fi.root.declaration is not func:
        return None
    return resolved_callee(fi, analyzer)


def signature_passings(types: tuple[TpyType, ...], consts: tuple[bool, ...]) -> tuple[ParamPassing, ...]:
    """How each parameter is passed at its const verdict -- the fact a
    definition's `THIRParam.passing` publishes, asked of the same types."""
    return tuple(t.param_passing(const) for t, const in zip(types, consts, strict=True))


def _stub_param_consts(fi: FunctionInfo) -> tuple[bool, ...]:
    """The const verdict a body-less stub declares per parameter: a
    `readonly[...]` parameter, or a whole-callable promise -- `@pure`, or
    `@readonly` on a free function, which sema reads as "mutates no
    argument" for a callee it cannot analyze (on a method it is a receiver
    fact only)."""
    whole = fi.is_pure or (fi.is_readonly and not fi.is_method)
    return tuple(whole or isinstance(unwrap_ref_type(p.type), ReadonlyType) for p in fi.params)


def _open_stub_type(typ: TpyType) -> bool:
    # A protocol parameter stays: the passing says it is template-dependent.
    return (isinstance(typ, AnyType) or is_fn_type(typ) or contains_type_param(typ)
            or contains_pending_leaf(typ) or _stale_reference(typ))


def _bound_by_body(fi: FunctionInfo, root: FunctionInfo, arity: int | None) -> bool:
    """Whether a call binds `fi` exactly as the body behind it declares,
    with nothing the call adds: no symbol or template of its own, no
    wrapper, resumable frame or callable value, no type parameter,
    variadics or keyword pack, and the substituted signature equal to the
    raw one."""
    return not (root.originating_module is None or root.type_params
                or root.is_staticmethod or root.is_classmethod
                or root.is_constructor or root.is_callable_value or root.frame_captures is not None
                or root.is_async or root.is_generator or root.error_return_type is not None
                or root.native_name is not None or root.cpp_template is not None
                or root.linkage is not FunctionLinkage.DEFAULT or root.is_builtin_function
                or root.special_handling or root.inline_body is not None or root.kwarg_name is not None
                or any(p.is_variadic or p.is_kwargs for p in root.params)
                or fi.return_type is None or fi.return_type != root.return_type
                or tuple(p.type for p in fi.params) != tuple(p.type for p in root.params)
                or arity is not None and arity != len(fi.params))


def _declares(root: FunctionInfo, declaration: TpyFunction) -> bool:
    return (root.return_type == declaration.return_type
            and tuple(p.name for p in root.params) == tuple(n for n, _ in declaration.params)
            and tuple(declared_param_type(p.type) for p in root.params)
            == tuple(declared_param_type(t) for _, t in declaration.params))


def _closed_signature(fi: FunctionInfo) -> bool:
    return not any(contains_type_param(t) or contains_pending_leaf(t) or _implicit_template_type(t)
                   or _stale_reference(t) for t in (*(p.type for p in fi.params), fi.return_type))


def _identity_module(root: FunctionInfo, analyzer: 'SemanticAnalyzer') -> str:
    owner = root.originating_module
    return analyzer.ctx.cpp_module_name if owner == analyzer.ctx.module_name else owner


def _borrowed_result(fi: FunctionInfo, declaration: TpyFunction,
                     analyzer: 'SemanticAnalyzer') -> THIRBorrowedRecord | None:
    # Readonly exactly when the emitted result is const. The declaration's
    # verdict is the signature's (a free function's `@pure` alone leaves it
    # mutable); an inferred method verdict const-projects only a result that
    # borrows the receiver.
    if not isinstance(fi.return_type, (RefType, ReadonlyType)):
        return None
    readonly = ((declaration.is_readonly and return_const_projected(fi))
                or isinstance(unwrap_ref_type(fi.return_type), ReadonlyType))
    return borrowed_record(fi.return_type, readonly, analyzer)


def _declared_passings(fi: FunctionInfo, declaration: TpyFunction) -> tuple[ParamPassing, ...] | None:
    # The declaration's own types and verdict, as its THIRParams read them.
    verdict = fi.const_borrow_params
    return (None if verdict is None else
            signature_passings(tuple(t for _, t in declaration.params),
                               tuple(i in verdict for i in range(len(declaration.params)))))


def resolved_callee(fi: FunctionInfo | None, analyzer: 'SemanticAnalyzer',
                    *, arity: int | None = None) -> THIRResolvedCallee | None:
    if fi is None:
        return None
    root = fi.root
    if root.declaration is None or root.is_method or not _bound_by_body(fi, root, arity):
        return None
    declaration = root.declaration
    if not _declares(root, declaration):
        return None
    owner = root.originating_module
    module = _identity_module(root, analyzer)
    info = analyzer.registry.get_module(module)
    if info is None or info.module_attributes is None:
        return None
    cell = info.module_attributes.get(root.name)
    group = info.functions.get(root.name)
    if (cell is None or cell.binding.kind is not SymbolKind.FUNCTION
            or cell.binding.defining_module is not None or cell.binding.canonical_name != root.name
            or group is None or len(group) != 1 or group[0].root is not root
            or root.qualified_name != f"{owner}.{root.name}"):
        return None
    if not _closed_signature(fi):
        return None
    types = tuple(p.type for p in fi.params)
    return THIRResolvedCallee(THIRFunctionIdentity(module, root.name),
                              THIRCallableSignature(types, fi.return_type, _borrowed_result(fi, declaration, analyzer),
                                                    _declared_passings(fi, declaration),
                                                    return_representation(fi.return_type)))


def method_receiver(func: TpyFunction, self_type: TpyType | None, analyzer: 'SemanticAnalyzer', *,
                    stub: TpyFunction | None = None) -> THIRBorrowedRecord | None:
    """The receiver an instance method body of a plain record lowers under:
    `self` borrowed for the call at the method's finalized access. None for
    a body that binds anything else -- a static or class method, an enum
    member, a lifecycle hook the generated special members run around, a
    property accessor, a consuming, generic, async or generator body, a
    per-stub specialization or a clone of a two-body def -- and a constructor
    body, whose `self` is storage under construction."""
    if (self_type is None or stub is not None or not func.is_method or func.is_staticmethod
            or func.is_initializer or func.is_lifecycle_hook or func.is_consuming or func.type_params
            or func.is_property_getter or func.is_property_setter
            or func.is_async or func.is_generator
            or func.is_auto_own_borrowing_clone or func.is_auto_own_consuming_clone
            or func.auto_readonly_polarity is not None):
        return None
    receiver = borrowed_record(self_type, func.is_readonly, analyzer)
    info = analyzer.registry.get_record_for_type(receiver.type) if receiver is not None else None
    return receiver if info is not None and info.enum_companion_of is None else None


def _single_body(root: FunctionInfo) -> TpyFunction | None:
    compiler = get_current_compiler()
    return compiler.single_method_body(root.owning_type_qname, root.name) if compiler is not None else None


def method_callee(fi: FunctionInfo | None, receiver: TpyType | None, analyzer: 'SemanticAnalyzer', *,
                  arity: int) -> THIRResolvedCallee | None:
    """The user record method a call on `receiver` (the call's static
    receiver type) runs, as one identity and one signature whose parameter
    0 is the receiver at the method's readonly verdict -- the same value
    the definition publishes. None unless the target is proven: an ordinary
    instance method (`method_receiver`) of exactly the receiver's plain
    record, whose methods nothing overrides virtually (no `@dynamic`
    protocol), with one body under its name, no rendering of its own (a
    template -- a dunder's injected operator included -- or a native
    symbol) and a closed signature."""
    if fi is None or receiver is None:
        return None
    root = fi.root
    if (not root.is_method or root.is_consuming or root.native_function
            or root.is_property_getter or root.is_property_setter or root.is_auto_readonly_mutable_clone
            or root.overloaded or not _bound_by_body(fi, root, arity) or not _closed_signature(fi)):
        return None
    owner = borrowed_record(receiver, root.is_readonly, analyzer)
    registry = analyzer.registry
    info = registry.get_record_for_type(owner.type) if owner is not None else None
    overloads = info.get_method_overloads(root.name) if info is not None else []
    if (info is None or owner.type.qualified_name() != root.owning_type_qname
            # One canonical spelling, so the definition and every call publish one value.
            or owner.type != NominalType(info.name, _module_qname=info.qualified_name())
            or next(registry.iter_dynamic_protocols(info), None) is not None
            or len(overloads) != 1 or overloads[0].root is not root):
        return None
    body = _single_body(root)
    passings = _declared_passings(fi, body) if body is not None else None
    if (body is None or method_receiver(body, owner.type, analyzer) != owner
            or not _declares(root, body) or passings is None):
        return None
    return THIRResolvedCallee(
        THIRFunctionIdentity(_identity_module(root, analyzer), root.name, root.owning_type_qname),
        THIRCallableSignature((owner.type, *(p.type for p in fi.params)), fi.return_type,
                              _borrowed_result(fi, body, analyzer), (receiver_param(owner).passing, *passings),
                              return_representation(fi.return_type)))


def method_definition(func: TpyFunction, receiver: THIRBorrowedRecord | None,
                      analyzer: 'SemanticAnalyzer') -> THIRResolvedCallee | None:
    """The callee fact a method body publishes: its calls' `method_callee`,
    when this body is the one they run."""
    info = analyzer.registry.get_record_for_type(receiver.type) if receiver is not None else None
    overloads = info.get_method_overloads(func.name) if info is not None else []
    if len(overloads) != 1 or _single_body(overloads[0].root) is not func:
        return None
    return method_callee(overloads[0], receiver.type, analyzer, arity=len(overloads[0].params))


def _not_a_stub(fi: FunctionInfo, arity: int) -> bool:
    """The bindings a stub's declaration does not describe alone: no bound
    symbol or template, a consuming or resumable callee, a callable value,
    a wrapper or special form the call adds, variadics and keyword packs."""
    return any((not (f.is_native_import or f.cpp_template is not None)
                or f.is_constructor or f.is_callable_value or f.is_async or f.is_generator
                or f.is_consuming or f.error_return_type is not None or f.special_handling
                or f.is_builtin_function or f.inline_body is not None or f.kwarg_name is not None
                or f.frame_captures is not None or f.is_property_getter or f.is_property_setter
                or any(p.is_variadic or p.is_kwargs for p in f.params))
               for f in (fi, fi.root)) or arity != len(fi.params) or fi.return_type is None


def stub_callee(fi: FunctionInfo | None, result_type: TpyType, *, arity: int) -> THIRStubCallee | None:
    """The declared facts of a call to a `@native` / `@cpp_template` free
    function or builtin-type initializer, or None when the callee is not
    such a stub or anything about the binding is open (a type parameter,
    variadics, keyword packs, a wrapper the call adds)."""
    if fi is None:
        return None
    initializer = fi.is_method and fi.name == "__init__" and not fi.is_staticmethod
    if _not_a_stub(fi, arity) or (fi.is_method and not initializer):
        return None
    if initializer:
        # An initializer returns None; the call yields the type it initializes.
        built = unwrap_send_sync(unwrap_readonly(unwrap_ref_type(result_type)))
        if (not fi.owning_type_qname or not isinstance(built, NominalType) or built.type_args
                or built.qualified_name() != fi.owning_type_qname):
            return None
        name, ret = f"{fi.owning_type_qname}.{fi.name}", built
    else:
        name, ret = fi.qualified_name, fi.return_type
    types = tuple(p.type for p in fi.params)
    if not name or any(_open_stub_type(t) for t in (*types, ret)):
        return None
    consts = _stub_param_consts(fi)
    return THIRStubCallee(THIRStubIdentity(name, types),
                          THIRCallableSignature(types, ret, None, signature_passings(types, consts),
                                                return_representation(ret)),
                          _stub_contract(fi), consts)


def _stub_contract(fi: FunctionInfo) -> THIRStubContract | None:
    return (THIRStubContract.PURE if fi.is_pure
            else THIRStubContract.TRANSIENT if fi.is_transient else None)


def method_stub_callee(fi: FunctionInfo | None, receiver: TpyType | None, *,
                       arity: int) -> THIRStubCallee | None:
    """The declared facts of a call to a method stub of a native type
    (`@native` record: list, dict, Span, ...) on `receiver`, the call's
    instantiated receiver type; None when the callee is no such stub or
    anything about the binding is open. The receiver binds parameter 0 at
    the passing its type has under the method's `@readonly` verdict; every
    method type parameter must be one of the type's own (a bounded one
    names the receiver's type argument it constrains)."""
    if (fi is None or receiver is None or _not_a_stub(fi, arity) or not fi.is_method
            or fi.name == "__init__" or fi.is_staticmethod or fi.is_classmethod or not fi.owning_type_qname):
        return None
    bare = unwrap_send_sync(native_container_subject(receiver))
    td = get_type_def(fi.owning_type_qname)
    record = td.record if td is not None else None
    if (record is None or not record.is_native or not isinstance(bare, NominalType)
            or bare.qualified_name() != fi.owning_type_qname or len(bare.type_args) != len(record.type_params)):
        return None
    bound: list[TpyType] = []
    for name in fi.type_params:
        if name not in record.type_params:
            return None
        if name in fi.type_param_bounds:
            argument = bare.type_args[record.type_params.index(name)]
            if not isinstance(argument, TpyType):
                return None
            bound.append(argument)
    types = (bare, *(p.type for p in fi.params))
    if any(_open_stub_type(t) for t in (*types, fi.return_type, *bound)):
        return None
    # A parameter passed as a view (a `str` key) cannot be written through.
    consts = (fi.is_readonly, *(const or t.param_passing(False) is ParamPassing.VIEW
                                for const, t in zip(_stub_param_consts(fi), types[1:], strict=True)))
    return THIRStubCallee(THIRStubIdentity(f"{fi.owning_type_qname}.{fi.name}", types),
                          THIRCallableSignature(types, fi.return_type, None, signature_passings(types, consts),
                                                return_representation(fi.return_type)),
                          _stub_contract(fi), consts, mutates_elements=fi.native_mutates == "elements",
                          receiver=True, bound_arguments=tuple(bound))


def with_method_stub(node: THIRMethodCall, fi: FunctionInfo | None) -> THIRMethodCall:
    """`node` carrying its callee's method stub facts, read against the
    receiver it lowered (`method_stub_callee`)."""
    stub = method_stub_callee(fi, node.receiver.result_type, arity=len(node.args))
    return node if stub is None else replace(node, stub_callee=stub)


def with_method_callee(node: THIRMethodCall, fi: FunctionInfo | None,
                       analyzer: 'SemanticAnalyzer') -> THIRMethodCall:
    """`node` carrying the user method it statically runs
    (`method_callee`), unless it binds a stub or renders through a form
    of its own (a template, a native symbol, a deref, a move or unwrap)."""
    if node.stub_callee is not None or not node.renders_plain_member_call:
        return node
    callee = method_callee(fi, node.receiver.result_type, analyzer, arity=len(node.args))
    return node if callee is None else replace(node, resolved_callee=callee)


def setitem_stub_callee(target: THIRExpr, analyzer: 'SemanticAnalyzer') -> THIRStubCallee | None:
    """The `__setitem__` stub a subscript write `c[k] = v` dispatches to on
    a native type: the receiver type's overload, substituted at its type
    arguments, whose index parameter is the written index's type (a
    subscript write never takes a slice overload). None when the receiver
    is no native type or no single overload takes the index."""
    if not isinstance(target, THIRSubscript):
        return None
    bare = unwrap_send_sync(native_container_subject(target.receiver.result_type))
    record = analyzer.registry.get_record_for_type(bare) if isinstance(bare, NominalType) else None
    if record is None or not record.is_native or len(bare.type_args) != len(record.type_params):
        return None
    subst = dict(zip(record.type_params, bare.type_args))
    key = unwrap_send_sync(unwrap_readonly(unwrap_ref_type(target.index.result_type)))
    matches = []
    for overload in record.methods.get("__setitem__", []):
        method = analyzer.type_ops.substitute_method_type_params(overload, subst) if subst else overload
        index = unwrap_readonly(unwrap_ref_type(method.params[0].type)) if len(method.params) == 2 else None
        # An int literal index converts to the fixed-int index parameter that holds it.
        if index is not None and (index == key or isinstance(key, IntLiteralType) and leaf_constant(index, key.value)):
            matches.append(method)
    return method_stub_callee(matches[0], bare, arity=2) if len(matches) == 1 else None
