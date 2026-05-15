"""
TurboPython Builtin Code Generation

Code generation for builtin functions, type constructors, and print().
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, IntLiteralType,
    NominalType, OptionalType, NoneType, TypeParamRef, TypeParamKind, FunctionInfo, RecordInfo,
    ListRepeatType,
    TupleType, OwnType, is_protocol_type, unwrap_readonly, is_any_str_type, is_any_bytes_type,
    make_ref,
    is_float_type, is_integer_type,
)
from ..parse import (
    TpyExpr, TpyCall, TpyStrLiteral, TpyArrayLiteral, TpyNoneLiteral, TpyCoerce,
    TpyBoolLiteral, TpyFieldAccess,
)

from .context import escape_cpp_string, CodeGenError, expand_cpp_template, qualify_native_name, cpp_string_literal_expr
from ..type_def_registry import (
    is_dict_view, is_set, is_dict, is_array, is_span, is_list,
    is_fixed_int_type, is_big_int_type, is_bool_type, is_float32_type, is_float64_type,
    is_bytearray_type, int_traits_of, is_enum_type, enum_info_of,
)

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver


# float(str) special-value tokens that fold to constexpr numeric_limits at
# codegen, bypassing the non-constexpr runtime tpy::float_from_str. Matches
# CPython's case-insensitive, whitespace-trimming semantics. Note: CPython
# preserves the sign bit for float("-nan"), so emitting -quiet_NaN() (IEEE 754
# sign-bit flip) is bit-for-bit equivalent, not just functionally-isnan.
_FLOAT_STR_CONSTANTS: dict[str, str] = {
    "nan": "std::numeric_limits<double>::quiet_NaN()",
    "+nan": "std::numeric_limits<double>::quiet_NaN()",
    "-nan": "-std::numeric_limits<double>::quiet_NaN()",
    "inf": "std::numeric_limits<double>::infinity()",
    "+inf": "std::numeric_limits<double>::infinity()",
    "-inf": "-std::numeric_limits<double>::infinity()",
    "infinity": "std::numeric_limits<double>::infinity()",
    "+infinity": "std::numeric_limits<double>::infinity()",
    "-infinity": "-std::numeric_limits<double>::infinity()",
}


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
                         type_args: tuple[TpyType, ...] | None = None) -> str:
        """Generate a call from a resolved FunctionInfo.

        Unified entry point for cpp_template, native_function, and native member calls.
        receiver is None for free function calls (no self).
        self_type: receiver's TpyType (for {cpp} expansion in method templates).
        type_args: inferred type arguments paired with fi.type_params by position.
        """
        if fi.cpp_template:
            template = fi.cpp_template
            # Substitute method-level type params
            effective_subst: dict[str, TpyType] | None = None
            if type_args and fi.type_params:
                effective_subst = dict(zip(fi.type_params, type_args))
            if effective_subst:
                for name, typ in effective_subst.items():
                    placeholder = f"{{{name}}}"
                    if placeholder in template and hasattr(typ, "to_cpp"):
                        template = template.replace(placeholder, typ.to_cpp_stored())
            # {cpp} substitutes the (substituted) return-type spelling.
            # Used by free functions whose template needs the full
            # return-type C++ shape rather than just `{T}` -- e.g.
            # `unsafe_cast[T] -> Ptr[T]` wants `Ptr[None]` to lower to
            # `void*` (PtrType's special-case) rather than `std::monostate*`.
            if "{cpp}" in template and fi.return_type is not None:
                ret_type = fi.return_type
                if effective_subst:
                    ret_type = self.types.substitute_type_params(ret_type, effective_subst)
                template = template.replace("{cpp}", ret_type.to_cpp())
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
                    # Multi-overload __getitem__: find the integer-index overload
                    for fi in overloads:
                        if (len(fi.params) == 1
                                and is_integer_type(fi.params[0].type)
                                and (fi.cpp_template or fi.native_function or fi.native_name)):
                            return fi
                    return None
                if method_name == "__setitem__":
                    # Multi-overload __setitem__: find the integer-index overload
                    for fi in overloads:
                        if (len(fi.params) == 2
                                and is_integer_type(fi.params[0].type)
                                and (fi.cpp_template or fi.native_function or fi.native_name)):
                            return fi
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
                gen_args = [self._gen_call_arg(arg, ptype, inline_template=True)
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

    @staticmethod
    def _try_float_str_fold(fi: FunctionInfo, args: list[TpyExpr]) -> str | None:
        """Fold `float("nan"/"inf"/"-inf"/...)` to a constexpr numeric_limits.

        Returns the C++ expression string if the call resolves to the builtin
        `float.__init__(str)` with a recognized special-value literal arg;
        otherwise None (caller proceeds with the normal native dispatch that
        emits `tpy::float_from_str(...)`).
        """
        if not (fi.is_method and fi.name == "__init__" and len(args) == 1):
            return None
        if fi.owning_type_qname != "builtins.float":
            return None
        arg = args[0]
        if not isinstance(arg, TpyStrLiteral):
            return None
        return _FLOAT_STR_CONSTANTS.get(arg.value.strip().lower())

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
            # float("nan"/"inf"/"-inf"/aliases) -> constexpr numeric_limits fold.
            # Defensive: gen_template_or_native_call is currently called for
            # free functions / module functions, not record __init__, so this
            # won't fire for float.__init__ today (that path goes through
            # _gen_call / _gen_method_call direct calls to gen_call_from_fi,
            # which have their own fold invocations). Kept here so a future
            # caller that passes fi=float.__init__ through this entry point
            # still folds correctly. Gated on fi.is_method + fi.owning_type_qname
            # so user shadows of `float` don't fold.
            folded = self._try_float_str_fold(fi, args)
            if folded is not None:
                return folded
            # cpp_template substitutes the arg expression textually, so a
            # bare lvalue arg is fine when it's not a last-use consume --
            # skip gen_call_arg's lvalue->rvalue copy-temp in that case.
            inline_template = bool(fi.cpp_template)
            gen_args = [self._gen_call_arg(arg, ptype, inline_template=inline_template)
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
            if is_big_int_type(param_type):
                return self.types.is_runtime_bigint(arg, arg_type)
            if is_fixed_int_type(param_type):
                return not self.types.is_runtime_bigint(arg, arg_type)
        # INT TypeParamRef can match Int32 or BigInt (it's a compile-time constant)
        if isinstance(arg_type, TypeParamRef) and arg_type.kind == TypeParamKind.INT:
            return is_integer_type(param_type)
        # TpyCoerce nodes match their expected type
        if isinstance(arg, TpyCoerce):
            return arg.expected_type == param_type
        return False

    def gen_print(self, args: list[TpyExpr], kwargs: dict[str, TpyExpr] = None) -> str:
        """Generate stream call for print().

        Default sink is std::cout. With `file=`, uses ::tpy::as_ostream(<f>)
        to dispatch on the static type (TextFile, StdStream, std::ostream&,
        or generic Writable via streambuf adapter). With `flush=True`,
        appends `<< std::flush` to the chain. sep= and end= accept literals
        or runtime string expressions.
        """
        kwargs = kwargs or {}

        # sep / end resolve to a single ready-to-emit C++ token (or None to
        # skip emission). Literals shorten to a quoted string; runtime exprs
        # render via _gen_expr_deref. Empty literals are skipped entirely so
        # `print(..., sep="")` doesn't emit a redundant `<< ""`.
        def chain_token(kw_name: str, default_literal: str) -> str | None:
            kw_expr = kwargs.get(kw_name)
            if kw_expr is None:
                return f'"{default_literal}"' if default_literal else None
            if isinstance(kw_expr, TpyStrLiteral):
                if not kw_expr.value:
                    return None
                return cpp_string_literal_expr(kw_expr.value)
            return self._gen_expr_deref(kw_expr)

        end_token = chain_token("end", "\\n")
        sep_token = chain_token("sep", " ")

        # Sink: default cout, or as_ostream(file) when file= is provided.
        if "file" in kwargs:
            sink = f"::tpy::as_ostream({self._gen_expr_deref(kwargs['file'])})"
        else:
            sink = "std::cout"

        # flush=True appends `<< std::flush` to the chain.
        flush_expr = kwargs.get("flush")
        flush_on = isinstance(flush_expr, TpyBoolLiteral) and flush_expr.value

        if not args:
            tail = []
            if end_token is not None:
                tail.append(end_token)
            if flush_on:
                tail.append("std::flush")
            if not tail:
                return ""
            return f"{sink} << " + " << ".join(tail)

        parts = []
        for i, arg in enumerate(args):
            if i > 0 and sep_token is not None:
                parts.append(sep_token)

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
            elif isinstance(arg_type, NoneType):
                # NoneType-typed expression (e.g. `x = await none_coro()`).
                # std::monostate has no operator<<, so a direct `std::cout
                # << x` won't compile; evaluate the expression for side
                # effects, discard, then emit the literal.
                expr_code = self._gen_expr_deref(arg)
                parts.append(f'((void)({expr_code}), "None")')
            elif isinstance(arg, TpyStrLiteral):
                parts.append(cpp_string_literal_expr(arg.value))
            elif self.types.is_runtime_bigint(arg, arg_type):
                # BigInt has operator<< for std::ostream, no .to_string() needed
                parts.append(self._gen_expr_deref(arg))
            elif is_float64_type(arg_type):
                parts.append(f'::tpy::print_float({self._gen_expr_deref(arg)})')
            elif is_float32_type(arg_type):
                parts.append(f'::tpy::print_float(static_cast<double>({self._gen_expr_deref(arg)}))')
            elif is_bool_type(arg_type):
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
                if is_bool_type(inner):
                    parts.append(f'::tpy::print_optional_val<::tpy::print_bool, {inner_cpp}>({gen})')
                elif is_float_type(inner):
                    parts.append(f'::tpy::print_optional_val<::tpy::print_float, {inner_cpp}>({gen})')
                else:
                    parts.append(f'::tpy::print_optional_val({gen})')
            elif isinstance(arg_type, TupleType):
                parts.append(f'::tpy::TuplePrinter({self._gen_expr_deref(arg)})')
            elif is_any_str_type(arg_type):
                # Strings print as-is (not using ListPrinter)
                parts.append(self._gen_expr_deref(arg))
            elif is_bytearray_type(arg_type):
                parts.append(f'::tpy::ByteArrayPrinter({self._gen_expr_deref(arg)})')
            elif is_any_bytes_type(arg_type):
                parts.append(f'::tpy::BytesPrinter({self._gen_expr_deref(arg)})')
            elif is_dict(arg_type):
                # Dict uses DictPrinter for {k: v, ...} formatting
                parts.append(f'::tpy::DictPrinter({self._gen_expr_deref(arg)})')
            elif is_set(arg_type):
                # Set uses SetPrinter for {a, b, c} or set() formatting
                parts.append(f'::tpy::SetPrinter({self._gen_expr_deref(arg)})')
            elif is_dict_view(arg_type):
                # Dict views use their own operator<< for printing
                parts.append(self._gen_expr_deref(arg))
            elif is_array(arg_type) or is_span(arg_type) or is_list(arg_type) or isinstance(arg_type, ListRepeatType):
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
            elif is_enum_type(arg_type) and (einfo := enum_info_of(arg_type)) is not None and einfo.is_native:
                # @native enums: no operator<< is emitted (would conflict
                # with user-provided one). Route through __repr__ which
                # the runtime template resolves via EnumUtil. Tpy-defined
                # enums keep the raw-emit path below (their operator<< is
                # still emitted; preserves existing behavior).
                parts.append(f'::tpy::__repr__({self._gen_expr_deref(arg)})')
            else:
                # FixedInt, Char, Bool, literals, etc. - direct output
                expr_code = self._gen_expr_deref(arg)
                # 8-bit integers need cast to avoid char interpretation in std::cout
                arg_tr = int_traits_of(arg_type)
                if arg_tr is not None and arg_tr.bits == 8:
                    expr_code = f"static_cast<int>({expr_code})"
                parts.append(expr_code)

        if end_token is not None:
            parts.append(end_token)

        if flush_on:
            parts.append("std::flush")

        return f"{sink} << " + " << ".join(parts)
