"""Pending numeric locals.

An unannotated function local whose first binding is a literal constant
(`scan_pending_num_locals`) has no width of its own while its
function is analyzed: it is its literal family's default type (the default
int, or `float`) widened by the values stored in it, and every use --
earlier ones included -- is compiled at the type decided once the whole
body has been analyzed. Nothing is analyzed twice. A read of such a local
is typed `PendingNumType`; an operation over one is typed by the ordinary
operator resolution at the types known so far and resolved when the
function settles; a
conversion whose ends are not known yet is a `TpyCoerce` placeholder filled
then, or spliced out when it turns out to be none.

A use that needs a concrete type on the spot (anything the forcing
chokepoint in `ExpressionAnalyzer` does not let a pending type reach)
SETTLES the local from the evidence so far and freezes it; a later wider
store is an error naming that use.

The sibling arms of one statement that each give a local its first
binding (`scan_first_bindings`) bind it together: a cell joins the values
all of them store, typed or literal, and a bare literal beside typed arms
must not decide the type by which arm is read first
(`check_arm_group`). Typed arms alone also get a cell, of the first
arm's value, so a narrower arm read first does not fix the type.

Per local there is one cell. The cells live on the context, not on the
function state: a settle inside an overload trial that is rolled back stays
settled, as the scope it publishes to does.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterator

from ..coercions import Coercion, CoercionContext
from ..parse import (
    TpyCoerce, TpyExpr, TpyIntLiteral, TpyName, TpyStmt, TpyUnaryOp,
    TpyVarDecl,
)
from ..parse.nodes import is_parse_node
from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, PendingNumType, OwnType,
    OptionalType, BIGINT, FLOAT, INT64, is_float_type,
    is_integer_type, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from ..type_def_registry import (
    is_fixed_int_type, is_big_int_type, is_float64_type, int_traits_of,
)
from .numeric_lattice import (join_numeric, smallest_type_holding,
                              widen_numeric_types)
from .type_join import (annotate_first_binding, operand_spelling,
                        python_type_name, wider_store_message)

if TYPE_CHECKING:
    from .context import SemanticContext
    from .compatibility import TypeCompatibility


# The conversion a placeholder carries until its function settles.
PENDING_NUM_COERCION = Coercion(
    name="pending_num",
    from_type=lambda t: True, to_type=lambda t: True,
)


class _NoCommonType:
    """The join of two integer types that have none (int32 and uint32)."""


NO_COMMON = _NoCommonType()


@dataclass(eq=False)
class PendingNumCell:
    """One pending local, of the integer or the float family. `derived`
    locals have no literal in their first binding: their type is the join
    of the values their first binding stores (itself pending, or the typed
    values of sibling arms), and a later store must fit it. The others
    start at their family's default type and join every store."""
    cid: int
    name: str
    first_decl: TpyVarDecl | None
    derived: bool = False
    is_float: bool = False
    # The sibling-arm stores that are together the local's first binding
    # (empty for one), and what each stored: (the literal's type when it is
    # a literal of the family's default, the value's type, the value, the
    # store).
    arm_sites: tuple[TpyStmt, ...] = ()
    arm_stores: list[tuple[TpyType | None, TpyType, TpyExpr | None,
                           TpyStmt]] = field(default_factory=list)
    # The declarations whose recorded types follow the settled one.
    decls: list[TpyVarDecl] = field(default_factory=list)
    decl_keys: list[tuple[int, str]] = field(default_factory=list)
    # The types of the stores that are not literals, for the annotation a
    # diagnostic suggests.
    typed: list[TpyType] = field(default_factory=list)
    evidence: list[tuple[TpyType, TpyStmt | TpyExpr]] = field(default_factory=list)
    # A store made after the cell settled, or into a derived cell after its
    # first: it must fit the settled type rather than widen it.
    must_fit: list[tuple[TpyType, TpyStmt | TpyExpr]] = field(default_factory=list)
    settled: TpyType | None = None
    # The use that settled the cell early: (node, what it is, the pending
    # local the use read when it is not this one).
    frozen_by: tuple[TpyExpr | TpyStmt | None, str, str | None] | None = None


@dataclass
class DeferredIntOp:
    """An operation or conversion over pending values, resolved once all of
    `types` have settled: `resolve` receives them concrete, in order."""
    node: TpyExpr | TpyStmt | None
    types: tuple[TpyType, ...]
    resolve: Callable[[tuple[TpyType, ...]], None]


def strip_int(t: TpyType | None) -> TpyType | None:
    """`t` without the qualifiers an integer value can carry at a read."""
    if t is None:
        return None
    t = unwrap_ref_type(t)
    if isinstance(t, OwnType):
        t = t.wrapped
    return unwrap_readonly(t)


def is_pending_num(t: TpyType | None) -> bool:
    return isinstance(strip_int(t), PendingNumType)


def is_numeric_slot(t: TpyType | None) -> bool:
    """A slot a pending number converts into once its function settles:
    an int or a float, or an optional one."""
    t = unwrap_send_sync(strip_int(t)) if t is not None else None
    if isinstance(t, OptionalType):
        t = unwrap_readonly(t.inner)
    return is_integer_type(t) or is_float_type(t)


def value_family(t: TpyType | None) -> bool | None:
    """The numeric family of a value a pending local can store or combine
    with: False for an integer, True for a float, None for anything else."""
    if isinstance(t, PendingNumType):
        return t.is_float
    if isinstance(t, IntLiteralType) or is_fixed_int_type(t) or is_big_int_type(t):
        return False
    if isinstance(t, FloatLiteralType) or is_float_type(t):
        return True
    return None


def is_top(t: TpyType | None) -> bool:
    """A type every value of its family widens into (`int`, `float`), which
    no store can widen further."""
    return t is not None and (is_big_int_type(t) or is_float64_type(t))


def join_int(a: TpyType | None, b: TpyType | None) -> TpyType | None | _NoCommonType:
    """The join of two concrete integer types by the slot relation; None is
    no evidence."""
    if a is NO_COMMON or b is NO_COMMON:
        return NO_COMMON
    if a is None:
        return b
    if b is None or a == b:
        return a
    widened = widen_numeric_types(a, b)
    return widened if widened is not None else NO_COMMON


def lub_int(types: list[TpyType | None | _NoCommonType],
            ) -> TpyType | None | _NoCommonType:
    """`join_numeric` over the evidence `types`, where None is no evidence:
    None when there is none, NO_COMMON when no member holds them all."""
    if any(t is NO_COMMON for t in types):
        return NO_COMMON
    ts = [t for t in types if isinstance(t, TpyType)]
    if not ts:
        return None
    joined = join_numeric(ts)
    return joined if joined is not None else NO_COMMON


def pending_join(a: TpyType, b: TpyType) -> TpyType | None:
    """The type of a numeric result over two operands of which at least one
    is pending, in the family of the wider: the wider of the two, which
    stays pending. A literal operand adapts and counts for nothing (the
    caller maps one no default holds to its type first). None when the
    concrete parts have no common type."""
    cells: set[int] = set()
    floor: TpyType | None | _NoCommonType = None
    is_float = any(value_family(t) for t in (a, b))
    for t in (a, b):
        if isinstance(t, PendingNumType):
            cells |= t.cells
            floor = join_int(floor, t.floor)
        elif isinstance(t, (IntLiteralType, FloatLiteralType)):
            continue
        else:
            floor = join_int(floor, t)
    if floor is NO_COMMON:
        return None
    return make_pending(cells, floor, is_float)


def make_pending(cells: set[int] | frozenset[int], floor: TpyType | None,
                 is_float: bool) -> TpyType | None:
    if is_top(floor) or not cells:
        return floor
    return PendingNumType(frozenset(cells), floor, is_float)


def smallest_signed_holding(t: TpyType) -> TpyType:
    """The narrowest type that holds both `t` (an unsigned type no signed
    default int holds) and negative literals."""
    tr = int_traits_of(t)
    if tr is not None and tr.bits < 64:
        return INT64
    return BIGINT


class PendingNums:
    """The pending locals of the function under analysis: their cells, the
    join, the settle, and the deferred resolution."""

    def __init__(self, ctx: SemanticContext, compat: TypeCompatibility) -> None:
        self.ctx = ctx
        self.compat = compat
        # What a forced read is used as, asked of the analyzers at the
        # moment of the force (`describe_use`).
        self.describe_use: Callable[[TpyExpr], str] = lambda _e: "this use"

    # ------------------------------------------------------------------
    # Sinks: the consumers a pending type may reach
    # ------------------------------------------------------------------

    @contextmanager
    def sink(self, node: TpyExpr | None) -> Iterator[None]:
        """Analyze `node` as a consumer that takes a pending type."""
        saved = self.ctx.pending_ok_node
        self.ctx.pending_ok_node = node
        try:
            yield
        finally:
            self.ctx.pending_ok_node = saved

    # ------------------------------------------------------------------
    # Cells
    # ------------------------------------------------------------------

    def is_pending_name(self, name: str) -> bool:
        return name in self.ctx.func.pending_num_names

    def is_excluded(self, name: str) -> bool:
        return name in self.ctx.func.pending_num_excluded

    def cell_for(self, name: str) -> PendingNumCell | None:
        cid = self.ctx.func.pending_cell_of.get(name)
        return self.ctx.pending_num_cells.get(cid) if cid is not None else None

    def arm_group(self, name: str) -> tuple[TpyStmt, ...]:
        """The sibling-arm stores that are together `name`'s first binding,
        when there are several and none of the name's bindings is an
        excluded form; else ()."""
        sites = self.ctx.func.first_bindings.get(name, ())
        return sites if len(sites) > 1 and not self.is_excluded(name) else ()

    def new_cell(self, name: str, decl: TpyVarDecl | None,
                 derived: bool, is_float: bool = False) -> PendingNumCell:
        self.ctx.pending_num_counter += 1
        cell = PendingNumCell(cid=self.ctx.pending_num_counter, name=name,
                              first_decl=decl, derived=derived,
                              is_float=is_float,
                              arm_sites=self.arm_group(name))
        self.ctx.pending_num_cells[cell.cid] = cell
        self.ctx.func.pending_cell_of[name] = cell.cid
        return cell

    def cell_type(self, cell: PendingNumCell) -> TpyType:
        """What a read of the cell's local is typed."""
        if cell.settled is not None:
            return cell.settled
        return PendingNumType(frozenset({cell.cid}), None, cell.is_float)

    def default_type(self, is_float: bool) -> TpyType:
        """The type a bare literal of the family counts as."""
        return FLOAT if is_float else self.ctx.default_int_type

    def literal_type(self, literal: TpyType, node: TpyExpr | None = None,
                     rebinding: bool = False) -> TpyType:
        """The type a bare literal counts as when stored: its family's
        default, or `int` for an integer the default int does not hold
        (warned, with `node`, as a first binding or a rebinding is)."""
        if isinstance(literal, FloatLiteralType):
            return FLOAT
        return self.ctx.default_int_for_literal(literal, warn_node=node,
                                                rebinding=rebinding)

    def literal_operand(self, t: TpyType) -> TpyType:
        """An operand of an operation over a pending one, as both its typing
        and its late resolution read it: a literal adapts to the other
        operand (so counts for nothing) unless it is an integer no default
        int holds, which is an `int` whatever the other side settles to --
        a settle that makes the literal fit must not change the operator."""
        if isinstance(t, IntLiteralType):
            wide = self.literal_type(t)
            return wide if is_big_int_type(wide) else t
        return t

    def add_literal_store(self, cell: PendingNumCell, literal: TpyType,
                          node: TpyExpr, site: TpyStmt) -> None:
        """Record a bare literal stored into the cell's local: it counts as
        its family's default type."""
        self._add(cell, self.literal_type(literal, node, bool(cell.evidence)),
                  site)

    def add_store(self, cell: PendingNumCell, value_type: TpyType,
                  node: TpyStmt | TpyExpr) -> None:
        """Record a store of a typed value into the cell's local: a numeric
        type of its family or a pending one."""
        value_type = strip_int(value_type)
        cell.typed.append(value_type)
        self._add(cell, value_type, node)

    def record_arm_store(self, cell: PendingNumCell, value_type: TpyType,
                         value: TpyExpr | None, site: TpyStmt) -> None:
        """Note what a store that is one of the cell's sibling-arm first
        bindings stores, for `check_arm_group`. A literal no default holds
        is an `int` value there, as in sequential code."""
        if not any(s is site for s in cell.arm_sites):
            return
        literal = isinstance(value_type, (IntLiteralType, FloatLiteralType))
        t = self.literal_type(value_type) if literal else value_type
        held = value_type if literal and not is_big_int_type(t) else None
        cell.arm_stores.append((held, t, value, site))

    def _add(self, cell: PendingNumCell, value_type: TpyType,
             node: TpyStmt | TpyExpr) -> None:
        if cell.settled is not None:
            if (isinstance(value_type, PendingNumType)
                    and not self.settled_all(value_type)):
                self.defer(node, (value_type,),
                           lambda ts: self._check_fits(cell, ts[0], node))
            else:
                self._check_fits(cell, value_type, node)
            return
        if (cell.derived and cell.evidence
                and not any(s is node for s in cell.arm_sites)):
            cell.must_fit.append((value_type, node))
        else:
            cell.evidence.append((value_type, node))

    def known_type(self, t: TpyType) -> TpyType:
        """`t` at the types its pending locals have so far; asks nothing
        to settle. Anything else as it is."""
        if not isinstance(t, PendingNumType):
            return t
        known = lub_int([t.floor] + [
            self.known_so_far(self.ctx.pending_num_cells[c]) for c in t.cells])
        return known if isinstance(known, TpyType) else self.default_type(t.is_float)

    def known_so_far(self, cell: PendingNumCell) -> TpyType:
        """The type the evidence recorded so far gives, the default int when
        there is none; asks nothing to settle."""
        if cell.settled is not None:
            return cell.settled
        t = self._value(cell, {}, set())
        return t if isinstance(t, TpyType) else self.default_type(cell.is_float)

    def _value(self, cell: PendingNumCell, cur: dict[int, TpyType | None],
               visiting: set[int]) -> TpyType | None | _NoCommonType:
        if cell.settled is not None:
            return cell.settled
        if cell.cid in cur:
            return cur[cell.cid]
        if cell.cid in visiting:
            return None
        visiting.add(cell.cid)
        t = lub_int([self._base(cell)] + self._values(cell, cur, visiting))
        visiting.discard(cell.cid)
        return t

    def _values(self, cell: PendingNumCell, cur: dict[int, TpyType | None],
                visiting: set[int]) -> list[TpyType | None]:
        """The types of the values stored in the cell. A value that is an
        operation over types with none in common counts for nothing here:
        its operator refuses it, with the message that names the operands."""
        out: list[TpyType | None] = []
        for et, _node in cell.evidence:
            v = self._eval(et, cur, visiting)
            out.append(v if isinstance(v, TpyType) else None)
        return out

    def _base(self, cell: PendingNumCell) -> TpyType | None:
        return None if cell.derived else self.default_type(cell.is_float)

    def _eval(self, t: TpyType, cur: dict[int, TpyType | None],
              visiting: set[int]) -> TpyType | None | _NoCommonType:
        if not isinstance(t, PendingNumType):
            return t
        return lub_int([t.floor] + [
            self._value(self.ctx.pending_num_cells[cid], cur, visiting)
            for cid in t.cells])

    # ------------------------------------------------------------------
    # Settle
    # ------------------------------------------------------------------

    def _reach(self, cids: set[int] | frozenset[int]) -> list[PendingNumCell]:
        """The unsettled cells `cids` depend on, themselves included."""
        seen: dict[int, PendingNumCell] = {}
        stack = list(cids)
        while stack:
            cid = stack.pop()
            cell = self.ctx.pending_num_cells.get(cid)
            if cid in seen or cell is None or cell.settled is not None:
                continue
            seen[cid] = cell
            for et, _node in (*cell.evidence, *cell.must_fit):
                if isinstance(et, PendingNumType):
                    stack.extend(et.cells)
        return list(seen.values())

    def settle(self, cids: set[int] | frozenset[int],
               use: TpyExpr | TpyStmt | None = None, what: str | None = None,
               via: str | None = None) -> None:
        """Decide the cells `cids` depend on from their evidence so far. With
        `use`, the settle is a use that needed the type on the spot, and a
        later wider store names it."""
        cells = self._reach(cids)
        if not cells:
            return
        # A fixed point: cells joined through one another's stores
        # (`i = j + 1`, `j = i`) go up together until nothing changes.
        cur: dict[int, TpyType | None] = {c.cid: None for c in cells}
        changed = True
        while changed:
            changed = False
            for cell in cells:
                values = self._values(cell, cur, set())
                t = lub_int([self._base(cell)] + values)
                if t is NO_COMMON:
                    # Arms that bind together are refused in their own words.
                    self.check_arm_group(cell, cur)
                    self._raise_no_common(cell, values)
                if t != cur[cell.cid]:
                    cur[cell.cid] = t
                    changed = True
        for cell in cells:
            self.check_arm_group(cell, cur)
        for cell in cells:
            t = cur[cell.cid]
            cell.settled = t if t is not None else self.default_type(cell.is_float)
            if use is not None:
                cell.frozen_by = (use, what or "a use that needs its type",
                                  via if via != cell.name else None)
        for cell in cells:
            for ft, node in cell.must_fit:
                self._check_fits(cell, self._eval(ft, {}, set()), node)
            cell.must_fit.clear()
        self._publish(cells)

    def _publish(self, cells: list[PendingNumCell]) -> None:
        """Make later reads see the settled type directly. Reads through a
        copy the publish does not reach resolve it lazily (`current`)."""
        func = self.ctx.func
        for cell in cells:
            if func.pending_cell_of.get(cell.name) != cell.cid:
                continue
            t = cell.settled
            if func.current_scope is not None:
                func.current_scope.set_existing(cell.name, t)
            if func.current_ns is not None:
                func.current_ns.update_variable_type_recursive(cell.name, t)

    def _check_fits(self, cell: PendingNumCell, t: TpyType | None | _NoCommonType,
                    node: TpyStmt | TpyExpr) -> None:
        """A store into a settled cell, or a later store into a derived one,
        must not widen it."""
        if not isinstance(t, TpyType) or isinstance(t, (IntLiteralType, FloatLiteralType)):
            return
        if isinstance(t, PendingNumType):
            t = self.concrete(t)
        if join_int(cell.settled, t) == cell.settled:
            return
        fix = annotate_first_binding(cell.name, t, self._first_value(cell))
        if cell.frozen_by is not None:
            use, what, via = cell.frozen_by
            line = getattr(getattr(use, "loc", None), "line", None)
            at = f" at line {line}" if line is not None else ""
            through = f"through '{via}', " if via is not None else ""
            raise self.ctx.error(wider_store_message(
                cell.name, cell.settled, t, fix, at=at,
                used_as=f"{through}{what}"), node)
        line = getattr(getattr(cell.first_decl, "loc", None), "line", None)
        at = f" (line {line})" if line is not None else ""
        raise self.ctx.error(
            wider_store_message(cell.name, cell.settled, t, fix, at=at), node)

    def check_arm_group(self, cell: PendingNumCell,
                        cur: dict[int, TpyType | None] | None = None) -> None:
        """The sibling arms that are together the local's first binding
        decide its type together, whichever is read first: their typed
        values join, and a bare literal beside them is refused unless its
        family's default widens into that join -- else the literal arm read
        first would make the local the default and the typed one would
        make it its own type. `cur` holds cell values a settle is deciding;
        a typed value not known yet leaves the verdict to a later call."""
        typed: list[tuple[TpyType, TpyStmt]] = []
        joined: TpyType | None = None
        for literal, t, _value, site in cell.arm_stores:
            if literal is not None:
                continue
            tv = self._eval(t, cur if cur is not None else {}, set())
            if not isinstance(tv, TpyType):
                return
            nxt = join_int(joined, tv)
            if not isinstance(nxt, TpyType):
                self._raise_arms_no_common(cell, typed, tv, site)
            typed.append((tv, site))
            joined = nxt
        literals = [(lit, v, site) for lit, _t, v, site in cell.arm_stores
                    if lit is not None]
        if joined is None or not literals:
            return
        if join_int(self.default_type(cell.is_float), joined) == joined:
            return
        lit, value, site = literals[0]
        wide = python_type_name(joined)
        # Converting the literal to the join only helps when it fits there;
        # else the annotation names a type that holds both.
        holder = smallest_type_holding(joined, [l for l, _v, _s in literals])
        write = (f"write {wide}({self._spelled_value(value)}), or "
                 if holder == joined else "")
        raise self.ctx.error(
            f"'{cell.name}' is {wide} in one arm (line {_line(typed[0][1])}) "
            f"and a bare literal in {_other_arm(cell)} (line {_line(site)}), so its type "
            f"depends on which arm is read first; {write}annotate "
            f"{cell.name}: {python_type_name(holder)} at line "
            f"{self._first_arm_line(cell)}", site)

    def _raise_arms_no_common(self, cell: PendingNumCell,
                              typed: list[tuple[TpyType, TpyStmt]],
                              t: TpyType, site: TpyStmt) -> None:
        """The first arm whose value no type holds together with the arms
        before it."""
        held = next(h for h, _s in typed
                    if not isinstance(join_int(h, t), TpyType))
        held_site = next(s for h, s in typed if h is held)
        unsigned = (t if not int_traits_of(t) or not int_traits_of(t).signed
                    else held)
        wide = python_type_name(smallest_signed_holding(unsigned))
        raise self.ctx.error(
            f"'{cell.name}' is {python_type_name(held)} in one arm (line "
            f"{_line(held_site)}) and {python_type_name(t)} in "
            f"{_other_arm(cell)} (line "
            f"{_line(site)}), which have no common type; annotate "
            f"{cell.name}: {wide} at line {self._first_arm_line(cell)}", site)

    @staticmethod
    def _first_arm_line(cell: PendingNumCell) -> int | None:
        lines = [ln for s in cell.arm_sites if (ln := _line(s)) is not None]
        return min(lines) if lines else None

    def _raise_no_common(self, cell: PendingNumCell,
                         values: list[TpyType | None]) -> None:
        """The first store whose value no type holds together with the
        stores before it."""
        prefix: list[TpyType | None | _NoCommonType] = [self._base(cell)]
        held = prefix[0]
        for (_et, node), t in zip(cell.evidence, values):
            prefix.append(t)
            joined = lub_int(prefix)
            if joined is NO_COMMON:
                break
            held = joined
        assert isinstance(held, TpyType) and isinstance(t, TpyType)
        # The unsigned side is the one no default int holds.
        unsigned = t if not int_traits_of(t) or not int_traits_of(t).signed else held
        wide = self._annotation_for(cell, unsigned)
        raise self.ctx.error(
            f"'{cell.name}' holds {python_type_name(held)} values and this "
            f"value is {python_type_name(t)}, which have no common type; "
            f"annotate its first binding: {cell.name}: "
            f"{python_type_name(wide)} = {self._first_value(cell)}",
            node)

    def _literals_fit(self, name: str, t: TpyType) -> bool:
        tr = int_traits_of(t)
        return tr is not None and all(
            tr.min_value <= v <= tr.max_value
            for v in self.ctx.func.int_literal_values.get(name, ()))

    def _annotation_for(self, cell: PendingNumCell, t: TpyType) -> TpyType:
        """The type to annotate a local with that holds its literals and a
        `t` value no default int holds: `t` itself when every literal fits
        it, else the narrowest signed type that holds both."""
        if self._literals_fit(cell.name, t):
            return t
        return smallest_signed_holding(t)

    def annotation_hint(self, name: str, expected: TpyType) -> str:
        """For a mismatch at a use of literal-seeded local `name` whose type
        is not `expected`: the annotation that makes it one, when every
        value stored in it fits."""
        if name not in self.ctx.func.int_literal_values:
            return ""
        e = strip_int(expected)
        if not self._literals_fit(name, e):
            return ""
        cell = self.cell_for(name)
        for t in (cell.typed if cell is not None else ()):
            t = self._eval(t, {}, set())
            if not isinstance(t, TpyType) or join_int(e, t) != e:
                return ""
        decl = self.ctx.func.var_decl_by_name.get(name)
        first = self._spelled_value(decl.init if decl is not None else None)
        return (f"; '{name}' takes its type from the values stored in it, "
                f"not from its uses: annotate its first binding: {name}: "
                f"{python_type_name(e)} = {first}")

    def _first_value(self, cell: PendingNumCell) -> str:
        return self._spelled_value(
            cell.first_decl.init if cell.first_decl is not None else None)

    @staticmethod
    def _spelled_value(init: TpyExpr | None) -> str:
        """A first binding's value as written, for the annotation a
        diagnostic suggests; `...` when it is longer than a name or a
        literal."""
        if (isinstance(init, TpyUnaryOp) and init.op == "-"
                and isinstance(init.operand, TpyIntLiteral)):
            return f"-{init.operand.value}"
        spelled = operand_spelling(init) if init is not None else None
        return spelled or "..."

    # ------------------------------------------------------------------
    # Reading pending types
    # ------------------------------------------------------------------

    def settled_all(self, t: PendingNumType) -> bool:
        return all(self.ctx.pending_num_cells[c].settled is not None
                   for c in t.cells)

    def current(self, t: TpyType | None) -> TpyType | None:
        """`t` with a pending number whose cells have all settled replaced
        by its concrete type; anything else as it is."""
        inner = strip_int(t)
        if isinstance(inner, PendingNumType) and self.settled_all(inner):
            return self.concrete(inner)
        return t

    def concrete(self, t: PendingNumType) -> TpyType:
        settled = []
        for cid in t.cells:
            cell = self.ctx.pending_num_cells[cid]
            assert cell.settled is not None, (
                f"pending local '{cell.name}' read before it settled")
            settled.append(cell.settled)
        out = lub_int([t.floor] + settled)
        # An operation over types with none in common is refused when its
        # operator is resolved, which runs before anything reads its type.
        assert isinstance(out, TpyType), f"no common type for {t}"
        return out

    def force(self, expr: TpyExpr | None, t: TpyType, what: str | None = None
              ) -> TpyType:
        """Settle what `t` (the type `expr` analyzed to) depends on: `expr`
        is a use that needs a concrete type now."""
        inner = strip_int(t)
        assert isinstance(inner, PendingNumType)
        if not self.settled_all(inner):
            via = expr.name if isinstance(expr, TpyName) else None
            if via is None:
                names = sorted(self.ctx.pending_num_cells[c].name
                               for c in inner.cells)
                via = names[0] if len(names) == 1 else None
            if what is None:
                what = (self.describe_use(expr) if expr is not None
                        else "a use that needs its type")
            self.settle(inner.cells, use=expr, what=what, via=via)
            self.resolve_ready()
        concrete = self.concrete(inner)
        if expr is not None:
            self.ctx.set_expr_type(expr, concrete)
        return concrete

    def settle_names(self, names: set[str], use: TpyExpr | TpyStmt,
                     what: str) -> None:
        """Settle the pending locals among `names`: a body analyzed in a
        state of its own reads them (a nested def, a lambda, a generator
        expression), so their type has to be known before it is."""
        cids = {cid for n, cid in self.ctx.func.pending_cell_of.items()
                if n in names
                and self.ctx.pending_num_cells[cid].settled is None}
        for cid in cids:
            self.settle({cid}, use=use, what=what)
        if cids:
            self.resolve_ready()

    # ------------------------------------------------------------------
    # Deferred resolution
    # ------------------------------------------------------------------

    def defer(self, node: TpyExpr | TpyStmt | None, types: tuple[TpyType, ...],
              resolve: Callable[[tuple[TpyType, ...]], None]) -> None:
        self.ctx.func.pending_num_deferred.append(
            DeferredIntOp(node, tuple(strip_int(t) for t in types), resolve))

    def _ready(self, op: DeferredIntOp) -> bool:
        return all(not isinstance(t, PendingNumType) or self.settled_all(t)
                   for t in op.types)

    def resolve_ready(self) -> None:
        """Resolve the deferred operations whose types have all settled.
        Not inside an overload trial: the operations are rolled back with
        it, and the next call outside (at the latest `settle_all`) resolves
        them once."""
        if self.ctx.trial_depth:
            return
        func = self.ctx.func
        while True:
            ready = [op for op in func.pending_num_deferred if self._ready(op)]
            if not ready:
                return
            func.pending_num_deferred = [
                op for op in func.pending_num_deferred if not self._ready(op)]
            for op in ready:
                op.resolve(tuple(self.concrete(t) if isinstance(t, PendingNumType)
                                 else t for t in op.types))

    def check_late_result(self, node: TpyExpr | TpyStmt, what: str,
                          resolved: TpyType, typed: TpyType) -> None:
        """An operation resolved at the settle must have the type it was
        typed with at analysis (an integer operation the wider of its
        operands, anything else what its operator returned then): its
        consumers already took that one.
        An explicit raise, so `-O` does not strip it."""
        want = self.concrete(typed) if isinstance(typed, PendingNumType) else typed
        got = strip_int(resolved)
        if got != want:
            line = getattr(getattr(node, "loc", None), "line", None)
            raise AssertionError(
                f"Internal error: {what} at line {line} resolved to {got} "
                f"once its operands settled, but was typed {want}")

    def coerce(self, expr: TpyExpr, actual: TpyType, expected: TpyType,
               context: str, coercion_ctx: CoercionContext | None,
               rebind_of: str | None = None) -> TpyExpr:
        """A conversion of `expr` whose two ends are known when the function
        settles: a placeholder filled then, or spliced out if it is none.
        `rebind_of` names the typed local a store rebinds, which a wider
        value may not widen."""
        node = TpyCoerce(expr=expr, actual_type=actual, expected_type=expected,
                         coercion=PENDING_NUM_COERCION,
                         context_kind=coercion_ctx, context_msg=context,
                         loc=expr.loc)
        self.ctx.set_expr_type(node, expected)

        def resolve(types: tuple[TpyType, ...]) -> None:
            a, e = types
            if rebind_of is not None:
                self.compat.deduction.refuse_wider_rebind(
                    rebind_of, e, a, expr, None, expr)
            node.actual_type = a
            node.expected_type = e
            self.ctx.expr_types[node] = e
            coercion = (None if a == e else self.compat.check_type_compatible(
                a, e, context, getattr(expr, "loc", None), source_expr=expr,
                coercion_ctx=coercion_ctx))
            if coercion is None:
                self.ctx.func.pending_num_splices.append(node)
            else:
                node.coercion = coercion

        self.defer(node, (actual, expected), resolve)
        return node

    def check(self, actual: TpyType, expected: TpyType, context: str,
              coercion_ctx: CoercionContext | None,
              source_expr: TpyExpr | None) -> None:
        """A compatibility check whose ends are known when the function
        settles."""
        def resolve(types: tuple[TpyType, ...]) -> None:
            a, e = types
            if a != e:
                self.compat.check_type_compatible(
                    a, e, context, getattr(source_expr, "loc", None),
                    source_expr=source_expr, coercion_ctx=coercion_ctx)
        self.defer(source_expr, (actual, expected), resolve)

    # ------------------------------------------------------------------
    # The end of the function
    # ------------------------------------------------------------------

    def settle_all(self, body: list[TpyStmt] | None) -> None:
        """Decide every cell of the function, resolve what waited on them,
        and rewrite every type and node that held a pending one."""
        func = self.ctx.func
        if not func.pending_cell_of:
            return
        self.settle(set(func.pending_cell_of.values()))
        # A cell settled early, inside its first arm, is judged here with
        # every arm seen.
        for cid in func.pending_cell_of.values():
            self.check_arm_group(self.ctx.pending_num_cells[cid])
        self.resolve_ready()
        # The expression types that held a pending leaf are rewritten with
        # the other pending leaves' (`pending_composite_exprs`).
        for cid in func.pending_cell_of.values():
            cell = self.ctx.pending_num_cells[cid]
            for decl in cell.decls:
                t = self.ctx.var_types.get(decl)
                if t is not None:
                    self.ctx.var_types[decl] = self.finalize(t)
            for key in cell.decl_keys:
                t = self.ctx.declared_var_types.get(key)
                if t is not None:
                    self.ctx.declared_var_types[key] = self.finalize(t)
        if func.pending_num_splices:
            _splice_out(body or [], func.pending_num_splices)
            func.pending_num_splices = []

    def assert_settled(self, body: list[TpyStmt] | None) -> None:
        """The invariant after a body's `resolve_all`: no pending number is
        left anywhere codegen reads a type (the frame hoist and the
        composite-expression finalization check their own tables), and no
        conversion placeholder is left unfilled in the body. An explicit
        raise, so `-O` does not strip it."""
        func = self.ctx.func
        if not func.pending_cell_of:
            return
        left = surviving_placeholder(body or [])
        if left is not None:
            raise AssertionError(
                f"Internal error: a conversion at line "
                f"{getattr(left.loc, 'line', None)} was left unresolved "
                f"after its function settled")
        tables: list[tuple[str, TpyType | None]] = []
        for cid in func.pending_cell_of.values():
            cell = self.ctx.pending_num_cells[cid]
            tables += [(f"var_types of '{cell.name}'",
                        self.ctx.var_types.get(d)) for d in cell.decls]
            tables += [(f"declared type of '{cell.name}'",
                        self.ctx.declared_var_types.get(k))
                       for k in cell.decl_keys]
        if func.current_ns is not None:
            tables += [(f"binding '{n}'", b.type)
                       for n, b in func.current_ns.all_bindings().items()]
        if func.current_scope is not None:
            tables += [(f"scope binding '{n}'", t)
                       for n, t in func.current_scope.bindings.items()]
        tables += [(f"loop variable '{n}'", e.var_type)
                   for n, e in func.pending_loop_vars.items()]
        for decls in func.pending_branch_decl_maps:
            tables += [(f"branch declaration '{n}'", t) for n, t in decls.items()]
        for what, t in tables:
            if isinstance(t, TpyType) and contains_pending_num(t):
                raise AssertionError(
                    f"Internal error: {what} still holds a pending number "
                    f"type after its function settled")
        if func.pending_num_deferred:
            raise AssertionError(
                "Internal error: integer operations left unresolved after "
                "their function settled")

    def finalize(self, t: TpyType) -> TpyType:
        """`t` with every pending numeric leaf replaced by its settled type."""
        if isinstance(t, PendingNumType):
            return self.concrete(t)
        return t.map_inner_types(self.finalize)

    def drop_cells(self) -> None:
        """Forget the function's cells once nothing refers to them."""
        for cid in self.ctx.func.pending_cell_of.values():
            self.ctx.pending_num_cells.pop(cid, None)
        self.ctx.func.pending_cell_of = {}


def _other_arm(cell: PendingNumCell) -> str:
    return "the other" if len(cell.arm_sites) == 2 else "another"


def _line(node: TpyStmt | TpyExpr | None) -> int | None:
    return getattr(getattr(node, "loc", None), "line", None)


def contains_pending_num(t: TpyType) -> bool:
    if isinstance(t, PendingNumType):
        return True
    return any(contains_pending_num(i) for i in t.inner_types())


def _rewrite_tree(body: list[TpyStmt],
                  replace: Callable[[object], object]) -> None:
    """Visit every parse node the body holds -- through fields, lists,
    tuples and dicts -- once, storing back `replace(v)` for every value; a
    tuple is rebuilt when an element changes."""
    seen: set[int] = set()
    stack: list[object] = []

    def fix(v: object) -> object:
        v = replace(v)
        if is_parse_node(v):
            stack.append(v)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                v[i] = fix(x)
        elif isinstance(v, tuple):
            items = tuple(fix(x) for x in v)
            if any(a is not b for a, b in zip(items, v)):
                v = items
        elif isinstance(v, dict):
            for k, x in v.items():
                v[k] = fix(x)
        return v

    fix(body)
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        for attr, v in list(vars(node).items()):
            u = fix(v)
            if u is not v:
                setattr(node, attr, u)


def _splice_out(body: list[TpyStmt], placeholders: list[TpyCoerce]) -> None:
    """Replace every placeholder that turned out to convert nothing by the
    expression it wraps, wherever the tree holds it, so the tree is the one
    an annotated local gives. One walk of the body for all of them: the
    placeholder does not know where its caller stored it."""
    ids = {id(p) for p in placeholders}
    found: set[int] = set()

    def unwrap(v: object) -> object:
        while id(v) in ids:
            found.add(id(v))
            v = v.expr  # type: ignore[attr-defined]
        return v

    _rewrite_tree(body, unwrap)
    for p in placeholders:
        if id(p) not in found:
            line = getattr(p.loc, "line", None)
            raise AssertionError(
                f"Internal error: a conversion placeholder at line {line} is "
                f"held where its function's body does not reach it")


def surviving_placeholder(body: list[TpyStmt]) -> TpyCoerce | None:
    """A conversion placeholder left in the body unfilled, if any."""
    left: list[TpyCoerce] = []

    def look(v: object) -> object:
        if isinstance(v, TpyCoerce) and v.coercion is PENDING_NUM_COERCION:
            left.append(v)
        return v

    _rewrite_tree(body, look)
    return left[0] if left else None
