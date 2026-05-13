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
    ExprStmt,
    Field,
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
}

# Types that should NOT trigger an automatic `from tpy import X` -- the
# entries that live in TPy's builtins module are visible without import.
_BUILTIN_TYPES_NO_IMPORT: frozenset[str] = frozenset({"bool"})

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
    # Per-routine state (saved / restored when entering / leaving each
    # subroutine; left at the defaults at module-body level).
    current_func: str | None = None
    current_func_return_type: str | None = None
    current_var_params: set[str] = field(default_factory=set)

    def add_import(self, module: str, name: str) -> None:
        self.needed_imports.setdefault(module, set()).add(name)


def translate(program: pa.Program,
              module_name: str) -> tuple[FrontendModule, list[Diagnostic]]:
    ctx = _Ctx()

    # Pre-scan subroutines so callers can see their signatures even
    # when emitted before the callee in source order.
    for sub in program.subroutines:
        ctx.signatures[sub.name] = _Sig(
            name=sub.name,
            param_names=[p.name for p in sub.params],
            param_var_flags=[p.is_var for p in sub.params],
            return_type=sub.return_type,
        )

    # Pre-scan type blocks so all user record names are known before
    # any var/param/return type is lowered against them. Also record
    # each field's Pascal type spelling so writeln dispatch can route
    # `p.x` correctly.
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

    records: list = []
    top_level_stmts: list = []
    functions: list = []

    # Type blocks -> IR Records.
    for tb in program.type_blocks:
        for td in tb.decls:
            rec = _lower_type_decl(td, ctx)
            if rec is not None:
                records.append(rec)

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

    # Module-level body.
    for stmt in program.block.statements:
        lowered = _lower_stmt(stmt, ctx)
        if lowered is not None:
            top_level_stmts.append(lowered)

    # Subroutines.
    for sub in program.subroutines:
        fn = _lower_subroutine(sub, ctx)
        if fn is not None:
            functions.append(fn)

    imports = tuple(
        FromImport(
            module=mod,
            names=tuple(sorted(
                (ImportName(original=n, local=n) for n in names),
                key=lambda im: im.local,
            )),
        )
        for mod, names in sorted(ctx.needed_imports.items())
    )

    fm = FrontendModule(
        qname=module_name,
        source_language="pascal",
        source_lines=program.source_lines,
        imports=imports,
        records=tuple(records),
        functions=tuple(functions),
        top_level_stmts=tuple(top_level_stmts),
        directives=FrontendDirectives(),
    )
    return fm, ctx.diagnostics


# ---------------------------------------------------------------------------
# Type lowering

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
    builtins or user records) or NamedType("Array", ...) (for arrays).
    """
    if isinstance(spec, pa.NamedTypeSpec):
        # Built-in scalar (integer / boolean) routes through the
        # built-in mapping; user record types pass through as-is.
        if spec.name in _TYPE_MAP:
            tpy_name = _TYPE_MAP[spec.name]
            if tpy_name not in _BUILTIN_TYPES_NO_IMPORT:
                ctx.add_import("tpy", tpy_name)
            return NamedType(name=tpy_name, args=(),
                             loc=_to_ir_loc(spec.loc))
        if spec.name in ctx.record_types:
            return NamedType(name=spec.name, args=(),
                             loc=_to_ir_loc(spec.loc))
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
    ctx.diagnostics.append(_diag(
        f"unsupported type spec {type(spec).__name__}",
        getattr(spec, "loc", _zero_loc()),
    ))
    return None


def _record_var_metadata(name: str, spec, ctx: _Ctx) -> None:
    """Update the static type env and per-variable metadata (array
    lower bounds) based on a Pascal var's declared type."""
    if isinstance(spec, pa.NamedTypeSpec):
        ctx.type_env[name] = spec.name
        return
    if isinstance(spec, pa.ArrayTypeSpec):
        ctx.type_env[name] = "array"
        ctx.array_lower_bounds[name] = spec.lower
        return


def _default_init_for_spec(spec, loc: pa.Loc, ctx: _Ctx):
    """Zero-style default init for a function-local declaration.

    - Scalars route through `_default_init_for` (int 0, bool False).
    - User record types lower to a default constructor call `T()`,
      which TPy provides for records without an explicit `__init__`.
    - Arrays rely on `Array[T, N]`'s own zero-fill default
      construction (no init needed in the IR).
    """
    if isinstance(spec, pa.NamedTypeSpec):
        if spec.name in ctx.record_types:
            return _default_record_ctor(spec.name, loc)
        return _default_init_for(spec.name, loc)
    return None


def _default_init_module(spec, loc: pa.Loc, ctx: _Ctx):
    """Default init for a module-level variable. Scalars get None (C++
    auto-zero-init applies); records and arrays require an explicit
    constructor call -- TPy rejects uninitialised non-value globals."""
    if isinstance(spec, pa.NamedTypeSpec):
        if spec.name in ctx.record_types:
            return _default_record_ctor(spec.name, loc)
    if isinstance(spec, pa.ArrayTypeSpec):
        return _default_array_ctor(spec, loc, ctx)
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

    # Local var-block. Locals get a zero-style default init so TPy's
    # definitely-assigned analysis admits Pascal's common
    # write-then-read flow without forcing the user to spell out a
    # value at declaration. (Turbo Pascal de facto zero-inits locals;
    # the explicit init keeps the surface program portable.)
    body_stmts: list = []
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
        return _lower_writeln_stmt(stmt, ctx)
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


def _lower_writeln_stmt(stmt: pa.CallStmt, ctx: _Ctx):
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
    if arg_type == "integer":
        runtime_name = f"{callee_name}_int"
    else:
        runtime_name = callee_name
    ctx.add_import("pascal.runtime.io", runtime_name)
    ir_call = Call(
        callee=Name(ident=runtime_name, loc=_to_ir_loc(stmt.callee.loc)),
        args=(ir_arg,),
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
        cmp_op = _CMP_OP.get(expr.op)
        if cmp_op is not None:
            lhs = _lower_expr(expr.lhs, ctx)
            rhs = _lower_expr(expr.rhs, ctx)
            if lhs is None or rhs is None:
                return None
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


def _lower_call_expr(expr: pa.CallExpr, ctx: _Ctx):
    name = expr.callee.name
    sig = ctx.signatures.get(name)
    if sig is None:
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
    if isinstance(expr, pa.StrLit):
        return "string"
    if isinstance(expr, pa.BoolLit):
        return "boolean"
    if isinstance(expr, pa.Ident):
        return ctx.type_env.get(expr.name)
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
        sig = ctx.signatures.get(expr.callee.name)
        return sig.return_type if sig is not None else None
    if isinstance(expr, (pa.BinOp, pa.UnaryOp)):
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
