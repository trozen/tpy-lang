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
    BuilderContext, MacroArg, MacroArgs, TypeInfo, MacroError,
    macro_deps, types, ast,
)

macro_deps("tpy", "sys")


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
        "help_text",
    )

    def __init__(
        self, *, is_flag: bool, flag_names: list[str], name: str,
        dest: str, arg_type: str, action: str, default, has_default: bool,
        const=None, has_const: bool = False,
        choices: list | None = None, required: bool = False,
        nargs=None, help_text: str | None = None,
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
        self.help_text = help_text  # source text for the `--help` printer

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

        Mirrors CPython argparse: an optional flag that wasn't given
        on the command line and has no ``default=`` returns ``None``.
        Positional arguments are always required (or carry an explicit
        default for ``nargs='?'``), so positionals never produce
        Optional fields.

        For list-typed actions (``append`` / ``extend`` / ``store``
        with multi-valued nargs), an absent flag also produces ``None``
        rather than an empty list -- the synthesizer accumulates into
        a local list and reconciles to ``None`` post-loop when the
        flag was never seen.
        """
        if not self.is_flag:
            return False
        if self.has_default:
            return False
        if self.required:
            return False
        if self.is_list_field:
            return True
        if self.action == "store_const":
            return True
        if self.action == "store" and self.nargs == "?" and not self.has_const:
            return True
        if self.action == "store" and self.nargs is None:
            return True
        return False

    @property
    def is_optional_list_field(self) -> bool:
        """True when the synthesized field is ``Optional[list[T]]``.

        These specs use the accumulator + post-loop reconciliation
        pattern: the flag handler writes into an accumulator local,
        and the post-loop fixup either copies it into the dest field
        (flag was seen) or leaves the dest at ``None``.
        """
        return self.is_list_field and self.is_optional_field

    @property
    def field_type(self):
        # Field type follows action / nargs.
        if self.action in ("store_true", "store_false"):
            return types.bool
        if self.action == "count":
            return types.bigint
        scalar = _scalar_type(self.arg_type) if self.action != "store_const" \
            else _python_value_type(self.const)
        if self.is_optional_list_field:
            return types.optional(types.list(scalar))
        if self.is_list_field:
            return types.list(scalar)
        if self.is_optional_field:
            return types.optional(scalar)
        return scalar


# ---------------------------------------------------------------------------
# Internal: resolution helpers
# ---------------------------------------------------------------------------

# Limited set of types accepted by ``type=`` in this slice. v2 will
# add user-pluggable types via an ArgType[T] protocol. ``_FIXED_INT_TPY_TYPES``
# is the source of truth -- ``_FIXED_INT_TYPES`` and ``_ALLOWED_TYPES``
# derive from it.
#
# Float32 isn't included yet -- ``Float32(runtime_str)`` doesn't lower
# to ``float32_from_str`` the way ``Int32(runtime_str)`` lowers to
# ``from_str_check<int32_t>``, so the synthesized parse function would
# emit a plain C-style ``float(string)`` cast that fails at C++
# compile time. Add Float32 once the codegen gap closes.
_FIXED_INT_TPY_TYPES = {
    "Int8": types.int8, "Int16": types.int16,
    "Int32": types.int32, "Int64": types.int64,
    "UInt8": types.uint8, "UInt16": types.uint16,
    "UInt32": types.uint32, "UInt64": types.uint64,
}
_FIXED_INT_TYPES: frozenset[str] = frozenset(_FIXED_INT_TPY_TYPES.keys())
_ALLOWED_TYPES: frozenset[str] = frozenset(
    {"str", "int", "float"} | _FIXED_INT_TYPES
)

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

    Fixed-width int (Int8..Int64, UInt8..UInt64) and Float32 map to
    the corresponding TPy primitive, so ``type=Int32`` produces an
    Int32 field rather than a BigInt one. CPython's argparse can run
    the same source via ``lib/cpy/`` shims (the user's ``Int32`` etc.
    are plain ``int`` callables there).
    """
    if arg_type in _FIXED_INT_TPY_TYPES:
        return _FIXED_INT_TPY_TYPES[arg_type]
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
        f"supported: int, float, str, "
        f"Int8/16/32/64, UInt8/16/32/64"
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

        help_text = ctx.kwarg_str(args, "help")
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
                nargs=nargs, help_text=help_text,
            )
        else:
            # Optional flag(s). Absent flags fall back to their default
            # (or None when no default is given; field type becomes
            # Optional[T] or Optional[list[T]] depending on action).
            # nargs='?' on a flag uses const when the flag is bare and
            # value when the flag carries one.
            name = flags[0]
            dest = _dest_for_flags(flags, explicit_dest)
            for f in flags:
                if f in ("-h", "--help"):
                    ctx.error(
                        "argparse: -h / --help is reserved by the auto-"
                        "generated help printer; remove it from your "
                        "add_argument() call (custom add_help=False is "
                        "not supported yet)"
                    )
            spec = _ArgSpec(
                is_flag=True, flag_names=list(flags), name=name, dest=dest,
                arg_type=arg_type, action=action,
                default=default, has_default=has_default,
                const=const, has_const=has_const,
                choices=choices, required=required_kw,
                nargs=nargs, help_text=help_text,
            )

        if spec.dest in self._dest_seen:
            ctx.error(f"argparse: duplicate argument destination {spec.dest!r}")
        self._dest_seen.add(spec.dest)
        self.specs.append(spec)

    @builder_terminal
    def parse_args(self, ctx: BuilderContext, args: MacroArgs) -> TypeInfo:
        # CPython's ``parse_args()`` falls back to ``sys.argv[1:]``
        # when called with no arguments. ``sys`` is injected into the
        # user's macro_ns via this module's ``macro_deps("tpy", "sys")``
        # declaration, so the synthesized rewrite can reference it
        # without the user having imported sys explicitly.
        if not args.positional:
            # ``list(...)`` materializes a fresh list[str] from the
            # span returned by ``sys.argv[1:]`` so the synthesized
            # parse fn (which takes list[str]) accepts it.
            args.positional.append(MacroArg(
                expr=ast.quote_expr("list(sys.argv[1:])"),
                type=TypeInfo("?", _tpy_type=None),
            ))

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

        # Synthesize the help-printer first so the parse fn's
        # `-h` / `--help` detection prelude can reference its name.
        # Pre-render the entire help text at macro time -- the printer
        # body is just `print(<literal>); sys.exit(0)`.
        help_fn_name = ctx.fresh_module_name("argparse_help")
        help_text = _format_help_text(self.specs, self.description)
        ctx.emit_function(
            help_fn_name, [], types.void,
            ast.quote(f"print({help_text!r})\nsys.exit(Int32(0))"),
        )

        usage_text = _format_usage(self.specs)
        body = _build_parse_body(
            self.specs, record_name, help_fn_name, usage_text,
        )

        # ``argv`` is ``list[str]`` rather than ``Span[str]`` so an
        # empty list literal at the call site (``parse_args([])``)
        # infers its element type from the parameter -- empty-literal
        # inference doesn't reach through Span[T] context today.
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

def _build_parse_body(
    specs: list[_ArgSpec], record_name: str, help_fn_name: str,
    usage_text: str,
) -> list:
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

    # Usage line bound as a local so each parse-error site can write
    # `usage + "\\nprog: error: <msg>"` to stderr without re-rendering.
    src_lines.append(f"__tpy_argparse_usage = {usage_text!r}")

    # Help-detection prelude: scan argv for ``-h`` / ``--help`` BEFORE
    # any other dispatch. The synthesized help fn prints help and
    # calls sys.exit(0), so the loop never returns from the call --
    # but its return type is ``None``, so sema sees normal flow and
    # the post-call increment compiles fine.
    src_lines.append("__tpy_argparse_h = 0")
    src_lines.append("while __tpy_argparse_h < len(argv):")
    src_lines.append(
        '    if argv[__tpy_argparse_h] == "-h" '
        'or argv[__tpy_argparse_h] == "--help":'
    )
    src_lines.append(f"        {help_fn_name}()")
    src_lines.append("    __tpy_argparse_h = __tpy_argparse_h + 1")

    # Empty parser: no add_argument() was called. Reject any argv
    # tokens (matching CPython argparse) and return an empty record
    # without entering the main loop -- the loop body would otherwise
    # never advance __tpy_argparse_i and hang on non-empty argv.
    if not specs:
        src_lines.append("if len(argv) != 0:")
        src_lines.extend(_error_emit_lines(
            "    ", '"unrecognized arguments: " + argv[0]',
        ))
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
        src_lines.extend(_error_emit_lines(
            "            ",
            '"unexpected positional argument: " + __tpy_argparse_tok',
        ))
    elif not first:
        src_lines.append("    else:")
        src_lines.extend(_error_emit_lines(
            "        ",
            '"unknown argument: " + __tpy_argparse_tok',
        ))

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
        src_lines.extend(_error_emit_lines(
            "    ", '"missing required positional argument(s)"',
        ))
    # nargs='+' on the trailing positional must have got at least
    # one value; the dispatch sets pi only when consumed, so the
    # count check above already covers this.
    for s in required_flags:
        src_lines.append(f"if not __tpy_argparse_seen_{s.dest}:")
        src_lines.extend(_error_emit_lines(
            "    ", repr(f"missing required argument: {s.flag_names[0]}"),
        ))

    # --- 4b. Optional[list[T]] reconciliation ---
    # Assign each accumulator into its dest field when the flag was
    # actually seen. ``tpy.copy`` is here for the implicit-copy
    # warning, not for correctness -- assigning a ``list[T]`` local
    # into an ``Optional[list[T]]`` slot warns ("use copy() to make
    # this explicit") even when the local is at its last use.
    # Codegen lowers ``copy(acc)`` to a ``vector(acc)`` copy ctor, so
    # this is technically one redundant allocation per seen flag.
    # Replace with a true move once TPy core gains move-into-Optional.
    for s in specs:
        if s.is_optional_list_field:
            src_lines.append(f"if {_seen_local(s)}:")
            src_lines.append(f"    {s.dest} = tpy.copy({_acc_local(s)})")

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
    if spec.is_optional_list_field:
        # Three locals: dest field (Optional[list[T]] = None), the
        # accumulator the flag handler writes into, and a seen flag
        # so the post-loop reconciliation can distinguish "absent"
        # from "present with zero values".
        elem = _scalar_type(spec.arg_type)
        return [
            ast.var_decl(
                spec.dest, types.optional(types.list(elem)), ast.none_lit()
            ),
            ast.var_decl(
                _acc_local(spec), types.list(elem), ast.list_lit()
            ),
            *ast.quote(f"{_seen_local(spec)} = False"),
        ]
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


def _acc_local(spec: _ArgSpec) -> str:
    return f"__tpy_argparse_acc_{spec.dest}"


def _seen_local(spec: _ArgSpec) -> str:
    return f"__tpy_argparse_seen_{spec.dest}"


def _list_target(spec: _ArgSpec) -> str:
    """Local the flag handler writes into: accumulator for
    Optional[list[T]] (post-loop reconciled via tpy.copy), dest field
    directly otherwise.
    """
    return _acc_local(spec) if spec.is_optional_list_field else spec.dest


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

    target = _list_target(spec)

    # Value-taking actions: store / append / extend, with optional nargs.
    if spec.nargs is None:
        # Single value, advance by 2.
        value_expr = _value_expr_src(spec, "argv[__tpy_argparse_i + 1]")
        L.append(f"{indent}if __tpy_argparse_i + 1 >= len(argv):")
        L.extend(_error_emit_lines(
            f"{indent}    ", '"missing value for " + __tpy_argparse_tok',
        ))
        if spec.choices:
            tmp = f"__tpy_argparse_v_{spec.dest}"
            L.append(f"{indent}{tmp} = {value_expr}")
            for line in _choices_check_lines(spec, tmp, indent=indent):
                L.append(line)
            if a == "store":
                L.append(f"{indent}{spec.dest} = {tmp}")
            else:
                L.append(f"{indent}{target}.append({tmp})")
        else:
            if a == "store":
                L.append(f"{indent}{spec.dest} = {value_expr}")
            else:
                L.append(f"{indent}{target}.append({value_expr})")
        L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_i + 2")
        if spec.is_optional_list_field:
            L.append(f"{indent}{_seen_local(spec)} = True")
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
    if a == "store":
        # Reassign to empty list -- field type was set at init.
        L.append(f"{indent}{target} = []")
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
        L.append(f"{indent}    {target}.append({tmp})")
    else:
        L.append(f"{indent}    {target}.append({inner_value})")
    L.append(f"{indent}    __tpy_argparse_j = __tpy_argparse_j + 1")
    consumed = "(__tpy_argparse_j - __tpy_argparse_i - 1)"
    if isinstance(spec.nargs, int):
        L.append(f"{indent}if {consumed} != {spec.nargs}:")
        L.extend(_error_emit_lines(
            f"{indent}    ",
            f'__tpy_argparse_tok + " requires exactly {spec.nargs} value(s)"',
        ))
    elif spec.nargs == "+":
        L.append(f"{indent}if {consumed} == 0:")
        L.extend(_error_emit_lines(
            f"{indent}    ",
            '__tpy_argparse_tok + " requires at least one value"',
        ))
    L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_j")
    if spec.is_optional_list_field:
        L.append(f"{indent}{_seen_local(spec)} = True")
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
        L.extend(_error_emit_lines(
            f"{indent}    ",
            repr(f"positional {spec.name!r} requires {N} value(s)"),
        ))
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
    # For fixed-width primitives, wrap with the constructor so the
    # literal carries the field's type rather than the default int /
    # float literal type.
    if spec.has_default:
        rendered = _literal_repr(spec.default, spec.arg_type)
        if spec.arg_type in _FIXED_INT_TYPES:
            return f"{spec.arg_type}({rendered})"
        return rendered
    if spec.action in ("store_true", "store_false"):
        return "False" if spec.action == "store_true" else "True"
    if spec.action == "count":
        return "0"
    if spec.arg_type == "int":
        return "0"
    if spec.arg_type == "float":
        return "0.0"
    if spec.arg_type in _FIXED_INT_TYPES:
        # Use the constructor form so the literal type matches the
        # field type regardless of options.json's default_int.
        return f"{spec.arg_type}(0)"
    return '""'


def _choices_check_lines(spec: _ArgSpec, value_var: str, *, indent: str) -> list[str]:
    """Source-text lines that emit a parse error to stderr + exit(2)
    if value_var is not in the spec's choices=. Empty when no choices=.
    """
    if not spec.choices:
        return []
    options = ", ".join(_literal_repr(c, _python_type_name(c)) for c in spec.choices)
    out = [f"{indent}if {value_var} not in ({options},):"]
    out.extend(_error_emit_lines(
        f"{indent}    ",
        f'"invalid choice for {spec.name}: " + str({value_var})',
    ))
    return out


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
    if spec.arg_type in _FIXED_INT_TYPES:
        # Fixed-width int constructors accept a string at runtime and
        # parse it (panicking on overflow / invalid).
        return f"{spec.arg_type}({source_expr})"
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


def _error_emit_lines(indent: str, msg_expr: str) -> list[str]:
    """Render the parse-error sequence at ``indent``.

    Prints ``<usage>\\nprog: error: <msg>`` to stderr and calls
    ``sys.exit(Int32(2))``. ``msg_expr`` is a TPy source expression
    for the error message (string-typed, may concat a runtime
    token). The synthesized parse fn binds ``__tpy_argparse_usage``
    near its top, so each error site reuses the formatted line
    without re-rendering.
    """
    return [
        f'{indent}print(__tpy_argparse_usage, '
        f'"prog: error: " + {msg_expr}, sep="\\n", file=sys.stderr)',
        f"{indent}sys.exit(Int32(2))",
    ]


# ---------------------------------------------------------------------------
# Help-text formatting (also reused by parse-error path)
# ---------------------------------------------------------------------------

# Hardcoded for v1. CPython uses ``os.path.basename(sys.argv[0])`` --
# adding ``prog=`` to ArgumentParser is Tier 2 future work.
_DEFAULT_PROG = "prog"
_HELP_OPT_FORM = "-h, --help"
_HELP_OPT_DESC = "show this help message and exit"


def _metavar_for(spec: _ArgSpec) -> str:
    """Metavar shown in usage / help for a value-taking arg.

    Flags use the dest in uppercase (matching CPython); positionals
    use their literal name. ``metavar=`` override is Tier 2.
    """
    return spec.dest.upper() if spec.is_flag else spec.name


def _nargs_pattern(token: str, nargs) -> str:
    """Render the metavar pattern for a given nargs value.

    ``token`` is the metavar to repeat; ``nargs`` is the spec's nargs
    field. Returns just the metavar pattern (no flag prefix).
    """
    if nargs is None:
        return token
    if isinstance(nargs, int):
        return " ".join([token] * nargs)
    if nargs == "?":
        return f"[{token}]"
    if nargs == "*":
        return f"[{token} ...]"
    if nargs == "+":
        return f"{token} [{token} ...]"
    return token


def _flag_usage_token(spec: _ArgSpec) -> str:
    """One flag's contribution to the usage line. Shows the first
    flag form only (CPython does the same to keep usage compact).
    """
    first = spec.flag_names[0]
    metavar = _metavar_for(spec)
    if spec.action in _VALUE_FREE_ACTIONS:
        body = first
    else:
        body = f"{first} {_nargs_pattern(metavar, spec.nargs)}"
    return body if spec.required else f"[{body}]"


def _positional_usage_token(spec: _ArgSpec) -> str:
    return _nargs_pattern(spec.name, spec.nargs)


def _flag_help_signature(spec: _ArgSpec) -> str:
    """All flag forms with metavar, for the options section.

    e.g. ``-n NAME, --name NAME`` -- CPython repeats the metavar on
    every form for clarity.
    """
    metavar = _metavar_for(spec)
    if spec.action in _VALUE_FREE_ACTIONS:
        return ", ".join(spec.flag_names)
    pattern = _nargs_pattern(metavar, spec.nargs)
    return ", ".join(f"{f} {pattern}" for f in spec.flag_names)


def _format_usage(specs: list[_ArgSpec], prog: str = _DEFAULT_PROG) -> str:
    """Render just the usage line. Reused by parse-error path."""
    parts = ["usage:", prog, "[-h]"]
    for s in specs:
        if s.is_flag:
            parts.append(_flag_usage_token(s))
    for s in specs:
        if not s.is_flag:
            parts.append(_positional_usage_token(s))
    return " ".join(parts)


def _format_help_text(
    specs: list[_ArgSpec], description: str | None,
    prog: str = _DEFAULT_PROG,
) -> str:
    """Render the full --help output: usage line, description (if
    any), per-section listings of positionals and options.
    """
    flags = [s for s in specs if s.is_flag]
    positionals = [s for s in specs if not s.is_flag]

    pos_rows = [(_positional_usage_token(s), s.help_text or "")
                for s in positionals]
    flag_rows = [(_flag_help_signature(s), s.help_text or "") for s in flags]
    # Match CPython's ``self._action_max_length + 2``: align all
    # rows (including the auto ``-h, --help`` entry) to the longest
    # signature plus two spaces of separation.
    rows_with_help = [_HELP_OPT_FORM, *(sig for sig, _ in pos_rows),
                      *(sig for sig, _ in flag_rows)]
    pad = max(len(s) for s in rows_with_help) + 2

    lines = [_format_usage(specs, prog), ""]
    if description:
        lines.append(description)
        lines.append("")

    if positionals:
        lines.append("positional arguments:")
        for sig, text in pos_rows:
            lines.append(f"  {sig.ljust(pad)}{text}".rstrip())
        lines.append("")

    lines.append("options:")
    lines.append(f"  {_HELP_OPT_FORM.ljust(pad)}{_HELP_OPT_DESC}")
    for sig, text in flag_rows:
        lines.append(f"  {sig.ljust(pad)}{text}".rstrip())
    return "\n".join(lines)
