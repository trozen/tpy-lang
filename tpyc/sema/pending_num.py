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
from typing import TYPE_CHECKING, Callable, Iterator, Sequence

from ..coercions import Coercion, CoercionContext
from ..parse import (
    TpyArrayLiteral, TpyCall, TpyCoerce, TpyExpr, TpyIntLiteral,
    TpyListRepeat, TpyMethodCall, TpyName, TpyStmt, TpySubscript,
    TpyTupleLiteral, TpyUnaryOp, TpyVarDecl,
)
from ..parse.nodes import is_parse_node
from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, PendingNumType, OwnType,
    OptionalType, PendingListType, TupleType, UnionType, UnknownElementType,
    ListLiteralInfo, TypeParamRef, BIGINT, FLOAT, INT64, is_float_type,
    make_list, contains_pending_leaf, contains_pending_num,
    is_integer_type, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from ..type_def_registry import (
    is_fixed_int_type, is_big_int_type, is_float64_type, int_traits_of,
    is_array, is_list, is_span,
)
from .numeric_lattice import (join_numeric, smallest_type_holding,
                              widen_numeric_types)
from .type_join import (annotate_first_binding, operand_spelling,
                        python_type_name, usage_mix_message,
                        wider_store_message)

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
    # The list literal whose element this cell decides a leaf of (its id);
    # `name` is then the list's variable, and the cell is no local's.
    list_literal: int | None = None
    # Where in that element the leaf is (`ListCells`): () the element
    # itself, an int a tuple member, `ELEM_ROW` a nested list's element.
    path: tuple = ()
    # The element of a list whose first binding holds typed values: the
    # join of those values, decided there (`PendingNums.decide_at_birth`),
    # with no default type to start from and no later widening -- as a
    # local first bound to a typed value has that value's type.
    no_base: bool = False
    # The typed container that decided a list's element: (its type as
    # the source spells it, the node, what the list is there).
    context: tuple[TpyType, TpyExpr | TpyStmt | None, str] | None = None
    # The number literals stored into the list after its first binding, as
    # written: with the initializer's, what an annotation must hold.
    stored_literals: list[TpyExpr] = field(default_factory=list)
    # What the evidence joined to (`PendingNums.known_so_far`), and the
    # epoch that answer is good for.
    known: TpyType | None = None
    known_at: int = -1


# The step of a leaf path into a nested list's element.
ELEM_ROW = "row"


@dataclass(eq=False)
class ListCells:
    """The element of a list literal whose numeric leaves cells decide:
    `info` the record the list's type names, `tree` its element type, whose
    numeric leaves are the `PendingNumType`s naming `cells` (in path
    order), every other part the type the literal holds there. A list of
    scalars has one leaf, the element itself; a list of tuples one per
    numeric member; a nested list one per depth, shared by every row."""
    info: ListLiteralInfo
    tree: TpyType
    cells: tuple[PendingNumCell, ...]
    # Each cell's path in `tree` (a row's own element is its leaf's root,
    # while the cell's `path` is from the list first bound).
    paths: tuple[tuple, ...] = ()

    @property
    def settled(self) -> bool:
        return all(c.settled is not None for c in self.cells)

    @property
    def cids(self) -> frozenset[int]:
        return frozenset(c.cid for c in self.cells)

    @property
    def scalar(self) -> PendingNumCell | None:
        """The one cell of a list of scalar numbers."""
        return self.cells[0] if isinstance(self.tree, PendingNumType) else None


def tree_leaves(tree: TpyType, path: tuple = ()
                ) -> list[tuple[tuple, PendingNumType]]:
    """The pending numeric leaves of element tree `tree`, with their paths,
    in path order."""
    if isinstance(tree, PendingNumType):
        return [(path, tree)]
    if isinstance(tree, TupleType):
        return [leaf for i, m in enumerate(tree.element_types)
                for leaf in tree_leaves(m, path + (i,))]
    if isinstance(tree, PendingListType):
        return tree_leaves(tree.element_type, path + (ELEM_ROW,))
    return []


def map_parts(tree: TpyType, f: Callable[[tuple, TpyType], TpyType | None],
              path: tuple = ()) -> TpyType:
    """`tree` with each part `f(path, part)` names a replacement for (not
    None) replaced, outermost first; the members of a tuple and the element
    of a row `f` leaves in place are asked in turn. Unchanged parts stay the
    same objects."""
    new = f(path, tree)
    if new is not None:
        return new
    if isinstance(tree, TupleType):
        members = tuple(map_parts(m, f, path + (i,))
                        for i, m in enumerate(tree.element_types))
        return (tree if all(a is b for a, b in zip(members, tree.element_types))
                else TupleType(members))
    if isinstance(tree, PendingListType):
        elem = map_parts(tree.element_type, f, path + (ELEM_ROW,))
        return (tree if elem is tree.element_type
                else PendingListType(elem, tree.size, tree.literal_id))
    return tree


def map_leaves(tree: TpyType, f: Callable[[tuple, PendingNumType], TpyType],
               ) -> TpyType:
    """`tree` with every pending numeric leaf replaced by `f(path, leaf)`."""
    return map_parts(tree, lambda path, part: (
        f(path, part) if isinstance(part, PendingNumType) else None))


def at_path(t: TpyType | None, path: tuple) -> TpyType | None:
    """The part of type `t` at leaf path `path`: a tuple's member, a list,
    Span or Array's element (under their qualifiers); None when `t` has no
    such part."""
    for step in path:
        t = _bare_slot(t)
        if isinstance(step, int):
            if (not isinstance(t, TupleType)
                    or step >= len(t.element_types)):
                return None
            t = t.element_types[step]
        elif isinstance(t, PendingListType):
            t = t.element_type
        elif t is not None and (is_list(t) or is_span(t) or is_array(t)):
            t = t.type_args[0]
        else:
            return None
    return t


def zip_parts(tree: TpyType, other: TpyType | None, rows: bool = True,
              path: tuple = ()
              ) -> Iterator[tuple[tuple, TpyType, TpyType | None]]:
    """Element tree `tree` paired with type `other` part by part, outermost
    first: (path, the tree's part, `other`'s part there without its
    qualifiers). A tuple pairs with a tuple of its arity, a row with a list,
    Span, Array or list literal, and only such a pair is descended (a row
    only with `rows`); where `other` has no part of a tuple's or a row's
    shape, its part is None. The element of an empty list literal holds
    nothing yet: it pairs with any part, and nothing under that part is
    visited."""
    o = _bare_slot(other)
    if (not isinstance(tree, (TupleType, PendingListType))
            or isinstance(o, UnknownElementType)):
        yield path, tree, o
        return
    if isinstance(tree, TupleType):
        steps: Sequence = range(len(tree.element_types))
        match = (isinstance(o, TupleType)
                 and len(o.element_types) == len(tree.element_types))
    else:
        steps = (ELEM_ROW,)
        match = at_path(o, (ELEM_ROW,)) is not None
    yield path, tree, o if match else None
    if match and (rows or isinstance(tree, TupleType)):
        for step in steps:
            yield from zip_parts(at_path(tree, (step,)), at_path(o, (step,)),
                                 rows, path + (step,))


def path_words(path: tuple) -> str:
    """Where in a list's element a leaf is, as a refusal names it:
    `tuple element 0`, `row element`; '' for the element itself."""
    return ", ".join(f"tuple element {step}" if isinstance(step, int)
                     else "row element" for step in path)


def pending_leaves(t: TpyType | None) -> list[PendingNumType]:
    """Every pending number in type `t`, at any depth."""
    if t is None:
        return []
    if isinstance(t, PendingNumType):
        return [t]
    return [leaf for i in t.inner_types() for leaf in pending_leaves(i)]


def value_leaves(t: TpyType | None) -> list[PendingNumType]:
    """The pending numbers type `t` holds by value -- itself, a tuple
    member -- leaving out the element of a list literal, which the list's
    own uses decide."""
    if t is None or isinstance(t, PendingListType):
        return []
    if isinstance(t, PendingNumType):
        return [t]
    return [leaf for i in t.inner_types() for leaf in value_leaves(i)]


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


def pending_list_of(t: TpyType | None) -> PendingListType | None:
    """The pending list literal type `t` is, under its qualifiers."""
    seen = None
    while t is not None and t is not seen:
        seen = t
        t = unwrap_send_sync(unwrap_readonly(unwrap_ref_type(t)))
        if isinstance(t, OwnType):
            t = t.wrapped
    return t if isinstance(t, PendingListType) else None


def _bare_slot(declared: TpyType | None) -> TpyType | None:
    """`declared` without its readonly / Ref / Own / Send / Sync wrappers."""
    t, seen = declared, None
    while t is not None and t is not seen:
        seen = t
        t = unwrap_send_sync(unwrap_readonly(unwrap_ref_type(t)))
        if isinstance(t, OwnType):
            t = t.wrapped
    return t


def numeric_container(declared: TpyType | None) -> TpyType | None:
    """`declared` without its qualifiers when it is a list, a Span or an
    Array whose element holds numbers -- a number, a tuple with a numeric
    member, a list of such: the container a list literal's element can
    agree with. A slot is asked through `PendingNums.slot_containers`."""
    t = _bare_slot(declared)
    if t is None or not (is_list(t) or is_span(t) or is_array(t)):
        return None
    if not _holds_number(t.type_args[0]):
        return None
    return t


def _holds_number(t: TpyType) -> bool:
    t = unwrap_send_sync(unwrap_readonly(t))
    if is_integer_type(t) or is_float_type(t):
        return True
    if isinstance(t, TupleType):
        return any(_holds_number(m) for m in t.element_types)
    if is_list(t) or is_span(t) or is_array(t):
        return _holds_number(t.type_args[0])
    return False


def _type_leaves(t: TpyType, f: Callable[[tuple, TpyType], TpyType],
                 path: tuple = ()) -> TpyType:
    """Concrete type `t` with every number in it outside a nested list --
    itself, a tuple member -- replaced by `f(path, number)`."""
    bare = unwrap_send_sync(unwrap_readonly(t))
    if isinstance(bare, TupleType):
        return TupleType(tuple(_type_leaves(m, f, path + (i,))
                               for i, m in enumerate(bare.element_types)))
    if is_integer_type(bare) or is_float_type(bare):
        return f(path, bare)
    return t


def with_list_elem(t: TpyType, elem: TpyType) -> TpyType:
    """`t`, a pending list under its qualifiers, with `elem` as element."""
    if isinstance(t, PendingListType):
        if t.element_type == elem:
            return t
        return PendingListType(elem, t.size, t.literal_id)
    return t.map_inner_types(lambda i: with_list_elem(i, elem))


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

    @contextmanager
    def list_sink(self, node: TpyExpr | None) -> Iterator[None]:
        """Analyze `node` as a consumer that takes a list whose element is
        not decided yet. A permission of its own: a consumer of a pending
        number is not thereby one of such a list."""
        saved = self.ctx.pending_list_ok_node
        self.ctx.pending_list_ok_node = node
        try:
            yield
        finally:
            self.ctx.pending_list_ok_node = saved

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

    def new_elem_cell(self, name: str | None, literal_id: int,
                      decl: TpyVarDecl | None, is_float: bool,
                      no_base: bool, path: tuple = ()) -> PendingNumCell:
        """The cell that decides the leaf at `path` of the element of list
        literal `literal_id`, bound to local `name`."""
        self.ctx.pending_num_counter += 1
        cell = PendingNumCell(cid=self.ctx.pending_num_counter,
                              name=name or "this list", first_decl=decl,
                              is_float=is_float, list_literal=literal_id,
                              no_base=no_base, path=path)
        self.ctx.pending_num_cells[cell.cid] = cell
        self.ctx.func.pending_elem_cids.append(cell.cid)
        return cell

    def list_cells(self, t: TpyType | None) -> ListCells | None:
        """The cells that decide the element of the list literal `t` is
        the type of; None for a list no cell decides."""
        inner = pending_list_of(t)
        if inner is None:
            return None
        info = self.ctx.list_literals.get(inner.literal_id)
        if info is None or info.elem_cells is None:
            return None
        return self.cells_of(info)

    def cells_of(self, info: ListLiteralInfo) -> ListCells:
        """The cells of cell list `info` (`elem_cells` set)."""
        cells = self.ctx.pending_num_cells
        leaves = tree_leaves(info.element_type)
        return ListCells(info, info.element_type,
                         tuple(cells[min(leaf.cells)] for _p, leaf in leaves),
                         tuple(p for p, _leaf in leaves))

    def list_cell(self, t: TpyType | None) -> PendingNumCell | None:
        """The one cell of the list of scalar numbers `t` is the type of;
        None for a list of tuples or rows, or one no cell decides."""
        lc = self.list_cells(t)
        return lc.scalar if lc is not None else None

    # ------------------------------------------------------------------
    # Empty lists: the cell is born at the first evidence
    # ------------------------------------------------------------------

    def seedable(self, t: TpyType | None) -> ListLiteralInfo | None:
        """The record of `t` when it is an empty list bound to an
        unannotated local of the function under analysis that nothing has
        given an element yet: its element cells are born at the first store
        or typed container it meets, as if that value had been written in
        the literal."""
        inner = pending_list_of(t)
        if (inner is None or self.ctx.trial_depth or self.ctx.is_top_level
                or not isinstance(inner.element_type, UnknownElementType)):
            return None
        info = self.ctx.list_literals.get(inner.literal_id)
        if (info is None or info.elem_cells is not None
                or not isinstance(info.element_type, UnknownElementType)
                or info.has_explicit_annotation or info.is_global
                or info.variable_name is None
                or info.literal_id not in self.ctx.func.pending_resolutions):
            return None
        return info

    def cell_list(self, t: TpyType | None
                  ) -> ListCells | ListLiteralInfo | None:
        """What decides the element of list `t`, born or not: its cells,
        or, for an empty list whose cells its first store or typed
        container gives birth to, its record (`seedable`). None for a list
        no cell decides."""
        lc = self.list_cells(t)
        return lc if lc is not None else self.seedable(t)

    def attach(self, info: ListLiteralInfo, tree: TpyType) -> None:
        """From here on the cells `tree` names decide the element of the
        list of `info`."""
        info.element_type = tree
        info.elem_cells = {path: min(leaf.cells)
                           for path, leaf in tree_leaves(tree)}

    def join_rows(self, *infos: ListLiteralInfo | None) -> None:
        """The lists of `infos` are rows at one position of one list: one
        C++ type, so one representation (`ListLiteralInfo.row_group`)."""
        group: list[int] = []
        for info in infos:
            if info is None:
                continue
            for lid in info.row_group or [info.literal_id]:
                if lid not in group:
                    group.append(lid)
        for lid in group:
            rec = self.ctx.list_literals.get(lid)
            if rec is not None:
                rec.row_group = group

    def _connected(self, info: ListLiteralInfo) -> list[ListLiteralInfo]:
        """The records of the names bound to the list of `info` so far:
        the alias edges point one way and a rebinding adds edges too, so
        every record the chain from `info` reaches and every record whose
        chain reaches one of those, cycle-safe."""
        lits = self.ctx.list_literals
        found: dict[int, ListLiteralInfo] = {}
        cur: ListLiteralInfo | None = info
        while cur is not None and cur.literal_id not in found:
            found[cur.literal_id] = cur
            src = cur.source_literal_id
            cur = lits.get(src) if src is not None else None
        changed = True
        while changed:
            changed = False
            for lid in self.ctx.func.pending_resolutions:
                rec = lits.get(lid)
                if (rec is not None and lid not in found
                        and rec.source_literal_id in found):
                    found[lid] = rec
                    changed = True
        return [r for r in found.values()
                if r.elem_cells is None
                and isinstance(r.element_type, UnknownElementType)]

    def _root(self, info: ListLiteralInfo) -> ListLiteralInfo:
        """The record of the name first bound to the list `info` is."""
        seen: set[int] = set()
        cur = info
        while cur.source_literal_id is not None and cur.literal_id not in seen:
            seen.add(cur.literal_id)
            src = self.ctx.list_literals.get(cur.source_literal_id)
            if src is None:
                break
            cur = src
        return cur

    def first_binding(self, info: ListLiteralInfo) -> TpyVarDecl | None:
        """The first binding of the name empty list `info` (`seedable`)
        was bound to: the binding the cells born for it name."""
        root = self._root(info)
        name = root.variable_name or info.variable_name
        decl = self.ctx.func.var_decl_by_name.get(name) if name else None
        return decl if decl is not None and decl.init is root.expr else None

    def is_literal_value(self, value: TpyExpr | None,
                         value_type: TpyType | None) -> bool:
        """Whether a value that seeds a list counts as a literal of its
        family: a literal, a literal-seeded local (`int_literal_values`:
        its first binding is a literal, pending or not) or a pending read.
        A typed value decides the element where it seeds it."""
        t = strip_int(value_type)
        return (isinstance(t, (IntLiteralType, FloatLiteralType,
                               PendingNumType))
                or (isinstance(value, TpyName)
                    and value.name in self.ctx.func.int_literal_values))

    def new_list_tree(self, name: str | None, literal_id: int,
                      decl: TpyVarDecl | None,
                      values: list[tuple[TpyType, TpyExpr | None]],
                      joined: TpyType | None,
                      site: TpyStmt | TpyExpr | None) -> TpyType | None:
        """The element tree of a list born holding `values`: the values of
        its literal, or the first stores into an empty list. Each numeric
        leaf gets a cell born from the values at that leaf of every element
        and every row (`_born_cell`); `joined`, the element the values
        joined to, gives the parts that hold no number. The rows met on the
        way take their part of the tree and are grouped by position. None
        when the values hold no number, or a position whose values do not
        agree in shape or family, which the ordinary checks report."""
        leaves: dict[tuple, tuple[bool, list]] = {}
        rows: dict[tuple, list[ListLiteralInfo]] = {}
        later: list[tuple[TpyType, TpyType, TpyExpr | None]] = []
        skeleton = self._collect(values, joined, (), leaves, rows, later)
        if skeleton is None or not (leaves or tree_leaves(skeleton)):
            return None
        cells = {path: self._born_cell(name, literal_id, decl, family, vals,
                                       site, path)
                 for path, (family, vals) in leaves.items()}
        # A placeholder names no cell yet; a leaf of a row that has cells
        # of its own is that row's.
        tree = map_leaves(skeleton, lambda path, leaf: (
            self.elem_leaf(cells[path]) if not leaf.cells else leaf))
        for part, vt, e in later:
            self._store_at(part, vt, e, site)
        for path, recs in rows.items():
            row_elem = at_path(tree, path + (ELEM_ROW,))
            for rec in recs:
                if rec.elem_cells is None:
                    self.attach(rec, row_elem)
            self.join_rows(*recs)
        return tree

    def _literal_values(self, info: ListLiteralInfo,
                        ) -> list[tuple[TpyType, TpyExpr | None]] | None:
        """The elements a list literal or repeat `info` is written with;
        None for a list built another way."""
        if not isinstance(info.expr, (TpyArrayLiteral, TpyListRepeat)):
            return None
        out = []
        for e in info.expr.elements:
            t = self.ctx.get_expr_type(e)
            if t is None:
                return None
            out.append((t, e))
        return out

    def _collect(self, values: list[tuple[TpyType, TpyExpr | None]],
                 joined: TpyType | None, path: tuple,
                 leaves: dict[tuple, tuple[bool, list]],
                 rows: dict[tuple, list[ListLiteralInfo]],
                 later: list[tuple[TpyType, TpyType, TpyExpr | None]],
                 ) -> TpyType | None:
        """The shape of the element at `path`, gathered from every value
        there (`new_list_tree`): a numeric leaf (a placeholder; its family
        and values go to `leaves`), a tuple of shapes, a row (its records to
        `rows`), or the type `joined` holds for a part with no number. Rows
        among which one is a list with cells of its own take that list's
        element: the other rows' values are stores into it (`later`)."""
        bare = [(_bare_slot(t), e) for t, e in values]
        if not bare:
            return None
        families = {value_family(t) for t, _e in bare}
        if None not in families:
            if len(families) != 1:
                return None
            is_float = bool(families.pop())
            leaves[path] = (is_float, values)
            return PendingNumType(frozenset(), None, is_float)
        if all(isinstance(t, TupleType) for t, _e in bare):
            n = len(bare[0][0].element_types)
            if any(len(t.element_types) != n for t, _e in bare):
                return None
            j = _bare_slot(joined)
            j_members = (j.element_types if isinstance(j, TupleType)
                         and len(j.element_types) == n else (None,) * n)
            members = []
            for i in range(n):
                vals = [(t.element_types[i],
                         e.elements[i] if isinstance(e, TpyTupleLiteral)
                         and len(e.elements) == n else None)
                        for t, e in bare]
                m = self._collect(vals, j_members[i], path + (i,), leaves,
                                  rows, later)
                if m is None:
                    return None
                members.append(m)
            return TupleType(tuple(members))
        if all(isinstance(t, PendingListType) for t, _e in bare):
            recs: list[ListLiteralInfo] = []
            elems: list[tuple[TpyType, TpyExpr | None]] = []
            own = next((lc for t, _e in bare
                        if (lc := self.list_cells(t)) is not None), None)
            for t, e in bare:
                rec = self.ctx.list_literals.get(t.literal_id)
                if rec is None:
                    return None
                recs.append(rec)
                if own is not None:
                    if self.list_cells(t) is not own:
                        if not self._pairs_with(own.tree, t.element_type):
                            return None
                        later.append((PendingListType(own.tree, t.size,
                                                      own.info.literal_id),
                                      t, e))
                    continue
                written = self._literal_values(rec)
                if written is None:
                    return None
                elems += written
            first = bare[0][0]
            if own is not None:
                rows.setdefault(path, []).extend(recs)
                return PendingListType(own.tree, first.size,
                                       own.info.literal_id)
            j = _bare_slot(joined)
            sub = self._collect(
                elems, j.element_type if isinstance(j, PendingListType)
                else None, path + (ELEM_ROW,), leaves, rows, later)
            if sub is None:
                return None
            rows.setdefault(path, []).extend(recs)
            return PendingListType(sub, first.size, first.literal_id)
        if any(f is not None for f in families) or any(
                isinstance(t, (TupleType, PendingListType)) for t, _e in bare):
            return None
        return joined if joined is not None else values[0][0]

    def _born_cell(self, name: str | None, literal_id: int,
                   decl: TpyVarDecl | None, is_float: bool,
                   values: list[tuple[TpyType, TpyExpr | None]],
                   site: TpyStmt | TpyExpr | None,
                   path: tuple) -> PendingNumCell:
        """The cell of the leaf at `path` of a list born holding the
        numbers `values` there. Literals start it at their family's default
        base, which the later stores widen; typed values decide it here,
        and the literals beside them adapt as a literal stored later does.
        A leaf born holding nothing (a typed container seeds it) has no base
        either: the container decides it. A value that is a leaf of
        another list's cells is linked both ways: the two lists hold one
        type there."""
        literal = [self.is_literal_value(e, t) for t, e in values]
        cell = self.new_elem_cell(name, literal_id, decl, is_float,
                                  no_base=not values or not all(literal),
                                  path=path)
        if cell.no_base and values:
            for (t, e), lit in zip(values, literal):
                if not lit:
                    self.elem_store(cell, t, e, site)
            self.decide_at_birth(cell, site)
            values = [p for p, lit in zip(values, literal) if lit]
        for t, e in values:
            self.elem_store(cell, t, e, site)
            self._link_back(cell, t, site)
        return cell

    def _link_back(self, cell: PendingNumCell, value_type: TpyType,
                   site: TpyStmt | TpyExpr | None) -> None:
        """`value_type`, stored into `cell`, names leaf cells of other
        lists: each is linked to `cell` (`link_cells`)."""
        t = strip_int(value_type)
        if not isinstance(t, PendingNumType):
            return
        for cid in t.cells:
            other = self.ctx.pending_num_cells.get(cid)
            if other is not None and other.list_literal is not None:
                self.link_cells(cell, other, site)

    def link_cells(self, a: PendingNumCell, b: PendingNumCell,
                   site: TpyStmt | TpyExpr | None) -> None:
        """Leaf cells `a` and `b` of two lists hold one type: each holds
        what the other does. A shared type constraint, not aliasing -- the
        two lists stay two objects."""
        if a is b:
            return
        self.elem_store(a, self.cell_type(b), None, site)
        self.elem_store(b, self.cell_type(a), None, site)

    def link(self, a: TpyType, b: TpyType,
             site: TpyStmt | TpyExpr | None) -> bool:
        """The element trees `a` and `b` of two lists hold one type leaf by
        leaf (`link_cells`): a list stored as a row of the other, a list
        rebound to the other. False when the trees are not of one shape."""
        pairs = self.pair_leaves(a, b)
        if pairs is None:
            return False
        for x, y in pairs:
            self.link_cells(x, y, site)
        return True

    def _seed(self, info: ListLiteralInfo,
              values: list[tuple[TpyType, TpyExpr | None]],
              site: TpyStmt | TpyExpr | None) -> ListCells | None:
        """The cells of empty list `info` (`seedable`), born holding
        `values` (`new_list_tree`) and set on every record connected to
        it: the diagnostics name the list's first binding."""
        root = self._root(info)
        tree = self.new_list_tree(root.variable_name or info.variable_name,
                                  root.literal_id, self.first_binding(info),
                                  values, None, site)
        if tree is None:
            return None
        for rec in self._connected(info):
            self.attach(rec, tree)
        return self.cells_of(info)

    def seed_by_store(self, t: TpyType | None, value_type: TpyType,
                      value: TpyExpr | None,
                      site: TpyStmt | TpyExpr) -> ListCells | None:
        """A value stored into empty list `t` (`seedable`) seeds its
        element as if the list had been written with that value
        (`seed_by_stores`)."""
        return self.seed_by_stores(t, [(value_type, value)], site)

    def seed_by_stores(self, t: TpyType | None,
                       values: list[tuple[TpyType, TpyExpr | None]],
                       site: TpyStmt | TpyExpr) -> ListCells | None:
        """The values `values` stored together into empty list `t`
        (`seedable`) seed its element as a list literal of them does
        (`new_list_tree`). None when `t` takes no cell or the values hold
        no number."""
        info = self.seedable(t)
        if info is None or not values:
            return None
        return self._seed(info, values, site)

    def unseeded(self, t: TpyType | None, value: TpyExpr | None,
                 value_type: TpyType) -> TpyType:
        """A value stored into empty list `t` was analyzed as a value the
        list may take pending (`seedable`); when it seeded no cell, it goes
        to the element as a concrete value."""
        if self.list_cells(t) is None and value_leaves(strip_int(value_type)):
            return self.force_value(value, value_type)
        return value_type

    def store_value(self, t: TpyType | None, value_type: TpyType,
                    value: TpyExpr | None, site: TpyStmt | TpyExpr,
                    ) -> tuple[ListCells | None, TpyType]:
        """`value` stored as one element of cell list `t`, born or not
        (`cell_list`): a store into its cells, or the store that seeds
        them. Returns the cells and the value's type as the list takes
        it."""
        lc = self.list_cells(t)
        if lc is not None:
            return lc, self.tree_store(lc, value_type, value, site)
        lc = self.seed_by_store(t, value_type, value, site)
        if lc is not None:
            return lc, self.lists_as_known(value_type)
        return None, self.unseeded(t, value, value_type)

    def tree_store(self, lc: ListCells, value_type: TpyType,
                   value: TpyExpr | None, site: TpyStmt | TpyExpr) -> TpyType:
        """`value`, of `value_type`, stored as one element of cell list
        `lc`: its type tree is zipped with the element tree, each numeric
        leaf a store into that leaf's cell (`elem_store`), a row a store of
        a list into the row (`_store_row`). A part that does not pair is
        left to the ordinary check at the store. Returns the value's type
        as the list takes it."""
        self._store_at(lc.tree, value_type, value, site)
        return self.lists_as_known(value_type)

    def _store_at(self, tree: TpyType, value_type: TpyType,
                  value: TpyExpr | None, site: TpyStmt | TpyExpr) -> None:
        # The member a tuple literal writes at each path, for the messages.
        written: dict[tuple, TpyExpr | None] = {(): value}
        for path, part, vt in zip_parts(tree, value_type, rows=False):
            e = written.get(path)
            while isinstance(e, TpyCoerce):
                e = e.expr
            if isinstance(part, PendingNumType):
                self.elem_store(self.ctx.pending_num_cells[min(part.cells)],
                                vt, e, site)
            elif isinstance(part, PendingListType) and vt is not None:
                self._store_row(part, vt, site)
            elif (isinstance(part, TupleType) and vt is not None
                    and isinstance(e, TpyTupleLiteral)
                    and len(e.elements) == len(part.element_types)):
                for i, m in enumerate(e.elements):
                    written[path + (i,)] = m

    def _store_row(self, row: PendingListType, value_type: TpyType,
                   site: TpyStmt | TpyExpr) -> None:
        """A list stored as a row of the rows `row` names: one of the same
        element and one representation. A list with cells of its own is
        linked to the row's leaf by leaf; a list literal takes the row's
        cells and stores its values there; an empty one takes them as
        they are; a typed container stores its element."""
        group = self.ctx.list_literals.get(row.literal_id)
        sub = row.element_type
        other = self.list_cells(value_type)
        if other is not None:
            if self.link(sub, other.tree, site):
                self.join_rows(group, other.info)
                self._stored_as_row(group, value_type)
            return
        pending = pending_list_of(value_type)
        if pending is not None:
            rec = self.ctx.list_literals.get(pending.literal_id)
            if rec is None or rec.elem_cells is not None:
                return
            if isinstance(rec.element_type, UnknownElementType):
                empties = (self._connected(rec) if rec.variable_name
                           else [rec])
                for r in empties:
                    self.attach(r, sub)
            else:
                written = self._literal_values(rec)
                if written is None or not all(self._pairs_with(sub, t)
                                              for t, _e in written):
                    return
                for t, e in written:
                    self._store_at(sub, t, e, site)
                self.attach(rec, sub)
            self.join_rows(group, rec)
            self._stored_as_row(group, value_type)
            return
        bare = _bare_slot(value_type)
        if (bare is not None and (is_list(bare) or is_array(bare))
                and group is not None and group.elem_cells is not None):
            # A typed list stored as a row is a typed container the rows
            # meet: no conversion makes one C++ list type of another.
            row_lc = self.cells_of(group)
            if not self.fits_container(row_lc.tree, self.context_elem(bare)):
                return
            refusal = self.context_refusal(row_lc, bare, "stored")
            if refusal is not None:
                raise self.ctx.error(refusal, site)
            self.elem_context(row, bare, site, "stored")
            self._stored_as_row(group, value_type)

    @staticmethod
    def _row_key(value_type: TpyType) -> object:
        """What names a list stored as a row: its literal's record for a
        list literal, else its type."""
        pending = pending_list_of(value_type)
        return (pending.literal_id if pending is not None
                else _bare_slot(value_type))

    def _stored_as_row(self, group: ListLiteralInfo | None,
                       value_type: TpyType) -> None:
        """The store of a list of `value_type` as a row of `group` is
        admitted (`_store_row`): the store's own check reads the verdict
        (`is_stored_row`) rather than judging the list again."""
        if group is not None:
            group.stored_rows.add(self._row_key(value_type))

    def is_stored_row(self, rows: ListCells, value_type: TpyType) -> bool:
        """Whether a list of `value_type` was admitted as a row of the
        rows of `rows` (`_stored_as_row`)."""
        return self._row_key(value_type) in rows.info.stored_rows

    def pair_leaves(self, a: TpyType, b: TpyType,
                     ) -> list[tuple[PendingNumCell, PendingNumCell]] | None:
        """The leaf cells of element trees `a` and `b` at the same paths;
        None when the trees are not of one shape."""
        la, lb = tree_leaves(a), tree_leaves(b)
        if [p for p, _l in la] != [p for p, _l in lb]:
            return None
        cells = self.ctx.pending_num_cells
        return [(cells[min(x.cells)], cells[min(y.cells)])
                for (_p, x), (_q, y) in zip(la, lb)]

    def lists_as_known(self, t: TpyType) -> TpyType:
        """`t` with every list literal in it (itself, a tuple member, under
        their qualifiers) at the element its cells name so far: a row a
        store just gave the cells of the list it went into."""
        def known(_path: tuple, part: TpyType) -> TpyType | None:
            lc = self.list_cells(part)
            if lc is not None:
                return self.list_as_known(part, lc)
            if isinstance(part, PendingListType):
                # No cell decides this list: its rows are its own.
                return part
            bare = _bare_slot(part)
            if bare is not part and isinstance(bare, TupleType):
                new = map_parts(bare, known)
                return part if new is bare else new
            return None
        return map_parts(t, known)

    def store_elements(self, t: TpyType, value: TpyExpr,
                       value_type: TpyType,
                       site: TpyStmt | TpyExpr) -> ListCells | None:
        """`value`, analyzed to `value_type`, is an iterable whose every
        element list `t` then holds (`extend`, `+=`): each is a store into
        the list's element cells, which an empty list is seeded by. A list
        literal written there stores each of its values; a list whose
        element cells decide (settled by its analysis) and a typed list,
        Span or Array store their element. Returns the cells; None when the
        value says nothing about its elements' types here, or `t` has no
        cell and takes none."""
        v = value
        while isinstance(v, TpyCoerce):
            v = v.expr
        written: ListLiteralInfo | None = None
        if isinstance(v, TpyArrayLiteral):
            values = [(self.ctx.get_expr_type(e), e) for e in v.elements]
            if any(vt is None for vt, _e in values):
                return None
            inner = pending_list_of(value_type)
            written = (self.ctx.list_literals.get(inner.literal_id)
                       if inner is not None else None)
        else:
            other = self.list_cells(value_type)
            if other is not None:
                values = [(self.tree_type(other), None)]
            else:
                container = numeric_container(value_type)
                if container is None:
                    return None
                values = [(self.context_elem(container), None)]
        lc = self.list_cells(t)
        if lc is None:
            lc = self.seed_by_stores(t, values, site)
        else:
            for vt, e in values:
                self.tree_store(lc, vt, e, site)
        if (lc is not None and written is not None
                and written.elem_cells is None):
            # The literal written there holds what the list does: its own
            # element is the list's.
            self.attach(written, lc.tree)
        return lc

    def store_elements_at(self, target: TpyExpr, t: TpyType,
                          value: TpyExpr | None, value_type: TpyType,
                          site: TpyStmt | TpyExpr, use: str,
                          ) -> tuple[TpyType, ListCells | None]:
        """`value` stored element by element into cell list `t`, born or
        not, that `target` reads (`extend`, `+=`; `store_elements`). An
        operation that stores no values (`value` None), or a value that
        says nothing about its elements' types, is a use that needs the
        element: `use` decides it first. Returns the list as the operation
        sees it and the cells of a store."""
        stored = (self.store_elements(t, value, value_type, site)
                  if value is not None else None)
        if stored is not None:
            return self.list_so_far(t, stored), stored
        lc = self.list_cells(t)
        if lc is not None:
            t = self.force_list(target, t, lc, use)
            self.ctx.set_expr_type(target, t)
        return t, None

    def seed_by_context(self, t: TpyType, container: TpyType,
                        node: TpyExpr | TpyStmt | None, verb: str,
                        shown: TpyType | None = None) -> TpyType | None:
        """Empty list `t` (`seedable`) meets the typed `container` before
        any store: the container's element is the list's, decided here --
        a cell for each numeric leaf of it outside a nested list, which has
        no list literal of its own to hold one. Returns the list as the
        container sees it; None when `t` takes no cell."""
        info = self.seedable(t)
        if info is None:
            return None
        root = self._root(info)
        name = root.variable_name or info.variable_name
        decl = self.first_binding(info)

        def leaf(path: tuple, number: TpyType) -> TpyType:
            return self.elem_leaf(self.new_elem_cell(
                name, root.literal_id, decl, bool(value_family(number)),
                no_base=True, path=path))

        tree = _type_leaves(self.context_elem(container), leaf)
        if not tree_leaves(tree):
            return None
        for rec in self._connected(info):
            self.attach(rec, tree)
        return self.elem_context(t, container, node, verb, shown)

    def meets_list(
            self, into: ListCells | ListLiteralInfo, declared: TpyType,
            verb: str, adaptive: bool = False,
            order: Callable[[tuple[TpyType, ...]], Sequence[TpyType]] | None = None,
    ) -> tuple[TpyType | None, TpyType | None, str | None]:
        """`meets` for cell list `into`, born or not (`cell_list`). An empty
        list meets the first container the slot holds, or a declared view
        of numbers (`Iterable[int64]`): it has no value a view would
        convert, so nothing there refuses it."""
        if isinstance(into, ListCells):
            return self.meets(into, declared, verb, adaptive, order)
        containers = self.slot_containers(declared, order)
        if containers:
            return containers[0], None, None
        container, shown = self.compat.deduction.elem_container(
            declared, TypeParamRef("__empty_list_elem"))
        return container, shown, None

    def decide_list(self, t: TpyType, into: ListCells | ListLiteralInfo,
                    container: TpyType, node: TpyExpr | TpyStmt | None,
                    verb: str, shown: TpyType | None = None,
                    declared: bool = True) -> TpyType | None:
        """List `t`, cell list `into` born or not, meets the typed
        `container` (`meets_list` admitted it): its element is decided
        there (`elem_context`), an empty list's cells born holding the
        container's element (`seed_by_context`). Returns the list as the
        container sees it."""
        if isinstance(into, ListCells):
            return self.elem_context(t, container, node, verb, shown,
                                     declared)
        return self.seed_by_context(t, container, node, verb, shown)

    def bind_list(self, into: ListCells | ListLiteralInfo | None,
                  info: ListLiteralInfo, name: str,
                  decl: TpyVarDecl | None,
                  values: list[tuple[TpyType, TpyExpr | None]],
                  joined: TpyType | None,
                  site: TpyStmt | TpyExpr) -> ListCells | None:
        """List literal `info` of the values `values` bound to local
        `name`, which holds cell list `into`, born or not, or no list yet
        (`decl` its binding then). One local, one element type: a literal
        that rebinds a list with cells stores its values there, one that
        rebinds an empty list nothing seeded yet seeds it, as if the empty
        list had been written with these values. Returns the cells; None
        when the binding takes none: an empty literal, values with no
        number, or values the element of the list rebound does not pair
        with (an int list rebound to floats), which the rebinding refuses
        in its own words."""
        if isinstance(into, ListCells):
            if not all(self._pairs_with(into.tree, t) for t, _e in values):
                return None
            for t, e in values:
                self._store_at(into.tree, t, e, site)
            tree = into.tree
        elif not values:
            return None
        else:
            if into is not None:
                decl = self.first_binding(into)
            tree = self.new_list_tree(name, info.literal_id, decl, values,
                                      joined, site)
            if tree is None:
                return None
        self.attach(info, tree)
        if isinstance(into, ListLiteralInfo):
            self.share_cell(into, tree)
        return self.cells_of(info)

    def _pairs_with(self, tree: TpyType, value_type: TpyType) -> bool:
        """Whether a value of `value_type` pairs with element tree `tree`
        leaf by leaf, in the same numeric family; a part that holds no
        number is left to the ordinary check."""
        for _path, part, t in zip_parts(tree, value_type):
            if isinstance(t, UnknownElementType):
                continue
            if isinstance(part, PendingNumType):
                if value_family(t) != part.is_float:
                    return False
            elif isinstance(part, (TupleType, PendingListType)) and t is None:
                return False
        return True

    def share_cell(self, info: ListLiteralInfo, tree: TpyType) -> None:
        """Empty list `info` (`seedable`) is rebound to a list literal whose
        element the cells of `tree` decide: one local, one element type, so
        the empty list's records share them."""
        for rec in self._connected(info):
            self.attach(rec, tree)

    def open_list(self, t: TpyType | None) -> bool:
        """Whether `t` is, or holds as a tuple element, a list literal
        whose element is not decided yet; an empty list that has no element
        cell yet is one (`cell_list`)."""
        lc = self.list_cells(t)
        if lc is not None:
            return not lc.settled
        if self.seedable(t) is not None:
            return True
        bare = _bare_slot(t)
        return isinstance(bare, TupleType) and any(
            self.open_list(e) for e in bare.element_types)

    def slot_containers(
            self, declared: TpyType | None,
            order: Callable[[tuple[TpyType, ...]], Sequence[TpyType]] | None = None,
    ) -> tuple[TpyType, ...]:
        """The typed containers of numbers a list literal can meet at a
        slot declared `declared`: the slot itself (`numeric_container`),
        the list an annotated list local was declared as, the member of an
        optional slot, each such member of a union slot -- in the order
        `order` ranks the union's members, the one the coercion tries them
        in (`TypeCompatibility._union_member_order`)."""
        t = _bare_slot(declared)
        if isinstance(t, PendingListType):
            info = self.ctx.list_literals.get(t.literal_id)
            if (info is None or not info.has_explicit_annotation
                    or info.elem_cells is not None):
                return ()
            return self.slot_containers(info.explicit_type, order)
        if isinstance(t, OptionalType):
            return self.slot_containers(t.inner, order)
        if isinstance(t, UnionType):
            members = order(t.members) if order is not None else t.members
            return tuple(c for m in members
                         for c in self.slot_containers(m, order))
        container = numeric_container(t)
        return (container,) if container is not None else ()

    def meets(self, lc: ListCells, declared: TpyType, verb: str,
              adaptive: bool = False,
              order: Callable[[tuple[TpyType, ...]], Sequence[TpyType]] | None = None,
              ) -> tuple[TpyType | None, TpyType | None, str | None]:
        """How the list of cells `lc` meets a slot declared `declared`:
        (container, shown, refusal). The first of the slot's containers
        (`slot_containers`, a union's members in `order`) whose element
        the list's pairs with (`fits_container`) and that admits the list
        (`context_refusal`); the refusal of a slot whose one such container
        does not; all None for a slot with no container, or with several
        that all refuse, which the ordinary check reports. `adaptive` is an
        argument the enclosing generic call left adaptive, at a parameter
        that call resolved: there a view of a list (`Iterable[T]`) is a
        container too, and `shown` is the view the source spells."""
        if adaptive:
            container, shown = self.compat.deduction.elem_container(
                declared, lc.tree)
            found = [(container, shown)] if container is not None else []
        else:
            found = [(c, None) for c in self.slot_containers(declared, order)]
        found = [(c, s) for c, s in found
                 if self.fits_container(lc.tree, self.context_elem(c))]
        refusals = []
        for container, shown in found:
            refusal = self.context_refusal(lc, container, verb, shown,
                                           adaptive)
            if refusal is None:
                return container, shown, None
            refusals.append(refusal)
        return None, None, refusals[0] if len(refusals) == 1 else None

    def fits_container(self, tree: TpyType, want: TpyType | None) -> bool:
        """Whether element tree `tree` pairs with a container's element
        `want` part by part: a numeric leaf with a number (of either
        family: the family refusal names that one), a tuple with a tuple of
        its arity, a row with a list, Span or Array; a part that holds no
        number must be compatible as it is."""
        for _path, part, w in zip_parts(tree, want):
            if isinstance(part, PendingNumType):
                if value_family(w) is None:
                    return False
            elif isinstance(part, (TupleType, PendingListType)):
                if w is None:
                    return False
            elif w is None or not self.compat.is_type_compatible(part, w):
                return False
        return True

    def known_as(self, lc: ListCells, t: TpyType) -> bool:
        """Whether the element of cell list `lc`, at the types known so far,
        is `t`: each leaf the number there, each row a list, every other
        part the same type."""
        cells = self.ctx.pending_num_cells
        for _path, part, o in zip_parts(lc.tree, t):
            if isinstance(part, PendingNumType):
                if self.known_so_far(cells[min(part.cells)]) != o:
                    return False
            elif isinstance(part, PendingListType):
                if o is None or not is_list(o):
                    return False
            elif isinstance(part, TupleType):
                if o is None:
                    return False
            elif part != o:
                return False
        return True

    @staticmethod
    def elem_leaf(cell: PendingNumCell) -> PendingNumType:
        """The type that names element cell `cell`, settled or not: what
        the list literal's own record holds at the cell's leaf."""
        return PendingNumType(frozenset({cell.cid}), None, cell.is_float)

    def tree_type(self, lc: ListCells) -> TpyType:
        """The element of cell list `lc` as its cells have it: each leaf
        the settled type, else the pending one."""
        cells = self.ctx.pending_num_cells
        return map_leaves(lc.tree, lambda _p, leaf: self.cell_type(
            cells[min(leaf.cells)]))

    def tree_known(self, lc: ListCells) -> TpyType:
        """The element of cell list `lc` at the types known so far; asks
        nothing to settle."""
        cells = self.ctx.pending_num_cells
        return map_leaves(lc.tree, lambda _p, leaf: self.known_so_far(
            cells[min(leaf.cells)]))

    def list_as_known(self, t: TpyType, lc: ListCells) -> TpyType:
        """The list type `t` with the element as its cells have it: the
        settled types, else the pending ones."""
        return with_list_elem(t, self.tree_type(lc))

    def adaptive_view(self, t: TpyType, lc: ListCells) -> TpyType:
        """The list type `t` as an argument of a generic call sees it while
        the call's type parameters are inferred: a leaf that holds only
        literals so far shows a literal, which binds a type parameter as an
        integer or float literal argument does -- to whatever another
        argument binds it to, else to the default. A leaf that holds typed
        values shows the type known so far. Nothing is settled: the
        resolved parameter decides the element afterwards
        (`LocalTypeDeduction.mark_container_param_context`)."""
        cells = self.ctx.pending_num_cells

        def view(_path: tuple, leaf: PendingNumType) -> TpyType:
            cell = cells[min(leaf.cells)]
            elem = self.known_so_far(cell)
            if cell.typed or elem != self.default_type(cell.is_float):
                return elem
            literal = FloatLiteralType if cell.is_float else IntLiteralType
            init = cell.first_decl.init if cell.first_decl is not None else None
            first = (self.ctx.get_expr_type(init.elements[0])
                     if cell.path == () and getattr(init, "elements", None)
                     else None)
            return (first if isinstance(first, literal)
                    and self.literal_type(first) == elem else literal())
        return with_list_elem(t, map_leaves(lc.tree, view))

    def list_so_far(self, t: TpyType, lc: ListCells) -> TpyType:
        """The list type `t` at the element type known so far; asks nothing
        to settle. For a reader that only inspects the type."""
        return with_list_elem(t, self.tree_known(lc))

    def _cids(self) -> list[int]:
        """Every cell of the function under analysis."""
        func = self.ctx.func
        return [*func.pending_cell_of.values(), *func.pending_elem_cids]

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
        self.ctx.pending_num_epoch += 1
        if (cell.derived and cell.evidence
                and not any(s is node for s in cell.arm_sites)):
            cell.must_fit.append((value_type, node))
        elif cell.list_literal is None or not any(
                et == value_type for et, _ in cell.evidence):
            # A list holds many values of few types; one of each is all
            # the join reads.
            cell.evidence.append((value_type, node))

    def known_type(self, t: TpyType) -> TpyType:
        """`t` at the types its pending locals have so far; asks nothing
        to settle. Anything else as it is."""
        if not isinstance(t, PendingNumType):
            return t
        known = lub_int([t.floor] + [
            self.known_so_far(self.ctx.pending_num_cells[c])
            for c in t.cells])
        return known if isinstance(known, TpyType) else self.default_type(t.is_float)

    def known_so_far(self, cell: PendingNumCell) -> TpyType:
        """The type the evidence recorded so far gives, the default int when
        there is none; asks nothing to settle."""
        if cell.settled is not None:
            return cell.settled
        if cell.known is None or cell.known_at != self.ctx.pending_num_epoch:
            t = self._value(cell, {}, set())
            cell.known = (t if isinstance(t, TpyType)
                          else self.default_type(cell.is_float))
            cell.known_at = self.ctx.pending_num_epoch
        return cell.known

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
        if cell.derived or cell.no_base:
            return None
        return self.default_type(cell.is_float)

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
        self.ctx.pending_num_epoch += 1
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
        if cell.list_literal is not None:
            raise self.ctx.error(self._wider_store_refusal(cell, t), node)
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
        if cell.list_literal is not None:
            raise self.ctx.error(
                f"'{cell.name}' holds {self._elem_spelled(cell, held)} "
                f"elements and this value is {python_type_name(t)}"
                f"{self._member(cell)}, which have no common "
                f"type; annotate its first binding: {cell.name}: "
                f"list[{self._elem_spelled(cell, smallest_signed_holding(unsigned))}]"
                f" = {self._list_init(cell)}", node)
        raise self.ctx.error(
            f"'{cell.name}' holds {python_type_name(held)} values and this "
            f"value is {python_type_name(t)}, which have no common type; "
            f"annotate its first binding: {cell.name}: "
            f"{python_type_name(wide)} = {self._first_value(cell)}",
            node)

    # ------------------------------------------------------------------
    # List elements
    # ------------------------------------------------------------------

    def _elem_holds(self, cell: PendingNumCell) -> str:
        """What a refusal says the list of element cell `cell` holds, and
        since which use when one decided it early."""
        head = f"'{cell.name}' holds {self._elem_spelled(cell)} elements"
        if cell.no_base and cell.context is None:
            # The line that decided it: the first binding, or the first
            # store into a list first bound empty.
            line = _line(cell.frozen_by[0] if cell.frozen_by is not None
                         else cell.first_decl)
            return f"{head} (line {line})" if line is not None else head
        if cell.frozen_by is None:
            return head
        use, what, via = cell.frozen_by
        line = _line(use)
        since = f" since line {line}" if line is not None else ""
        through = f"through '{via}', " if via is not None else ""
        return f"{head}{since} ({through}{what})"

    def _elem_annotation(self, cell: PendingNumCell, t: TpyType,
                         spelled: str | None = None) -> str:
        """The fix a refusal about a list's element names: the annotation
        of the list's first binding that holds `t` and what it holds now."""
        if spelled is None:
            wide = join_int(self.known_so_far(cell), t)
            # Signed and unsigned values with no fixed type in common fit
            # an `int`.
            if not isinstance(wide, TpyType):
                wide = FLOAT if cell.is_float else BIGINT
            spelled = f"list[{self._elem_spelled(cell, wide)}]"
        return (f"annotate its first binding: {cell.name}: {spelled} = "
                f"{self._list_init(cell)}")

    def _elem_spelled(self, cell: PendingNumCell,
                      at: TpyType | None = None) -> str:
        """The element of the list whose leaf cell `cell` decides, as a
        diagnostic spells it: every leaf at the type known so far, this
        cell's at `at` when given."""
        info = (self.ctx.list_literals.get(cell.list_literal)
                if cell.list_literal is not None else None)
        if cell.path == () or info is None or info.elem_cells is None:
            return python_type_name(at if at is not None
                                    else self.known_so_far(cell))
        cells = self.ctx.pending_num_cells

        def leaf(_path: tuple, t: PendingNumType) -> TpyType:
            c = cells[min(t.cells)]
            return at if c is cell and at is not None else self.known_so_far(c)
        return python_type_name(_spelled_rows(map_leaves(info.element_type,
                                                         leaf)))

    @staticmethod
    def _member(cell: PendingNumCell) -> str:
        """Where in the element a leaf cell is, as a refusal adds it."""
        return f" ({path_words(cell.path)})" if cell.path else ""

    @staticmethod
    def _list_init(cell: PendingNumCell) -> str:
        """The first binding of the list of element cell `cell` as an
        annotation hint spells it: `[]` / `list()` for an empty one."""
        init = cell.first_decl.init if cell.first_decl is not None else None
        if isinstance(init, TpyArrayLiteral) and not init.elements:
            return "[]"
        if isinstance(init, TpyCall) and not init.args and not init.kwargs:
            return f"{init.func_name}()"
        return "[...]"

    def _unfit_value(self, cell: PendingNumCell, t: TpyType) -> str | None:
        """The first value the list was first bound to that an element of
        type `t` would not hold, as the source spells it; None when `t`
        holds them all, so an annotation at `t` is a fix to name."""
        init = cell.first_decl.init if cell.first_decl is not None else None
        values = [*_at_leaf(getattr(init, "elements", None) or [], cell.path),
                  *cell.stored_literals]
        unfit = self.first_unfit(values, t)
        return self._spelled_value(unfit) if unfit is not None else None

    def first_unfit(self, elements: list[TpyExpr] | None, t: TpyType,
                    literals_only: bool = False) -> TpyExpr | None:
        """The first of a list literal's `elements` that an element of type
        `t` would not hold; with `literals_only`, the first number literal."""
        for elem in elements or ():
            et = strip_int(self.ctx.get_expr_type(elem))
            if et is None or (literals_only and not isinstance(
                    et, (IntLiteralType, FloatLiteralType))):
                continue
            if not self.compat.is_type_compatible(et, t):
                return elem
        return None

    def _elem_family(self, cell: PendingNumCell) -> TpyType:
        """The element as a mix message names it: a list that holds only
        literals holds `int` or `float` values, whatever width they will
        take."""
        known = self.known_so_far(cell)
        if not cell.typed and known == self.default_type(cell.is_float):
            return FloatLiteralType() if cell.is_float else IntLiteralType()
        return known

    def elem_mix_message(self, cell: PendingNumCell, other: TpyType,
                         value: TpyExpr | None) -> str:
        """The refusal of an int meeting a float in the element of a list:
        a list literal keeps the numeric family its values are written in."""
        mine = self._elem_family(cell)
        floats = other if cell.is_float is False else mine
        mix = self.compat.deduction.int_float_mix(mine, other)
        if mix is None:
            return (f"'{cell.name}' holds {self._elem_spelled(cell, mine)} "
                    f"elements and this value is {python_type_name(other)}"
                    f"{self._member(cell)}")
        return usage_mix_message(
            mix, f"list '{cell.name}'{self._member(cell)}",
            f"{cell.name}: list[{self._elem_spelled(cell, floats)}] = "
            f"{self._list_init(cell)}", value)

    def _elem_mix(self, cell: PendingNumCell, other: TpyType,
                  node: TpyExpr | TpyStmt | None,
                  value: TpyExpr | None) -> None:
        raise self.ctx.error(self.elem_mix_message(cell, other, value), node)

    def elem_annotation_hint(self, source: TpyExpr | None,
                             expected: TpyType,
                             coercion_ctx: CoercionContext | None = None,
                             ) -> str:
        """For a mismatch at a read of a list literal's element whose type
        is not `expected`: the annotation of the list that makes it one,
        or -- when the list holds a value that annotation would not -- the
        type to give the slot the read goes to (`coercion_ctx`)."""
        while isinstance(source, TpyCoerce):
            source = source.expr
        if not isinstance(source, (TpySubscript, TpyMethodCall)):
            return ""
        cell = self.list_cell(self.ctx.expr_types.get(source.obj))
        e = strip_int(expected)
        if (cell is None or value_family(e) != cell.is_float
                or isinstance(e, (IntLiteralType, FloatLiteralType,
                                  PendingNumType))):
            return ""
        unfit = self._unfit_value(cell, e)
        if unfit is not None:
            held = python_type_name(self.known_so_far(cell))
            slot = coercion_ctx.slot if coercion_ctx is not None else "the slot"
            return (f"; '{cell.name}' holds {held} elements, and a "
                    f"list[{python_type_name(e)}] would not hold {unfit}: "
                    f"declare {slot} as {held}")
        return (f"; '{cell.name}' takes its element type from the values "
                f"stored in it, not from its uses: annotate its first "
                f"binding: {cell.name}: list[{python_type_name(e)}] = "
                f"{self._list_init(cell)}")

    def _wider_store_refusal(self, cell: PendingNumCell, t: TpyType,
                             literal: TpyExpr | None = None,
                             literal_type: TpyType | None = None) -> str:
        """The refusal of a value stored into a list whose element is
        decided and does not hold it. `literal` is the stored literal, which
        counts as `t`."""
        this = (f"the literal {self._spelled_value(literal)} counts as "
                f"{python_type_name(t)}" if literal is not None
                else f"this value is {python_type_name(t)}")
        this += self._member(cell)
        head = f"{self._elem_holds(cell)}, and {this}"
        if cell.context is None:
            return f"{head}; {self._elem_annotation(cell, t)}"
        # A typed container decided the element: its type is the only
        # annotation that container accepts.
        container, _node, verb = cell.context
        if (literal_type is not None
                and self.compat.is_type_compatible(literal_type, cell.settled)):
            spelled = self._annotated(container, cell, cell.settled)
            return f"{head}; {self._elem_annotation(cell, t, spelled)}"
        return (f"{head}; the list is {verb} as {container}, so the value "
                f"must be {python_type_name(cell.settled)}")

    def _annotated(self, container: TpyType, cell: PendingNumCell,
                   leaf: TpyType) -> str:
        """The annotation of the list of leaf cell `cell` that meets the
        typed `container` holding `leaf` at the cell's leaf: a container of
        one of its rows names the whole element (`g: list[list[int32]]`,
        not the row's `list[int32]`); a view takes a list, so the list is
        what to annotate."""
        if cell.path and value_family(
                at_path(self.context_elem(container), cell.path)) is None:
            return f"list[{self._elem_spelled(cell, leaf)}]"
        if is_list(container) or is_array(container):
            return str(container)
        return f"list[{python_type_name(self.context_elem(container))}]"

    def elem_store(self, cell: PendingNumCell, value_type: TpyType,
                   value: TpyExpr | None, site: TpyStmt | TpyExpr) -> bool:
        """Record a value stored into the list of element cell `cell` (an
        initializer element, an `append`, `xs[i] = v`). False when the value
        is no number: the ordinary check at the store reports it."""
        t = strip_int(value_type)
        family = value_family(t)
        if family is None:
            return False
        if family != cell.is_float:
            self._elem_mix(cell, t, value if value is not None else site, value)
        if not isinstance(t, (IntLiteralType, FloatLiteralType)):
            self.add_store(cell, t, site)
            return True
        if cell.settled is not None:
            at = value if value is not None else site
            if cell.no_base:
                # Beside typed values a literal adapts to their type, as in
                # the initializer; one that does not fit is refused.
                if self.compat.is_type_compatible(t, cell.settled):
                    return True
                counted = self.literal_type(t)
                # A typed container that decided the element names itself
                # as the fix (`_wider_store_refusal`).
                if not is_big_int_type(counted) and cell.context is None:
                    raise self.ctx.error(
                        f"{self._elem_holds(cell)}, and the literal "
                        f"{self._spelled_value(value)} does not fit "
                        f"{python_type_name(cell.settled)}"
                        f"{self._member(cell)}; "
                        f"{self._elem_annotation(cell, counted)}", at)
            else:
                # In a list of literals a literal counts as its default
                # type, which a decided element must hold.
                counted = self.literal_type(t)
                if join_int(cell.settled, counted) == cell.settled:
                    return True
            raise self.ctx.error(
                self._wider_store_refusal(cell, counted, value, t), at)
        self._add(cell, self.literal_type(t, value, bool(cell.evidence)), site)
        if value is not None:
            cell.stored_literals.append(value)
        return True

    def decide_at_birth(self, cell: PendingNumCell,
                        site: TpyStmt | TpyExpr) -> None:
        """A list whose first binding holds typed values has the element
        their join gives, decided at that binding (`site`), as a local
        first bound to a typed value has that value's type: a later store,
        container or generic context must fit it."""
        self.settle({cell.cid}, use=site, what="its first binding")
        self.resolve_ready()

    def force_list(self, expr: TpyExpr | None, t: TpyType,
                   lc: ListCells, what: str) -> TpyType:
        """Settle the element of list `t`, every leaf of it: `expr` is a
        use that needs it now. Returns the list with its element decided."""
        if not lc.settled:
            self.settle({c.cid for c in lc.cells if c.settled is None},
                        use=expr, what=what)
            self.resolve_ready()
        return self.list_as_known(t, lc)

    @staticmethod
    def context_elem(container: TpyType) -> TpyType:
        """The element a typed container of numbers holds."""
        return unwrap_send_sync(unwrap_readonly(container.type_args[0]))

    def context_refusal(self, lc: ListCells, container: TpyType,
                        verb: str, shown: TpyType | None = None,
                        resolved: bool = False) -> str | None:
        """Why the list of cells `lc` cannot meet the typed `container`
        (`shown` is the type the source spells there when it is a view of
        that container), or None when it can: at every leaf the container
        confirms the leaf, or widens it within its family while it is
        still open. Asks nothing to settle. `resolved` says the container
        is a generic call's parameter, resolved from the call's other
        arguments."""
        for cell, path in zip(lc.cells, lc.paths):
            refusal = self._leaf_refusal(cell, self.leaf_want(container, path),
                                         container, verb, shown, resolved)
            if refusal is not None:
                return refusal
        return None

    def leaf_want(self, container: TpyType, path: tuple) -> TpyType:
        """What typed `container` holds at leaf path `path` of its
        element."""
        part = at_path(self.context_elem(container), path)
        assert part is not None, "the container pairs with the element"
        return unwrap_send_sync(unwrap_readonly(part))

    def _leaf_refusal(self, cell: PendingNumCell, want: TpyType,
                      container: TpyType, verb: str,
                      shown: TpyType | None, resolved: bool) -> str | None:
        if value_family(want) != cell.is_float:
            return self._family_refusal(cell, want, container, verb, shown)
        held = self.known_so_far(cell)
        if held == want or (cell.settled is None
                            and join_int(held, want) == want):
            return None
        return self.elem_mismatch(cell, want, container, verb, shown,
                                  resolved)

    def elem_context(self, t: TpyType, container: TpyType,
                     node: TpyExpr | TpyStmt | None, verb: str,
                     shown: TpyType | None = None,
                     declared: bool = True) -> TpyType:
        """The list `t` meets the typed `container`, which `context_refusal`
        admitted: the container's element is one more the list holds, and
        the element is decided here. `verb` says what the list is there
        (`passed`). `declared` is False for a generic parameter the call
        resolved: an annotation of the list would resolve it otherwise, so
        it is no fixed type later refusals have to respect. Returns the
        list as the container sees it."""
        lc = self.list_cells(t)
        assert lc is not None
        open_cells = [(c, p) for c, p in zip(lc.cells, lc.paths)
                      if c.settled is None]
        if open_cells:
            spelled = shown if shown is not None else container
            for cell, path in open_cells:
                want = self.leaf_want(container, path)
                cell.typed.append(want)
                self._add(cell, want, node)
            self.settle({c.cid for c, _p in open_cells}, use=node,
                        what=f"{verb} as {spelled}")
            if declared:
                for cell, _p in open_cells:
                    cell.context = (spelled, node, verb)
            self.resolve_ready()
        self._rows_at_container(t, lc.tree, container)
        lc = self._adopt_parts(lc, self.context_elem(container))
        return self.list_as_known(t, lc)

    def _adopt_parts(self, lc: ListCells, want: TpyType) -> ListCells:
        """The parts of the element of `lc` that hold no number take the
        typed container's types there (`fits_container` found them
        compatible: a view member stored as the owned string the container
        holds), on every record that names this element."""
        held = {path: w for path, part, w in zip_parts(lc.tree, want)
                if w is not None and not pending_leaves(part)
                and not isinstance(part, (TupleType, PendingListType))}
        new = map_parts(lc.tree, lambda path, _part: held.get(path))
        if new == lc.tree:
            return lc
        old_parts = _row_parts(lc.tree)
        new_parts = _row_parts(new)
        for lid in self.ctx.func.pending_resolutions:
            rec = self.ctx.list_literals.get(lid)
            if rec is None or rec.elem_cells is None:
                continue
            for old_part, new_part in zip(old_parts, new_parts):
                if rec.element_type == old_part:
                    self.attach(rec, new_part)
                    break
        return self.cells_of(lc.info)

    def _rows_at_container(self, t: TpyType, tree: TpyType,
                           container: TpyType) -> None:
        """A row of a nested list that meets a `list` there is stored as
        one, with every row of its group (`join_rows`): the row `t` itself
        when it is one, and each row of `tree` the container holds a list
        at."""
        rows = [pending_list_of(t)] if is_list(container) else []
        rows += [part for _path, part, w in
                 zip_parts(tree, self.context_elem(container))
                 if isinstance(part, PendingListType)
                 and w is not None and is_list(w)]
        for row in rows:
            rec = (self.ctx.list_literals.get(row.literal_id)
                   if row is not None else None)
            if rec is not None and rec.row_group is not None:
                rec.passed_to_list_param = True

    def elem_mismatch(self, cell: PendingNumCell, want: TpyType,
                      container: TpyType, verb: str,
                      shown: TpyType | None = None,
                      resolved: bool = False) -> str:
        """The refusal of a list whose element is not the one the typed
        `container` it meets holds; `shown` is the type the source spells
        there when it is a view of that container, and `resolved` says the
        call's other arguments gave the container its element."""
        here = shown if shown is not None else container
        if cell.context is not None:
            # Two typed containers of different elements: no annotation
            # satisfies both.
            first, node, first_verb = cell.context
            line = _line(node)
            at = f" at line {line}" if line is not None else ""
            again = "" if verb == first_verb else f"{verb} "
            return (f"'{cell.name}' is {first_verb} as {first}{at} and "
                    f"{again}as {here} here; a list has one element type")
        head = (f"{self._elem_holds(cell)}, and it is {verb} here as "
                f"{here}{self._member(cell)}")
        unfit = self._unfit_value(cell, want)
        if unfit is not None:
            held = python_type_name(self.known_so_far(cell))
            fix = (f"; the call's other arguments make the element "
                   f"{python_type_name(want)}: convert them to {held}"
                   if resolved else "")
            return f"{head}, which would not hold {unfit}{fix}"
        spelled = self._annotated(container, cell, want)
        return f"{head}; {self._elem_annotation(cell, want, spelled)}"

    def _family_refusal(self, cell: PendingNumCell, want: TpyType,
                        container: TpyType, verb: str,
                        shown: TpyType | None) -> str:
        """The refusal of a list at a typed container of the other numeric
        family: a list literal keeps the family its values are written in."""
        here = shown if shown is not None else container
        family = self._elem_family(cell)
        held = ("integer" if isinstance(family, IntLiteralType)
                else python_type_name(family))
        head = (f"'{cell.name}' holds {held} values, and it is {verb} here "
                f"as {here}{self._member(cell)}")
        if cell.is_float or self._unfit_value(cell, want) is not None:
            return head
        # Written as floats the values print as CPython prints them; the
        # annotation converts the integers it is given.
        spelled = self._annotated(container, cell, want)
        return (f"{head}; write its values as floats, or convert them: "
                f"{self._elem_annotation(cell, want, spelled)}")

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
        if isinstance(inner, PendingNumType):
            return self.concrete(inner) if self.settled_all(inner) else t
        if t is not None and not isinstance(inner, PendingListType):
            leaves = value_leaves(t)
            if leaves and all(self.settled_all(leaf) for leaf in leaves):
                return self.finalize_values(t)
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

    def force_value(self, expr: TpyExpr | None, t: TpyType,
                    what: str | None = None) -> TpyType:
        """`force` for a value that is a pending number or holds some (a
        tuple read from a list whose leaves cells decide): every one of
        them is settled, `expr` being a use that needs the type now."""
        if is_pending_num(t):
            return self.force(expr, t, what)
        cids = {cid for leaf in value_leaves(t) for cid in leaf.cells
                if self.ctx.pending_num_cells[cid].settled is None}
        if cids:
            if what is None:
                what = (self.describe_use(expr) if expr is not None
                        else "a use that needs its type")
            self.settle(cids, use=expr, what=what)
            self.resolve_ready()
        concrete = self.finalize_values(t)
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
        # A list the body reads has its element decided the same way.
        for n in names:
            info = self.ctx.list_literals.get(
                self.ctx.func.variable_to_literal.get(n, -1))
            if info is not None and info.elem_cells is not None:
                cids |= {c.cid for c in self.cells_of(info).cells
                         if c.settled is None}
        for cid in cids:
            self.settle({cid}, use=use, what=what)
        if cids:
            self.resolve_ready()

    # ------------------------------------------------------------------
    # Deferred resolution
    # ------------------------------------------------------------------

    def when_elem_known(self, node: TpyExpr | TpyStmt, lc: ListCells,
                        record: Callable[[TpyType], None]) -> None:
        """Call `record` with the element of cell list `lc` once its cells
        settle -- a signature recorded at the element the call met -- and
        again once the rows in it, list literals resolved after the settle,
        have their list types."""
        def resolve(types: tuple[TpyType, ...]) -> None:
            record(types[0])
            if contains_pending_leaf(types[0]):
                self.ctx.func.after_list_resolution.append((types[0], record))
        self.defer(node, (lc.tree,), resolve)

    def defer(self, node: TpyExpr | TpyStmt | None, types: tuple[TpyType, ...],
              resolve: Callable[[tuple[TpyType, ...]], None]) -> None:
        self.ctx.func.pending_num_deferred.append(
            DeferredIntOp(node, tuple(strip_int(t) for t in types), resolve))

    def _ready(self, op: DeferredIntOp) -> bool:
        return all(self.settled_all(leaf) for t in op.types
                   for leaf in pending_leaves(t))

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
                op.resolve(tuple(self.finalize(t) if pending_leaves(t)
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
        if not self._cids():
            return
        self.settle(set(self._cids()))
        # A cell settled early, inside its first arm, is judged here with
        # every arm seen.
        for cid in func.pending_cell_of.values():
            self.check_arm_group(self.ctx.pending_num_cells[cid])
        self.resolve_ready()
        # The expression types that held a pending leaf are rewritten with
        # the other pending leaves' (`pending_composite_exprs`).
        for cid in self._cids():
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
        if not self._cids():
            return
        left = surviving_placeholder(body or [])
        if left is not None:
            raise AssertionError(
                f"Internal error: a conversion at line "
                f"{getattr(left.loc, 'line', None)} was left unresolved "
                f"after its function settled")
        tables: list[tuple[str, TpyType | None]] = []
        for cid in self._cids():
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

    def finalize_values(self, t: TpyType) -> TpyType:
        """`finalize` for the numbers `t` holds by value (`value_leaves`):
        a list literal in it keeps its type, which its resolution gives."""
        if isinstance(t, PendingNumType):
            return self.concrete(t)
        if isinstance(t, PendingListType):
            return t
        return t.map_inner_types(self.finalize_values)

    def drop_cells(self) -> None:
        """Forget the function's cells once nothing refers to them."""
        for cid in self._cids():
            self.ctx.pending_num_cells.pop(cid, None)
        self.ctx.func.pending_cell_of = {}
        self.ctx.func.pending_elem_cids = []


def _row_parts(tree: TpyType) -> list[TpyType]:
    """`tree` and the element of every row in it, outermost first."""
    out = [tree]

    def visit(path: tuple, part: TpyType) -> None:
        if path and path[-1] == ELEM_ROW:
            out.append(part)
    map_parts(tree, visit)
    return out


def _spelled_rows(t: TpyType) -> TpyType:
    """`t` with every row a list."""
    def spell(_path: tuple, part: TpyType) -> TpyType | None:
        if isinstance(part, PendingListType):
            return make_list(map_parts(part.element_type, spell))
        return None
    return map_parts(t, spell)


def _at_leaf(elements: list[TpyExpr], path: tuple) -> list[TpyExpr]:
    """The expressions written at leaf path `path` of a list literal's
    `elements`: through tuple literals' members and nested literals'
    elements."""
    out = list(elements)
    for step in path:
        nxt: list[TpyExpr] = []
        for e in out:
            while isinstance(e, TpyCoerce):
                e = e.expr
            if isinstance(step, int):
                if (isinstance(e, TpyTupleLiteral)
                        and step < len(e.elements)):
                    nxt.append(e.elements[step])
            elif isinstance(e, (TpyArrayLiteral, TpyListRepeat)):
                nxt.extend(e.elements)
        out = nxt
    return out


def _other_arm(cell: PendingNumCell) -> str:
    return "the other" if len(cell.arm_sites) == 2 else "another"


def _line(node: TpyStmt | TpyExpr | None) -> int | None:
    return getattr(getattr(node, "loc", None), "line", None)


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
