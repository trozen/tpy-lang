"""
TurboPython Builtin Code Generation

Code generation for builtin functions, type constructors, and print().
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, BoolType, StrType, CharType,
    NamedType, TypeParamRef, TypeParamKind, FunctionInfo, RecordInfo,
    CHAR
)
from ..parse import (
    TpyExpr, TpyCall, TpyStrLiteral, TpyArrayLiteral, TpyCoerce
)

from .context import escape_cpp_string

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver

from tpyc import modules as builtin_modules


class BuiltinGenerator:
    """Generates C++ code for builtin functions and type constructors."""

    def __init__(self, ctx: CodeGenContext, types: TypeResolver):
        self.ctx = ctx
        self.types = types
        # Will be set by expressions module to avoid circular import
        self._gen_expr = None
        self._gen_expr_deref = None

    def set_expr_generator(self, gen_expr, gen_expr_deref):
        """Set expression generator functions (to break circular dependency)."""
        self._gen_expr = gen_expr
        self._gen_expr_deref = gen_expr_deref

    def get_type_method_template(self, tpy_type: TpyType, method_name: str) -> str | None:
        """Look up a method's cpp_template from the registry.

        Returns the cpp_template string if found, None otherwise.
        Requires single overload - errors if multiple overloads exist.
        For methods with multiple overloads, sema should resolve and attach
        the correct FunctionInfo to the AST node.
        """
        record_info = self.ctx.analyzer.registry.get_record_for_type(tpy_type)
        if record_info:
            overloads = record_info.get_method_overloads(method_name)
            if len(overloads) > 1:
                raise RuntimeError(
                    f"get_type_method_template called for multi-overload method '{method_name}' "
                    f"on {tpy_type}; use resolved_function_info instead"
                )
            if overloads and overloads[0].cpp_template:
                return overloads[0].cpp_template
        return None

    def gen_method_from_function_info(self, obj: str, args: list[TpyExpr],
                                      method: FunctionInfo) -> str:
        """Generate method call code from a FunctionInfo with cpp_template.

        Substitutes {self} with obj and {0}, {1}, etc. with generated args.
        """
        assert method.cpp_template is not None
        result = method.cpp_template.replace("{self}", obj)
        for i, arg in enumerate(args):
            result = result.replace(f"{{{i}}}", self._gen_expr(arg))
        return result

    def gen_builtin_constructor(self, expr: TpyCall, record_info: RecordInfo) -> str:
        """Generate C++ code for a builtin type constructor using unified RecordInfo.constructors."""
        args = expr.args
        arg_types = [self.ctx.analyzer.get_expr_type(arg) for arg in args]

        for ctor in record_info.constructors:
            if len(ctor.params) != len(args):
                continue
            if all(self._builtin_codegen_type_matches(arg, arg_t, ptype)
                   for arg, arg_t, (_, ptype) in zip(args, arg_types, ctor.params)):
                gen_args = [self._gen_expr_deref(arg, ptype) for arg, (_, ptype) in zip(args, ctor.params)]
                return ctor.cpp_template.format(*gen_args)

        raise RuntimeError(f"No matching constructor for {expr.func}")

    def ctor_param_matches(self, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if argument type matches constructor parameter (for generic type constructors)."""
        # Check for protocol type with TypeParamRef (e.g., NativeIterable[T])
        if isinstance(param_type, NamedType) and param_type.is_protocol and param_type.type_args:
            has_type_param = any(isinstance(ta, TypeParamRef) for ta in param_type.type_args)
            if has_type_param:
                protocol_name = param_type.name
                # Get element type from arg to build concrete protocol type
                elem_type = arg_type.get_element_type()
                if elem_type is None:
                    # No element type - check if it's a concrete protocol like NativeIterable[Char]
                    # This handles str which has no get_element_type but extends NativeIterable[Char]
                    if isinstance(arg_type, StrType) and protocol_name == "NativeIterable":
                        elem_type = CHAR
                    else:
                        return False
                # Check with type-arg-aware protocol conformance
                return builtin_modules.type_extends_protocol(arg_type, protocol_name, [elem_type])

        if isinstance(param_type, TpyType):
            return arg_type == param_type or (
                isinstance(arg_type, IntLiteralType) and isinstance(param_type, (Int32Type, BigIntType))
            )
        return False

    def apply_cpp_template(
        self, template: str, args: list[str], type_params: dict[str, TpyType], result_type: TpyType
    ) -> str:
        """Apply a cpp template with argument and type parameter substitution."""
        result = template
        # Substitute positional arguments {0}, {1}, etc.
        for i, arg in enumerate(args):
            result = result.replace(f"{{{i}}}", arg)
        # Substitute type parameters {T}, etc.
        for name, typ in type_params.items():
            result = result.replace(f"{{{name}}}", typ.to_cpp())
        return result

    def gen_builtin_function_overloads(self, expr: TpyCall, overloads: list[FunctionInfo]) -> str:
        """Generate C++ code for a builtin function call using unified FunctionInfo overloads."""
        args = expr.args
        arg_types = [self.ctx.analyzer.get_expr_type(arg) for arg in args]

        for overload in overloads:
            if len(overload.params) != len(args):
                continue
            if all(self._builtin_codegen_type_matches(arg, arg_t, ptype)
                   for arg, arg_t, (_, ptype) in zip(args, arg_types, overload.params)):
                # Generate args with proper type coercion (e.g., int literal → BigInt)
                # Use _gen_expr_deref to handle globals (tpy::Global<T> needs dereferencing)
                gen_args = [self._gen_expr_deref(arg, ptype) for arg, (_, ptype) in zip(args, overload.params)]
                return overload.cpp_template.format(*gen_args)

        raise RuntimeError(f"No matching overload for {expr.func}")

    def _builtin_codegen_type_matches(self, arg: TpyExpr, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if an argument matches a parameter type for codegen purposes."""
        # Direct type match
        if isinstance(arg_type, type(param_type)) and arg_type == param_type:
            return True
        # Protocol parameter: sema already verified conformance, accept any arg
        if isinstance(param_type, NamedType) and param_type.is_protocol:
            return True
        # IntLiteral can match Int32 (if compile-time) or BigInt (if runtime)
        if isinstance(arg_type, IntLiteralType):
            if isinstance(param_type, BigIntType):
                return self.types.is_runtime_bigint(arg, arg_type)
            if isinstance(param_type, Int32Type):
                return not self.types.is_runtime_bigint(arg, arg_type)
        # INT TypeParamRef can match Int32 or BigInt (it's a compile-time constant)
        if isinstance(arg_type, TypeParamRef) and arg_type.kind == TypeParamKind.INT:
            return isinstance(param_type, (Int32Type, BigIntType))
        # TpyCoerce nodes match their expected type
        if isinstance(arg, TpyCoerce):
            return arg.expected_type == param_type
        return False

    def gen_print(self, args: list[TpyExpr], kwargs: dict[str, TpyExpr] = None) -> str:
        """Generate std::cout call for print()."""
        kwargs = kwargs or {}

        # Determine line ending (default is newline)
        end_str = "\\n"
        if "end" in kwargs:
            end_expr = kwargs["end"]
            if isinstance(end_expr, TpyStrLiteral):
                end_str = escape_cpp_string(end_expr.value)

        if not args:
            if end_str:
                return f'std::cout << "{end_str}"'
            return ""

        parts = []
        for i, arg in enumerate(args):
            if i > 0:
                parts.append('" "')  # Space separator between args

            arg_type = self.types.get_resolved_type(arg)

            if isinstance(arg, TpyStrLiteral):
                parts.append(f'"{escape_cpp_string(arg.value)}"')
            elif self.types.is_runtime_bigint(arg, arg_type):
                # BigInt has operator<< for std::ostream, no .to_string() needed
                parts.append(self._gen_expr_deref(arg))
            elif isinstance(arg_type, FloatType):
                # Float uses Python-style formatting via tpy::print_float
                parts.append(f'tpy::print_float({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, BoolType):
                # Bool uses Python-style formatting via tpy::print_bool
                parts.append(f'tpy::print_bool({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, StrType):
                # Strings print as-is (not using ListPrinter)
                parts.append(self._gen_expr_deref(arg))
            elif arg_type.get_element_type() is not None:
                # Container types use ListPrinter for formatting
                if isinstance(arg, TpyArrayLiteral):
                    # Array literals need explicit type for ListPrinter CTAD
                    cpp_type = arg_type.to_cpp()
                    parts.append(f'tpy::ListPrinter({cpp_type}{self._gen_expr(arg)})')
                else:
                    parts.append(f'tpy::ListPrinter({self._gen_expr_deref(arg)})')
            else:
                # Int32, Char, Bool, literals, etc. - direct output
                parts.append(self._gen_expr_deref(arg))

        # Add end string
        if end_str:
            parts.append(f'"{end_str}"')

        return "std::cout << " + " << ".join(parts)
