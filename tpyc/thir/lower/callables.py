"""Semantic callable facts retained at the selected lowering operation."""

from typing import TYPE_CHECKING

from ...parse.nodes import TpyFunction
from ...symbol_binding import SymbolKind
from ...typesys import (
    AnyType, FunctionInfo, FunctionLinkage, RefType, TpyType, contains_pending_leaf, contains_type_param,
    is_fn_type, is_protocol_type, unwrap_readonly, unwrap_ref_type,
)
from ..nodes import THIRCallableSignature, THIRFunctionIdentity, THIRResolvedCallee

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
    return THIRResolvedCallee(THIRFunctionIdentity(module, root.name),
                              THIRCallableSignature(types, fi.return_type))
