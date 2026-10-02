"""Pre-scan utilities for TurboPython function bodies.

Pure AST walking -- no type registry or analysis context needed.
Run once in sema; results consumed by both sema and codegen.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, fields as dc_fields

from .parse import (
    TpyStmt, TpyExpr, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign,
    TpyForEach, TpyWith, TpyName, TpySubscript,
    TpyCall, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyMethodCall,
    TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
    TpyBoolLiteral, TpyNoneLiteral,
    TpyCoerce, TpyFieldAccess, TpyIfExpr, TpyNamedExpr,
    TpyNestedDef,
    TpyFStringValue, TpyComprehensionGenerator,
    TpyTupleLiteral, TpyFString,
    TpyDelVar, TpyDelAttr, TpyDelItem, TpyNonlocal, TpyGlobal, TpyTry,
    TpyLambda, TpyMatch, TpyWhile, TpyAwait, TpyIf,
    TpyPattern, TpyCapturePattern, TpyAsPattern, TpyOrPattern,
)
from .parse.nodes import (SourceLocation, is_property_getter_read,
                          iter_capture_bindings,
                          stmts_have_any_suspension, walk_body_stmts,
                          walrus_bindings, written_names)
from .identity_map import IdentityMap
from .value_category import CONTAINER_LITERAL_NODES, peel_coerce


@dataclass
class ScanResult:
    """Result of pre-scanning a function body for reassigned variables."""
    reassigned: set[str] = field(default_factory=set)
    rvalue_reassigned: set[str] = field(default_factory=set)
    lvalue_reassigned: set[str] = field(default_factory=set)
    aug_assigned: set[str] = field(default_factory=set)
    # alias_name -> source_name for lvalue-initialized, non-reassigned variables
    # with simple TpyName init (T& reference candidates).
    alias_sources: dict[str, str] = field(default_factory=dict)
    # alias_name -> root_name for field/subscript-chain inits (a = o.inner,
    # n = xs[0]): the binding references INTO the root's storage, so moving
    # the root while the alias is live dangles it. Kept separate from
    # alias_sources because that map also feeds del codegen and flow-fact
    # kill groups, which expect whole-object name-to-name aliases only;
    # this map is merged in solely for last-use liveness suppression.
    chain_alias_sources: dict[str, str] = field(default_factory=dict)
    # Variables initially aliased from another name (before reassignment cleanup).
    # Used by del codegen to avoid destroying through a pointer that may
    # still point at the source variable's storage.
    initial_alias_names: set[str] = field(default_factory=set)
    # name -> location of its first binding in the body (params excluded).
    first_bind_loc: dict[str, SourceLocation | None] = field(default_factory=dict)
    # Bindings whose scope is narrower than the body (comprehension and
    # genexpr loop vars, lambda params, `except ... as`, `match` arm
    # captures); kept apart so they never displace a body binding.
    scoped_bind_loc: dict[str, SourceLocation | None] = field(default_factory=dict)
    # name -> location of the `def` that binds it, for every nested def in
    # this body at any statement depth. Python makes such a name a local of
    # the enclosing scope from the scope's START, so sema needs the set
    # before it walks the body; a read of one before anything binds it is
    # CPython's UnboundLocalError.
    nested_def_bind_loc: dict[str, SourceLocation | None] = field(default_factory=dict)
    # Per local, the statements that are together its first binding
    # (`scan_first_bindings`), and the literal-seeded locals among them
    # (`scan_pending_num_locals`); both filled only on request.
    first_bindings: dict[str, tuple[TpyStmt, ...]] = field(default_factory=dict)
    literal_locals: LiteralLocals | None = None

    def bound_names(self) -> set[str]:
        """Every name the body binds, wherever it binds it: locals declared
        inside a branch or loop, `with`/`for` targets, walrus targets and
        nested-def names, plus every narrower-scoped binding
        (`scoped_bind_loc`: comprehension and genexpr loop vars, lambda
        params, `except ... as`, `match` arm captures). Params are NOT here
        (they are never `_declare`d); a caller that needs the whole binding
        set unions them in.

        A synthesized name must avoid ALL of these, not only the ones
        bound before it: a later binding at the same C++ scope redeclares,
        and one at an inner scope shadows."""
        return set(self.first_bind_loc) | set(self.scoped_bind_loc)


def _declare(name: str, loc: SourceLocation | None, declared: set[str],
             result: ScanResult) -> None:
    declared.add(name)
    result.first_bind_loc.setdefault(name, loc)


@dataclass(frozen=True)
class LiteralConstant:
    """A constant over numeric literals only (`15`, `-3`, `10 + 5`,
    `1 << 40`, `0.1 * 1`): a value with no width of its own. `value` is the
    folded integer, None when not folded (a float, or an operation the
    fold refuses)."""
    is_float: bool
    value: int | None


_CONST_OPS = frozenset(("+", "-", "*", "//", "%", "**", "<<", ">>", "&",
                        "|", "^", "div"))


def literal_constant(expr: TpyExpr | None) -> LiteralConstant | None:
    """`expr` as a constant over numeric literals only, else None -- the
    one test of a literal store, in the prescan and in sema alike."""
    if isinstance(expr, TpyIntLiteral):
        return LiteralConstant(False, expr.value)
    if isinstance(expr, TpyFloatLiteral):
        return LiteralConstant(True, None)
    if isinstance(expr, TpyUnaryOp) and expr.op in ("-", "+", "~"):
        inner = literal_constant(expr.operand)
        if inner is None or inner.value is None:
            return inner
        value = {"-": -inner.value, "+": inner.value, "~": ~inner.value}[expr.op]
        return LiteralConstant(False, value)
    if isinstance(expr, TpyBinOp) and expr.op in _CONST_OPS:
        left, right = literal_constant(expr.left), literal_constant(expr.right)
        if left is None or right is None:
            return None
        if left.is_float or right.is_float or expr.op == "div":
            return LiteralConstant(True, None)
        return LiteralConstant(False, fold_int_constant(expr.op, left.value, right.value))
    return None


# Past this many bits a constant is left to run time rather than folded:
# a chain of powers or shifts would otherwise exhaust memory at compile time.
_FOLD_MAX_BITS = 4096


def int_constant_too_wide(op: str, a: int | None, b: int | None) -> bool:
    """Whether `a op b` is left unfolded for its width alone: a known,
    valid operation whose value is past `_FOLD_MAX_BITS` (so an `int`)."""
    if a is None or b is None:
        return False
    if a.bit_length() > _FOLD_MAX_BITS or b.bit_length() > _FOLD_MAX_BITS:
        return True
    if op == "**" and b >= 0:
        return a.bit_length() * b > _FOLD_MAX_BITS
    if op == "<<" and b >= 0:
        return a.bit_length() + b > _FOLD_MAX_BITS
    return op == "*" and a.bit_length() + b.bit_length() > _FOLD_MAX_BITS


def fold_int_constant(op: str, a: int | None, b: int | None) -> int | None:
    """An integer constant `a op b` folded, None where it is not (unknown
    operands, a zero divisor, a negative power or shift, or a value wider
    than `_FOLD_MAX_BITS`). The one folder of the prescan and sema."""
    if a is None or b is None:
        return None
    if a.bit_length() > _FOLD_MAX_BITS or b.bit_length() > _FOLD_MAX_BITS:
        return None
    if op in ("//", "%") and b == 0:
        return None
    if op in ("**", "<<", ">>") and b < 0:
        return None
    if op == "**" and a.bit_length() * b > _FOLD_MAX_BITS:
        return None
    if op == "<<" and a.bit_length() + b > _FOLD_MAX_BITS:
        return None
    folders = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
               "//": lambda: a // b, "%": lambda: a % b, "**": lambda: a ** b,
               "<<": lambda: a << b, ">>": lambda: a >> b, "&": lambda: a & b,
               "|": lambda: a | b, "^": lambda: a ^ b}
    fold = folders.get(op)
    if fold is None:
        return None
    value = fold()
    return value if value.bit_length() <= _FOLD_MAX_BITS else None


def _nested_nonlocal_names(stmts: list[TpyStmt], out: set[str]) -> None:
    """Every name a nested def at any depth below `stmts` declares
    `nonlocal`: its stores are not this body's statements."""
    for s in stmts:
        if isinstance(s, TpyNestedDef):
            collect_nonlocals(s.func.body, out)
            continue
        for body in s.sub_bodies():
            _nested_nonlocal_names(body, out)


def collect_nonlocals(stmts: list[TpyStmt], out: set[str]) -> None:
    """Every name a `nonlocal` statement in `stmts` (nested defs included)
    names."""
    for s in stmts:
        if isinstance(s, TpyNonlocal):
            out.update(s.names)
        if isinstance(s, TpyNestedDef):
            collect_nonlocals(s.func.body, out)
            continue
        for body in s.sub_bodies():
            collect_nonlocals(body, out)


_INT32_MIN, _INT32_MAX = -2**31, 2**31 - 1


@dataclass(frozen=True)
class LiteralLocals:
    """The unannotated locals of a function body whose first binding is a
    bare literal, the pending ones among them (`pending`), and the integer
    literals each is assigned (`literals`)."""
    pending: frozenset[str] = frozenset()
    literals: dict[str, tuple[int, ...]] = field(default_factory=dict)
    # Names also bound through a form a pending local may not be (a loop
    # target, an unpack, a `nonlocal` store...): none of them gets a cell.
    excluded: frozenset[str] = frozenset()


def _store_value(s: TpyStmt) -> tuple[str, TpyExpr | None] | None:
    """The name and value of a plain unannotated store into a name."""
    if isinstance(s, TpyVarDecl) and s.type is None:
        return s.name, s.init
    if isinstance(s, TpyAssign) and isinstance(s.target, TpyName):
        return s.target.name, s.value
    return None


def scan_first_bindings(stmts: list[TpyStmt], params: Iterable[str] = (),
                        ) -> dict[str, tuple[TpyStmt, ...]]:
    """Per local of a function body, the statements that are together its
    first binding: the first one in source order, except that the sibling
    arms of one `if` / `elif` / `else` or `match`, and the `except`
    handlers and `else` of one `try`, each binding a name the statement
    did not see bound, are together its first binding. A `try` body binds
    before its handlers; a loop, `with` or `finally` body is read in source
    order; a nested def's body is its own scope. Parameters are bound on
    entry, so they have none."""

    def walk(body: list[TpyStmt], bound: set[str]) -> dict[str, list[TpyStmt]]:
        # The first bindings `body` makes of names not in `bound`, which it
        # extends with them.
        out: dict[str, list[TpyStmt]] = {}

        def note(name: str, s: TpyStmt) -> None:
            if name not in bound:
                bound.add(name)
                out[name] = [s]

        def sequential(sub: list[TpyStmt]) -> None:
            out.update(walk(sub, bound))

        def arms(bodies: list[list[TpyStmt]]) -> None:
            # Each arm starts from what the statement saw bound; the names
            # an arm binds are taken back out rather than copying the set
            # per arm, which would be quadratic in a long body.
            joined: dict[str, list[TpyStmt]] = {}
            for arm in bodies:
                sub = walk(arm, bound)
                bound.difference_update(sub)
                for name, sites in sub.items():
                    joined.setdefault(name, []).extend(sites)
            bound.update(joined)
            out.update(joined)

        for s in body:
            if not isinstance(s, TpyDelVar):
                for name in sorted(written_names(s)):
                    note(name, s)
            if isinstance(s, TpyNestedDef):
                continue
            if isinstance(s, TpyIf):
                arms([s.then_body, s.else_body])
            elif isinstance(s, TpyMatch):
                arms([case.body for case in s.cases])
            elif isinstance(s, TpyTry):
                sequential(s.try_body)
                arms([h.body for h in s.handlers] + [s.else_body])
                sequential(s.finally_body)
            else:
                for sub in s.sub_bodies():
                    sequential(sub)
        return out

    found = walk(stmts, set(params))
    return {name: tuple(sites) for name, sites in found.items()}


def scan_pending_num_locals(
    stmts: list[TpyStmt], params: Iterable[str],
    first: dict[str, tuple[TpyStmt, ...]],
) -> LiteralLocals:
    """The unannotated locals of a function body whose first binding
    (`first`, from `scan_first_bindings`) is a bare integer or float literal and some
    other statement assigns a value that is not one (or an integer literal
    no 32-bit default int holds). Their type is their literal family's
    default widened by what is stored in them, decided once the whole body
    has been analyzed. A local whose first binding is a value has that
    value's type instead, whatever is stored later. An augmented assignment
    stores the value of an operation, which is no literal whatever its
    operand: with a literal operand the value has the local's type, so it
    is no store that widens -- unless the literal is an integer no 32-bit
    default holds, which makes the value an `int`. A local every store of
    which is a literal the default holds is plainly the default type and is
    left out, as is a name also bound through a form whose stores are not
    plain assignments (a loop target, an unpack, a walrus, `with` /
    `except` / `match` bindings, a nested `def`, `nonlocal` / `global`),
    and a parameter."""
    literal: dict[str, list[int]] = {}
    widening: set[str] = set()
    excluded: set[str] = set(params)

    def store(name: str, value: TpyExpr | None, stmt: TpyStmt,
              aug: bool = False) -> None:
        excluded.update(ne.target for ne in walrus_bindings(stmt))
        lit = literal_constant(value)
        if lit is None:
            widening.add(name)
            return
        # An integer constant no 32-bit default holds (or one not folded
        # here) makes an `int`, stored as such or as an operation's operand.
        wide = (not lit.is_float
                and (lit.value is None
                     or not _INT32_MIN <= lit.value <= _INT32_MAX))
        if wide:
            widening.add(name)
        if not aug and not lit.is_float and lit.value is not None:
            literal.setdefault(name, []).append(lit.value)

    def on_stmt(s: TpyStmt) -> None:
        if isinstance(s, TpyVarDecl):
            if s.type is not None:
                excluded.add(s.name)
            elif s.init is not None:
                store(s.name, s.init, s)
        elif (isinstance(s, TpyAssign) and isinstance(s.target, TpyName)):
            store(s.target.name, s.value, s)
        elif (isinstance(s, TpyAugAssign) and isinstance(s.target, TpyName)):
            store(s.target.name, s.value, s, aug=True)
        elif isinstance(s, (TpyGlobal, TpyNonlocal)):
            excluded.update(s.names)
        elif not isinstance(s, TpyDelVar):
            excluded.update(written_names(s))

    walk_body_stmts(stmts, lambda e: None, on_stmt)
    _nested_nonlocal_names(stmts, excluded)
    # Seeded: some statement of the first binding stores a literal.
    seeded = {name for name, sites in first.items()
              if any((sv := _store_value(s)) is not None
                     and literal_constant(sv[1]) is not None for s in sites)}
    seeded -= excluded
    return LiteralLocals(
        frozenset(seeded & widening),
        {n: tuple(vs) for n, vs in literal.items() if n in seeded},
        frozenset(excluded))


def scan_reassigned_vars(stmts: list[TpyStmt],
                         pre_declared: set[str] | None = None, *,
                         pending_nums: bool = False) -> ScanResult:
    """Pre-scan a function body to find variables that are reassigned after first declaration.

    Args:
        stmts: The statements to scan.
        pre_declared: Names already in scope (e.g. function parameters).
            Assignment to these names counts as reassignment.

    Returns a ScanResult with:
    - reassigned: variables with a second TpyVarDecl or TpyAssign after first declaration
    - rvalue_reassigned: subset of reassigned with at least one rvalue reassignment
    - lvalue_reassigned: subset of reassigned with at least one lvalue reassignment
    - aug_assigned: variables targeted by augmented assignment (+=, -=, etc.)
    - first_bindings, literal_locals: with `pending_nums`,
      `scan_first_bindings` and `scan_pending_num_locals`
    """
    declared: set[str] = set(pre_declared) if pre_declared else set()
    result = ScanResult()
    if pending_nums:
        params = pre_declared or ()
        result.first_bindings = scan_first_bindings(stmts, params)
        result.literal_locals = scan_pending_num_locals(
            stmts, params, result.first_bindings)
    _scan_stmts(stmts, declared, result)
    result.initial_alias_names = set(result.alias_sources.keys())
    # Reassigned vars become T* pointers, not T& refs -- remove from alias map
    for name in result.reassigned:
        result.alias_sources.pop(name, None)
        result.chain_alias_sources.pop(name, None)
    return result


def liveness_alias_sources(result: ScanResult) -> dict[str, str]:
    """Alias map for last-use liveness: name-to-name aliases plus
    field/subscript-chain root aliases (both reference the source's storage,
    so both must suppress auto-move of the source while live).
    """
    if not result.chain_alias_sources:
        return result.alias_sources
    return {**result.chain_alias_sources, **result.alias_sources}


def _expr_to_narrowing_key(expr: TpyExpr) -> str | None:
    """Convert an expression to a narrowing key string.

    Returns a simple name for TpyName, or a dotted path for multi-level
    TpyFieldAccess (e.g. "obj.field", "obj.inner.field").
    Returns None for unsupported expressions.

    A `@property` read is deliberately NOT one: it is a getter CALL, and a
    call's result is not narrowable storage. Nothing kills the fact when the
    getter's backing changes (a global reassigned between the guard and the
    read keeps the key "proven"), and the reads under a narrow would still
    need the storage-form lift the positions do not carry. The spelled
    method twin is not narrowed either; see TODO.md for what narrowing a
    pure zero-arg getter would need.
    """
    if isinstance(expr, TpyName):
        return expr.name
    if isinstance(expr, TpyNamedExpr):
        return expr.target
    if isinstance(expr, TpyFieldAccess):
        obj_key = _expr_to_narrowing_key(expr.obj)
        if obj_key is not None:
            return f"{obj_key}.{expr.field}"
    return None


def storage_spelling(expr: TpyExpr) -> str | None:
    """How a diagnostic names a storage operand back to the user -- a name,
    a field path, or an accessor read spelled `h.i` and not as its getter
    call; None for anything else, whose source text the tree does not keep."""
    key = _expr_to_narrowing_key(expr)
    if key is not None:
        return key
    if is_property_getter_read(expr):
        obj_key = _expr_to_narrowing_key(expr.obj)
        if obj_key is not None:
            return f"{obj_key}.{expr.method}"
    return None


def int_literal_spelling(e: TpyExpr) -> str | None:
    """The source spelling of an int literal (`1`, `-1`) under any
    coercions; None for anything else, a folded `2 + 3` included."""
    e = peel_coerce(e)
    if isinstance(e, TpyIntLiteral):
        return str(e.value)
    if isinstance(e, TpyUnaryOp) and e.op == "-":
        operand = peel_coerce(e.operand)
        if isinstance(operand, TpyIntLiteral):
            return f"-{operand.value}"
    return None


# A deref-view narrowing rides `narrowed_types` under a distinct key: it
# narrows the polymorphic payload reached through an owning wrapper's
# reference-returning __deref__ (`if isinstance(rc, Dog): rc.bark()` resolves
# `bark` against Dog through the deref, while `rc.clone()` stays an Rc method).
# Keying it apart from the wrapper variable's own narrowing key leaves the
# wrapper type untouched and lets the existing save/restore/merge-at-joins
# machinery for narrowed_types apply unchanged -- exactly like field-path
# narrowing keys. The NUL suffix cannot collide with any source-level dotted
# path produced by _expr_to_narrowing_key.
#
# INVARIANT: a deref-view key is NOT a `name.`-dotted field path, so the
# field-path prefix sweeps (NarrowingTracker._invalidate_field_facts and
# friends) do NOT cover it. Any code that invalidates narrowed_types on a
# write/mutation of `name` must also drop deref_view_key(name) explicitly (see
# update_after_write); a prefix-scan alone silently leaves the deref-view fact
# stale -- the bug class that null-derefs a reassigned wrapper.
_DEREF_VIEW_SUFFIX = "\x00deref"


def deref_view_key(narrowing_key: str) -> str:
    """Map a receiver narrowing key to its deref-view narrowing key."""
    return narrowing_key + _DEREF_VIEW_SUFFIX


def parse_deref_view_key(key: str) -> 'str | None':
    """If `key` is a deref-view narrowing key, return the receiver key it wraps;
    otherwise None."""
    if key.endswith(_DEREF_VIEW_SUFFIX):
        return key[:-len(_DEREF_VIEW_SUFFIX)]
    return None


def match_is_none(expr: TpyExpr) -> tuple[str, bool] | None:
    """Match `v is None`, `v is not None`, `None is v`, `None is not v`.

    Also matches dotted field access at any depth: `obj.field is None`,
    `obj.a.b is None`, etc.
    Returns (key, is_not_none) or None if the pattern doesn't match.
    The key is a simple name or a dotted path ("obj.field", "obj.a.b").
    """
    if not isinstance(expr, TpyBinOp) or expr.op not in ("is", "is not"):
        return None
    key: str | None = None
    if isinstance(expr.right, TpyNoneLiteral):
        key = _expr_to_narrowing_key(expr.left)
    elif isinstance(expr.left, TpyNoneLiteral):
        key = _expr_to_narrowing_key(expr.right)
    if key is None:
        return None
    return key, expr.op == "is not"


def is_scan_rvalue(expr: TpyExpr | None) -> bool:
    """Conservative rvalue check for pre-scan (no type registry needed)."""
    if expr is None:
        return False
    if isinstance(expr, TpyCoerce):
        # The rule is on the node, so the untyped scan reads the same fact the
        # typed twin does: a fresh-value rule renders a prvalue whatever the
        # source was, every other rule passes its inner through.
        if expr.coercion.builds_fresh_value:
            return True
        return is_scan_rvalue(expr.expr)
    if isinstance(expr, (TpyName, TpySubscript)):
        return False
    if isinstance(expr, TpyFieldAccess):
        return is_scan_rvalue(expr.obj)
    if isinstance(expr, TpyNamedExpr):
        return is_scan_rvalue(expr.value)
    # These are the shapes the typed twin `value_category.is_rvalue_source`
    # admits too (the container/generator-shaped rvalues, comprehensions
    # included), so a comp-rebound name lands in `rvalue_reassigned` on both
    # sides. The twin then NARROWS a call/subscript/dunder result to an
    # lvalue when the callee returns `T&` -- a resolved-signature fact this
    # scan cannot see, so it answers True for the whole shape.
    return isinstance(expr, (TpyCall, TpyBinOp, TpyChainedCompare, TpyUnaryOp, TpyMethodCall,
                             TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                             TpyBoolLiteral, TpyNoneLiteral,
                             TpyTupleLiteral, TpyFString,
                             TpyIfExpr) + CONTAINER_LITERAL_NODES)


def _scan_walrus_in_expr(expr: TpyExpr | None, declared: set[str],
                         result: ScanResult) -> None:
    """Walk an expression tree to find walrus operator bindings.

    Uses generic dataclass field introspection so new expression types
    are handled automatically without manual enumeration.
    """
    if expr is None:
        return
    if isinstance(expr, TpyNamedExpr):
        if expr.target in declared:
            result.reassigned.add(expr.target)
            result.rvalue_reassigned.add(expr.target)
        else:
            _declare(expr.target, expr.loc, declared, result)
    if isinstance(expr, TpyLambda):
        # Lambda-scoped, like a comprehension var: it never displaces a body
        # binding, but a synthesized name must still avoid it (`bound_names`).
        for pname in expr.param_names:
            result.scoped_bind_loc.setdefault(pname, expr.loc)
    for f in dc_fields(expr):
        val = getattr(expr, f.name)
        if isinstance(val, TpyExpr):
            _scan_walrus_in_expr(val, declared, result)
        elif isinstance(val, TpyComprehensionGenerator):
            # Comprehension-scoped, so not a body declaration -- but still a
            # name a synthesized one must avoid (`bound_names`).
            for name in (val.unpack_vars or [val.var]):
                if name is not None:
                    result.scoped_bind_loc.setdefault(name, expr.loc)
            _scan_walrus_in_expr(val.iterable, declared, result)
            for cond in val.conditions:
                _scan_walrus_in_expr(cond, declared, result)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, TpyExpr):
                    _scan_walrus_in_expr(item, declared, result)
                elif isinstance(item, TpyFStringValue):
                    _scan_walrus_in_expr(item.expr, declared, result)
        elif isinstance(val, dict):
            for v in val.values():
                if isinstance(v, TpyExpr):
                    _scan_walrus_in_expr(v, declared, result)


def _scan_walrus_in_stmt(stmt: TpyStmt, declared: set[str],
                          result: ScanResult) -> None:
    """Scan expressions within a statement for walrus bindings."""
    for expr in stmt.exprs():
        _scan_walrus_in_expr(expr, declared, result)


def _scan_stmts(stmts: list[TpyStmt], declared: set[str],
                result: ScanResult) -> None:
    for stmt in stmts:
        # Scan for walrus bindings inside expressions before normal stmt scanning
        _scan_walrus_in_stmt(stmt, declared, result)
        if isinstance(stmt, TpyVarDecl):
            if stmt.name in declared:
                result.reassigned.add(stmt.name)
                if is_scan_rvalue(stmt.init):
                    result.rvalue_reassigned.add(stmt.name)
                else:
                    result.lvalue_reassigned.add(stmt.name)
            else:
                _declare(stmt.name, stmt.loc, declared, result)
                if (stmt.init is not None
                        and isinstance(stmt.init, TpyName)
                        and stmt.init.name != stmt.name):
                    result.alias_sources[stmt.name] = stmt.init.name
                elif stmt.init is not None:
                    root = chain_root_name(stmt.init)
                    if root is not None and root != stmt.name:
                        result.chain_alias_sources[stmt.name] = root
        elif isinstance(stmt, TpyAssign):
            if isinstance(stmt.target, TpyName) and stmt.target.name in declared:
                result.reassigned.add(stmt.target.name)
                if is_scan_rvalue(stmt.value):
                    result.rvalue_reassigned.add(stmt.target.name)
                else:
                    result.lvalue_reassigned.add(stmt.target.name)
        elif isinstance(stmt, TpyTupleUnpack):
            for name in stmt.targets:
                if name is None:
                    continue
                if name in declared:
                    result.reassigned.add(name)
                    # std::get<i>(tmp) is always an rvalue
                    result.rvalue_reassigned.add(name)
                else:
                    _declare(name, stmt.loc, declared, result)
        elif isinstance(stmt, TpyAugAssign):
            if isinstance(stmt.target, TpyName) and stmt.target.name in declared:
                result.aug_assigned.add(stmt.target.name)
        elif isinstance(stmt, TpyNestedDef):
            name = stmt.func.name
            result.nested_def_bind_loc.setdefault(name, stmt.loc)
            if name in declared:
                result.reassigned.add(name)
                result.rvalue_reassigned.add(name)
            else:
                _declare(name, stmt.loc, declared, result)
            # Do NOT recurse into nested body (separate scope)
        # Recurse into sub-bodies (if/while/for/with)
        if isinstance(stmt, TpyForEach):
            # A for-loop over an existing local REBINDS it (CPython: the
            # var holds the last element after the loop); a fresh var is a
            # declaration. Mirrors the with-as handling below.
            if stmt.var in declared:
                result.reassigned.add(stmt.var)
                result.rvalue_reassigned.add(stmt.var)
            else:
                _declare(stmt.var, stmt.loc, declared, result)
        if isinstance(stmt, TpyWith):
            for item in stmt.items:
                if item.target is not None:
                    if item.target in declared:
                        result.reassigned.add(item.target)
                        result.rvalue_reassigned.add(item.target)
                    else:
                        _declare(item.target, stmt.loc, declared, result)
        if isinstance(stmt, TpyTry):
            # Handler-scoped, like a comprehension var.
            for handler in stmt.handlers:
                if handler.binding is not None:
                    result.scoped_bind_loc.setdefault(handler.binding, handler.loc)
        if isinstance(stmt, TpyMatch):
            # Arm-scoped, like a handler binding: the capture is a real local
            # for the arm body only -- unless the name is already a local,
            # which the capture then rebinds, as a for-loop target does.
            for case in stmt.cases:
                for cap in iter_capture_bindings(case.pattern):
                    if cap.name in declared:
                        result.reassigned.add(cap.name)
                        result.rvalue_reassigned.add(cap.name)
                    else:
                        result.scoped_bind_loc.setdefault(cap.name, case.loc)
        for body in stmt.sub_bodies():
            _scan_stmts(body, declared, result)


def _object_leaf_names(e: TpyExpr | None, out: set[str]) -> None:
    """The names whose object `e` may evaluate to: a bare name, the arms of
    a ternary or `and` / `or`, both sides of a walrus, through a coercion,
    and the elements of a tuple literal (a tuple may hold its elements by
    reference). Anything else -- a call, a subscript, a list literal --
    yields a new object or an element, not one of these names' objects."""
    stack = [e] if e is not None else []
    while stack:
        node = stack.pop()
        if isinstance(node, TpyName):
            out.add(node.name)
        elif isinstance(node, TpyCoerce):
            stack.append(node.expr)
        elif isinstance(node, TpyNamedExpr):
            out.add(node.target)
            stack.append(node.value)
        elif isinstance(node, TpyIfExpr):
            stack.extend((node.then_expr, node.else_expr))
        elif isinstance(node, TpyBinOp) and node.op in ("&&", "||"):
            stack.extend((node.left, node.right))
        elif isinstance(node, TpyTupleLiteral):
            stack.extend(node.elements)


def _subject_captures(pattern: TpyPattern) -> list[str]:
    """The names a `match` arm binds to the WHOLE subject (`case x`,
    `case P() as x`, either side of an or-pattern)."""
    if isinstance(pattern, (TpyCapturePattern, TpyAsPattern)):
        return [pattern.name]
    if isinstance(pattern, TpyOrPattern):
        return [n for alt in pattern.patterns for n in _subject_captures(alt)]
    return []


@dataclass
class InPlaceWrites:
    """Which objects a body may write in place, by syntax alone and
    independent of statement order.

    `writes` maps each name to how a write OPERAND reaches it: None for a
    store (a subscript store, `+=` or `del` on an element, `+=` on the
    name), a method name for a call whose receiver may be the name (the
    reader resolves it against the name's type to learn whether it
    writes), "<arg>" for a call argument that may be the name (a callee can
    write what it receives by reference). An operand may be the name
    through `_object_leaf_names`, so `(a if c else b).append(x)` reaches
    both.

    `points_to` maps each name to the names whose objects it may hold:
    itself plus, closed to a fixpoint, the leaf names of every value ANY
    binding gives it (assignment, rebind, walrus, unpack, for / with
    target, whole-subject `match` capture, the locals of nested defs and
    lambdas). Two names may share an object when their sets meet, so a
    write through an alias bound anywhere -- before the view, after it,
    in a closure -- reaches the root."""
    writes: dict[str, set[str | None]] = field(default_factory=dict)
    points_to: dict[str, set[str]] = field(default_factory=dict)
    _cache: dict[str, frozenset[str | None]] = field(default_factory=dict)

    def writes_through(self, name: str) -> frozenset[str | None]:
        """How the body may write the object `name` holds: the writes of
        every name that may share it."""
        hit = self._cache.get(name)
        if hit is not None:
            return hit
        objs = self.points_to.get(name, {name})
        out: set[str | None] = set()
        for other, kinds in self.writes.items():
            if not objs.isdisjoint(self.points_to.get(other, (other,))):
                out |= kinds
        result = frozenset(out)
        self._cache[name] = result
        return result


def collect_in_place_writes(stmts: list[TpyStmt]) -> InPlaceWrites:
    """The pre-scan's write-through-alias relation (see `InPlaceWrites`).
    Nested defs and lambdas are walked too: a closure writes a captured
    object whenever it runs."""
    writes: dict[str, set[str | None]] = {}
    # target name -> leaf names of the values bound to it
    bound_from: dict[str, set[str]] = {}

    def write(operand: TpyExpr | None, kind: str | None) -> None:
        names: set[str] = set()
        _object_leaf_names(operand, names)
        for n in names:
            writes.setdefault(n, set()).add(kind)

    def bind(target: str | None, value: TpyExpr | None) -> None:
        if target is None or value is None:
            return
        names: set[str] = set()
        _object_leaf_names(value, names)
        names.discard(target)
        if names:
            bound_from.setdefault(target, set()).update(names)

    def bind_elements(targets: Iterable[str | None], iterable: TpyExpr) -> None:
        # Only a tuple literal's elements are the named objects themselves;
        # iterating a name yields its elements, never the name's object.
        if isinstance(iterable, TpyTupleLiteral):
            for t in targets:
                for el in iterable.elements:
                    bind(t, el)

    def on_expr(e: TpyExpr | None) -> None:
        stack = [e] if e is not None else []
        while stack:
            node = stack.pop()
            if isinstance(node, TpyNamedExpr):
                bind(node.target, node.value)
            if isinstance(node, TpyMethodCall):
                write(node.obj, node.method)
            if isinstance(node, (TpyCall, TpyMethodCall)):
                # The pre-scan knows no signatures, so every argument is a
                # candidate the reader weighs.
                for a in getattr(node, 'args', ()) or ():
                    write(a, "<arg>")
                for a in (getattr(node, 'kwargs', None) or {}).values():
                    write(a, "<arg>")
            if isinstance(node, TpyLambda):
                stack.append(node.body)
            gen = getattr(node, 'generator', None)
            if isinstance(gen, TpyComprehensionGenerator):
                bind_elements(gen.unpack_vars or [gen.var], gen.iterable)
            # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
            stack.extend(node.children())

    def on_store(t: TpyExpr) -> None:
        if isinstance(t, TpyName):
            write(t, None)
        elif isinstance(t, TpySubscript):
            write(t.obj, None)

    def walk(body: list[TpyStmt]) -> None:
        for s in body:
            if isinstance(s, TpyAssign):
                if isinstance(s.target, TpySubscript):
                    on_store(s.target)
                elif isinstance(s.target, TpyName):
                    bind(s.target.name, s.value)
            elif isinstance(s, TpyVarDecl):
                bind(s.name, s.init)
            elif isinstance(s, TpyTupleUnpack):
                for t in s.targets:
                    bind(t, s.value)
            elif isinstance(s, TpyAugAssign):
                on_store(s.target)
            elif isinstance(s, TpyDelItem):
                for t in s.targets:
                    on_store(t)
            elif isinstance(s, TpyForEach):
                bind_elements([s.var], s.iterable)
            elif isinstance(s, TpyWith):
                for item in s.items:
                    bind(item.target, item.context_expr)
            elif isinstance(s, TpyMatch):
                for case in s.cases:
                    for n in _subject_captures(case.pattern):
                        bind(n, s.subject)
            if isinstance(s, TpyNestedDef):
                walk(s.func.body)
                continue
            for e in s.exprs():
                on_expr(e)
            for b in s.sub_bodies():
                walk(b)

    walk(stmts)
    points_to: dict[str, set[str]] = {}
    if bound_from:
        feeds: dict[str, set[str]] = {}
        for target, sources in bound_from.items():
            for src in sources:
                feeds.setdefault(src, set()).add(target)
        names = set(bound_from) | set(feeds)
        points_to = {n: {n} for n in names}
        work = list(names)
        while work:
            src = work.pop()
            for target in feeds.get(src, ()):
                before = len(points_to[target])
                points_to[target] |= points_to[src]
                if len(points_to[target]) != before:
                    work.append(target)
    return InPlaceWrites(writes, points_to)


@dataclass
class FactKills:
    """Conservative syntactic over-approximation of the flow facts a body
    may invalidate (narrowing, ptr non-null, value ranges).

    Applied at control-flow meets where single-pass analysis would otherwise
    resurrect facts the body kills: loop body entry (back-edge), except
    handler entry (mid-try exception), finally entry (any-path exception).
    """
    # Names that may be rebound: kills the name's own facts, its deref-view
    # fact, all field facts rooted at it, and its value range.
    names: set[str] = field(default_factory=set)
    # Dotted field paths written directly (obj.field = ...): kills the path's
    # facts and all deeper paths, but not the root name's own facts.
    paths: set[str] = field(default_factory=set)
    # Names/paths whose *contents* may be mutated through a call (method
    # receivers, reference-type call args): kills field facts under the key
    # and len-derived ranges, but not the key's own narrowing.
    receivers: set[str] = field(default_factory=set)
    # The body contains a suspension point (yield / await / async for /
    # async with): re-entering it (back-edge) or leaving it mid-way
    # (handler/finally entry) crosses a suspension, so everything a
    # suspension kills (field paths, deref views, len ranges,
    # closure/global names) must die at the meet as well.
    suspends: bool = False

    def __bool__(self) -> bool:
        return bool(self.names or self.paths or self.receivers
                    or self.suspends)

    def update(self, other: 'FactKills') -> None:
        self.names |= other.names
        self.paths |= other.paths
        self.receivers |= other.receivers
        self.suspends |= other.suspends


def _kills_in_expr(expr: TpyExpr | None, kills: FactKills) -> None:
    if expr is None:
        return
    if isinstance(expr, TpyNamedExpr):
        kills.names.add(expr.target)
    elif isinstance(expr, TpyMethodCall):
        key = _expr_to_narrowing_key(expr.obj)
        if key is not None:
            kills.receivers.add(key)
    if isinstance(expr, (TpyCall, TpyMethodCall)):
        for arg in expr.args:
            if isinstance(arg, TpyName):
                kills.receivers.add(arg.name)
        for kw_val in getattr(expr, "kwargs", {}).values():
            if isinstance(kw_val, TpyName):
                kills.receivers.add(kw_val.name)
    for f in dc_fields(expr):
        val = getattr(expr, f.name)
        if isinstance(val, TpyExpr):
            _kills_in_expr(val, kills)
        elif isinstance(val, TpyComprehensionGenerator):
            _kills_in_expr(val.iterable, kills)
            for cond in val.conditions:
                _kills_in_expr(cond, kills)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, TpyExpr):
                    _kills_in_expr(item, kills)
                elif isinstance(item, TpyFStringValue):
                    _kills_in_expr(item.expr, kills)
        elif isinstance(val, dict):
            for v in val.values():
                if isinstance(v, TpyExpr):
                    _kills_in_expr(v, kills)


def _kills_assign_target(target: TpyExpr, kills: FactKills) -> None:
    if isinstance(target, TpyName):
        kills.names.add(target.name)
    elif isinstance(target, TpyFieldAccess):
        key = _expr_to_narrowing_key(target)
        if key is not None:
            kills.paths.add(key)
        else:
            root = _expr_root_name(target)
            if root is not None:
                kills.receivers.add(root)
    elif isinstance(target, TpySubscript):
        root = _expr_root_name(target.obj)
        if root is not None:
            kills.receivers.add(root)


def _expr_root_name(expr: TpyExpr) -> str | None:
    while isinstance(expr, TpyFieldAccess):
        expr = expr.obj
    if isinstance(expr, TpyName):
        return expr.name
    return None


def chain_root_name(expr: TpyExpr) -> str | None:
    """Root TpyName of a pure field/subscript chain (o.inner, xs[0],
    o.items[0].inner -> the root name), or None when the chain bottoms out
    in anything else (call results are handled by the borrow tracker, not
    the prescan alias map).

    A `@property` hop is a hop of this chain: the read is spelled like a
    member and names storage the root owns, so it is walked through like
    one. It is the ONE call shape that is -- a spelled zero-arg accessor
    returning a borrow is the same thing structurally and is not admitted
    here (see TODO.md); whether a hop's storage actually outlives the root
    is the caller's per-hop question, not this walker's.
    """
    while (isinstance(expr, (TpyFieldAccess, TpySubscript))
           or is_property_getter_read(expr)):
        expr = expr.obj
    return expr.name if isinstance(expr, TpyName) else None


def alias_group(aliases: dict[str, str], name: str) -> set[str]:
    """All names statically known to alias ``name`` (from the prescan
    alias map, alias -> source), including ``name`` itself.

    A mutation through any member reaches every member -- they are the
    same object -- so fact invalidation must cover the whole group.
    Rebinding a member does NOT affect the others (use only for
    mutation-driven kills, never for rebinds).
    """
    group = {name}
    cur = name
    while cur in aliases and aliases[cur] not in group:
        cur = aliases[cur]
        group.add(cur)
    changed = True
    while changed:
        changed = False
        for a, s in aliases.items():
            if s in group and a not in group:
                group.add(a)
                changed = True
    return group


def _collect_nested_def_writes(stmts: list[TpyStmt], kills: FactKills) -> None:
    """Names a nested def may rebind in the enclosing scope (nonlocal/global)."""
    for stmt in stmts:
        if isinstance(stmt, (TpyNonlocal, TpyGlobal)):
            kills.names.update(stmt.names)
        if isinstance(stmt, TpyNestedDef):
            _collect_nested_def_writes(stmt.func.body, kills)
        for body in stmt.sub_bodies():
            _collect_nested_def_writes(body, kills)


def collect_fact_kills(stmts: list[TpyStmt],
                       extra_exprs: 'tuple[TpyExpr, ...] | list[TpyExpr]' = ()
                       ) -> FactKills:
    """Collect the fact kill-set of a statement body (recursive).

    ``extra_exprs`` covers re-evaluated expressions that are part of the
    same control-flow cycle but not of the body (a while-loop condition).
    """
    kills = FactKills()
    _collect_fact_kills(stmts, kills)
    for e in extra_exprs:
        _kills_in_expr(e, kills)
    # Condition-position awaits are desugared to statement position before
    # sema, so extra_exprs (re-evaluated conditions) cannot suspend.
    kills.suspends = stmts_have_any_suspension(stmts)
    return kills


def bound_names_of(stmt: TpyStmt) -> set[str]:
    """Names this ONE statement binds by whole name -- no sub-body walk, no
    nested-def descent, no field / element targets (those write THROUGH a
    binding rather than reseating it).

    `del x` is a binding write: it releases the storage the name held, which
    invalidates an outstanding borrow exactly as a rebind does.
    """
    if isinstance(stmt, TpyVarDecl):
        return {stmt.name}
    if isinstance(stmt, (TpyAssign, TpyAugAssign)):
        return ({stmt.target.name} if isinstance(stmt.target, TpyName)
                else set())
    if isinstance(stmt, TpyTupleUnpack):
        return {n for n in stmt.targets if n is not None}
    if isinstance(stmt, TpyForEach):
        return {stmt.var}
    if isinstance(stmt, TpyWith):
        return {i.target for i in stmt.items if i.target is not None}
    if isinstance(stmt, TpyDelVar):
        return set(stmt.names)
    return set()


def scope_bound_names(body: list[TpyStmt],
                      params: Iterable[str] = ()) -> set[str]:
    """A function's own-scope bindings: `params` plus every name its body
    binds (`written_names`, lambda-body walruses included -- see
    `walrus_names_of`), minus the names it declares `nonlocal` / `global`."""
    out: set[str] = set(params)
    declared_outside: set[str] = set()

    def on_stmt(s: TpyStmt) -> None:
        out.update(written_names(s))
        out.update(walrus_names_of(s))
        if isinstance(s, (TpyGlobal, TpyNonlocal)):
            declared_outside.update(s.names)

    walk_body_stmts(body, lambda e: None, on_stmt)
    return out - declared_outside


def walrus_names_of(stmt: TpyStmt) -> set[str]:
    """Names this ONE statement binds through a walrus in its own expressions.

    A walrus is a whole-name binding of the enclosing scope exactly as an
    assignment is (in a comprehension too), but it sits inside an expression
    where `bound_names_of`'s target walk cannot reach it. Reads the same
    enumerator the fact-kill scan uses rather than walking expressions again.

    `_kills_in_expr`'s generic field walk descends into a LAMBDA body, so a
    walrus there counts as a binding of this statement's scope. That is wrong
    for Python -- the lambda is its own scope, which is why the parser's
    `written_names` / `walrus_bindings` stop at `TpyLambda.children() == []`
    -- but it matches what TPy currently EMITS for such a walrus under a
    `global` declaration (a write to the module slot,
    BUGS.md#lambda-body-walrus-binds-enclosing-scope), so counting it keeps
    the rebind facts consistent with the generated code. Swap this for
    `written_names` once the lambda-scope divergence is fixed.
    """
    kills = FactKills()
    for expr in stmt.exprs():
        _kills_in_expr(expr, kills)
    return kills.names


def _collect_fact_kills(stmts: list[TpyStmt], kills: FactKills) -> None:
    for stmt in stmts:
        kills.names.update(bound_names_of(stmt))
        if isinstance(stmt, (TpyAssign, TpyAugAssign)):
            _kills_assign_target(stmt.target, kills)
        elif isinstance(stmt, TpyDelAttr):
            for t in stmt.targets:
                key = _expr_to_narrowing_key(t)
                if key is not None:
                    kills.paths.add(key)
        elif isinstance(stmt, TpyDelItem):
            for t in stmt.targets:
                root = _expr_root_name(t.obj)
                if root is not None:
                    kills.receivers.add(root)
        elif isinstance(stmt, TpyGlobal):
            kills.names.update(stmt.names)
        elif isinstance(stmt, TpyNestedDef):
            # Calls anywhere in the cycle may invoke the closure; treat its
            # nonlocal/global targets as killable. Body otherwise not scanned
            # (separate scope).
            _collect_nested_def_writes(stmt.func.body, kills)
            continue
        for expr in stmt.exprs():
            _kills_in_expr(expr, kills)
        for body in stmt.sub_bodies():
            _collect_fact_kills(body, kills)


@dataclass(frozen=True)
class LoopBindings:
    """What one `for` / `while` binds and writes. `body`: every name a pass
    can bind -- its body and `else` clause (nested bodies included, nested
    defs not: their locals are the closure's) and a walrus in its own head.
    `target`: the for-each's own variable. Kept apart because readers differ
    on it: a loop over a container that stays put does not refill its
    variable's storage, while a pull from an iterator does. `stores`: the
    places a pass stores into -- a field, an element, an augmented or `del`
    target -- with `deletes` the `del` targets among them, and `effects`:
    every call, `await`, loop, `with` and augmented assignment it runs (its
    own head included); sema resolves what they write. `sites`: every
    statement and expression node it runs, for the calls sema records on
    them (`sema.receiver_calls.implicit_calls`)."""
    body: frozenset[str]
    target: frozenset[str]
    stores: tuple = field(default=(), compare=False)
    effects: tuple = field(default=(), compare=False)
    deletes: tuple = field(default=(), compare=False)
    sites: tuple = field(default=(), compare=False)


def _stmt_binds(s: TpyStmt) -> set[str]:
    """Every whole name one statement binds, handler and capture names
    included."""
    out = bound_names_of(s) | walrus_names_of(s)
    if isinstance(s, TpyTry):
        out.update(h.binding for h in s.handlers if h.binding is not None)
    elif isinstance(s, TpyMatch):
        for case in s.cases:
            out.update(cap.name for cap in iter_capture_bindings(case.pattern))
    return out


def _stmt_stores(s: TpyStmt) -> list[TpyExpr]:
    """The places (not whole names) one statement stores into."""
    if isinstance(s, (TpyAssign, TpyAugAssign)):
        targets: list[TpyExpr] = [s.target]
    elif isinstance(s, (TpyDelAttr, TpyDelItem)):
        targets = list(s.targets)
    else:
        return []
    return [t for t in targets if not isinstance(t, TpyName)]


_CALL_LIKE = (TpyCall, TpyMethodCall, TpyBinOp, TpyUnaryOp, TpyAwait)


def _expr_effects(e: TpyExpr | None, out: list, sites: list) -> None:
    """Every call-shaped node under `e` into `out` (an `await` runs a frame)
    and every node into `sites`, a lambda's body included (it runs whenever
    whoever it is handed to calls it)."""
    if e is None:
        return
    sites.append(e)
    if isinstance(e, _CALL_LIKE):
        out.append(e)
    if isinstance(e, TpyLambda):
        _expr_effects(e.body, out, sites)
        return
    # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
    for child in e.children():
        _expr_effects(child, out, sites)


def collect_loop_bindings(stmts: list[TpyStmt], table: 'IdentityMap') -> None:
    """Fill `table` with the `LoopBindings` of every loop in `stmts`, in one
    bottom-up walk, so a nested loop's names are collected once rather than
    once per enclosing loop."""
    _walk_writes(stmts, table)


def block_writes(stmts: list[TpyStmt]) -> LoopBindings:
    """What a statement list binds and writes, in the shape a loop's facts
    take (no loop variable)."""
    w = _walk_writes(stmts, IdentityMap())
    return LoopBindings(body=frozenset(w.body), target=frozenset(),
                        stores=tuple(w.stores), effects=tuple(w.effects),
                        deletes=tuple(w.deletes), sites=tuple(w.sites))


@dataclass
class _Writes:
    body: set[str] = field(default_factory=set)
    stores: list = field(default_factory=list)
    effects: list = field(default_factory=list)
    deletes: list = field(default_factory=list)
    sites: list = field(default_factory=list)

    def add(self, other: '_Writes') -> None:
        self.body |= other.body
        self.stores += other.stores
        self.effects += other.effects
        self.deletes += other.deletes
        self.sites += other.sites


def _walk_writes(ss: list[TpyStmt], table: 'IdentityMap') -> _Writes:
    """What `ss` binds, stores into, runs and deletes, filling `table` with
    the `LoopBindings` of every loop in it on the way."""
    out = _Writes()
    for s in ss:
        if isinstance(s, TpyNestedDef):
            continue
        inner = _Writes()
        for body in s.sub_bodies():
            inner.add(_walk_writes(body, table))
        own = _Writes(sites=[s])
        for e in s.exprs():
            _expr_effects(e, own.effects, own.sites)
        if isinstance(s, (TpyForEach, TpyWith, TpyAugAssign)):
            own.effects.append(s)
        own.stores = _stmt_stores(s)
        own.deletes = (own.stores
                       if isinstance(s, (TpyDelAttr, TpyDelItem)) else [])
        if isinstance(s, (TpyForEach, TpyWhile)):
            names = bound_names_of(s) if isinstance(s, TpyForEach) else set()
            table[s] = LoopBindings(
                body=frozenset(inner.body | walrus_names_of(s)),
                target=frozenset(names),
                stores=tuple(own.stores + inner.stores),
                effects=tuple(own.effects + inner.effects),
                deletes=tuple(own.deletes + inner.deletes),
                sites=tuple(own.sites + inner.sites))
        own.body = inner.body | _stmt_binds(s)
        own.add(inner)
        out.add(own)
    return out


def loop_bindings_of(table: 'IdentityMap', loop: TpyStmt) -> LoopBindings:
    """`loop`'s bindings, walking it (and every loop inside it) on first
    ask."""
    facts = table.get(loop)
    if facts is None:
        collect_loop_bindings([loop], table)
        facts = table[loop]
    return facts
