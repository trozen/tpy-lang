"""Semantic callable facts retained at the selected lowering operation."""

from typing import TYPE_CHECKING

from ...parse.nodes import TpyFunction
from ...symbol_binding import SymbolKind
from ...type_def_registry import ParamPassing
from ...typesys import (
    AnyType, FunctionInfo, FunctionLinkage, NominalType, ReadonlyType, RefType, TpyType, contains_pending_leaf,
    contains_type_param, is_fn_type, is_protocol_type, return_representation, unwrap_readonly, unwrap_ref_type,
    unwrap_send_sync,
)
from ..nodes import (
    THIRCallableSignature, THIRFunctionIdentity, THIRResolvedCallee, THIRStubCallee, THIRStubContract,
    THIRStubIdentity,
)
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


def stub_callee(fi: FunctionInfo | None, result_type: TpyType, *, arity: int) -> THIRStubCallee | None:
    """The declared facts of a call to a `@native` / `@cpp_template` free
    function or builtin-type initializer, or None when the callee is not
    such a stub or anything about the binding is open (a type parameter,
    variadics, keyword packs, a wrapper the call adds)."""
    if fi is None:
        return None
    initializer = fi.is_method and fi.name == "__init__" and not fi.is_staticmethod
    if (not (fi.is_native_import or fi.cpp_template is not None)
            or (fi.is_method and not initializer)
            or fi.is_constructor or fi.is_callable_value or fi.is_async or fi.is_generator
            or fi.is_consuming or fi.error_return_type is not None or fi.special_handling
            or fi.is_builtin_function or fi.inline_body is not None or fi.kwarg_name is not None
            or fi.frame_captures is not None
            or any(p.is_variadic or p.is_kwargs for p in fi.params)
            or arity != len(fi.params) or fi.return_type is None):
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
    contract = (THIRStubContract.PURE if fi.is_pure
                else THIRStubContract.TRANSIENT if fi.is_transient else None)
    return THIRStubCallee(THIRStubIdentity(name, types),
                          THIRCallableSignature(types, ret, None, signature_passings(types, consts),
                                                return_representation(ret)),
                          contract, consts)
