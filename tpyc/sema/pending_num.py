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
from typing import TYPE_CHECKING, Callable, Iterator, Mapping, Sequence

from ..coercions import Coercion, CoercionContext
from ..parse import (
    TpyArrayLiteral, TpyCall, TpyCoerce, TpyDictLiteral, TpyExpr,
    TpyIntLiteral, TpyListRepeat, TpyMethodCall, TpyName, TpySetLiteral,
    TpyStmt, TpySubscript, TpyTupleLiteral, TpyUnaryOp, TpyVarDecl,
)
from ..parse.nodes import is_parse_node
from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, PendingNumType, OwnType,
    AutoReadonlyType,
    OptionalType, PendingContainerType, PendingListType, PendingDictType,
    PendingSetType, TupleType, UnionType, UnknownElementType,
    ContainerLiteralInfo, ListLiteralInfo,
    TypeParamRef, BIGINT, FLOAT, INT64, ELEM, KEY, VALUE,
    is_float_type, contains_pending_leaf,
    contains_pending_num,
    is_integer_type, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from ..type_def_registry import (
    is_fixed_int_type, is_big_int_type, is_float64_type, int_traits_of,
    is_array, is_dict, is_list, is_set, is_span,
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
    # The record of the container literal (a list's, a dict's, a set's)
    # this cell decides a leaf of; `name` is then the container's variable,
    # and the cell is no local's.
    record: 'ContainerRecord | None' = None
    # Where in that container the leaf is (`ContainerCells`): the steps
    # from the container to it -- `ELEM` a list's or set's element, `KEY` /
    # `VALUE` a dict's key or value, an int a tuple member, and on into a
    # nested container.
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


ContainerRecord = ContainerLiteralInfo


@dataclass(eq=False)
class ContainerCells:
    """A container literal whose numeric leaves cells decide: `info` the
    record whose cells they are, `tree` its type (`pending_type()`), whose
    numeric leaves are the `PendingNumType`s naming `cells` (in path order)
    and every other part the type the literal holds there. A list of
    scalars has one leaf, its element; a list of tuples one per numeric
    member; a nested list one per depth, shared by every row; a dict one
    per numeric leaf of its key and value. A container nested in another
    container's tree has a record of its own, whose cells are the outer
    container's at that part."""
    info: ContainerRecord
    tree: PendingContainerType
    cells: tuple[PendingNumCell, ...]
    # Each cell's path in `tree` (a nested container's own paths start at
    # it, while the cell's `path` is from the container first bound).
    paths: tuple[tuple, ...] = ()

    @property
    def settled(self) -> bool:
        return all(c.settled is not None for c in self.cells)

    @property
    def cids(self) -> frozenset[int]:
        return frozenset(c.cid for c in self.cells)

    @property
    def scalar(self) -> PendingNumCell | None:
        """The one cell of a list or set of scalar numbers."""
        steps = self.tree.STEPS
        if len(steps) != 1:
            return None
        part = self.tree.part(steps[0])
        return self.cells[0] if isinstance(part, PendingNumType) else None


def _node_steps(t: TpyType) -> tuple:
    """The steps into the parts of tree node `t`: a tuple's members, a
    container's element, key and value (`PendingContainerType.STEPS`); ()
    for a leaf or a part that holds no number."""
    if isinstance(t, TupleType):
        return tuple(range(len(t.element_types)))
    if isinstance(t, PendingContainerType):
        return t.STEPS
    return ()


def pairs_as(node: TpyType | type, other: TpyType | None) -> bool:
    """Whether type `other` (without its qualifiers) is a container that
    tree node `node` (or a node of that class) pairs with: a literal of
    its kind, or a declared container it can be stored as."""
    cls = node if isinstance(node, type) else type(node)
    if other is None or not issubclass(cls, PendingContainerType):
        return False
    if isinstance(other, PendingContainerType):
        return type(other) is cls
    return cls.declares(other)


def _declared_part(t: TpyType, step: object) -> TpyType | None:
    """The part of declared container `t` at `step`: a list's, Span's,
    Array's or set's element, a dict's key or value."""
    if step in (KEY, VALUE):
        return t.type_args[0 if step == KEY else 1] if is_dict(t) else None
    if is_list(t) or is_span(t) or is_array(t) or is_set(t):
        return t.type_args[0]
    return None


def tree_leaves(tree: TpyType, path: tuple = ()
                ) -> list[tuple[tuple, PendingNumType]]:
    """The pending numeric leaves of tree `tree`, with their paths, in path
    order."""
    if isinstance(tree, PendingNumType):
        return [(path, tree)]
    return [leaf for step in _node_steps(tree)
            for leaf in tree_leaves(_child(tree, step), path + (step,))]


def _child(node: TpyType, step: object) -> TpyType:
    """The part of tree node `node` (a tuple or a container) at `step`."""
    if isinstance(step, int):
        return node.element_types[step]
    return node.part(step)


def _with_children(node: TpyType, parts: list[TpyType]) -> TpyType:
    """Tree node `node` with its parts (in `_node_steps` order) `parts`."""
    if isinstance(node, TupleType):
        return TupleType(tuple(parts))
    return node.with_parts(tuple(parts))


def map_parts(tree: TpyType, f: Callable[[tuple, TpyType], TpyType | None],
              path: tuple = ()) -> TpyType:
    """`tree` with each part `f(path, part)` names a replacement for (not
    None) replaced, outermost first; the members of a tuple and the parts
    of a container `f` leaves in place are asked in turn. Unchanged parts
    stay the same objects."""
    new = f(path, tree)
    if new is not None:
        return new
    steps = _node_steps(tree)
    if not steps:
        return tree
    old = [_child(tree, step) for step in steps]
    parts = [map_parts(c, f, path + (step,)) for c, step in zip(old, steps)]
    if all(a is b for a, b in zip(parts, old)):
        return tree
    return _with_children(tree, parts)


def map_leaves(tree: TpyType, f: Callable[[tuple, PendingNumType], TpyType],
               ) -> TpyType:
    """`tree` with every pending numeric leaf replaced by `f(path, leaf)`."""
    return map_parts(tree, lambda path, part: (
        f(path, part) if isinstance(part, PendingNumType) else None))


def at_path(t: TpyType | None, path: tuple) -> TpyType | None:
    """The part of type `t` at leaf path `path`: a tuple's member, a
    container literal's part, a list, Span, Array or set's element, a
    dict's key or value (under their qualifiers); None when `t` has no
    such part."""
    for step in path:
        t = bare_slot(t)
        if isinstance(step, int):
            if (not isinstance(t, TupleType)
                    or step >= len(t.element_types)):
                return None
            t = t.element_types[step]
        elif isinstance(t, PendingContainerType):
            if step not in t.STEPS:
                return None
            t = t.part(step)
        elif t is not None:
            t = _declared_part(t, step)
        else:
            return None
    return t


def zip_parts(tree: TpyType, other: TpyType | None, rows: bool = True,
              path: tuple = ()
              ) -> Iterator[tuple[tuple, TpyType, TpyType | None]]:
    """Tree `tree` paired with type `other` part by part, outermost first:
    (path, the tree's part, `other`'s part there without its qualifiers).
    A tuple pairs with a tuple of its arity, a container node with a
    container it pairs as (`pairs_as`), and only such a pair is descended
    (a list nested in the tree only with `rows`); where `other` has no
    part of a node's shape, its part is None. The element of an empty
    list literal holds nothing yet: it pairs with any part, and nothing
    under that part is visited."""
    o = bare_slot(other)
    steps = _node_steps(tree)
    if not steps or isinstance(o, UnknownElementType):
        yield path, tree, o
        return
    if isinstance(tree, TupleType):
        match = (isinstance(o, TupleType)
                 and len(o.element_types) == len(tree.element_types))
    else:
        match = pairs_as(tree, o)
    yield path, tree, o if match else None
    if match and (rows or not path or not isinstance(tree, PendingListType)):
        for step in steps:
            yield from zip_parts(_child(tree, step), at_path(o, (step,)),
                                 rows, path + (step,))


_STEP_WORDS = {KEY: "key", VALUE: "value", ELEM: "element"}


def path_words(node: TpyType, path: tuple) -> str:
    """Where under tree node `node` a leaf at `path` is, as a refusal names
    it: `tuple element 0`, `row element` (a nested list's), `key`,
    `value`, `element`; '' for `node` itself."""
    words = []
    for step in path:
        if isinstance(step, int):
            words.append(f"tuple element {step}")
        elif isinstance(node, PendingListType):
            words.append("row element")
        else:
            words.append(_STEP_WORDS[step])
        node = _child(node, step) if _node_steps(node) else node
    return ", ".join(words)


def pending_leaves(t: TpyType | None) -> list[PendingNumType]:
    """Every pending number in type `t`, at any depth."""
    if t is None:
        return []
    if isinstance(t, PendingNumType):
        return [t]
    return [leaf for i in t.inner_types() for leaf in pending_leaves(i)]


def value_leaves(t: TpyType | None) -> list[PendingNumType]:
    """The pending numbers type `t` holds by value -- itself, a tuple
    member -- leaving out the parts of a container literal, which the
    container's own uses decide."""
    if t is None or isinstance(t, PendingContainerType):
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


def leaves_as_numbers(t: TpyType) -> TpyType:
    """`t` with every pending numeric leaf a number of its family, for a
    question any width answers alike (hashability, equality)."""
    if isinstance(t, PendingNumType):
        return FLOAT if t.is_float else INT64
    return t.map_inner_types(leaves_as_numbers)


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


def bare_slot(declared: TpyType | None) -> TpyType | None:
    """`declared` without its readonly / auto-readonly / Ref / Own / Send /
    Sync wrappers."""
    t, seen = declared, None
    while t is not None and t is not seen:
        seen = t
        t = unwrap_send_sync(unwrap_readonly(unwrap_ref_type(t)))
        if isinstance(t, (OwnType, AutoReadonlyType)):
            t = t.wrapped
    return t


def numeric_container(declared: TpyType | None,
                      node: TpyType | type = PendingListType) -> TpyType | None:
    """`declared` without its qualifiers when it is a container that tree
    node `node` (or a node of that class) pairs as (`pairs_as`: a list
    literal a list, Span or Array, a dict literal a dict, a set literal a
    set) and it holds numbers -- itself, a tuple member, a nested
    container's: the container a literal's leaves can agree with. A slot
    is asked through `PendingNums.slot_containers`."""
    t = bare_slot(declared)
    if t is None or isinstance(t, PendingContainerType) or not pairs_as(node, t):
        return None
    return t if _holds_number(t) else None


def _holds_number(t: TpyType) -> bool:
    t = unwrap_send_sync(unwrap_readonly(t))
    if is_integer_type(t) or is_float_type(t):
        return True
    if isinstance(t, TupleType):
        return any(_holds_number(m) for m in t.element_types)
    if is_list(t) or is_span(t) or is_array(t) or is_set(t):
        return _holds_number(t.type_args[0])
    if is_dict(t):
        return any(_holds_number(a) for a in t.type_args[:2])
    return False


def with_list_elem(t: TpyType, elem: TpyType) -> TpyType:
    """`t`, a pending list under its qualifiers, with `elem` as element."""
    if isinstance(t, PendingListType):
        if t.element_type == elem:
            return t
        return PendingListType(elem, t.size, t.literal_id)
    return t.map_inner_types(lambda i: with_list_elem(i, elem))


def with_container(t: TpyType, node: TpyType) -> TpyType:
    """`t`, a container literal's type under its qualifiers, holding the
    parts of `node`, a tree of its kind -- it keeps its own record -- or,
    for a decided `node` (no container literal), replaced by it."""
    if isinstance(t, PendingContainerType):
        if not isinstance(node, PendingContainerType):
            return node
        if t.parts() == node.parts():
            return t
        return t.with_parts(node.parts())
    return t.map_inner_types(lambda i: with_container(i, node))


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


@dataclass(frozen=True, eq=False)
class Entry:
    """One entry stored into a container: per step of the container
    (`PendingContainerType.STEPS`), the type of what goes there and the
    expression written there, None for a part of a typed container -- a
    list's or set's element, a dict's key and value."""
    parts: tuple[tuple[TpyType, object], ...]


def container_parts(t: TpyType, node: TpyType | type
                    ) -> tuple[TpyType | None, ...]:
    """The parts of container type `t` at the steps of tree node `node`
    (or of a node of that class): a container literal's own, a declared
    container's element, key and value."""
    cls = node if isinstance(node, type) else type(node)
    if isinstance(t, PendingContainerType):
        return t.parts()
    return tuple(_declared_part(t, step) for step in cls.STEPS)


def root_of(into: 'ContainerCells | ContainerRecord') -> PendingContainerType:
    """The container type of cell container `into`, born or not."""
    return (into.tree if isinstance(into, ContainerCells)
            else into.pending_type())


def literal_entries(e: object, node: TpyType | type
                    ) -> list[tuple[object, ...]] | None:
    """The entries a container literal `e` of tree node `node`'s kind is
    written with, each the expressions at its steps (a list's or set's
    elements, a dict's key and value); None for anything else."""
    while isinstance(e, TpyCoerce):
        e = e.expr
    cls = node if isinstance(node, type) else type(node)
    if (not issubclass(cls, PendingContainerType)
            or not isinstance(e, cls.written_as())):
        return None
    if isinstance(e, TpyDictLiteral):
        return list(zip(e.keys, e.values))
    return [(x,) for x in e.elements]


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

    def new_elem_cell(self, name: str | None, record: ContainerRecord | None,
                      decl: TpyVarDecl | None, is_float: bool,
                      no_base: bool, path: tuple = ()) -> PendingNumCell:
        """The cell that decides the leaf at `path` of the container
        literal of `record`, bound to local `name`."""
        self.ctx.pending_num_counter += 1
        noun = record.kind if record is not None else "list"
        cell = PendingNumCell(cid=self.ctx.pending_num_counter,
                              name=name or f"this {noun}", first_decl=decl,
                              is_float=is_float, record=record,
                              no_base=no_base, path=path)
        self.ctx.pending_num_cells[cell.cid] = cell
        self.ctx.func.pending_elem_cids.append(cell.cid)
        return cell

    def container_cells(self, t: TpyType | None) -> ContainerCells | None:
        """The cells that decide the leaves of the container literal `t` is
        the type of (under its qualifiers): its record's, found by the
        literal it names. None for a container no cell decides."""
        bare = bare_slot(t)
        if not isinstance(bare, PendingContainerType):
            return None
        info = self.record_of(bare)
        if info is None or info.elem_cells is None:
            return None
        return self.cells_of(info)

    def record_of(self, t: PendingContainerType) -> ContainerRecord | None:
        """The record of container literal type `t`."""
        return self.ctx.container_record(t.literal_id)

    def list_cells(self, t: TpyType | None) -> ContainerCells | None:
        """`container_cells` for a list literal only."""
        lc = self.container_cells(t)
        return (lc if lc is not None and isinstance(lc.tree, PendingListType)
                else None)

    @staticmethod
    def record_tree(info: ContainerRecord) -> PendingContainerType:
        """What container record `info` holds as its cells' paths see it:
        the literal's type."""
        return info.pending_type()
    def cells_of(self, info: ContainerRecord) -> ContainerCells:
        """The cells of cell container `info` (`elem_cells` set)."""
        cells = self.ctx.pending_num_cells
        tree = info.pending_type()
        leaves = tree_leaves(tree)
        return ContainerCells(info, tree,
                              tuple(cells[min(leaf.cells)] for _p, leaf in leaves),
                              tuple(p for p, _leaf in leaves))

    def list_cell(self, t: TpyType | None) -> PendingNumCell | None:
        """The one cell of the list of scalar numbers `t` is the type of;
        None for a list of tuples or rows, or one no cell decides."""
        lc = self.list_cells(t)
        return lc.scalar if lc is not None else None

    # ------------------------------------------------------------------
    # Empty containers: the cells are born at the first evidence
    # ------------------------------------------------------------------

    def seedable(self, t: TpyType | None) -> ContainerRecord | None:
        """The record of `t` when it is an empty list, dict or set bound to
        an unannotated local of the function under analysis that nothing
        has given an element yet: its cells are born at the first store or
        typed container it meets, as if that value had been written in the
        literal."""
        if self.ctx.trial_depth or self.ctx.is_top_level:
            return None
        bare = bare_slot(t)
        if not isinstance(bare, PendingContainerType) or not all(
                isinstance(p, UnknownElementType) for p in bare.parts()):
            return None
        info = self.record_of(bare)
        if (info is None or info.elem_cells is not None
                or not all(isinstance(p, UnknownElementType)
                           for p in info.pending_type().parts())
                or info.has_explicit_annotation or info.is_global
                or info.variable_name is None
                or info.literal_id not in self.ctx.func.pending_resolutions):
            return None
        return info

    def cell_container(self, t: TpyType | None
                       ) -> ContainerCells | ContainerRecord | None:
        """What decides the leaves of container `t`, born or not: its
        cells, or, for an empty container whose cells its first store or
        typed container gives birth to, its record (`seedable`). None for a
        container no cell decides."""
        lc = self.container_cells(t)
        return lc if lc is not None else self.seedable(t)

    def cell_list(self, t: TpyType | None
                  ) -> ContainerCells | ContainerRecord | None:
        """`cell_container` for a list only."""
        into = self.cell_container(t)
        return (into if into is not None
                and isinstance(root_of(into), PendingListType) else None)

    def attach(self, info: ContainerRecord, tree: PendingContainerType) -> None:
        """From here on the cells `tree` names decide what the container of
        `info` holds (`record_tree`), and the records of the containers
        nested in it hold their parts of it."""
        info.set_parts(tree)
        info.elem_cells = {path: min(leaf.cells)
                           for path, leaf in tree_leaves(tree)}

        def nested(path: tuple, part: TpyType) -> None:
            if path and isinstance(part, (PendingDictType, PendingSetType)):
                rec = self.record_of(part)
                if rec is not None and rec.part_of is not None:
                    rec.set_parts(part)
                    rec.elem_cells = {p: min(leaf.cells)
                                      for p, leaf in tree_leaves(part)}
        map_parts(tree, nested)

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
            rec = self.ctx.list_literal(lid)
            if rec is not None:
                rec.row_group = group

    def _connected(self, info: ContainerRecord) -> list[ContainerRecord]:
        """The records of the names bound to the list of `info` so far:
        the alias edges point one way and a rebinding adds edges too, so
        every record the chain from `info` reaches and every record whose
        chain reaches one of those, cycle-safe. A dict or set has one
        record whatever names it."""
        if not isinstance(info, ListLiteralInfo):
            return [info] if info.elem_cells is None else []
        found: dict[int, ListLiteralInfo] = {}
        cur: ListLiteralInfo | None = info
        while cur is not None and cur.literal_id not in found:
            found[cur.literal_id] = cur
            cur = self.ctx.list_literal(cur.source_literal_id)
        changed = True
        while changed:
            changed = False
            for lid in self.ctx.func.pending_resolutions:
                rec = self.ctx.list_literal(lid)
                if (rec is not None and lid not in found
                        and rec.source_literal_id in found):
                    found[lid] = rec
                    changed = True
        return [r for r in found.values()
                if r.elem_cells is None
                and isinstance(r.element_type, UnknownElementType)]

    def _root(self, info: ContainerRecord) -> ContainerRecord:
        """The record of the name first bound to the list `info` is."""
        if not isinstance(info, ListLiteralInfo):
            return info
        seen: set[int] = set()
        cur = info
        while cur.source_literal_id is not None and cur.literal_id not in seen:
            seen.add(cur.literal_id)
            src = self.ctx.list_literal(cur.source_literal_id)
            if src is None:
                break
            cur = src
        return cur

    def first_binding(self, info: ContainerRecord) -> TpyVarDecl | None:
        """The first binding of the name empty container `info` (`seedable`)
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

    def new_tree(self, name: str | None, record: ContainerRecord,
                 decl: TpyVarDecl | None, entries: list[Entry],
                 joined: TpyType | None,
                 site: TpyStmt | TpyExpr | None) -> PendingContainerType | None:
        """The tree of container `record` born holding `entries`: the
        entries of its literal, or the first stores into an empty one. Each
        numeric leaf gets a cell born from the values at that leaf of every
        entry and row (`_born_cell`); `joined`, the container type the
        values joined to, gives the parts that hold no number. The rows met
        on the way take their part of the tree and are grouped by position;
        a dict or set met there gets a record of its own. None when the
        values hold no number, or a position whose values do not agree in
        shape or family, which the ordinary checks report."""
        leaves: dict[tuple, tuple[bool, list]] = {}
        rows: dict[tuple, list[ListLiteralInfo]] = {}
        later: list[tuple[TpyType, TpyType, TpyExpr | None]] = []
        root = record.pending_type()
        skeleton = self._collect_entries(
            root, [e.parts for e in entries], joined, (), leaves, rows,
            later, record)
        if skeleton is None or not (leaves or tree_leaves(skeleton)):
            return None
        cells = {path: self._born_cell(name, record, decl, family, vals,
                                       site, path)
                 for path, (family, vals) in leaves.items()}
        # A placeholder names no cell yet; a leaf of a row that has cells
        # of its own is that row's.
        tree = map_leaves(skeleton, lambda path, leaf: (
            self.elem_leaf(cells[path]) if not leaf.cells else leaf))
        for part, vt, e in later:
            self._store_at(part, vt, e, site)
        for entry in entries:
            for step, (_t, e) in zip(tree.STEPS, entry.parts):
                self._retype_written(tree.part(step), e)
        for path, recs in rows.items():
            row = at_path(tree, path)
            for rec in recs:
                if rec.elem_cells is None:
                    self.attach(rec, row)
            self.join_rows(*recs)
        return tree

    def _retype_written(self, part: TpyType, e: object) -> None:
        """A dict or set literal written inside the values a tree was born
        holding, at its `part`, holds what the tree does there: its type is
        that part (the leaves naming the cells), as a row literal's record
        takes its row's."""
        while isinstance(e, TpyCoerce):
            e = e.expr
        if isinstance(e, TpyTupleLiteral) and isinstance(part, TupleType):
            if len(e.elements) == len(part.element_types):
                for m, x in zip(part.element_types, e.elements):
                    self._retype_written(m, x)
            return
        # A list literal is a row, whose own record holds its part.
        if (not isinstance(part, PendingContainerType)
                or isinstance(part, PendingListType)):
            return
        written = literal_entries(e, part)
        if written is None or not tree_leaves(part):
            return
        self.ctx.set_expr_type(e, part)
        for exprs in written:
            for step, x in zip(part.STEPS, exprs):
                self._retype_written(part.part(step), x)

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

    def _collect(self, values: list[tuple[TpyType, object]],
                 joined: TpyType | None, path: tuple,
                 leaves: dict[tuple, tuple[bool, list]],
                 rows: dict[tuple, list[ListLiteralInfo]],
                 later: list[tuple[TpyType, TpyType, object]],
                 root: ContainerRecord | None,
                 ) -> TpyType | None:
        """The shape of the part at `path`, gathered from every value
        there (`new_tree`): a numeric leaf (a placeholder; its family
        and values go to `leaves`), a tuple of shapes, a row (its records to
        `rows`), a dict or set (`_collect_node`), or the type `joined` holds
        for a part with no number. Rows among which one is a list with
        cells of its own take that list's element: the other rows' values
        are stores into it (`later`)."""
        bare = [(bare_slot(t), e) for t, e in values]
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
            j = bare_slot(joined)
            j_members = (j.element_types if isinstance(j, TupleType)
                         and len(j.element_types) == n else (None,) * n)
            members = []
            for i in range(n):
                vals = [(t.element_types[i],
                         e.elements[i] if isinstance(e, TpyTupleLiteral)
                         and len(e.elements) == n else None)
                        for t, e in bare]
                m = self._collect(vals, j_members[i], path + (i,), leaves,
                                  rows, later, root)
                if m is None:
                    return None
                members.append(m)
            return TupleType(tuple(members))
        if all(isinstance(t, PendingListType) for t, _e in bare):
            recs: list[ListLiteralInfo] = []
            elems: list[tuple[TpyType, object]] = []
            own = next((lc for t, _e in bare
                        if (lc := self.list_cells(t)) is not None), None)
            for t, e in bare:
                rec = self.ctx.list_literal(t.literal_id)
                if rec is None:
                    return None
                recs.append(rec)
                if own is not None:
                    if self.list_cells(t) is not own:
                        if not self._pairs_with(own.tree, t):
                            return None
                        later.append((PendingListType(
                            own.tree.element_type, t.size,
                            own.info.literal_id), t, e))
                    continue
                written = self._literal_values(rec)
                if written is None:
                    return None
                elems += written
            first = bare[0][0]
            if own is not None:
                rows.setdefault(path, []).extend(recs)
                return PendingListType(own.tree.element_type, first.size,
                                       own.info.literal_id)
            j = bare_slot(joined)
            sub = self._collect(
                elems, j.element_type if isinstance(j, PendingListType)
                else None, path + (ELEM,), leaves, rows, later, root)
            if sub is None:
                return None
            rows.setdefault(path, []).extend(recs)
            return PendingListType(sub, first.size, first.literal_id)
        classes = {self._node_class(t) for t, _e in bare}
        if len(classes) == 1 and None not in classes:
            return self._collect_node(classes.pop(), bare, joined, path,
                                      leaves, rows, later, root)
        if any(f is not None for f in families) or any(
                isinstance(t, (TupleType, PendingListType)) for t, _e in bare):
            return None
        return joined if joined is not None else values[0][0]

    def _node_class(self, t: TpyType | None) -> type | None:
        """The dict or set node a value at a tree's position makes: a dict
        or set, or a dict or set literal whose cells decide it (an empty
        one nothing seeded is a part with no number)."""
        if isinstance(t, (PendingDictType, PendingSetType)):
            return type(t) if self.container_cells(t) is not None else None
        if is_dict(t):
            return PendingDictType
        if is_set(t):
            return PendingSetType
        return None

    def _collect_node(self, cls: type, bare: list[tuple[TpyType, object]],
                      joined: TpyType | None, path: tuple,
                      leaves: dict[tuple, tuple[bool, list]],
                      rows: dict[tuple, list[ListLiteralInfo]],
                      later: list[tuple[TpyType, TpyType, object]],
                      root: ContainerRecord | None,
                      ) -> TpyType | None:
        """`_collect` for a position whose values are dicts or sets (of
        node class `cls`): the node's parts gathered from every entry of
        every value -- a written literal's entries one by one, a typed
        container's types as one entry -- under a record of its own. A
        value whose leaves cells of its own decide gives the node its
        tree, and the other values are stores into it, as for rows."""
        own = next((lc for t, _e in bare
                    if (lc := self.container_cells(t)) is not None
                    and type(lc.tree) is cls), None)
        entries: list[tuple[tuple[TpyType, object], ...]] = []
        written: object = None
        for t, e in bare:
            if own is not None:
                mine = self.container_cells(t)
                if mine is None or mine.cids != own.cids:
                    if not self._pairs_with(own.tree, t):
                        return None
                    later.append((own.tree, t, e))
                continue
            if written is None and literal_entries(e, cls) is not None:
                written = e
            entries += self._entries(cls, t, e)
        if own is not None:
            return own.tree
        rec = self._nested_record(cls, written, root)
        return self._collect_entries(rec.pending_type(), entries, joined,
                                     path, leaves, rows, later, root)

    def _collect_entries(self, node: PendingContainerType,
                         entries: list[tuple[tuple[TpyType, object], ...]],
                         joined: TpyType | None, path: tuple,
                         leaves: dict[tuple, tuple[bool, list]],
                         rows: dict[tuple, list[ListLiteralInfo]],
                         later: list[tuple[TpyType, TpyType, object]],
                         root: ContainerRecord | None,
                         ) -> TpyType | None:
        """Container node `node` holding `entries` (`_collect` per step:
        the values at each step from every entry)."""
        j = bare_slot(joined)
        j = j if pairs_as(node, j) else None
        children = []
        for i, step in enumerate(node.STEPS):
            c = self._collect([entry[i] for entry in entries],
                              at_path(j, (step,)), path + (step,), leaves,
                              rows, later, root)
            if c is None:
                return None
            children.append(c)
        return node.with_parts(tuple(children))

    def _nested_record(self, cls: type, written: object,
                       root: ContainerRecord | None) -> ContainerRecord:
        """The record of a dict or set (node class `cls`) that is a part of
        container `root`'s tree: a written literal there, or a typed
        container's part. Its cells are `root`'s; it names that part, so a
        read of it finds them."""
        literal_id = self.ctx.literal_counter
        self.ctx.literal_counter += 1
        expr = written if written is not None else (
            root.expr if root is not None else None)
        rec = cls.new_record(
            literal_id, expr,
            part_of=root.literal_id if root is not None else -1)
        self.ctx.container_literals[literal_id] = rec
        return rec

    def _entries(self, cls: type, t: TpyType, e: object,
                 ) -> list[tuple[tuple[TpyType, object], ...]]:
        """The entries of a container value `t` (written `e`) of node class
        `cls`: each a (type, written expression) per step -- a written
        literal's entries, or a typed container's types as one entry."""
        written = literal_entries(e, cls)
        if written is not None:
            out = []
            for exprs in written:
                types = [self.ctx.get_expr_type(x) for x in exprs]
                if any(x is None for x in types):
                    break
                out.append(tuple(zip(types, exprs)))
            else:
                return out
        return [tuple((a, None) for a in container_parts(t, cls))]

    def _born_cell(self, name: str | None, record: ContainerRecord | None,
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
        cell = self.new_elem_cell(name, record, decl, is_float,
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
            if other is not None and other.record is not None:
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

    def _seed(self, info: ContainerRecord, entries: list[Entry],
              site: TpyStmt | TpyExpr | None) -> ContainerCells | None:
        """The cells of empty container `info` (`seedable`), born holding
        `entries` (`new_tree`) and set on every record connected to it: the
        diagnostics name the container's first binding."""
        root = self._root(info)
        tree = self.new_tree(root.variable_name or info.variable_name,
                             root, self.first_binding(info), entries, None,
                             site)
        if tree is None:
            return None
        for rec in self._connected(info):
            self.attach(rec, tree)
        return self.cells_of(info)

    def seed_by_store(self, t: TpyType | None, entry: Entry,
                      site: TpyStmt | TpyExpr) -> ContainerCells | None:
        """An entry stored into empty container `t` (`seedable`) seeds it
        as if it had been written with that entry (`seed_by_stores`)."""
        return self.seed_by_stores(t, [entry], site)

    def seed_by_stores(self, t: TpyType | None, entries: list[Entry],
                       site: TpyStmt | TpyExpr) -> ContainerCells | None:
        """The entries `entries` stored together into empty container `t`
        (`seedable`) seed it as a literal of them does (`new_tree`). None
        when `t` takes no cell or the values hold no number."""
        info = self.seedable(t)
        if info is None or not entries:
            return None
        return self._seed(info, entries, site)

    def unseeded(self, t: TpyType | None, value: TpyExpr | None,
                 value_type: TpyType) -> TpyType:
        """A value stored into empty container `t` was analyzed as a value
        it may take pending (`seedable`); when it seeded no cell, it goes
        to the container as a concrete value."""
        if (self.container_cells(t) is None
                and value_leaves(strip_int(value_type))):
            return self.force_value(value, value_type)
        return value_type

    def store_value(self, t: TpyType | None, entry: Entry,
                    site: TpyStmt | TpyExpr,
                    ) -> tuple[ContainerCells | None, TpyType]:
        """`entry` stored into cell container `t`, born or not
        (`cell_container`): a store into its cells, or the store that seeds
        them. Returns the cells and the type of the entry's last part (a
        list's or set's element, a dict's value) as the container takes
        it."""
        lc = self.container_cells(t)
        if lc is not None:
            return lc, self.tree_store(lc, entry, site)
        vt, value = entry.parts[-1]
        lc = self.seed_by_store(t, entry, site)
        if lc is not None:
            return lc, self.lists_as_known(vt)
        for pt, pe in entry.parts[:-1]:
            self.unseeded(t, pe if is_parse_node(pe) else None, pt)
        return None, self.unseeded(t, value if is_parse_node(value)
                                   else None, vt)

    def tree_store(self, lc: ContainerCells, entry: Entry,
                   site: TpyStmt | TpyExpr) -> TpyType:
        """`entry` stored into cell container `lc` (`_store_at` at each of
        its steps). Returns the type of the entry's last part as the
        container takes it: a literal written there holds what the
        container does."""
        for step, (vt, e) in zip(lc.tree.STEPS, entry.parts):
            self._store_at(lc.tree.part(step), vt, e, site)
        vt, e = entry.parts[-1]
        while isinstance(e, TpyCoerce):
            e = e.expr
        if isinstance(e, (TpyDictLiteral, TpySetLiteral)):
            held = self.ctx.get_expr_type(e)
            if held is not None and tree_leaves(held):
                return held
        return self.lists_as_known(vt)

    def _store_at(self, tree: TpyType, value_type: TpyType | None,
                  value: object, site: TpyStmt | TpyExpr) -> None:
        """`value`, of `value_type`, stored at part `tree` of a container's
        tree: each numeric leaf a store into its cell (`elem_store`), a row
        a store of a list into the row (`_store_row`), a dict or set node
        one per entry of a written literal or typed container, a container
        with cells of its own linked to the part. A part that does not pair
        is left to the ordinary check at the store. `value` names what is
        written at each part, for the messages."""
        e = value
        while isinstance(e, TpyCoerce):
            e = e.expr
        o = bare_slot(value_type)
        if isinstance(tree, PendingNumType):
            self.elem_store(self.ctx.pending_num_cells[min(tree.cells)],
                            o, e if is_parse_node(e) else None, site)
            return
        if o is None or isinstance(o, UnknownElementType):
            return
        if isinstance(tree, PendingListType):
            if pairs_as(tree, o):
                self._store_row(tree, o, site)
            return
        if isinstance(tree, TupleType):
            n = len(tree.element_types)
            if not isinstance(o, TupleType) or len(o.element_types) != n:
                return
            members = (e.elements if isinstance(e, TpyTupleLiteral)
                       and len(e.elements) == n else [None] * n)
            for m, mt, me in zip(tree.element_types, o.element_types,
                                 members):
                self._store_at(m, mt, me, site)
            return
        if not isinstance(tree, PendingContainerType) or not pairs_as(tree, o):
            return
        other = self.container_cells(o)
        if other is not None:
            if other.cids != frozenset(
                    min(lf.cells) for _p, lf in tree_leaves(tree)):
                self.link(tree, other.tree, site)
            return
        written = literal_entries(e, tree)
        if written is not None:
            # Each entry of a literal written there is one store; the
            # literal holds what the part does.
            for exprs in written:
                for step, x in zip(tree.STEPS, exprs):
                    self._store_at(tree.part(step),
                                   self.ctx.get_expr_type(x), x, site)
            self._retype_written(tree, e)
            return
        for step, part in zip(tree.STEPS, container_parts(o, tree)):
            self._store_at(tree.part(step), part, None, site)

    def _store_row(self, row: PendingListType, value_type: TpyType,
                   site: TpyStmt | TpyExpr) -> None:
        """A list stored as a row of the rows `row` names: one of the same
        element and one representation. A list with cells of its own is
        linked to the row's leaf by leaf; a list literal takes the row's
        cells and stores its values there; an empty one takes them as
        they are; a typed container stores its element."""
        group = self.ctx.list_literal(row.literal_id)
        sub = row.element_type
        other = self.list_cells(value_type)
        if other is not None:
            if self.link(row, other.tree, site):
                self.join_rows(group, other.info)
                self._stored_as_row(group, value_type)
            return
        pending = pending_list_of(value_type)
        if pending is not None:
            rec = self.ctx.list_literal(pending.literal_id)
            if rec is None or rec.elem_cells is not None:
                return
            if isinstance(rec.element_type, UnknownElementType):
                empties = (self._connected(rec) if rec.variable_name
                           else [rec])
                for r in empties:
                    self.attach(r, row)
            else:
                written = self._literal_values(rec)
                if written is None or not all(self._pairs_with(sub, t)
                                              for t, _e in written):
                    return
                for t, e in written:
                    self._store_at(sub, t, e, site)
                self.attach(rec, row)
            self.join_rows(group, rec)
            self._stored_as_row(group, value_type)
            return
        bare = bare_slot(value_type)
        if (bare is not None and (is_list(bare) or is_array(bare))
                and group is not None and group.elem_cells is not None):
            # A typed list stored as a row is a typed container the rows
            # meet: no conversion makes one C++ list type of another.
            row_lc = self.cells_of(group)
            if not self.fits_container(row_lc.tree, bare):
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
                else bare_slot(value_type))

    def _stored_as_row(self, group: ListLiteralInfo | None,
                       value_type: TpyType) -> None:
        """The store of a list of `value_type` as a row of `group` is
        admitted (`_store_row`): the store's own check reads the verdict
        (`is_stored_row`) rather than judging the list again."""
        if group is not None:
            group.stored_rows.add(self._row_key(value_type))

    def is_stored_row(self, rows: ContainerCells, value_type: TpyType) -> bool:
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
                return self.as_known(part, lc)
            if isinstance(part, PendingListType):
                # No cell decides this list: its rows are its own.
                return part
            bare = bare_slot(part)
            if bare is not part and isinstance(bare, TupleType):
                new = map_parts(bare, known)
                return part if new is bare else new
            return None
        return map_parts(t, known)

    def store_elements(self, t: TpyType, value: TpyExpr,
                       value_type: TpyType,
                       site: TpyStmt | TpyExpr) -> ContainerCells | None:
        """`value`, analyzed to `value_type`, is an iterable whose every
        entry container `t` then holds (a list's `extend` / `+=`, a dict's
        `update` / `|=`, a set's `update` / `|=` / `^=`): each is a store
        into the container's cells, which an empty one is seeded by. A
        literal written there stores each of its entries; a container whose
        cells decide and a typed container store their parts -- the
        containers inside its entries linked to the receiver's first, then
        its numbers settled (`_link_source`). Returns the cells; None when
        the value says nothing about its entries' types here, or `t` has no
        cell and takes none."""
        into = self.cell_container(t)
        if into is None:
            return None
        root = root_of(into)
        v = value
        while isinstance(v, TpyCoerce):
            v = v.expr
        source: TpyType | None = None
        # A repeat says how many copies it holds, not which values: its
        # element type decides first.
        written = (literal_entries(v, root)
                   if not isinstance(v, TpyListRepeat) else None)
        if written is not None:
            entries = []
            for exprs in written:
                types = [self.ctx.get_expr_type(x) for x in exprs]
                if any(x is None for x in types):
                    return None
                entries.append(Entry(tuple(zip(types, exprs))))
        else:
            other = self.container_cells(value_type)
            if other is not None and type(other.tree) is type(root):
                receiver = self.container_cells(t)
                if receiver is not None:
                    self._link_source(receiver.tree, other.tree, site)
                source = other.tree
                # Its numbers settle before their entries go in: the
                # runtime converts a scalar entry to the receiver's width.
                self.force_list(v, value_type, other, self.describe_use(v))
                parts = self.tree_type(other).parts()
            else:
                container = numeric_container(value_type, root)
                if container is None:
                    return None
                receiver = self.container_cells(t)
                if receiver is not None:
                    self._link_source(receiver.tree, container, site)
                parts = container_parts(container, root)
            entries = [Entry(tuple((p, None) for p in parts))]
        lc = self.container_cells(t)
        if lc is None:
            lc = self.seed_by_stores(t, entries, site)
            if lc is not None and source is not None:
                self._link_source(lc.tree, source, site)
        else:
            for entry in entries:
                self.tree_store(lc, entry, site)
        if lc is not None and written is not None:
            # The literal written there holds what the container does.
            held = bare_slot(value_type)
            rec = (self.record_of(held)
                   if isinstance(held, PendingContainerType) else None)
            if rec is None:
                self._retype_written(lc.tree, v)
            elif rec.elem_cells is None:
                self.attach(rec, lc.tree)
        return lc

    def _link_source(self, tree: PendingContainerType, source: TpyType,
                     site: TpyStmt | TpyExpr) -> None:
        """The entries of `source` (a container's tree with cells, or a
        typed container) go into the container whose tree is `tree`
        (`extend`, `update`, `|=`): a part that is a container of its own
        -- a row, a dict or set inside -- is one C++ type on both sides,
        since the runtime converts no container into another. A part with
        cells is linked to the receiver's leaf by leaf (`_store_row`,
        `link_cells`); a typed dict or set must be what the receiver's part
        is (`context_refusal`). The scalars of the entries themselves are
        left to the store, which converts them."""
        for step in tree.STEPS:
            self._link_part(tree.part(step), at_path(source, (step,)),
                            site, nested=False)

    def _link_part(self, part: TpyType, src: TpyType | None,
                   site: TpyStmt | TpyExpr, nested: bool) -> None:
        o = bare_slot(src)
        if o is None or isinstance(o, UnknownElementType):
            return
        cells = self.ctx.pending_num_cells
        if isinstance(part, PendingNumType):
            if nested and isinstance(o, PendingNumType):
                self.link_cells(cells[min(part.cells)],
                                cells[min(o.cells)], site)
            return
        if isinstance(part, PendingListType):
            # A typed row is the store's to judge (`_store_row`).
            if isinstance(o, PendingListType):
                self._store_row(part, o, site)
            return
        if isinstance(part, TupleType):
            if (isinstance(o, TupleType)
                    and len(o.element_types) == len(part.element_types)):
                for m, om in zip(part.element_types, o.element_types):
                    self._link_part(m, om, site, nested)
            return
        if not isinstance(part, PendingContainerType) or not pairs_as(part, o):
            return
        if tree_leaves(o):
            for step in part.STEPS:
                self._link_part(part.part(step), at_path(o, (step,)),
                                site, nested=True)
            return
        lc = self.container_cells(part)
        if lc is None or numeric_container(o, part) is None:
            return
        refusal = self.context_refusal(lc, o, "stored")
        if refusal is not None:
            raise self.ctx.error(refusal, site)
        self.elem_context(part, o, site, "stored")

    def store_elements_at(self, target: TpyExpr, t: TpyType,
                          value: TpyExpr | None, value_type: TpyType,
                          site: TpyStmt | TpyExpr, use: str,
                          ) -> tuple[TpyType, ContainerCells | None]:
        """`value` stored element by element into cell container `t`, born
        or not, that `target` reads (`store_elements`). An operation that
        stores no values (`value` None), or a value that says nothing about
        its elements' types, is a use that needs the element: `use` decides
        it first. Returns the container as the operation sees it and the
        cells of a store."""
        stored = (self.store_elements(t, value, value_type, site)
                  if value is not None else None)
        if stored is not None:
            return self.so_far(t, stored), stored
        lc = self.container_cells(t)
        if lc is not None:
            t = self.force_list(target, t, lc, use)
            self.ctx.set_expr_type(target, t)
        return t, None

    def seed_by_context(self, t: TpyType, container: TpyType,
                        node: TpyExpr | TpyStmt | None, verb: str,
                        shown: TpyType | None = None) -> TpyType | None:
        """Empty container `t` (`seedable`) meets the typed `container`
        before any store: the container's parts are `t`'s, decided here --
        a cell for each numeric leaf of them outside a nested list, which
        has no list literal of its own to hold one. Returns `t` as the
        container sees it; None when `t` takes no cell."""
        info = self.seedable(t)
        if info is None:
            return None
        root = self._root(info)
        name = root.variable_name or info.variable_name
        decl = self.first_binding(info)

        def leaf(path: tuple, number: TpyType) -> TpyType:
            return self.elem_leaf(self.new_elem_cell(
                name, root, decl, bool(value_family(number)),
                no_base=True, path=path))

        tree = self._declared_tree(info.pending_type(), bare_slot(container),
                                   leaf, (), root)
        if not tree_leaves(tree):
            return None
        for rec in self._connected(info):
            self.attach(rec, tree)
        return self.elem_context(t, container, node, verb, shown)

    def _declared_tree(self, node: PendingContainerType, declared: TpyType,
                       f: Callable[[tuple, TpyType], TpyType], path: tuple,
                       root: ContainerRecord) -> PendingContainerType:
        """Container node `node` holding what typed container `declared`
        does, every number in it outside a nested list -- itself, a tuple
        member, a dict's or set's part -- replaced by `f(path, number)`."""
        return node.with_parts(tuple(
            self._declared_leaves(part, f, path + (step,), root)
            for step, part in zip(node.STEPS,
                                  container_parts(declared, node))))

    def _declared_leaves(self, t: TpyType, f: Callable[[tuple, TpyType], TpyType],
                         path: tuple, root: ContainerRecord) -> TpyType:
        bare = unwrap_send_sync(unwrap_readonly(t))
        if isinstance(bare, TupleType):
            return TupleType(tuple(
                self._declared_leaves(m, f, path + (i,), root)
                for i, m in enumerate(bare.element_types)))
        cls = self._node_class(bare)
        if cls is not None:
            rec = self._nested_record(cls, None, root)
            node = self._declared_tree(rec.pending_type(), bare, f, path,
                                       root)
            rec.set_parts(node)
            return node
        if is_integer_type(bare) or is_float_type(bare):
            return f(path, bare)
        return t

    def meets_list(
            self, into: ContainerCells | ContainerRecord, declared: TpyType,
            verb: str, adaptive: bool = False,
            order: Callable[[tuple[TpyType, ...]], Sequence[TpyType]] | None = None,
    ) -> tuple[TpyType | None, TpyType | None, str | None]:
        """`meets` for cell container `into`, born or not
        (`cell_container`). An empty one meets the first container of its
        kind the slot holds, or, a list, a declared view of numbers
        (`Iterable[int64]`): it has no value a view would convert, so
        nothing there refuses it."""
        if isinstance(into, ContainerCells):
            return self.meets(into, declared, verb, adaptive, order)
        node = into.pending_type()
        containers = self.slot_containers(declared, order, node)
        if containers:
            return containers[0], None, None
        if not isinstance(node, PendingListType):
            return None, None, None
        container, shown = self.compat.deduction.elem_container(
            declared, TypeParamRef("__empty_list_elem"))
        return container, shown, None

    def overload_leaves(self, arg: TpyType, param: TpyType, view: bool
                        ) -> list[tuple[int | None, TpyType,
                                        TpyType]] | None:
        """How an undecided container argument `arg` meets the parameter
        `param` of one overload candidate, without deciding it: None when a
        call to that candidate alone would refuse it (the compatibility
        query), else each numeric type position of its tree as (the id of
        the cell deciding it, None for a literal written in the call; the
        type it holds so far; the type the parameter's container wants
        there). Whether several cells can hold what a candidate wants of
        them together is `wants_fit`.
        `view`: `param` is a view a generic candidate's call resolved, which
        decides the container as a typed one would. A literal leaf holds its
        family's default (`literal_type`); an empty part holds nothing yet
        and is not listed."""
        if not self.compat.is_type_compatible(arg, param,
                                              CoercionContext.ARG):
            return None
        into = self.cell_container(arg)
        order = self.compat.member_order(arg)
        if isinstance(into, ContainerCells):
            container, _shown, refusal = self.meets_list(
                into, param, "passed", adaptive=view, order=order)
            if refusal is not None:
                return None
            if container is None:
                return []
            return [(c.cid, self.known_so_far(c),
                     self.leaf_want(container, p))
                    for c, p in zip(into.cells, into.paths)]
        tree = bare_slot(arg)
        if not isinstance(tree, PendingContainerType):
            return []
        containers = self.slot_containers(param, order, tree)
        if not containers:
            return []
        out: list[tuple[int | None, TpyType, TpyType]] = []
        for _path, part, want in zip_parts(tree, containers[0]):
            if (_node_steps(part) or want is None
                    or value_family(want) is None):
                continue
            if isinstance(part, (IntLiteralType, FloatLiteralType)):
                out.append((None, self.literal_type(part), want))
            elif value_family(part) is not None:
                out.append((None, part, want))
        return out

    def decide_list(self, t: TpyType, into: ContainerCells | ContainerRecord,
                    container: TpyType, node: TpyExpr | TpyStmt | None,
                    verb: str, shown: TpyType | None = None,
                    declared: bool = True) -> TpyType | None:
        """Container `t`, cell container `into` born or not, meets the
        typed `container` (`meets_list` admitted it): its leaves are
        decided there (`elem_context`), an empty one's cells born holding
        the container's (`seed_by_context`). Returns `t` as the container
        sees it."""
        if isinstance(into, ContainerCells):
            return self.elem_context(t, container, node, verb, shown,
                                     declared)
        return self.seed_by_context(t, container, node, verb, shown)

    def bind_container(self, into: ContainerCells | ContainerRecord | None,
                       info: ContainerRecord, name: str,
                       decl: TpyVarDecl | None, entries: list[Entry],
                       joined: TpyType | None,
                       site: TpyStmt | TpyExpr) -> ContainerCells | None:
        """Container literal `info` of the entries `entries` (`new_tree`)
        bound to local `name`, which holds cell container `into`, born or
        not, or none yet (`decl` its binding then). One local, one element
        type: a literal that rebinds a container with cells stores its
        entries there, one that rebinds an empty one nothing seeded yet
        seeds it, as if the empty one had been written with these entries.
        Returns the cells; None when the binding takes none: an empty
        literal, entries with no number, or entries the tree of the
        container rebound does not pair with (an int list rebound to
        floats), which the rebinding refuses in its own words."""
        if isinstance(into, ContainerCells):
            if not all(self._pairs_with(into.tree.part(step), t)
                       for entry in entries
                       for step, (t, _e) in zip(into.tree.STEPS,
                                                entry.parts)):
                return None
            for entry in entries:
                self.tree_store(into, entry, site)
            tree = into.tree
        elif not entries:
            return None
        else:
            if into is not None:
                decl = self.first_binding(into)
            tree = self.new_tree(name, info, decl, entries, joined, site)
            if tree is None:
                return None
        self.attach(info, tree)
        if into is not None and not isinstance(into, ContainerCells):
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
            elif _node_steps(part) and t is None:
                return False
        return True

    def share_cell(self, info: ContainerRecord, tree: TpyType) -> None:
        """Empty container `info` (`seedable`) is rebound to a literal whose
        tree the cells of `tree` decide: one local, one element type, so
        the empty container's records share them."""
        for rec in self._connected(info):
            self.attach(rec, tree)

    def open_list(self, t: TpyType | None) -> bool:
        """Whether `t` is, or holds as a tuple element, a container literal
        whose leaves are not decided yet; an empty one that has no cell yet
        is one (`cell_container`)."""
        lc = self.container_cells(t)
        if lc is not None:
            return not lc.settled
        if self.seedable(t) is not None:
            return True
        bare = bare_slot(t)
        return isinstance(bare, TupleType) and any(
            self.open_list(e) for e in bare.element_types)

    def slot_containers(
            self, declared: TpyType | None,
            order: Callable[[tuple[TpyType, ...]], Sequence[TpyType]] | None = None,
            node: TpyType | type = PendingListType,
    ) -> tuple[TpyType, ...]:
        """The typed containers of numbers a container literal of tree node
        `node`'s kind can meet at a slot declared `declared`: the slot
        itself (`numeric_container`), the list an annotated list local was
        declared as, the member of an optional slot, each such member of a
        union slot -- in the order `order` ranks the union's members, the
        one the coercion tries them in
        (`TypeCompatibility._union_member_order`)."""
        cls = node if isinstance(node, type) else type(node)
        t = bare_slot(declared)
        if isinstance(t, PendingListType):
            info = self.ctx.list_literal(t.literal_id)
            if (cls is not PendingListType or info is None
                    or not info.has_explicit_annotation
                    or info.elem_cells is not None):
                return ()
            return self.slot_containers(info.explicit_type, order, cls)
        if isinstance(t, OptionalType):
            return self.slot_containers(t.inner, order, cls)
        if isinstance(t, UnionType):
            members = order(t.members) if order is not None else t.members
            return tuple(c for m in members
                         for c in self.slot_containers(m, order, cls))
        container = numeric_container(t, cls)
        return (container,) if container is not None else ()
    def meets(self, lc: ContainerCells, declared: TpyType, verb: str,
              adaptive: bool = False,
              order: Callable[[tuple[TpyType, ...]], Sequence[TpyType]] | None = None,
              ) -> tuple[TpyType | None, TpyType | None, str | None]:
        """How the container of cells `lc` meets a slot declared
        `declared`: (container, shown, refusal). The first of the slot's
        containers (`slot_containers`, a union's members in `order`) whose
        parts the container's pair with (`fits_container`) and that admits
        it (`context_refusal`); the refusal of a slot whose one such
        container does not; all None for a slot with no container, or with
        several that all refuse, which the ordinary check reports.
        `adaptive` is an argument the enclosing generic call left adaptive,
        at a parameter that call resolved: there a view of a list
        (`Iterable[T]`) is a container too, and `shown` is the view the
        source spells."""
        if adaptive and isinstance(lc.tree, PendingListType):
            container, shown = self.compat.deduction.elem_container(
                declared, lc.tree.element_type)
            found = [(container, shown)] if container is not None else []
        elif adaptive:
            container = numeric_container(declared, lc.tree)
            found = [(container, None)] if container is not None else []
        else:
            found = [(c, None) for c in
                     self.slot_containers(declared, order, lc.tree)]
        found = [(c, s) for c, s in found
                 if self.fits_container(lc.tree, c)]
        refusals = []
        for container, shown in found:
            refusal = self.context_refusal(lc, container, verb, shown,
                                           adaptive)
            if refusal is None:
                return container, shown, None
            refusals.append(refusal)
        return None, None, refusals[0] if len(refusals) == 1 else None

    def fits_container(self, tree: TpyType, want: TpyType | None) -> bool:
        """Whether tree `tree` pairs with a container's element (a dict's or
        set's own type) `want` part by part: a numeric leaf with a number
        (of either family: the family refusal names that one), a tuple with
        a tuple of its arity, a row with a list, Span or Array, a dict or
        set node with one of its kind; a part that holds no number must be
        compatible as it is."""
        for _path, part, w in zip_parts(tree, want):
            if isinstance(part, PendingNumType):
                if value_family(w) is None:
                    return False
            elif _node_steps(part):
                if w is None:
                    return False
            elif w is None or not self.compat.is_type_compatible(part, w):
                return False
        return True

    def known_as(self, lc: ContainerCells, t: TpyType) -> bool:
        """Whether the container of cells `lc`, at the types known so far,
        is `t`: each leaf the number there, each nested list a list, every
        other part the same type."""
        cells = self.ctx.pending_num_cells
        for path, part, o in zip_parts(lc.tree, t):
            if isinstance(part, PendingNumType):
                if self.known_so_far(cells[min(part.cells)]) != o:
                    return False
            elif isinstance(part, PendingListType) and path:
                if o is None or not is_list(o):
                    return False
            elif isinstance(part, (TupleType, PendingContainerType)):
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

    @staticmethod
    def declared_form(t: TpyType) -> TpyType:
        """`t` with every dict or set literal in it the dict or set it
        spells; a list literal stays one, whose storage its record
        decides."""
        def spell(_path: tuple, part: TpyType) -> TpyType | None:
            if isinstance(part, (PendingDictType, PendingSetType)):
                return part.spelled(tuple(map_parts(p, spell)
                                          for p in part.parts()))
            return None
        return map_parts(t, spell)

    def receiver_form(self, t: TpyType, lc: ContainerCells) -> TpyType:
        """The container type `t` as a method called on it is resolved at:
        its settled form once its cells have settled, else its parts as
        the cells have them so far with the dicts and sets inside them the
        containers they spell -- what the method hands out of them is a
        value, not the container's part (the signature is recorded again
        once the cells settle, `when_elem_known`)."""
        if lc.settled:
            return self.settled_form(t, lc)
        tree = self.tree_type(lc)
        return with_container(t, tree.with_parts(tuple(
            self.declared_form(p) for p in tree.parts())))

    def settled_form(self, t: TpyType, lc: ContainerCells) -> TpyType:
        """The container type `t`, whose cells `lc` have all settled, as a
        consumer that does not name it takes it: a dict or set the
        container its settled parts spell -- nothing about it is open any
        more -- and a list a list literal still, whose storage its record
        decides."""
        return with_container(t, self.declared_form(self.tree_type(lc)))

    def tree_type(self, lc: ContainerCells) -> TpyType:
        """The element of cell list `lc` as its cells have it: each leaf
        the settled type, else the pending one."""
        cells = self.ctx.pending_num_cells
        return map_leaves(lc.tree, lambda _p, leaf: self.cell_type(
            cells[min(leaf.cells)]))

    def tree_known(self, lc: ContainerCells) -> TpyType:
        """The element of cell list `lc` at the types known so far; asks
        nothing to settle."""
        cells = self.ctx.pending_num_cells
        return map_leaves(lc.tree, lambda _p, leaf: self.known_so_far(
            cells[min(leaf.cells)]))

    def as_known(self, t: TpyType, lc: ContainerCells) -> TpyType:
        """The container type `t` with its leaves as its cells have them:
        the settled types, else the pending ones."""
        return with_container(t, self.tree_type(lc))

    def adaptive_view(self, t: TpyType, lc: ContainerCells) -> TpyType:
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
                     if cell.path == (ELEM,)
                     and isinstance(lc.tree, PendingListType)
                     and getattr(init, "elements", None) else None)
            return (first if isinstance(first, literal)
                    and self.literal_type(first) == elem else literal())
        return with_container(t, map_leaves(lc.tree, view))

    def so_far(self, t: TpyType, lc: ContainerCells) -> TpyType:
        """The container type `t` at the types known so far; asks nothing
        to settle. For a reader that only inspects the type."""
        return with_container(t, self.tree_known(lc))

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
        elif cell.record is None or not any(
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

    def _joined(self, cells: list[PendingNumCell],
                wants: Mapping[int, TpyType] | None = None,
                ) -> tuple[dict[int, TpyType | None],
                           tuple[PendingNumCell, list[TpyType | None]] | None]:
        """The types `cells` (a `_reach` set) come to from their evidence:
        a fixed point, since cells joined through one another's stores
        (`i = j + 1`, `j = i`) go up together until nothing changes.
        `wants` gives some of them one more value each. Returns the types
        and, when a cell's values have no common type, that cell with its
        values (the types are then as far as the walk got)."""
        cur: dict[int, TpyType | None] = {c.cid: None for c in cells}
        changed = True
        while changed:
            changed = False
            for cell in cells:
                values = self._values(cell, cur, set())
                want = wants.get(cell.cid) if wants is not None else None
                t = lub_int([self._base(cell)] + values
                            + ([want] if want is not None else []))
                if t is NO_COMMON:
                    return cur, (cell, values)
                if t != cur[cell.cid]:
                    cur[cell.cid] = t
                    changed = True
        return cur, None

    def wants_fit(self, wants: Mapping[int, TpyType]
                  ) -> dict[int, int] | None:
        """Whether the cells `wants` names (by id) can be decided at the
        types it gives them TOGETHER, deciding nothing: settled as the
        settle walk would settle them with each want as one more value,
        every one comes out at its want and every later store checked
        against a cell (`must_fit`) still fits it. Cells a store ties --
        both ways (a local rebound to another, a row stored into a nested
        list) or one way (an element of one appended to the other) --
        cannot be wanted at types one of them would be pushed past. None
        when they cannot; else each wanted cell's group: the smallest id
        among the wanted cells tied to it both ways, which hold one type
        and widen as one."""
        cells = self._reach(set(wants))
        cur, clash = self._joined(cells, wants)
        if clash is not None:
            # Values that clash whatever is wanted are the deciding call's
            # refusal, in its words, not a reason against this candidate.
            if self._joined(cells)[1] is None:
                return None
        else:
            for cell in cells:
                t = cur[cell.cid]
                held = t if t is not None else self.default_type(cell.is_float)
                if wants.get(cell.cid, held) != held:
                    return None
                for ft, _node in cell.must_fit:
                    v = self._eval(ft, cur, set())
                    if (isinstance(v, TpyType)
                            and not isinstance(v, (IntLiteralType,
                                                   FloatLiteralType))
                            and join_int(held, v) != held):
                        return None
        reach = {cid: {c.cid for c in self._reach({cid})} for cid in wants}
        return {cid: min(o for o in wants
                         if o == cid or (o in reach[cid] and cid in reach[o]))
                for cid in wants}

    def settle(self, cids: set[int] | frozenset[int],
               use: TpyExpr | TpyStmt | None = None, what: str | None = None,
               via: str | None = None) -> None:
        """Decide the cells `cids` depend on from their evidence so far. With
        `use`, the settle is a use that needed the type on the spot, and a
        later wider store names it."""
        cells = self._reach(cids)
        if not cells:
            return
        cur, clash = self._joined(cells)
        if clash is not None:
            # Arms that bind together are refused in their own words.
            self.check_arm_group(clash[0], cur)
            self._raise_no_common(*clash)
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
        if cell.record is not None:
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
        if cell.record is not None:
            raise self.ctx.error(
                f"'{cell.name}' holds {self._elem_spelled(cell, held)} "
                f"{self._noun(cell)} and this value is {python_type_name(t)}"
                f"{self._member(cell)}, which have no common "
                f"type; annotate its first binding: {cell.name}: "
                f"{self._container_spelled(cell, smallest_signed_holding(unsigned))}"
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
        """What a refusal says the container of element cell `cell` holds,
        and since which use when one decided it early."""
        head = (f"'{cell.name}' holds {self._elem_spelled(cell)} "
                f"{self._noun(cell)}")
        if cell.no_base and cell.context is None:
            # The line that decided it: the first binding, or the first
            # store into a container first bound empty.
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

    @staticmethod
    def _kind(cell: PendingNumCell) -> str:
        return cell.record.kind if cell.record is not None else "list"
    def _noun(self, cell: PendingNumCell) -> str:
        """What a refusal calls the part of the container a leaf cell is
        in: a list's or set's elements, a dict's keys or values."""
        step = cell.path[0] if cell.path else ELEM
        return {KEY: "keys", VALUE: "values"}.get(step, "elements")

    def _held_part(self, cell: PendingNumCell) -> tuple[TpyType, tuple]:
        """The part of its container's tree a refusal spells for leaf cell
        `cell` -- a list's element, a dict's key or value, a set's element
        -- and the cell's path in that part."""
        tree = self.record_tree(cell.record)
        return at_path(tree, cell.path[:1]), cell.path[1:]

    def _elem_annotation(self, cell: PendingNumCell, t: TpyType,
                         spelled: str | None = None) -> str:
        """The fix a refusal about a container's leaf names: the annotation
        of the container's first binding that holds `t` and what it holds
        now."""
        if spelled is None:
            wide = join_int(self.known_so_far(cell), t)
            # Signed and unsigned values with no fixed type in common fit
            # an `int`.
            if not isinstance(wide, TpyType):
                wide = FLOAT if cell.is_float else BIGINT
            spelled = self._container_spelled(cell, wide)
        return (f"annotate its first binding: {cell.name}: {spelled} = "
                f"{self._list_init(cell)}")

    def _spelled_tree(self, cell: PendingNumCell, tree: TpyType,
                      at: TpyType | None) -> TpyType:
        """Tree `tree` of the container of leaf cell `cell` with every leaf
        at the type known so far, this cell's at `at` when given, and
        every row a list."""
        cells = self.ctx.pending_num_cells

        def leaf(_path: tuple, t: PendingNumType) -> TpyType:
            c = cells[min(t.cells)]
            return at if c is cell and at is not None else self.known_so_far(c)
        return _spelled_rows(map_leaves(tree, leaf))

    def _elem_spelled(self, cell: PendingNumCell,
                      at: TpyType | None = None) -> str:
        """The part of the container whose leaf cell `cell` decides
        (`_held_part`), as a diagnostic spells it: every leaf at the type
        known so far, this cell's at `at` when given."""
        info = cell.record
        if info is None or info.elem_cells is None:
            return python_type_name(at if at is not None
                                    else self.known_so_far(cell))
        part, rest = self._held_part(cell)
        if not rest:
            return python_type_name(at if at is not None
                                    else self.known_so_far(cell))
        return python_type_name(self._spelled_tree(cell, part, at))

    def _container_spelled(self, cell: PendingNumCell,
                           at: TpyType | None = None) -> str:
        """The type of the whole container whose leaf cell `cell` decides,
        as an annotation spells it, this cell's leaf at `at`."""
        info = cell.record
        if info is None or info.elem_cells is None:
            elem = python_type_name(at if at is not None
                                    else self.known_so_far(cell))
            return f"{self._kind(cell)}[{elem}]"
        return python_type_name(
            self._spelled_tree(cell, self.record_tree(info), at))

    def _member(self, cell: PendingNumCell) -> str:
        """Where in the part a refusal names a leaf cell is, as it adds
        it."""
        if cell.record is None:
            return ""
        part, rest = self._held_part(cell)
        return f" ({path_words(part, rest)})" if rest else ""

    def _list_init(self, cell: PendingNumCell) -> str:
        """The first binding of the container of element cell `cell` as an
        annotation hint spells it: `[]` / `{}` / `list()` / `set()` for an
        empty one."""
        init = cell.first_decl.init if cell.first_decl is not None else None
        if isinstance(init, TpyArrayLiteral) and not init.elements:
            return "[]"
        if isinstance(init, TpyDictLiteral) and not init.keys:
            return "{}"
        if isinstance(init, TpyCall) and not init.args and not init.kwargs:
            return f"{init.func_name}()"
        return "[...]" if self._kind(cell) == "list" else "{...}"

    def _unfit_value(self, cell: PendingNumCell, t: TpyType) -> str | None:
        """The first value the container was first bound to that a leaf of
        type `t` would not hold, as the source spells it; None when `t`
        holds them all, so an annotation at `t` is a fix to name."""
        init = cell.first_decl.init if cell.first_decl is not None else None
        written = [init] if init is not None else []
        values = [*_at_leaf(written, cell.path), *cell.stored_literals]
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
        """The refusal of an int meeting a float at a leaf of a container:
        a container literal keeps the numeric family its values are written
        in."""
        mine = self._elem_family(cell)
        floats = other if cell.is_float is False else mine
        mix = self.compat.deduction.int_float_mix(mine, other)
        if mix is None:
            return (f"'{cell.name}' holds {self._elem_spelled(cell, mine)} "
                    f"{self._noun(cell)} and this value is "
                    f"{python_type_name(other)}{self._member(cell)}")
        noun = self._noun(cell)
        part = "" if noun in ("elements", "values") else f" {noun}"
        return usage_mix_message(
            mix, f"{self._kind(cell)} '{cell.name}'{part}{self._member(cell)}",
            f"{cell.name}: {self._container_spelled(cell, floats)} = "
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
        return (f"{head}; the {self._kind(cell)} is {verb} as {container}, "
                f"so the value must be {python_type_name(cell.settled)}")

    def _annotated(self, container: TpyType, cell: PendingNumCell,
                   leaf: TpyType) -> str:
        """The annotation of the container of leaf cell `cell` that meets
        the typed `container` holding `leaf` at the cell's leaf: a
        container of one of its rows names the whole element
        (`g: list[list[int32]]`, not the row's `list[int32]`); a view takes
        a list, so the list is what to annotate. A dict or set names
        itself."""
        if self._kind(cell) != "list":
            return self._container_spelled(cell, leaf)
        if cell.path[1:] and value_family(
                at_path(self.context_elem(container), cell.path)) is None:
            return f"list[{self._elem_spelled(cell, leaf)}]"
        if is_list(container) or is_array(container):
            return str(container)
        return f"list[{python_type_name(at_path(container, (ELEM,)))}]"

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
                   lc: ContainerCells, what: str) -> TpyType:
        """Settle the element of list `t`, every leaf of it: `expr` is a
        use that needs it now. Returns the list with its element decided."""
        if not lc.settled:
            self.settle({c.cid for c in lc.cells if c.settled is None},
                        use=expr, what=what)
            self.resolve_ready()
        return self.as_known(t, lc)

    @staticmethod
    def context_elem(container: TpyType) -> TpyType:
        """A typed container of numbers as a container's tree pairs with
        it: itself, without its qualifiers."""
        return unwrap_send_sync(unwrap_readonly(container))
    def context_refusal(self, lc: ContainerCells, container: TpyType,
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
        """What typed `container` holds at leaf path `path`."""
        part = at_path(self.context_elem(container), path)
        assert part is not None, "the container pairs with the tree"
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
        """The container `t` meets the typed `container`, which
        `context_refusal` admitted: the container's leaves are more values
        `t` holds, and its leaves are decided here. `verb` says what `t` is
        there (`passed`). `declared` is False for a generic parameter the
        call resolved: an annotation of `t` would resolve it otherwise, so
        it is no fixed type later refusals have to respect. Returns `t` as
        the container sees it."""
        lc = self.container_cells(t)
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
        self._adopt_parts(lc, self.context_elem(container))
        return self.as_known(t, self.container_cells(t) or lc)

    def _adopt_parts(self, lc: ContainerCells, want: TpyType) -> None:
        """The parts of the tree of `lc` that hold no number take the typed
        container's types there (`fits_container` found them compatible: a
        view member stored as the owned string the container holds), on
        every record that names this tree."""
        held = {path: w for path, part, w in zip_parts(lc.tree, want)
                if w is not None and not pending_leaves(part)
                and not _node_steps(part)}
        new = map_parts(lc.tree, lambda path, _part: held.get(path))
        if new == lc.tree:
            return
        info = lc.info
        if not isinstance(lc.tree, PendingListType):
            if info.part_of is None:
                self.attach(info, new)
                return
            # A dict or set inside another container is that container's
            # part: its tree is where the parts change.
            outer = self.ctx.container_record(info.part_of)
            if outer is not None and outer.elem_cells is not None:
                lid = info.literal_id
                self.attach(outer, map_parts(
                    outer.pending_type(), lambda _p, n: (
                        new if isinstance(n, PendingContainerType)
                        and n.literal_id == lid else None)))
            return
        old_parts = _row_parts(lc.tree)
        new_parts = _row_parts(new)
        for lid in self.ctx.func.pending_resolutions:
            rec = self.ctx.list_literal(lid)
            if rec is None or rec.elem_cells is None:
                continue
            for old_part, new_part in zip(old_parts, new_parts):
                if rec.element_type == old_part:
                    self.attach(rec, rec.pending_type().with_parts(
                        (new_part,)))
                    break

    def _rows_at_container(self, t: TpyType, tree: TpyType,
                           container: TpyType) -> None:
        """A row of a nested list that meets a `list` there is stored as
        one, with every row of its group (`join_rows`): the row `t` itself
        when it is one, and each row of `tree` the container holds a list
        at."""
        rows = [pending_list_of(t)] if is_list(container) else []
        rows += [part for path, part, w in
                 zip_parts(tree, self.context_elem(container))
                 if path and isinstance(part, PendingListType)
                 and w is not None and is_list(w)]
        for row in rows:
            rec = (self.ctx.list_literal(row.literal_id)
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
            words = " and ".join(_STEP_WORDS[s] for s in
                                 cell.record.pending_type().STEPS)
            one = f"one {words} type"
            return (f"'{cell.name}' is {first_verb} as {first}{at} and "
                    f"{again}as {here} here; a {self._kind(cell)} has {one}")
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
        if t is not None and not isinstance(inner, PendingContainerType):
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
                     what: str, container_what: str | None = None) -> None:
        """Settle the pending locals among `names`: a body analyzed in a
        state of its own reads them (a nested def, a lambda, a generator
        expression), so their type has to be known before it is.
        `container_what` names the use of a container among them, which
        the body may write into as well as read, when it differs."""
        cids = {cid for n, cid in self.ctx.func.pending_cell_of.items()
                if n in names
                and self.ctx.pending_num_cells[cid].settled is None}
        for cid in cids:
            self.settle({cid}, use=use, what=what)
        settled = bool(cids)
        cids = set()
        # A container the body reads has its leaves decided the same way.
        scope = self.ctx.func.current_scope
        for n in names:
            info = self.ctx.list_literal(
                self.ctx.func.variable_to_literal.get(n, -1))
            lc = (self.cells_of(info)
                  if info is not None and info.elem_cells is not None
                  else self.container_cells(scope.lookup(n))
                  if scope is not None else None)
            if lc is not None:
                cids |= {c.cid for c in lc.cells if c.settled is None}
        for cid in cids:
            self.settle({cid}, use=use, what=container_what or what)
        if settled or cids:
            self.resolve_ready()

    # ------------------------------------------------------------------
    # Deferred resolution
    # ------------------------------------------------------------------

    def when_elem_known(self, node: TpyExpr | TpyStmt, lc: ContainerCells,
                        record: Callable[[TpyType], None]) -> None:
        """Call `record` with the container of cells `lc` once its cells
        settle -- a signature recorded at the parts the call met -- and
        again once the rows in it, list literals resolved after the settle,
        have their list types."""
        def resolve(types: tuple[TpyType, ...]) -> None:
            tree = types[0]
            record(self.declared_form(tree))
            parts = tree.parts()
            if any(contains_pending_leaf(p) for p in parts):
                # The parts, resolved, go back into the container's tree.
                self.ctx.func.after_list_resolution.append((
                    TupleType(parts),
                    lambda done: record(self.declared_form(
                        tree.with_parts(done.element_types)))))
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

    def part_leaf(self, lc: ContainerCells, step: str, arg: TpyExpr,
                  t: TpyType) -> PendingNumType | None:
        """The leaf an argument of type `t` naming part `step` of cell
        container `lc` is passed at -- a method's part argument, the
        operand of `in`: the part when it is a number not settled yet and
        `t` is of its family. A container keeps the numeric family its
        values are written in, a looked-up one included: another family is
        refused here. None when the argument goes in as itself."""
        part = at_path(lc.tree, (step,))
        if not isinstance(part, PendingNumType):
            return None
        family = value_family(strip_int(t))
        if family is not None and family != part.is_float:
            cell = self.ctx.pending_num_cells[min(part.cells)]
            raise self.ctx.error(
                self.elem_mix_message(cell, strip_int(t), arg), arg)
        if family == part.is_float and not self.settled_all(part):
            return part
        return None

    def lookup_leaf(self, lc: ContainerCells | None, step: str | None,
                    arg: TpyExpr, t: TpyType) -> PendingNumType | None:
        """The leaf a value of type `t` looked up at part `step` of cell
        container `lc` is passed at (`part_leaf`) when the lookup left the
        container open (`lookup_decides`). None for no cells, no part, or a
        part already settled: a compared value meets a decided part as it
        is, with no family check of its own."""
        if lc is None or step is None or not self.open_part(lc, step):
            return None
        return self.part_leaf(lc, step, arg, t)

    def open_part(self, lc: ContainerCells, step: str) -> bool:
        """Whether part `step` of cell container `lc` has a leaf not
        settled yet."""
        part = at_path(lc.tree, (step,))
        return part is not None and not all(
            self.settled_all(leaf) for _p, leaf in tree_leaves(part))

    def lookup_decides(self, lc: ContainerCells, step: str,
                       t: TpyType) -> bool:
        """Whether a value of type `t` looked up at part `step` of cell
        container `lc` (`x in c`, `d[k]`, `del d[k]`, `d.get(k)`,
        `s.discard(x)`, `xs.count(x)`) decides the container first. A
        value the part holds losslessly so far -- a literal that fits it, a
        typed value of its family no wider -- is passed at its leaf and
        leaves it open (`part_leaf`). Any other -- a wider width, an `int`,
        the other family, a pending local, a part that is no single number
        -- decides the container, and the lookup then compares with what it
        holds: a lookup never converts its operand down."""
        if not self.open_part(lc, step):
            return False
        part = at_path(lc.tree, (step,))
        if not isinstance(part, PendingNumType):
            return True
        bare = strip_int(t)
        if (value_family(bare) != part.is_float
                or isinstance(bare, PendingNumType)):
            return True
        known = self.known_type(part)
        if isinstance(bare, IntLiteralType):
            tr = int_traits_of(known)
            return tr is not None and not (
                tr.min_value <= bare.value <= tr.max_value)
        if isinstance(bare, FloatLiteralType):
            return False
        return lub_int([known, bare]) != known

    def pass_at_leaf(self, arg: TpyExpr, t: TpyType, leaf: PendingNumType,
                     context: str) -> TpyExpr:
        """`arg`, of type `t`, passed at pending leaf `leaf`
        (`part_leaf`): a literal adapts to it, as a literal stored into a
        pending local does; a typed value's conversion to it is judged once
        it settles."""
        if isinstance(t, (IntLiteralType, FloatLiteralType)):
            return arg
        return self.coerce(arg, strip_int(t), leaf, context,
                           CoercionContext.ARG)

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
        if isinstance(t, PendingContainerType):
            return t
        return t.map_inner_types(self.finalize_values)

    def drop_cells(self) -> None:
        """Forget the function's cells once nothing refers to them."""
        for cid in self._cids():
            self.ctx.pending_num_cells.pop(cid, None)
        self.ctx.func.pending_cell_of = {}
        self.ctx.func.pending_elem_cids = []


def _row_parts(tree: TpyType) -> list[TpyType]:
    """The element of `tree`, when it is a list, and of every list nested
    in it, outermost first."""
    out: list[TpyType] = []

    def visit(_path: tuple, part: TpyType) -> None:
        if isinstance(part, PendingListType):
            out.append(part.element_type)
    map_parts(tree, visit)
    return out


def _spelled_rows(t: TpyType) -> TpyType:
    """`t` with every container literal in it the container it spells."""
    def spell(_path: tuple, part: TpyType) -> TpyType | None:
        if isinstance(part, PendingContainerType):
            return part.spelled(tuple(map_parts(p, spell)
                                      for p in part.parts()))
        return None
    return map_parts(t, spell)


def _at_leaf(elements: list[TpyExpr], path: tuple) -> list[TpyExpr]:
    """The expressions written at leaf path `path` of the written values
    `elements`: through tuple literals' members, list and set literals'
    elements and dict literals' keys or values."""
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
            elif step in (KEY, VALUE):
                if isinstance(e, TpyDictLiteral):
                    nxt.extend(e.keys if step == KEY else e.values)
            elif isinstance(e, (TpyArrayLiteral, TpyListRepeat,
                                TpySetLiteral)):
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
