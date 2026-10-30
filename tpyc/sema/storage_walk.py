"""The one traversal for "which named storage does this expression reference".

`trace` (`tpyc/storage_trace.py`) climbs an expression once, syntactically,
over coercions, field / subscript steps, property getters, walruses and
borrow-returning awaits, and ends at a name, a select (`receiver_leaves`), a
call or any other node. Every storage question is a projection of that trace
under a named call policy (`CallPolicy`, `tpyc/sema/call_lends.py`): at a
getter or a call the projection climbs on into each operand the policy says
the result may point into, as it climbs into each operand of a select.

The LIFETIME loop (`_Leaves`, `leaves`) is the one climb asking where the
value's storage lives: per operand the value may be, it climbs to a
classified `Leaf` -- a name, static storage, a temporary, a call result no
operand names, ... -- recording the calls it went through and how each lent.
Each lifetime question is a mapping of those leaves: the HOLDER roots
(`storage_roots` at `Use.HOLDER`), the view leaves (`view_leaves`), and the
durability and temporariness readers in `compatibility.py` and
`local_deduction.py`. The root projections answer one of three questions
(`Use`), each a different fact about the same trace:

- HOLDER: whose storage holds the value -- the lifetime question, read off
  the LIFETIME loop's leaves. A step through a handle stays tied to
  whatever holds the handle.
- PLACE: whom replacing or writing the place credits. A step taken through a
  handle writes the handle's referent (`_Roots.handle`), not its holder.
- POINTEE: whom writing THROUGH the value credits: a handle value's referent,
  else the place.

Every use climbs an unbound-self read (`Base.f` names this object, `self`)
and each operand a select may pick. PLACE and POINTEE also carry the tuple
component path into a call, so the policy can answer a receiver lent only at
some components; HOLDER reads no types and asks about the whole result
(BUGS.md#holder-climb-drops-tuple-path).

An operand the climb cannot place is reported (`Roots.unknown`), never
dropped; each consumer states what it does with one. `mark_roots` is the one
routine that turns roots into mutation marks. `call_loans` is the same climb
asked which loans a binding of a call's result files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Iterable

from ..parse import (
    TpyCall, TpyCoerce, TpyExpr, TpyFieldAccess, TpyIntLiteral,
    TpyMethodCall, TpyName, TpyStarUnpack, TpyStrLiteral, TpySubscript,
    TpyVarargPack, const_tuple_index, is_property_getter_read,
)
from ..storage_trace import Step, StepKind, Trace, is_unbound_field, trace
from ..typesys import (
    TpyType, TupleType, following_components, is_indirection_type,
    unwrap_qualifiers, unwrap_readonly,
)
from ..type_def_registry import is_borrowing_view_type
from ..value_category import (
    ExprTypeOf, call_operands, call_returns_cpp_ref, lent_operands,
)

if TYPE_CHECKING:
    from .call_lends import LendSource
    from .context import SemanticContext, SitedKey
    from ..value_category import ValueCategoryAnalyzer


class Use(Enum):
    """Which fact about a place the climb answers (module docstring)."""
    HOLDER = "holder"
    PLACE = "place"
    POINTEE = "pointee"


class EscapePosition(Enum):
    """Where a borrow escapes the body (`SemanticContext.escape_marks`)."""
    RETURN = "return"
    YIELD = "yield"


class StorageKey:
    """A named storage the borrow tracker files loans under: a name `root`
    plus the `members` hopped off it (`o.f` -> root 'o', members ('f',)).
    `str()` is the user-facing spelling the diagnostics print.

    A name is both a storage key (who borrows from it) and a borrower (what
    it borrows); a key with members is storage only. The two namespaces
    meet only through `of_name` / `as_name`: comparing a key with a plain
    string raises, so a crossing without the conversion fails loudly
    instead of answering False. Immutable, so a state snapshot shares it."""

    __slots__ = ('root', 'members', '_text', '_hash')

    root: str
    members: tuple[str, ...]
    _text: str
    _hash: int

    def __init__(self, root: str, members: tuple[str, ...] = ()) -> None:
        text = ".".join((root, *members))
        object.__setattr__(self, 'root', root)
        object.__setattr__(self, 'members', members)
        object.__setattr__(self, '_text', text)
        # The spelling's hash: a plain-string lookup of a filed key reaches
        # `__eq__` and raises rather than silently missing.
        object.__setattr__(self, '_hash', hash(text))

    @classmethod
    def of_name(cls, name: str) -> 'StorageKey':
        """The key of the storage a binding name holds."""
        assert "." not in name, name
        return cls(name)

    def as_name(self) -> str | None:
        """The binding name this key is the storage of, None for a member
        path (storage only, never a borrower)."""
        return None if self.members else self.root

    @property
    def root_key(self) -> 'StorageKey':
        """The key of the binding this path hangs off (`o.f` -> `o`)."""
        return StorageKey(self.root) if self.members else self

    def has_prefix(self, other: 'StorageKey') -> bool:
        """`other` is this key or a path this key extends (`o.f.g` has
        prefixes `o`, `o.f` and itself)."""
        n = len(other.members)
        return self.root == other.root and self.members[:n] == other.members

    def rebase(self, src: 'StorageKey') -> 'StorageKey':
        """This key's members hopped off `src` instead of off `root`
        (`m.inner` rebased on `o.mid` -> `o.mid.inner`)."""
        return StorageKey(src.root, src.members + self.members)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"StorageKey is immutable ({name})")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, StorageKey):
            return self.root == other.root and self.members == other.members
        if isinstance(other, str):
            raise TypeError(
                f"storage key {self._text!r} compared with the name "
                f"{other!r}: convert with StorageKey.of_name / as_name")
        return NotImplemented

    def __hash__(self) -> int:
        return self._hash

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return f"StorageKey({self._text!r})"

    def __copy__(self) -> 'StorageKey':
        return self

    def __deepcopy__(self, memo: dict) -> 'StorageKey':
        return self


@dataclass(frozen=True)
class Place:
    """One named storage an expression references. `elem`: the literal
    element index of the step right off the name (`t[0].f` -> 0), None for
    the whole value. `via_handle`: the name's own binding is a handle (a
    local holding an iterator or a view) whose REFERENT the access reaches,
    so the binding itself is not written -- unlike `through_handle` below,
    where the name is the storage and a call on the path builds the handle.

    How the climb got there, from the call policy's answers: `through_call`,
    through a spelled call (not only a path and getters); `assumed`, through
    a callee whose facts are pending, so whether it lends this name at all
    is a guess; `held_whole`, only as a reference to the whole object (a
    genexpr capture: growing it invalidates nothing, though it still dies
    with its root); `grants_write`, a write through the value reaches it
    (False past a source that grants none -- a readonly callee, a `Ptr`
    operand, a readonly slot); `through_handle`, through a spelled call on
    the path whose result is an object built over the name's storage (an
    iterator, a frame, a view) rather than a C++ reference to it, so a
    callee handed the value may advance it without writing the storage."""
    name: str
    elem: int | None = None
    via_handle: bool = False
    through_call: bool = field(default=False, compare=False)
    assumed: bool = field(default=False, compare=False)
    held_whole: bool = field(default=False, compare=False)
    grants_write: bool = field(default=True, compare=False)
    through_handle: bool = field(default=False, compare=False)
    # The name node the climb ended at (None for `self` reached through an
    # unbound `Base.f`).
    node: TpyExpr | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Hit:
    """One thing a climb found -- a place or an operand it could not place
    -- with the operand whose climb found it: the climbed value, or the lent
    operand of the innermost call the climb went through."""
    operand: TpyExpr
    place: Place | None
    unknown: TpyExpr | None


@dataclass(frozen=True)
class Roots:
    """What the climb found: the places, in source order, and the operands
    it could not place (`hits`: the same, in order, with their operand)."""
    places: tuple[Place, ...] = ()
    unknown: tuple[TpyExpr, ...] = ()
    hits: tuple[Hit, ...] = field(default=(), compare=False)

    @property
    def names(self) -> list[str]:
        """Each named root once, in source order."""
        return list(dict.fromkeys(p.name for p in self.places))

    def single(self) -> str | None:
        """The one root, or None for several roots, none, or any unknown
        operand -- never the first of several."""
        names = self.names
        return names[0] if len(names) == 1 and not self.unknown else None

    def single_place(self) -> Place | None:
        """The one place, or None for several places (two elements of one
        name included), none, or any unknown operand."""
        return (self.places[0] if len(self.places) == 1 and not self.unknown
                else None)

    def __add__(self, other: 'Roots') -> 'Roots':
        return Roots(self.places + other.places, self.unknown + other.unknown,
                     self.hits + other.hits)

    def by_operand(self) -> list[tuple[TpyExpr, 'Roots']]:
        """The hits grouped by the operand that found them, in source
        order."""
        groups: list[tuple[TpyExpr, list[Hit]]] = []
        for h in self.hits:
            group = next((g for g in groups if g[0] is h.operand), None)
            if group is None:
                group = (h.operand, [])
                groups.append(group)
            group[1].append(h)
        return [(e, Roots(tuple(h.place for h in hs if h.place is not None),
                          tuple(h.unknown for h in hs
                                if h.unknown is not None), tuple(hs)))
                for e, hs in groups]

    def proven(self) -> 'Roots':
        """What a recorded fact or a hard verdict may take: an `assumed`
        place (a guess about a pending callee) counts as unknown."""
        guessed = tuple(h.operand for h in self.hits
                        if h.place is not None and h.place.assumed)
        return Roots(tuple(p for p in self.places if not p.assumed),
                     self.unknown + guessed,
                     tuple(h if h.place is None or not h.place.assumed
                           else Hit(h.operand, None, h.operand)
                           for h in self.hits))

    def writes(self) -> 'Roots':
        """The places a write through the value reaches (`grants_write`)."""
        return Roots(tuple(p for p in self.places if p.grants_write),
                     self.unknown, tuple(
                         h for h in self.hits
                         if h.place is None or h.place.grants_write))


def element_index_key(expr: TpyExpr) -> tuple[str, int | str] | None:
    """A comparable identity for a subscript index, or None when the index
    has none (an arithmetic expression, a slice, a call).

    Two forms answer: a non-negative int or a str LITERAL, whose values
    settle both SAME and DISTINCT, and a bare NAME, whose spelling settles
    SAME only (two different names may still hold one index). A negative int
    literal deliberately has no key: `rows[-1]` and `rows[1]` are the same
    element of a two-element list, so treating them as distinct would be
    unsound.
    """
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    if isinstance(expr, TpyIntLiteral):
        return ("lit", expr.value) if expr.value >= 0 else None
    if isinstance(expr, TpyStrLiteral):
        return ("lit", expr.value)
    if isinstance(expr, TpyName):
        return ("name", expr.name)
    return None


def _literal_elem(step: TpyExpr) -> int | None:
    if not isinstance(step, TpySubscript):
        return None
    key = element_index_key(step.index)
    return (key[1] if key is not None and key[0] == "lit"
            and isinstance(key[1], int) else None)


def tuple_subscript_index(expr: TpyExpr, type_of: ExprTypeOf) -> int | None:
    """The element `t[i]` reads off a tuple-typed `t`, or None for any other
    step: the one place step that stays a component of a call's result."""
    if not isinstance(expr, TpySubscript):
        return None
    obj_t = type_of(expr.obj)
    tup = unwrap_qualifiers(obj_t) if obj_t is not None else None
    if not isinstance(tup, TupleType):
        return None
    i = const_tuple_index(expr.index)
    if i is None:
        return None
    n = len(tup.element_types)
    return i + n if i < 0 else i


def call_lends_receiver_at(expr: TpyExpr, path: tuple) -> bool:
    """Whether `expr` is a call whose result, at component `path` or a part
    containing it, is its receiver's storage by declaration
    (`following_components`): the one test of a call transparent to its
    receiver."""
    return (isinstance(expr, TpyMethodCall)
            and any(path[:len(c)] == c
                    for c in following_components(expr.resolved_function_info)))


def receiver_leaves(expr: TpyExpr) -> list[TpyExpr]:
    """The operands a value may BE (`Trace.leaves`): the expression past its
    coercions, or for a ternary / value and/or select every operand it can
    pick -- a call on it runs on whichever one it is, a binding of it holds
    whichever one."""
    return [t.node_at(j) for t, j in trace(expr).leaves()]


def ternary_operands(expr: TpyExpr) -> list[TpyExpr]:
    """The operands a value is through its ternaries (`Trace.leaves` past
    coercions and walruses, ternary only): what a binding or a boundary of
    it hands out, an and/or select being one operand of its own."""
    return [t.node_at(j) for t, j in trace(expr).leaves(
        ternary_only=True, past_walrus=True)]


class Ask(Enum):
    """What a projection asks of a call it climbs through (`CallPolicy`):
    whose storage the result may live in (LIFETIME, the HOLDER climb), whom
    a write through the result reaches (WRITE, the PLACE / POINTEE climbs),
    what a handle the call produced refers to (HANDLE), and which loans a
    binding of the result files (LOAN, `call_loans`)."""
    LIFETIME = "lifetime"
    WRITE = "write"
    HANDLE = "handle"
    LOAN = "loan"


class CallPolicy:
    """How a projection treats the calls and the steps it climbs: the one
    variable part of the walk (`tpyc/sema/call_lends.py` defines the
    policies). `at_call` names the operands the result of `call` (a call
    end or a property getter step) may point into at component `path` --
    a list of `LendSource`, empty for a fresh result -- or None when the
    policy cannot say (the call is reported unknown). `at_step` refuses a
    step the policy does not climb (reported unknown); `follows_selects`
    says whether it climbs each operand a select or a pack stands for.

    The LIFETIME climb (`leaves`) also asks the classifier hooks below;
    each default is the climb's own answer. `ctx`: the analyzer whose types
    and registry the climb's own leaf rule reads (`static_read_kind`), None
    for a policy without one."""
    follows_selects: bool = True
    ctx: 'ValueCategoryAnalyzer | None' = None

    def at_step(self, step: Step) -> bool:
        raise NotImplementedError

    def at_call(self, call: TpyExpr, path: tuple,
                ask: Ask) -> 'list[LendSource] | None':
        raise NotImplementedError

    def leaf_kind(self, node: TpyExpr,
                  above: Step | None) -> 'LeafKind | None':
        """What `node` is when the climb must stop at it, asked before it
        steps past the node (`above`: the step the climb took onto it);
        None to climb on. A read whose stamped referent is static storage
        (`static_read_kind`) is classified before this hook and never
        reaches it."""
        return None

    def call_kind(self, call: TpyExpr,
                  srcs: 'list[LendSource] | None') -> 'LeafKind':
        """The leaf the result of `call` is when `at_call` answered no
        source (`srcs`: None or empty)."""
        return LeafKind.UNNAMED if srcs is None else LeafKind.TEMPORARY

    def end_kind(self, node: TpyExpr) -> 'LeafKind':
        """The leaf a node the climb ends at is when it is no name, no
        call and no select."""
        return LeafKind.UNPLACEABLE

    def at_await(self, step: Step) -> 'list[LendSource] | LeafKind | None':
        """The operands a borrow-returning await's value lends -- climbed
        through a CALL step on the awaited call -- or the leaf kind the
        await is; None leaves the await to `at_step`."""
        return None


@dataclass(frozen=True)
class _Via:
    """How a climb reached the storage it is on: through a spelled call
    (`through_call`), an assumed lend (`assumed`), a whole-object hold
    (`held_whole`), a source granting no write (`grants_write` False), a
    spelled call whose result is a handle over the storage
    (`through_handle`, `Place.through_handle`)."""
    through_call: bool = False
    assumed: bool = False
    held_whole: bool = False
    grants_write: bool = True
    through_handle: bool = False

    def then(self, src: 'LendSource', *, call: TpyExpr | None) -> '_Via':
        """The climb past `src`, lent by the spelled call `call` (None for
        a getter read, a path hop like the field it wraps)."""
        return _Via(self.through_call or call is not None,
                    self.assumed or src.assumed,
                    self.held_whole or src.held_whole,
                    self.grants_write and src.grants_write,
                    self.through_handle or (
                        call is not None and _returns_handle(call)))


def _returns_handle(call: TpyExpr) -> bool:
    """Whether `call`'s result is an object built over what it lends, not a
    C++ reference to that storage (`b.get()` returns the storage itself).
    A `@property` read is a path hop like the field it wraps, never a
    handle."""
    if is_property_getter_read(call):
        return False
    ops = call_operands(call)
    # `call_returns_cpp_ref` never reads its analyzer parameter.
    return not call_returns_cpp_ref(None, ops.fi if ops is not None else None)


_DIRECT = _Via()


def _operands(t: Trace, i: int) -> list[tuple[Trace, int]]:
    """The operands the select or pack `t` ends at stands for."""
    return ([(arm, 0) for arm in t.arms] if t.is_pack else t.leaves(i))


def _past_transparent(t: Trace, i: int, policy: CallPolicy) -> int:
    """The first position at or past `i` that is no coercion and no walrus
    or await step `policy` climbs: what the value at `i` IS."""
    while i < len(t.steps) and (
            t.steps[i].kind is StepKind.COERCE
            or (t.steps[i].kind in (StepKind.WALRUS, StepKind.AWAIT)
                and policy.at_step(t.steps[i]))):
        i += 1
    return i


class _Roots:
    """The PLACE / POINTEE projections of a trace under one call policy, and
    the call climb every projection shares (`_through`). Each hit remembers
    the operand whose climb found it: the value itself, or the lent operand
    of the innermost call it came through."""

    def __init__(self, type_of: ExprTypeOf | None, policy: CallPolicy,
                 operand: TpyExpr) -> None:
        self.type_of = type_of
        self.policy = policy
        self.hits: list[Hit] = []
        self.operand = operand
        self.via = _DIRECT
        # The source of the innermost call the climb is in (`_through`).
        self.lent: 'LendSource | None' = None

    def result(self) -> Roots:
        return Roots(tuple(h.place for h in self.hits if h.place is not None),
                     tuple(h.unknown for h in self.hits
                           if h.unknown is not None),
                     tuple(self.hits))

    def _add(self, name: str, elem: int | None = None,
             via_handle: bool = False, node: TpyExpr | None = None) -> None:
        v = self.via
        self.hits.append(Hit(self.operand, Place(
            name, elem, via_handle, v.through_call, v.assumed, v.held_whole,
            v.grants_write, v.through_handle, node), None))

    def _unknown(self, node: TpyExpr) -> None:
        self.hits.append(Hit(self.operand, None, node))

    def _fresh(self, call: TpyExpr) -> None:
        """A call whose result points into no operand: the root projections
        find nothing there."""

    def _temp_backed(self, call: TpyExpr, src: 'LendSource',
                     alone: bool) -> None:
        """Storage a tuple temporary holds by value for `call`'s operand
        `src`: it adds no root. A result backed by such storage alone points
        into no named storage, so the climb cannot place it."""
        if alone and src is None:
            self._unknown(call)

    def _indirect(self, expr: TpyExpr) -> bool:
        return self.type_of is not None and is_indirection_type(
            self.type_of(expr))

    def _through(self, call: TpyExpr, path: tuple, ask: Ask,
                 then: 'Callable[[Trace], None]') -> None:
        """Climb on into each operand `call` lends (the policy's answer);
        each temp-backed source goes to `_temp_backed`."""
        srcs = self.policy.at_call(call, path, ask)
        if srcs is None:
            self._unknown(call)
            return
        if not srcs:
            self._fresh(call)
            return
        alone = all(s.temp_backed for s in srcs)
        # A `@property` read is a path hop like the field it wraps.
        spelled = None if is_property_getter_read(call) else call
        operand, via, lent = self.operand, self.via, self.lent
        for src in srcs:
            self.lent = src
            if src.temp_backed:
                self._temp_backed(call, src, alone)
                continue
            self.operand = src.expr
            self.via = via.then(src, call=spelled)
            then(trace(src.expr))
            self.operand, self.via = operand, via
        self.operand, self.via, self.lent = operand, via, lent
        if alone:
            self._temp_backed(call, None, alone)

    def _select(self, t: Trace) -> bool:
        """Whether the climb goes on into each operand of the select or pack
        `t` ends at; when the policy refuses, the select is unknown."""
        if self.policy.follows_selects:
            return True
        self._unknown(t.end)
        return False

    def handle(self, t: Trace, i: int) -> None:
        """A write into what the handle at `i` points at credits the
        handle's own name when it is a local or parameter (its referent is
        what was handed in, so the effect travels with the name), and
        nothing for a handle read out of some holder's storage: the holder
        itself is not written. A handle a call produced points into what
        that call lends (a view over a dict's values points into the
        dict's elements), the policy's answer."""
        assert self.type_of is not None
        i = _past_transparent(t, i, self.policy)
        while i < len(t.steps) and tuple_subscript_index(
                t.steps[i].node, self.type_of) is not None:
            i = _past_transparent(t, i + 1, self.policy)
        if i == len(t.steps):
            if isinstance(t.end, TpyName):
                self._add(t.end.name, via_handle=True, node=t.end)
            elif t.ends_at_call:
                self._through(t.end, (), Ask.HANDLE, self.pointee)
            else:
                self._unknown(t.end)
            return
        step = t.steps[i]
        if step.kind is StepKind.GETTER:
            self._through(step.node, (), Ask.HANDLE, self.pointee)
        elif step.kind not in (StepKind.FIELD, StepKind.INDEX):
            self._unknown(step.node)

    def place(self, t: Trace, i: int = 0, path: tuple = ()) -> None:
        assert self.type_of is not None
        elem: int | None = None
        while i < len(t.steps):
            step = t.steps[i]
            if step.kind is StepKind.GETTER:
                self._through(step.node, path, Ask.WRITE, self.pointee)
                return
            if not self.policy.at_step(step):
                self._unknown(step.node)
                return
            if step.kind in (StepKind.FIELD, StepKind.INDEX):
                if is_unbound_field(step):
                    self._add("self")
                    return
                if self._indirect(t.node_at(i + 1)):
                    self.handle(t, i + 1)
                    return
                index = tuple_subscript_index(step.node, self.type_of)
                path = (index,) + path if index is not None else ()
                elem = _literal_elem(step.node)
            elif self._indirect(t.node_at(i + 1)):
                self.handle(t, i + 1)
                return
            i += 1
        if t.arms:
            if self._select(t):
                for leaf, j in _operands(t, i):
                    self.place(leaf, j, path)
            return
        if t.ends_at_call:
            self._through(t.end, path, Ask.WRITE, self.pointee)
        elif isinstance(t.end, TpyName):
            self._add(t.end.name, elem, node=t.end)
        else:
            self._unknown(t.end)

    def pointee(self, t: Trace, i: int = 0) -> None:
        j = t.skip(i)
        for k in range(i + 1, j + 1):
            if self._indirect(t.node_at(k)):
                self.handle(t, k)
                return
        if j == len(t.steps) and t.arms and not t.is_pack:
            if self._select(t):
                for leaf, k in t.leaves(j):
                    self.pointee(leaf, k)
            return
        if self._indirect(t.node_at(j)):
            self.handle(t, j)
            return
        self.place(t, j)


class LeafKind(Enum):
    """What one leaf of the LIFETIME climb is (`leaves`): STATIC storage (a
    str / bytes / None literal, an enum member's `.name`, a `Literal[str]`
    call), NULL (`Ptr[T]()`, pointing nowhere), a CONSTANT (a view read off
    a module or class qualifier), a GLOBAL (any other read off a module or
    class qualifier -- either may be rebindable unless `Leaf.static_read`
    says otherwise), a PLACE (a name), a TEMPORARY (a fresh value dying
    with the statement, a temp-backed tuple element), UNNAMED (a call result
    that may point into storage no operand names), a VIEWNODE (a node of a
    borrowing-view type no rule classifies) or UNPLACEABLE (anything else
    the climb cannot name). Each reader maps every kind it can meet."""
    STATIC = "static"
    NULL = "null"
    CONSTANT = "constant"
    GLOBAL = "global"
    PLACE = "place"
    TEMPORARY = "temporary"
    UNNAMED = "unnamed"
    VIEWNODE = "viewnode"
    UNPLACEABLE = "unplaceable"


def static_read_kind(node: TpyExpr,
                     ctx: 'ValueCategoryAnalyzer | None') -> LeafKind | None:
    """A read of storage no frame owns and nothing rebinds, known by the
    referent sema stamped on it, whatever its receiver (`c.K` reads the
    class's constant, not `c`): an enum member, a `Final` class constant (a
    `ClassVar` is a mutable slot) or a `Final` module variable -- a filled
    default's (`default_fill`) referent always is one. A CONSTANT for a view
    (a constexpr view), else a GLOBAL; None for any other node. Without a
    context (`NO_CALLS`) a module variable's finality is known only for a
    filled default."""
    if not isinstance(node, TpyFieldAccess):
        return None
    if node.enum_member_of is not None:
        static = True
    elif node.class_constant_owner is not None:
        static = node.class_constant_owner.is_recorded_final_constant(
            node.field)
    elif node.module_var_access is not None:
        var = (ctx.registry.module_variable(*node.module_var_access)
               if ctx is not None else None)
        static = node.default_fill or (var is not None and var.is_final)
    else:
        return None
    if not static:
        return None
    return qualifier_kind(node, ctx) if ctx is not None else LeafKind.GLOBAL


def qualifier_kind(node: TpyExpr,
                   ctx: 'ValueCategoryAnalyzer') -> LeafKind:
    """A read off a qualifier or a stamped referent as a leaf: a CONSTANT
    for a view, else a GLOBAL."""
    et = ctx.get_expr_type(node)
    return (LeafKind.CONSTANT if et is not None
            and is_borrowing_view_type(unwrap_readonly(et))
            else LeafKind.GLOBAL)


@dataclass(frozen=True)
class Leaf:
    """One leaf of the LIFETIME climb. `origin`: the operand the value may
    BE (`Trace.leaves`), `arm` the select operand it is (None when the value
    is no select). `path`: the steps from `origin` down to `node` -- the
    trace's steps, and a CALL step (with how the call lent, `Step.how`) for
    each call the climb went through into one of its lent operands. `name`:
    a PLACE's root. `at_call`: `node` is a call's result the climb did not
    climb into. `elem`: the literal element index of the last field or
    subscript step of the path's last stretch (`t[0]` -> 0, `t[0].f` ->
    None). `operand`: the value, or the lent operand of the innermost call
    the climb went through. `via`: how the climb reached it (`_Via`).
    `temp_backed`: storage a tuple temporary holds by value for the call
    the path ends at (`LendSource.temp_backed`), with `alone` when every
    source of that call is. `static_read`: a CONSTANT or GLOBAL whose
    stamped referent is durable static storage (`static_read_kind`), never
    rebound and owned by no frame."""
    origin: TpyExpr
    arm: TpyExpr | None
    path: tuple[Step, ...]
    node: TpyExpr
    kind: LeafKind
    name: str | None = None
    at_call: bool = False
    elem: int | None = None
    operand: TpyExpr | None = None
    via: '_Via' = _DIRECT
    temp_backed: bool = False
    alone: bool = False
    static_read: bool = False

    def stretches(self) -> list[Trace]:
        """The path cut at its calls: from `origin`, then from each lent
        operand, the steps down to the next call or to `node`, as a trace
        ending there."""
        out: list[Trace] = []
        steps: list[Step] = []
        for step in self.path:
            if step.kind is StepKind.CALL:
                out.append(Trace(tuple(steps), step.node))
                steps = []
            else:
                steps.append(step)
        out.append(Trace(tuple(steps), self.node))
        return out

    @property
    def calls(self) -> list[Step]:
        """The CALL steps of the path, outermost first."""
        return [s for s in self.path if s.kind is StepKind.CALL]


class _Leaves(_Roots):
    """The LIFETIME climb of one operand a value may be (`leaves`): the
    trace's steps, each select or pack below the value the policy follows,
    and each call into the operands the policy names (`_Roots._through`),
    recording a `Leaf` where it stops; the policy's classifier hooks
    (`CallPolicy.leaf_kind`, `call_kind`, `end_kind`, `at_await`) name the
    leaves."""

    def __init__(self, policy: CallPolicy, origin: TpyExpr,
                 arm: TpyExpr | None, out: list[Leaf]) -> None:
        super().__init__(None, policy, origin)
        self.origin = origin
        self.arm = arm
        self.out = out
        self.trail: tuple[Step, ...] = ()

    def _leaf(self, kind: LeafKind, node: TpyExpr, name: str | None = None,
              *, at_call: bool = False, elem: int | None = None,
              temp_backed: bool = False, alone: bool = False,
              static_read: bool = False) -> None:
        self.out.append(Leaf(self.origin, self.arm, self.trail, node, kind,
                             name, at_call, elem, self.operand, self.via,
                             temp_backed, alone, static_read))

    def _unknown(self, node: TpyExpr) -> None:
        # Only `_through` reports here: a call the policy cannot answer.
        self._leaf(self.policy.call_kind(node, None), node, at_call=True)

    def _fresh(self, call: TpyExpr) -> None:
        self._leaf(self.policy.call_kind(call, []), call, at_call=True)

    def _temp_backed(self, call: TpyExpr, src: 'LendSource | None',
                     alone: bool) -> None:
        if src is None:
            return
        trail = self.trail
        self.trail = trail + (Step(StepKind.CALL, call, src.how),)
        self._leaf(LeafKind.TEMPORARY, src.expr, temp_backed=True,
                   alone=alone)
        self.trail = trail

    def _call(self, call: TpyExpr) -> None:
        trail = self.trail

        def into(operand: Trace) -> None:
            self.trail = trail + (Step(StepKind.CALL, call, self.lent.how),)
            self.climb(operand)

        self._through(call, (), Ask.LIFETIME, into)
        self.trail = trail

    def _await(self, step: Step,
               answer: 'list[LendSource] | LeafKind') -> None:
        if isinstance(answer, LeafKind):
            self._leaf(answer, step.node)
            return
        trail, operand, via, lent = (self.trail, self.operand, self.via,
                                     self.lent)
        call = step.node.value
        if not answer:
            # An awaited result lending no operand points into storage no
            # name the call was handed holds.
            self.trail = trail + (step,)
            self._leaf(LeafKind.UNNAMED, call, at_call=True)
            self.trail = trail
            return
        for src in answer:
            self.lent = src
            self.trail = trail + (step, Step(StepKind.CALL, call, src.how))
            self.operand = src.expr
            self.via = via.then(src, call=call)
            self.climb(trace(src.expr))
        self.trail, self.operand, self.via, self.lent = (trail, operand, via,
                                                         lent)

    def _stops_at(self, node: TpyExpr) -> bool:
        kind = static_read_kind(node, self.policy.ctx)
        if kind is not None:
            self._leaf(kind, node, static_read=True)
            return True
        kind = self.policy.leaf_kind(node,
                                     self.trail[-1] if self.trail else None)
        if kind is not None:
            self._leaf(kind, node)
        return kind is not None

    def climb(self, t: Trace, i: int = 0) -> None:
        trail = self.trail
        elem: int | None = None
        while i < len(t.steps):
            step = t.steps[i]
            if step.kind is StepKind.GETTER:
                self._call(step.node)
                self.trail = trail
                return
            if self._stops_at(step.node):
                self.trail = trail
                return
            if step.kind is StepKind.AWAIT and (
                    answer := self.policy.at_await(step)) is not None:
                self._await(step, answer)
                self.trail = trail
                return
            if not self.policy.at_step(step):
                self._leaf(LeafKind.UNPLACEABLE, step.node)
                self.trail = trail
                return
            if step.kind in (StepKind.FIELD, StepKind.INDEX):
                if is_unbound_field(step):
                    self._leaf(LeafKind.PLACE, step.node, "self")
                    self.trail = trail
                    return
                elem = _literal_elem(step.node)
            self.trail += (step,)
            i += 1
        if self._stops_at(t.end):
            pass
        elif t.arms:
            if self.policy.follows_selects:
                # Each operand on its own climb, past its coercions: a select
                # nested in one meets the policy's classifier first.
                here = self.trail
                for arm in t.arms:
                    self.trail = here
                    self.climb(arm, 0 if t.is_pack else arm.skip())
            else:
                self._leaf(LeafKind.UNPLACEABLE, t.end)
        elif t.ends_at_call:
            self._call(t.end)
        elif isinstance(t.end, TpyName):
            self._leaf(LeafKind.PLACE, t.end, t.end.name, elem=elem)
        else:
            self._leaf(self.policy.end_kind(t.end), t.end)
        self.trail = trail


def leaves(expr: TpyExpr, policy: CallPolicy, *,
           split: bool = True) -> list[Leaf]:
    """The LIFETIME climb of `expr` under `policy` (module docstring): per
    operand the value may be (`Trace.leaves`; with `split` False, the value
    whole, its own select left to the policy), the leaves its storage lives
    in."""
    out: list[Leaf] = []
    top = trace(expr)
    if not split:
        _Leaves(policy, expr, None, out).climb(top)
        return out
    for t, j in top.leaves():
        _Leaves(policy, t.node_at(j),
                None if t is top else t.node_at(0), out).climb(t, j)
    return out


def view_leaves(expr: TpyExpr, policy: CallPolicy) -> list[Leaf]:
    """The leaves of the storage a `str` / `bytes` view bound to `expr`
    reads (`leaves` under `ViewLend`): per operand the value may be, the
    climb through paths and each call into the operands `policy` says its
    result may read, to a leaf `policy` classifies or a name."""
    return leaves(expr, policy)


def _holder(expr: TpyExpr, policy: CallPolicy) -> Roots:
    """HOLDER: the LIFETIME leaves of `expr` as roots -- a name is a place,
    a fresh call result holds nothing, nor does a stamped static read
    (`Leaf.static_read`: storage no frame owns), a temp-backed element
    nothing unless every source of its call is one (then the call is
    unknown), and every other leaf is an operand the climb could not
    place."""
    hits: list[Hit] = []
    alone_calls: list[TpyExpr] = []
    for leaf in leaves(expr, policy, split=False):
        if leaf.static_read:
            continue
        if leaf.kind is LeafKind.PLACE:
            v = leaf.via
            hits.append(Hit(leaf.operand, Place(
                leaf.name, leaf.elem, False, v.through_call, v.assumed,
                v.held_whole, v.grants_write, v.through_handle,
                leaf.node if isinstance(leaf.node, TpyName) else None),
                None))
        elif leaf.temp_backed:
            call = leaf.path[-1].node
            if leaf.alone and not any(c is call for c in alone_calls):
                alone_calls.append(call)
                hits.append(Hit(leaf.operand, None, call))
        elif not (leaf.at_call and leaf.kind is LeafKind.TEMPORARY):
            hits.append(Hit(leaf.operand, None, leaf.node))
    return Roots(tuple(h.place for h in hits if h.place is not None),
                 tuple(h.unknown for h in hits if h.unknown is not None),
                 tuple(hits))


def reads_static_only(expr: TpyExpr, policy: CallPolicy) -> bool:
    """Whether every LIFETIME leaf of `expr` under `policy` is storage no
    frame owns and nothing rebinds: STATIC storage or a stamped static read
    (`Leaf.static_read`). An operand with no leaf is not."""
    found = leaves(expr, policy, split=False)
    return bool(found) and all(
        leaf.static_read or leaf.kind is LeafKind.STATIC for leaf in found)


def storage_roots(expr: TpyExpr, use: Use, type_of: ExprTypeOf | None,
                  policy: CallPolicy) -> Roots:
    """The named storage `expr` references, as `use` asks it (module
    docstring), climbing each call as `policy` answers it. PLACE and
    POINTEE need `type_of`; HOLDER reads no types."""
    if use is Use.HOLDER:
        return _holder(expr, policy)
    roots = _Roots(type_of, policy, expr)
    t = trace(expr)
    if use is Use.PLACE:
        roots.place(t)
    else:
        roots.pointee(t)
    return roots.result()


class KeyShape(Enum):
    """What the expression a loan key was spelled from reads of the keyed
    storage: the storage itself (a name, a getter read), an ELEMENT of it
    (a subscript off the key), or the FIELD the key names."""
    WHOLE = "whole"
    ELEMENT = "element"
    FIELD = "field"


class Unkeyed(Enum):
    """Why `loan_key` names no key. NOT_A_PLACE: a call result, whose loans
    are the call's own legs. UNPLACEABLE: storage the tracker cannot key
    (a chain deeper than one hop, a walrus, a select -- an and/or one
    too, BUGS.md#select-or-arm-unkeyed); each consumer states what it does
    instead."""
    NOT_A_PLACE = "not a place"
    UNPLACEABLE = "unplaceable"


@dataclass(frozen=True)
class LoanKey:
    """The borrow tracker's key for the storage an expression borrows: a
    name, or one field / getter hop off a name (`o.f`; an unbound `Base.f`
    keys at `self.f`). `node`: the expression the key was spelled from,
    past its coercions, which says the key's `shape` and, for an ELEMENT,
    its `index`."""
    key: StorageKey
    node: TpyExpr

    @property
    def shape(self) -> KeyShape:
        if isinstance(self.node, TpySubscript):
            return KeyShape.ELEMENT
        if isinstance(self.node, TpyFieldAccess):
            return KeyShape.FIELD
        return KeyShape.WHOLE

    @property
    def index(self) -> TpySubscript | None:
        """The subscript off the key when `shape` is ELEMENT -- raw, so each
        consumer states which subscripts its loan names an element by."""
        return self.node if isinstance(self.node, TpySubscript) else None


def _names_self(step: Step) -> bool:
    """An unbound `Base.f` hop names this object's storage: keyed under the
    class name, a loan would never meet the mutation through `self.f`."""
    return step.node.unbound_self_parent_type is not None


def _member_key(t: Trace, i: int) -> StorageKey | None:
    """The key the node at `i` names itself: a name, or one field / getter
    hop off a name."""
    if i == len(t.steps):
        return (StorageKey(t.end.name) if isinstance(t.end, TpyName)
                else None)
    step = t.steps[i]
    if step.kind is StepKind.FIELD:
        member = step.node.field
    elif step.kind is StepKind.GETTER:
        member = step.node.method
    else:
        return None
    if _names_self(step):
        return StorageKey("self", (member,))
    if i + 1 == len(t.steps) and isinstance(t.end, TpyName):
        return StorageKey(t.end.name, (member,))
    return None


def _loan_key(t: Trace) -> LoanKey | Unkeyed:
    i = t.skip()
    top = t.node_at(i)
    named = None
    if i < len(t.steps) and t.steps[i].kind is StepKind.INDEX:
        named = _member_key(t, i + 1)
    else:
        named = _member_key(t, i)
    if named is not None:
        return LoanKey(named, top)
    if (isinstance(top, (TpyMethodCall, TpyCall))
            and not is_property_getter_read(top)):
        return Unkeyed.NOT_A_PLACE
    return Unkeyed.UNPLACEABLE


def loan_key(expr: TpyExpr) -> LoanKey | Unkeyed:
    """The key a loan of `expr`'s storage is filed under: the trace past
    its top coercions, keyed at most one member hop off a name, plus a
    trailing subscript (`xs[i]`, `o.f[i]` -> `xs`, `o.f`). A property
    getter is a hop whether or not it lends: the key decides whether the
    storage can be guarded at all, not whom the loan names."""
    return _loan_key(trace(expr))


def prefix_loan_key(expr: TpyExpr, type_of: ExprTypeOf,
                    policy: 'CallPolicy') -> LoanKey | None:
    """For a path `loan_key` cannot key (`o.inner.xs`, a chain deeper than
    one hop), the key of its longest keyable prefix: the storage that holds
    what the path reaches. None for any other shape, and for a path that
    reaches its end through a handle (`self._p.first` over a `Ptr` member):
    by the POINTEE rule, what a handle points at is not its holder's."""
    t = trace(expr)
    if _loan_key(t) is not Unkeyed.UNPLACEABLE:
        return None
    roots = storage_roots(expr, Use.POINTEE, type_of, policy).names
    for i in range(t.skip(), len(t.steps)):
        lk = _loan_key(Trace(t.steps[i + 1:], t.end, t.arms))
        if isinstance(lk, LoanKey):
            return lk if lk.key.root in roots else None
    return None


def borrow_key(expr: TpyExpr) -> StorageKey | None:
    """`loan_key`'s key, None where it names none."""
    lk = loan_key(expr)
    return lk.key if isinstance(lk, LoanKey) else None


def select_arm_keys(expr: TpyExpr) -> list[LoanKey] | None:
    """The loan keys a ternary hands out, one per arm, nested ternaries
    flattened; None when `expr` (past its coercions) is no ternary. An arm
    with no key -- a call, a literal, an and/or select
    (BUGS.md#select-or-arm-unkeyed) -- loans nothing here."""
    t = trace(expr)
    picks = t.leaves(ternary_only=True)
    # A ternary picks its arms; anything else is its own single operand.
    if picks[0][0] is t:
        return None
    return [lk for lk in (_loan_key(arm) for arm, _ in picks)
            if isinstance(lk, LoanKey)]


def pack_operands(expr: TpyExpr) -> list[tuple[TpyExpr, bool]]:
    """The operands one argument position hands over, each with whether it
    is SPREAD: a `*args` pack occupies one slot while holding many
    operands, and a forwarded pack (`f(*xs)`) hands its source's
    elements, so the source is the operand, spread. Any other argument is
    its own single operand."""
    if isinstance(expr, TpyVarargPack):
        return [(a.expr, True) if isinstance(a, TpyStarUnpack) else (a, False)
                for a in expr.args]
    return [(expr, False)]


def operand_keys(expr: TpyExpr) -> list[StorageKey]:
    """The loan keys one argument position lends: a callee that borrows a
    pack borrows every element, so it lends one key per keyed operand
    (`pack_operands`), a forwarded pack the source pack itself."""
    return [k for k in (borrow_key(e) for e, _spread in pack_operands(expr))
            if k is not None]


def storage_key(expr: TpyExpr) -> StorageKey | None:
    """The key `expr` itself names -- no coercion peeled, no subscript: the
    mutation side's question (`BorrowTracker.resolve_obj_storage`, a field
    write's own path), spelled by the same speller as `loan_key`."""
    return _member_key(trace(expr), 0)


def path_key(expr: TpyExpr) -> StorageKey | None:
    """The dotted path of a pure field CHAIN off a name ('o.inner.tag'), or
    None: no coercion, getter, subscript or `__getattr__` hop (that hop
    mints a temporary the path does not own). An unbound `Base.f` is rooted
    at `self`, as `loan_key` spells it.

    The question is the MUTATION side's -- an alias bind, an argument at a
    mutable parameter, a field write -- whose write is spelled at whatever
    depth the program wrote it, and the whole path is what lets
    `mark_view_borrowers_mutated` relate it to a view's key by prefix
    (`m = i` keys 'i' and so marks the view under 'i.name'). A loan is never
    filed under it: loans are looked up by `loan_key`."""
    return _path_key(trace(expr), 0)


def container_path_key(expr: TpyExpr) -> StorageKey | None:
    """`path_key` of the container a store into `expr` lands in: the
    trace past its top coercions and subscripts (`o.items[i]` ->
    'o.items')."""
    t = trace(expr)
    return _path_key(t, t.skip(kinds=(StepKind.COERCE, StepKind.INDEX)))


def _path_key(t: Trace, i: int) -> StorageKey | None:
    if not isinstance(t.end, TpyName):
        return None
    root = t.end.name
    members: list[str] = []
    for step in reversed(t.steps[i:]):
        if step.kind is not StepKind.FIELD or step.node.hidden_call is not None:
            return None
        if _names_self(step):
            root = "self"
        members.append(step.node.field)
    return StorageKey(root, tuple(members))


def holder_root(expr: TpyExpr, policy: CallPolicy, *,
                proven: bool = False) -> str | None:
    """The single storage root holding `expr` (`Roots.single`); with
    `proven`, a guess about a pending callee counts as unknown
    (`Roots.proven`)."""
    roots = storage_roots(expr, Use.HOLDER, None, policy)
    return (roots.proven() if proven else roots).single()


def pointee_root(expr: TpyExpr, type_of: ExprTypeOf,
                 policy: CallPolicy) -> str | None:
    """The single name a write through the value `expr` credits."""
    return storage_roots(expr, Use.POINTEE, type_of, policy).single()


def slot_lent_roots(expr: TpyExpr, slot: TpyType | None, use: Use,
                    type_of: ExprTypeOf | None, policy: CallPolicy, *,
                    write_only: bool = False) -> Roots:
    """The roots of what `expr` lends as the value of `slot`
    (`lent_operands`: a tuple literal at a tuple slot lends each borrowing
    element), climbed for `use`; with `write_only`, only the operands the
    slot grants write access through. The operands' access rights come from
    the destination slot, never from the expression."""
    out = Roots()
    for lent in lent_operands(expr, slot, expr_type=type_of):
        if write_only and not lent.grants_write:
            continue
        out = out + storage_roots(lent.expr, use, type_of, policy)
    return out


@dataclass(frozen=True)
class LoanSource:
    """One loan a binding of a call's result files (`call_loans`).

    `call` is the outermost call the climb went through and `source` the
    operand of it the loan comes from (its index, -1 the receiver, and
    slot). `operand` is the lent operand of the innermost call, `key` its
    loan key -- or, for a path too deep to key that ends at a name, the
    name (`root`) -- or `Unkeyed` when it names no storage: a temporary
    (`fresh`: a call lending nothing, a literal) or a call the policy
    cannot answer. The flags are the policy's, joined along the climb:
    `assumed` (a pending callee: a guess), `held_whole` (the result keeps
    only a whole-object reference), `declared_only` (a receiver lent only
    by its declaration, whose shape is unknown), `temp_backed` (storage a
    tuple temporary holds by value for the call: then `call` and `source`
    are that call, at any depth, and the parameter the temporary binds)."""
    call: TpyExpr
    source: 'LendSource'
    operand: TpyExpr
    key: 'StorageKey | Unkeyed'
    fresh: bool = False
    assumed: bool = False
    held_whole: bool = False
    declared_only: bool = False
    temp_backed: bool = False


def _climb_to_call(t: Trace, i: int, policy: CallPolicy
                   ) -> tuple[Trace, int, TpyExpr | None]:
    """Past the field / subscript steps and the steps `policy` climbs, the
    call (a getter step or a call end) the value at `i` is read off, or
    None."""
    while i < len(t.steps):
        step = t.steps[i]
        if step.kind is StepKind.GETTER:
            return t, i, step.node
        if step.kind not in (StepKind.COERCE, StepKind.FIELD,
                             StepKind.INDEX) and not policy.at_step(step):
            return t, i, None
        i += 1
    return t, i, (t.end if t.ends_at_call else None)


def call_loans(expr: TpyExpr, policy: CallPolicy) -> list[LoanSource]:
    """The loans a binding of `expr` files for the calls it reads its value
    off: through each call `expr` is (or is a field / element / select arm
    of), each operand the policy says the result lends -- recursively
    through an operand that is itself read off a call, and per operand a
    select or `*args` pack stands for. A value read off no call (a name, a
    path) files nothing here: its loan is the binding's own."""
    out: list[LoanSource] = []
    Flags = tuple[bool, bool, bool]
    Top = tuple[TpyExpr, 'LendSource']

    def emit(top: Top, operand: TpyExpr, key: 'StorageKey | Unkeyed',
             flags: Flags, *, fresh: bool = False,
             temp_backed: bool = False) -> None:
        out.append(LoanSource(top[0], top[1], operand, key, fresh, *flags,
                              temp_backed))

    def visit(t: Trace, i: int, top: Top | None, flags: Flags) -> None:
        j = _past_transparent(t, i, policy)
        if j == len(t.steps) and t.arms:
            if policy.follows_selects:
                for sub, k in _operands(t, j):
                    visit(sub, k, top, flags)
            elif top is not None:
                emit(top, t.end, Unkeyed.UNPLACEABLE, flags)
            return
        _, k, call = _climb_to_call(t, i, policy)
        node = t.node_at(i)
        if call is not None:
            through(call, node, top, flags)
            return
        if k == len(t.steps) and t.arms:
            # A field or element of a select is that projection of each
            # operand the select picks: each arm continues under it.
            if policy.follows_selects:
                for leaf, m in _operands(t, k):
                    visit(Trace(t.steps[:k] + leaf.steps[m:], leaf.end,
                                leaf.arms), i, top, flags)
            elif top is not None:
                emit(top, node, Unkeyed.UNPLACEABLE, flags)
            return
        if top is None:
            return
        lk = _loan_key(Trace(t.steps[i:], t.end, t.arms))
        if isinstance(lk, LoanKey):
            emit(top, node, lk.key, flags)
        elif k == len(t.steps) and isinstance(t.end, TpyName):
            # A path too deep to key still lends its root.
            emit(top, node, StorageKey.of_name(t.end.name), flags)
        else:
            emit(top, node, lk, flags, fresh=True)

    def through(call: TpyExpr, operand: TpyExpr, top: Top | None,
                flags: Flags) -> None:
        srcs = policy.at_call(call, (), Ask.LOAN)
        if srcs is None or not srcs:
            if top is not None:
                emit(top, operand, Unkeyed.NOT_A_PLACE, flags,
                     fresh=srcs is not None)
            return
        for src in srcs:
            here = (flags[0] or src.assumed, flags[1] or src.held_whole,
                    flags[2] or src.declared_only)
            if src.temp_backed:
                # A C++ temporary lives to the end of the full-expression, so
                # a tuple temporary of a call at any depth backs the result.
                # The loan names that call: its parameter is the temporary.
                emit((call, src), src.expr, Unkeyed.NOT_A_PLACE, here,
                     temp_backed=True)
                continue
            visit(trace(src.expr), 0, top or (call, src), here)

    visit(trace(expr), 0, None, (False, False, False))
    return out


@dataclass(frozen=True)
class MarkRequest:
    """What one marking site does to the roots it found. Each site builds
    its own request from its context (the callee, the slot, the operand),
    so no flag is a fixed property of a site kind.

    `params`: the mutation mark (`mark_param_mutated`, with
    `through_field` / `via_element`); `loop_vars`: the loop-binding mark
    (`mark_binding_written`, every loop binding the write reaches);
    `handle_bindings`: a name reached only as a handle
    (`Place.via_handle`) gets the binding mark too -- False where only the
    referent is written (an argument dereferenced into a reference
    parameter), True where the use may run on the handle value itself (a
    receiver: an iterator view advances itself); `structural`: the
    structural mark; `effective_storage`: the param and
    loop-var marks land on the storage the name borrows
    (`effective_storage`) rather than the name."""
    params: bool = True
    loop_vars: bool = False
    handle_bindings: bool = True
    through_field: bool = False
    via_element: bool = False
    structural: bool = False
    effective_storage: bool = False


def mark_roots(ctx: 'SemanticContext',
               roots: 'Roots | Iterable[StorageKey | SitedKey]',
               req: MarkRequest) -> None:
    """The one routine that marks source roots mutated: the roots a climb
    found, or the storage keys a loan names (a member path as the
    `SitedKey` its entrance handed out). A place the climb reached past
    a source granting no write (`Place.grants_write`) is not written. An
    operand the climb could not place is not credited here: each consumer
    states what it does with one."""
    targets = ([(StorageKey.of_name(p.name), p.via_handle)
                for p in roots.writes().places] if isinstance(roots, Roots)
               else [(key, False) for key in roots])
    bt = ctx.func.borrow_tracker
    for key, via_handle in targets:
        name = (key if isinstance(key, StorageKey) else key.key).as_name()
        if req.effective_storage and name is not None:
            # An alias of a member path stays the start: the climb from it
            # crosses the loan, which carries whether the path writes its
            # root.
            storage = bt.effective_storage(name)
            if not storage.members:
                key = storage
                name = storage.as_name()
        if (req.loop_vars and name is not None
                and (req.handle_bindings or not via_handle)):
            ctx.mark_binding_written(key)
        if req.params:
            ctx.mark_param_mutated(key, through_field=req.through_field,
                                   via_element=req.via_element)
        if req.structural:
            ctx.mark_param_structurally_mutated(key)
