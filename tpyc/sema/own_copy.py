"""The owning-slot copy contract of a generic body.

A body that stores an open `T` into an owning slot MAY copy, and its author
should read that at DECLARATION time -- a library generic has no
instantiation to consult, and copyable is TPy's default, so no bound is
required to write one. Each sink therefore warns at the body line, in the
hedged form (`may copy T into <sink> if not a value type`; a reference-type
head such as `list[T]` copies whatever `T` is and is not hedged), and the
warning stands whatever the program instantiates. Exactly two things silence it:
`copy()` at the slot, which is the author saying the copy is intended, and a
`T: ValueType` bound, under which a reference-type copy cannot happen.
`T: Copyable` does not -- copyable is TPy's default, so that bound only rules
out a non-copyable payload; it does not state an intent to copy.

Whether the copy is LEGAL is still the instantiation's to answer, because
only it knows the payload. So the sink also records the site as an
obligation, and a non-copyable instantiation (`@nocopy`, or a record with
`__del__`, which deletes the generated copy ctor) gives it a verdict: the
located `cannot copy non-copyable type 'X'` error, which takes the hedge's
line in the declaring module's output. A copyable instantiation adds
nothing: it has nothing to say beyond what the hedge already said.

The obligation is a fact of the declaring body and never changes; the
verdict is a fact of the program that instantiated it, so it lives in that
compilation's `OwnCopyVerdicts` table, keyed on the obligation by identity.
A stdlib generic instantiated by user code therefore keeps its own state
untouched, which is what lets an analyzed module outlive one compilation.
The module's final diagnostics are composed from its hedges and the table
(`apply_own_copy_verdicts` in `context.py`), and the obligation holds the
`Diagnostic` the sink emitted, not its index. A body's walk only registers
its hedges; settlement emits one -- and records its obligation -- only where
the source's record leaf copies (`TypeCompatibility.project_copies`), so a
hedge whose leaf moves has no obligation for a verdict to answer.

It is a channel of its own rather than one of the two nearby ones because
what it defers is different: `representational_type_params` stamps a codegen
fact per call node, and `pending_borrow_checks` defers a check the analyzer
will run itself, while this defers a PROMOTION the instantiation supplies to
a diagnostic already in the list. See docs/ARCHITECTURE.md for the
comparison.
"""
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Collection

from ..compilation_context import get_current_compiler
from ..diagnostics import Diagnostic, DiagnosticLevel, NOCOPY_REMEDIATION_HINT
from ..identity_map import IdentityMap
from ..parse import (ResultForm, TpyCallLike, TpyCoerce, TpyExpr, TpyFieldAccess,
                     TpyName, TpySubscript, is_property_getter_read)
from ..typesys import (CallableType, NominalType, OptionalType, OwnType,
                       ReadonlyType, TpyType, TupleType, TypeParamRef,
                       UnionType, class_own_arg_is_identity, unwrap_own,
                       contains_reference_type, inner_type_param_names,
                       named_class_types,
                       unwrap_qualifiers, unwrap_readonly, unwrap_ref_type)

if TYPE_CHECKING:
    from .context import SemanticContext

# The hedge clause. The body cannot know whether the payload is a value type,
# and says so; only a non-copyable instantiation replaces the line.
HEDGE_CLAUSE = "if not a value type"

# Message kinds. `SLOT` is the whole-value store (`.append`, `Own[T]` param,
# `Own[T]` return, `yield`, field / container assign); `ELEMENTS` is the
# per-element copy a container conversion makes.
KIND_SLOT = "slot"
KIND_ELEMENTS = "elements"
# A read that copies only at an instantiation where its open `T` is
# `Own[X]` (a stored place returned at `-> T`): reported at the call that
# instantiates the generic, with the twin's message.
KIND_OWN_INSTANTIATION = "own_instantiation"


@dataclass(frozen=True, eq=False)
class OwnCopyObligation:
    """One owning-slot copy site in a body whose payload is still open.

    Frozen: it belongs to the declaring body, and the bodies of a library
    module are analyzed once for every program that imports it.
    """

    display_type: TpyType
    target_type: TpyType
    dest: str
    kind: str
    hint: str
    diag: Diagnostic
    # The read hands its value on where its open `T` is `Own[X]`
    # (`SemanticContext.hands_on_at_own`): no copy at that instantiation.
    hands_on: bool = False
    # Decided by its class's type parameters alone (`class_owns`): the
    # class's instantiation answers it, not a call of the member.
    of_class: bool = False


@dataclass(frozen=True, eq=False)
class OwnCompositeLeaf:
    """The body's own open `param` is a leaf of the tuple or Optional /
    union `composite` (its signature's or a type in its body). An
    instantiation at a non-plain `Own[X]` is refused at the instantiating
    call: the composite's owned forms are not built per leaf
    (BUGS.md#own-type-arg-composite-leaf-refused). Rides the body's
    obligations, so the discharge composes it through forwards as it does
    a copy only an `Own` instantiation makes."""

    param: TypeParamRef
    composite: TpyType
    # The generic whose body declares `param`.
    owner: str
    # `param` is the class's, `owner` the class declaring the method.
    of_class: bool = False


@dataclass(frozen=True, eq=False)
class OpenClosureCapture:
    """A lambda or nested def at a `Callable` slot whose result names the
    body's open `T` (`T`, `readonly[T]`), returning what the closure
    captures (`message`: the concrete twin's refusal,
    `_check_callable_result_roots`). Whether the result is a borrow is the
    instantiation's: refused where the substituted slot returns one
    (`T = P`), admitted at `Own[X]` and at a value `T`."""

    slot: CallableType
    message: str
    line: 'int | None'
    # The generic whose body holds the closure.
    owner: str
    # The closure as the refusal names it: "the lambda", "nested def 'f'".
    closure: str = "the lambda"
    of_class: bool = False


@dataclass(frozen=True, eq=False)
class OwnStorageArgFlow:
    """A class formed at the storage form of the body's open `param`
    (`C[Unowned(T)]`, a construction the binder inferred) flowing into a
    slot declared at `param` itself (`C[T]`): the same class at
    `T = P`, two classes at a non-plain `T = Own[X]` -- the twin's
    `C[X]` meeting `C[Own[X]]`, refused there with the twin's mismatch."""

    param: TypeParamRef
    value: TpyType
    slot: TpyType
    context: str
    line: 'int | None'
    # The generic whose body declares `param`.
    owner: str
    # The class spelled at the open parameter, for the remedy
    # (`Crate[T](...)`).
    cls: str
    of_class: bool = False


def shown_type(t: TpyType) -> TpyType:
    """`t` as a diagnostic spells it: no borrow markers at any depth."""
    return unwrap_ref_type(t).map_inner_types(shown_type)


def binding_sources(callee: object, own_sources: 'tuple[tuple[str, int], ...]',
                    evidence: 'tuple[tuple[int | None, TpyExpr], ...]'
                    ) -> tuple[tuple[str, str, 'str | None'], ...]:
    """`OwnCopyEdge.sources` from the binder's `own_sources` (type
    parameter -> index in `evidence`, `Candidate.own_sources`)."""
    params = list(getattr(callee, "params", None) or ())
    found = []
    for name, at in own_sources:
        if at >= len(evidence):
            continue
        slot, arg = evidence[at]
        if slot is None or slot >= len(params):
            continue
        found.append((name, params[slot].name,
                      arg.name if isinstance(arg, TpyName) else None))
    return tuple(found)


def composite_leaves(typ: TpyType, declared: 'Collection[str]'
                     ) -> list[TypeParamRef]:
    """Every open type parameter of `declared` that is a leaf of a tuple or
    of an Optional / union in `typ` -- at any depth, through a callable's
    parameters and result, an iterator's step, a container's elements. An
    element of any other type (`list[T]`) is no leaf: a container owns its
    elements whether or not they are `Own` (`list[Own[P]]` is `list[P]`),
    and a class TPy emits is instantiated at `Own[X]` itself (`C[T]`); a
    tuple element (`list[tuple[T, int32]]`) still is, as that normalizes
    only a whole element. A protocol's type argument is none, and neither
    is the storage form `Unowned(T)`: it is never `Own`."""
    found: dict[str, TypeParamRef] = {}

    def walk(t: TpyType, inside: bool) -> None:
        t = unwrap_qualifiers(t)
        if type(t) is TypeParamRef:
            if inside and t.name in declared:
                found.setdefault(t.name, t)
            return
        member = isinstance(t, (TupleType, OptionalType, UnionType))
        for inner in t.inner_types():
            walk(inner, member)
    walk(typ, False)
    return list(found.values())


class OwnCopyVerdicts:
    """What one compilation's instantiations told the obligations they reached.

    Per compilation, keyed on the obligation by identity, so a program's
    verdict never lands on the declaring body's objects. Two calls at the
    same type report once and two calls at different types report twice, in
    the order they resolved (the corpus has one such line).
    """

    def __init__(self) -> None:
        self._by_obligation: IdentityMap = IdentityMap()
        self._instantiation_seen: set = set()
        # Per walked instantiation (`OwnCopyEdge.key`), the
        # `KIND_OWN_INSTANTIATION` copies it makes, its forwards' included:
        # (obligation, the substituted `Own[X]` target).
        self.instantiation_copies: dict[tuple, list] = {}
        # The instantiations being walked, outermost first, each one's
        # lowest stack index its walk reached (a cycle back to an
        # instantiation still in progress), and per cycle root the members
        # whose walks finished under it: an instantiation cycle reaches one
        # set of copies, recorded for every member when its root finishes.
        self.walk_stack: list[tuple] = []
        self.walk_low: dict[tuple, int] = {}
        self.cycle_members: dict[tuple, list[tuple]] = {}
        self.cycle_owner: dict[tuple, tuple] = {}

    def record(self, obligation: OwnCopyObligation, level: DiagnosticLevel,
               message: str) -> None:
        verdicts = self._by_obligation.setdefault(obligation, [])
        if any(m == message for _, m in verdicts):
            return
        verdicts.append((level, message))

    def promoted(self, obligation: OwnCopyObligation) -> list[Diagnostic]:
        """The diagnostics that take the hedge's place, at the hedge's line."""
        return [Diagnostic(level, message, obligation.diag.loc)
                for level, message in self._by_obligation.get(obligation, ())]

    def __len__(self) -> int:
        return len(self._by_obligation)

    def closure_capture(self, ctx, obligation: 'OpenClosureCapture',
                        slot: CallableType, edge: 'OwnCopyEdge',
                        member: str) -> None:
        """Refuse, at the instantiation `edge`, a closure returning its
        capture through a slot this instantiation makes a borrow."""
        self._refuse_at(ctx, obligation, str(slot), edge, member, lambda where: (
            f"{obligation.message} ({obligation.closure} in {where}, "
            f"instantiated here with a result of '{slot.return_type}')"))

    def storage_arg_mismatch(self, ctx, obligation: 'OwnStorageArgFlow',
                             types: 'tuple[TpyType, TpyType]', member: str,
                             edge: 'OwnCopyEdge') -> None:
        """Refuse, at the instantiation `edge`, a storage-form class value
        meeting its slot's class at an `Own[X]` argument, as the twin's
        check does."""
        # The twin's mismatch names the classes, not a slot's `Ref` / `Own`.
        value, slot = (unwrap_own(unwrap_ref_type(t)) for t in types)
        self._refuse_at(ctx, obligation, str(slot), edge, member, lambda where: (
            f"Type mismatch in {obligation.context}: expected {slot}, got "
            f"{value} (in {where}, instantiated here); spell the class "
            f"argument (`{obligation.cls}(...)`)"))

    def _refuse_at(self, ctx, obligation, target: str, edge: 'OwnCopyEdge',
                   member: str, message) -> None:
        """One refusal per (obligation, target, site): `message` given where
        the body is -- the class `member` with the obligation's line, or the
        generic owning it."""
        key = (id(obligation), target, edge.site)
        if key in self._instantiation_seen:
            return
        self._instantiation_seen.add(key)
        where = member or f"'{obligation.owner}'"
        if obligation.line is not None:
            where = (where[:-1] + f":{obligation.line})" if member
                     else f"{where} (line {obligation.line})")
        ctx.diagnostics.append(Diagnostic(DiagnosticLevel.ERROR,
                                          message(where), edge.loc))

    def instantiation_copy(self, ctx, obligation: OwnCopyObligation,
                           target: 'OwnType', edge: 'OwnCopyEdge',
                           remedy: str = "", member: str = "") -> None:
        """Report, at the instantiating call or class formation `edge`, the
        copy obligation's read makes at this `Own[X]` instantiation: the
        twin's warning (its error for a type that has no copy); `remedy`
        names the signature that would lend instead, `member` the class
        member whose body copies (`member_site`). Once per site
        (`site_key`)."""
        loc = edge.loc
        key = (id(obligation), str(target), edge.site)
        if key in self._instantiation_seen:
            return
        self._instantiation_seen.add(key)
        dest = (f"{obligation.dest} in {member}" if member
                else obligation.dest)
        level, message = slot_message(
            target.wrapped, target.wrapped, dest, KIND_SLOT,
            obligation.hint, non_copyable=ctx.is_type_non_copyable(
                target.wrapped),
            remedy="use copy() to make this explicit" + remedy)
        ctx.diagnostics.append(Diagnostic(
            level, message, loc if loc is not None else obligation.diag.loc))


@dataclass(frozen=True, eq=False)
class OwnCopyEdge:
    """An instantiation of `callee` under `subst`.

    Concrete edges are discharged directly; an edge whose substitution still
    names a type parameter belongs to the enclosing generic body and is
    re-composed when that body is itself instantiated.
    """

    callee: object
    subst: tuple[tuple[str, TpyType], ...]
    is_record: bool = False
    # The call that instantiates it -- the outermost concrete call of a
    # re-composed chain -- where a copy only this instantiation makes is
    # reported (`KIND_OWN_INSTANTIATION`).
    loc: object = None
    # The call inferred its type arguments (none spelled): a remedy may name
    # the signature that would bind them otherwise.
    inferred: bool = False
    # Of an inferred call: per type parameter inference bound to a
    # non-plain `Own[X]`, the callee's parameter whose argument bound it and
    # that argument's name (None when it is no name).
    sources: tuple[tuple[str, str, 'str | None'], ...] = ()
    # Where the instantiation reports (`site_key`): its module and line,
    # as a SourceLocation names no file.
    site: tuple = ()
    # Of a forward: its substitution names only the enclosing class's type
    # parameters (`class_owns`), so the class's instantiation walks it.
    of_class: bool = False
    # The frame of the body's own generator expression: what it copies is
    # the enclosing body's, reported as that member's.
    inline: bool = False

    def key(self) -> tuple:
        """Identity for dedupe: a RecordInfo / FunctionInfo is not hashable.
        The call location is no part of it -- an instantiation is walked
        once, and the copies only it makes are replayed at each call that
        reaches it (`OwnCopyVerdicts.instantiation_copies`)."""
        return (id(self.callee), self.subst, self.is_record)


def site_key(module: str, loc: object) -> tuple:
    """The site an instantiation reports at: its module and line, so a
    line instantiating one generic several ways -- an annotation and the
    constructor it types, a static method spelled at its class's
    arguments -- reports each copy once, whichever module's analysis
    reaches it first."""
    if loc is None:
        return (module,)
    return (module, getattr(loc, "line", None))


def holds_only_values(typ: TpyType) -> bool:
    """Whether a copy of `typ` copies values only -- the test a loop
    variable whose source may change reads before taking its own copy (a
    tuple holding a reference type would copy the reference element)."""
    return not contains_reference_type(unwrap_qualifiers(typ))


def type_has_type_param(typ: TpyType | None) -> bool:
    """Whether `typ` names any type parameter."""
    return bool(inner_type_param_names(typ))


def copy_is_hedged(display: TpyType, target: TpyType,
                   kind: str = KIND_SLOT) -> bool:
    """Whether an owning sink's copy of `display` into `target` is the
    hedge an instantiation answers rather than a plain warning: a side names
    a type parameter, and the copied head is no reference type, which copies
    whatever its arguments are (an element copy names its elements, so its
    head does not settle it). The one rule of the copy diagnostic and of
    the record leaf it speaks for."""
    if not (type_has_type_param(target) or type_has_type_param(display)):
        return False
    head = unwrap_readonly(display)
    return not (kind != KIND_ELEMENTS and isinstance(head, NominalType)
                and not head.is_protocol and not head.is_value_type())


def reference_source_params(typ: TpyType) -> 'set[str] | None':
    """The type params whose instantiation decides whether copying `typ`
    copies a REFERENCE -- or None when it copies one whatever they are.

    `None` is the certain copy: a payload holding a reference type even with
    every parameter read as a value (`list[T]`, `Array[T, N]`, a plain
    `class GContainer[T]`, `tuple[T, Box]`) copies a struct at every
    instantiation, so no bound on `T` can make the copy unobservable.
    Otherwise every parameter the payload names may decide it.
    """
    if contains_reference_type(typ, open_params_as_values=True):
        return None
    return inner_type_param_names(typ)


def slot_message(display: TpyType, target: TpyType, dest: str, kind: str,
                 hint: str, non_copyable: bool, *,
                 remedy: str = "use copy() to make this explicit",
                 nocopy_hint: str = NOCOPY_REMEDIATION_HINT,
                 note: str = "") -> tuple[DiagnosticLevel, str]:
    """The diagnostic the monomorphic twin reports for this copy. A body's
    own sink passes the remedies its source earns (`copy_remedy`,
    `nocopy_hint`) and, for an element copy, the source's `note`."""
    if non_copyable:
        if display == target:
            return (DiagnosticLevel.ERROR,
                    f"cannot copy non-copyable type '{target}' into "
                    f"{dest}{nocopy_hint}")
        return (DiagnosticLevel.ERROR,
                f"cannot copy {display} into {dest} of type '{target}'; "
                f"target is non-copyable{nocopy_hint}")
    if kind == KIND_ELEMENTS:
        return (DiagnosticLevel.WARNING,
                f"copies {display} elements; {hint}{note}")
    # The copy is its own mutable value, whatever access the source had.
    return (DiagnosticLevel.WARNING,
            f"copies {unwrap_readonly(display)} into {dest}; {remedy}")


def warn_value_call_binding(ctx: 'SemanticContext', expr: TpyExpr,
                            dest: str) -> None:
    """A holder -- a local binding, a frame argument -- as the owning sink
    of a borrow-declared call sema stamped a fresh value
    (a fresh `TpyCallLike.result_form`): it holds a COPY (`P t =
    ::tpy::min_key(a, __tmp_1, f);`), so where a lent argument is still
    reached the copy is observable and is warned in the owning slots' words
    with `dest` named; `copy(...)` is the explicit spelling. A non-copyable
    type has no copy to make. A transient consumer is not a sink and says
    nothing."""
    found = value_call_copy_message(ctx, expr, dest)
    if found is not None:
        call, held, message = found
        ctx.copy_warning(message, call, held)


def value_call_copy_message(
        ctx: 'SemanticContext', expr: TpyExpr,
        dest: str) -> 'tuple[TpyCallLike, TpyExpr, str] | None':
    """The call, the value held and the owning-slot warning for a
    borrow-declared call stamped a fresh value whose copy into `dest` is
    observable, or None -- the value is the call itself, or a field /
    element read off it (`k = next(it, d).q` holds a copy of `q` as much as
    of the whole result). A non-copyable payload is an error here: there is
    no copy to make."""
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    held = expr
    while (isinstance(expr, TpySubscript)
           or (isinstance(expr, TpyFieldAccess) and expr.hidden_call is None
               and not is_property_getter_read(expr))):
        expr = expr.obj
        while isinstance(expr, TpyCoerce):
            expr = expr.expr
    # A value-shaped result copies nothing CPython would share.
    if not (isinstance(expr, TpyCallLike)
            and expr.result_form.copies_operand):
        return None
    payload = unwrap_readonly(unwrap_ref_type(ctx.get_expr_type(held)))
    if type_has_type_param(payload):
        # A generic body decides the verdict before instantiation, so the
        # copy is the hedged generic contract (the instantiation answers a
        # non-copyable payload); `list[T]` in place of `Iterable[T]` lends.
        if expr.copy_observable:
            ctx.defer_own_copy_verdict(payload, payload, dest, expr,
                                       source=held)
        return None
    if held is not expr and payload.is_value_type():
        return None
    non_copyable = ctx.is_type_non_copyable(payload)
    if not (expr.copy_observable or non_copyable):
        return None
    level, message = slot_message(payload, payload, dest,
                                  KIND_SLOT, "", non_copyable=non_copyable)
    if level is DiagnosticLevel.ERROR:
        raise ctx.error(message, expr)
    return expr, held, message


def hedge_message(display: TpyType, dest: str, kind: str,
                  hint: str) -> str:
    """The declaration-time contract, reported at the body line."""
    if kind == KIND_ELEMENTS:
        return f"may copy {display} elements {HEDGE_CLAUSE}; {hint}"
    return (f"may copy {display} into {dest} {HEDGE_CLAUSE}; "
            f"use copy() to make this explicit")


def _still_open(display: TpyType, target: TpyType) -> bool:
    return type_has_type_param(display) or type_has_type_param(target)


def _apply(ctx, type_ops, obligation: OwnCopyObligation,
           subst: dict, verdicts: OwnCopyVerdicts, copies: list,
           member: str = "") -> None:
    """Record the verdict this instantiation implies for one obligation; a
    copy only an `Own` instantiation makes joins `copies`, as does a
    composite leaf this instantiation makes `Own` -- each with the class
    `member` whose body it is in (`member_site`), "" for a function's."""
    if isinstance(obligation, OwnCompositeLeaf):
        leaf = type_ops.substitute_types(obligation.param, subst)
        if (isinstance(leaf, OwnType) and not leaf.is_plain()
                and not type_has_type_param(leaf)):
            copies.append((obligation, leaf, member))
        return
    if isinstance(obligation, OwnStorageArgFlow):
        arg = type_ops.substitute_types(obligation.param, subst)
        if (isinstance(arg, OwnType) and not arg.is_plain()
                and not type_has_type_param(arg)):
            copies.append((obligation, (
                type_ops.substitute_types(obligation.value, subst),
                type_ops.substitute_types(obligation.slot, subst)), member))
        return
    if isinstance(obligation, OpenClosureCapture):
        slot = type_ops.substitute_types(obligation.slot, subst)
        if (isinstance(slot, CallableType)
                and not type_has_type_param(slot.return_type)
                and slot.returns_borrow()):
            copies.append((obligation, slot, member))
        return
    display = type_ops.substitute_types(obligation.display_type, subst)
    target = type_ops.substitute_types(obligation.target_type, subst)
    if obligation.kind == KIND_OWN_INSTANTIATION:
        # A copy holding only values (`Own[str]`) is no divergence, as the
        # twin's `-> Own[str]` says nothing; one that cannot be made is.
        if (isinstance(target, OwnType) and not target.is_plain()
                and not _still_open(display, target)
                and not (holds_only_values(target.wrapped)
                         and not ctx.is_type_non_copyable(target.wrapped))):
            copies.append((obligation, target, member))
        return
    if _still_open(display, target):
        # A method-level type param this route does not bind; the method
        # call that binds it answers.
        return
    if (obligation.hands_on and isinstance(target, OwnType)
            and not target.is_plain()):
        return
    if not ctx.is_type_non_copyable(target):
        # A copyable instantiation says nothing the declaration-time hedge has
        # not already said, and the hedge is what a library author reads
        # without instantiating anything -- so it stands unchanged.
        return
    level, message = slot_message(display, target, obligation.dest,
                                  obligation.kind, obligation.hint,
                                  non_copyable=True)
    verdicts.record(obligation, level, message)


def _collapse_readonly(typ: TpyType) -> TpyType:
    """`readonly[readonly[T]]` is `readonly[T]`. A const clone forwarding
    `Rc[readonly[T]]` would otherwise compose one more layer per round and
    never reach an instantiation `seen` already holds."""
    while isinstance(typ, ReadonlyType) and isinstance(typ.wrapped, ReadonlyType):
        typ = typ.wrapped
    return typ.map_inner_types(_collapse_readonly)


def obligation_params(obligation) -> set[str]:
    """The type parameters an obligation's verdict depends on."""
    if isinstance(obligation, (OwnCompositeLeaf, OwnStorageArgFlow)):
        return {obligation.param.name}
    if isinstance(obligation, OpenClosureCapture):
        return set(inner_type_param_names(obligation.slot.return_type))
    return (set(inner_type_param_names(obligation.display_type))
            | set(inner_type_param_names(obligation.target_type)))


def _discharge_function(ctx, type_ops, fi, subst: dict, seen: set,
                        verdicts: OwnCopyVerdicts, copies: list,
                        member: str = "", class_part: bool = True):
    """`class_part` False: a member call, whose class's own obligations
    and forwards (`of_class`) its class's instantiation answers. A step
    of `_walk_edge`: yields each instantiation it forms, and is sent
    that one's copies."""
    for obligation in fi.own_copy_obligations:
        if not class_part and obligation.of_class:
            continue
        _apply(ctx, type_ops, obligation, subst, verdicts, copies,
               member)
    for forward in fi.own_copy_forwards:
        if not class_part and forward.of_class:
            continue
        composed = tuple(
            (name, _collapse_readonly(type_ops.substitute_types(value, subst)))
            for name, value in forward.subst
        )
        if forward.inline and not class_part:
            # A generator expression's frame is the member's own body: its
            # class part is answered where the member's is.
            found = []
            yield from _discharge_function(ctx, type_ops, forward.callee,
                                           dict(composed), seen, verdicts,
                                           found, member, class_part=False)
        else:
            found = yield OwnCopyEdge(forward.callee, composed,
                                      forward.is_record)
        if forward.inline and member:
            found = [(o, t, m or member) for o, t, m in found]
        copies.extend(found)


def _class_edge(ctx, type_ops, typ: TpyType) -> 'OwnCopyEdge | None':
    """The instantiation a generic class type at its arguments forms, as a
    written one does (`TypeOps._note_class_formation`); None for any other
    type."""
    if not isinstance(typ, NominalType) or not typ.type_args:
        return None
    info = ctx.registry.get_record_for_type(typ)
    if (info is None or not info.is_generic()
            or len(typ.type_args) != len(info.type_params)):
        return None
    pairs = tuple((n, v) for n, v in type_ops.record_subst(typ, info).items()
                  if isinstance(v, TpyType))
    return OwnCopyEdge(info, pairs, True) if pairs else None


def _owner_class_edge(ctx, edge: OwnCopyEdge) -> 'OwnCopyEdge | None':
    """The instantiation of the class declaring method `edge.callee` that a
    call of it at `edge.subst` names: the class part of the substitution.
    None for a function, or a method of a class taking no `Own[X]`
    argument as its identity."""
    fi = edge.callee
    if not class_own_arg_is_identity(fi):
        return None
    owner = ctx.registry.find_record_by_qname(fi.owning_type_qname)
    params = (owner.type_params or ()) if owner is not None else ()
    subst = dict(edge.subst)
    # An integer argument is no part of an edge (`record_own_copy_
    # instantiation`), so the formation's key holds only the types.
    pairs = tuple((p, subst[p]) for p in params if p in subst)
    if not pairs:
        return None
    return OwnCopyEdge(owner, pairs, True)


def _record_methods(record_info):
    """Every body the record owns: a monomorphic twin reports in all of them,
    whether or not this program calls them."""
    for overloads in record_info.methods.values():
        yield from overloads
    for prop in record_info.properties.values():
        yield from prop.accessors


def _discharge_record(ctx, type_ops, record_info, subst: dict,
                      seen: set, verdicts: OwnCopyVerdicts, copies: list):
    """A step of `_walk_edge`, as `_discharge_function` is."""
    for fi in _record_methods(record_info):
        yield from _discharge_function(ctx, type_ops, fi.root, subst, seen,
                                       verdicts, copies,
                                       member_site(record_info, fi.root))
    # A field's class is formed with this one (`class Outer[T]: b: Box[T]`
    # at `Outer[Own[P]]` holds a `Box[Own[P]]`), written nowhere else.
    for fld in record_info.fields:
        held = type_ops.substitute_types(fld.type, subst)
        for part in named_class_types(held):
            inner = _class_edge(ctx, type_ops, part)
            if inner is not None and not _nested_too_deep(verdicts, inner):
                copies.extend((yield inner))
    # An ancestor named only through an intermediate generic (`class Kid(
    # Mid[Cell])` over `class Mid[T](Base[T])`) is never spelled at concrete
    # args anywhere, so its instantiation exists only as this composition.
    for parent in record_info.parents:
        parent_args = parent.type_args if isinstance(parent, NominalType) else None
        if not parent_args:
            continue
        parent_info = ctx.registry.get_record_for_type(parent)
        if parent_info is None or not parent_info.type_params:
            continue
        composed = tuple(
            (name, type_ops.substitute_types(value, subst))
            for name, value in zip(parent_info.type_params, parent_args)
            if isinstance(value, TpyType)
        )
        if not composed:
            continue
        copies.extend((yield OwnCopyEdge(parent_info, composed, True)))


# How many instantiations of one class at ever larger arguments a field
# walk nests: a field naming its own class at a growing argument
# (`children: list[Node[list[T]]]`) forms a new instantiation at every
# level. One at arguments that do not grow (`kids: list[Node[B, A]]`)
# ranges over finitely many, which the walk's `seen` ends.
_FIELD_NESTING_BOUND = 4


def _nested_too_deep(verdicts: OwnCopyVerdicts, edge: OwnCopyEdge) -> bool:
    callee = id(edge.callee)
    size = _subst_size(edge.subst)
    return sum(1 for key in verdicts.walk_stack
               if key[0] == callee and _subst_size(key[1]) < size
               ) >= _FIELD_NESTING_BOUND


def _subst_size(pairs: tuple) -> int:
    """How many type nodes an edge's arguments hold."""
    total = 0
    stack = [v for _, v in pairs]
    while stack:
        t = stack.pop()
        if isinstance(t, TpyType):
            total += 1
            stack.extend(t.inner_types())
    return total


def member_site(record_info, fi) -> str:
    """A class member as a report at its class's instantiation names it:
    the member and the declaring file (a `SourceLocation` names a line
    only), so a member of a class from another module is found."""
    module = record_info.defining_module or record_info.module or "__main__"
    modules = getattr(get_current_compiler(), "modules", {})
    compiled = modules.get(module) or (next(
        (m for m in modules.values() if m.is_entry_point), None)
        if module == "__main__" else None)
    file = (compiled.path.name if compiled is not None
            else f"{module.replace('.', '/')}.py")
    return f"'{record_info.name}.{fi.name}' ({file})"


def discharge_edge(ctx, type_ops, edge: OwnCopyEdge, seen: set,
                   verdicts: OwnCopyVerdicts) -> None:
    """Discharge one instantiation rooted at a concrete call, and everything
    it instantiates in turn; the copies only an `Own` instantiation makes
    are reported at that call (`edge.loc`).

    `seen` stops a self-recursive generic (`f[T]` calling `f[T]`) and keeps
    a diamond of forwards from being walked twice; `verdicts` is the
    compilation's table the walk records into.
    """
    copies = _walk_edge(ctx, type_ops, edge, seen, verdicts)
    refused = next(((o, t, m) for o, t, m in copies
                    if isinstance(o, OwnCompositeLeaf)), None)
    if refused is not None:
        # The refusal stands alone: the copies it would also report are
        # of a form the instantiation does not build.
        _refuse_composite_leaf(ctx, edge, *refused)
        return
    mismatch = next(((o, t, m) for o, t, m in copies
                     if isinstance(o, OwnStorageArgFlow)), None)
    if mismatch is not None:
        verdicts.storage_arg_mismatch(ctx, *mismatch, edge)
        return
    remedy = (_owned_result_remedy(edge) if copies and edge.inferred
              else "")
    for obligation, target, member in copies:
        if isinstance(obligation, OpenClosureCapture):
            verdicts.closure_capture(ctx, obligation, target, edge, member)
            continue
        verdicts.instantiation_copy(ctx, obligation, target, edge, remedy,
                                    _at_line(member, obligation))


def _at_line(member: str, obligation: OwnCopyObligation) -> str:
    """`member` with the line of the obligation's read, "" for none."""
    if not member:
        return ""
    line = getattr(obligation.diag.loc, "line", None)
    if line is None:
        return member
    return member[:-1] + f":{line})"


def _refuse_composite_leaf(ctx, edge: OwnCopyEdge, leaf: OwnCompositeLeaf,
                           target: OwnType, member: str = "") -> None:
    callee = getattr(edge.callee, "name", "?")
    where = (f"'{leaf.param.name}'" if leaf.owner == callee
             else f"'{leaf.param.name}' of '{leaf.owner}'")
    inside = f"{where} appears inside '{leaf.composite}'"
    if edge.inferred:
        # The call wrote no type argument: name what inferred it.
        message = _inferred_refusal(edge, leaf, target, callee, inside)
    elif leaf.of_class:
        site = f" in {member}" if member else ""
        message = (f"`{target}` as a type argument of class "
                   f"'{leaf.owner}' is not supported yet: '{leaf.param.name}' "
                   f"appears inside '{leaf.composite}'{site}; use "
                   f"`{target.wrapped}` as the type argument, or write the "
                   f"class with `{target}` in place of '{leaf.param.name}'")
    else:
        message = (f"`{target}` as a type argument of '{callee}' is not "
                   f"supported yet: {inside}; use `{target.wrapped}` as the "
                   f"type argument (the argument is lent), or write the "
                   f"function with `{target}` in place of "
                   f"'{leaf.param.name}'")
    ctx.diagnostics.append(Diagnostic(DiagnosticLevel.ERROR, message,
                                      edge.loc))


def _inferred_refusal(edge: OwnCopyEdge, leaf: OwnCompositeLeaf,
                      target: OwnType, callee: str, inside: str) -> str:
    """The refusal at a call that wrote no type argument, naming the type
    parameter and the argument the binder bound to `Own[X]`."""
    owned = {n: v for n, v in edge.subst
             if isinstance(v, OwnType) and not v.is_plain()}
    if leaf.owner == callee and leaf.param.name in owned:
        name = leaf.param.name
    else:
        # A leaf of a generic the callee hands its parameter on to: the
        # callee's own parameter is known only when one binds `target`.
        matching = [n for n, v in owned.items() if v == target]
        name = matching[0] if len(matching) == 1 else None
    if name is None:
        return (f"'{callee}' infers `{target}` as a type argument, which is "
                f"not supported yet: {inside}; write the function with "
                f"`{target}` in place of '{leaf.param.name}'")
    value = owned[name]
    source = next(((p, a) for n, p, a in edge.sources if n == name), None)
    by = ""
    remedies = []
    if source is not None:
        pname, arg = source
        by = f" from '{arg}'" if arg else f" from the argument for '{pname}'"
        ptype = next((unwrap_readonly(unwrap_ref_type(p.type))
                      for p in getattr(edge.callee, "params", None) or ()
                      if p.name == pname), None)
        if isinstance(ptype, CallableType):
            remedies.append(f"pass a function returning `{value.wrapped}`")
    remedies.append(f"write the function with `{value}` in place of "
                    f"'{name}'")
    return (f"'{callee}' infers `{name} = {value}`{by}, which is not "
            f"supported yet: {inside}; {' or '.join(remedies)}"
            f"{_owned_result_remedy(edge, name)}")


def _owned_result_remedy(edge: OwnCopyEdge, name: 'str | None' = None) -> str:
    """Where a callable parameter's bare `T` result is what binds `T` to
    `Own[X]` at this call (`make: Fn[[], T]` given a fresh callable), the
    signature that keeps the two contracts apart and lends: the callable's
    result written `Own[T]`, which binds `T` to `X`. `name` limits it to
    that type parameter."""
    params = getattr(edge.callee, "params", None) or ()
    subst = dict(edge.subst)
    for p in params:
        ct = unwrap_readonly(unwrap_ref_type(p.type))
        ret = getattr(ct, "return_type", None)
        if (isinstance(ct, CallableType) and type(ret) is TypeParamRef
                and name in (None, ret.name)
                and isinstance(subst.get(ret.name), OwnType)
                # A parameter of `T` binds it too: the result alone is no
                # remedy there.
                and not any(ret.name in inner_type_param_names(t)
                            for t in ct.param_types)):
            fixed = replace(ct, return_type=OwnType(ret))
            return (f", or declare '{p.name}: {fixed}' so the result is "
                    f"lent")
    return ""


def _walk_edge(ctx, type_ops, edge: OwnCopyEdge, seen: set,
               verdicts: OwnCopyVerdicts) -> list:
    """`_walk_steps` of `edge`, and of every instantiation it forms in
    turn, run from a work list: a finite cycle can nest more instantiations
    than the Python stack holds frames."""
    frames = [_walk_steps(ctx, type_ops, edge, seen, verdicts)]
    sent: 'list | None' = None
    failed: 'BaseException | None' = None
    while True:
        try:
            if failed is not None:
                exc, failed = failed, None
                child = frames[-1].throw(exc)
            else:
                child = frames[-1].send(sent)
        except StopIteration as done:
            frames.pop()
            if not frames:
                return done.value
            sent = done.value
            continue
        except BaseException as exc:
            frames.pop()
            if not frames:
                raise
            failed = exc
            continue
        frames.append(_walk_steps(ctx, type_ops, child, seen, verdicts))
        sent = None


def _walk_steps(ctx, type_ops, edge: OwnCopyEdge, seen: set,
                verdicts: OwnCopyVerdicts):
    """Walk one instantiation once (`seen`), returning the copies only an
    `Own` instantiation makes in it and under it -- the walked one's
    recorded list on a repeat visit. Mutually recursive instantiations
    form a cycle (Tarjan's strongly connected component), every member of
    which reaches the same copies: a visit to a member still in progress
    adds nothing to the visitor's list, the members' lists flow into the
    outermost member's, and that one's list is recorded for every member
    once it finishes."""
    key = edge.key()
    stack = verdicts.walk_stack
    if key in seen:
        done = verdicts.instantiation_copies.get(key)
        if done is not None:
            return done
        # A member of a cycle still being walked: the visitor joins its
        # cycle; the visitor's callers join on return (below).
        root = key
        while root not in verdicts.walk_low:
            root = verdicts.cycle_owner[root]
        _lower_walk_low(verdicts, stack[-1], verdicts.walk_low[root])
        return []
    seen.add(key)
    index = len(stack)
    stack.append(key)
    verdicts.walk_low[key] = index
    copies: list = []
    subst = dict(edge.subst)
    try:
        if edge.is_record:
            yield from _discharge_record(ctx, type_ops, edge.callee, subst,
                                         seen, verdicts, copies)
        else:
            owner = _owner_class_edge(ctx, edge)
            if owner is not None and owner.key() not in seen:
                # No formation walked the class at these arguments (an
                # inferred static call): the call forms it.
                copies.extend((yield owner))
            yield from _discharge_function(ctx, type_ops, edge.callee,
                                           subst, seen, verdicts, copies,
                                           class_part=owner is None)
    finally:
        stack.pop()
        low = verdicts.walk_low.pop(key)
        if stack:
            _lower_walk_low(verdicts, stack[-1], low)
    members = verdicts.cycle_members.pop(key, [])
    if low < index:
        # Not the cycle's root: its copies reach the root through the
        # caller's list, its entry is written with the root's.
        owner = stack[low]
        verdicts.cycle_members.setdefault(owner, []).extend([key, *members])
        verdicts.cycle_owner[key] = owner
        for m in members:
            verdicts.cycle_owner[m] = owner
        return copies
    verdicts.instantiation_copies[key] = copies
    for m in members:
        verdicts.instantiation_copies[m] = copies
        verdicts.cycle_owner.pop(m, None)
    return copies


def _lower_walk_low(verdicts: OwnCopyVerdicts, key: object,
                    low: int) -> None:
    if verdicts.walk_low[key] > low:
        verdicts.walk_low[key] = low


def copy_result_under_write(receiver: TpyExpr) -> 'TpyCallLike | None':
    """The borrow-declared call whose COPY (`ResultForm.COPY`) a write
    through `receiver` would land in -- the receiver itself, or the object
    a field / element read off it lives in -- or None."""
    while True:
        if isinstance(receiver, TpyCoerce):
            receiver = receiver.expr
        elif isinstance(receiver, (TpyFieldAccess, TpySubscript)):
            receiver = receiver.obj
        else:
            break
    if (isinstance(receiver, TpyCallLike)
            and receiver.result_form is ResultForm.COPY):
        return receiver
    return None


def lost_write_message(call: TpyCallLike) -> str:
    """A write through a call's COPY result changes nothing the program can
    see again; CPython writes the element itself."""
    return (f"the result of {call.call_display} is a copy here (its source "
            f"is an iterator, or this is a generic body); a write through it "
            f"is lost -- bind it to a name first (the binding warns), or "
            f"spell the copy with copy(...)")
