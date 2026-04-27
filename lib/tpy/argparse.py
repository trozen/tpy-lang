# tpy: macro_module
"""argparse builder-trace macro -- v1.

Mirrors a slice of CPython's argparse.ArgumentParser surface as a
@builder_macro: the user writes ordinary builder-style code, the
compiler synthesizes a per-call-site record + parse function on
parse_args(...). Under CPython the same source resolves to the
stdlib argparse module (this file is invisible outside tpyc's lib
search path), so test programs run under both backends without
code changes.

v1 surface, future-work tiers, and known CPython divergences are
tracked in ``docs/MACRO_DESIGN.md`` (the argparse use-case section)
and mirrored in ``docs/STDLIB_ROADMAP.md``. Keeping the canonical
list there avoids drift between the macro module and the design doc.
"""

from tpyc.macro_api import (
    builder_macro, builder_method, builder_terminal,
    BuilderContext, MacroArgs, TypeInfo, MacroError,
    types, ast,
)


# ---------------------------------------------------------------------------
# Internal: ArgSpec -- one registered argument
# ---------------------------------------------------------------------------

class _ArgSpec:
    """One @builder_method add_argument(...) call, fully resolved at
    macro time. Carries everything the terminal needs to synthesize
    init / dispatch / record-construction code.
    """
    __slots__ = (
        "is_flag", "flag_names", "name", "dest",
        "arg_type", "action", "default", "has_default",
        "const", "has_const",
        "choices", "required",
        "nargs",
    )

    def __init__(
        self, *, is_flag: bool, flag_names: list[str], name: str,
        dest: str, arg_type: str, action: str, default, has_default: bool,
        const=None, has_const: bool = False,
        choices: list | None = None, required: bool = False,
        nargs=None,
    ) -> None:
        self.is_flag = is_flag
        self.flag_names = flag_names
        self.name = name
        self.dest = dest
        self.arg_type = arg_type   # "str" | "int" | "float" (only for value-taking actions)
        self.action = action
        self.default = default
        self.has_default = has_default
        self.const = const
        self.has_const = has_const
        self.choices = choices     # None or list of macro-time literals
        self.required = required   # always True for positionals; user-controllable for flags
        self.nargs = nargs         # None | "?" | "*" | "+" | int

    @property
    def is_list_field(self) -> bool:
        """True when the synthesized record field is list[T]."""
        if self.action in ("append", "extend"):
            return True
        if self.action == "store" and isinstance(self.nargs, int):
            return True
        if self.action == "store" and self.nargs in ("*", "+"):
            return True
        return False

    @property
    def is_optional_field(self) -> bool:
        """True when the synthesized field is wrapped in Optional[T].

        Mirrors CPython argparse for scalar fields: an optional flag
        that wasn't given on the command line and has no ``default=``
        returns ``None``. Positional arguments are always required
        (or carry an explicit default for ``nargs='?'``), so positionals
        never produce Optional fields. List-typed fields are NOT made
        Optional today -- v1 leaves them as ``list[T] = []`` even when
        absent, diverging from CPython's None-on-absence behavior; the
        accumulator-and-reconcile pattern that would close that gap
        runs into TPy's owned-copy warning under macro-synthesized
        bodies and isn't worth the workaround until macro_deps wiring
        for builder-trace macros lands.
        """
        if not self.is_flag:
            return False
        if self.has_default:
            return False
        if self.required:
            return False
        if self.is_list_field:
            return False
        if self.action == "store_const":
            return True
        if self.action == "store" and self.nargs == "?" and not self.has_const:
            return True
        if self.action == "store" and self.nargs is None:
            return True
        return False

    @property
    def field_type(self):
        # Field type follows action / nargs.
        if self.action in ("store_true", "store_false"):
            return types.bool
        if self.action == "count":
            return types.bigint
        scalar = _scalar_type(self.arg_type) if self.action != "store_const" \
            else _python_value_type(self.const)
        if self.is_list_field:
            return types.list(scalar)
        if self.is_optional_field:
            return types.optional(scalar)
        return scalar


# ---------------------------------------------------------------------------
# Internal: resolution helpers
# ---------------------------------------------------------------------------

# Limited set of types accepted by ``type=`` in this slice. v2 will add
# user-pluggable types via an ArgType[T] protocol.
_ALLOWED_TYPES: frozenset[str] = frozenset({"str", "int", "float"})

# Actions that consume value(s) from argv and apply ``type=`` to them.
_VALUE_TAKING_ACTIONS: frozenset[str] = frozenset({"store", "append", "extend"})

# Actions that take no value (the flag itself fully specifies the result).
_VALUE_FREE_ACTIONS: frozenset[str] = frozenset(
    {"store_true", "store_false", "count", "store_const"}
)

# Actions that produce a list-typed field.
_LIST_ACTIONS: frozenset[str] = frozenset({"append", "extend"})

_ALLOWED_ACTIONS: frozenset[str] = _VALUE_TAKING_ACTIONS | _VALUE_FREE_ACTIONS


def _scalar_type(arg_type: str):
    """Map a Python type-name string to the TPy type for the record field.

    ``type=int`` maps to BigInt (Python's arbitrary-precision int) and
    ``type=str`` maps to ``String`` (owned), both matching CPython
    argparse's behavior under the cpy phase. ``String`` over ``StrView``
    in particular keeps the synthesized record self-contained -- the
    field's lifetime is independent of whatever backed argv. A future
    macro extension can opt into ``StrView`` fields for zero-alloc
    parsing once a use case justifies it.
    """
    if arg_type == "int":
        return types.bigint
    if arg_type == "float":
        return types.float64
    return types.str  # default & "str"


def _python_value_type(value):
    """TPy type for a Python value extracted via eval_literal_or_final.

    Used to derive a record field type from a const= literal. Mirrors
    _scalar_type's choice of BigInt for ints so const= and type= give
    the same field type for the same conceptual value.
    """
    if isinstance(value, bool):
        return types.bool
    if isinstance(value, int):
        return types.bigint
    if isinstance(value, float):
        return types.float64
    if isinstance(value, str):
        return types.str
    raise MacroError(
        f"argparse: const= must be a bool/int/float/str literal, "
        f"got {type(value).__name__}"
    )


def _resolve_type_kwarg(ctx: BuilderContext, args: MacroArgs) -> str:
    """Read ``type=`` and return its name as a string. Defaults to "str"."""
    ti = ctx.kwarg_type(args, "type")
    if ti is None:
        return "str"
    if ti.name in _ALLOWED_TYPES:
        return ti.name
    ctx.error(
        f"argparse: unsupported type={ti.name!r}; "
        f"only int / float / str are supported in this phase"
    )


def _dest_for_flags(flag_names: list[str], explicit_dest: str | None) -> str:
    """Compute the destination field name from the flag spelling.

    Mirrors CPython argparse: prefer the long flag with leading dashes
    stripped and remaining dashes replaced by underscores; otherwise
    use the (single) short flag.
    """
    if explicit_dest is not None:
        return explicit_dest
    long_flags = [f for f in flag_names if f.startswith("--")]
    if long_flags:
        return long_flags[0][2:].replace("-", "_")
    short = flag_names[0]
    return short[1:] if short.startswith("-") else short


def _is_flag(name: str) -> bool:
    return name.startswith("-")


def _resolve_nargs_kwarg(ctx: BuilderContext, args: MacroArgs):
    """Read ``nargs=`` and validate. Returns None | '?' | '*' | '+' | int."""
    ma = ctx.kwarg_macroarg(args, "nargs")
    if ma is None:
        return None
    v = ctx.eval_literal_or_final(ma.expr)
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        ctx.error(
            f"argparse: nargs= must be a string '?'/'*'/'+' or a "
            f"positive int, got {type(v).__name__}"
        )
    if isinstance(v, str):
        if v not in ("?", "*", "+"):
            ctx.error(f"argparse: nargs={v!r} is not supported")
        return v
    # int
    if v <= 0:
        ctx.error(f"argparse: nargs= must be a positive integer, got {v}")
    return v


def _action_implicit_default(action: str):
    """Default value applied when the user omits default= for a value-
    free action. Mirrors CPython argparse where it can: store_true
    defaults to False, store_false defaults to True, count and
    store_const default to None there but we materialize 0 / None to
    keep the typed record well-formed.
    """
    if action == "store_true":
        return False
    if action == "store_false":
        return True
    if action == "count":
        return 0
    return None


# ---------------------------------------------------------------------------
# Public macro: ArgumentParser
# ---------------------------------------------------------------------------

@builder_macro
class ArgumentParser:
    def __init__(self, ctx: BuilderContext, args: MacroArgs) -> None:
        self.description: str | None = ctx.kwarg_str(args, "description")
        self.specs: list[_ArgSpec] = []
        self._dest_seen: set[str] = set()

    @builder_method
    def add_argument(self, ctx: BuilderContext, args: MacroArgs) -> None:
        names = ctx.positional_strs(args)
        if not names:
            ctx.error("argparse: add_argument() requires at least one name")

        # ``help=`` is accepted and stored for future help-printer
        # generation, but not used in the synthesized parse code.
        ctx.kwarg_str(args, "help")  # validated and discarded
        explicit_dest = ctx.kwarg_str(args, "dest")
        required_kw = ctx.kwarg_bool(args, "required", False)
        choices_arg = ctx.kwarg_macroarg(args, "choices")
        choices: list | None = None
        if choices_arg is not None:
            choices = ctx.eval_sequence_of_literal_or_final(choices_arg.expr)
            if not choices:
                ctx.error("argparse: choices= must be non-empty")

        flags = [n for n in names if _is_flag(n)]
        positionals = [n for n in names if not _is_flag(n)]
        if flags and positionals:
            ctx.error(
                "argparse: add_argument() positional and optional names "
                "cannot be mixed"
            )

        action = ctx.kwarg_str(args, "action") or "store"
        if action not in _ALLOWED_ACTIONS:
            ctx.error(
                f"argparse: action={action!r} is not supported "
                f"(supported: store, store_true, store_false, count, "
                f"append, extend, store_const)"
            )

        # type= is meaningful only for value-taking actions. Reject
        # explicit type= on the value-free ones to mirror CPython.
        if action in _VALUE_FREE_ACTIONS and ctx.kwarg_macroarg(args, "type") is not None:
            ctx.error(
                f"argparse: type= cannot be combined with action={action!r}"
            )
        arg_type = _resolve_type_kwarg(ctx, args)

        nargs = _resolve_nargs_kwarg(ctx, args)
        if nargs is not None and action in _VALUE_FREE_ACTIONS and action != "store_const":
            ctx.error(
                f"argparse: nargs= cannot be combined with action={action!r}"
            )
        # store_const + nargs is allowed only as nargs='?' (and means
        # "if flag-bare use const, if flag-with-value use value, if
        # absent use default"). For now reject the explicit pairing.
        if action == "store_const" and nargs is not None:
            ctx.error(
                "argparse: nargs= is not supported with action='store_const'"
            )
        # extend without nargs is the confusing CPython case (iterates
        # the converted value); require nargs explicitly.
        if action == "extend" and nargs is None:
            ctx.error(
                "argparse: action='extend' requires nargs= (otherwise "
                "the converted value gets iterated, which is rarely "
                "what callers want)"
            )

        # const= is required for store_const and for store + nargs='?'
        # (where it's the value used when the flag appears bare).
        const_arg = ctx.kwarg_macroarg(args, "const")
        const = None
        has_const = False
        if const_arg is not None:
            if action == "store_const":
                pass  # always allowed
            elif action == "store" and nargs == "?":
                pass  # const provides the bare-flag value
            else:
                ctx.error(
                    f"argparse: const= is only valid with action='store_const' "
                    f"or action='store' + nargs='?' (got action={action!r}, "
                    f"nargs={nargs!r})"
                )
            const = ctx.eval_literal_or_final(const_arg.expr)
            has_const = True
        elif action == "store_const":
            ctx.error("argparse: action='store_const' requires const=")

        # Default handling. ``default=`` is a literal at macro time;
        # absent for required positionals, None-typed for absent flags
        # without explicit default.
        default_arg = ctx.kwarg_macroarg(args, "default")
        if default_arg is not None:
            default = ctx.eval_literal_or_final(default_arg.expr)
            has_default = True
        else:
            default = _action_implicit_default(action)
            has_default = default is not None

        if positionals:
            if action != "store":
                ctx.error(
                    f"argparse: action={action!r} is only valid for "
                    f"optional flags, not positional arguments"
                )
            if has_default and nargs != "?":
                # nargs='?' positionals can use default= when the slot
                # is missing from argv; non-'?' positionals are always
                # required.
                ctx.error(
                    "argparse: positional arguments may only specify "
                    "default= when nargs='?'"
                )
            if required_kw:
                ctx.error(
                    "argparse: required= is meaningless for positional "
                    "arguments (they are always required)"
                )
            name = positionals[0]
            dest = explicit_dest if explicit_dest is not None else name
            spec = _ArgSpec(
                is_flag=False, flag_names=[], name=name, dest=dest,
                arg_type=arg_type, action=action,
                default=default, has_default=has_default,
                choices=choices, required=True,
                nargs=nargs,
            )
        else:
            # Optional flag(s). Absent flags fall back to their default
            # (or None when no default is given; field type becomes
            # Optional[T] or Optional[list[T]] depending on action).
            # nargs='?' on a flag uses const when the flag is bare and
            # value when the flag carries one.
            name = flags[0]
            dest = _dest_for_flags(flags, explicit_dest)
            spec = _ArgSpec(
                is_flag=True, flag_names=list(flags), name=name, dest=dest,
                arg_type=arg_type, action=action,
                default=default, has_default=has_default,
                const=const, has_const=has_const,
                choices=choices, required=required_kw,
                nargs=nargs,
            )

        if spec.dest in self._dest_seen:
            ctx.error(f"argparse: duplicate argument destination {spec.dest!r}")
        self._dest_seen.add(spec.dest)
        self.specs.append(spec)

    @builder_terminal
    def parse_args(self, ctx: BuilderContext, args: MacroArgs) -> TypeInfo:
        # CPython's ``parse_args()`` falls back to ``sys.argv[1:]``
        # when called with no arguments. The synthesized parse function
        # always requires explicit argv -- macro_deps wiring for
        # builder-trace macros (which would let us inject ``sys`` into
        # the user's macro_ns) isn't in place yet, so we reject the
        # bare-call form with a clearer macro-time error than the
        # downstream "expects 1 arg, got 0".
        if not args.positional:
            ctx.error(
                "argparse: parse_args() requires explicit argv in this "
                "compiler (CPython's sys.argv[1:] fallback is not yet "
                "supported); pass argv[1:] explicitly"
            )

        # Validate positional layout before code synthesis: ``*``,
        # ``+``, and ``?`` positionals must be the LAST positional --
        # earlier ones can't be deterministically consumed without
        # CPython's full regex-fitting algorithm.
        positional_specs = [s for s in self.specs if not s.is_flag]
        for i, s in enumerate(positional_specs):
            if s.nargs in ("*", "+", "?") and i != len(positional_specs) - 1:
                ctx.error(
                    f"argparse: positional {s.name!r} with "
                    f"nargs={s.nargs!r} must be the last positional"
                )

        record_name = ctx.fresh_module_name("argparse_args")
        record_fields = [(s.dest, s.field_type) for s in self.specs]
        record_type = ctx.emit_record(record_name, record_fields)

        body = _build_parse_body(self.specs, record_name)

        fn_name = ctx.fresh_module_name("argparse_parse")
        ctx.emit_function(
            fn_name,
            [("argv", types.list(types.str))],
            record_type.raw_type,
            body,
        )
        ctx.replace_call(fn_name, args)
        return record_type


# ---------------------------------------------------------------------------
# Internal: parse-function body synthesis
# ---------------------------------------------------------------------------

def _build_parse_body(specs: list[_ArgSpec], record_name: str) -> list:
    """Generate the full body of the synthesized parse function.

    Layout:
      <init defaults / typed empty lists>
      <seen-tracking for required flags>
      __tpy_argparse_i = 0
      __tpy_argparse_pi = 0
      while __tpy_argparse_i < len(argv):
          __tpy_argparse_tok = argv[__tpy_argparse_i]
          if __tpy_argparse_tok == "--flag1": ...handler advances i...
          elif ...: ...
          else: ...positional dispatch advances i and pi...
      <missing-positional check>
      <required-flag check>
      return Record(...)
    """
    init_stmts: list = []
    src_lines: list[str] = []

    # Empty parser: no add_argument() was called. Reject any argv
    # tokens (matching CPython argparse) and return an empty record
    # without entering the main loop -- the loop body would otherwise
    # never advance __tpy_argparse_i and hang on non-empty argv.
    if not specs:
        src_lines.append("if len(argv) != 0:")
        src_lines.append(
            "    raise ValueError("
            "\"argparse: unrecognized arguments: \" + argv[0])"
        )
        src_lines.append(f"return {record_name}()")
        return ast.quote("\n".join(src_lines))

    # --- 1. Initialization for each spec ---
    for s in specs:
        init_stmts.extend(_spec_init_stmts(s))

    # --- 2. Required-flag seen tracking ---
    required_flags = [s for s in specs if s.is_flag and s.required]
    for s in required_flags:
        src_lines.append(f"__tpy_argparse_seen_{s.dest} = False")

    # --- 3. Main loop ---
    src_lines.append("__tpy_argparse_i = 0")
    src_lines.append("__tpy_argparse_pi = 0")
    src_lines.append("while __tpy_argparse_i < len(argv):")
    src_lines.append("    __tpy_argparse_tok = argv[__tpy_argparse_i]")

    flag_specs = [s for s in specs if s.is_flag]
    positional_specs = [s for s in specs if not s.is_flag]

    # Flag dispatch
    first = True
    for s in flag_specs:
        match_expr = " or ".join(
            f"__tpy_argparse_tok == {f!r}" for f in s.flag_names
        )
        kw = "if" if first else "elif"
        first = False
        src_lines.append(f"    {kw} {match_expr}:")
        for line in _flag_handler_lines(s, indent="        "):
            src_lines.append(line)
        if s.required:
            src_lines.append(f"        __tpy_argparse_seen_{s.dest} = True")

    # Positional dispatch
    if positional_specs:
        kw = "else" if not first else "if True"
        src_lines.append(f"    {kw}:")
        inner_first = True
        for pi, s in enumerate(positional_specs):
            inner_kw = "if" if inner_first else "elif"
            inner_first = False
            src_lines.append(f"        {inner_kw} __tpy_argparse_pi == {pi}:")
            for line in _positional_handler_lines(s, indent="            "):
                src_lines.append(line)
        src_lines.append("        else:")
        src_lines.append(
            "            raise ValueError("
            "\"argparse: unexpected positional argument: \" + __tpy_argparse_tok)"
        )
    elif not first:
        src_lines.append("    else:")
        src_lines.append(
            "        raise ValueError("
            "\"argparse: unknown argument: \" + __tpy_argparse_tok)"
        )

    # --- 4. Post-loop validation ---
    # Missing-positional check: every required positional slot must
    # have been filled. Variable-nargs trailing positionals (* / ?)
    # are themselves optional; +/N/none are required.
    required_positional_count = sum(
        1 for s in positional_specs
        if s.nargs not in ("*", "?")
    )
    if required_positional_count > 0:
        src_lines.append(f"if __tpy_argparse_pi < {required_positional_count}:")
        src_lines.append(
            f"    raise ValueError("
            f"\"argparse: missing required positional argument(s)\")"
        )
    # nargs='+' on the trailing positional must have got at least
    # one value; the dispatch sets pi only when consumed, so the
    # count check above already covers this.
    for s in required_flags:
        src_lines.append(f"if not __tpy_argparse_seen_{s.dest}:")
        src_lines.append(
            f"    raise ValueError("
            f"\"argparse: missing required argument: {s.flag_names[0]}\")"
        )

    # --- 5. Construct & return ---
    ctor_args = ", ".join(s.dest for s in specs)
    src_lines.append(f"return {record_name}({ctor_args})")

    return init_stmts + ast.quote("\n".join(src_lines))


def _spec_init_stmts(spec: _ArgSpec) -> list:
    """Initialization statements for one spec.

    Type-bearing decls (typed list, Optional[T]) go through ``ast.*``
    so the annotation is a TpyType the per-module resolver doesn't
    need to import. Plain scalar inits go through source text so
    default-literal rendering stays compact.
    """
    if spec.is_list_field:
        elem = _scalar_type(spec.arg_type)
        return [ast.var_decl(spec.dest, types.list(elem), ast.list_lit())]
    if spec.is_optional_field:
        scalar = _scalar_type(spec.arg_type) if spec.action != "store_const" \
            else _python_value_type(spec.const)
        return [ast.var_decl(
            spec.dest, types.optional(scalar), ast.none_lit()
        )]
    return ast.quote(f"{spec.dest} = {_default_expr_src(spec)}")


def _flag_handler_lines(spec: _ArgSpec, *, indent: str) -> list[str]:
    """Source lines that consume the matched flag plus its values
    (per nargs/action) and advance __tpy_argparse_i.
    """
    L: list[str] = []
    a = spec.action
    if a == "store_true":
        L.append(f"{indent}{spec.dest} = True")
        L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + 1")
        return L
    if a == "store_false":
        L.append(f"{indent}{spec.dest} = False")
        L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + 1")
        return L
    if a == "count":
        L.append(f"{indent}{spec.dest} = {spec.dest} + 1")
        L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + 1")
        return L
    if a == "store_const":
        const_repr = _literal_repr(spec.const, _python_type_name(spec.const))
        L.append(f"{indent}{spec.dest} = {const_repr}")
        L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + 1")
        return L

    # Value-taking actions: store / append / extend, with optional nargs.
    if spec.nargs is None:
        # Single value, advance by 2.
        value_expr = _value_expr_src(spec, "argv[__tpy_argparse_i + 1]")
        L.append(f"{indent}if __tpy_argparse_i + 1 >= len(argv):")
        L.append(f"{indent}    raise ValueError("
                 f"\"argparse: missing value for \" + __tpy_argparse_tok)")
        if spec.choices:
            tmp = f"__tpy_argparse_v_{spec.dest}"
            L.append(f"{indent}{tmp} = {value_expr}")
            for line in _choices_check_lines(spec, tmp, indent=indent):
                L.append(line)
            if a == "store":
                L.append(f"{indent}{spec.dest} = {tmp}")
            else:
                L.append(f"{indent}{spec.dest}.append({tmp})")
        else:
            if a == "store":
                L.append(f"{indent}{spec.dest} = {value_expr}")
            else:
                L.append(f"{indent}{spec.dest}.append({value_expr})")
        L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + 2")
        return L

    if spec.nargs == "?":
        # 0 or 1 value: peek at next token; if absent or starts
        # with '-' use const, otherwise consume one value.
        L.append(
            f"{indent}if __tpy_argparse_i + 1 < len(argv) and not "
            f"argv[__tpy_argparse_i + 1].startswith(\"-\"):"
        )
        value_expr = _value_expr_src(spec, "argv[__tpy_argparse_i + 1]")
        if spec.choices:
            tmp = f"__tpy_argparse_v_{spec.dest}"
            L.append(f"{indent}    {tmp} = {value_expr}")
            for line in _choices_check_lines(spec, tmp, indent=indent + "    "):
                L.append(line)
            L.append(f"{indent}    {spec.dest} = {tmp}")
        else:
            L.append(f"{indent}    {spec.dest} = {value_expr}")
        L.append(f"{indent}    __tpy_argparse_i = __tpy_argparse_i + 2")
        L.append(f"{indent}else:")
        if spec.has_const:
            const_repr = _literal_repr(spec.const, _python_type_name(spec.const))
            L.append(f"{indent}    {spec.dest} = {const_repr}")
        # else: leave field at its initial default
        L.append(f"{indent}    __tpy_argparse_i = __tpy_argparse_i + 1")
        return L

    # nargs in {'*', '+', int}: scan forward consuming non-flag
    # tokens, appending into the field directly. The field is
    # already typed list[T] (or Optional[list[T]]) from the init
    # pass; for action=store the field is cleared at the top of
    # the match so repeated flags replace rather than accumulate.
    # For action=append/extend with Optional[list], lazy-init from
    # None to [] before the loop.
    if a == "store":
        # Reassign to empty list -- field type was set at init.
        L.append(f"{indent}{spec.dest} = []")
    L.append(f"{indent}__tpy_argparse_j = __tpy_argparse_i + 1")
    L.append(
        f"{indent}while __tpy_argparse_j < len(argv) and not "
        f"argv[__tpy_argparse_j].startswith(\"-\"):"
    )
    inner_value = _value_expr_src(spec, "argv[__tpy_argparse_j]")
    if spec.choices:
        tmp = f"__tpy_argparse_v_{spec.dest}"
        L.append(f"{indent}    {tmp} = {inner_value}")
        for line in _choices_check_lines(spec, tmp, indent=indent + "    "):
            L.append(line)
        L.append(f"{indent}    {spec.dest}.append({tmp})")
    else:
        L.append(f"{indent}    {spec.dest}.append({inner_value})")
    L.append(f"{indent}    __tpy_argparse_j = __tpy_argparse_j + 1")
    consumed = "(__tpy_argparse_j - __tpy_argparse_i - 1)"
    if isinstance(spec.nargs, int):
        L.append(f"{indent}if {consumed} != {spec.nargs}:")
        L.append(
            f"{indent}    raise ValueError("
            f"\"argparse: \" + __tpy_argparse_tok + "
            f"\" requires exactly {spec.nargs} value(s)\")"
        )
    elif spec.nargs == "+":
        L.append(f"{indent}if {consumed} == 0:")
        L.append(
            f"{indent}    raise ValueError("
            f"\"argparse: \" + __tpy_argparse_tok + "
            f"\" requires at least one value\")"
        )
    L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_j")
    return L


def _positional_handler_lines(spec: _ArgSpec, *, indent: str) -> list[str]:
    """Source lines that handle one positional slot when the loop
    falls through to the else branch. Each branch advances both
    __tpy_argparse_i and __tpy_argparse_pi.
    """
    L: list[str] = []
    if spec.nargs is None:
        value_expr = _value_expr_src(spec, "__tpy_argparse_tok")
        if spec.choices:
            tmp = f"__tpy_argparse_v_{spec.dest}"
            L.append(f"{indent}{tmp} = {value_expr}")
            for line in _choices_check_lines(spec, tmp, indent=indent):
                L.append(line)
            L.append(f"{indent}{spec.dest} = {tmp}")
        else:
            L.append(f"{indent}{spec.dest} = {value_expr}")
        L.append(f"{indent}__tpy_argparse_pi = __tpy_argparse_pi + 1")
        L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + 1")
        return L
    if isinstance(spec.nargs, int):
        N = spec.nargs
        L.append(f"{indent}if __tpy_argparse_i + {N} > len(argv):")
        L.append(
            f"{indent}    raise ValueError("
            f"\"argparse: positional {spec.name!r} requires {N} value(s)\")"
        )
        L.append(f"{indent}__tpy_argparse_k = 0")
        L.append(f"{indent}while __tpy_argparse_k < {N}:")
        idx_expr = "argv[__tpy_argparse_i + __tpy_argparse_k]"
        inner_value = _value_expr_src(spec, idx_expr)
        if spec.choices:
            tmp = f"__tpy_argparse_v_{spec.dest}"
            L.append(f"{indent}    {tmp} = {inner_value}")
            for line in _choices_check_lines(spec, tmp, indent=indent + "    "):
                L.append(line)
            L.append(f"{indent}    {spec.dest}.append({tmp})")
        else:
            L.append(f"{indent}    {spec.dest}.append({inner_value})")
        L.append(f"{indent}    __tpy_argparse_k = __tpy_argparse_k + 1")
        L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + {N}")
        L.append(f"{indent}__tpy_argparse_pi = __tpy_argparse_pi + 1")
        return L
    if spec.nargs == "?":
        # consume exactly one (the current token); falling off the end
        # is handled by the missing-positional check after the loop.
        value_expr = _value_expr_src(spec, "__tpy_argparse_tok")
        L.append(f"{indent}{spec.dest} = {value_expr}")
        L.append(f"{indent}__tpy_argparse_pi = __tpy_argparse_pi + 1")
        L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + 1")
        return L
    # nargs in {'*', '+'}: greedy -- consume the current token plus
    # all consecutive non-flag tokens that follow.
    value_expr = _value_expr_src(spec, "__tpy_argparse_tok")
    if spec.choices:
        tmp = f"__tpy_argparse_v_{spec.dest}"
        L.append(f"{indent}{tmp} = {value_expr}")
        for line in _choices_check_lines(spec, tmp, indent=indent):
            L.append(line)
        L.append(f"{indent}{spec.dest}.append({tmp})")
    else:
        L.append(f"{indent}{spec.dest}.append({value_expr})")
    L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + 1")
    L.append(
        f"{indent}while __tpy_argparse_i < len(argv) and not "
        f"argv[__tpy_argparse_i].startswith(\"-\"):"
    )
    inner_value = _value_expr_src(spec, "argv[__tpy_argparse_i]")
    if spec.choices:
        tmp = f"__tpy_argparse_v_{spec.dest}"
        L.append(f"{indent}    {tmp} = {inner_value}")
        for line in _choices_check_lines(spec, tmp, indent=indent + "    "):
            L.append(line)
        L.append(f"{indent}    {spec.dest}.append({tmp})")
    else:
        L.append(f"{indent}    {spec.dest}.append({inner_value})")
    L.append(f"{indent}    __tpy_argparse_i = __tpy_argparse_i + 1")
    L.append(f"{indent}__tpy_argparse_pi = __tpy_argparse_pi + 1")
    return L


def _default_expr_src(spec: _ArgSpec) -> str:
    """Source-text rendering of the spec's default expression.

    Only called for scalar-typed fields; list-typed fields are
    initialized via ast.var_decl in _spec_init_stmts.
    """
    # Both optional flags and ``nargs='?'`` positionals can carry a
    # user-supplied default literal; render it as-is when present.
    if spec.has_default:
        return _literal_repr(spec.default, spec.arg_type)
    if spec.action in ("store_true", "store_false"):
        return "False" if spec.action == "store_true" else "True"
    if spec.action == "count":
        return "0"
    if spec.arg_type == "int":
        return "0"
    if spec.arg_type == "float":
        return "0.0"
    return '""'


def _choices_check_lines(spec: _ArgSpec, value_var: str, *, indent: str) -> list[str]:
    """Source-text lines that raise ValueError if value_var is not in
    the spec's choices=. Returns an empty list when no choices=.
    """
    if not spec.choices:
        return []
    options = ", ".join(_literal_repr(c, _python_type_name(c)) for c in spec.choices)
    return [
        f"{indent}if {value_var} not in ({options},):",
        f"{indent}    raise ValueError("
        f"\"argparse: invalid choice for {spec.name}: \" + str({value_var}))",
    ]


def _python_type_name(value) -> str:
    """Map a Python value to its arg_type-style name. Used to render
    a const literal with the right repr fall-back."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _value_expr_src(spec: _ArgSpec, source_expr: str) -> str:
    """Wrap a source expression with the spec's type conversion."""
    if spec.arg_type == "int":
        return f"int({source_expr})"
    if spec.arg_type == "float":
        return f"float({source_expr})"
    return source_expr


def _literal_repr(value, arg_type: str) -> str:
    """Source-text rendering of a literal default. Macro-time literal
    values flow through eval_literal_or_final, so values are plain
    Python int / float / str / bool / None.
    """
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    return repr(value)
