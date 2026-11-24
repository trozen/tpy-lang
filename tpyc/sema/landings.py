"""The union member a committing check lands a value in, keyed by the
boundary that receives the value.

A value meeting a union slot lands in one member; sema decides which (the
union arm of `TypeCompatibility._check_compat`) and records it here, and
the conversion that builds the union at that place reads the record --
nothing after sema chooses a member. The key is the RECEIVING boundary,
not the value: one value can meet two boundaries with different members
(`m[key()] += 1` reads at the getter's key slot and stores at the
setter's).

A boundary is the parse node holding the position (`holder`) and the
position's structural role in it: the field and index the value occupies
(`("value", None)`, `("elements", 2)`, `("setitem_index", None)`), except
a call argument, keyed by the callee PARAMETER index (`("param", k)`)
since `relayout` renumbers a call's arguments. Inside the value an element
path (`(i,)`, `(i, j)`) addresses a tuple's elements, so a tuple literal's
elements are addressed through the boundary receiving the tuple.

Tables are `IdentityMap[holder] -> {role: {path: member}}`: identity on the
holder node, value equality on the rest. Sema's per-body table lives on
`FunctionTrackingState.union_landings` (rolled back with a trial); the
body's settlement publishes it into `SemanticContext.union_landings`, which
the lowering reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..identity_map import IdentityMap
from ..parse import TpyVarDecl
from ..typesys import (BOOL, FLOAT, FLOAT32, LiteralType, NoneType,
                       OptionalType,
                       RecursiveAliasInstanceType, TpyType, UnionType,
                       contains_type_param, cpp_alternative,
                       members_share_alternative,
                       repr_shape, unwrap_own, unwrap_readonly)
from ..value_category import ElemPath

if TYPE_CHECKING:
    from ..typesys import TypeRegistry
    from .context import SemanticContext

Role = tuple[str, object]
# The members a boundary's value landed in, per element path.
Members = dict[ElemPath, TpyType]
LandingTable = IdentityMap


class PublishedLandings(IdentityMap):
    """The module's published table, which also notes each member the
    lowering applies (`lookup`, and `.get` on what it returns): a published
    member never applied is a position whose C++ picks the member some other
    way (`unread`). A route decision reads the table without a mark
    (`peek`)."""
    __slots__ = ("reads",)

    def __init__(self) -> None:
        super().__init__()
        # holder -> {(role, path)} applied
        self.reads: IdentityMap = IdentityMap()

    def mark(self, holder: object, role: Role, path: ElemPath) -> None:
        _add(self.reads, holder, (role, path))

    def applied(self, holder: object, role: Role) -> bool:
        """Whether a conversion applied a member of the boundary."""
        return any(key[0] == role for key in self.reads.get(holder, ()))

    def move_marks(self, old_holder: object, old_role: Role,
                   new_holder: object, new_role: Role) -> None:
        read = self.reads.get(old_holder)
        if read is not None:
            for key in [k for k in read if k[0] == old_role]:
                read.discard(key)
                _add(self.reads, new_holder, (new_role, key[1]))

    def unread(self) -> 'list[tuple[Boundary, ElemPath]]':
        """Every published (boundary, element path) whose member no
        conversion applied."""
        return [(Boundary(holder, role), path)
                for holder, entry in self.items()
                for role, members in entry.items()
                for path in members
                if (role, path) not in self.reads.get(holder, ())]

def _add(table: IdentityMap, holder: object, key: object) -> None:
    keys = table.get(holder)
    if keys is None:
        keys = set()
        table[holder] = keys
    keys.add(key)


class _Applied(Mapping):
    """A published boundary's members as the conversion applying them
    reads them: each element path's member it reads is marked applied."""
    __slots__ = ("_members", "_table", "_holder", "_role")

    def __init__(self, members: Members, table: PublishedLandings,
                 holder: object, role: Role) -> None:
        self._members = members
        self._table = table
        self._holder = holder
        self._role = role

    def __getitem__(self, path: ElemPath) -> TpyType:
        member = self._members[path]
        self._table.mark(self._holder, self._role, path)
        return member

    def __iter__(self):
        return iter(self._members)

    def __len__(self) -> int:
        return len(self._members)


@dataclass(frozen=True, eq=False)
class Boundary:
    """The place that receives and converts a value: its holder node and
    the role of the position in it. A carrier only: tables key on the
    holder's identity and the role's value, never on a Boundary object."""
    holder: object
    role: Role


def boundary_of(holder: object, field: str,
                index: object = None) -> Boundary:
    """The boundary of the value `holder` holds in `field` (at `index` of
    a list field)."""
    return Boundary(holder, (field, index))


def param_boundary(call: object, k: int) -> Boundary:
    """The boundary of the argument a call hands its callee's parameter
    `k` (the parameter index, never the argument's position)."""
    return Boundary(call, ("param", k))


def binding_boundary(node: object) -> 'Boundary | None':
    """The boundary of the value a binding receives: a declaration's
    `init`, an assignment's or walrus's `value`."""
    if node is None:
        return None
    return boundary_of(node, "init" if isinstance(node, TpyVarDecl)
                       else "value")


def subscript_boundary(sub: object, dunder: str, position: int, *,
                       value: bool = False) -> Boundary:
    """The boundary of operand `position` of subscript `sub` received by
    its `dunder` (`__getitem__`, `__setitem__`, `__delitem__`): the key
    (position 0), a further argument, or the stored value. The dunder is
    part of the role, so a read-modify-write's getter and setter keys are
    two boundaries of one subscript."""
    if value:
        return Boundary(sub, ("stored", dunder))
    if position == 0:
        return Boundary(sub, ("index", dunder))
    return Boundary(sub, ("args", (dunder, position - 1)))


def arg_boundary(call: object, k: int) -> 'Boundary | None':
    """`param_boundary` where the call and the parameter are known (a
    lowering site may have neither)."""
    if call is None or k < 0:
        return None
    return param_boundary(call, k)


DEFAULT_ROLE: Role = ("default", None)


def default_boundary(default_expr: object) -> Boundary:
    """A default's boundary: the default expression node itself, stable
    across signature rebuilds and copied with each method-expansion
    clone."""
    return Boundary(default_expr, DEFAULT_ROLE)


class UnionLanding:
    """The members one committing check lands its value in, per element
    path, written by the check's union arm. `at(i)` is the landing of
    element `i` of the same value (the tuple arm hands it down)."""
    __slots__ = ('members', 'path', 'boundary')

    def __init__(self, boundary: 'Boundary | None' = None,
                 members: 'Members | None' = None,
                 path: ElemPath = ()) -> None:
        self.boundary = boundary
        self.members: Members = {} if members is None else members
        self.path = path

    def at(self, i: int) -> 'UnionLanding':
        return UnionLanding(self.boundary, self.members, self.path + (i,))

    def record(self, member: TpyType) -> None:
        self.members[self.path] = member


def register(table: LandingTable, boundary: Boundary,
             members: Mapping[ElemPath, TpyType]) -> None:
    """The boundary's entry REPLACED whole by `members`: the last committed
    check of a boundary wins, and one that landed nothing clears it."""
    if isinstance(table, PublishedLandings):
        members = _without_absence(members)
    entry = table.get(boundary.holder)
    if not members:
        if entry is not None:
            entry.pop(boundary.role, None)
        return
    if entry is None:
        entry = {}
        table[boundary.holder] = entry
    entry[boundary.role] = dict(members)


def publish(body: LandingTable, published: LandingTable) -> None:
    """A settled body's entries into the module's table -- but for `None`,
    whose member is the absence every union spells one way: no conversion
    reads it (the body's own table keeps it for declaration narrowing)."""
    for holder in body:
        for role, members in body[holder].items():
            register(published, Boundary(holder, role), members)


def _without_absence(members: Mapping[ElemPath, TpyType]) -> Members:
    return {path: m for path, m in members.items()
            if not isinstance(m, NoneType)}


def peek(table: LandingTable,
         boundary: 'Boundary | None') -> 'Mapping[ElemPath, TpyType] | None':
    """The members recorded at `boundary`, None for none, for a reader
    that only decides a route: nothing is marked applied."""
    if boundary is None:
        return None
    entry = table.get(boundary.holder)
    if entry is None:
        return None
    return entry.get(boundary.role)


def peek_member(table: LandingTable,
                boundary: 'Boundary | None') -> 'TpyType | None':
    """`peek` of the member a whole value landed in."""
    members = peek(table, boundary)
    return members.get(()) if members is not None else None


def lookup(table: LandingTable,
           boundary: 'Boundary | None') -> 'Mapping[ElemPath, TpyType] | None':
    """The members recorded at `boundary`, None for none, for the
    conversion that applies them: each path's member it reads from the
    published table is marked applied."""
    members = peek(table, boundary)
    if members is not None and isinstance(table, PublishedLandings):
        return _Applied(members, table, boundary.holder, boundary.role)
    return members


def member_at(table: LandingTable,
              boundary: 'Boundary | None') -> 'TpyType | None':
    """The member a whole value landed in at `boundary`, None for none,
    marked applied (`lookup`)."""
    members = lookup(table, boundary)
    return members.get(()) if members is not None else None


def move_landing(table: LandingTable, old_holder: object, old_role: Role,
                 new_holder: object, new_role: Role) -> None:
    """A value already checked at one boundary now sits at another (a
    synthesized call adopting it, a decided select's live arm): its entry
    moves with it, so the place that converts it reads it."""
    entry = table.get(old_holder)
    if entry is None or old_role not in entry:
        return
    members = entry.pop(old_role)
    target = table.get(new_holder)
    if target is None:
        target = {}
        table[new_holder] = target
    target[new_role] = members
    if isinstance(table, PublishedLandings):
        table.move_marks(old_holder, old_role, new_holder, new_role)


def _tables(ctx: 'SemanticContext') -> list[LandingTable]:
    """The tables an entry may sit in now: the body's own before its
    settlement, the published one after."""
    if ctx.func is None:
        return [ctx.union_landings]
    return [ctx.func.union_landings, ctx.union_landings]


def move_holder(ctx: 'SemanticContext', old_holder: object, old_role: Role,
                new_holder: object, new_role: Role) -> None:
    """`move_landing` in whichever table holds the entry (`_tables`)."""
    for table in _tables(ctx):
        move_landing(table, old_holder, old_role, new_holder, new_role)


def move_all_landings(ctx: 'SemanticContext', old_holder: object,
                      new_holder: object) -> None:
    """Every entry of `old_holder` moved to `new_holder`, role for role: a
    call adopting the arguments another call node analyzed."""
    for table in _tables(ctx):
        entry = table.get(old_holder)
        if entry is None:
            continue
        del table[old_holder]
        target = table.get(new_holder)
        if target is None:
            table[new_holder] = entry
        else:
            target.update(entry)
        if isinstance(table, PublishedLandings):
            for role in entry:
                table.move_marks(old_holder, role, new_holder, role)


def union_members(t: TpyType) -> 'tuple[TpyType, ...] | None':
    """The members of a union slot (a recursive alias's alternatives), the
    payload union of an Optional included; None for any other slot."""
    t = unwrap_readonly(unwrap_own(t))
    if isinstance(t, OptionalType):
        t = unwrap_readonly(unwrap_own(t.inner))
    if isinstance(t, UnionType):
        return t.members
    if isinstance(t, RecursiveAliasInstanceType):
        return t.alternatives()
    return None


def landing_exempt(target: TpyType, source: 'TpyType | None',
                   member: 'TpyType | None' = None) -> bool:
    """A union whose member a value lands in is decided per instantiation,
    not by sema's one check: an open `T` member the source could fill, or
    a member sharing its C++ alternative with another. No entry is
    recorded for either, and each keeps the render of its instantiation.
    The writer passes the `member` it landed the value in; a reader, which
    finds none recorded, asks about the source: its own alternative, or --
    a source no member spells (a widened width) -- any shared one."""
    members = union_members(target)
    if members is None:
        return False
    bare_src = (unwrap_readonly(unwrap_own(source))
                if source is not None else None)
    for m in members:
        bm = unwrap_readonly(m)
        if contains_type_param(bm) and bm != bare_src:
            return True
    if not members_share_alternative(members):
        return False
    if bare_src is None or bare_src.inner_types():
        # A composite source is ambiguous wherever two members share one
        # alternative.
        return True
    alts = [cpp_alternative(m) for m in members]
    own = cpp_alternative(member if member is not None else bare_src)
    if own is None:
        return False
    if member is None and own not in alts:
        return True
    # Only the members of the landed alternative are ambiguous for it:
    # `"a"` at `str | String | int32 | Literal[1]` lands in `str` whatever
    # `int32` and `Literal[1]` share.
    return alts.count(own) > 1


def renders_as(rendered: 'TpyType | None', member: TpyType,
               registry: 'TypeRegistry') -> bool:
    """A value whose C++ render has the type of TPy type `rendered` (None
    for a render no TPy type names: a string literal's array, a braced
    list) is already union member `member`'s alternative, so building the
    union from it needs no conversion to the member. A recursive alias's
    placeholder is its union (`registry.resolve_recursive_refs`)."""
    if rendered is None:
        return False
    a, b = repr_shape(rendered), repr_shape(member)
    # A `Literal` member is its base type's alternative.
    a = a.base_type if isinstance(a, LiteralType) else a
    b = b.base_type if isinstance(b, LiteralType) else b
    return (registry.resolve_recursive_refs(a)
            == registry.resolve_recursive_refs(b))


def literal_token_type(value: object, *,
                       float32: bool = False) -> 'TpyType | None':
    """The TPy type a literal's own C++ token has: `bool`, a float's
    `double` (`float32` for an `f`-suffixed token); None for an int
    literal, which is respelled in its member's own width
    (`render_int_literal_value(..., exact_type=True)`), and for a string
    or bytes literal, whose token is an array."""
    if isinstance(value, bool):
        return BOOL
    if isinstance(value, float):
        return FLOAT32 if float32 else FLOAT
    return None
