"""Pascal AST -> Frontend IR translator.

Walks the Pascal AST and emits a `FrontendModule`. Responsibilities:

- Hoist module-level `var` section into per-name IR `VarDecl`s.
- Map Pascal type spellings (`integer`, `boolean`) onto TPy builtins
  (`Int32`, `bool`) and emit corresponding `from tpy import T` imports.
- Translate each procedure / function into an IR `Function`. For
  `function name ...: T;` Pascal's "return by assigning the function
  name" convention is rewritten via a synthetic `__result` local + a
  trailing `Return(__result)`.
- Translate Pascal `var` (by-reference) parameters via
  `PointerType(NamedType(T))`. Inside the routine body, reads of a
  var-param `p` become `deref(p)` and writes `p := v` become
  `unsafe_store(p, 0, v)`. At call sites, var-param arguments are
  wrapped in `take_ptr(...)`.
- Route `writeln(...)` to the per-type runtime overload
  (`writeln_int` for integers, `writeln` for strings).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from tpyc.diagnostics import Diagnostic, DiagnosticLevel
from tpyc.frontend_ir import (
    Assign,
    Attr,
    BinOp,
    BinOpKind,
    BoolLit,
    Call,
    CmpOpKind,
    Compare,
    Enum,
    EnumValue,
    ExprStmt,
    Field,
    FloatLit,
    ForRange,
    FrontendDirectives,
    FrontendModule,
    FromImport,
    Function,
    If,
    ImportName,
    IntLit,
    IntTypeArg,
    Loc as IRLoc,
    Match,
    MatchCase,
    MatchValue,
    MatchWildcard,
    Name,
    NamedType,
    Param,
    PointerType,
    RangeDir,
    Record,
    RepeatUntil,
    Return,
    StarImport,
    StrLit,
    Subscript,
    TypeTypeArg,
    UnaryOp,
    UnaryOpKind,
    VarDecl,
    While,
)

from . import ast as pa


# Pascal type spellings -> TPy builtin names. Single source of truth so
# both var decls and writeln dispatch agree on the mapping. Booleans
# don't need a `from tpy import bool` entry because `bool` is in TPy's
# builtins namespace; the translator records the mapping so the static
# type env still tracks bool variables for writeln dispatch.
_TYPE_MAP: dict[str, str] = {
    "integer": "Int32",
    "boolean": "bool",
    "char": "Char",
    # TP `real` is a 6-byte float at the hardware level; we map both
    # `real` and `double` to TPy's `float` (IEEE 754 64-bit) for the
    # POC. Divergence documented in
    # `examples/frontends/pascal/DESIGN.md`.
    "real": "float",
    "double": "float",
}

# Types that should NOT trigger an automatic `from tpy import X` -- the
# entries that live in TPy's builtins module are visible without import.
_BUILTIN_TYPES_NO_IMPORT: frozenset[str] = frozenset({"bool", "float"})

# Pascal infix operators -> IR BinOpKind. Pascal `/` is the integer-or-
# real "true" division (real result for integers); `div` is integer
# division. M2 emits Pascal's `div` as IR `FLOOR_DIV` (TPy's `//`)
# because Pascal integer operands stay integers; `TRUE_DIV` is reserved
# for later when float operands enter the picture.
_BIN_OP: dict[str, BinOpKind] = {
    "+": BinOpKind.ADD,
    "-": BinOpKind.SUB,
    "*": BinOpKind.MUL,
    "div": BinOpKind.FLOOR_DIV,
    "/": BinOpKind.TRUE_DIV,
    "mod": BinOpKind.MOD,
    # Pascal word-spelled logical operators. M3 treats `and` / `or` as
    # logical because operands are boolean expressions from comparisons.
    "and": BinOpKind.LOGICAL_AND,
    "or": BinOpKind.LOGICAL_OR,
    "xor": BinOpKind.BIT_XOR,
}

_UNARY_OP: dict[str, UnaryOpKind] = {
    "+": UnaryOpKind.POS,
    "-": UnaryOpKind.NEG,
    "not": UnaryOpKind.NOT,
}

# Pascal comparison spellings -> IR CmpOpKind. `=` -> EQ, `<>` -> NE
# follow Pascal's syntax; the rest match Python's spelling.
_CMP_OP: dict[str, CmpOpKind] = {
    "=": CmpOpKind.EQ,
    "<>": CmpOpKind.NE,
    "<": CmpOpKind.LT,
    "<=": CmpOpKind.LE,
    ">": CmpOpKind.GT,
    ">=": CmpOpKind.GE,
}


# Name of the synthetic local that carries a function's return value.
# Pascal's `function_name := expr` writes get rewritten to assign to
# this name instead; the function body ends with `Return(_RESULT_NAME)`.
# Underscored to keep it well outside any plausible Pascal identifier.
_RESULT_NAME = "__pascal_result"


@dataclass
class _Sig:
    """Subroutine signature info needed at call sites."""
    name: str
    param_names: list[str]
    param_var_flags: list[bool]   # True iff the corresponding param is `var`
    return_type: str | None       # Pascal spelling, or None for a procedure


@dataclass
class _Ctx:
    """Shared translator state."""
    diagnostics: list[Diagnostic] = field(default_factory=list)
    needed_imports: dict[str, set[str]] = field(default_factory=dict)
    # Module names from Pascal `uses` clauses. Lowered to IR
    # StarImports; the compiler's `_expand_star_imports_for_module`
    # fans each module's public surface into the user's unqualified
    # scope at compile time.
    star_imports: set[str] = field(default_factory=set)
    # WorkspaceContext from the plugin's `parse()` call. Used by
    # `uses` resolution to locate `.pas` / `.py` units in the user's
    # project layout (search dirs minus the TPy stdlib root).
    workspace: object | None = None
    type_env: dict[str, str] = field(default_factory=dict)
    signatures: dict[str, _Sig] = field(default_factory=dict)
    # User record type names (declared via Pascal `type X = record ...`).
    # The translator uses this set to distinguish user record types
    # from built-in scalar type spellings.
    record_types: set[str] = field(default_factory=set)
    # Field type map: record_name -> {field_name: pascal_type_spelling}.
    # Powers writeln-overload dispatch for `p.x` and similar field
    # access in expression position.
    record_fields: dict[str, dict[str, str]] = field(default_factory=dict)
    # Lower bound for each declared array variable, keyed by variable
    # name. Indexing into the variable subtracts this from the index
    # expression so the lowered IR uses 0-based indexing.
    array_lower_bounds: dict[str, int] = field(default_factory=dict)
    # FixStr capacity per declared `string`/`string[N]` variable.
    # Used by assignment lowering (`s := 'lit'` -> `s.assign('lit')`)
    # and by string-literal-vs-FixStr disambiguation.
    string_vars: dict[str, int] = field(default_factory=dict)
    # Enum member -> enclosing enum type name. Pascal puts each enum
    # member in unqualified scope (`c := Red;`) -- the translator
    # rewrites a bare `Red` to `Attr(Color, "Red")` so the lowered
    # IR / TPy AST sees a canonical qualified access.
    enum_member_to_type: dict[str, str] = field(default_factory=dict)
    # Enum name -> list of member names (kept for diagnostics / future
    # checks like exhaustive case-arm analysis).
    enum_types: dict[str, list[str]] = field(default_factory=dict)
    # User-defined type aliases that don't materialize as IR Record /
    # Enum nodes. Today this only carries subrange types (`type Byte
    # = 0..255` -> integer); later milestones can add string aliases,
    # array aliases, pointer aliases, etc.
    type_aliases: dict[str, object] = field(default_factory=dict)
    # Per-routine state (saved / restored when entering / leaving each
    # subroutine; left at the defaults at module-body level).
    current_func: str | None = None
    current_func_return_type: str | None = None
    current_var_params: set[str] = field(default_factory=set)

    def add_import(self, module: str, name: str) -> None:
        self.needed_imports.setdefault(module, set()).add(name)


def translate(program: pa.Program,
              module_name: str,
              workspace=None,
              ) -> tuple[FrontendModule, list[Diagnostic]]:
    ctx = _Ctx(workspace=workspace)

    # Pre-scan subroutines so callers can see their signatures even
    # when emitted before the callee in source order.
    for sub in program.subroutines:
        ctx.signatures[sub.name] = _Sig(
            name=sub.name,
            param_names=[p.name for p in sub.params],
            param_var_flags=[p.is_var for p in sub.params],
            return_type=sub.return_type,
        )

    # Pre-scan type blocks so all user record / enum names are known
    # before any var/param/return type is lowered against them.
    for tb in program.type_blocks:
        for td in tb.decls:
            if isinstance(td.type_spec, pa.RecordTypeSpec):
                ctx.record_types.add(td.name)
                field_map: dict[str, str] = {}
                for fg in td.type_spec.fields:
                    field_type = (fg.type_spec.name
                                  if isinstance(fg.type_spec,
                                                pa.NamedTypeSpec)
                                  else None)
                    if field_type is not None:
                        for fname in fg.names:
                            field_map[fname] = field_type
                ctx.record_fields[td.name] = field_map
            elif isinstance(td.type_spec, pa.EnumTypeSpec):
                ctx.enum_types[td.name] = list(td.type_spec.members)
                for member in td.type_spec.members:
                    if member in ctx.enum_member_to_type:
                        ctx.diagnostics.append(_diag(
                            f"enum member {member!r} declared in both "
                            f"{ctx.enum_member_to_type[member]!r} and "
                            f"{td.name!r}",
                            td.loc,
                        ))
                        continue
                    ctx.enum_member_to_type[member] = td.name
            elif isinstance(td.type_spec, pa.SubrangeTypeSpec):
                # Subrange types lower to plain integer. The alias
                # name is recorded so var decls / parameter types can
                # reference it.
                ctx.type_aliases[td.name] = td.type_spec

    # `uses` clauses: resolve each to a canonical TPy module name and
    # mark it as a star import. Bare names (`uses Crt`) resolve only
    # against the user's project search path; `py.X` (`uses py.math`)
    # is the explicit escape hatch into TPy stdlib / arbitrary Python
    # modules. For Pascal-source units we also peek at the
    # interface-section signatures and seed `ctx.signatures` so the
    # translator's writeln-dispatch / return-type-aware paths can see
    # cross-unit functions without waiting for sema.
    for uses_name in program.uses_clauses:
        resolved, resolved_path = _resolve_uses_name_and_path(
            uses_name, program.loc, ctx)
        if resolved is not None:
            ctx.star_imports.add(resolved)
            if resolved_path is not None and resolved_path.suffix == ".pas":
                _ingest_unit_signatures(resolved_path, ctx)

    records: list = []
    enums: list = []
    top_level_stmts: list = []
    functions: list = []

    # Type blocks -> IR Records / Enums (in declaration order, so a
    # later type can reference earlier ones). Subrange aliases were
    # registered into `ctx.type_aliases` during the pre-scan and
    # don't materialise as IR nodes.
    for tb in program.type_blocks:
        for td in tb.decls:
            if isinstance(td.type_spec, pa.RecordTypeSpec):
                rec = _lower_type_decl(td, ctx)
                if rec is not None:
                    records.append(rec)
            elif isinstance(td.type_spec, pa.EnumTypeSpec):
                enum_node = _lower_enum_decl(td, ctx)
                if enum_node is not None:
                    enums.append(enum_node)
            elif isinstance(td.type_spec, pa.SubrangeTypeSpec):
                pass  # registered in pre-scan as a type alias
            else:
                ctx.diagnostics.append(_diag(
                    f"unsupported type declaration "
                    f"{type(td.type_spec).__name__}",
                    td.loc,
                ))

    # Module-level const sections. Each Pascal const declaration
    # becomes an IR `VarDecl(init=...)` with a type inferred from the
    # initializer literal. Immutability is enforced at the source
    # level by the lack of any reassignment path that targets the
    # name (Pascal forbids `const X := ...`); a downstream sema
    # check is future work.
    for cb in program.const_blocks:
        for cd in cb.decls:
            lowered = _lower_const_decl(cd, ctx)
            if lowered is not None:
                top_level_stmts.append(lowered)

    # Module-level var section -> per-name IR VarDecls. Scalar globals
    # stay uninitialised in the IR (codegen emits `{}` zero-init).
    # Records need an explicit default-constructor init -- TPy rejects
    # uninitialised record globals.
    if program.var_block is not None:
        for decl in program.var_block.decls:
            for name in decl.names:
                ir_type = _lower_type_spec(decl.type_spec, ctx)
                if ir_type is None:
                    continue
                _record_var_metadata(name, decl.type_spec, ctx)
                top_level_stmts.append(VarDecl(
                    name=name, type=ir_type,
                    init=_default_init_module(decl.type_spec, decl.loc, ctx),
                    mutable=True, loc=_to_ir_loc(decl.loc),
                ))

    # Module-level body. For a `program X;` this is the main begin /
    # end block. For a `unit X;` it's the optional `initialization
    # ... end` section -- absent units have `block is None` and emit
    # nothing here.
    if program.block is not None:
        for stmt in program.block.statements:
            lowered = _lower_stmt(stmt, ctx)
            if lowered is not None:
                top_level_stmts.append(lowered)

    # Subroutines.
    for sub in program.subroutines:
        fn = _lower_subroutine(sub, ctx)
        if fn is not None:
            functions.append(fn)

    # Emit imports in a deterministic order: star imports first (so
    # any name-collision diagnostics blame the explicit uses-clause
    # site), then the per-name imports for runtime helpers.
    star_decls = tuple(
        StarImport(module=mod)
        for mod in sorted(ctx.star_imports)
    )
    from_decls = tuple(
        FromImport(
            module=mod,
            names=tuple(sorted(
                (ImportName(original=n, local=n) for n in names),
                key=lambda im: im.local,
            )),
        )
        for mod, names in sorted(ctx.needed_imports.items())
        if mod not in ctx.star_imports
    )
    imports = star_decls + from_decls

    fm = FrontendModule(
        qname=module_name,
        source_language="pascal",
        source_lines=program.source_lines,
        imports=imports,
        records=tuple(records),
        enums=tuple(enums),
        functions=tuple(functions),
        top_level_stmts=tuple(top_level_stmts),
        directives=FrontendDirectives(),
    )
    return fm, ctx.diagnostics


# ---------------------------------------------------------------------------
# Type lowering

def _resolve_uses_name_and_path(uses_name: str, loc: pa.Loc,
                                ctx: _Ctx) -> tuple:
    """Resolve a Pascal `uses` target. Returns `(module_name, path)`.

    - `py.X`           -> `(X, None)` -- defer to TPy resolver; no
                          file-system lookup at translate time.
    - bare `Foo`       -> search user dirs for `Foo.pas` / `foo.py`;
                          returns the file's stem and absolute path.
                          Returns `(None, None)` and emits a
                          diagnostic when not found.
    """
    if uses_name.startswith("py."):
        return uses_name[3:], None
    ws = ctx.workspace
    if ws is None:
        return uses_name, None
    stdlib_dirs = {d.resolve() for d in ws.stdlib_search_dirs}
    user_dirs = [
        d for d in ws.search_dirs if d.resolve() not in stdlib_dirs
    ]
    target = uses_name.lower()
    import os
    for d in user_dirs:
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for fname in entries:
            stem, _, ext = fname.rpartition(".")
            if ext not in ("pas", "py"):
                continue
            if stem.lower() == target:
                return stem, d / fname
    ctx.diagnostics.append(_diag(
        f"unit {uses_name!r} not found in project directories. "
        f"Use `py.{uses_name.lower()}` to import a TPy / Python "
        f"module by that name explicitly.",
        loc,
    ))
    return None, None


def _ingest_unit_signatures(path, ctx: _Ctx) -> None:
    """Parse a Pascal `.pas` unit and add its interface-section
    subroutine signatures into `ctx.signatures`. The translator
    consults this map for writeln-dispatch and return-type
    decisions; without it, every cross-unit call looks unknown.

    Parse errors are swallowed -- they'll resurface when the
    compiler discovers and re-parses the unit through the normal
    path, at which point the user sees the real diagnostic.
    """
    from . import parser as pa_parser
    try:
        source = path.read_text()
        unit_program = pa_parser.parse(source, path)
    except Exception:
        return
    if unit_program.kind != "unit":
        return
    for sub in unit_program.subroutines:
        if sub.name in ctx.signatures:
            continue
        ctx.signatures[sub.name] = _Sig(
            name=sub.name,
            param_names=[p.name for p in sub.params],
            param_var_flags=[p.is_var for p in sub.params],
            return_type=sub.return_type,
        )


def _lower_enum_decl(td: pa.TypeDecl, ctx: _Ctx) -> Enum | None:
    """Lower a Pascal `type Color = (Red, Green, ...)` declaration to
    an IR `Enum`. Member values stay implicit; the IR lowering pass
    auto-numbers them starting at 0."""
    assert isinstance(td.type_spec, pa.EnumTypeSpec)
    values: list = []
    for member in td.type_spec.members:
        values.append(EnumValue(
            name=member, value=None,
            loc=_to_ir_loc(td.type_spec.loc),
        ))
    return Enum(
        name=td.name, values=tuple(values),
        loc=_to_ir_loc(td.loc),
    )


def _lower_type_decl(td: pa.TypeDecl, ctx: _Ctx) -> Record | None:
    """Lower a single `type X = TypeSpec` declaration.

    M5 supports `record` here. Each scalar field gets a Pascal-style
    zero default (`0` for integer, `False` for boolean) so TPy's
    auto-generated default constructor lets `var p: Point;` and
    `p := Point()` both work without the user spelling out an init
    method. Pascal's de facto behaviour is that record fields start
    zero-initialised; emitting explicit defaults preserves that.
    """
    if isinstance(td.type_spec, pa.RecordTypeSpec):
        fields: list = []
        for fg in td.type_spec.fields:
            ir_t = _lower_type_spec(fg.type_spec, ctx)
            if ir_t is None:
                return None
            default = _field_default_for_spec(fg.type_spec, fg.loc, ctx)
            for name in fg.names:
                fields.append(Field(
                    name=name, type=ir_t, default=default,
                    loc=_to_ir_loc(fg.loc),
                ))
        return Record(
            name=td.name, fields=tuple(fields),
            loc=_to_ir_loc(td.loc),
        )
    ctx.diagnostics.append(_diag(
        f"M5 type declarations only support `record` (not "
        f"{type(td.type_spec).__name__})",
        td.loc,
    ))
    return None


def _lower_type_spec(spec, ctx: _Ctx):
    """Lower a Pascal TypeSpec to an IR type. Returns NamedType (for
    builtins, user records, or strings) or NamedType("Array", ...).
    """
    if isinstance(spec, pa.NamedTypeSpec):
        # Built-in scalar (integer / boolean / char) routes through the
        # built-in mapping; user record types pass through as-is.
        if spec.name in _TYPE_MAP:
            tpy_name = _TYPE_MAP[spec.name]
            if tpy_name not in _BUILTIN_TYPES_NO_IMPORT:
                ctx.add_import("tpy", tpy_name)
            return NamedType(name=tpy_name, args=(),
                             loc=_to_ir_loc(spec.loc))
        if spec.name in ctx.record_types or spec.name in ctx.enum_types:
            return NamedType(name=spec.name, args=(),
                             loc=_to_ir_loc(spec.loc))
        if spec.name in ctx.type_aliases:
            # Re-lower as the underlying type. M10 only carries
            # subrange aliases (integer-backed).
            return _lower_type_spec(ctx.type_aliases[spec.name], ctx)
        ctx.diagnostics.append(_diag(
            f"unknown type {spec.name!r}", spec.loc))
        return None
    if isinstance(spec, pa.ArrayTypeSpec):
        elem = _lower_type_spec(spec.element, ctx)
        if elem is None:
            return None
        count = spec.upper - spec.lower + 1
        ctx.add_import("tpy", "Array")
        return NamedType(
            name="Array",
            args=(TypeTypeArg(value=elem),
                  IntTypeArg(value=count)),
            loc=_to_ir_loc(spec.loc),
        )
    if isinstance(spec, pa.StringTypeSpec):
        ctx.add_import("pascal.runtime.strings", "PStr")
        return NamedType(
            name="PStr",
            args=(IntTypeArg(value=spec.capacity),),
            loc=_to_ir_loc(spec.loc),
        )
    if isinstance(spec, pa.SubrangeTypeSpec):
        # M10 lowers Pascal subrange types to plain `integer`. Tier 2
        # makes the bounds first-class with insertion of assert /
        # clamp sites at assignment / index sites.
        ctx.add_import("tpy", "Int32")
        return NamedType(name="Int32", args=(),
                         loc=_to_ir_loc(spec.loc))
    ctx.diagnostics.append(_diag(
        f"unsupported type spec {type(spec).__name__}",
        getattr(spec, "loc", _zero_loc()),
    ))
    return None


def _lower_const_decl(decl: pa.ConstDecl, ctx: _Ctx):
    """Lower a Pascal `const N = value;` to an IR `VarDecl(init=value)`.

    M10 accepts literal initialisers; the IR type is inferred from
    the literal shape (int / real / bool / string). Compile-time
    constant expressions and string-as-PStr-bound consts are future
    work; document the gap rather than partially support either.
    """
    value = decl.value
    type_name: str | None = None
    if isinstance(value, pa.IntLit):
        type_name = "integer"
    elif isinstance(value, pa.FloatLit):
        type_name = "real"
    elif isinstance(value, pa.BoolLit):
        type_name = "boolean"
    elif isinstance(value, pa.StrLit):
        type_name = "string_view"  # treat as str (TPy StrView)
    elif isinstance(value, pa.UnaryOp) and value.op in ("+", "-"):
        inner = value.operand
        if isinstance(inner, pa.IntLit):
            type_name = "integer"
        elif isinstance(inner, pa.FloatLit):
            type_name = "real"
    if type_name is None:
        ctx.diagnostics.append(_diag(
            f"const {decl.name!r} value must be a literal in M10",
            decl.loc,
        ))
        return None
    # Map the inferred Pascal type to its IR shape. `string_view` is
    # the str/StrView path -- consts that hold a string literal stay
    # `str`-typed, not PStr; existing string-assign machinery already
    # routes a `str` source into a PStr lvalue when needed.
    if type_name == "string_view":
        ir_type = NamedType(name="str", args=(), loc=_to_ir_loc(decl.loc))
        ctx.type_env[decl.name] = "string_view"
    else:
        spec = pa.NamedTypeSpec(name=type_name, loc=decl.loc)
        ir_type = _lower_type_spec(spec, ctx)
        if ir_type is None:
            return None
        ctx.type_env[decl.name] = type_name
    init = _lower_expr(value, ctx)
    if init is None:
        return None
    return VarDecl(
        name=decl.name, type=ir_type, init=init,
        mutable=True, loc=_to_ir_loc(decl.loc),
    )


def _record_var_metadata(name: str, spec, ctx: _Ctx) -> None:
    """Update the static type env and per-variable metadata (array
    lower bounds, string capacities) based on a Pascal var's declared
    type."""
    if isinstance(spec, pa.NamedTypeSpec):
        # Resolve through type aliases (subranges, future aliases)
        # so writeln dispatch sees the underlying Pascal type rather
        # than the alias name.
        resolved = spec.name
        seen: set[str] = set()
        while resolved in ctx.type_aliases and resolved not in seen:
            seen.add(resolved)
            target = ctx.type_aliases[resolved]
            if isinstance(target, pa.NamedTypeSpec):
                resolved = target.name
                continue
            if isinstance(target, pa.SubrangeTypeSpec):
                resolved = "integer"
            break
        ctx.type_env[name] = resolved
        return
    if isinstance(spec, pa.ArrayTypeSpec):
        ctx.type_env[name] = "array"
        ctx.array_lower_bounds[name] = spec.lower
        return
    if isinstance(spec, pa.StringTypeSpec):
        ctx.type_env[name] = "string"
        ctx.string_vars[name] = spec.capacity
        return


def _default_init_for_spec(spec, loc: pa.Loc, ctx: _Ctx):
    """Zero-style default init for a function-local declaration.

    - Scalars route through `_default_init_for` (int 0, bool False).
    - User record types lower to a default constructor call `T()`.
    - Arrays / strings get their explicit type-args constructor.
    """
    if isinstance(spec, pa.NamedTypeSpec):
        if spec.name in ctx.record_types:
            return _default_record_ctor(spec.name, loc)
        return _default_init_for(spec.name, loc)
    if isinstance(spec, pa.ArrayTypeSpec):
        return _default_array_ctor(spec, loc, ctx)
    if isinstance(spec, pa.StringTypeSpec):
        return _default_fixstr_ctor(spec.capacity, loc, ctx)
    return None


def _default_init_module(spec, loc: pa.Loc, ctx: _Ctx):
    """Default init for a module-level variable. Scalars get None (C++
    auto-zero-init applies); records, arrays, and strings require an
    explicit constructor call -- TPy rejects uninitialised non-value
    globals."""
    if isinstance(spec, pa.NamedTypeSpec):
        if spec.name in ctx.record_types:
            return _default_record_ctor(spec.name, loc)
    if isinstance(spec, pa.ArrayTypeSpec):
        return _default_array_ctor(spec, loc, ctx)
    if isinstance(spec, pa.StringTypeSpec):
        return _default_fixstr_ctor(spec.capacity, loc, ctx)
    return None


def _default_array_ctor(spec: pa.ArrayTypeSpec, loc: pa.Loc, ctx: _Ctx):
    """`Array[T, N]()` constructor call. The translator emits the
    type args explicitly via IR `Call.type_args`; lowering threads
    them into the resulting TpyCall's `call_type` so sema's type-
    instantiation path picks the right element type and count."""
    elem_type = _lower_type_spec(spec.element, ctx)
    if elem_type is None:
        return None
    count = spec.upper - spec.lower + 1
    ctx.add_import("tpy", "Array")
    ir_loc = _to_ir_loc(loc)
    return Call(
        callee=Name(ident="Array", loc=ir_loc),
        type_args=(TypeTypeArg(value=elem_type),
                   IntTypeArg(value=count)),
        args=(), loc=ir_loc,
    )


def _default_fixstr_ctor(capacity: int, loc: pa.Loc, ctx: _Ctx):
    """`PStr[N]()` constructor call."""
    ctx.add_import("pascal.runtime.strings", "PStr")
    ir_loc = _to_ir_loc(loc)
    return Call(
        callee=Name(ident="PStr", loc=ir_loc),
        type_args=(IntTypeArg(value=capacity),),
        args=(), loc=ir_loc,
    )


def _default_record_ctor(name: str, loc: pa.Loc):
    ir_loc = _to_ir_loc(loc)
    return Call(
        callee=Name(ident=name, loc=ir_loc),
        args=(), loc=ir_loc,
    )


def _field_default_for_spec(spec, loc: pa.Loc, ctx: _Ctx):
    """Default value for a record field. Scalar fields get a literal
    zero; record-typed fields get a nested default-constructor call."""
    if isinstance(spec, pa.NamedTypeSpec):
        if spec.name in ctx.record_types:
            return _default_record_ctor(spec.name, loc)
        return _default_init_for(spec.name, loc)
    return None


# ---------------------------------------------------------------------------
# Subroutines

def _lower_subroutine(sub: pa.SubroutineDecl, ctx: _Ctx) -> Function | None:
    saved_func = ctx.current_func
    saved_return = ctx.current_func_return_type
    saved_var_params = ctx.current_var_params
    # Each subroutine starts with a fresh per-routine env layered over
    # the module env. M4 routines don't see module vars (Pascal does,
    # but supporting that requires more sema-side scope work).
    saved_type_env = ctx.type_env
    ctx.type_env = dict(ctx.type_env)
    ctx.current_func = sub.name
    ctx.current_func_return_type = sub.return_type
    ctx.current_var_params = {p.name for p in sub.params if p.is_var}

    # Params.
    ir_params: list = []
    for p in sub.params:
        base_type = _lower_type_spec(p.type_spec, ctx)
        if base_type is None:
            ctx.current_func = saved_func
            ctx.current_func_return_type = saved_return
            ctx.current_var_params = saved_var_params
            ctx.type_env = saved_type_env
            return None
        param_type = (PointerType(inner=base_type, loc=_to_ir_loc(p.loc))
                      if p.is_var else base_type)
        ir_params.append(Param(
            name=p.name, type=param_type, default=None,
            loc=_to_ir_loc(p.loc),
        ))
        # The type env records the Pascal type spelling (without the
        # `Ptr` wrapper) so writeln dispatch and other type-driven
        # decisions see the value type, not the pointer.
        if isinstance(p.type_spec, pa.NamedTypeSpec):
            ctx.type_env[p.name] = p.type_spec.name

    # `var` params require take_ptr / deref / unsafe_store visibility
    # in the lowered module.
    if ctx.current_var_params:
        ctx.add_import("tpy", "take_ptr")
        ctx.add_import("tpy", "deref")
        ctx.add_import("tpy.unsafe", "unsafe_store")

    # Local const sections come before the var section in routine
    # bodies (matches Pascal's textual order). Each const emits an
    # ordinary VarDecl with an explicit init.
    body_stmts: list = []
    for cb in sub.const_blocks:
        for cd in cb.decls:
            lowered = _lower_const_decl(cd, ctx)
            if lowered is not None:
                body_stmts.append(lowered)

    # Local var-block. Locals get a zero-style default init so TPy's
    # definitely-assigned analysis admits Pascal's common
    # write-then-read flow without forcing the user to spell out a
    # value at declaration. (Turbo Pascal de facto zero-inits locals;
    # the explicit init keeps the surface program portable.)
    if sub.var_block is not None:
        for decl in sub.var_block.decls:
            for name in decl.names:
                ir_type = _lower_type_spec(decl.type_spec, ctx)
                if ir_type is None:
                    continue
                _record_var_metadata(name, decl.type_spec, ctx)
                body_stmts.append(VarDecl(
                    name=name, type=ir_type,
                    init=_default_init_for_spec(decl.type_spec,
                                                decl.loc, ctx),
                    mutable=True, loc=_to_ir_loc(decl.loc),
                ))

    # Functions get a synthetic `__pascal_result` local that holds the
    # return value across multiple `function_name := expr` assignments.
    # A zero-style default initializer keeps TPy's "no uninitialized
    # reads" rule satisfied for routines that conditionally assign the
    # result (e.g. assigning only inside an if-branch); the explicit
    # init is harmless when every path eventually writes.
    if sub.return_type is not None:
        ret_spec = pa.NamedTypeSpec(name=sub.return_type, loc=sub.loc)
        result_type = _lower_type_spec(ret_spec, ctx)
        body_stmts.append(VarDecl(
            name=_RESULT_NAME, type=result_type,
            init=_default_init_for(sub.return_type, sub.loc),
            mutable=True, loc=_to_ir_loc(sub.loc),
        ))
        ctx.type_env[_RESULT_NAME] = sub.return_type

    # Body statements.
    for stmt in sub.body.statements:
        lowered = _lower_stmt(stmt, ctx)
        if lowered is not None:
            body_stmts.append(lowered)

    # Trailing return for functions.
    return_ir_type = None
    if sub.return_type is not None:
        ret_spec = pa.NamedTypeSpec(name=sub.return_type, loc=sub.loc)
        return_ir_type = _lower_type_spec(ret_spec, ctx)
        body_stmts.append(Return(
            value=Name(ident=_RESULT_NAME, loc=_to_ir_loc(sub.loc)),
            loc=_to_ir_loc(sub.loc),
        ))

    ctx.current_func = saved_func
    ctx.current_func_return_type = saved_return
    ctx.current_var_params = saved_var_params
    ctx.type_env = saved_type_env
    return Function(
        name=sub.name,
        params=tuple(ir_params),
        return_type=return_ir_type,
        body=tuple(body_stmts),
        loc=_to_ir_loc(sub.loc),
    )


# ---------------------------------------------------------------------------
# Statements

def _lower_stmt(stmt, ctx: _Ctx):
    if isinstance(stmt, pa.AssignStmt):
        return _lower_assign_stmt(stmt, ctx)
    if isinstance(stmt, pa.CallStmt):
        return _lower_call_stmt(stmt, ctx)
    if isinstance(stmt, pa.CompoundStmt):
        return _lower_compound_as_marker(stmt, ctx)
    if isinstance(stmt, pa.IfStmt):
        cond = _lower_expr(stmt.cond, ctx)
        if cond is None:
            return None
        then_body = _lower_branch(stmt.then_branch, ctx)
        else_body = (_lower_branch(stmt.else_branch, ctx)
                     if stmt.else_branch is not None else ())
        return If(cond=cond, then_body=then_body, else_body=else_body,
                  loc=_to_ir_loc(stmt.loc))
    if isinstance(stmt, pa.WhileStmt):
        cond = _lower_expr(stmt.cond, ctx)
        if cond is None:
            return None
        body = _lower_branch(stmt.body, ctx)
        return While(cond=cond, body=body, loc=_to_ir_loc(stmt.loc))
    if isinstance(stmt, pa.ForStmt):
        start = _lower_expr(stmt.start, ctx)
        end = _lower_expr(stmt.end, ctx)
        if start is None or end is None:
            return None
        ctx.type_env[stmt.var] = "integer"
        body = _lower_branch(stmt.body, ctx)
        direction = (RangeDir.ASC if stmt.direction == "to"
                     else RangeDir.DESC)
        return ForRange(
            var=stmt.var, start=start, end=end,
            direction=direction, inclusive=True, body=body,
            loc=_to_ir_loc(stmt.loc),
        )
    if isinstance(stmt, pa.RepeatStmt):
        cond = _lower_expr(stmt.cond, ctx)
        if cond is None:
            return None
        body = _lower_block_stmts(stmt.statements, ctx)
        return RepeatUntil(body=body, cond=cond, loc=_to_ir_loc(stmt.loc))
    if isinstance(stmt, pa.CaseStmt):
        return _lower_case_stmt(stmt, ctx)
    ctx.diagnostics.append(_diag(
        f"unsupported statement {type(stmt).__name__}",
        getattr(stmt, "loc", _zero_loc()),
    ))
    return None


def _lower_assign_stmt(stmt: pa.AssignStmt, ctx: _Ctx):
    # Bare-identifier target: special cases for Pascal function-return
    # (`function_name := expr`) and `var` parameter writes.
    if isinstance(stmt.target, pa.Ident):
        target_name = stmt.target.name
        if (ctx.current_func is not None
                and target_name == ctx.current_func
                and ctx.current_func_return_type is not None):
            target_name = _RESULT_NAME
        # String-variable assignment routes through PStr.assign so the
        # buffer stays in place. Without this, Pascal's `s := 'literal'`
        # would translate to `s = "literal"` (TPy `str` reassignment),
        # which TPy can't coerce into a PStr.
        if target_name in ctx.string_vars and _produces_string_value(
                stmt.value, ctx):
            return _lower_string_assign(target_name, stmt, ctx)
        # Char-typed target: a 1-char string literal RHS auto-wraps
        # in `Char(...)`. Pascal doesn't distinguish single-char
        # string vs char literals at the syntax level -- both spell as
        # 'A' -- so the conversion lands at the assignment site.
        if (ctx.type_env.get(target_name) == "char"
                and isinstance(stmt.value, pa.StrLit)
                and len(stmt.value.value) == 1):
            stmt = pa.AssignStmt(
                target=stmt.target,
                value=pa.CallExpr(
                    callee=pa.Ident(name="char", loc=stmt.value.loc),
                    args=[stmt.value], loc=stmt.value.loc,
                ),
                loc=stmt.loc,
            )
        value = _lower_expr(stmt.value, ctx)
        if value is None:
            return None
        if target_name in ctx.current_var_params:
            ctx.add_import("tpy.unsafe", "unsafe_store")
            ir_call = Call(
                callee=Name(ident="unsafe_store",
                            loc=_to_ir_loc(stmt.target.loc)),
                args=(
                    Name(ident=target_name, loc=_to_ir_loc(stmt.target.loc)),
                    IntLit(value=0, loc=_to_ir_loc(stmt.target.loc)),
                    value,
                ),
                loc=_to_ir_loc(stmt.loc),
            )
            return ExprStmt(value=ir_call, loc=_to_ir_loc(stmt.loc))
        target = Name(ident=target_name, loc=_to_ir_loc(stmt.target.loc))
        return Assign(targets=(target,), value=value, loc=_to_ir_loc(stmt.loc))
    # Compound target (field access / array index). The target is
    # lowered as an expression with `Attr` / `Subscript` nodes; IR
    # `Assign.targets` accepts these directly.
    target = _lower_target(stmt.target, ctx)
    if target is None:
        return None
    value = _lower_expr(stmt.value, ctx)
    if value is None:
        return None
    return Assign(targets=(target,), value=value, loc=_to_ir_loc(stmt.loc))


def _coerce_string_operand(lowered, source, ctx: _Ctx):
    """Wrap a PStr-typed operand in `str(...)` so it can be passed
    where a `str` (StrView) is expected -- both PStr's `__add__` and
    `__eq__` take `str`. String literals already have str type and
    pass through unchanged.
    """
    if isinstance(source, pa.StrLit):
        return lowered
    if not _produces_string_value(source, ctx):
        return lowered
    loc = lowered.loc if lowered is not None else None
    return Call(
        callee=Name(ident="str", loc=loc),
        args=(lowered,), loc=loc,
    )


def _produces_string_value(expr, ctx: _Ctx) -> bool:
    """True when an expression produces a string-typed result that
    should be assigned into a PStr lvalue via `assign(...)` rather
    than ordinary value reassignment."""
    if isinstance(expr, pa.StrLit):
        return True
    if isinstance(expr, pa.Ident):
        return expr.name in ctx.string_vars
    if isinstance(expr, pa.BinOp) and expr.op == "+":
        return (_produces_string_value(expr.lhs, ctx)
                or _produces_string_value(expr.rhs, ctx))
    return False


def _lower_string_assign(target_name: str, stmt: pa.AssignStmt,
                         ctx: _Ctx):
    """Lower `target := <string expr>` to `target.assign(<expr>)`. The
    PStr-side `assign` method clears the buffer and copies the source
    characters, so the lvalue keeps its identity (mirrors Pascal's
    value-copy assignment semantics).

    Non-`str` source values (a PStr variable, or the `Own[PStr]`
    produced by `+`) are funneled through `str(...)` so PStr.assign's
    `str` parameter always sees a string view -- PStr's `__str__`
    returns one for us.
    """
    value = _lower_expr(stmt.value, ctx)
    if value is None:
        return None
    loc = _to_ir_loc(stmt.loc)
    target_loc = _to_ir_loc(stmt.target.loc)
    if not isinstance(stmt.value, pa.StrLit):
        value = Call(
            callee=Name(ident="str", loc=target_loc),
            args=(value,), loc=loc,
        )
    call = Call(
        callee=Attr(
            target=Name(ident=target_name, loc=target_loc),
            ident="assign", loc=target_loc,
        ),
        args=(value,), loc=loc,
    )
    return ExprStmt(value=call, loc=loc)


def _lower_target(target, ctx: _Ctx):
    """Lower a Pascal lvalue (FieldAccess / IndexExpr). Field accesses
    pass through unchanged; array indexes subtract the variable's
    declared lower bound so the lowered IR is 0-based."""
    if isinstance(target, pa.FieldAccess):
        obj = _lower_expr(target.target, ctx)
        if obj is None:
            return None
        return Attr(target=obj, ident=target.ident,
                    loc=_to_ir_loc(target.loc))
    if isinstance(target, pa.IndexExpr):
        return _lower_index_expr(target, ctx)
    ctx.diagnostics.append(_diag(
        f"unsupported assignment target {type(target).__name__}",
        getattr(target, "loc", _zero_loc()),
    ))
    return None


def _lower_index_expr(expr: pa.IndexExpr, ctx: _Ctx):
    """Lower `arr[i]` to `IR Subscript(arr, i - lower_bound)`.

    The translator looks up the declared lower bound via the variable's
    name. Indexing into anything other than a bare identifier (e.g.
    nested arrays, future record-of-array fields) defaults to a
    lower bound of 1, matching Pascal's string-indexing convention.
    """
    target_node = _lower_expr(expr.target, ctx)
    index_node = _lower_expr(expr.index, ctx)
    if target_node is None or index_node is None:
        return None
    lower = 1
    if isinstance(expr.target, pa.Ident):
        lower = ctx.array_lower_bounds.get(expr.target.name, 1)
    if lower != 0:
        index_node = BinOp(
            op=BinOpKind.SUB,
            lhs=index_node,
            rhs=IntLit(value=lower, loc=_to_ir_loc(expr.loc)),
            loc=_to_ir_loc(expr.loc),
        )
    return Subscript(target=target_node, index=index_node,
                     loc=_to_ir_loc(expr.loc))


def _lower_compound_as_marker(stmt: pa.CompoundStmt, ctx: _Ctx):
    """A bare compound statement at the program-body level becomes a
    no-op marker (the IR has no compound-stmt node). When a compound
    is used as a control-flow branch, `_lower_branch` flattens it
    directly into the surrounding body; this fallback only handles
    the degenerate top-level case."""
    if not stmt.statements:
        return None
    if len(stmt.statements) == 1:
        return _lower_stmt(stmt.statements[0], ctx)
    ctx.diagnostics.append(_diag(
        "compound statement at program-body level with multiple "
        "inner statements is not supported (use them directly in the "
        "outer `begin ... end`)",
        stmt.loc,
    ))
    return None


def _lower_branch(stmt, ctx: _Ctx) -> tuple:
    if isinstance(stmt, pa.CompoundStmt):
        return _lower_block_stmts(stmt.statements, ctx)
    lowered = _lower_stmt(stmt, ctx)
    return (lowered,) if lowered is not None else ()


def _lower_block_stmts(stmts, ctx: _Ctx) -> tuple:
    out: list = []
    for s in stmts:
        lowered = _lower_stmt(s, ctx)
        if lowered is not None:
            out.append(lowered)
    return tuple(out)


def _lower_case_stmt(stmt: pa.CaseStmt, ctx: _Ctx):
    subject = _lower_expr(stmt.subject, ctx)
    if subject is None:
        return None
    cases: list = []
    for arm in stmt.arms:
        body = _lower_branch(arm.body, ctx)
        for v in arm.values:
            ir_v = _lower_expr(v, ctx)
            if ir_v is None:
                return None
            cases.append(MatchCase(
                pattern=MatchValue(value=ir_v, loc=_to_ir_loc(arm.loc)),
                body=body, loc=_to_ir_loc(arm.loc),
            ))
    if stmt.else_branch is not None:
        else_body = _lower_branch(stmt.else_branch, ctx)
        cases.append(MatchCase(
            pattern=MatchWildcard(loc=_to_ir_loc(stmt.loc)),
            body=else_body, loc=_to_ir_loc(stmt.loc),
        ))
    return Match(subject=subject, cases=tuple(cases),
                 loc=_to_ir_loc(stmt.loc))


# ---------------------------------------------------------------------------
# Calls

def _lower_call_stmt(stmt: pa.CallStmt, ctx: _Ctx):
    name = stmt.callee.name
    if name in ("write", "writeln"):
        # `_lower_writeln_stmt` handles both `write` and `writeln` --
        # the callee name picks the runtime function (`write` or
        # `writeln`) and the rest of the dispatch (per-arg-type
        # overloads) is identical.
        return _lower_writeln_stmt(stmt, ctx)
    if name in ("read", "readln"):
        return _lower_readln_stmt(stmt, ctx)
    if name in ("inc", "dec"):
        return _lower_inc_dec_stmt(stmt, ctx, name)
    if name == "randomize":
        if stmt.args:
            ctx.diagnostics.append(_diag(
                "randomize takes no arguments", stmt.loc))
            return None
        ctx.add_import("pascal.runtime.builtins", "randomize")
        call = Call(
            callee=Name(ident="randomize",
                        loc=_to_ir_loc(stmt.callee.loc)),
            args=(), loc=_to_ir_loc(stmt.loc),
        )
        return ExprStmt(value=call, loc=_to_ir_loc(stmt.loc))
    sig = ctx.signatures.get(name)
    if sig is None:
        ctx.diagnostics.append(_diag(
            f"unknown procedure {name!r}", stmt.callee.loc))
        return None
    # User procedure call. Build the argument list with take_ptr
    # wrapping for `var` parameters; the lowering rule for these is
    # documented above.
    ir_args = _build_user_call_args(stmt.callee.name, stmt.args, sig, ctx)
    if ir_args is None:
        return None
    ir_call = Call(
        callee=Name(ident=name, loc=_to_ir_loc(stmt.callee.loc)),
        args=ir_args,
        loc=_to_ir_loc(stmt.loc),
    )
    return ExprStmt(value=ir_call, loc=_to_ir_loc(stmt.loc))


def _lower_inc_dec_stmt(stmt: pa.CallStmt, ctx: _Ctx, name: str):
    """`inc(x)` / `inc(x, n)` -- mutate `x` by 1 (or `n`). `dec` is
    the symmetric subtract. M10 accepts an Ident target; richer
    lvalue targets (record fields, array elements) follow when the
    common kid-program cases need them."""
    if len(stmt.args) not in (1, 2):
        ctx.diagnostics.append(_diag(
            f"{name}(x[, n]) takes 1 or 2 arguments", stmt.loc))
        return None
    arg = stmt.args[0]
    if not isinstance(arg, pa.Ident):
        ctx.diagnostics.append(_diag(
            f"{name}(...) target must be a variable", arg.loc))
        return None
    step_pa = stmt.args[1] if len(stmt.args) == 2 else pa.IntLit(
        value=1, loc=arg.loc,
    )
    op = BinOpKind.ADD if name == "inc" else BinOpKind.SUB
    target_loc = _to_ir_loc(arg.loc)
    target = Name(ident=arg.name, loc=target_loc)
    step = _lower_expr(step_pa, ctx)
    if step is None:
        return None
    delta = BinOp(
        op=op,
        lhs=Name(ident=arg.name, loc=target_loc),
        rhs=step, loc=_to_ir_loc(stmt.loc),
    )
    # Var-param targets route through unsafe_store, the same way
    # ordinary assignment does.
    if arg.name in ctx.current_var_params:
        ctx.add_import("tpy.unsafe", "unsafe_store")
        ctx.add_import("tpy", "deref")
        # Reading via deref(name); writing via unsafe_store(name, 0, v).
        read = Call(
            callee=Name(ident="deref", loc=target_loc),
            args=(Name(ident=arg.name, loc=target_loc),),
            loc=target_loc,
        )
        delta = BinOp(op=op, lhs=read, rhs=step,
                      loc=_to_ir_loc(stmt.loc))
        store = Call(
            callee=Name(ident="unsafe_store", loc=target_loc),
            args=(
                Name(ident=arg.name, loc=target_loc),
                IntLit(value=0, loc=target_loc),
                delta,
            ),
            loc=_to_ir_loc(stmt.loc),
        )
        return ExprStmt(value=store, loc=_to_ir_loc(stmt.loc))
    return Assign(targets=(target,), value=delta,
                  loc=_to_ir_loc(stmt.loc))


def _lower_readln_stmt(stmt: pa.CallStmt, ctx: _Ctx):
    """Lower `readln(var)` / `read(var)` to an assignment from the
    matching pascal-runtime helper. Pascal passes the argument by
    reference; here we model that by dispatching on the variable's
    declared type and emitting a value-returning call paired with an
    assign-back to the variable. Args must be plain identifiers in
    M8 -- compound targets (record fields, array elements) wait on a
    later pass.
    """
    callee_name = stmt.callee.name
    if len(stmt.args) != 1:
        ctx.diagnostics.append(_diag(
            f"{callee_name!r} takes exactly one argument in M8",
            stmt.loc,
        ))
        return None
    arg = stmt.args[0]
    if not isinstance(arg, pa.Ident):
        ctx.diagnostics.append(_diag(
            f"{callee_name!r} argument must be a variable",
            getattr(arg, "loc", stmt.loc),
        ))
        return None
    var_name = arg.name
    arg_type = ctx.type_env.get(var_name)
    target_loc = _to_ir_loc(arg.loc)
    if arg_type == "integer":
        ctx.add_import("pascal.runtime.io", "readln_int")
        rhs = Call(
            callee=Name(ident="readln_int", loc=target_loc),
            args=(), loc=target_loc,
        )
        target = Name(ident=var_name, loc=target_loc)
        # `var` parameter: route through unsafe_store so the write
        # lands in the caller's slot.
        if var_name in ctx.current_var_params:
            ctx.add_import("tpy.unsafe", "unsafe_store")
            ir_call = Call(
                callee=Name(ident="unsafe_store", loc=target_loc),
                args=(target, IntLit(value=0, loc=target_loc), rhs),
                loc=_to_ir_loc(stmt.loc),
            )
            return ExprStmt(value=ir_call, loc=_to_ir_loc(stmt.loc))
        return Assign(
            targets=(target,), value=rhs,
            loc=_to_ir_loc(stmt.loc),
        )
    if arg_type == "string":
        ctx.add_import("pascal.runtime.io", "readln_line")
        line_call = Call(
            callee=Name(ident="readln_line", loc=target_loc),
            args=(), loc=target_loc,
        )
        # Route through PStr.assign for the same reason `s := lit`
        # does -- preserves the lvalue identity and matches Pascal's
        # in-place semantics.
        assign_call = Call(
            callee=Attr(
                target=Name(ident=var_name, loc=target_loc),
                ident="assign", loc=target_loc,
            ),
            args=(line_call,), loc=_to_ir_loc(stmt.loc),
        )
        return ExprStmt(value=assign_call, loc=_to_ir_loc(stmt.loc))
    ctx.diagnostics.append(_diag(
        f"{callee_name!r} does not support type {arg_type!r}",
        arg.loc,
    ))
    return None


def _lower_writeln_stmt(stmt: pa.CallStmt, ctx: _Ctx):
    """Lower Pascal `writeln(x)` / `write(x)` to TPy `print` -- the
    builtin handles any type (`__str__`-driven), so no Pascal-runtime
    detour is needed. The translator wraps PStr operands in `str(...)`
    only because TPy's print sees a string view rather than a PStr.
    """
    callee_name = stmt.callee.name
    if len(stmt.args) != 1:
        ctx.diagnostics.append(_diag(
            f"{callee_name!r} takes exactly one argument",
            stmt.loc,
        ))
        return None
    arg = stmt.args[0]
    ir_arg = _lower_expr(arg, ctx)
    if ir_arg is None:
        return None
    arg_type = _static_type_of(arg, ctx)
    if arg_type == "string" and not isinstance(arg, pa.StrLit):
        # PStr argument: TPy's print formats `str` / `StrView`
        # naturally; wrap to surface the string view.
        ir_arg = Call(
            callee=Name(ident="str", loc=_to_ir_loc(arg.loc)),
            args=(ir_arg,), loc=_to_ir_loc(arg.loc),
        )
    callee_loc = _to_ir_loc(stmt.callee.loc)
    if callee_name == "writeln":
        ir_call = Call(
            callee=Name(ident="print", loc=callee_loc),
            args=(ir_arg,), loc=_to_ir_loc(stmt.loc),
        )
    else:  # write
        ir_call = Call(
            callee=Name(ident="print", loc=callee_loc),
            args=(ir_arg,),
            kwargs=(("end", StrLit(value="", loc=callee_loc)),),
            loc=_to_ir_loc(stmt.loc),
        )
    return ExprStmt(value=ir_call, loc=_to_ir_loc(stmt.loc))


def _build_user_call_args(callee_name: str, args: list, sig: _Sig,
                          ctx: _Ctx) -> tuple | None:
    if len(args) != len(sig.param_names):
        ctx.diagnostics.append(_diag(
            f"{callee_name!r} expects {len(sig.param_names)} arguments, "
            f"got {len(args)}",
            getattr(args[0], "loc", _zero_loc()) if args else _zero_loc(),
        ))
        return None
    out: list = []
    for a, is_var in zip(args, sig.param_var_flags):
        if is_var:
            # Pascal requires var-arguments to be variable references
            # (identifiers in the M4 subset). take_ptr's lvalue rule
            # rejects anything else; surface a clear translator error
            # rather than letting the C++ compiler complain.
            if not isinstance(a, pa.Ident):
                ctx.diagnostics.append(_diag(
                    "argument to a `var` parameter must be a variable",
                    getattr(a, "loc", _zero_loc()),
                ))
                return None
            ctx.add_import("tpy", "take_ptr")
            inner = Name(ident=a.name, loc=_to_ir_loc(a.loc))
            out.append(Call(
                callee=Name(ident="take_ptr", loc=_to_ir_loc(a.loc)),
                args=(inner,),
                loc=_to_ir_loc(a.loc),
            ))
        else:
            ir_a = _lower_expr(a, ctx)
            if ir_a is None:
                return None
            out.append(ir_a)
    return tuple(out)


# ---------------------------------------------------------------------------
# Expressions

def _lower_expr(expr, ctx: _Ctx):
    if isinstance(expr, pa.IntLit):
        return IntLit(value=expr.value, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.FloatLit):
        return FloatLit(value=expr.value, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.StrLit):
        return StrLit(value=expr.value, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.BoolLit):
        return BoolLit(value=expr.value, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.Ident):
        # Reads of a `var` parameter route through `deref(p)`.
        if expr.name in ctx.current_var_params:
            ctx.add_import("tpy", "deref")
            return Call(
                callee=Name(ident="deref", loc=_to_ir_loc(expr.loc)),
                args=(Name(ident=expr.name, loc=_to_ir_loc(expr.loc)),),
                loc=_to_ir_loc(expr.loc),
            )
        # Unqualified enum member reference: Pascal puts every enum
        # member in scope, so `Red` reads as `Color.Red`. A local
        # variable / parameter / function name with the same spelling
        # wins (matches Pascal's scoping rule).
        if (expr.name in ctx.enum_member_to_type
                and expr.name not in ctx.type_env
                and expr.name not in ctx.signatures):
            enum_name = ctx.enum_member_to_type[expr.name]
            ir_loc = _to_ir_loc(expr.loc)
            return Attr(
                target=Name(ident=enum_name, loc=ir_loc),
                ident=expr.name, loc=ir_loc,
            )
        return Name(ident=expr.name, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.FieldAccess):
        obj = _lower_expr(expr.target, ctx)
        if obj is None:
            return None
        return Attr(target=obj, ident=expr.ident,
                    loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.IndexExpr):
        return _lower_index_expr(expr, ctx)
    if isinstance(expr, pa.CallExpr):
        return _lower_call_expr(expr, ctx)
    if isinstance(expr, pa.BinOp):
        # `x in [a, b, c]` desugars to `(x = a) or (x = b) or (x = c)`.
        # M10 only accepts literal-element set RHS; bigger Pascal sets
        # arrive in Tier 2 with a real `tpy.Set` runtime type.
        if expr.op == "in" and isinstance(expr.rhs, pa.SetLit):
            return _lower_in_membership(expr, ctx)
        cmp_op = _CMP_OP.get(expr.op)
        if cmp_op is not None:
            lhs = _lower_expr(expr.lhs, ctx)
            rhs = _lower_expr(expr.rhs, ctx)
            if lhs is None or rhs is None:
                return None
            # String comparison: route both operands through `str(...)`
            # so the comparison reaches PStr's `__eq__(other: str)`.
            lhs = _coerce_string_operand(lhs, expr.lhs, ctx)
            rhs = _coerce_string_operand(rhs, expr.rhs, ctx)
            return Compare(
                lhs=lhs, ops=(cmp_op,), comparators=(rhs,),
                loc=_to_ir_loc(expr.loc),
            )
        op = _BIN_OP.get(expr.op)
        if op is None:
            ctx.diagnostics.append(_diag(
                f"unsupported binary operator {expr.op!r}", expr.loc))
            return None
        lhs = _lower_expr(expr.lhs, ctx)
        rhs = _lower_expr(expr.rhs, ctx)
        if lhs is None or rhs is None:
            return None
        # String concatenation (`+`): wrap each PStr operand in
        # `str(...)` so each call site hits PStr.__add__(other: str)
        # regardless of which side carries the PStr value.
        if op == BinOpKind.ADD and _produces_string_value(expr, ctx):
            lhs = _coerce_string_operand(lhs, expr.lhs, ctx)
            rhs = _coerce_string_operand(rhs, expr.rhs, ctx)
        return BinOp(op=op, lhs=lhs, rhs=rhs, loc=_to_ir_loc(expr.loc))
    if isinstance(expr, pa.UnaryOp):
        op = _UNARY_OP.get(expr.op)
        if op is None:
            ctx.diagnostics.append(_diag(
                f"unsupported unary operator {expr.op!r}", expr.loc))
            return None
        operand = _lower_expr(expr.operand, ctx)
        if operand is None:
            return None
        return UnaryOp(op=op, operand=operand, loc=_to_ir_loc(expr.loc))
    ctx.diagnostics.append(_diag(
        f"unsupported expression {type(expr).__name__}",
        getattr(expr, "loc", _zero_loc()),
    ))
    return None


def _lower_in_membership(expr: pa.BinOp, ctx: _Ctx):
    """Lower `x in [a, b, c]` to `(x = a) or (x = b) or (x = c)`. An
    empty literal set folds to a plain `False`. Each element is
    compared with the LHS via the existing equality / comparison
    machinery -- enum members route through their unqualified-scope
    rewrite, so `c in [Red, Green]` works without naming the enum
    type."""
    if not isinstance(expr.rhs, pa.SetLit):
        ctx.diagnostics.append(_diag(
            "`in` rhs must be a set literal in M10", expr.rhs.loc
            if hasattr(expr.rhs, "loc") else expr.loc,
        ))
        return None
    elements = expr.rhs.elements
    ir_loc = _to_ir_loc(expr.loc)
    if not elements:
        return BoolLit(value=False, loc=ir_loc)
    lhs_ir = _lower_expr(expr.lhs, ctx)
    if lhs_ir is None:
        return None
    # Build `(lhs = e1) or (lhs = e2) or ...`. Lowering each element
    # via the standard expression path picks up the enum-member
    # rewrite + literal-vs-Ident handling for free.
    or_chain = None
    for elem in elements:
        elem_ir = _lower_expr(elem, ctx)
        if elem_ir is None:
            return None
        cmp = Compare(
            lhs=lhs_ir, ops=(CmpOpKind.EQ,), comparators=(elem_ir,),
            loc=ir_loc,
        )
        or_chain = cmp if or_chain is None else BinOp(
            op=BinOpKind.LOGICAL_OR, lhs=or_chain, rhs=cmp, loc=ir_loc,
        )
    return or_chain


def _builtin_passthrough(tpy_name: str, expr: pa.CallExpr, ctx: _Ctx):
    """Wrap an arg list in a Call to a TPy / Python builtin of the
    matching name. Used for `abs`, `chr`, `ord`, `length`->`len` --
    one Pascal call site -> one TPy call."""
    if len(expr.args) != 1:
        ctx.diagnostics.append(_diag(
            f"{expr.callee.name}(x) takes exactly one argument",
            expr.callee.loc,
        ))
        return None
    arg = _lower_expr(expr.args[0], ctx)
    if arg is None:
        return None
    return Call(
        callee=Name(ident=tpy_name, loc=_to_ir_loc(expr.callee.loc)),
        args=(arg,), loc=_to_ir_loc(expr.loc),
    )


def _builtin_sqr(expr: pa.CallExpr, ctx: _Ctx):
    """`sqr(x)` -> `x * x`. Pascal's classic shorthand."""
    if len(expr.args) != 1:
        ctx.diagnostics.append(_diag(
            "sqr(x) takes exactly one argument", expr.callee.loc))
        return None
    arg = _lower_expr(expr.args[0], ctx)
    if arg is None:
        return None
    return BinOp(op=BinOpKind.MUL, lhs=arg, rhs=arg,
                 loc=_to_ir_loc(expr.loc))


def _builtin_succ_pred(name: str, expr: pa.CallExpr, ctx: _Ctx):
    """`succ(x)` / `pred(x)` -- step an ordinal by one. M10 supports
    integer args (routes to `pascal.runtime.builtins.succ_int` /
    `pred_int`); char args are desugared to `chr(ord(c) +/- 1)`;
    enum args produce a clear translator-side error for now."""
    if len(expr.args) != 1:
        ctx.diagnostics.append(_diag(
            f"{name}(x) takes exactly one argument", expr.callee.loc))
        return None
    arg = expr.args[0]
    arg_type = _static_type_of(arg, ctx)
    if arg_type == "integer":
        helper = f"{name}_int"
        ctx.add_import("pascal.runtime.builtins", helper)
        ir_arg = _lower_expr(arg, ctx)
        if ir_arg is None:
            return None
        return Call(
            callee=Name(ident=helper, loc=_to_ir_loc(expr.callee.loc)),
            args=(ir_arg,), loc=_to_ir_loc(expr.loc),
        )
    if arg_type == "char":
        # `succ(c)` -> `chr(ord(c) + 1)`; `pred(c)` -> `chr(ord(c) - 1)`.
        ir_arg = _lower_expr(arg, ctx)
        if ir_arg is None:
            return None
        ir_loc = _to_ir_loc(expr.loc)
        op = BinOpKind.ADD if name == "succ" else BinOpKind.SUB
        ord_call = Call(
            callee=Name(ident="ord", loc=ir_loc),
            args=(ir_arg,), loc=ir_loc,
        )
        stepped = BinOp(
            op=op, lhs=ord_call,
            rhs=IntLit(value=1, loc=ir_loc), loc=ir_loc,
        )
        return Call(
            callee=Name(ident="chr", loc=ir_loc),
            args=(stepped,), loc=ir_loc,
        )
    ctx.diagnostics.append(_diag(
        f"{name}() on type {arg_type!r} is not supported in M10 "
        "(integer and char only)",
        arg.loc,
    ))
    return None


def _builtin_random(expr: pa.CallExpr, ctx: _Ctx):
    """`random` (no arg) returns a real in [0, 1); `random(n)`
    returns an integer in [0, n). Both route to
    `pascal.runtime.builtins`."""
    if len(expr.args) == 0:
        ctx.add_import("pascal.runtime.builtins", "random_real")
        return Call(
            callee=Name(ident="random_real",
                        loc=_to_ir_loc(expr.callee.loc)),
            args=(), loc=_to_ir_loc(expr.loc),
        )
    if len(expr.args) == 1:
        ctx.add_import("pascal.runtime.builtins", "random_int")
        ir_arg = _lower_expr(expr.args[0], ctx)
        if ir_arg is None:
            return None
        return Call(
            callee=Name(ident="random_int",
                        loc=_to_ir_loc(expr.callee.loc)),
            args=(ir_arg,), loc=_to_ir_loc(expr.loc),
        )
    ctx.diagnostics.append(_diag(
        "random takes 0 or 1 argument", expr.callee.loc))
    return None


def _builtin_odd(expr: pa.CallExpr, ctx: _Ctx):
    """`odd(x)` -> `(x mod 2) <> 0`. Equivalent to `x & 1 == 1` for
    non-negative integers and matches Pascal's definition (true when
    the low bit is set) for negative integers as well."""
    if len(expr.args) != 1:
        ctx.diagnostics.append(_diag(
            "odd(x) takes exactly one argument", expr.callee.loc))
        return None
    arg = _lower_expr(expr.args[0], ctx)
    if arg is None:
        return None
    ir_loc = _to_ir_loc(expr.loc)
    mod = BinOp(
        op=BinOpKind.MOD,
        lhs=arg, rhs=IntLit(value=2, loc=ir_loc),
        loc=ir_loc,
    )
    return Compare(
        lhs=mod, ops=(CmpOpKind.NE,),
        comparators=(IntLit(value=0, loc=ir_loc),),
        loc=ir_loc,
    )


_BUILTIN_EXPR: dict[str, "Callable"] = {
    # length(x) -> TPy len(x); works for strings and arrays.
    "length": lambda e, c: _builtin_passthrough("len", e, c),
    # Pascal pass-through builtins. TPy provides matching functions
    # in builtins; no import needed.
    "abs": lambda e, c: _builtin_passthrough("abs", e, c),
    "chr": lambda e, c: _builtin_passthrough("chr", e, c),
    "ord": lambda e, c: _builtin_passthrough("ord", e, c),
    # Pascal-internal char wrapping: `char('X')` -> TPy Char('X').
    # Emitted by the assignment-coercion path when a 1-char string
    # literal lands in a Char-typed lvalue.
    "char": lambda e, c: _builtin_passthrough("Char", e, c),
    # Desugar-only builtins.
    "sqr": _builtin_sqr,
    "odd": _builtin_odd,
    # Pascal-runtime backed builtins.
    "succ": lambda e, c: _builtin_succ_pred("succ", e, c),
    "pred": lambda e, c: _builtin_succ_pred("pred", e, c),
    "random": _builtin_random,
}


def _lower_call_expr(expr: pa.CallExpr, ctx: _Ctx):
    name = expr.callee.name
    # Pascal builtins routed inline (no Pascal-runtime trip).
    if name in _BUILTIN_EXPR:
        return _BUILTIN_EXPR[name](expr, ctx)
    sig = ctx.signatures.get(name)
    if sig is None:
        # The name isn't a known same-module subroutine. If the unit
        # `uses` some other module(s), the function might be star-
        # imported from there -- emit a generic Call and let TPy's
        # sema validate after star-expansion has run. With no
        # `uses` clauses the name is genuinely unknown; error out.
        if ctx.star_imports:
            args: list = []
            for a in expr.args:
                la = _lower_expr(a, ctx)
                if la is None:
                    return None
                args.append(la)
            return Call(
                callee=Name(ident=name,
                            loc=_to_ir_loc(expr.callee.loc)),
                args=tuple(args),
                loc=_to_ir_loc(expr.loc),
            )
        ctx.diagnostics.append(_diag(
            f"unknown function {name!r} in expression position",
            expr.callee.loc,
        ))
        return None
    if sig.return_type is None:
        ctx.diagnostics.append(_diag(
            f"procedure {name!r} has no return value; cannot be used "
            f"in an expression",
            expr.callee.loc,
        ))
        return None
    ir_args = _build_user_call_args(name, expr.args, sig, ctx)
    if ir_args is None:
        return None
    return Call(
        callee=Name(ident=name, loc=_to_ir_loc(expr.callee.loc)),
        args=ir_args,
        loc=_to_ir_loc(expr.loc),
    )


def _static_type_of(expr, ctx: _Ctx) -> str | None:
    """Best-effort static type for writeln-overload dispatch."""
    if isinstance(expr, pa.IntLit):
        return "integer"
    if isinstance(expr, pa.FloatLit):
        return "real"
    if isinstance(expr, pa.StrLit):
        return "string"
    if isinstance(expr, pa.BoolLit):
        return "boolean"
    if isinstance(expr, pa.Ident):
        if expr.name in ctx.type_env:
            t = ctx.type_env[expr.name]
            # A const declared as a string literal is `string_view` in
            # the type env; writeln dispatch and string-arg paths both
            # treat it the same as the `string` (PStr) type.
            return "string" if t == "string_view" else t
        # Bare reference to an enum member: its static type is the
        # enclosing enum type name.
        if expr.name in ctx.enum_member_to_type:
            return ctx.enum_member_to_type[expr.name]
        return None
    if isinstance(expr, pa.FieldAccess):
        # M5 supports only one level of field access on a known record
        # variable (`p.x`); deeper chains route through this branch
        # recursively, but currently nested-record fields don't have a
        # tracked type spelling and return None.
        if isinstance(expr.target, pa.Ident):
            record_name = ctx.type_env.get(expr.target.name)
            if record_name in ctx.record_fields:
                return ctx.record_fields[record_name].get(expr.ident)
        return None
    if isinstance(expr, pa.IndexExpr):
        # M5 only supports arrays of scalar `integer` elements; tracking
        # the element type for nested arrays is future work.
        if isinstance(expr.target, pa.Ident):
            if expr.target.name in ctx.array_lower_bounds:
                return "integer"
        return None
    if isinstance(expr, pa.CallExpr):
        name = expr.callee.name
        # Pascal builtins: routing here mirrors the
        # `_BUILTIN_EXPR` table. Adding a new builtin? add a return
        # type here too so writeln-dispatch and assignment-target
        # type-check both see the right result type.
        if name in ("length", "ord"):
            return "integer"
        if name == "chr":
            return "char"
        if name == "abs":
            # abs propagates the operand type (Int32 / real / etc.).
            return _static_type_of(expr.args[0], ctx) if expr.args else None
        if name == "sqr":
            return _static_type_of(expr.args[0], ctx) if expr.args else None
        if name == "odd":
            return "boolean"
        if name in ("succ", "pred"):
            return _static_type_of(expr.args[0], ctx) if expr.args else None
        if name == "random":
            return "integer" if expr.args else "real"
        sig = ctx.signatures.get(name)
        return sig.return_type if sig is not None else None
    if isinstance(expr, pa.BinOp):
        # String operations stay string-typed; numeric arithmetic
        # widens to `real` whenever either operand is real (Pascal's
        # promotion rule). `/` (true division) always yields a real,
        # matching Pascal semantics.
        if expr.op == "+" and _produces_string_value(expr, ctx):
            return "string"
        if expr.op == "/":
            return "real"
        lhs_t = _static_type_of(expr.lhs, ctx)
        rhs_t = _static_type_of(expr.rhs, ctx)
        if lhs_t == "real" or rhs_t == "real":
            return "real"
        return "integer"
    if isinstance(expr, pa.UnaryOp):
        operand_t = _static_type_of(expr.operand, ctx)
        if operand_t == "real":
            return "real"
        return "integer"
    return None


# ---------------------------------------------------------------------------
# Type lowering

def _default_init_for(type_name: str, loc: pa.Loc):
    """Zero-style default value for a Pascal type spelling. Used for
    the synthetic `__pascal_result` local so TPy's flow-sensitive
    init analysis accepts conditional assignments."""
    ir_loc = _to_ir_loc(loc)
    if type_name == "integer":
        return IntLit(value=0, loc=ir_loc)
    if type_name == "boolean":
        return BoolLit(value=False, loc=ir_loc)
    if type_name in ("real", "double"):
        return FloatLit(value=0.0, loc=ir_loc)
    # Fallback: no init. The compiler will raise a clear error if a
    # path reads the synthetic var before writing it, which is the
    # right behavior for unsupported return types.
    return None


# ---------------------------------------------------------------------------
# Loc / diag plumbing

def _to_ir_loc(loc: pa.Loc) -> IRLoc:
    return IRLoc(
        file=loc.file,
        line=loc.line, col=loc.col,
        end_line=loc.end_line, end_col=loc.end_col,
        source_language="pascal",
    )


def _zero_loc() -> pa.Loc:
    return pa.Loc(file=Path("<unknown>"),
                  line=0, col=0, end_line=0, end_col=0)


def _diag(message: str, loc: pa.Loc) -> Diagnostic:
    from tpyc.parse import SourceLocation
    return Diagnostic(
        level=DiagnosticLevel.ERROR,
        message=message,
        loc=SourceLocation(line=loc.line, column=max(0, loc.col - 1),
                           file=str(loc.file)),
    )
