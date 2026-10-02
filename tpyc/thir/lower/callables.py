"""Semantic callable facts retained at the selected lowering operation."""

from dataclasses import replace
from typing import TYPE_CHECKING

from ...parse.nodes import TpyFunction
from ...symbol_binding import SymbolKind
from ...type_def_registry import ParamPassing, get_type_def
from ...typesys import (
    AnyType, FunctionInfo, FunctionLinkage, IntLiteralType, NominalType, ReadonlyType, RefType, TpyType,
    contains_pending_leaf, contains_type_param, is_fn_type, is_protocol_type, return_representation, unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ..nodes import (
    THIRCallableSignature, THIRExpr, THIRFunctionIdentity, THIRMethodCall, THIRResolvedCallee, THIRStubCallee,
    THIRStubContract, THIRStubIdentity, THIRSubscript,
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


def _parameter_type(typ: TpyType) -> TpyType:
    return unwrap_ref_type(unwrap_readonly(unwrap_ref_type(typ)))


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


def resolved_callee(fi: FunctionInfo | None, analyzer: 'SemanticAnalyzer',
                    *, arity: int | None = None) -> THIRResolvedCallee | None:
    if fi is None:
        return None
    root = fi.root
    if (root.originating_module is None or root.type_params or root.declaration is None
            or root.is_method or root.is_staticmethod or root.is_classmethod
            or root.is_constructor or root.is_callable_value or root.frame_captures is not None
            or root.is_async or root.is_generator or root.error_return_type is not None
            or root.native_name is not None or root.cpp_template is not None
            or root.linkage is not FunctionLinkage.DEFAULT or root.is_builtin_function
            or root.special_handling or root.inline_body is not None or root.kwarg_name is not None
            or any(p.is_variadic or p.is_kwargs for p in root.params)
            or fi.return_type is None or fi.return_type != root.return_type
            or tuple(p.type for p in fi.params) != tuple(p.type for p in root.params)):
        return None
    declaration = root.declaration
    if (root.return_type != declaration.return_type
            or tuple(p.name for p in root.params) != tuple(n for n, _ in declaration.params)
            or tuple(_parameter_type(p.type) for p in root.params)
            != tuple(_parameter_type(t) for _, t in declaration.params)):
        return None
    if arity is not None and arity != len(fi.params):
        return None
    owner = root.originating_module
    module = analyzer.ctx.cpp_module_name if owner == analyzer.ctx.module_name else owner
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
    types = tuple(p.type for p in fi.params)
    if any(contains_type_param(t) or contains_pending_leaf(t) or _implicit_template_type(t) or _stale_reference(t)
           for t in (*types, fi.return_type)):
        return None
    result = (borrowed_record(fi.return_type,
                             declaration.is_readonly or isinstance(unwrap_ref_type(fi.return_type), ReadonlyType),
                             analyzer) if isinstance(fi.return_type, (RefType, ReadonlyType)) else None)
    # The declaration's own types and verdict, as its THIRParams read them.
    verdict = fi.const_borrow_params
    passings = (None if verdict is None else
                signature_passings(tuple(t for _, t in declaration.params),
                                   tuple(i in verdict for i in range(len(declaration.params)))))
    return THIRResolvedCallee(THIRFunctionIdentity(module, root.name),
                              THIRCallableSignature(types, fi.return_type, result, passings,
                                                    return_representation(fi.return_type)))


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
                          _stub_contract(fi), consts, preserves_refs=fi.native_preserves_refs,
                          receiver=True, bound_arguments=tuple(bound))


def with_method_stub(node: THIRMethodCall, fi: FunctionInfo | None) -> THIRMethodCall:
    """`node` carrying its callee's method stub facts, read against the
    receiver it lowered (`method_stub_callee`)."""
    stub = method_stub_callee(fi, node.receiver.result_type, arity=len(node.args))
    return node if stub is None else replace(node, stub_callee=stub)


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
