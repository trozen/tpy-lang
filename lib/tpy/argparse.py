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


# Default ``prog`` used in synthesized usage / help / error text when
# the user doesn't pass ``prog=`` to ``ArgumentParser``. Hardcoded for
# v1; CPython uses ``os.path.basename(sys.argv[0])`` -- runtime-derived
# prog is Tier 2 follow-up. Defined up here (before any helper that
# uses it as a default-arg) so module load order is well-defined.
_DEFAULT_PROG = "prog"
_DEFAULT_ERROR_PREFIX = f"{_DEFAULT_PROG}: error: "


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
        "type_info", "action", "default", "has_default",
        "const", "has_const",
        "choices", "required",
        "nargs",
        "help_text",
        "metavar",
    )

    def __init__(
        self, *, is_flag: bool, flag_names: list[str], name: str,
        dest: str, type_info: TypeInfo, action: str, default, has_default: bool,
        const=None, has_const: bool = False,
        choices: list | None = None, required: bool = False,
        nargs=None, help_text: str | None = None,
        metavar: str | None = None,
    ) -> None:
        self.is_flag = is_flag
        self.flag_names = flag_names
        self.name = name
        self.dest = dest
        self.type_info = type_info  # resolved ``type=`` (defaults to str)
        self.action = action
        self.default = default
        self.has_default = has_default
        self.const = const
        self.has_const = has_const
        self.choices = choices     # None or list of macro-time literals
        self.required = required   # always True for positionals; user-controllable for flags
        self.nargs = nargs         # None | "?" | "*" | "+" | int
        self.help_text = help_text  # source text for the `--help` printer
        self.metavar = metavar     # display name override for usage / help

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
        if self.action == "store_const":
            scalar = _python_value_type_info(self.const).raw_type
        else:
            scalar = self.type_info.raw_type
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

# Actions that consume value(s) from argv and apply ``type=`` to them.
_VALUE_TAKING_ACTIONS: frozenset[str] = frozenset({"store", "append", "extend"})

# Actions that take no value (the flag itself fully specifies the result).
_VALUE_FREE_ACTIONS: frozenset[str] = frozenset(
    {"store_true", "store_false", "count", "store_const"}
)

# Actions that produce a list-typed field.
_LIST_ACTIONS: frozenset[str] = frozenset({"append", "extend"})

_ALLOWED_ACTIONS: frozenset[str] = _VALUE_TAKING_ACTIONS | _VALUE_FREE_ACTIONS


# Default ``type=`` is ``str`` (CPython parity). Cached as a TypeInfo
# so callers don't have to special-case the absent-kwarg path.
_STR_TYPE_INFO: TypeInfo = TypeInfo.from_tpy_type(types.str)


def _is_allowed_arg_type(ti: TypeInfo) -> bool:
    """Whether ``type=<ti>`` is supported as a value-taking arg type.

    Keyed on TypeInfo's category helpers rather than name lookup so new
    acceptable types get picked up via the type system, not a string
    table. ``is_float`` covers both ``float`` (Float64) and ``Float32``
    -- both lower to a constructor that accepts ``str`` at runtime.
    """
    return ti.is_str or ti.is_bigint or ti.is_int or ti.is_float


def _arg_type_name(ti: TypeInfo) -> str:
    """User-facing / source-text name for an arg type.

    Doubles as the constructor expression for value coercion: ``int(s)``
    for BigInt, ``Int32(s)`` for fixed-width, ``float(s)`` for Float64,
    ``Float32(s)`` for Float32. The ``str`` case is handled by callers
    that skip wrapping (no constructor needed when the field stays a
    plain string). ``is_float32`` is checked before ``is_float`` because
    ``is_float`` is true for both Float64 and Float32.
    """
    if ti.is_str:
        return "str"
    if ti.is_bigint:
        return "int"
    if ti.is_int:
        return ti.int_type_name
    if ti.is_float32:
        return "Float32"
    if ti.is_float:
        return "float"
    # Should not occur once _is_allowed_arg_type gates entries; the
    # raw_type's own repr is the safest fallback for diagnostics.
    return str(ti.raw_type)


def _python_value_type_info(value) -> TypeInfo:
    """TypeInfo for a Python literal value extracted via
    eval_literal_or_final. Used to derive a record field type from a
    ``const=`` literal so ``store_const`` and ``store + type=`` agree
    on the field type for the same conceptual value.
    """
    if isinstance(value, bool):
        return TypeInfo.from_tpy_type(types.bool)
    if isinstance(value, int):
        return TypeInfo.from_tpy_type(types.bigint)
    if isinstance(value, float):
        return TypeInfo.from_tpy_type(types.float64)
    if isinstance(value, str):
        return TypeInfo.from_tpy_type(types.str)
    raise MacroError(
        f"argparse: const= must be a bool/int/float/str literal, "
        f"got {type(value).__name__}"
    )


def _resolve_type_info(ctx: BuilderContext, args: MacroArgs) -> TypeInfo:
    """Read ``type=`` and return its TypeInfo. Defaults to ``str``."""
    ti = ctx.kwarg_type(args, "type")
    if ti is None:
        return _STR_TYPE_INFO
    if not _is_allowed_arg_type(ti):
        ctx.error(
            f"argparse: unsupported type={ti.name!r}; "
            f"supported: int, float, str, "
            f"Int8/16/32/64, UInt8/16/32/64, Float32"
        )
    return ti


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


def _check_default_elem(ctx: BuilderContext, value, ti: TypeInfo) -> None:
    """Validate a single element of a list-typed ``default=`` against
    the spec's resolved ``type=``. Bool literals are rejected upfront
    even for int / float types, since Python's ``isinstance(True, int)``
    quirk would otherwise let ``default=[True]`` slip through.
    """
    name = _arg_type_name(ti)
    if isinstance(value, bool):
        ctx.error(f"argparse: list default element must be {name}, got bool")
    if ti.is_str:
        ok = isinstance(value, str)
    elif ti.is_bigint or ti.is_int:
        ok = isinstance(value, int)
    elif ti.is_float:
        ok = isinstance(value, (int, float))
    else:
        ok = True  # _is_allowed_arg_type already gated unsupported types
    if not ok:
        ctx.error(
            f"argparse: list default element must be {name}, "
            f"got {type(value).__name__}"
        )


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
        # Help-text customization. ``prog`` substitutes for the
        # default ``"prog"`` placeholder in the usage line and
        # parse-error prefix; ``usage`` overrides the auto-generated
        # usage line entirely (CPython prefixes it with ``"usage: "``);
        # ``epilog`` is appended after the options block in --help.
        # ``add_help`` controls whether the ``-h`` / ``--help`` printer
        # is auto-emitted and the flag names reserved.
        self.prog: str | None = ctx.kwarg_str(args, "prog")
        self.usage: str | None = ctx.kwarg_str(args, "usage")
        self.epilog: str | None = ctx.kwarg_str(args, "epilog")
        self.add_help: bool = ctx.kwarg_bool(args, "add_help", True)
        self.specs: list[_ArgSpec] = []
        self._dest_seen: set[str] = set()

    @builder_method
    def add_argument(self, ctx: BuilderContext, args: MacroArgs) -> None:
        names = ctx.positional_strs(args)
        if not names:
            ctx.error("argparse: add_argument() requires at least one name")

        help_text = ctx.kwarg_str(args, "help")
        metavar = ctx.kwarg_str(args, "metavar")
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
        type_info = _resolve_type_info(ctx, args)

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
        # without explicit default. List literals are accepted only
        # for list-typed actions (append / extend / store + nargs=*/+/N).
        default_arg = ctx.kwarg_macroarg(args, "default")
        if default_arg is not None:
            default = ctx.eval_literal_or_final(default_arg.expr)
            has_default = True
            if isinstance(default, list):
                list_action_ok = (
                    action in _LIST_ACTIONS
                    or (action == "store"
                        and (isinstance(nargs, int) or nargs in ("*", "+")))
                )
                if not list_action_ok:
                    ctx.error(
                        f"argparse: list default is only valid for "
                        f"list-typed actions (append/extend or store + "
                        f"nargs=*/+/<int>); got action={action!r}, "
                        f"nargs={nargs!r}"
                    )
                for v in default:
                    _check_default_elem(ctx, v, type_info)
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
                type_info=type_info, action=action,
                default=default, has_default=has_default,
                choices=choices, required=True,
                nargs=nargs, help_text=help_text,
                metavar=metavar,
            )
        else:
            # Optional flag(s). Absent flags fall back to their default
            # (or None when no default is given; field type becomes
            # Optional[T] or Optional[list[T]] depending on action).
            # nargs='?' on a flag uses const when the flag is bare and
            # value when the flag carries one.
            name = flags[0]
            dest = _dest_for_flags(flags, explicit_dest)
            if self.add_help:
                for f in flags:
                    if f in ("-h", "--help"):
                        ctx.error(
                            "argparse: -h / --help is reserved by the "
                            "auto-generated help printer; pass "
                            "add_help=False to ArgumentParser() to "
                            "register your own"
                        )
            spec = _ArgSpec(
                is_flag=True, flag_names=list(flags), name=name, dest=dest,
                type_info=type_info, action=action,
                default=default, has_default=has_default,
                const=const, has_const=has_const,
                choices=choices, required=required_kw,
                nargs=nargs, help_text=help_text,
                metavar=metavar,
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

        prog = self.prog if self.prog is not None else _DEFAULT_PROG

        # Render usage once and reuse it for both the parse-error path
        # and the help-printer body so we don't walk specs twice.
        usage_text = _format_usage(
            self.specs, prog=prog, usage=self.usage,
            include_help_opt=self.add_help,
        )

        # Synthesize the help-printer (when add_help=True) so the parse
        # fn's `-h` / `--help` detection prelude can reference its name.
        # Pre-render the entire help text at macro time -- the printer
        # body is just `print(<literal>); sys.exit(0)`.
        help_fn_name: str | None = None
        if self.add_help:
            help_fn_name = ctx.fresh_module_name("argparse_help")
            help_text = _format_help_text(
                self.specs, self.description,
                usage_text=usage_text, epilog=self.epilog,
            )
            ctx.emit_function(
                help_fn_name, [], types.void,
                ast.quote(f"print({help_text!r})\nsys.exit(Int32(0))"),
            )
        body = _build_parse_body(
            self.specs, record_name, help_fn_name, usage_text,
            prog=prog, add_help=self.add_help,
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
    specs: list[_ArgSpec], record_name: str, help_fn_name: str | None,
    usage_text: str, *, prog: str, add_help: bool,
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
    error_prefix = f"{prog}: error: "

    # Usage line bound as a local so each parse-error site can write
    # `usage + "\\n<prog>: error: <msg>"` to stderr without re-rendering.
    # The prefix itself is inlined as a literal at each emit site
    # (threaded as a Python string through the helpers) so the
    # generated code stays compact when prog is at its default.
    src_lines.append(f"__tpy_argparse_usage = {usage_text!r}")

    # Help-detection prelude (only when add_help=True): scan argv for
    # ``-h`` / ``--help`` BEFORE any other dispatch. The synthesized
    # help fn prints help and calls sys.exit(0), so the loop never
    # returns from the call -- but its return type is ``None``, so
    # sema sees normal flow and the post-call increment compiles fine.
    if add_help:
        assert help_fn_name is not None
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
            error_prefix=error_prefix,
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
        for line in _flag_handler_lines(s, indent="        ",
                                         error_prefix=error_prefix):
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
            for line in _positional_handler_lines(
                s, indent="            ", error_prefix=error_prefix,
            ):
                src_lines.append(line)
        src_lines.append("        else:")
        src_lines.extend(_error_emit_lines(
            "            ",
            '"unexpected positional argument: " + __tpy_argparse_tok',
            error_prefix=error_prefix,
        ))
    elif not first:
        src_lines.append("    else:")
        src_lines.extend(_error_emit_lines(
            "        ",
            '"unknown argument: " + __tpy_argparse_tok',
            error_prefix=error_prefix,
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
            error_prefix=error_prefix,
        ))
    # nargs='+' on the trailing positional must have got at least
    # one value; the dispatch sets pi only when consumed, so the
    # count check above already covers this.
    for s in required_flags:
        src_lines.append(f"if not __tpy_argparse_seen_{s.dest}:")
        src_lines.extend(_error_emit_lines(
            "    ", repr(f"missing required argument: {s.flag_names[0]}"),
            error_prefix=error_prefix,
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
        elem = spec.type_info.raw_type
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
        elem = spec.type_info.raw_type
        if spec.has_default:
            # Non-empty list literal default. Element-typed list
            # initializer with each element wrapped through the
            # spec's value-conversion (handles fixed-width int
            # constructor wrapping the same way scalar defaults do).
            elements = [_render_default_elem(v, spec.type_info)
                        for v in spec.default]
            return [ast.var_decl(
                spec.dest, types.list(elem), ast.list_lit(elements),
            )]
        return [ast.var_decl(spec.dest, types.list(elem), ast.list_lit())]
    if spec.is_optional_field:
        if spec.action == "store_const":
            scalar = _python_value_type_info(spec.const).raw_type
        else:
            scalar = spec.type_info.raw_type
        return [ast.var_decl(
            spec.dest, types.optional(scalar), ast.none_lit()
        )]
    return ast.quote(f"{spec.dest} = {_default_expr_src(spec)}")


def _render_default_elem(value, ti: TypeInfo):
    """AST expression for one element of a list-typed default.

    Mirrors ``_default_expr_src``'s fixed-width wrapping at the AST
    level: ``Int32(1)`` / ``Float32(0.5)`` rather than a bare literal,
    so the typed list annotation stays consistent regardless of
    options.json defaults and Float32 elements don't widen to Float64.
    """
    if ti.is_str:
        return ast.str_lit(str(value))
    if ti.is_bigint:
        return ast.int_lit(int(value))
    if ti.is_float32:
        return ast.call("Float32", [ast.float_lit(float(value))])
    if ti.is_float:
        return ast.float_lit(float(value))
    # Fixed-width int: wrap with the constructor so the literal type
    # matches the field type regardless of options.json's default_int.
    return ast.call(_arg_type_name(ti), [ast.int_lit(int(value))])


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


def _flag_handler_lines(
    spec: _ArgSpec, *, indent: str,
    error_prefix: str = _DEFAULT_ERROR_PREFIX,
) -> list[str]:
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
        const_repr = _literal_repr(spec.const)
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
            error_prefix=error_prefix,
        ))
        if spec.choices:
            tmp = f"__tpy_argparse_v_{spec.dest}"
            L.append(f"{indent}{tmp} = {value_expr}")
            for line in _choices_check_lines(
                spec, tmp, indent=indent, error_prefix=error_prefix,
            ):
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
            for line in _choices_check_lines(
                spec, tmp, indent=indent + "    ", error_prefix=error_prefix,
            ):
                L.append(line)
            L.append(f"{indent}    {spec.dest} = {tmp}")
        else:
            L.append(f"{indent}    {spec.dest} = {value_expr}")
        L.append(f"{indent}    __tpy_argparse_i = __tpy_argparse_i + 2")
        L.append(f"{indent}else:")
        if spec.has_const:
            const_repr = _literal_repr(spec.const)
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
        for line in _choices_check_lines(
            spec, tmp, indent=indent + "    ", error_prefix=error_prefix,
        ):
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
            error_prefix=error_prefix,
        ))
    elif spec.nargs == "+":
        L.append(f"{indent}if {consumed} == 0:")
        L.extend(_error_emit_lines(
            f"{indent}    ",
            '__tpy_argparse_tok + " requires at least one value"',
            error_prefix=error_prefix,
        ))
    L.append(f"{indent}__tpy_argparse_i = __tpy_argparse_j")
    if spec.is_optional_list_field:
        L.append(f"{indent}{_seen_local(spec)} = True")
    return L


def _positional_handler_lines(
    spec: _ArgSpec, *, indent: str,
    error_prefix: str = _DEFAULT_ERROR_PREFIX,
) -> list[str]:
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
            for line in _choices_check_lines(
                spec, tmp, indent=indent, error_prefix=error_prefix,
            ):
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
            error_prefix=error_prefix,
        ))
        L.append(f"{indent}__tpy_argparse_k = 0")
        L.append(f"{indent}while __tpy_argparse_k < {N}:")
        idx_expr = "argv[__tpy_argparse_i + __tpy_argparse_k]"
        inner_value = _value_expr_src(spec, idx_expr)
        if spec.choices:
            tmp = f"__tpy_argparse_v_{spec.dest}"
            L.append(f"{indent}    {tmp} = {inner_value}")
            for line in _choices_check_lines(
                spec, tmp, indent=indent + "    ", error_prefix=error_prefix,
            ):
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
        for line in _choices_check_lines(
            spec, tmp, indent=indent, error_prefix=error_prefix,
        ):
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
        for line in _choices_check_lines(
            spec, tmp, indent=indent + "    ", error_prefix=error_prefix,
        ):
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
    ti = spec.type_info
    if spec.has_default:
        rendered = _literal_repr(spec.default)
        if ti.is_int or ti.is_float32:
            return f"{_arg_type_name(ti)}({rendered})"
        return rendered
    if spec.action in ("store_true", "store_false"):
        return "False" if spec.action == "store_true" else "True"
    if spec.action == "count":
        return "0"
    if ti.is_bigint:
        return "0"
    if ti.is_int or ti.is_float32:
        # Constructor form so the literal type matches the field type
        # (regardless of options.json's default_int, and so Float32
        # fields don't get a Float64 init that widens the inferred type).
        return f"{_arg_type_name(ti)}(0)"
    if ti.is_float:
        return "0.0"
    return '""'


def _choices_check_lines(
    spec: _ArgSpec, value_var: str, *, indent: str,
    error_prefix: str = _DEFAULT_ERROR_PREFIX,
) -> list[str]:
    """Source-text lines that emit a parse error to stderr + exit(2)
    if value_var is not in the spec's choices=. Empty when no choices=.
    """
    if not spec.choices:
        return []
    options = ", ".join(_literal_repr(c) for c in spec.choices)
    out = [f"{indent}if {value_var} not in ({options},):"]
    out.extend(_error_emit_lines(
        f"{indent}    ",
        f'"invalid choice for {spec.name}: " + str({value_var})',
        error_prefix=error_prefix,
    ))
    return out


def _value_expr_src(spec: _ArgSpec, source_expr: str) -> str:
    """Wrap a source expression with the spec's type conversion.

    Fixed-width int / BigInt / float constructors all accept a string
    at runtime and parse it (panicking on overflow / invalid). The
    ``str`` case skips wrapping since argv tokens are already strings.
    """
    if spec.type_info.is_str:
        return source_expr
    return f"{_arg_type_name(spec.type_info)}({source_expr})"


def _literal_repr(value) -> str:
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


def _error_emit_lines(
    indent: str, msg_expr: str, *, error_prefix: str = _DEFAULT_ERROR_PREFIX,
) -> list[str]:
    """Render the parse-error sequence at ``indent``.

    Prints ``<usage>\\n<prog>: error: <msg>`` to stderr and calls
    ``sys.exit(Int32(2))``. ``msg_expr`` is a TPy source expression
    for the error message (string-typed, may concat a runtime
    token). The ``error_prefix`` parameter is a Python string baked
    in at macro time so the generated code keeps the prefix as a
    plain string literal -- threading it through the helpers (rather
    than via a runtime local) keeps generated output stable for
    callers that don't pass ``prog=``.
    """
    return [
        f'{indent}print(__tpy_argparse_usage, '
        f'{error_prefix!r} + {msg_expr}, sep="\\n", file=sys.stderr)',
        f"{indent}sys.exit(Int32(2))",
    ]


# ---------------------------------------------------------------------------
# Help-text formatting (also reused by parse-error path)
# ---------------------------------------------------------------------------

_HELP_OPT_FORM = "-h, --help"
_HELP_OPT_DESC = "show this help message and exit"


def _metavar_for(spec: _ArgSpec) -> str:
    """Metavar shown in usage / help for a value-taking arg.

    Honors an explicit ``metavar=`` override; otherwise flags use the
    dest in uppercase (matching CPython) and positionals use their
    literal name.
    """
    if spec.metavar is not None:
        return spec.metavar
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
    return _nargs_pattern(_metavar_for(spec), spec.nargs)


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


_USAGE_TEXT_WIDTH = 80
_USAGE_PREFIX = "usage: "


def _format_usage(
    specs: list[_ArgSpec], *,
    prog: str = _DEFAULT_PROG,
    usage: str | None = None,
    include_help_opt: bool = True,
) -> str:
    """Render just the usage line. Reused by parse-error path.

    When ``usage`` is provided, it overrides the auto-generated tail
    after ``"usage: "`` (matching CPython's ``ArgumentParser(usage=)``
    behavior). When ``include_help_opt`` is False, the ``[-h]`` token
    is omitted from the auto-generated form.

    Long usage lines wrap at ``_USAGE_TEXT_WIDTH`` (80) cols, matching
    CPython argparse's behavior when stdout isn't a TTY. Continuation
    lines are indented to align past the prog name (or past
    ``"usage: "`` when prog is too long for that to fit).
    """
    if usage is not None:
        return _USAGE_PREFIX + usage
    opt_parts: list[str] = []
    if include_help_opt:
        opt_parts.append("[-h]")
    for s in specs:
        if s.is_flag:
            opt_parts.append(_flag_usage_token(s))
    pos_parts = [_positional_usage_token(s) for s in specs if not s.is_flag]

    # Try the single-line form first.
    flat = " ".join([prog, *opt_parts, *pos_parts]).rstrip()
    if len(_USAGE_PREFIX) + len(flat) <= _USAGE_TEXT_WIDTH:
        return _USAGE_PREFIX + flat

    # Wrap. Mirrors CPython argparse.HelpFormatter._format_usage:
    # short prog stays on row 1 (continuation aligns past it); long
    # prog gets its own row (continuation aligns past "usage: ").
    if len(_USAGE_PREFIX) + len(prog) <= 0.75 * _USAGE_TEXT_WIDTH:
        indent = " " * (len(_USAGE_PREFIX) + len(prog) + 1)
        opt_lines = _wrap_usage_parts(
            [prog, *opt_parts], indent, prefix=_USAGE_PREFIX,
        )
        pos_lines = _wrap_usage_parts(pos_parts, indent, prefix=None)
        body = "\n".join(opt_lines + pos_lines)
    else:
        indent = " " * len(_USAGE_PREFIX)
        wrapped = _wrap_usage_parts(
            [*opt_parts, *pos_parts], indent, prefix=None,
        )
        body = "\n".join([prog, *wrapped])
    return _USAGE_PREFIX + body


def _wrap_usage_parts(
    parts: list[str], indent: str, *, prefix: str | None,
) -> list[str]:
    """Greedy-wrap usage tokens to ``_USAGE_TEXT_WIDTH``.

    First line uses ``prefix`` as starter (the caller prepends the
    prefix; this function strips ``indent`` from the first line so
    the prefix lines up); continuation lines are emitted with
    ``indent`` already prepended. When ``prefix`` is None, every line
    starts with ``indent`` (no special-cased first row). Empty
    ``parts`` returns an empty list.
    """
    if not parts:
        return []
    lines: list[str] = []
    line: list[str] = []
    indent_length = len(indent)
    line_len = (len(prefix) if prefix is not None else indent_length) - 1
    for part in parts:
        if line and line_len + 1 + len(part) > _USAGE_TEXT_WIDTH:
            lines.append(indent + " ".join(line))
            line = []
            line_len = indent_length - 1
        line.append(part)
        line_len += len(part) + 1
    if line:
        lines.append(indent + " ".join(line))
    if prefix is not None:
        # First line carries the prefix instead of the indent; caller
        # prepends prefix once at the very start of the usage block.
        lines[0] = lines[0][indent_length:]
    return lines


def _format_help_text(
    specs: list[_ArgSpec], description: str | None, *,
    usage_text: str,
    epilog: str | None = None,
) -> str:
    """Render the full --help output: usage line, description (if
    any), per-section listings of positionals and options, optional
    epilog. Only emitted when ``add_help=True``, so the auto
    ``-h, --help`` row is always present. The caller pre-renders
    ``usage_text`` so it isn't walked twice.
    """
    flags = [s for s in specs if s.is_flag]
    positionals = [s for s in specs if not s.is_flag]

    pos_rows = [(_positional_usage_token(s), s.help_text or "")
                for s in positionals]
    flag_rows = [(_flag_help_signature(s), s.help_text or "") for s in flags]
    # Match CPython's ``self._action_max_length + 2``: align all rows
    # (including the auto ``-h, --help`` entry) to the longest
    # signature plus two spaces of separation.
    sigs = [_HELP_OPT_FORM, *(sig for sig, _ in pos_rows),
            *(sig for sig, _ in flag_rows)]
    pad = max(len(s) for s in sigs) + 2

    lines = [usage_text, ""]
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

    if epilog:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(epilog)
    return "\n".join(lines)
