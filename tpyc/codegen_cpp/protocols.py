"""
TurboPython Protocol/Concept Code Generation

C++20 concept generation from TurboPython protocols and template header utilities.
"""

from __future__ import annotations
from collections import namedtuple
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, NominalType, TypeParamRef, TypeParamKind, ReadonlyType, VoidType, SelfType,
    OptionalType, UnionType, OwnType, MethodSignature, is_protocol_type,
    unwrap_readonly, unwrap_own, is_protocol_union, protocol_union_protocols,
    protocol_union_has_none, unwrap_ref_type,
)
from ..parse import TpyProtocol, TpyRecord
from .context import INDENT, DUNDER_TO_BINARY_OP, CodeGenError, qualified_cpp_name
from ..type_def_registry import is_str_type, protocol_info_of, is_subtype
from ..symbol_binding import lookup_imported, SymbolKind

if TYPE_CHECKING:
    from .context import CodeGenContext

# Unified protocol parameter info: name, list of protocol types (1+), nullable flag
ProtocolParamInfo = namedtuple('ProtocolParamInfo', ['name', 'protocols', 'has_none'])

# Adapter/RefAdapter template parameter name for the concrete impl type.
# `_GENERIC` is used inside the partial-spec template that's parameterized
# over the protocol's own type params; it uses the reserved `__tpy_` prefix
# to avoid colliding with any user-chosen identifier. `_NON_GENERIC` is the
# historical name kept for snapshot stability when the protocol has no type
# params.
_ADAPTER_IMPL_GENERIC = "__tpy_Impl"
_ADAPTER_IMPL_NON_GENERIC = "T"


def protocol_param_template_name(pname: str) -> str:
    """Deduced template-argument name for a static-protocol param.

    Shared `T_<pname>` spelling for the resumable-coro sites that must agree
    on one name: the coro template-header declaration (`_protocol_template_parts`),
    the captured-param frame field (`_classify_params`), and a for-loop over
    the param (`_analyze_for_strategy`). The raw (unescaped) `pname` is used
    deliberately so all three agree even when `pname` is a C++ keyword --
    escaping here would diverge from the declaration. (The non-coro
    template-header path in this module still inlines the spelling; those
    sites are independent of the resumable frame.)
    """
    return f"T_{pname}"


class ProtocolGenerator:
    """Generates C++20 concepts from TurboPython protocols."""

    def __init__(self, ctx: CodeGenContext):
        self.ctx = ctx

    def resolve_type_for_codegen(self, typ: TpyType) -> TpyType:
        """Resolve a type, setting is_protocol flag if it's actually a protocol.

        During parsing, some types may be classified as records when they're
        actually protocols (e.g., imported protocols). This method fixes that for codegen.
        """
        if isinstance(typ, NominalType) and not typ.is_protocol:
            # Check if this is actually a protocol. Probe with the protocol
            # flag set so `protocol_info_of`'s qname-derived TypeDef lookup
            # matches the authoritative `{module}.{name}` key.
            probe = typ.with_protocol_flag(is_protocol=True)
            if protocol_info_of(probe) is not None:
                return probe
        return typ

    def is_static_protocol_param(self, typ: TpyType) -> bool:
        """Check if a type is a static protocol parameter (single or union, optional or not).

        Returns True for:
        - NominalType that is a static protocol
        - OptionalType(static protocol)
        - UnionType of 2+ static protocols (optionally with None)
        Excludes @dynamic protocols.
        """
        unwrapped = unwrap_own(unwrap_readonly(unwrap_ref_type(typ)))
        # Optional[Protocol]
        if isinstance(unwrapped, OptionalType):
            resolved = self.resolve_type_for_codegen(unwrapped.inner)
            if is_protocol_type(resolved):
                protocol_info = protocol_info_of(resolved)
                if protocol_info and protocol_info.is_dynamic:
                    return False
                return True
            return False
        # Protocol union (2+ protocols, optionally with None)
        if isinstance(unwrapped, UnionType):
            resolved_members = tuple(self.resolve_type_for_codegen(m) for m in unwrapped.members)
            resolved_union = UnionType(resolved_members)
            return is_protocol_union(resolved_union)
        # Single bare protocol
        resolved = self.resolve_type_for_codegen(unwrapped)
        if is_protocol_type(resolved):
            protocol_info = protocol_info_of(resolved)
            if protocol_info and protocol_info.is_dynamic:
                return False
            return True
        return False

    def get_all_protocol_params(
        self, params: list[tuple[str, TpyType]]
    ) -> list[ProtocolParamInfo]:
        """Get unified list of all static protocol parameters.

        Handles single protocols (bare or Optional) and protocol unions.
        Returns ProtocolParamInfo(name, protocols, has_none) for each.
        """
        result: list[ProtocolParamInfo] = []
        for pname, ptype in params:
            unwrapped = unwrap_own(unwrap_readonly(unwrap_ref_type(ptype)))

            # Optional[Protocol] -- single protocol, nullable
            if isinstance(unwrapped, OptionalType):
                resolved = self.resolve_type_for_codegen(unwrapped.inner)
                if is_protocol_type(resolved) and isinstance(resolved, NominalType):
                    protocol_info = protocol_info_of(resolved)
                    if protocol_info and protocol_info.is_dynamic:
                        continue
                    result.append(ProtocolParamInfo(pname, [resolved], True))
                continue

            # Protocol union (2+ protocols, optionally with None)
            if isinstance(unwrapped, UnionType):
                resolved_members = tuple(self.resolve_type_for_codegen(m) for m in unwrapped.members)
                resolved_union = UnionType(resolved_members)
                if is_protocol_union(resolved_union):
                    protos = [m for m in protocol_union_protocols(resolved_union) if isinstance(m, NominalType)]
                    has_none = protocol_union_has_none(resolved_union)
                    result.append(ProtocolParamInfo(pname, protos, has_none))
                continue

            # Single bare protocol
            resolved = self.resolve_type_for_codegen(unwrapped)
            if is_protocol_type(resolved) and isinstance(resolved, NominalType):
                protocol_info = protocol_info_of(resolved)
                if protocol_info and protocol_info.is_dynamic:
                    continue
                result.append(ProtocolParamInfo(pname, [resolved], False))

        return result

    def get_concept_name(self, protocol: NominalType) -> str:
        """Get the C++ concept name for a protocol type.

        For @dynamic protocols the concept is renamed to __{Name}_Concept__
        so the clean name is free for the base class struct.
        Non-dynamic protocols keep their original name.
        """
        # Unified lookup via registry
        protocol_info = protocol_info_of(protocol)
        if protocol_info and protocol_info.cpp_concept:
            return protocol_info.cpp_concept
        is_dynamic = protocol_info is not None and protocol_info.is_dynamic
        # Check if this is an imported protocol from another user module
        qual = lookup_imported(
            self.ctx.analyzer.ctx.module_attributes, protocol.name,
            SymbolKind.PROTOCOL_STATIC, SymbolKind.PROTOCOL_DYNAMIC)
        if qual is not None:
            source_module, original_name = qual
            cpp_name = f"__{original_name}_Concept__" if is_dynamic else original_name
            return qualified_cpp_name(source_module, cpp_name)
        # Protocol from a bare-imported module (import typing + typing.Sized)
        if protocol_info and protocol_info.module and protocol_info.module in self.ctx.user_module_imports:
            cpp_name = f"__{protocol.name}_Concept__" if is_dynamic else protocol.name
            return qualified_cpp_name(protocol_info.module, cpp_name)
        # Protocol from an implicit stdlib module (typing, tpy) -- needs qualified name
        if protocol_info and protocol_info.module and protocol_info.module in self.ctx.all_user_modules:
            cpp_name = f"__{protocol.name}_Concept__" if is_dynamic else protocol.name
            return qualified_cpp_name(protocol_info.module, cpp_name)
        # Protocol defined in the current module (or unknown origin fallback)
        return f"__{protocol.name}_Concept__" if is_dynamic else protocol.name

    def get_dynamic_base_name(self, protocol: NominalType) -> str:
        """Get the (possibly qualified) C++ base class name for a @dynamic protocol.

        Type args render through `t.to_cpp()`; for unresolved TypeParamRef
        args (inside a nested template emission) this is the bare name `T`,
        so the same helper covers both call-site instantiations
        (`Awaitable<int32_t>`) and nested-template references (`Awaitable<T>`).

        For protocols that are both @dynamic and @native (cpp_concept set),
        the C++ class name comes from the @native annotation -- the abstract
        base lives in a runtime header rather than codegen output. Used by
        Throwable (`@native("tpy::Throwable") @dynamic`) to bridge to
        `runtime/cpp/include/tpy/throwable.hpp::tpy::Throwable`.
        """
        protocol_info = protocol_info_of(protocol)
        if protocol_info and protocol_info.cpp_concept:
            base = protocol_info.cpp_concept
            if protocol.type_args:
                args_cpp = ", ".join(t.to_cpp() for t in protocol.type_args)
                return f"{base}<{args_cpp}>"
            return base
        name = protocol.name
        qual = lookup_imported(
            self.ctx.analyzer.ctx.module_attributes, name,
            SymbolKind.PROTOCOL_STATIC, SymbolKind.PROTOCOL_DYNAMIC)
        if qual is not None:
            source_module, original_name = qual
            base = qualified_cpp_name(source_module, original_name)
        else:
            base = self._qualify_protocol_via_qname(protocol) or name
        if protocol.type_args:
            args_cpp = ", ".join(t.to_cpp() for t in protocol.type_args)
            return f"{base}<{args_cpp}>"
        return base

    def _qualify_protocol_via_qname(self, protocol: NominalType) -> str | None:
        """Fallback qualification for protocols not bound in the current
        module's attributes -- e.g. implicit-stdlib protocols referenced
        only through a sema-synthesized type like `Cancellable[T]`
        constructed by `make_cancellable` for async-def returns. Skips
        when the protocol lives in the current module (the bare local
        name is correct there)."""
        qname = protocol._module_qname
        if not qname or "." not in qname:
            return None
        source_module, original_name = qname.rsplit(".", 1)
        if source_module == self.ctx.analyzer.ctx.module_name:
            return None
        return qualified_cpp_name(source_module, original_name)

    def get_dynamic_adapter_type(self, protocol: NominalType, concrete_cpp: str) -> str:
        """Get the full C++ type for an owning adapter: ::tpy::Adapter<Base, Concrete>."""
        base = self.get_dynamic_base_name(protocol)
        return f"::tpy::Adapter<{base}, {concrete_cpp}>"

    def get_dynamic_ref_adapter_type(self, protocol: NominalType, concrete_cpp: str) -> str:
        """Get the full C++ type for a ref adapter: ::tpy::RefAdapter<Base, Concrete>."""
        base = self.get_dynamic_base_name(protocol)
        return f"::tpy::RefAdapter<{base}, {concrete_cpp}>"

    def dynamic_narrow_cast_rhs(
        self, cpp_type: str, check_type: TpyType, source_inner: 'TpyType | None',
        cast_arg: str, *, is_const: bool,
    ) -> str:
        """Build the pointer-producing RHS that narrows `cast_arg` (a
        `[const] P*` payload pointer) to a `[const] Sub*` for isinstance
        dispatch. The result is used as the if-init / extraction-local
        initializer and, against nullptr, as the branch condition.

        An inheritance conformer (or a polymorphic-class root, which has no
        structural conformers) uses `dynamic_cast`. A STRUCTURAL conformer of
        a @dynamic protocol routes through `tpy::dyn_adapter_cast`, which
        unwraps the `Adapter`/`RefAdapter` the structural value sits inside --
        a plain `dynamic_cast<Sub*>` would always fail there. The structural
        overload infers const-ness from `cast_arg`, so `is_const` only shapes
        the `dynamic_cast` form."""
        if (is_protocol_type(source_inner)
                and isinstance(check_type, NominalType)
                and not self.directly_implements_dynamic(check_type, source_inner)):
            base = self.get_dynamic_base_name(source_inner)
            return f"::tpy::dyn_adapter_cast<{base}, {cpp_type}>({cast_arg})"
        const_pfx = "const " if is_const else ""
        return f"dynamic_cast<{const_pfx}{cpp_type}*>({cast_arg})"

    def dyn_protocol_forward_ok(self, source: TpyType, target: TpyType) -> bool:
        """True if `source` (@dynamic protocol) can be forwarded as `target`
        (@dynamic protocol) without an Adapter wrap -- same protocol (joint
        qualified_name + type_args check, so generic instantiations like
        `Container[int]` and `Container[str]` stay distinct) or inheriting peer.
        """
        if not (isinstance(source, NominalType) and isinstance(target, NominalType)):
            return False
        if (source.qualified_name() == target.qualified_name()
                and source.type_args == target.type_args):
            return True
        return is_subtype(
            self.ctx.analyzer.registry.scan_by_short_name(source.name), target.name
        )

    def directly_implements_dynamic(self, concrete_type: TpyType, protocol: NominalType) -> bool:
        """Check if concrete_type inherits a @dynamic protocol (directly or transitively).

        Returns True when the record explicitly implements a @dynamic protocol that
        is (or transitively inherits from) `protocol`. This means the C++ struct
        inherits the protocol base class through the inheritance chain and no adapter
        wrapping is needed.

        Match is by short name; type_args are ignored. Cross-instantiation
        mismatches (e.g. assigning `Container[Int32]` into `Container[str]`)
        are already rejected by sema before this helper runs.

        The ``is_dynamic`` filter is applied at the IMPLEMENTED-protocol level
        (chain root), not at the target. A non-@dynamic protocol contributes no
        C++ base, so a chain rooted in a non-@dynamic implemented protocol
        cannot make ``concrete_type`` C++-inherit anything -- even if a
        @dynamic ancestor sits further up the chain. This differs from
        ``sema/type_ops._inherits_protocol`` (which filters on the target,
        not the root); the closure cached on ``RecordInfo.transitive_supertypes``
        encodes the unfiltered closure used by the latter, so this helper keeps
        an explicit per-element walk and uses ``is_subtype`` only for the
        per-protocol ancestor step.

        @native records are excluded: their C++ representation is opaque to
        codegen (the struct is hand-written elsewhere), so even when the TPy
        declaration claims `class NativeRec(SomeDynProto)`, the C++ struct
        almost certainly does NOT inherit the codegen-emitted protocol base.
        Routing through Adapter is the only safe lowering for those types.
        Example: `BaseException` inherits `Throwable` at the TPy level for
        polymorphism dispatch on `Optional[BaseException]`, but
        `::tpy::BaseException` in core.hpp does not inherit `tpystd::tpy::Throwable`.
        """
        if not isinstance(concrete_type, NominalType) or not concrete_type.is_user_record:
            return False
        record_info = self.ctx.analyzer.registry.get_record(concrete_type.name)
        if record_info is None or record_info.is_native:
            return False
        proto_name = protocol.name
        for p in record_info.implemented_protocols:
            pi = protocol_info_of(p)
            if pi is None or not pi.is_dynamic:
                continue
            if p.name == proto_name or is_subtype(pi, proto_name):
                return True
        return False

    def gen_record_template_parts(
        self,
        type_params: list[str],
        type_param_bounds: dict[str, NominalType],
        type_param_kinds: list[TypeParamKind] | None = None
    ) -> list[str]:
        """Render the per-param parts of a generic record's template header.

        For unbounded TYPE params: `typename T`
        For protocol-bounded TYPE params: `Comparable T` / `Sequence<int32_t> T`
        For INT params: `std::size_t N`
        For class / type-param bounds (not C++ concepts): `typename T` -- the
        TPy-side subtype bound is enforced at the call site (satisfies_bound).
        """
        template_parts = []
        for i, tp in enumerate(type_params):
            kind = type_param_kinds[i] if type_param_kinds and i < len(type_param_kinds) else TypeParamKind.TYPE
            if kind == TypeParamKind.INT:
                template_parts.append(f"std::size_t {tp}")
            elif tp in type_param_bounds and is_protocol_type(type_param_bounds[tp]):
                bound = type_param_bounds[tp]
                concept_name = self.get_concept_name(bound)
                if bound.type_args:
                    type_args_cpp = ", ".join(t.to_cpp() for t in bound.type_args)
                    template_parts.append(f"{concept_name}<{type_args_cpp}> {tp}")
                else:
                    template_parts.append(f"{concept_name} {tp}")
            else:
                template_parts.append(f"typename {tp}")
        return template_parts

    def gen_record_template_header(
        self,
        type_params: list[str],
        type_param_bounds: dict[str, NominalType],
        type_param_kinds: list[TypeParamKind] | None = None
    ) -> str:
        parts = self.gen_record_template_parts(type_params, type_param_bounds, type_param_kinds)
        return f"template<{', '.join(parts)}>"

    def _concept_constraint(self, pname: str, ptype: NominalType) -> str:
        """Build the concept constraint expression for a protocol template param.

        Returns e.g. '::tpy::Sized<T_items>' or '::tpy::Sequence<T_items, int32_t>'.
        """
        concept_name = self.get_concept_name(ptype)
        if ptype.type_args:
            type_args_cpp = ", ".join(t.to_cpp() for t in ptype.type_args)
            return f"{concept_name}<T_{pname}, {type_args_cpp}>"
        return f"{concept_name}<T_{pname}>"

    def gen_combined_template_header(
        self,
        type_params: list[str],
        protocol_params: list[ProtocolParamInfo],
        type_param_bounds: dict[str, NominalType] | None = None,
        *, emit_defaults: bool = True,
    ) -> str:
        """Generate template header combining type parameters and concept constraints.

        For generic functions: template<typename T>
        For generic functions with protocols: template<typename T, ::tpy::Sized T_items>
        For bounded type params: template<Comparable T>
        For protocol unions: template<typename T_items> requires (A<T_items> || B<T_items>)
        """
        template_parts = []

        # Add type parameters for generic functions (with optional bounds)
        for tp in type_params:
            if (type_param_bounds and tp in type_param_bounds
                    and is_protocol_type(type_param_bounds[tp])):
                bound = type_param_bounds[tp]
                concept_name = self.get_concept_name(bound)
                if bound.type_args:
                    type_args_cpp = ", ".join(t.to_cpp() for t in bound.type_args)
                    template_parts.append(f"{concept_name}<{type_args_cpp}> {tp}")
                else:
                    template_parts.append(f"{concept_name} {tp}")
            else:
                # A class or type-param bound (`U: Animal` / `U: T`) is not a
                # C++ concept; emit an unconstrained param. The subtype bound
                # is enforced at the TPy call site (satisfies_bound).
                template_parts.append(f"typename {tp}")

        # Add protocol params with concept constraints
        requires_parts = []
        for info in protocol_params:
            pname = info.name
            # Single required protocol: clean `Concept T_x` syntax
            if len(info.protocols) == 1 and not info.has_none:
                ptype = info.protocols[0]
                concept_name = self.get_concept_name(ptype)
                if ptype.type_args:
                    type_args_cpp = ", ".join(t.to_cpp() for t in ptype.type_args)
                    template_parts.append(f"{concept_name}<{type_args_cpp}> T_{pname}")
                else:
                    template_parts.append(f"{concept_name} T_{pname}")
            else:
                # Multiple protocols or nullable: typename + requires clause.
                # `nullptr_t` here is the *nullable-static-protocol-param*
                # sentinel (e.g. `def f(p: Sized | None)` -- the param accepts
                # nullptr at the C++ level so `f(None)` lowers to a call with
                # no arg). This is distinct from `None` as a value-bearing
                # generic type argument (which lowers to std::monostate via
                # NoneType.to_cpp): the protocol-template default is matched
                # by passing literal `nullptr` and gated by `if constexpr`
                # bodies, never stored as a value.
                default_part = " = std::nullptr_t" if info.has_none and emit_defaults else ""
                template_parts.append(f"typename T_{pname}{default_part}")
                constraints = []
                if info.has_none:
                    constraints.append(f"std::same_as<T_{pname}, std::nullptr_t>")
                for proto in info.protocols:
                    constraints.append(self._concept_constraint(pname, proto))
                requires_parts.append(f"({' || '.join(constraints)})")

        if not template_parts:
            return ""
        result = f"template<{', '.join(template_parts)}>\n"
        if requires_parts:
            result += f"  requires {' && '.join(requires_parts)}\n"
        return result

    def collect_concept_methods(self, protocol_name: str) -> list[MethodSignature]:
        return self.ctx.analyzer.protocols.collect_protocol_methods(protocol_name)

    def is_protocol_const(self, protocol_name: str) -> bool:
        """Check if a protocol is functionally const (all methods are readonly).

        True when the protocol-level is_readonly flag is set, or when every
        method (including inherited ones) is individually @readonly.
        """
        pi = self.ctx.analyzer.registry.scan_by_short_name(protocol_name)
        if pi is None:
            return False
        if pi.is_readonly:
            return True
        all_methods = self.collect_concept_methods(protocol_name)
        return bool(all_methods) and all(m.is_readonly for m in all_methods)

    def collect_concept_fields(self, protocol_name: str) -> list[tuple[str, TpyType]]:
        return self.ctx.analyzer.protocols.collect_protocol_fields(protocol_name)

    def gen_concept_decl(self, out: TextIO, protocol: TpyProtocol) -> None:
        """Generate a C++20 concept for a user-defined protocol.

        SelfType in method signatures is rendered as 'T' (the template parameter).
        This allows the concept to check that e.g., T + T -> T.

        For generic protocols like Container[T], we generate:
        - template<typename T, typename _T0> where T is the checked type and _T0 is the protocol's T
        - Usage: Container<int32_t> V means V must satisfy Container<V, int32_t>

        For protocol inheritance, includes requirements from all parent protocols.

        Protocols with cpp_concept (e.g. @native marker protocols) are backed by
        runtime C++ concepts and don't need generated concept definitions.

        Returns True if a concept was emitted, False if skipped.
        """
        # Skip protocols backed by runtime C++ concepts
        protocol_info = self.ctx.analyzer.registry.scan_by_short_name(protocol.name)
        if protocol_info and protocol_info.cpp_concept:
            return False
        # For @dynamic protocols, forward-declare the abstract base struct so
        # the concept can mention the protocol's own type in method signatures
        # (e.g. `clone(self) -> Own[Self]` lowering to `std::unique_ptr<Proto>`
        # inside a convertible_to constraint). The base struct is fully defined
        # by gen_dynamic_base_class below; concept constraints only require
        # the type to be complete at instantiation time.
        if protocol.is_dynamic:
            self.emit_dynamic_base_forward_decl(out, protocol)
        self.ctx.emit_preceding_comments(out, protocol.loc)
        self.ctx.emit_source_comment(out, protocol.loc)
        # Build template params: T (checked type) + one for each protocol type param
        template_params = ["typename T"]
        type_param_map: dict[str, str] = {}  # Protocol type param -> C++ template param
        for i, tp in enumerate(protocol.type_params):
            cpp_param = f"_T{i}"
            template_params.append(f"typename {cpp_param}")
            type_param_map[tp] = cpp_param

        out.write(f"template<{', '.join(template_params)}>\n")

        # Collect all methods including inherited ones
        all_methods = self.collect_concept_methods(protocol.name)
        all_fields_for_decl = self.collect_concept_fields(protocol.name)

        # @dynamic protocols use __{Name}_Concept__ so the clean name is
        # free for the base class struct
        concept_name = (f"__{protocol.name}_Concept__"
                        if protocol.is_dynamic else protocol.name)
        # Marker (no methods, no fields) -> trivially-satisfied concept.
        # An empty `requires(T& t) { }` body is ill-formed in C++20 (GCC/Clang
        # reject it), so emit `= true` instead. Used by markerless @dynamic
        # protocols that act as phylum tags (e.g. Throwable for the exception
        # tree's polymorphism predicate).
        if not all_methods and not all_fields_for_decl:
            out.write(f"concept {concept_name} = true;\n")
            return True
        if self.is_protocol_const(protocol.name):
            out.write(f"concept {concept_name} = requires(const T& t) {{\n")
        else:
            out.write(f"concept {concept_name} = requires(T& t) {{\n")


        def subst_type(typ: TpyType) -> TpyType:
            """Substitute protocol type params recursively in a type."""
            if isinstance(typ, TypeParamRef) and typ.name in type_param_map:
                # Return a TypeParamRef with the mapped name
                return TypeParamRef(type_param_map[typ.name])
            return typ.map_inner_types(subst_type)

        def subst_to_cpp(typ: TpyType) -> str:
            """Convert type to C++, substituting protocol type params."""
            return subst_type(typ).to_cpp()

        def concept_type_cpp(typ: TpyType) -> str:
            """C++ type for concept constraint, with covariant str handling.

            Protocol -> str uses string_view so implementations can return
            any string type (str, StrView, String).
            """
            resolved = subst_type(typ)
            if is_str_type(resolved):
                return "std::string_view"
            return resolved.to_cpp()

        for method_sig in all_methods:
            # Generate requirement for each method.
            # SelfType.to_cpp() returns "T", so this handles Self -> T substitution.
            # Protocol type params (e.g., T in Container[T]) are mapped to _T0, _T1, etc.
            ret_cpp = concept_type_cpp(method_sig.return_type)
            # Skip return type check when the C++ return doesn't match the
            # protocol signature: protocol returns (Iterator[T] -- can't
            # convertible_to a concept), __next__ (C++ returns
            # std::expected<T, StopIteration>, not T), and Self on __iter__
            # (just checks the expression is valid, not the return type).
            skip_return_check = (is_protocol_type(method_sig.return_type)
                                 or method_sig.name == "__next__"
                                 or (isinstance(method_sig.return_type, SelfType)
                                     and method_sig.name not in DUNDER_TO_BINARY_OP))

            # Build the C++ call expression from DUNDER_CPP_TEMPLATES, binary
            # operator map, or direct member call (in that priority order).
            from ..modules import get_dunder_cpp_template
            cpp_tmpl = get_dunder_cpp_template(method_sig.name)
            if cpp_tmpl is not None:
                # Expand template: {self} -> t, {0}/{1}/... -> std::declval<ParamCpp>()
                call_expr = cpp_tmpl.replace("{self}", "t")
                for i, (_, ptype) in enumerate(method_sig.params):
                    call_expr = call_expr.replace(f"{{{i}}}", f"std::declval<{subst_to_cpp(ptype)}>()")
            elif method_sig.name in DUNDER_TO_BINARY_OP and len(method_sig.params) == 1:
                cpp_op = DUNDER_TO_BINARY_OP[method_sig.name]
                _, ptype = method_sig.params[0]
                call_expr = f"t {cpp_op} std::declval<{subst_to_cpp(ptype)}>()"
            else:
                param_exprs = [f"std::declval<{subst_to_cpp(ptype)}>()" for _, ptype in method_sig.params]
                call_expr = f"t.{method_sig.name}({', '.join(param_exprs)})"

            if skip_return_check:
                out.write(f"{INDENT}{call_expr};\n")
            else:
                out.write(f"{INDENT}{{ {call_expr} }} -> std::convertible_to<{ret_cpp}>;\n")

        # Collect all fields including inherited ones
        all_fields = self.collect_concept_fields(protocol.name)
        for field_name, field_type in all_fields:
            field_cpp = concept_type_cpp(field_type)
            out.write(f"{INDENT}{{ t.{field_name} }} -> std::convertible_to<{field_cpp}>;\n")

        out.write("};\n")
        return True

    def gen_dynamic_base_class(self, out: TextIO, protocol: TpyProtocol) -> None:
        """Generate abstract base class for a @dynamic protocol.

        Emitted inside the user namespace. The base class gets the protocol's
        clean name (e.g., struct Pet) so C++ interop code can use it directly.
        When this protocol extends other @dynamic protocols, the base class
        inherits from their bases (e.g., NamedPet : Pet).

        For generic @dynamic protocols (e.g. Awaitable[T]), the base is a
        class template parameterized on the protocol's type params; method
        signatures referring to those params render via TypeParamRef.to_cpp()
        which already returns the bare name ("T").

        Note on naming: the *concept* (emitted by gen_concept_decl) renames
        protocol type params to `_T0`, `_T1`, ... because its first slot is
        already taken by the checked type `T`. The base class here uses the
        bare names directly. The two schemes round-trip via the C++20
        abbreviated constraint form -- see gen_dynamic_adapter_specs for the
        construction that bridges them.
        """
        protocol_info = self.ctx.analyzer.registry.scan_by_short_name(protocol.name)
        if protocol_info is None:
            return

        all_methods = self.collect_concept_methods(protocol.name)

        # Collect @dynamic parent bases and their already-declared methods
        dynamic_parent_bases: list[str] = []
        parent_dynamic_methods: set[str] = set()
        for parent in protocol_info.parent_protocols:
            parent_info = self.ctx.analyzer.registry.scan_by_short_name(parent.name)
            if parent_info and parent_info.is_dynamic:
                dynamic_parent_bases.append(self.get_dynamic_base_name(parent))
                for m in self.collect_concept_methods(parent.name):
                    parent_dynamic_methods.add(m.name)

        # Methods to declare in this base class (exclude those in @dynamic parents)
        base_methods = [m for m in all_methods if m.name not in parent_dynamic_methods]

        if protocol.type_params:
            out.write(self.gen_record_template_header(protocol.type_params, {}) + "\n")
        base_name = protocol.name
        if dynamic_parent_bases:
            bases_str = ", ".join(dynamic_parent_bases)
            out.write(f"struct {base_name} : {bases_str} {{\n")
        else:
            out.write(f"struct {base_name} {{\n")
        for method_sig in base_methods:
            ret_cpp = self._dynamic_return_type(method_sig)
            const_qual = " const" if self._is_readonly_method(method_sig, protocol_info) else ""
            params_cpp = self._dynamic_param_list(method_sig)
            out.write(f"{INDENT}virtual {ret_cpp} {method_sig.name}({params_cpp}){const_qual} = 0;\n")
        out.write(f"{INDENT}virtual ~{base_name}() = default;\n")
        out.write("};\n")

    @staticmethod
    def emit_dynamic_base_forward_decl(out: TextIO, protocol: TpyProtocol) -> None:
        """Write `[template<...>] struct {Proto};` for a @dynamic protocol.

        Two call sites: the concept declaration in this module (so the concept
        body can name the protocol's own base struct inside a `convertible_to`
        constraint) and the cycle-peer `_fwd.hpp` header (so peer translation
        units can refer to the base by name without including the full header).
        """
        cpp_name = protocol.name.replace(".", "::")
        if protocol.type_params:
            tparams = ", ".join(f"typename {tp}" for tp in protocol.type_params)
            out.write(f"template<{tparams}> struct {cpp_name};\n")
        else:
            out.write(f"struct {cpp_name};\n")

    def gen_dyn_protocol_base_trait(self, out: TextIO, protocol: TpyProtocol,
                                    module_namespace: str) -> None:
        """Generate the ``tpy::is_dyn_protocol_base<P>`` specialization only.

        Split from ``gen_dynamic_adapter_specs`` so it can be emitted early
        (right after the protocol forward decl, before any in-module use that
        would implicitly instantiate the primary template via ``Box<P>``,
        ``Adapter<P, T>``, etc.). The full Adapter / RefAdapter specs are
        emitted later (see ``gen_dynamic_adapter_specs``) so their override
        bodies see complete value types referenced in protocol method
        signatures.
        """
        protocol_info = self.ctx.analyzer.registry.scan_by_short_name(protocol.name)
        if protocol_info is None:
            return
        if protocol_info.cpp_concept:
            return
        qbase_name = f"{module_namespace}::{protocol.name}"
        if protocol.type_params:
            tparam_refs = ", ".join(protocol.type_params)
            qbase = f"{qbase_name}<{tparam_refs}>"
            tparam_only = "template<" + ", ".join(f"typename {tp}" for tp in protocol.type_params) + ">"
            out.write(f"{tparam_only}\n")
            out.write(f"struct tpy::is_dyn_protocol_base<{qbase}> : std::true_type {{}};\n\n")
        else:
            out.write(f"template<> struct tpy::is_dyn_protocol_base<{qbase_name}> : std::true_type {{}};\n\n")

    def gen_dynamic_adapter_specs(self, out: TextIO, protocol: TpyProtocol,
                                  module_namespace: str,
                                  user_type_names: set[str] | None = None) -> None:
        """Generate ::tpy::Adapter and ::tpy::RefAdapter partial specializations.

        Emitted at global scope (outside user namespace), following the EnumUtil
        pattern. Uses fully-qualified names for concept and base class.

        For generic @dynamic protocols, both adapters become class templates
        parameterized on the protocol's type params + a concrete impl. The
        concept constraint uses the C++20 abbreviated form `Concept<args>
        Impl`, which desugars to `requires Concept<Impl, args>` -- matching
        the concept signature `template<typename T_checked, typename _T0,
        ...>` emitted by gen_concept_decl.

        ``user_type_names`` is the set of record/protocol short names declared
        in the current module; the adapter overrides qualify any bare
        occurrence of these names with ``module_namespace::`` so they resolve
        from inside the ``tpy::`` namespace (where the user namespace's
        injected names are not visible, and for generic protocols the
        dependent base class is not searched by unqualified lookup).

        The ``tpy::is_dyn_protocol_base<P>`` trait specialization is emitted
        separately by ``gen_dyn_protocol_base_trait`` (called earlier so
        in-module uses of ``Box<P>`` resolve the trait correctly before the
        Adapter spec lands).
        """
        protocol_info = self.ctx.analyzer.registry.scan_by_short_name(protocol.name)
        if protocol_info is None:
            return
        # @native + @dynamic: the runtime owns the abstract base and there
        # is no codegen-emitted concept, so Adapter/RefAdapter specializations
        # cannot be expressed here. Inheritance-only conformance covers the
        # use case (Throwable: every concrete subclass inherits BaseException
        # which inherits the runtime ::tpy::Throwable directly).
        if protocol_info.cpp_concept:
            return

        all_methods = self.collect_concept_methods(protocol.name)
        qbase_name = f"{module_namespace}::{protocol.name}"
        qconcept = f"{module_namespace}::__{protocol.name}_Concept__"

        if protocol.type_params:
            impl = _ADAPTER_IMPL_GENERIC
            if impl in protocol.type_params:
                # Without this guard the redeclaration would surface only as a
                # raw C++ error -- give the user a tpyc-level diagnostic.
                raise CodeGenError(
                    f"@dynamic protocol '{protocol.name}' cannot declare a "
                    f"type parameter named '{impl}' (reserved by the "
                    f"@dynamic adapter codegen). Rename the type parameter "
                    f"(e.g. 'T').",
                    loc=protocol.loc,
                )
            tparam_decls = [f"typename {tp}" for tp in protocol.type_params]
            tparam_refs = ", ".join(protocol.type_params)
            tparam_decls.append(f"{qconcept}<{tparam_refs}> {impl}")
            template_header = "template<" + ", ".join(tparam_decls) + ">"
            qbase = f"{qbase_name}<{tparam_refs}>"
        else:
            impl = _ADAPTER_IMPL_NON_GENERIC
            template_header = f"template<{qconcept} {impl}>"
            qbase = qbase_name

        # -- Owning adapter (for locals and rvalue call-site args) --
        out.write(f"{template_header}\n")
        out.write(f"struct tpy::Adapter<{qbase}, {impl}> : {qbase} {{\n")
        out.write(f"{INDENT}{impl} inner;\n")
        out.write(f"{INDENT}template<typename... Args>\n")
        out.write(f"{INDENT}Adapter(Args&&... args) : inner(std::forward<Args>(args)...) {{}}\n")
        self._gen_adapter_overrides(out, all_methods, protocol_info,
                                    module_namespace, user_type_names)
        out.write("};\n\n")

        # -- Ref adapter (for lvalue call-site args, zero-copy) --
        out.write(f"{template_header}\n")
        out.write(f"struct tpy::RefAdapter<{qbase}, {impl}> : {qbase} {{\n")
        out.write(f"{INDENT}{impl}& inner;\n")
        out.write(f"{INDENT}RefAdapter({impl}& ref) : inner(ref) {{}}\n")
        self._gen_adapter_overrides(out, all_methods, protocol_info,
                                    module_namespace, user_type_names)
        out.write("};\n")

    def _gen_adapter_overrides(self, out: TextIO, all_methods: list[MethodSignature],
                               protocol_info: 'ProtocolInfo',
                               module_namespace: str = "",
                               user_type_names: set[str] | None = None) -> None:
        """Emit method override bodies shared by owning and ref adapters."""
        for method_sig in all_methods:
            ret_cpp = self._dynamic_return_type(method_sig)
            const_qual = " const" if self._is_readonly_method(method_sig, protocol_info) else ""
            params_cpp = self._dynamic_param_list(method_sig)
            is_void = isinstance(method_sig.return_type, VoidType)
            ret_kw = "" if is_void else "return "
            call_expr = self._dynamic_forward_call(method_sig)
            # Covariant str: inner may return string_view but vtable returns string;
            # explicit construction handles the conversion.
            if is_str_type(method_sig.return_type):
                call_expr = f"std::string({call_expr})"
            if user_type_names and module_namespace:
                ret_cpp = self._qualify_user_types(ret_cpp, module_namespace, user_type_names)
                params_cpp = self._qualify_user_types(params_cpp, module_namespace, user_type_names)
            out.write(f"{INDENT}{ret_cpp} {method_sig.name}({params_cpp}){const_qual} override {{ {ret_kw}{call_expr}; }}\n")

    @staticmethod
    def _qualify_user_types(cpp_str: str, module_namespace: str,
                            user_type_names: set[str]) -> str:
        """Prefix bare occurrences of ``user_type_names`` with ``module_namespace::``.

        Used for adapter override return/parameter types, which are rendered
        inside ``tpy::`` and cannot rely on either the user namespace's scope
        or (for generic protocols) the dependent base class's injected names.
        The lookbehind skips names that already follow ``::`` or another
        identifier character, leaving fully qualified or substring matches
        untouched.
        """
        import re
        for name in user_type_names:
            cpp_str = re.sub(
                rf'(?<![:\w]){re.escape(name)}(?!\w)',
                f'{module_namespace}::{name}',
                cpp_str,
            )
        return cpp_str

    def _is_readonly_method(self, method_sig: MethodSignature, protocol_info: 'ProtocolInfo') -> bool:
        from ..typesys import ProtocolInfo as _PI
        return method_sig.is_readonly or protocol_info.is_readonly

    def _dynamic_return_type(self, method_sig: MethodSignature) -> str:
        """C++ return type for a dynamic dispatch method."""
        if isinstance(method_sig.return_type, VoidType):
            return "void"
        return method_sig.return_type.to_cpp()

    def _dynamic_param_list(self, method_sig: MethodSignature) -> str:
        """C++ parameter list for a dynamic dispatch method (excluding self)."""
        parts = []
        for pname, ptype in method_sig.params:
            parts.append(ptype.to_cpp_param(pname))
        return ", ".join(parts)

    def _dynamic_forward_call(self, method_sig: MethodSignature) -> str:
        """Generate the forwarding call expression for an adapter method."""
        arg_names = [pname for pname, _ in method_sig.params]
        args_str = ", ".join(arg_names)

        # Dunder methods with ::tpy:: free function equivalents
        if method_sig.name == "__len__":
            return f"::tpy::__len__(inner)"
        if method_sig.name == "__getitem__" and len(method_sig.params) == 1:
            return f"::tpy::__getitem__(inner, {args_str})"
        if method_sig.name == "__setitem__" and len(method_sig.params) == 2:
            return f"::tpy::__setitem__(inner, {args_str})"
        if method_sig.name in DUNDER_TO_BINARY_OP and len(method_sig.params) == 1:
            cpp_op = DUNDER_TO_BINARY_OP[method_sig.name]
            return f"inner {cpp_op} {arg_names[0]}"

        return f"inner.{method_sig.name}({args_str})"

    def collect_record_types_from_type(self, typ: TpyType, result: set[str]) -> None:
        """Recursively collect all record type names from a type.

        Codegen iterates `TpyProtocol.methods` (parser AST, not rewritten by
        sema's `register_protocol`), so some NominalTypes here are still bare
        parser placeholders (no `_module_qname`, no TypeDef entry). For those,
        `is_user_record` is False; fall back to the record registry directly.

        `@builtin_type` records (e.g. `tpy.coro.Waker`) are included too: when
        they're TPy-defined in the current module, the forward-decl is
        required just like a plain user record. The emit step filters by
        `module_record_names`, so off-module builtin entries are dropped
        there harmlessly.
        """
        if isinstance(typ, NominalType) and not typ.is_protocol:
            if typ.is_user_record:
                result.add(typ.name)
            else:
                info = self.ctx.analyzer.registry.get_record(typ.name)
                if info is not None:
                    result.add(typ.name)
        for inner in typ.inner_types():
            self.collect_record_types_from_type(inner, result)

    def collect_type_args_of_bounded_records(
        self,
        typ: TpyType,
        records_by_name: dict[str, TpyRecord],
        result: set[str]
    ) -> None:
        """Collect record names used as type args to records with user-defined bounds.

        When a protocol references Container[Message] where Container has a user-defined
        protocol bound, C++ needs Message to be fully defined to check the constraint.
        Same placeholder-tolerance story as `collect_record_types_from_type` --
        accept either a resolved user-record NominalType or a bare placeholder
        whose name resolves to a non-builtin RecordInfo in the registry.
        """
        registry = self.ctx.analyzer.registry

        def is_user_record_nominal(t: TpyType) -> bool:
            if not isinstance(t, NominalType) or t.is_protocol:
                return False
            if t.is_user_record:
                return True
            info = registry.get_record(t.name)
            return info is not None and info.builtin_type_key is None

        if (isinstance(typ, NominalType) and typ.type_args
                and is_user_record_nominal(typ)):
            record = records_by_name.get(typ.name)
            if record and record.type_param_bounds:
                # Check if any bound is a user-defined protocol
                has_user_bound = any(
                    (pi := protocol_info_of(bound)) is None or pi.cpp_concept is None
                    for bound in record.type_param_bounds.values()
                )
                if has_user_bound:
                    # Collect all type args as needing early definition
                    for type_arg in typ.type_args:
                        if isinstance(type_arg, NominalType) and is_user_record_nominal(type_arg):
                            result.add(type_arg.name)

        # Recurse into inner types
        for inner in typ.inner_types():
            self.collect_type_args_of_bounded_records(inner, records_by_name, result)
