"""Tests sema decides statically, and the arms they rule out.

A test whose value the analysis proves makes the arm it does not take an
impossible path: sema leaves that arm unanalyzed and empty (mypy's rule),
and the statement keeps its test as written. `decided_truth` is the one
decider; its leaves are the verdicts analysis already reached -- a folded
`isinstance`, `is None` on an operand that is never (or always) None, a
Literal or local-constant compare, membership or truth test with Python's
value semantics -- and `not` / `and` / `or` combine them. Value ranges
decide no arm.

A decided test whose evaluated part only reads (`folded_truth`) is its bool
literal; one that runs code stays, and runs as CPython runs it. A ternary
or an `and` / `or` value is decided only when its test folds.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING, Mapping

from ..parse import (
    TpyArrayLiteral, TpyBinOp, TpyBoolLiteral, TpyCall, TpyChainedCompare,
    TpyCoerce, TpyExpr, TpyFieldAccess, TpyFloatLiteral, TpyGlobal,
    TpyIfExpr, TpyIntLiteral,
    TpyName, TpyNestedDef, TpyNoneLiteral, TpyNonlocal, TpyPassStmt,
    TpySetLiteral, TpyStmt, TpyStrLiteral, TpyTupleLiteral, TpyUnaryOp,
    is_docstring,
)
from ..compilation_context import get_current_compiler
from ..parse import nodes as _nodes
from ..prescan import fact_key_root
from ..typesys import (
    FunctionInfo, LiteralType, LiteralValue, RecordInfo, TpyType,
    TypeRegistry, is_bodyless_binding, is_union_or_optional_type,
    none_identity_of, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from .read_binding import ReadKind, resolve_read
from .literal_utils import literal_value_from_expr
from .receiver_calls import implicit_calls

if TYPE_CHECKING:
    from .context import SemanticContext
    from .narrowing import NarrowingTracker

_NODES_MODULE = _nodes.__name__
_COMPARISONS = ("==", "!=", "<", ">", "<=", ">=")


# -- Literal verdicts (Python value semantics: `1 == True`) -----------------

def _same_value(a: LiteralValue, b: LiteralValue) -> bool:
    return a.value == b.value


def literal_compare(op: str, values: tuple[LiteralValue, ...],
                    val: LiteralValue) -> bool | None:
    """`x == val` / `x != val` for an `x` holding one of `values`: decided
    when the answer is the same for each."""
    hits = {_same_value(v, val) for v in values}
    if len(hits) != 1:
        return None
    eq = hits.pop()
    return eq if op == "==" else not eq


def literal_membership(op: str, values: tuple[LiteralValue, ...],
                       haystack: list[LiteralValue]) -> bool | None:
    """`x in (a, b, ...)` / `not in` for an `x` holding one of `values`."""
    hits = {any(_same_value(v, h) for h in haystack) for v in values}
    if len(hits) != 1:
        return None
    found = hits.pop()
    return found if op == "in" else not found


def literal_truth(values: tuple[LiteralValue, ...]) -> bool | None:
    hits = {bool(v.value) for v in values}
    return hits.pop() if len(hits) == 1 else None


ValuesOf = Callable[[TpyExpr], 'tuple[LiteralValue, ...] | None']


def literal_test(e: TpyBinOp, values_of: ValuesOf) -> bool | None:
    """A `==` / `!=` / `in` / `not in` leaf decided by the values a name
    operand may hold (`values_of`)."""
    if e.op in ("==", "!="):
        for var_side, lit_side in ((e.left, e.right), (e.right, e.left)):
            values = values_of(var_side)
            val = literal_value_from_expr(lit_side)
            if values is None or val is None:
                continue
            verdict = literal_compare(e.op, values, val)
            if verdict is not None:
                return verdict
        return None
    if e.op in ("in", "not in"):
        values = values_of(e.left)
        right = e.right.expr if isinstance(e.right, TpyCoerce) else e.right
        if values is None or not isinstance(
                right, (TpyTupleLiteral, TpyArrayLiteral, TpySetLiteral)):
            return None
        haystack = [literal_value_from_expr(x) for x in right.elements]
        if any(h is None for h in haystack):
            return None
        return literal_membership(e.op, values, haystack)  # type: ignore[arg-type]
    return None


def _flatten(e: TpyExpr, op: str) -> list[TpyExpr]:
    out: list[TpyExpr] = []
    while isinstance(e, TpyBinOp) and e.op == op:
        out.append(e.right)
        e = e.left
    out.append(e)
    out.reverse()
    return out


def literal_chain(e: TpyBinOp, values_of: ValuesOf) -> bool | None:
    """An `or` of builtin `==` tests covering every value a name may hold is
    True; an `and` requiring one name to equal two different values is
    False."""
    required: dict[str, list[LiteralValue]] = {}
    names: dict[str, TpyExpr] = {}
    for operand in _flatten(e, e.op):
        # A user `__eq__` may answer anything.
        if (not isinstance(operand, TpyBinOp) or operand.op != "=="
                or not is_pure(operand)):
            return None
        for var_side, lit_side in ((operand.left, operand.right),
                                   (operand.right, operand.left)):
            val = literal_value_from_expr(lit_side)
            if isinstance(var_side, TpyName) and val is not None:
                required.setdefault(var_side.name, []).append(val)
                names.setdefault(var_side.name, var_side)
                break
        else:
            return None
    if e.op == "||":
        for name, vals in required.items():
            values = values_of(names[name])
            if values is not None and all(
                    any(_same_value(v, c) for c in vals) for v in values):
                return True
        return None
    for vals in required.values():
        if any(not _same_value(vals[0], v) for v in vals[1:]):
            return False
    return None


def fact_values(facts: dict[str, TpyType]) -> ValuesOf:
    """`values_of` over a name -> LiteralType fact map."""
    def values_of(e: TpyExpr) -> tuple[LiteralValue, ...] | None:
        if not isinstance(e, TpyName):
            return None
        t = facts.get(e.name)
        return t.values if isinstance(t, LiteralType) else None
    return values_of


# -- The decider ---------------------------------------------------------------

def _killed(nt: NarrowingTracker, e: TpyExpr) -> bool:
    """A name the enclosing loop rebinds before its head runs again: its
    flow facts do not hold on every evaluation of the head."""
    kills = nt.ctx.func.head_kills
    return (bool(kills) and isinstance(e, TpyName)
            and fact_key_root(e.name) in kills)


def name_values(nt: NarrowingTracker, e: TpyExpr, *,
                constants: bool = True) -> tuple[LiteralValue, ...] | None:
    """The values a name operand may hold here: its Literal type's, or the
    one literal a local bound once and never written holds."""
    if not isinstance(e, TpyName) or _killed(nt, e):
        return None
    t = nt.ctx.get_expr_type(e)
    if t is not None:
        t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        if isinstance(t, LiteralType):
            return t.values
    const = nt.ctx.func.constant_locals.get(e.name) if constants else None
    if const is None or resolve_read(nt.ctx, e.name).kind is not ReadKind.LOCAL:
        # A comprehension's or a lambda's own binding of the name, or a
        # capture, is another variable.
        return None
    return (const,)


def _none_identity(nt: NarrowingTracker, e: TpyBinOp) -> bool | None:
    """`x is None` / `is not None` over an operand typed `None` (always
    None) or never None -- the operand types its analysis accepted: one
    never None is accepted only as a test."""
    for other, none_side in ((e.left, e.right), (e.right, e.left)):
        if not isinstance(none_side, TpyNoneLiteral):
            continue
        t = nt.ctx.get_expr_type(other)
        # The identity test reads the declared Optional / union of a name
        # flow narrowing proved non-None, as its analysis did.
        declared = nt.declared_type_for_expr(other)
        if is_union_or_optional_type(declared):
            t = declared
        is_none = none_identity_of(t) if t is not None else None
        if is_none is None:
            return None
        return is_none if e.op == "is" else not is_none
    return None


def decided_truth(nt: NarrowingTracker, e: TpyExpr,
                  ran: Callable[[TpyExpr], None] | None = None, *,
                  ranges: bool = False,
                  constants: bool = True) -> bool | None:
    """The value analyzed test `e` always has, or None when it varies.
    `ran(operand)` is told each operand as it runs (an operand may write
    what a later one reads). `ranges=True` also reads the integer value
    ranges as they stand: only the `while` first-iteration check does,
    since a range can outlive a write the analysis does not see
    (BUGS.md#range-facts-survive-unseen-writes) and an arm decided on one
    would drop live code. `constants=False` reads no local's
    bound-once literal, only Literal types."""
    def sub(x: TpyExpr,
            ran_: Callable[[TpyExpr], None] | None = ran) -> bool | None:
        return decided_truth(nt, x, ran_, ranges=ranges, constants=constants)

    def values_of(x: TpyExpr) -> tuple[LiteralValue, ...] | None:
        return name_values(nt, x, constants=constants)

    if isinstance(e, TpyBoolLiteral):
        return e.value
    if isinstance(e, TpyCall):
        if isinstance(e.macro_expansion, TpyBoolLiteral):
            return e.macro_expansion.value
        return e.isinstance_fold
    if isinstance(e, TpyUnaryOp) and e.op == "!":
        inner = sub(e.operand)
        return None if inner is None else not inner
    if isinstance(e, TpyBinOp) and e.op in ("&&", "||"):
        short = e.op == "||"
        left = sub(e.left)
        if left is short:
            return left
        if ran is not None:
            ran(e.left)
        right = sub(e.right)
        if right is short:
            return right
        if left is not None and right is not None:
            return left
        if left is None and right is None:
            return literal_chain(e, values_of)
        return None
    if isinstance(e, TpyChainedCompare) and e.pairs:
        truths: list[bool | None] = []
        for pair in e.pairs:
            truths.append(sub(pair, None))
            if truths[-1] is False:
                return False
            if ran is not None:
                ran(pair)
        return True if all(t is True for t in truths) else None
    if ran is not None:
        ran(e)
    if isinstance(e, TpyBinOp):
        if e.op in ("is", "is not"):
            return _none_identity(nt, e)
        verdict = literal_test(e, values_of)
        if verdict is not None:
            return verdict
        if e.op in _COMPARISONS and ranges:
            return nt.comparison_truth(e)
        return None
    values = values_of(e)
    return literal_truth(values) if values is not None else None


# -- Folding -----------------------------------------------------------------

def narrows_past_literal(facts: Mapping[str, TpyType]) -> bool:
    """Whether a test's path facts narrow a name past a Literal fact: the
    test's own extraction binds the narrowed name there, so it stays."""
    return any(not isinstance(t, LiteralType) for t in facts.values())


def truthy_operands(expr: TpyExpr,
                    selects: list[TpyExpr] | None = None, *,
                    arms: bool = True) -> list[TpyExpr]:
    """The leaf expressions a truthiness context actually tests.

    `not x`, the `and` / `or` operands and a ternary's two arms distribute
    the test to their own operands (the ternary's own test is a condition
    apart); everything else IS the operand, whatever its node kind. The
    distributing `and` / `or` / ternary nodes -- each one a truth test
    itself, whose value is never used -- are appended to `selects` when
    given. `arms=False` stops at a ternary: its arms are values, which the
    test reads through the ternary, so a fold never reaches into them.
    """
    if isinstance(expr, TpyUnaryOp) and expr.op == "!":
        return truthy_operands(expr.operand, selects, arms=arms)
    if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
        halves = (expr.left, expr.right)
    elif isinstance(expr, TpyIfExpr) and arms:
        halves = (expr.then_expr, expr.else_expr)
    else:
        return [expr]
    if selects is not None:
        selects.append(expr)
    return (truthy_operands(halves[0], selects, arms=arms)
            + truthy_operands(halves[1], selects, arms=arms))


def _runs_user_code(fi: FunctionInfo | None) -> bool:
    return fi is None or not is_bodyless_binding(fi)


def is_pure(e: TpyExpr) -> bool:
    """Whether evaluating value `e` only reads: a literal, a name, a plain
    field read of one (a property is a call), or a decided-leaf kind over
    those -- an `isinstance`, an identity, compare or literal membership
    test sema resolved to no user dunder. A field read sema guards with a
    None check (`none_use`) can raise, and one through a `__deref__` chain
    (`deref_depth`) calls user code, so neither is a pure read."""
    if isinstance(e, (TpyBoolLiteral, TpyNoneLiteral, TpyStrLiteral,
                      TpyIntLiteral, TpyFloatLiteral, TpyName)):
        return True
    if isinstance(e, TpyCoerce):
        return is_pure(e.expr)
    if isinstance(e, TpyFieldAccess):
        return (e.none_use is None and e.deref_depth == 0
                and e.resolved_property_getter is None
                and e.dyn_getattr_call is None and is_pure(e.obj))
    if isinstance(e, TpyCall):
        if isinstance(e.macro_expansion, TpyBoolLiteral):
            return True
        # An isinstance test, folded or not, reads its subject only.
        return e.isinstance_type is not None and is_pure(e.args[0])
    if isinstance(e, TpyChainedCompare):
        return bool(e.pairs) and all(is_pure(p) for p in e.pairs)
    if not isinstance(e, TpyBinOp):
        return False
    if any(_runs_user_code(c.fi) for c in implicit_calls(e)):
        return False
    if e.op in ("in", "not in"):
        right = e.right.expr if isinstance(e.right, TpyCoerce) else e.right
        if not isinstance(right, (TpyTupleLiteral, TpyArrayLiteral,
                                  TpySetLiteral)):
            return False
        return (is_pure(e.left)
                and all(is_pure(x) for x in right.elements)
                and not (e.resolved_contains is not None
                         and _runs_user_code(e.resolved_contains)))
    if e.op in ("is", "is not"):
        return is_pure(e.left) and is_pure(e.right)
    if e.op in _COMPARISONS:
        return (is_pure(e.left) and is_pure(e.right)
                and not (e.resolved_binop is not None
                         and _runs_user_code(e.resolved_binop.method)))
    return False


def _tests_pure(nt: NarrowingTracker, e: TpyExpr, constants: bool) -> bool:
    """Whether the part of truth test `e` that runs only reads: every
    operand it tests is a bool literal, a decided name or a pure leaf
    (`is_pure`), and `and` / `or` reach their right operand only when the
    left one passes the test on. An undecided name is a truth test that may
    run code."""
    while isinstance(e, TpyCoerce):
        e = e.expr
    if isinstance(e, TpyBoolLiteral):
        return True
    if isinstance(e, TpyUnaryOp):
        return e.op == "!" and _tests_pure(nt, e.operand, constants)
    if isinstance(e, TpyBinOp) and e.op in ("&&", "||"):
        if not _tests_pure(nt, e.left, constants):
            return False
        left = decided_truth(nt, e.left, constants=constants)
        return (left is (e.op == "||")
                or _tests_pure(nt, e.right, constants))
    if isinstance(e, TpyName):
        return name_values(nt, e, constants=constants) is not None
    return (isinstance(e, (TpyCall, TpyBinOp, TpyChainedCompare))
            and is_pure(e))


def folded_truth(nt: NarrowingTracker, e: TpyExpr, *,
                 constants: bool = True) -> bool | None:
    """The bool literal analyzed test `e` folds to: its decided value when
    the part of it that runs only reads (`_tests_pure`), else None -- a
    test that runs code stays as written, whatever its value."""
    taken = decided_truth(nt, e, constants=constants)
    if taken is None or not _tests_pure(nt, e, constants):
        return None
    return taken


# -- Disjoint classes ----------------------------------------------------------

class Relation(Enum):
    """How a checked class stands to a subject's static class."""
    INSTANCE = "instance"      # every value of the subject is one
    DESCENDANT = "descendant"  # it derives the subject's class
    UNRELATED = "unrelated"    # neither derives the other


def class_relation(reg: TypeRegistry, subject: TpyType,
                   checked: TpyType) -> Relation:
    """The one answer an `isinstance` fold, a union non-member and a class
    pattern give: an UNRELATED check is False unless some class of the
    program derives both (`queue_disjoint_check`)."""
    if reg.is_subclass_of_or_equal(subject, checked):
        return Relation.INSTANCE
    if reg.is_subclass_of(checked, subject):
        return Relation.DESCENDANT
    return Relation.UNRELATED


@dataclasses.dataclass(frozen=True)
class DisjointCheck:
    """A test answered False for an UNRELATED pair, to warn on once every
    module's classes are known."""
    ctx: SemanticContext
    subject: TpyType
    checked: TpyType
    site: object
    pattern: bool


def queue_disjoint_check(ctx: SemanticContext, subject: TpyType,
                         checked: TpyType, site: object, *,
                         pattern: bool = False) -> None:
    """Note a False answer for an UNRELATED pair: a class deriving both,
    which only the whole program shows, lets CPython take the arm. A
    trial analysis notes nothing."""
    compiler = get_current_compiler()
    if compiler is not None and not ctx.trial_depth:
        compiler.pending_disjoint_checks.append(
            DisjointCheck(ctx, subject, checked, site, pattern))


def resolve_disjoint_checks(pending: list[DisjointCheck],
                            registries: list[TypeRegistry]) -> None:
    """Warn on each noted pair some class of the program derives both of,
    from one index over every module's classes (`registries`) -- the same
    answer whatever the module order."""
    if not pending:
        return
    index: dict[tuple[str, str], RecordInfo] = {}
    seen: set[str] = set()
    for reg in registries:
        for rec in list(reg.records.values()):
            qname = rec.qualified_name() or rec.name
            if qname in seen:
                continue
            seen.add(qname)
            lineage = [qname] + [anc.qualified_name() or anc.name
                                 for anc in reg.iter_ancestor_records(rec)]
            for x in lineage:
                for y in lineage:
                    if x != y:
                        index.setdefault((x, y), rec)
    for check in pending:
        reg = check.ctx.registry
        ra = reg.get_record_for_type(check.subject)
        rb = reg.get_record_for_type(check.checked)
        if ra is None or rb is None:
            continue
        both = index.get((ra.qualified_name() or ra.name,
                          rb.qualified_name() or rb.name))
        if both is None:
            continue
        what = (f"class pattern '{check.checked}' on '{check.subject}' never "
                f"matches" if check.pattern else
                f"isinstance() check against '{check.checked}' on "
                f"'{check.subject}' folds to False")
        check.ctx.warning(
            f"{what} (static dispatch; use @dynamic for runtime type "
            f"checks): '{both.name}' derives both", check.site)
    pending.clear()


# -- Declarations, out of every arm -------------------------------------------

def hoist_scope_declarations(body: list[TpyStmt]) -> None:
    """Move every `global` / `nonlocal` statement of `body`'s own scope (a
    nested def's body is its own) to the front of `body`, behind a leading
    docstring (it stays the `__doc__`), in source order, each keeping its
    own location. They declare for the whole function
    whether or not they run, and the lowering reads them off the statement
    lists, so emptying or splicing a ruled-out block must not take one
    along. A block left empty keeps a `pass`: an empty `finally` reads as
    none written."""
    found: list[TpyStmt] = []
    # Asked before the move: a string after a leading `global` is no docstring.
    at = 1 if body and is_docstring(body[0]) else 0

    def visit(stmts: list[TpyStmt], nested: bool) -> None:
        kept: list[TpyStmt] = []
        for s in stmts:
            if isinstance(s, (TpyGlobal, TpyNonlocal)):
                found.append(s)
                continue
            kept.append(s)
            if not isinstance(s, TpyNestedDef):
                for sub in s.sub_bodies():
                    visit(sub, True)
        if len(kept) != len(stmts):
            stmts[:] = (kept or [TpyPassStmt(loc=stmts[0].loc)]
                        if nested else kept)

    visit(body, False)
    found.sort(key=lambda s: (s.loc.line, s.loc.column) if s.loc else (0, 0))
    body[at:at] = found


# -- Rewriting a decided node's parent slot ------------------------------------

def replace_child(parent: object, old: TpyExpr, new: TpyExpr) -> bool:
    """Put `new` in every slot of `parent` holding `old`, looking through the
    containers and non-statement nodes `parent` holds (a coercion built
    around `old` after it was analyzed, a comprehension clause, a `with`
    item) but not into nested statements. Returns whether one was found."""
    seen: set[int] = set()
    found = False

    def visit(node: object) -> None:
        nonlocal found
        if id(node) in seen:
            return
        seen.add(id(node))
        for f in dataclasses.fields(node):  # type: ignore[arg-type]
            v = getattr(node, f.name)
            if v is old:
                setattr(node, f.name, new)
                found = True
            else:
                walk(v)

    def walk(v: object) -> None:
        nonlocal found
        if isinstance(v, list):
            for i, item in enumerate(v):
                if item is old:
                    v[i] = new
                    found = True
                else:
                    walk(item)
        elif isinstance(v, dict):
            for k, item in v.items():
                if item is old:
                    v[k] = new
                    found = True
                else:
                    walk(item)
        elif (dataclasses.is_dataclass(v) and not isinstance(v, type)
              and not isinstance(v, TpyStmt)
              and type(v).__module__ == _NODES_MODULE):
            visit(v)

    visit(parent)
    return found
