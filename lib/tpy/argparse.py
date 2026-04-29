# tpy: macro_module
"""argparse builder-trace macro.

Mirrors a slice of CPython's argparse.ArgumentParser surface as a
@builder_macro: the user writes ordinary builder-style code, the
compiler synthesizes a per-call-site record + parse function on
parse_args(...). Under CPython the same source resolves to the
stdlib argparse module (this file is invisible outside tpyc's lib
search path), so test programs run under both backends without
code changes.

Subparsers are layered on top via @builder_returns: the macro
classes ``_SubparsersAction`` (returned by ``add_subparsers()``)
and ``_SubparserBuilder`` (returned by ``add_parser(name)``)
collect per-sub arg specs that the top-level terminal walks to
emit per-sub records and parse fns. The top namespace flattens
per-sub fields as ``Optional[T]`` (CPython argparse Namespace
shape); a typed-union escape hatch is **not** stored -- see the
argparse Future Work table in ``docs/MACRO_DESIGN.md`` for the
phasing reason and the planned pre-pass-6 expansion that would
unlock it.

v1 surface, future-work tiers, and known CPython divergences are
tracked in ``docs/MACRO_DESIGN.md`` (the argparse use-case section)
and mirrored in ``docs/STDLIB_ROADMAP.md``. Keeping the canonical
list there avoids drift between the macro module and the design doc.
"""

from tpyc.macro_api import (
    builder_macro, builder_method, builder_returns, builder_terminal,
    BuilderContext, MacroArg, MacroArgs, TypeInfo, MacroError,
    macro_deps, types, ast, Type,
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
        "type_info", "is_arg_type", "action", "default", "has_default",
        "const", "has_const",
        "choices", "required",
        "nargs",
        "help_text",
        "metavar",
    )

    def __init__(
        self, *, is_flag: bool, flag_names: list[str], name: str,
        dest: str, type_info: TypeInfo, is_arg_type: bool,
        action: str, default, has_default: bool,
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
        self.is_arg_type = is_arg_type  # True iff type uses from_arg factory
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


def _is_arg_type(ctx: BuilderContext, ti: TypeInfo) -> bool:
    """Whether ``ti`` is a user type with a ``@staticmethod from_arg(s: str) -> Self``.

    This is the duck-typed escape hatch that lets users plug in custom
    types (Path, datetime, domain records) without a built-in
    enumeration: any record exposing such a factory is accepted, and
    the synthesized parse fn calls ``T.from_arg(token)`` for it.
    Sema checks the param/return-type details when the synthesized
    call is analyzed -- the macro just gates on existence + staticness.
    """
    return ctx.get_static_method_return_type(ti, "from_arg") is not None


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


def _resolve_type_info(
    ctx: BuilderContext, args: MacroArgs,
) -> tuple[TypeInfo, bool]:
    """Read ``type=`` and return ``(TypeInfo, is_arg_type)``.

    Defaults to ``(str, False)``. The second tuple element is True when
    the type is a user record providing the ``ArgType`` factory hook
    (see ``_is_arg_type``); callers consult it to decide between
    ``T(arg)`` and ``T.from_arg(arg)`` at synthesis time.
    """
    ti = ctx.kwarg_type(args, "type")
    if ti is None:
        return _STR_TYPE_INFO, False
    if _is_allowed_arg_type(ti):
        return ti, False
    if _is_arg_type(ctx, ti):
        # Builder-trace passes user-defined types as placeholders with
        # ``_tpy_type=None``; resolve_type_info rebuilds them on the
        # registered record's qname-bearing NominalType so downstream
        # field types and codegen identity-match sema-resolved
        # references in the synthesized parse function body.
        return ctx.resolve_type_info(ti), True
    ctx.error(
        f"argparse: unsupported type={ti.name!r}; "
        f"supported: int, float, str, "
        f"Int8/16/32/64, UInt8/16/32/64, Float32, "
        f"or any record with @staticmethod from_arg(s: str) -> Self"
    )
    return ti, False


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


def _unwrap_optional(t):
    """Strip one ``Optional[...]`` wrapper if present, else return ``t``
    unchanged. Used to compare per-sub field types under the flat
    namespace where ``T`` and ``Optional[T]`` should be treated as
    equivalent (the top record always wraps once anyway).
    """
    ti = TypeInfo.from_tpy_type(t)
    inner = ti.unwrap_optional()
    return inner.raw_type if inner is not None else t


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
# Internal: shared add_argument body
# ---------------------------------------------------------------------------

def _build_arg_spec(
    ctx: BuilderContext, args: MacroArgs, *,
    add_help_reserved: bool,
) -> _ArgSpec:
    """Validate add_argument() kwargs and construct an _ArgSpec.

    Shared between ArgumentParser.add_argument and
    _SubparserBuilder.add_argument. ``add_help_reserved`` controls
    whether ``-h``/``--help`` are rejected as user-registered flag
    names (true for the top-level parser when ``add_help=True``;
    always false for sub-parsers in v1, since sub-help is not yet
    auto-emitted).
    """
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
    type_info, is_arg_type = _resolve_type_info(ctx, args)

    nargs = _resolve_nargs_kwarg(ctx, args)
    # Custom ``type=<MyType>`` (ArgType) v1: ``store`` /
    # ``append`` / ``extend`` plus all four nargs shapes are
    # supported via the same synthesis as the built-in types
    # (``T.from_arg`` is just a different value-coercion than the
    # builtin constructor). ``choices=`` stays out of v1 because
    # comparing the unparsed token against a literal set would
    # diverge from CPython's "compare parsed values" semantics.
    if is_arg_type and choices is not None:
        ctx.error(
            "argparse: type=<custom> does not support choices= in v1"
        )
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
        if is_arg_type and not isinstance(default, str):
            # Mirrors CPython's "string defaults run through type="
            # rule: a single string literal is routed through
            # ``T.from_arg`` at parse-fn entry. List defaults
            # diverge from CPython (CPython keeps the elements as
            # strings while TPy needs them typed as T to fit a
            # ``list[T]`` field), so they're rejected; users can
            # omit ``default=`` to get ``Optional[list[T]]`` or an
            # empty ``list[T]``.
            ctx.error(
                f"argparse: type=<custom> with default= requires "
                f"a string literal (got {type(default).__name__})"
            )
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
        return _ArgSpec(
            is_flag=False, flag_names=[], name=name, dest=dest,
            type_info=type_info, is_arg_type=is_arg_type, action=action,
            default=default, has_default=has_default,
            choices=choices, required=True,
            nargs=nargs, help_text=help_text,
            metavar=metavar,
        )

    # Optional flag(s). Absent flags fall back to their default
    # (or None when no default is given; field type becomes
    # Optional[T] or Optional[list[T]] depending on action).
    # nargs='?' on a flag uses const when the flag is bare and
    # value when the flag carries one.
    name = flags[0]
    dest = _dest_for_flags(flags, explicit_dest)
    if add_help_reserved:
        for f in flags:
            if f in ("-h", "--help"):
                ctx.error(
                    "argparse: -h / --help is reserved by the "
                    "auto-generated help printer; pass "
                    "add_help=False to ArgumentParser() to "
                    "register your own"
                )
    return _ArgSpec(
        is_flag=True, flag_names=list(flags), name=name, dest=dest,
        type_info=type_info, is_arg_type=is_arg_type, action=action,
        default=default, has_default=has_default,
        const=const, has_const=has_const,
        choices=choices, required=required_kw,
        nargs=nargs, help_text=help_text,
        metavar=metavar,
    )


# ---------------------------------------------------------------------------
# Public macro: nested builders for subparsers
# ---------------------------------------------------------------------------

class _SubparserBuilder:
    """One sub-parser registered via ``parser.add_subparsers().add_parser(name)``.

    Holds its own arg specs; the top-level terminal walks every
    sub-parser registered on the enclosing _SubparsersAction to
    synthesize per-sub records and parse functions. v1 does not
    auto-emit ``--help`` on sub-parsers, so ``add_help_reserved`` is
    always false for sub-arg validation -- users may register their
    own ``-h`` / ``--help`` here.
    """

    def __init__(self, name: str, help_text: str | None) -> None:
        self.name = name
        self.help_text = help_text
        self.specs: list[_ArgSpec] = []
        self._dest_seen: set[str] = set()

    @builder_method
    def add_argument(self, ctx: BuilderContext, args: MacroArgs) -> None:
        spec = _build_arg_spec(ctx, args, add_help_reserved=False)
        if spec.dest in self._dest_seen:
            ctx.error(f"argparse: duplicate argument destination {spec.dest!r}")
        self._dest_seen.add(spec.dest)
        self.specs.append(spec)

    @builder_method
    def add_subparsers(self, ctx: BuilderContext, args: MacroArgs) -> None:
        # No nested subparsers in v1: the top-level synth assumes a
        # flat (top, sub*) shape so it can emit a single union field.
        # Lifting this requires recursive sub-record synthesis and a
        # nested union representation -- defer.
        ctx.error(
            "argparse: nested add_subparsers() is not supported in v1"
        )


class _SubparsersAction:
    """Result of ``parser.add_subparsers()``.

    Tracks the subparser dispatch parameters (`dest=`, `required=`,
    optional `help=`/`title=` for help-formatting hooks) and the
    list of registered sub-parsers. Reachable through the trace via
    ``add_parser(name)``, which returns a fresh _SubparserBuilder.
    """

    def __init__(
        self, *, dest: str, required: bool,
        title: str | None, action_help: str | None,
        loc: object,
    ) -> None:
        self.dest = dest
        self.required = required
        self.title = title
        self.action_help = action_help
        self.loc = loc
        self.subparsers: list[_SubparserBuilder] = []
        self._names_seen: set[str] = set()

    @builder_returns(_SubparserBuilder)
    def add_parser(
        self, ctx: BuilderContext, args: MacroArgs,
    ) -> _SubparserBuilder:
        if not args.positional:
            ctx.error("argparse: add_parser() requires a name argument")
        name = ctx.positional_str(args, 0)
        if name in self._names_seen:
            ctx.error(f"argparse: duplicate sub-parser name {name!r}")
        if not name or name.startswith("-"):
            ctx.error(
                f"argparse: sub-parser name must be a non-empty "
                f"non-flag token, got {name!r}"
            )
        self._names_seen.add(name)
        help_text = ctx.kwarg_str(args, "help")
        sub = _SubparserBuilder(name=name, help_text=help_text)
        self.subparsers.append(sub)
        return sub


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
        # Set by add_subparsers(); a single _SubparsersAction holds
        # every registered sub-parser. v1 allows at most one call.
        self._subparsers: _SubparsersAction | None = None

    @builder_method
    def add_argument(self, ctx: BuilderContext, args: MacroArgs) -> None:
        spec = _build_arg_spec(ctx, args, add_help_reserved=self.add_help)
        if spec.dest in self._dest_seen:
            ctx.error(f"argparse: duplicate argument destination {spec.dest!r}")
        self._dest_seen.add(spec.dest)
        self.specs.append(spec)

    @builder_returns(_SubparsersAction)
    def add_subparsers(
        self, ctx: BuilderContext, args: MacroArgs,
    ) -> _SubparsersAction:
        if self._subparsers is not None:
            ctx.error(
                "argparse: add_subparsers() can only be called once "
                "per parser"
            )
        # CPython argparse rejects sub-parsers when any positional
        # argument is already registered (the regex matcher can't
        # disambiguate where the subcommand starts). v1 keeps that
        # restriction; lifting it requires the regex matcher.
        for s in self.specs:
            if not s.is_flag:
                ctx.error(
                    "argparse: add_subparsers() is not supported when "
                    "the parser also has positional arguments"
                )
        dest = ctx.kwarg_str(args, "dest") or "cmd"
        if dest in self._dest_seen:
            ctx.error(
                f"argparse: subparsers dest={dest!r} collides with "
                f"a top-level argument of the same dest"
            )
        required = ctx.kwarg_bool(args, "required", False)
        title = ctx.kwarg_str(args, "title")
        action_help = ctx.kwarg_str(args, "help")
        action = _SubparsersAction(
            dest=dest, required=required,
            title=title, action_help=action_help,
            loc=ctx.call_loc,
        )
        self._subparsers = action
        return action

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

        if self._subparsers is None:
            return self._terminal_single(ctx, args)
        return self._terminal_with_subparsers(ctx, args)

    def _terminal_single(
        self, ctx: BuilderContext, args: MacroArgs,
    ) -> TypeInfo:
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

    def _terminal_with_subparsers(
        self, ctx: BuilderContext, args: MacroArgs,
    ) -> TypeInfo:
        """Synthesize the subparsers-aware parser.

        Layout (Phase 1, no property forwarders):
          per_sub_record_i: <fields for sub_i>             # one record per sub-parser
          per_sub_parse_i: list[str] -> per_sub_record_i   # one parse fn per sub
          top_record: <common arg fields>
                      <dest=:str | Optional[str]>          # CPython-shape subcommand name
                      <_subcommand: Union[Sub] | Optional[Union[Sub]]>
                                                            # TPy-only typed union
          top_parse: list[str] -> top_record

        Top-level parse:
          - --help / -h scan (top help only; sub-help is v2)
          - main loop dispatches common flags
          - non-flag token -> sub-parser dispatch:
              tail = list(argv[i+1:]); _subcommand = sub_parse_<name>(tail); break
          - post-loop required-flag and required-subparser checks.
        """
        sp = self._subparsers
        assert sp is not None
        if not sp.subparsers:
            ctx.error(
                "argparse: add_subparsers() requires at least one "
                "add_parser() call before parse_args()"
            )

        # Per-sub vs common-arg collisions are checked further below
        # when per-sub flat fields are added to the top record.
        # ``add_subparsers`` already rejected ``sp.dest`` colliding
        # with an existing common-arg dest at registration time.
        top_dests = {s.dest for s in self.specs}

        prog = self.prog if self.prog is not None else _DEFAULT_PROG

        # Synthesize per-sub records + per-sub parse fns. Each sub's
        # error path uses ``"<top-prog> <sub-name>"`` as the prog so
        # error messages match CPython's "prog cmd: error: ..." shape.
        sub_record_types: list = []  # parallel to sp.subparsers
        sub_record_names: list[str] = []  # bare names, parallel to sp.subparsers
        sub_parse_fn_names: list[str] = []
        for sub in sp.subparsers:
            positional_in_sub = [x for x in sub.specs if not x.is_flag]
            for i, s in enumerate(positional_in_sub):
                if s.nargs in ("*", "+", "?") and i != len(positional_in_sub) - 1:
                    ctx.error(
                        f"argparse: positional {s.name!r} with "
                        f"nargs={s.nargs!r} must be the last "
                        f"positional in sub-parser {sub.name!r}"
                    )
            sub_record_name = ctx.fresh_module_name(f"argparse_{sub.name}_args")
            sub_fields = [(s.dest, s.field_type) for s in sub.specs]
            sub_record_type = ctx.emit_record(sub_record_name, sub_fields)
            sub_record_types.append(sub_record_type)
            sub_record_names.append(sub_record_name)

            sub_prog = f"{prog} {sub.name}"
            sub_usage_text = _format_usage(
                sub.specs, prog=sub_prog, usage=None,
                include_help_opt=False,
            )
            sub_body = _build_parse_body(
                sub.specs, sub_record_name, None, sub_usage_text,
                prog=sub_prog, add_help=False,
            )
            sub_fn_name = ctx.fresh_module_name(f"argparse_{sub.name}_parse")
            ctx.emit_function(
                sub_fn_name,
                [("argv", types.list(types.str))],
                sub_record_type.raw_type,
                sub_body,
            )
            sub_parse_fn_names.append(sub_fn_name)

        cmd_field_type = types.str if sp.required else types.optional(types.str)

        # Per-sub flat fields on the top record. CPython argparse
        # exposes per-sub args directly as Optional[T] attributes on
        # the Namespace; we mirror that. A typed-union escape hatch
        # ``_subcommand: A | B`` is intentionally NOT stored: users
        # can't reference the synthesized sub-record names (they
        # carry the ``__tpy_builder_`` private prefix), so a stored
        # union would be unreachable. Once builder-trace expansion
        # moves to a dedicated pre-pass-6 pass (see MACRO_DESIGN.md's
        # "Move builder-trace expansion to a dedicated pre-pass-6
        # pass"), property forwarders that match over the union
        # become viable and the union storage can be added back as
        # the underlying state.
        flat_field_map: dict[str, list[tuple[int, _ArgSpec]]] = {}
        for sub_idx, sub in enumerate(sp.subparsers):
            for spec in sub.specs:
                flat_field_map.setdefault(spec.dest, []).append(
                    (sub_idx, spec)
                )
        flat_fields: list[tuple[str, Type, list[tuple[int, _ArgSpec]]]] = []
        # Reserve the cmd dest now -- the top record carries cmd as a
        # stored field too, so a per-sub arg named the same as sp.dest
        # would shadow it.
        reserved_dests = {sp.dest}
        for name in sorted(flat_field_map.keys()):
            occurrences = flat_field_map[name]
            if name in top_dests:
                ctx.error(
                    f"argparse: per-sub argument {name!r} collides with "
                    f"a top-level argument of the same dest; rename one "
                    f"of them so the top record can carry distinct fields"
                )
            if name in reserved_dests:
                ctx.error(
                    f"argparse: per-sub argument {name!r} is reserved "
                    f"by the subparsers machinery (matches the "
                    f"subcommand-name field on the top record)"
                )
            # Compare *unwrapped* field types: a sub with ``required=True``
            # carries ``T`` and another with the same flag but no required=
            # carries ``Optional[T]`` -- both unify to a single ``Optional[T]``
            # field on the top record (matching CPython, which doesn't care
            # which sub set the attribute). Reject only when the underlying
            # base types disagree.
            base_types = {_unwrap_optional(spec.field_type) for _, spec in occurrences}
            if len(base_types) > 1:
                ctx.error(
                    f"argparse: per-sub argument {name!r} has conflicting "
                    f"field types across sub-parsers; under Option B "
                    f"(flat namespace) the top record needs a single "
                    f"field type per name. Rename one of them, or unify "
                    f"the types (same ``type=`` and same default-shape)"
                )
            top_type = types.optional(next(iter(base_types)))
            flat_fields.append((name, top_type, occurrences))

        top_record_name = ctx.fresh_module_name("argparse_args")
        top_fields: list = [(s.dest, s.field_type) for s in self.specs]
        top_fields.append((sp.dest, cmd_field_type))
        for name, top_type, _ in flat_fields:
            top_fields.append((name, top_type))
        top_record_type = ctx.emit_record(top_record_name, top_fields)

        # Top-level usage / help. v1 lists subcommands as a single
        # positional placeholder ``{a,b,c}`` to match CPython's output
        # shape; per-sub help is not auto-emitted in v1.
        usage_text = _format_usage(
            self.specs, prog=prog, usage=self.usage,
            include_help_opt=self.add_help,
            subparser_action=sp,
        )
        help_fn_name: str | None = None
        if self.add_help:
            help_fn_name = ctx.fresh_module_name("argparse_help")
            help_text = _format_help_text(
                self.specs, self.description,
                usage_text=usage_text, epilog=self.epilog,
                subparser_action=sp,
            )
            ctx.emit_function(
                help_fn_name, [], types.void,
                ast.quote(f"print({help_text!r})\nsys.exit(Int32(0))"),
            )

        body = _build_subparser_parse_body(
            self.specs, sp, sub_parse_fn_names, flat_fields,
            top_record_name, help_fn_name, usage_text,
            prog=prog, add_help=self.add_help,
        )

        fn_name = ctx.fresh_module_name("argparse_parse")
        ctx.emit_function(
            fn_name,
            [("argv", types.list(types.str))],
            top_record_type.raw_type,
            body,
        )
        ctx.replace_call(fn_name, args)
        return top_record_type


# ---------------------------------------------------------------------------
# Internal: parse-function body synthesis
# ---------------------------------------------------------------------------

def _emit_parse_prelude(
    specs: list[_ArgSpec], usage_text: str, *,
    add_help: bool, help_fn_name: str | None,
) -> tuple[list, list[str]]:
    """Returns ``(init_stmts, src_lines)`` for the boilerplate every
    parse-fn body opens with: usage local, optional ``-h``/``--help``
    pre-scan, per-spec init, required-flag-seen tracking. Shared by
    the single-parser and subparser-aware parse-body builders.

    Both lists are returned mutable; callers append further AST
    decls / source lines onto them before assembling the final body
    (e.g. the subparser path adds ``acc_<dest>`` and per-sub flat
    locals to ``init_stmts`` and emits its dispatch loop into
    ``src_lines``).

    The help fn prints help and calls ``sys.exit(0)``, so the
    pre-scan loop never returns from the call -- but its return type
    is ``None``, so sema sees normal flow and the post-call increment
    compiles fine.
    """
    init_stmts: list = []
    src_lines: list[str] = []
    src_lines.append(f"__tpy_argparse_usage = {usage_text!r}")
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
    for s in specs:
        init_stmts.extend(_spec_init_stmts(s))
    for s in specs:
        if s.is_flag and s.required:
            src_lines.append(f"__tpy_argparse_seen_{s.dest} = False")
    return init_stmts, src_lines


def _emit_parse_reconciliation(
    specs: list[_ArgSpec], *, error_prefix: str,
) -> list[str]:
    """Returns ``src_lines`` for the post-loop reconciliation shared
    by both parse-body builders: required-flag missing checks, then
    Optional[list[T]] accumulator -> dest copy, then ArgType
    accumulator unwrap (assert + copy).
    """
    src_lines: list[str] = []
    for s in specs:
        if s.is_flag and s.required:
            src_lines.append(f"if not __tpy_argparse_seen_{s.dest}:")
            src_lines.extend(_error_emit_lines(
                "    ", repr(f"missing required argument: {s.flag_names[0]}"),
                error_prefix=error_prefix,
            ))
    # Optional[list[T]] reconciliation: ``tpy.copy`` is here for the
    # implicit-copy warning, not correctness -- assigning a
    # ``list[T]`` local into an ``Optional[list[T]]`` slot warns even
    # when the local is at its last use. Codegen lowers ``copy`` to a
    # ``vector(acc)`` copy ctor, so this is one redundant allocation
    # per seen flag. Replace with a true move once TPy core gains
    # move-into-Optional.
    for s in specs:
        if s.is_optional_list_field:
            src_lines.append(f"if {_seen_local(s)}:")
            src_lines.append(f"    {s.dest} = tpy.copy({_acc_local(s)})")
    # ArgType accumulator unwrap: missing-required checks above
    # already exited via sys.exit(2) when the accumulator stayed
    # None, so the assert is for sema's flow-narrowing
    # (Optional[T] -> T) rather than runtime safety. ``tpy.copy``
    # makes the implicit copy explicit so the record ctor (which
    # moves into owned storage) doesn't warn.
    for s in specs:
        if _needs_arg_type_accumulator(s):
            src_lines.append(f"assert {_acc_local(s)} is not None")
            src_lines.append(f"{s.dest} = tpy.copy({_acc_local(s)})")
    return src_lines


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
    error_prefix = f"{prog}: error: "

    # Empty parser: no add_argument() was called. Reject any argv
    # tokens (matching CPython argparse) and return an empty record
    # without entering the main loop -- the loop body would otherwise
    # never advance __tpy_argparse_i and hang on non-empty argv.
    if not specs:
        init_stmts, src_lines = _emit_parse_prelude(
            specs, usage_text, add_help=add_help, help_fn_name=help_fn_name,
        )
        src_lines.append("if len(argv) != 0:")
        src_lines.extend(_error_emit_lines(
            "    ", '"unrecognized arguments: " + argv[0]',
            error_prefix=error_prefix,
        ))
        src_lines.append(f"return {record_name}()")
        return init_stmts + ast.quote("\n".join(src_lines))

    init_stmts, src_lines = _emit_parse_prelude(
        specs, usage_text, add_help=add_help, help_fn_name=help_fn_name,
    )

    # --- Main loop ---
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

    # Missing-positional check: every required positional slot must
    # have been filled. Variable-nargs trailing positionals (* / ?)
    # are themselves optional; +/N/none are required.
    # nargs='+' on the trailing positional must have got at least
    # one value; the dispatch sets pi only when consumed, so the
    # count check below already covers this.
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

    src_lines.extend(_emit_parse_reconciliation(specs, error_prefix=error_prefix))

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
    if _needs_arg_type_accumulator(spec):
        # No zero-arg sentinel for the user type. Init only the
        # ``Optional[T]`` accumulator; ``spec.dest`` is rebound to the
        # unwrapped ``T`` after the post-loop missing-required check.
        scalar = spec.type_info.raw_type
        return [ast.var_decl(
            _acc_local(spec), types.optional(scalar), ast.none_lit()
        )]
    return ast.quote(f"{spec.dest} = {_default_expr_src(spec)}")


def _render_default_elem(value, ti: TypeInfo):
    """AST expression for one element of a list-typed default.

    Mirrors ``_default_expr_src``'s wrapping at the AST level:
    ``Int32(1)`` / ``Float32(0.5)`` for fixed-width primitives so the
    typed list annotation stays consistent regardless of options.json
    defaults and Float32 elements don't widen to Float64.
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


def _needs_arg_type_accumulator(spec: _ArgSpec) -> bool:
    """True for ArgType specs whose record field is non-Optional T but
    whose parse local has to start as ``Optional[T]`` (no zero-arg
    sentinel for the user type). Covers required positional + required
    flag without a string default. Optional flags without default
    already get an Optional[T] field via ``is_optional_field``, so
    they don't need the accumulator+narrow dance. List-typed fields
    (``nargs=*/+/<int>`` or append/extend) own a ``list[T]`` directly
    and never reach the scalar-unwrap path either.
    """
    if not spec.is_arg_type:
        return False
    if spec.has_default:
        return False
    if spec.is_optional_field:
        return False
    if spec.is_list_field:
        return False
    return True


def _store_target(spec: _ArgSpec) -> str:
    """Local the ``store`` handler writes into. For ArgType specs that
    need the accumulator-then-narrow pattern, that's ``_acc_<dest>``
    (Optional[T]); a post-missing-check rebind to ``<dest>`` (T) lands
    the unwrapped value in the name the record constructor consumes.
    """
    return _acc_local(spec) if _needs_arg_type_accumulator(spec) else spec.dest


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
                L.append(f"{indent}{_store_target(spec)} = {tmp}")
            else:
                L.append(f"{indent}{target}.append({tmp})")
        else:
            if a == "store":
                L.append(f"{indent}{_store_target(spec)} = {value_expr}")
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
            L.append(f"{indent}{_store_target(spec)} = {tmp}")
        else:
            L.append(f"{indent}{_store_target(spec)} = {value_expr}")
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
        if spec.is_arg_type:
            # Default is a string literal (validated at add_argument);
            # route through ``T.from_arg`` so the field carries T at
            # parse-fn entry. Mirrors CPython's "string defaults run
            # through type=" behaviour.
            return f"{ti.name}.from_arg({rendered})"
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
    Custom ``ArgType`` types route through ``T.from_arg(token)``.
    """
    if spec.type_info.is_str:
        return source_expr
    if spec.is_arg_type:
        return f"{spec.type_info.name}.from_arg({source_expr})"
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
    subparser_action: '_SubparsersAction | None' = None,
) -> str:
    """Render just the usage line. Reused by parse-error path.

    When ``usage`` is provided, it overrides the auto-generated tail
    after ``"usage: "`` (matching CPython's ``ArgumentParser(usage=)``
    behavior). When ``include_help_opt`` is False, the ``[-h]`` token
    is omitted from the auto-generated form. When ``subparser_action``
    is provided, the ``{a,b} ...`` subcommand placeholder is appended
    after any common positionals (matches CPython's rendering of the
    subparsers action).

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
    if subparser_action is not None:
        pos_parts.append(_subcommand_metavar(subparser_action))
        pos_parts.append("...")

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


def _subcommand_metavar(sp: _SubparsersAction) -> str:
    """``{a,b,c}`` placeholder shown in usage / help. Matches CPython
    argparse's rendering of the subparsers positional.
    """
    return "{" + ",".join(s.name for s in sp.subparsers) + "}"


def _subparser_cmd_acc(sp: _SubparsersAction) -> str:
    return f"__tpy_argparse_acc_{sp.dest}"


def _subparser_flat_local(field_name: str) -> str:
    return f"__tpy_argparse_flat_{field_name}"


def _subparser_sub_local(sub_name: str) -> str:
    return f"__tpy_argparse_sub_{sub_name}"


def _format_help_text(
    specs: list[_ArgSpec], description: str | None, *,
    usage_text: str,
    epilog: str | None = None,
    subparser_action: '_SubparsersAction | None' = None,
) -> str:
    """Render the full --help output: usage line, description (if
    any), per-section listings of positionals and options, optional
    epilog. Only emitted when ``add_help=True``, so the auto
    ``-h, --help`` row is always present. The caller pre-renders
    ``usage_text`` so it isn't walked twice.

    When ``subparser_action`` is provided, the positional section
    shows the ``{a,b}`` subcommand metavar plus one indented row per
    registered sub-parser instead of plain positional rows. v1
    rejects mixing positionals with subparsers, so the two cases
    don't overlap.
    """
    flags = [s for s in specs if s.is_flag]
    positionals = [s for s in specs if not s.is_flag]

    pos_rows = [(_positional_usage_token(s), s.help_text or "")
                for s in positionals]
    flag_rows = [(_flag_help_signature(s), s.help_text or "") for s in flags]
    sub_rows: list[tuple[str, str]] = []
    sub_token = ""
    if subparser_action is not None:
        sub_token = _subcommand_metavar(subparser_action)
        sub_rows = [(s.name, s.help_text or "")
                    for s in subparser_action.subparsers]
    # Match CPython's ``self._action_max_length + 2``: align all rows
    # (including the auto ``-h, --help`` entry) to the longest
    # signature plus two spaces of separation.
    sigs = [_HELP_OPT_FORM, *(sig for sig, _ in pos_rows),
            *(sig for sig, _ in flag_rows),
            *([sub_token] if sub_token else []),
            *(sig for sig, _ in sub_rows)]
    pad = max(len(s) for s in sigs) + 2

    lines = [usage_text, ""]
    if description:
        lines.append(description)
        lines.append("")

    if subparser_action is not None:
        lines.append("positional arguments:")
        sub_action_help = subparser_action.action_help or ""
        lines.append(f"  {sub_token.ljust(pad)}{sub_action_help}".rstrip())
        for name, text in sub_rows:
            lines.append(f"    {name.ljust(pad - 2)}{text}".rstrip())
        lines.append("")
    elif positionals:
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


def _build_subparser_parse_body(
    specs: list[_ArgSpec], sp: _SubparsersAction,
    sub_parse_fn_names: list[str],
    flat_fields: list[tuple[str, Type, list[tuple[int, _ArgSpec]]]],
    record_name: str, help_fn_name: str | None,
    usage_text: str, *, prog: str, add_help: bool,
) -> list:
    """Generate the body of the synthesized top-level parse function
    when subparsers are present (Option B / flat namespace).

    Each per-sub field appears as an ``Optional[T]`` stored field on
    the top record. The parse fn dispatches the subcommand name,
    invokes the matching sub-parser, then copies the chosen sub
    record's fields into the corresponding flat locals (per-sub
    fields not on the chosen sub stay None). Layout:

      __tpy_argparse_usage = <usage>
      <-h/--help scan>
      <init common arg defaults>
      <init required-flag-seen tracking>
      __tpy_argparse_acc_<dest>: Optional[str] = None
      <flat_field_i>: Optional[T_i] = None        # one per per-sub field name
      while __tpy_argparse_i < len(argv):
          __tpy_argparse_tok = argv[__tpy_argparse_i]
          if __tpy_argparse_tok == "--top-flag-1": ...
          elif __tpy_argparse_tok == "<sub-name-1>":
              __tpy_argparse_acc_<dest> = "<sub-name-1>"
              __tpy_argparse_i = __tpy_argparse_i + 1
              <sub_var> = <sub_parse_1>(list(argv[i:]))
              <copy sub_var.field into flat_field for each field on this sub>
              break
          ...
          else: <unknown / invalid-choice error>
      <required flag missing checks>
      <Optional[list[T]] / ArgType reconciliation for common args>
      if sp.required and __tpy_argparse_acc_<dest> is None: <error>
      <build top record>
    """
    error_prefix = f"{prog}: error: "
    init_stmts, src_lines = _emit_parse_prelude(
        specs, usage_text, add_help=add_help, help_fn_name=help_fn_name,
    )

    # Subcommand-name accumulator. cmd is always typed Optional[str]
    # in the body even when sp.required forces the field to str: the
    # post-loop required-check converts None -> error and the assert
    # narrows back to str for the field write.
    init_stmts.append(ast.var_decl(
        _subparser_cmd_acc(sp),
        types.optional(types.str),
        ast.none_lit(),
    ))

    # Per-sub flat field locals (Optional[T] = None). Populated inside
    # the matching sub-parser dispatch branch from the chosen sub
    # record's fields.
    flat_local_for: dict[str, str] = {
        name: _subparser_flat_local(name) for name, _, _ in flat_fields
    }
    for name, top_type, _ in flat_fields:
        init_stmts.append(ast.var_decl(
            flat_local_for[name], top_type, ast.none_lit(),
        ))

    # Main loop.
    src_lines.append("__tpy_argparse_i = 0")
    src_lines.append("while __tpy_argparse_i < len(argv):")
    src_lines.append("    __tpy_argparse_tok = argv[__tpy_argparse_i]")

    flag_specs = [s for s in specs if s.is_flag]
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

    # Subcommand dispatch: each registered sub-parser becomes a
    # branch. After calling the sub parse fn, copy each declared
    # field on the chosen sub into its flat local on the top record.
    cmd_acc = _subparser_cmd_acc(sp)
    for sub_idx, sub in enumerate(sp.subparsers):
        kw = "if" if first else "elif"
        first = False
        src_lines.append(
            f'    {kw} __tpy_argparse_tok == {sub.name!r}:'
        )
        src_lines.append(f"        {cmd_acc} = {sub.name!r}")
        src_lines.append("        __tpy_argparse_i = __tpy_argparse_i + 1")
        sub_var = _subparser_sub_local(sub.name)
        src_lines.append(
            f"        {sub_var} = "
            f"{sub_parse_fn_names[sub_idx]}"
            f"(list(argv[__tpy_argparse_i:]))"
        )
        sub_field_names = {s.dest for s in sub.specs}
        for name, _, _ in flat_fields:
            if name in sub_field_names:
                src_lines.append(
                    f"        {flat_local_for[name]} = {sub_var}.{name}"
                )
        src_lines.append("        break")

    # Else branch: unknown token. Distinguishes "unknown flag" (token
    # starts with '-') from "invalid choice" (positional that doesn't
    # match any sub-parser name) so error messages match CPython.
    src_lines.append("    else:")
    src_lines.append(
        "        if __tpy_argparse_tok.startswith(\"-\"):"
    )
    src_lines.extend(_error_emit_lines(
        "            ",
        '"unknown argument: " + __tpy_argparse_tok',
        error_prefix=error_prefix,
    ))
    src_lines.append("        else:")
    src_lines.extend(_error_emit_lines(
        "            ",
        '"invalid choice: " + __tpy_argparse_tok',
        error_prefix=error_prefix,
    ))

    src_lines.extend(_emit_parse_reconciliation(specs, error_prefix=error_prefix))

    # Subcommand resolution. ``__tpy_argparse_cmd`` is bound here as
    # the ctor input for the top record's cmd field; sp.required
    # widens / narrows the accumulator type.
    if sp.required:
        src_lines.append(f"if {cmd_acc} is None:")
        src_lines.extend(_error_emit_lines(
            "    ",
            repr(f"the following argument is required: {_subcommand_metavar(sp)}"),
            error_prefix=error_prefix,
        ))
        src_lines.append(f"assert {cmd_acc} is not None")
    src_lines.append(f"__tpy_argparse_cmd = {cmd_acc}")

    ctor_args = ", ".join(
        [s.dest for s in specs]
        + ["__tpy_argparse_cmd"]
        + [flat_local_for[name] for name, _, _ in flat_fields]
    )
    src_lines.append(f"return {record_name}({ctor_args})")

    return init_stmts + ast.quote("\n".join(src_lines))
