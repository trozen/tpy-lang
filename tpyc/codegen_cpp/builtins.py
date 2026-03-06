"""
TurboPython Builtin Code Generation

Code generation for builtin functions, type constructors, and print().
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, FixedIntType, BigIntType, IntLiteralType, FloatType, Float32Type, BoolType, StrType, CharType,
    NamedType, OptionalType, NoneType, TypeParamRef, TypeParamKind, FunctionInfo, RecordInfo,
    ListType, ListRepeatType, DictType, DictKeysViewType, DictValuesViewType, DictItemsViewType,
    ArrayType, SpanType, TupleType, is_protocol_type, unwrap_readonly, is_any_str_type,
)
from ..parse import (
    TpyExpr, TpyCall, TpyStrLiteral, TpyArrayLiteral, TpyNoneLiteral, TpyCoerce,
    TpyFieldAccess,
)

from .context import escape_cpp_string, CodeGenError, expand_cpp_template

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver


class BuiltinGenerator:
    """Generates C++ code for builtin functions and type constructors."""

    def __init__(self, ctx: CodeGenContext, types: TypeResolver):
        self.ctx = ctx
        self.types = types
        # Will be set by expressions module to avoid circular import
        self._gen_expr = None
        self._gen_expr_deref = None
        self._gen_call_arg = None

    def set_expr_generator(self, gen_expr, gen_expr_deref, gen_call_arg):
        """Set expression generator functions (to break circular dependency)."""
        self._gen_expr = gen_expr
        self._gen_expr_deref = gen_expr_deref
        self._gen_call_arg = gen_call_arg

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
        # Check builtin protocol method templates (e.g., Sequence.__getitem__)
        if is_protocol_type(tpy_type):
            from ..modules import lookup_protocol
            proto_def = lookup_protocol(tpy_type.name)
            if proto_def and method_name in proto_def.methods:
                return proto_def.methods[method_name].cpp
        return None

    def gen_method_from_function_info(self, obj: str, args: list[TpyExpr],
                                      method: FunctionInfo) -> str:
        """Generate method call code from a FunctionInfo with cpp_template.

        Substitutes {self} with obj and {0}, {1}, etc. with generated args.
        """
        if method.cpp_template is None:
            raise CodeGenError(f"Method '{method.name}' has no C++ template")
        gen_args = [self._gen_call_arg(arg, method.params[i].type if i < len(method.params) else None)
                    for i, arg in enumerate(args)]
        return expand_cpp_template(method.cpp_template, obj, *gen_args)

    def gen_builtin_constructor(self, expr: TpyCall, record_info: RecordInfo) -> str:
        """Generate C++ code for a builtin type constructor using unified RecordInfo.constructors."""
        fi = expr.resolved_function_info
        if fi and fi.cpp_template:
            gen_args = [self._gen_expr_deref(arg, ptype)
                        for arg, (_, ptype) in zip(expr.args, fi.params)]
            return fi.cpp_template.format(*gen_args)

        # Fallback for synthetic calls (e.g. module-aliased constructors like t.Int32(42))
        args = expr.args
        arg_types = [self.ctx.get_expr_type(arg) for arg in args]
        for ctor in record_info.constructors:
            if len(ctor.params) != len(args):
                continue
            if all(self._builtin_codegen_type_matches(arg, arg_t, ptype)
                   for arg, arg_t, (_, ptype) in zip(args, arg_types, ctor.params)):
                gen_args = [self._gen_expr_deref(arg, ptype) for arg, (_, ptype) in zip(args, ctor.params)]
                return ctor.cpp_template.format(*gen_args)

        raise RuntimeError(f"No matching constructor for {expr.func}")


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

    def _match_overload_args(self, expr: TpyCall,
                             overloads: list[FunctionInfo]) -> tuple[FunctionInfo, list[str]]:
        """Match a builtin call to an overload and generate C++ arg expressions.

        Returns (matched_overload, gen_args). Raises RuntimeError if no match.
        """
        args = expr.args
        arg_types = [self.ctx.get_expr_type(arg) for arg in args]

        for overload in overloads:
            if len(overload.params) != len(args):
                continue
            if all(self._builtin_codegen_type_matches(arg, arg_t, ptype)
                   for arg, arg_t, (_, ptype) in zip(args, arg_types, overload.params)):
                gen_args = [self._gen_expr_deref(arg, ptype)
                            for arg, (_, ptype) in zip(args, overload.params)]
                return overload, gen_args

        raise RuntimeError(f"No matching overload for {expr.func}")

    def gen_range_args(self, expr: TpyCall) -> list[str]:
        """Generate individual C++ arg expressions for range().

        Returns the generated arg list (e.g. ["0", "10", "2"]) rather than
        formatting into the full template string.  Used by the counter-loop
        optimisation in statements.py.
        """
        fi = expr.resolved_function_info
        return [self._gen_expr_deref(arg, ptype)
                for arg, (_, ptype) in zip(expr.args, fi.params)]

    def gen_builtin_function_overloads(self, expr: TpyCall, overloads: list[FunctionInfo]) -> str:
        """Generate C++ code for a builtin function call using unified FunctionInfo overloads."""
        fi = expr.resolved_function_info
        if fi and fi.cpp_template:
            template = fi.cpp_template
            # Resolve type param placeholders (e.g. {T} -> int32_t) at codegen
            # time so native C++ names (_native_cpp_names) are available.
            if expr.inferred_type_args and fi.type_params:
                for name, typ in zip(fi.type_params, expr.inferred_type_args):
                    placeholder = f"{{{name}}}"
                    if placeholder in template and hasattr(typ, "to_cpp"):
                        template = template.replace(placeholder, typ.to_cpp())
            gen_args = [self._gen_expr_deref(arg, ptype)
                        for arg, (_, ptype) in zip(expr.args, fi.params)]
            return template.format(*gen_args)
        # Fallback: re-resolve (shouldn't normally be needed)
        overload, gen_args = self._match_overload_args(expr, overloads)
        return overload.cpp_template.format(*gen_args)

    def _builtin_codegen_type_matches(self, arg: TpyExpr, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if an argument matches a parameter type for codegen purposes."""
        # Direct type match
        if isinstance(arg_type, type(param_type)) and arg_type == param_type:
            return True
        # Protocol parameter: sema already verified conformance, accept any arg
        if is_protocol_type(param_type):
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

            arg_type = unwrap_readonly(self.types.get_resolved_type(arg))

            if isinstance(arg, TpyNoneLiteral):
                parts.append('"None"')
            elif isinstance(arg, TpyStrLiteral):
                parts.append(f'"{escape_cpp_string(arg.value)}"')
            elif self.types.is_runtime_bigint(arg, arg_type):
                # BigInt has operator<< for std::ostream, no .to_string() needed
                parts.append(self._gen_expr_deref(arg))
            elif isinstance(arg_type, FloatType):
                parts.append(f'tpy::print_float({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, Float32Type):
                parts.append(f'tpy::print_float(static_cast<double>({self._gen_expr_deref(arg)}))')
            elif isinstance(arg_type, BoolType):
                # Bool uses Python-style formatting via tpy::print_bool
                parts.append(f'tpy::print_bool({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, OptionalType) and arg_type.uses_pointer_repr():
                if isinstance(arg, TpyFieldAccess):
                    # Field access produces std::optional<T> -- use print_optional_val
                    parts.append(f'tpy::print_optional_val({self._gen_expr(arg)})')
                else:
                    # Pointer-local/function return produces T* -- use print_optional
                    parts.append(f'tpy::print_optional({self._gen_expr(arg)})')
            elif isinstance(arg_type, OptionalType) and not arg_type.uses_pointer_repr():
                # Optional value-type: use print_optional_val with inner-type-aware formatting
                inner = arg_type.inner
                gen = self._gen_expr_deref(arg)
                inner_cpp = inner.to_cpp()
                if isinstance(inner, BoolType):
                    parts.append(f'tpy::print_optional_val<tpy::print_bool, {inner_cpp}>({gen})')
                elif isinstance(inner, (FloatType, Float32Type)):
                    parts.append(f'tpy::print_optional_val<tpy::print_float, {inner_cpp}>({gen})')
                else:
                    parts.append(f'tpy::print_optional_val({gen})')
            elif isinstance(arg_type, TupleType):
                parts.append(f'tpy::TuplePrinter({self._gen_expr_deref(arg)})')
            elif is_any_str_type(arg_type):
                # Strings print as-is (not using ListPrinter)
                parts.append(self._gen_expr_deref(arg))
            elif isinstance(arg_type, DictType):
                # Dict uses DictPrinter for {k: v, ...} formatting
                parts.append(f'tpy::DictPrinter({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, (DictKeysViewType, DictValuesViewType, DictItemsViewType)):
                # Dict views use their own operator<< for printing
                parts.append(self._gen_expr_deref(arg))
            elif isinstance(arg_type, (ListType, ListRepeatType, ArrayType, SpanType)):
                # Sequence containers use ListPrinter for [a, b, c] formatting
                if isinstance(arg, TpyArrayLiteral):
                    # Array literals need explicit type for ListPrinter CTAD
                    cpp_type = arg_type.to_cpp()
                    parts.append(f'tpy::ListPrinter({cpp_type}{self._gen_expr(arg)})')
                else:
                    parts.append(f'tpy::ListPrinter({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, TypeParamRef):
                # Generic type parameter -- use ValuePrinter for runtime dispatch
                parts.append(f'tpy::ValuePrinter({self._gen_expr_deref(arg)})')
            else:
                # FixedInt, Char, Bool, literals, etc. - direct output
                expr_code = self._gen_expr_deref(arg)
                # 8-bit integers need cast to avoid char interpretation in std::cout
                if isinstance(arg_type, FixedIntType) and arg_type.bits == 8:
                    expr_code = f"static_cast<int>({expr_code})"
                parts.append(expr_code)

        # Add end string
        if end_str:
            parts.append(f'"{end_str}"')

        return "std::cout << " + " << ".join(parts)
