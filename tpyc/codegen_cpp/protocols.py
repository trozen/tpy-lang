"""
TurboPython Protocol/Concept Code Generation

C++20 concept generation from TurboPython protocols and template header utilities.
"""

from __future__ import annotations
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, TypeParamRef, TypeParamKind, ReadonlyType, VoidType,
    MethodSignature, is_protocol_type, unwrap_readonly,
)
from ..parse import TpyProtocol, TpyRecord
from .context import INDENT, DUNDER_TO_BINARY_OP, qualified_cpp_name

if TYPE_CHECKING:
    from .context import CodeGenContext


class ProtocolGenerator:
    """Generates C++20 concepts from TurboPython protocols."""

    def __init__(self, ctx: CodeGenContext):
        self.ctx = ctx

    def resolve_type_for_codegen(self, typ: TpyType) -> TpyType:
        """Resolve a type, setting is_protocol flag if it's actually a protocol.

        During parsing, some types may be classified as records when they're
        actually protocols (e.g., imported protocols). This method fixes that for codegen.
        """
        if isinstance(typ, NamedType) and not typ.is_protocol:
            # Check if this is actually a protocol
            protocol_info = self.ctx.analyzer.registry.get_protocol(typ.name)
            if protocol_info is not None:
                return typ.with_protocol_flag(is_protocol=True)
        return typ

    def get_protocol_params(self, params: list[tuple[str, TpyType]]) -> list[tuple[str, NamedType]]:
        """Get list of static protocol-typed parameters (unwraps readonly[protocol]).

        Excludes @dynamic protocols -- those use concrete __tpy_Base& params
        instead of template parameters.
        """
        result = []
        for pname, ptype in params:
            unwrapped = unwrap_readonly(ptype)
            resolved = self.resolve_type_for_codegen(unwrapped)
            if is_protocol_type(resolved):
                protocol_info = self.ctx.analyzer.registry.get_protocol(resolved.name)
                if protocol_info and protocol_info.is_dynamic:
                    continue
                result.append((pname, resolved))
        return result

    def get_concept_name(self, protocol: NamedType) -> str:
        """Get the C++ concept name for a protocol type."""
        # Unified lookup via registry
        protocol_info = self.ctx.analyzer.registry.get_protocol(protocol.name)
        if protocol_info and protocol_info.cpp_concept:
            return protocol_info.cpp_concept
        # Check if this is an imported protocol from another user module
        if protocol.name in self.ctx.user_imported_protocols:
            source_module, original_name = self.ctx.user_imported_protocols[protocol.name]
            return qualified_cpp_name(source_module, original_name)
        # User-defined protocol - use the protocol name directly
        return protocol.name

    def get_dynamic_base_name(self, protocol_name: str) -> str:
        """Get the (possibly qualified) C++ name for __tpy_Base_{Name}."""
        if protocol_name in self.ctx.user_imported_protocols:
            source_module, original_name = self.ctx.user_imported_protocols[protocol_name]
            return qualified_cpp_name(source_module, f"__tpy_Base_{original_name}")
        return f"__tpy_Base_{protocol_name}"

    def get_dynamic_adapter_name(self, protocol_name: str) -> str:
        """Get the (possibly qualified) C++ name for __tpy_Adapter_{Name}."""
        if protocol_name in self.ctx.user_imported_protocols:
            source_module, original_name = self.ctx.user_imported_protocols[protocol_name]
            return qualified_cpp_name(source_module, f"__tpy_Adapter_{original_name}")
        return f"__tpy_Adapter_{protocol_name}"

    def get_dynamic_ref_adapter_name(self, protocol_name: str) -> str:
        """Get the (possibly qualified) C++ name for __tpy_RefAdapter_{Name}."""
        if protocol_name in self.ctx.user_imported_protocols:
            source_module, original_name = self.ctx.user_imported_protocols[protocol_name]
            return qualified_cpp_name(source_module, f"__tpy_RefAdapter_{original_name}")
        return f"__tpy_RefAdapter_{protocol_name}"

    def _protocol_inherits_from(self, protocol_name: str, ancestor_name: str) -> bool:
        """Check if protocol_name transitively inherits from ancestor_name."""
        visited: set[str] = set()
        stack = [protocol_name]
        while stack:
            name = stack.pop()
            if name == ancestor_name:
                return True
            if name in visited:
                continue
            visited.add(name)
            info = self.ctx.analyzer.registry.get_protocol(name)
            if info:
                stack.extend(info.parent_protocols)
        return False

    def directly_implements_dynamic(self, concrete_type: TpyType, proto_name: str) -> bool:
        """Check if concrete_type inherits a @dynamic protocol (directly or transitively).

        Returns True when the record explicitly implements a @dynamic protocol that
        is (or transitively inherits from) proto_name. This means the C++ struct
        inherits __tpy_Base_{proto_name} through the inheritance chain and no adapter
        wrapping is needed.
        """
        if not isinstance(concrete_type, NamedType) or not concrete_type.is_record:
            return False
        record_info = self.ctx.analyzer.registry.get_record(concrete_type.name)
        if not record_info:
            return False
        for p in record_info.implemented_protocols:
            pi = self.ctx.analyzer.registry.get_protocol(p.name)
            if pi and pi.is_dynamic:
                if p.name == proto_name:
                    return True
                if self._protocol_inherits_from(p.name, proto_name):
                    return True
        return False

    def gen_record_template_header(
        self,
        type_params: list[str],
        type_param_bounds: dict[str, NamedType],
        type_param_kinds: list[TypeParamKind] | None = None
    ) -> str:
        """Generate template header for a generic record.

        For unbounded TYPE params: template<typename T>
        For bounded TYPE params: template<Comparable T>
        For INT params: template<std::size_t N>
        """
        template_parts = []
        for i, tp in enumerate(type_params):
            # Check if this is an INT type param
            kind = type_param_kinds[i] if type_param_kinds and i < len(type_param_kinds) else TypeParamKind.TYPE
            if kind == TypeParamKind.INT:
                template_parts.append(f"std::size_t {tp}")
            elif tp in type_param_bounds:
                bound = type_param_bounds[tp]
                concept_name = self.get_concept_name(bound)
                if bound.type_args:
                    type_args_cpp = ", ".join(t.to_cpp() for t in bound.type_args)
                    template_parts.append(f"{concept_name}<{type_args_cpp}> {tp}")
                else:
                    template_parts.append(f"{concept_name} {tp}")
            else:
                template_parts.append(f"typename {tp}")
        return f"template<{', '.join(template_parts)}>"

    def gen_template_header(self, protocol_params: list[tuple[str, NamedType]]) -> str:
        """Generate template header with concept constraints for protocol params.

        For non-generic protocols: template<tpy::Sized T_items>
        For generic protocols: template<tpy::Sequence<int32_t> T_items>
        """
        if not protocol_params:
            return ""
        template_parts = []
        for pname, ptype in protocol_params:
            # Look up protocol to get C++ concept name via unified registry
            concept_name = self.get_concept_name(ptype)

            # For generic protocols, add type arguments
            if ptype.type_args:
                type_args_cpp = ", ".join(t.to_cpp() for t in ptype.type_args)
                template_parts.append(f"{concept_name}<{type_args_cpp}> T_{pname}")
            else:
                template_parts.append(f"{concept_name} T_{pname}")
        return f"template<{', '.join(template_parts)}>\n"

    def gen_combined_template_header(
        self,
        type_params: list[str],
        protocol_params: list[tuple[str, NamedType]],
        type_param_bounds: dict[str, NamedType] | None = None
    ) -> str:
        """Generate template header combining type parameters and concept constraints.

        For generic functions: template<typename T>
        For generic functions with protocols: template<typename T, tpy::Sized T_items>
        For bounded type params: template<Comparable T>
        """
        template_parts = []

        # Add type parameters for generic functions (with optional bounds)
        for tp in type_params:
            if type_param_bounds and tp in type_param_bounds:
                bound = type_param_bounds[tp]
                concept_name = self.get_concept_name(bound)
                if bound.type_args:
                    type_args_cpp = ", ".join(t.to_cpp() for t in bound.type_args)
                    template_parts.append(f"{concept_name}<{type_args_cpp}> {tp}")
                else:
                    template_parts.append(f"{concept_name} {tp}")
            else:
                template_parts.append(f"typename {tp}")

        # Add protocol params with concept constraints
        for pname, ptype in protocol_params:
            concept_name = self.get_concept_name(ptype)

            if ptype.type_args:
                type_args_cpp = ", ".join(t.to_cpp() for t in ptype.type_args)
                template_parts.append(f"{concept_name}<{type_args_cpp}> T_{pname}")
            else:
                template_parts.append(f"{concept_name} T_{pname}")

        if not template_parts:
            return ""
        return f"template<{', '.join(template_parts)}>\n"

    def collect_concept_methods(self, protocol_name: str, visited: set[str] | None = None) -> list[MethodSignature]:
        """Collect methods from a protocol and all its parents for concept generation."""
        if visited is None:
            visited = set()
        if protocol_name in visited:
            return []
        visited.add(protocol_name)

        protocol_info = self.ctx.analyzer.registry.get_protocol(protocol_name)
        if protocol_info is None:
            return []

        # Start with direct methods
        methods_by_name: dict[str, MethodSignature] = {}
        for method in protocol_info.methods:
            methods_by_name[method.name] = method

        # Add inherited methods (only if not already defined directly)
        for parent_name in protocol_info.parent_protocols:
            for method in self.collect_concept_methods(parent_name, visited):
                if method.name not in methods_by_name:
                    methods_by_name[method.name] = method

        return list(methods_by_name.values())

    def collect_concept_fields(self, protocol_name: str, visited: set[str] | None = None) -> list[tuple[str, TpyType]]:
        """Collect fields from a protocol and all its parents for concept generation."""
        if visited is None:
            visited = set()
        if protocol_name in visited:
            return []
        visited.add(protocol_name)

        protocol_info = self.ctx.analyzer.registry.get_protocol(protocol_name)
        if protocol_info is None:
            return []

        # Start with direct fields
        fields_by_name: dict[str, tuple[str, TpyType]] = {}
        for field_name, field_type in protocol_info.fields:
            fields_by_name[field_name] = (field_name, field_type)

        # Add inherited fields (only if not already defined directly)
        for parent_name in protocol_info.parent_protocols:
            for field_name, field_type in self.collect_concept_fields(parent_name, visited):
                if field_name not in fields_by_name:
                    fields_by_name[field_name] = (field_name, field_type)

        return list(fields_by_name.values())

    def gen_concept_decl(self, out: TextIO, protocol: TpyProtocol) -> None:
        """Generate a C++20 concept for a user-defined protocol.

        SelfType in method signatures is rendered as 'T' (the template parameter).
        This allows the concept to check that e.g., T + T -> T.

        For generic protocols like Container[T], we generate:
        - template<typename T, typename _T0> where T is the checked type and _T0 is the protocol's T
        - Usage: Container<int32_t> V means V must satisfy Container<V, int32_t>

        For protocol inheritance, includes requirements from all parent protocols.
        """
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

        # Use const T& unless any method is non-readonly
        protocol_info = self.ctx.analyzer.registry.get_protocol(protocol.name)
        has_mutable_method = any(
            not m.is_readonly and not (protocol_info is not None and protocol_info.is_readonly)
            for m in all_methods
        )
        if not has_mutable_method:
            out.write(f"concept {protocol.name} = requires(const T& t) {{\n")
        else:
            out.write(f"concept {protocol.name} = requires(T& t) {{\n")


        def subst_type(typ: TpyType) -> TpyType:
            """Substitute protocol type params recursively in a type."""
            if isinstance(typ, TypeParamRef) and typ.name in type_param_map:
                # Return a TypeParamRef with the mapped name
                return TypeParamRef(type_param_map[typ.name])
            return typ.map_inner_types(subst_type)

        def subst_to_cpp(typ: TpyType) -> str:
            """Convert type to C++, substituting protocol type params."""
            return subst_type(typ).to_cpp()

        for method_sig in all_methods:
            # Generate requirement for each method
            # SelfType.to_cpp() returns "T", so this handles Self -> T substitution
            # Protocol type params (e.g., T in Container[T]) are mapped to _T0, _T1, etc.
            ret_cpp = subst_to_cpp(method_sig.return_type)

            # For dunder methods that have tpy:: free function equivalents, use those
            # This allows std types (vector, string, etc.) to satisfy the protocol
            if method_sig.name == "__len__":
                out.write(f"{INDENT}{{ tpy::__len__(t) }} -> std::convertible_to<{ret_cpp}>;\n")
            elif method_sig.name in DUNDER_TO_BINARY_OP and len(method_sig.params) == 1:
                # Binary operators - use C++ operator syntax
                # e.g., __add__(Self) -> Self becomes { t + std::declval<T>() } -> convertible_to<T>
                cpp_op = DUNDER_TO_BINARY_OP[method_sig.name]
                _, ptype = method_sig.params[0]
                param_cpp = subst_to_cpp(ptype)
                out.write(f"{INDENT}{{ t {cpp_op} std::declval<{param_cpp}>() }} -> std::convertible_to<{ret_cpp}>;\n")
            else:
                # { t.method_name(args...) } -> std::convertible_to<return_type>;
                params_str = ""
                if method_sig.params:
                    # Use std::declval for parameter types
                    # SelfType.to_cpp() returns "T", so Self params become std::declval<T>()
                    param_exprs = [f"std::declval<{subst_to_cpp(ptype)}>()" for _, ptype in method_sig.params]
                    params_str = ", ".join(param_exprs)
                out.write(f"{INDENT}{{ t.{method_sig.name}({params_str}) }} -> std::convertible_to<{ret_cpp}>;\n")

        # Collect all fields including inherited ones
        all_fields = self.collect_concept_fields(protocol.name)
        for field_name, field_type in all_fields:
            field_cpp = subst_to_cpp(field_type)
            out.write(f"{INDENT}{{ t.{field_name} }} -> std::convertible_to<{field_cpp}>;\n")

        out.write("};\n")

    def gen_dynamic_base_and_adapter(self, out: TextIO, protocol: TpyProtocol) -> None:
        """Generate abstract base class and type-erasing adapter for @dynamic protocol.

        Emitted after the concept so the adapter can use the concept as a constraint.
        When this protocol extends other @dynamic protocols, the base class inherits
        from their bases (e.g., __tpy_Base_NamedPet : __tpy_Base_Pet) and only declares
        methods not already present in parent bases. Adapters still override all methods.
        """
        protocol_info = self.ctx.analyzer.registry.get_protocol(protocol.name)
        if protocol_info is None:
            return

        all_methods = self.collect_concept_methods(protocol.name)

        base_name = f"__tpy_Base_{protocol.name}"
        adapter_name = f"__tpy_Adapter_{protocol.name}"

        # Collect @dynamic parent bases and their already-declared methods
        dynamic_parent_bases: list[str] = []
        parent_dynamic_methods: set[str] = set()
        for parent_name in protocol_info.parent_protocols:
            parent_info = self.ctx.analyzer.registry.get_protocol(parent_name)
            if parent_info and parent_info.is_dynamic:
                dynamic_parent_bases.append(self.get_dynamic_base_name(parent_name))
                for m in self.collect_concept_methods(parent_name):
                    parent_dynamic_methods.add(m.name)

        # Methods to declare in this base class (exclude those in @dynamic parents)
        base_methods = [m for m in all_methods if m.name not in parent_dynamic_methods]

        # -- Abstract base class --
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
        out.write("};\n\n")

        # -- Owning adapter template (for locals and rvalue call-site args) --
        out.write(f"template<{protocol.name} T>\n")
        out.write(f"struct {adapter_name} : {base_name} {{\n")
        out.write(f"{INDENT}T inner;\n")
        out.write(f"{INDENT}template<typename... Args>\n")
        out.write(f"{INDENT}{adapter_name}(Args&&... args) : inner(std::forward<Args>(args)...) {{}}\n")
        self._gen_adapter_overrides(out, all_methods, protocol_info)
        out.write("};\n\n")

        # -- Ref adapter template (for lvalue call-site args, zero-copy) --
        ref_adapter_name = f"__tpy_RefAdapter_{protocol.name}"
        out.write(f"template<{protocol.name} T>\n")
        out.write(f"struct {ref_adapter_name} : {base_name} {{\n")
        out.write(f"{INDENT}T& inner;\n")
        out.write(f"{INDENT}{ref_adapter_name}(T& ref) : inner(ref) {{}}\n")
        self._gen_adapter_overrides(out, all_methods, protocol_info)
        out.write("};\n")

    def _gen_adapter_overrides(self, out: TextIO, all_methods: list[MethodSignature],
                               protocol_info: 'ProtocolInfo') -> None:
        """Emit method override bodies shared by owning and ref adapters."""
        for method_sig in all_methods:
            ret_cpp = self._dynamic_return_type(method_sig)
            const_qual = " const" if self._is_readonly_method(method_sig, protocol_info) else ""
            params_cpp = self._dynamic_param_list(method_sig)
            is_void = isinstance(method_sig.return_type, VoidType)
            ret_kw = "" if is_void else "return "
            call_expr = self._dynamic_forward_call(method_sig)
            out.write(f"{INDENT}{ret_cpp} {method_sig.name}({params_cpp}){const_qual} override {{ {ret_kw}{call_expr}; }}\n")

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

        # Dunder methods with tpy:: free function equivalents
        if method_sig.name == "__len__":
            return f"tpy::__len__(inner)"
        if method_sig.name == "__getitem__" and len(method_sig.params) == 1:
            return f"tpy::__getitem__(inner, {args_str})"
        if method_sig.name == "__setitem__" and len(method_sig.params) == 2:
            return f"tpy::__setitem__(inner, {args_str})"
        if method_sig.name in DUNDER_TO_BINARY_OP and len(method_sig.params) == 1:
            cpp_op = DUNDER_TO_BINARY_OP[method_sig.name]
            return f"inner {cpp_op} {arg_names[0]}"

        return f"inner.{method_sig.name}({args_str})"

    def collect_record_types_from_type(self, typ: TpyType, result: set[str]) -> None:
        """Recursively collect all record type names from a type."""
        if isinstance(typ, NamedType) and typ.is_record:
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
        """
        if isinstance(typ, NamedType) and typ.is_record and typ.type_args:
            record = records_by_name.get(typ.name)
            if record and record.type_param_bounds:
                # Check if any bound is a user-defined protocol
                has_user_bound = any(
                    self.ctx.analyzer.registry.get_protocol(bound.name) is None or
                    self.ctx.analyzer.registry.get_protocol(bound.name).cpp_concept is None
                    for bound in record.type_param_bounds.values()
                )
                if has_user_bound:
                    # Collect all type args as needing early definition
                    for type_arg in typ.type_args:
                        if isinstance(type_arg, NamedType) and type_arg.is_record:
                            result.add(type_arg.name)

        # Recurse into inner types
        for inner in typ.inner_types():
            self.collect_type_args_of_bounded_records(inner, records_by_name, result)
