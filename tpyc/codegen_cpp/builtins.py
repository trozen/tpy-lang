"""
TurboPython Builtin Code Generation

Code generation for builtin functions, type constructors, and print().
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, FixedIntType, BigIntType, IntLiteralType, FloatType, Float32Type, BoolType, StrType, CharType,
    NamedType, OptionalType, NoneType, TypeParamRef, TypeParamKind, FunctionInfo, RecordInfo,
    ListType, ListRepeatType, DictType, SetType, DictKeysViewType, DictValuesViewType, DictItemsViewType,
    ArrayType, SpanType, TupleType, OwnType, is_protocol_type, unwrap_readonly, is_any_str_type, is_any_bytes_type,
    BytesType, ByteArrayType, BytesViewType,
)
from ..parse import (
    TpyExpr, TpyCall, TpyStrLiteral, TpyArrayLiteral, TpyNoneLiteral, TpyCoerce,
    TpyFieldAccess,
)

from .context import escape_cpp_string, CodeGenError, expand_cpp_template, qualify_native_name

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
        self._get_cpp_declared_type = None

    def set_expr_generator(self, gen_expr, gen_expr_deref, gen_call_arg,
                           get_cpp_declared_type):
        """Set expression generator functions (to break circular dependency)."""
        self._gen_expr = gen_expr
        self._gen_expr_deref = gen_expr_deref
        self._gen_call_arg = gen_call_arg
        self._get_cpp_declared_type = get_cpp_declared_type

    def gen_call_from_fi(self, fi: FunctionInfo, receiver: str | None,
                         gen_args: list[str], *,
                         self_type: TpyType | None = None,
                         type_args: tuple[TpyType, ...] | None = None,
                         type_subst: dict[str, TpyType] | None = None,
                         result_type: TpyType | None = None) -> str:
        """Generate a call from a resolved FunctionInfo.

        Unified entry point for cpp_template, native_function, and native member calls.
        receiver is None for free function calls (no self).
        self_type: receiver's TpyType (for {cpp} expansion in method templates).
        type_args: inferred type arguments paired with fi.type_params by position.
        type_subst: explicit name-to-type mapping (for class-level type params
            where fi.type_params may be empty). Takes precedence over type_args.
        result_type: concrete result type for {cpp} substitution in constructors.
        """
        if fi.cpp_template:
            template = fi.cpp_template
            if result_type is not None and "{cpp}" in template:
                template = template.replace("{cpp}", result_type.to_cpp())
            # Build effective type substitution
            effective_subst: dict[str, TpyType] | None = None
            if type_subst:
                effective_subst = type_subst
            elif type_args and fi.type_params:
                effective_subst = dict(zip(fi.type_params, type_args))
            if effective_subst:
                for name, typ in effective_subst.items():
                    placeholder = f"{{{name}}}"
                    if placeholder in template and hasattr(typ, "to_cpp"):
                        template = template.replace(placeholder, typ.to_cpp())
            if receiver is not None:
                return expand_cpp_template(template, receiver, *gen_args,
                                           self_type=self_type)
            return template.format(*gen_args)
        if fi.native_function and fi.native_name:
            all_args = [receiver] + gen_args if receiver is not None else gen_args
            return f"{qualify_native_name(fi.native_name)}({', '.join(all_args)})"
        if fi.native_name:
            args_str = ", ".join(gen_args)
            if receiver is not None:
                return f"{receiver}.{fi.native_name}({args_str})"
            return f"{qualify_native_name(fi.native_name)}({args_str})"
        args_str = ", ".join(gen_args)
        if receiver is not None:
            return f"{receiver}.{fi.name}({args_str})"
        return f"{fi.name}({args_str})"

    def get_type_method_fi(self, tpy_type: TpyType, method_name: str) -> FunctionInfo | None:
        """Look up a single-overload method's FunctionInfo from the registry.

        Returns None for multi-overload methods (use resolved_function_info instead).
        Falls back to dunder protocol templates for protocol types.
        """
        record_info = self.ctx.analyzer.registry.get_record_for_type(tpy_type)
        if record_info:
            overloads = record_info.get_method_overloads(method_name)
            if len(overloads) > 1:
                if method_name == "__getitem__":
                    return None
                raise RuntimeError(
                    f"get_type_method_fi called for multi-overload method "
                    f"'{method_name}' on {tpy_type}; use resolved_function_info instead"
                )
            if overloads:
                fi = overloads[0]
                if fi.cpp_template or fi.native_function or fi.native_name:
                    return fi
        # Dunder protocol templates (e.g., __getitem__ -> ::tpy::__getitem__)
        if is_protocol_type(tpy_type):
            from ..modules import get_dunder_cpp_template
            tmpl = get_dunder_cpp_template(method_name)
            if tmpl:
                from ..typesys import VOID
                return FunctionInfo(name=method_name, params=[], return_type=VOID,
                                    cpp_template=tmpl)
        return None

    def gen_method_from_function_info(self, obj: str, args: list[TpyExpr],
                                      method: FunctionInfo) -> str:
        """Generate method call code from a FunctionInfo with pre-generated args."""
        gen_args = [self._gen_call_arg(arg, method.params[i].type if i < len(method.params) else None,
                                       inline_template=True)
                    for i, arg in enumerate(args)]
        return self.gen_call_from_fi(method, obj, gen_args)


    def _match_overload_args(self, args: list[TpyExpr],
                             overloads: list[FunctionInfo]) -> tuple[FunctionInfo, list[str]]:
        """Match a builtin call to an overload and generate C++ arg expressions.

        Returns (matched_overload, gen_args). Raises RuntimeError if no match.
        """
        arg_types = [self.ctx.get_expr_type(arg) for arg in args]

        for overload in overloads:
            if len(overload.params) != len(args):
                continue
            if all(self._builtin_codegen_type_matches(arg, arg_t, ptype)
                   for arg, arg_t, (_, ptype) in zip(args, arg_types, overload.params)):
                gen_args = [self._gen_expr_deref(arg, ptype)
                            for arg, (_, ptype) in zip(args, overload.params)]
                return overload, gen_args

        raise RuntimeError(f"No matching overload for {overloads[0].name if overloads else '?'}")

    def gen_range_args(self, expr: TpyCall) -> list[str]:
        """Generate individual C++ arg expressions for range().

        Returns the generated arg list (e.g. ["0", "10", "2"]) rather than
        formatting into the full template string.  Used by the counter-loop
        optimisation in statements.py.
        """
        fi = expr.resolved_function_info
        return [self._gen_expr_deref(arg, ptype)
                for arg, (_, ptype) in zip(expr.args, fi.params)]

    def gen_template_or_native_call(self, args: list[TpyExpr],
                                    overloads: list[FunctionInfo], *,
                                    fi: FunctionInfo | None = None,
                                    type_args: tuple[TpyType, ...] | None = None) -> str:
        """Generate C++ for a @cpp_template or @native function call.

        Generates arg expressions, then delegates to gen_call_from_fi.
        fi: resolved FunctionInfo (when available from sema).
        type_args: inferred type arguments for generic calls.
        """
        if fi and (fi.cpp_template or fi.native_name):
            if fi.cpp_template:
                # Template strings handle move semantics themselves (may embed std::move)
                gen_args = [self._gen_expr_deref(arg, ptype)
                            for arg, (_, ptype) in zip(args, fi.params)]
            else:
                # Native functions: use gen_call_arg for auto-consuming
                # Iterable[Own[T]] and auto-move on Own[T] params
                gen_args = [self._gen_call_arg(arg, ptype)
                            for arg, (_, ptype) in zip(args, fi.params)]
            return self.gen_call_from_fi(fi, None, gen_args, type_args=type_args)
        # Fallback: re-resolve (shouldn't normally be needed)
        overload, gen_args = self._match_overload_args(args, overloads)
        return self.gen_call_from_fi(overload, None, gen_args, type_args=type_args)

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

        # Determine separator (default is space)
        sep_str = " "
        if "sep" in kwargs:
            sep_expr = kwargs["sep"]
            if isinstance(sep_expr, TpyStrLiteral):
                sep_str = escape_cpp_string(sep_expr.value)

        if not args:
            if end_str:
                return f'std::cout << "{end_str}"'
            return ""

        parts = []
        for i, arg in enumerate(args):
            if i > 0:
                parts.append(f'"{sep_str}"')

            arg_type = unwrap_readonly(self.types.get_resolved_type(arg))
            if isinstance(arg_type, OwnType):
                arg_type = arg_type.wrapped

            # When sema narrowed an optional field to its inner type,
            # the C++ field is still std::optional<T> -- use the declared
            # type for print formatting decisions.
            if (not isinstance(arg_type, OptionalType)
                    and isinstance(arg, TpyFieldAccess)):
                declared = self._get_cpp_declared_type(arg)
                if isinstance(declared, OptionalType):
                    arg_type = declared

            if isinstance(arg, TpyNoneLiteral):
                parts.append('"None"')
            elif isinstance(arg, TpyStrLiteral):
                parts.append(f'"{escape_cpp_string(arg.value)}"')
            elif self.types.is_runtime_bigint(arg, arg_type):
                # BigInt has operator<< for std::ostream, no .to_string() needed
                parts.append(self._gen_expr_deref(arg))
            elif isinstance(arg_type, FloatType):
                parts.append(f'::tpy::print_float({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, Float32Type):
                parts.append(f'::tpy::print_float(static_cast<double>({self._gen_expr_deref(arg)}))')
            elif isinstance(arg_type, BoolType):
                # Bool uses Python-style formatting via ::tpy::print_bool
                parts.append(f'::tpy::print_bool({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, OptionalType) and arg_type.uses_pointer_repr():
                if isinstance(arg, TpyFieldAccess):
                    # Field access produces std::optional<T> -- use print_optional_val
                    parts.append(f'::tpy::print_optional_val({self._gen_expr(arg)})')
                else:
                    # Pointer-local/function return produces T* -- use print_optional
                    parts.append(f'::tpy::print_optional({self._gen_expr(arg)})')
            elif isinstance(arg_type, OptionalType) and not arg_type.uses_pointer_repr():
                # Optional value-type: use print_optional_val with inner-type-aware formatting
                inner = arg_type.inner
                gen = self._gen_expr_deref(arg)
                inner_cpp = inner.to_cpp()
                if isinstance(inner, BoolType):
                    parts.append(f'::tpy::print_optional_val<::tpy::print_bool, {inner_cpp}>({gen})')
                elif isinstance(inner, (FloatType, Float32Type)):
                    parts.append(f'::tpy::print_optional_val<::tpy::print_float, {inner_cpp}>({gen})')
                else:
                    parts.append(f'::tpy::print_optional_val({gen})')
            elif isinstance(arg_type, TupleType):
                parts.append(f'::tpy::TuplePrinter({self._gen_expr_deref(arg)})')
            elif is_any_str_type(arg_type):
                # Strings print as-is (not using ListPrinter)
                parts.append(self._gen_expr_deref(arg))
            elif isinstance(arg_type, ByteArrayType):
                parts.append(f'::tpy::ByteArrayPrinter({self._gen_expr_deref(arg)})')
            elif is_any_bytes_type(arg_type):
                parts.append(f'::tpy::BytesPrinter({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, DictType):
                # Dict uses DictPrinter for {k: v, ...} formatting
                parts.append(f'::tpy::DictPrinter({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, SetType):
                # Set uses SetPrinter for {a, b, c} or set() formatting
                parts.append(f'::tpy::SetPrinter({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, (DictKeysViewType, DictValuesViewType, DictItemsViewType)):
                # Dict views use their own operator<< for printing
                parts.append(self._gen_expr_deref(arg))
            elif isinstance(arg_type, (ListType, ListRepeatType, ArrayType, SpanType)):
                # Sequence containers use ListPrinter for [a, b, c] formatting
                if isinstance(arg, TpyArrayLiteral):
                    # Array literals need explicit type for ListPrinter CTAD
                    cpp_type = arg_type.to_cpp()
                    parts.append(f'::tpy::ListPrinter({cpp_type}{self._gen_expr(arg)})')
                else:
                    parts.append(f'::tpy::ListPrinter({self._gen_expr_deref(arg)})')
            elif isinstance(arg_type, TypeParamRef):
                # Generic type parameter -- use ValuePrinter for runtime dispatch
                parts.append(f'::tpy::ValuePrinter({self._gen_expr_deref(arg)})')
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
