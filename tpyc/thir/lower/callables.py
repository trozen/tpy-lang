"""Semantic callable facts retained at the selected lowering operation."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Callable

from ...compilation_context import get_current_compiler
from ...parse.nodes import SubscriptDunder, TpyExpr, TpyFunction, TpySubscript
from ...symbol_binding import SymbolKind
from ...type_def_registry import ParamPassing, get_type_def
from ...typesys import (
    AnyType, FunctionInfo, FunctionLinkage, NominalType, ReadonlyType,
    RecordInfo, RefType, TpyType, VoidType, contains_pending_leaf, contains_type_param, is_fn_type, is_protocol_type,
    accessor_role, body_method_info, declares_whole_result_following, property_getter_returns_storage_ref,
    receiver_neutral_return, return_representation, unwrap_readonly, unwrap_ref_type,
    unwrap_send_sync,
)
from ..nodes import (
    THIRBorrowedRecord, THIRCallableSignature, THIRExpr, THIRFunctionIdentity, THIRMethodCall, THIRResolvedCallee,
    THIRStubCallee, THIRStubContract, THIRStubIdentity, declared_param_type, receiver_param,
)
from ..param_contract import method_stub_param_consts, published_passing, signature_passings, stub_param_consts
from ..scalar_leaves import native_container_subject, record_owner
from .predicates import param_passing
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
                or root.special_handling or root.kwarg_name is not None
                or any(p.is_variadic or p.is_kwargs for p in root.params)
                or fi.return_type is None or fi.return_type != root.return_type
                or tuple(p.type for p in fi.params) != tuple(p.type for p in root.params)
                or arity is not None and arity != len(fi.params))


def _declared_return(declaration: TpyFunction) -> TpyType:
    return declaration.return_type if isinstance(declaration.return_type, TpyType) else VoidType()


def _declares(root: FunctionInfo, declaration: TpyFunction, accessor: str | None = None,
              twin: bool = False) -> bool:
    """Whether `declaration` is the body `root` binds: the same parameter
    names and declared types and the same return -- for an access-twin pair
    the pair's one receiver-neutral result (`receiver_neutral_return`),
    whichever clone `root` is. An accessor's FunctionInfo holds the value
    type of the reference its body returns, and a setter's name is its
    property's (Python has no differently named setter)."""
    mine, declared = root.return_type, _declared_return(declaration)
    if twin:
        mine = receiver_neutral_return(mine, declares_whole_result_following(root.root))
        declared = receiver_neutral_return(declared, declares_whole_result_following(declaration))
    returns = (unwrap_ref_type(mine) == unwrap_ref_type(declared)
               if accessor is not None else mine == declared)
    return (returns and (accessor != "fset" or root.property_name == root.name)
            and tuple(p.name for p in root.params) == tuple(n for n, _ in declaration.params)
            and tuple(declared_param_type(p.type) for p in root.params)
            == tuple(declared_param_type(t) for _, t in declaration.params))


def _closed_types(types: tuple[TpyType, ...]) -> bool:
    return not any(contains_type_param(t) or contains_pending_leaf(t) or _implicit_template_type(t)
                   or _stale_reference(t) for t in types)


def _closed_signature(fi: FunctionInfo) -> bool:
    return _closed_types((*(p.type for p in fi.params), fi.return_type))


def _identity_module(root: FunctionInfo, analyzer: 'SemanticAnalyzer') -> str:
    owner = root.originating_module
    return analyzer.ctx.cpp_module_name if owner == analyzer.ctx.module_name else owner


def _borrowed_result(returned: TpyType, analyzer: 'SemanticAnalyzer',
                     follows_receiver: bool = False) -> THIRBorrowedRecord | None:
    # Readonly exactly when the declared result is: the return type is the
    # contract. A follows-receiver callable publishes the result its
    # definition -- the const clone -- binds; a call derives its own access
    # from its receiver (`call_contract.bound_result`), since C++ picks the
    # clone by the receiver's constness.
    if not isinstance(returned, (RefType, ReadonlyType)):
        return None
    explicit = isinstance(unwrap_ref_type(returned), ReadonlyType)
    return borrowed_record(returned, follows_receiver or explicit, analyzer)


def callable_signature(types: tuple[TpyType, ...], return_type: TpyType,
                       borrowed_result: THIRBorrowedRecord | None,
                       passings: tuple[ParamPassing, ...] | None, analyzer: 'SemanticAnalyzer', *,
                       follows_receiver: bool = False, getter: bool = False) -> THIRCallableSignature:
    """The one constructor of a callable's contract, for a definition and
    for every call that binds one: the result's representation and, for a
    property getter (`getter`), whether it hands back its storage by
    reference are decided here from the return type."""
    return THIRCallableSignature(
        types, return_type, borrowed_result, passings, return_representation(return_type),
        result_follows_receiver=follows_receiver,
        storage_reference_result=getter and property_getter_returns_storage_ref(return_type))


def definition_signature(types: tuple[TpyType, ...], return_type: TpyType,
                         passings: tuple[ParamPassing, ...] | None, analyzer: 'SemanticAnalyzer', *,
                         getter: bool = False) -> THIRCallableSignature:
    """The contract a body lowers under when no call binds it as its unique
    target (`resolved_callee`): its own parameters and result as lowered."""
    return callable_signature(types, return_type, _borrowed_result(return_type, analyzer), passings,
                              analyzer, getter=getter)


def body_passings(declaration: TpyFunction, owning: FunctionInfo | None) -> tuple[ParamPassing, ...]:
    # The body's own THIRParam passings, so the definition and every call agree.
    return tuple(param_passing(n, t, declaration, owning) for n, t in declaration.params)


@dataclass(frozen=True)
class CallSignature:
    """The signature the callee a call binds publishes, resolved once where
    the call's node is built and read by its arguments' contracts; for a
    method (`receiver`) parameter 0 is the receiver."""
    signature: THIRCallableSignature
    receiver: bool = False

    def passing(self, index: int) -> ParamPassing | None:
        return published_passing(self.signature,
                                 index + 1 if self.receiver else index)


def call_signature(resolved: THIRResolvedCallee | None = None,
                   stub: THIRStubCallee | None = None, *,
                   receiver: bool = False) -> CallSignature | None:
    """The signature the call's callee facts publish: its stub's, else the
    resolved function's or method's; None for a callee neither describes."""
    if stub is not None:
        return CallSignature(stub.signature, stub.receiver)
    if resolved is not None:
        return CallSignature(resolved.signature, receiver)
    return None


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
                              callable_signature(types, fi.return_type, _borrowed_result(fi.return_type, analyzer),
                                                 body_passings(declaration, group[0]), analyzer))


def method_receiver(func: TpyFunction, self_type: TpyType | None, analyzer: 'SemanticAnalyzer', *,
                    stub: TpyFunction | None = None) -> THIRBorrowedRecord | None:
    """The receiver an instance method body of a plain record lowers under:
    `self` borrowed for the call at the body's finalized access (a property
    accessor and each clone of an `@auto_readonly` def included). None for
    a body that binds anything else -- a static or class method, an enum
    member, a lifecycle hook the generated special members run around, a
    consuming, generic, async or generator body, a per-stub specialization
    or a clone of an `auto_own` def -- and a constructor body, whose `self`
    is storage under construction."""
    if (self_type is None or stub is not None or not func.is_method or func.is_staticmethod
            or func.is_initializer or func.is_lifecycle_hook or func.is_consuming or func.type_params
            or func.is_async or func.is_generator
            or func.is_auto_own_borrowing_clone or func.is_auto_own_consuming_clone):
        return None
    receiver = borrowed_record(self_type, func.is_readonly, analyzer)
    info = analyzer.registry.get_record_for_type(receiver.type) if receiver is not None else None
    return receiver if info is not None and info.enum_companion_of is None else None


def _callable_body(owner: str | None, name: str, accessor: str | None) -> TpyFunction | None:
    compiler = get_current_compiler()
    return compiler.callable_body(owner, name, accessor) if compiler is not None else None


def _role_fis(info: RecordInfo, name: str, accessor: str | None) -> list[FunctionInfo]:
    """The registry FunctionInfos a call of the role binds: an accessor's
    from the property (sema pops accessors from the method table), each
    clone's own, a method's from its overload list."""
    if accessor is None:
        return info.get_method_overloads(name)
    prop = info.properties.get(name)
    return [] if prop is None else [fi for fi in prop.accessors if accessor_role(fi) == accessor]


def method_callee(fi: FunctionInfo | None, receiver: TpyType | None, analyzer: 'SemanticAnalyzer', *,
                  arity: int) -> THIRResolvedCallee | None:
    """The user record callable a call on `receiver` (the call's static
    receiver type) runs -- an ordinary instance method, a property getter
    or setter, or an `@auto_readonly` def -- as one identity and one
    signature whose parameter 0 is the receiver: the value its defining
    body publishes (`method_definition`): the record that declares the
    body -- the receiver's own or a struct-base ancestor's, which the
    receiver binds at. None unless the target is proven: a body-eligible
    callable (`method_receiver`) of the receiver's plain record or one of
    its ancestors, whose methods nothing overrides virtually (no `@dynamic`
    protocol in the receiver's hierarchy), with one defining body in its
    role (`Compiler.callable_body`), that body the one `fi` binds, no rendering of its own (a template -- a
    dunder's injected operator included -- or a native symbol) and a closed
    signature. The defining body of a getter and of an `@auto_readonly` def
    is the const clone of the pair: its receiver passes const and its
    result follows the receiver, whichever clone sema resolved; the result
    access of one call is derived from its receiver
    (`call_contract.bound_result`). Every other body's receiver passes at
    its readonly verdict, the one the call binds."""
    if fi is None or receiver is None:
        return None
    root = fi.root
    if (not root.is_method or root.is_consuming or root.native_function
            or not _bound_by_body(fi, root, arity) or not _closed_signature(fi)):
        return None
    actual = borrowed_record(receiver, root.is_readonly, analyzer)
    registry = analyzer.registry
    info = registry.get_record_for_type(actual.type) if actual is not None else None
    # One canonical spelling, so the definition and every call publish one value.
    if (info is None or actual.type != record_owner(info)
            # Asked of the receiver's record: the walk covers its ancestors.
            or next(registry.iter_dynamic_protocols(info), None) is not None):
        return None
    # The record whose body runs: the receiver's own, or the struct-base
    # ancestor that declares the method sema resolved.
    declaring = next((r for r in (info, *registry.iter_field_ancestors(info))
                      if r.qualified_name() == root.owning_type_qname), None)
    if declaring is None:
        return None
    owner = THIRBorrowedRecord(record_owner(declaring), actual.readonly)
    role = accessor_role(root)
    if not any(f.root is root for f in _role_fis(declaring, root.name, role)):
        return None
    body = _callable_body(root.owning_type_qname, root.name, role)
    defined = method_receiver(body, owner.type, analyzer) if body is not None else None
    # A getter is always a clone pair; a method's overload group is one only
    # when its single defining body is the pair's const clone.
    twin = defined is not None and body.auto_readonly_polarity is not None
    if (defined is None or twin != (role == "fget" or root.overloaded)
            or not twin and defined != owner or not _declares(root, body, role, twin)):
        return None
    ret = (receiver_neutral_return(_declared_return(body),
                                   declares_whole_result_following(body))
           if twin else _declared_return(body))
    types = (owner.type, *(p.type for p in fi.params))
    if not _closed_types((*types, ret)):
        return None
    return THIRResolvedCallee(
        THIRFunctionIdentity(_identity_module(root, analyzer), root.name, root.owning_type_qname, role),
        callable_signature(types, ret, _borrowed_result(ret, analyzer, twin),
                           (receiver_param(defined).passing,
                            *body_passings(body, body_method_info(declaring, body))),
                           analyzer, follows_receiver=twin, getter=role == "fget"))


def method_definition(func: TpyFunction, receiver: THIRBorrowedRecord | None,
                      analyzer: 'SemanticAnalyzer') -> tuple[THIRResolvedCallee | None, bool]:
    """The callee fact a method body publishes -- its calls' `method_callee`,
    when this body is the one they run -- and whether the body is the
    access twin: the mutable clone of an `@auto_readonly` def
    (`TpyFunction.clone_of`), which publishes the identity its const clone
    defines."""
    info = analyzer.registry.get_record_for_type(receiver.type) if receiver is not None else None
    if info is None:
        return None, False
    role = accessor_role(func)
    body = _callable_body(info.qualified_name(), func.name, role)
    twin = body is not None and func.clone_of is body
    fi = body_method_info(info, body) if body is not None else None
    if body is None or not (body is func or twin) or fi is None:
        return None, False
    callee = (method_callee(fi, receiver.type, analyzer, arity=len(fi.params))
              if method_receiver(body, receiver.type, analyzer) is not None else None)
    return callee, twin and callee is not None


def _not_a_stub(fi: FunctionInfo, arity: int) -> bool:
    """The bindings a stub's declaration does not describe alone: no bound
    symbol or template, a consuming or resumable callee, a callable value,
    a wrapper or special form the call adds, variadics and keyword packs."""
    return any((not (f.is_native_import or f.cpp_template is not None)
                or f.is_constructor or f.is_callable_value or f.is_async or f.is_generator
                or f.is_consuming or f.error_return_type is not None or f.special_handling
                or f.is_builtin_function or f.kwarg_name is not None
                or f.frame_captures is not None or f.is_property_getter or f.is_property_setter
                or any(p.is_variadic or p.is_kwargs for p in f.params))
               for f in (fi, fi.root)) or arity != len(fi.params) or fi.return_type is None


def stub_callee(fi: FunctionInfo | None, result_type: TpyType, analyzer: 'SemanticAnalyzer', *,
                arity: int) -> THIRStubCallee | None:
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
    consts = stub_param_consts(fi)
    return THIRStubCallee(THIRStubIdentity(name, types),
                          callable_signature(types, ret, None, signature_passings(types, consts), analyzer),
                          _stub_contract(fi), consts)


def _stub_contract(fi: FunctionInfo) -> THIRStubContract | None:
    return (THIRStubContract.PURE if fi.is_pure
            else THIRStubContract.TRANSIENT if fi.is_transient else None)


def method_stub_callee(fi: FunctionInfo | None, receiver: TpyType | None, analyzer: 'SemanticAnalyzer', *,
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
    # The declaration's type params: an instantiated view no longer lists
    # the class-shadowed ones its substitution bound.
    decl = fi.root
    for name in decl.type_params:
        if name not in record.type_params:
            return None
        if name in decl.type_param_bounds:
            argument = bare.type_args[record.type_params.index(name)]
            if not isinstance(argument, TpyType):
                return None
            bound.append(argument)
    types = (bare, *(p.type for p in fi.params))
    if any(_open_stub_type(t) for t in (*types, fi.return_type, *bound)):
        return None
    consts = method_stub_param_consts(fi, types)
    return THIRStubCallee(THIRStubIdentity(f"{fi.owning_type_qname}.{fi.name}", types),
                          callable_signature(types, fi.return_type, None, signature_passings(types, consts),
                                             analyzer),
                          _stub_contract(fi), consts, mutates_elements=fi.native_mutates == "elements",
                          receiver=True, bound_arguments=tuple(bound))


@dataclass(frozen=True)
class MethodCallees:
    """The callee facts of one method call, resolved once on its receiver
    as lowered: the method stub it binds, else the user method it
    statically runs (only for a plain member call)."""
    stub_callee: THIRStubCallee | None
    resolved_callee: THIRResolvedCallee | None


def method_signature(node: THIRMethodCall | MethodCallees) -> CallSignature | None:
    """What a method call's arguments read: the signature its callee facts
    publish, parameter 0 the receiver."""
    return call_signature(node.resolved_callee, node.stub_callee, receiver=True)


def method_callees(fi: FunctionInfo | None, receiver: TpyType | None, analyzer: 'SemanticAnalyzer', *,
                   arity: int, plain: bool) -> MethodCallees:
    """The callee facts of a call to `fi` on `receiver` with `arity`
    arguments: its method stub (`method_stub_callee`), else -- when the call
    renders a plain member call (`plain`) -- the user method it runs
    (`method_callee`)."""
    stub = method_stub_callee(fi, receiver, analyzer, arity=arity)
    resolved = (method_callee(fi, receiver, analyzer, arity=arity)
                if stub is None and plain else None)
    return MethodCallees(stub, resolved)


def with_method_callees(node: THIRMethodCall, fi: FunctionInfo | None, analyzer: 'SemanticAnalyzer',
                        receiver_readonly: Callable[[], bool], *, arity: int) -> THIRMethodCall:
    """`node` carrying its callee facts (`method_callees`), resolved on the
    receiver it lowered, and the access that receiver is emitted at
    (`receiver_readonly`, asked only for a resolved call). `arity` is the
    call's argument count: its arguments are lowered after, reading the
    signature the facts publish."""
    callees = method_callees(fi, node.receiver.result_type, analyzer, arity=arity,
                             plain=node.renders_plain_member_call)
    if callees.stub_callee is not None:
        return replace(node, stub_callee=callees.stub_callee)
    if callees.resolved_callee is None:
        return node
    # The access is the actual receiver's; parameter 0 may be an ancestor it binds at.
    receiver = unwrap_readonly(unwrap_ref_type(node.receiver.result_type))
    return replace(node, resolved_callee=callees.resolved_callee,
                   receiver_access=THIRBorrowedRecord(receiver, receiver_readonly()))


def setitem_stub_callee(sub: TpyExpr, receiver: THIRExpr,
                        analyzer: 'SemanticAnalyzer') -> THIRStubCallee | None:
    """The `__setitem__` stub a subscript write `c[k] = v` on `receiver`
    dispatches to on a native type (a container, an element-typed `@native`
    wrapper): the variant sema resolved for the store
    (`TpySubscript.dunder`), substituted at the receiver type's arguments.
    None when the receiver is no native type."""
    if not isinstance(sub, TpySubscript):
        return None
    setter = sub.dunder(SubscriptDunder.SET)
    bare = unwrap_send_sync(native_container_subject(receiver.result_type))
    record = analyzer.registry.get_record_for_type(bare) if isinstance(bare, NominalType) else None
    if (setter is None or record is None or not record.is_native
            or len(bare.type_args) != len(record.type_params)):
        return None
    subst = dict(zip(record.type_params, bare.type_args))
    method = (analyzer.type_ops.substitute_method_type_params(setter.root, subst)
              if subst else setter.root)
    return method_stub_callee(method, bare, analyzer, arity=2)
