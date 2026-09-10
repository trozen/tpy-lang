"""The owning-slot copy contract of a generic body.

A body that stores an open `T` into an owning slot MAY copy, and its author
should read that at DECLARATION time -- a library generic has no
instantiation to consult, and copyable is TPy's default, so no bound is
required to write one. Each sink therefore warns at the body line, in the
hedged form (`may copy T into <sink> if not a value type`), and the warning
stands whatever the program instantiates. Exactly two things silence it:
`copy()` at the slot, which is the author saying the copy is intended, and a
`T: ValueType` bound, under which a reference-type copy cannot happen.
`T: Copyable` does not -- copyable is TPy's default, so that bound only rules
out a non-copyable payload; it does not state an intent to copy.

Whether the copy is LEGAL is still the instantiation's to answer, because
only it knows the payload. So the sink also records the site as an
obligation, and a non-copyable instantiation (`@nocopy`, or a record with
`__del__`, which deletes the generated copy ctor) rewrites that same
diagnostic into the located `cannot copy non-copyable type 'X'` error --
one diagnostic object, one line, promoted in place. A copyable instantiation
rewrites nothing: it has nothing to add to what the hedge already said.

The obligation holds the `Diagnostic` the sink emitted, not its index: the
loop-copy suppression in `statements.py` deletes by index while a body is
analysed, so an index recorded here would drift onto an unrelated line.

It is a channel of its own rather than one of the two nearby ones because
what it defers is different: `representational_type_params` stamps a codegen
fact per call node, and `pending_borrow_checks` defers a check the analyzer
will run itself, while this defers a PROMOTION the instantiation supplies to
a diagnostic already in the list. See docs/ARCHITECTURE.md for the
comparison.
"""
from dataclasses import dataclass, field

from ..diagnostics import Diagnostic, DiagnosticLevel, NOCOPY_REMEDIATION_HINT
from ..typesys import NominalType, TpyType, TypeParamRef

# The hedge clause. The body cannot know whether the payload is a value type,
# and says so; only a non-copyable instantiation replaces the line.
HEDGE_CLAUSE = "if not a value type"

# Message kinds. `SLOT` is the whole-value store (`.append`, `Own[T]` param,
# `Own[T]` return, `yield`, field / container assign); `ELEMENTS` is the
# per-element copy a container conversion makes.
KIND_SLOT = "slot"
KIND_ELEMENTS = "elements"


@dataclass
class OwnCopyObligation:
    """One owning-slot copy site in a body whose payload is still open."""

    display_type: TpyType
    target_type: TpyType
    dest: str
    kind: str
    hint: str
    diag: Diagnostic
    diagnostics: list[Diagnostic]
    # Messages already applied, so two calls at the same type report once and
    # two calls at different types report twice (the corpus has one such line).
    applied: set[str] = field(default_factory=set)
    # Whether the placeholder is still in the diagnostics list; resolved once,
    # at the first discharge, since the only withdrawal happens during body
    # analysis.
    alive: bool | None = None


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

    def key(self) -> tuple:
        """Identity for dedupe: a RecordInfo / FunctionInfo is not hashable."""
        return (id(self.callee), self.subst, self.is_record)


def contains_reference_type(typ: TpyType) -> bool:
    """True when copying `typ` copies a reference type -- the copy question.

    Recursive, because a structural type answers `is_value_type()` for its
    own shape and not for what it holds: `TupleType` hardcodes True, so
    `tuple[str, Node]` is a value type that copies a `Node`. An open
    `TypeParamRef` counts as a possible reference; at discharge the payload
    is fully substituted, so the answer there is definite.

    Owned by this module rather than by `compatibility.py` so the sink that
    RECORDS the obligation and the discharge that ANSWERS it ask one
    question; a second predicate would let the two disagree.
    """
    if not typ.is_value_type():
        return True
    return any(contains_reference_type(inner) for inner in typ.inner_types())


def type_param_names(typ: TpyType | None) -> set[str]:
    """Every type parameter `typ` names, at any depth.

    The copy verdict keys on *contains* rather than *is* a `TypeParamRef`: a
    `T | None` or `tuple[T, Int32]` payload is just as unknowable as a bare
    `T`, and answering it confidently is a false positive at a value `T`.
    """
    names: set[str] = set()
    if typ is None:
        return names
    stack = [typ]
    while stack:
        current = stack.pop()
        if isinstance(current, TypeParamRef):
            names.add(current.name)
            continue
        stack.extend(current.inner_types())
    return names


def type_has_type_param(typ: TpyType | None) -> bool:
    """Whether `typ` names any type parameter."""
    return bool(type_param_names(typ))


def reference_source_params(typ: TpyType) -> 'set[str] | None':
    """The type params whose instantiation decides whether copying `typ`
    copies a REFERENCE -- or None when it copies one whatever they are.

    A named non-value type is the `None` case: `list[T]`, `Array[T, N]` and
    a plain `class GContainer[T]` are structs that get copied at every
    instantiation, so no bound on `T` can make the copy unobservable. Every
    other shape delegates: a bare `TypeParamRef` answers for itself, and a
    tuple / `Optional` / union / qualifier wrapper answers for what it holds
    (`tuple` in particular reports `is_value_type()` True regardless of its
    elements, which is why the walk cannot stop at the outer type).

    Same question as `contains_reference_type`, which asks whether the answer
    is "yes" for a payload that is already concrete; this one asks WHICH
    params the answer still depends on.
    """
    if isinstance(typ, TypeParamRef):
        return {typ.name}
    if isinstance(typ, NominalType) and not typ.is_value_type():
        return None
    names: set[str] = set()
    for inner in typ.inner_types():
        sub = reference_source_params(inner)
        if sub is None:
            return None
        names |= sub
    return names


def slot_message(display: TpyType, target: TpyType, dest: str, kind: str,
                 hint: str, non_copyable: bool) -> tuple[DiagnosticLevel, str]:
    """The diagnostic the monomorphic twin reports for this copy."""
    if non_copyable:
        if display == target:
            return (DiagnosticLevel.ERROR,
                    f"cannot copy non-copyable type '{target}' into "
                    f"{dest}{NOCOPY_REMEDIATION_HINT}")
        return (DiagnosticLevel.ERROR,
                f"cannot copy {display} into {dest} of type '{target}'; "
                f"target is non-copyable{NOCOPY_REMEDIATION_HINT}")
    if kind == KIND_ELEMENTS:
        return (DiagnosticLevel.WARNING, f"copies {display} elements; {hint}")
    return (DiagnosticLevel.WARNING,
            f"copies {display} into {dest}; use copy() to make this explicit")


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
           subst: dict) -> None:
    """Give one obligation the verdict this instantiation implies."""
    if obligation.alive is None:
        obligation.alive = any(d is obligation.diag
                               for d in obligation.diagnostics)
    if not obligation.alive:
        # The loop-copy path already withdrew the placeholder: consuming
        # iteration moves the element, so there is no copy to report.
        return
    display = type_ops.substitute_types(obligation.display_type, subst)
    target = type_ops.substitute_types(obligation.target_type, subst)
    if _still_open(display, target):
        # A method-level type param this route does not bind; the method
        # call that binds it answers.
        return
    if not ctx.is_type_non_copyable(target):
        # A copyable instantiation says nothing the declaration-time hedge has
        # not already said, and the hedge is what a library author reads
        # without instantiating anything -- so it stands unchanged.
        return
    level, message = slot_message(display, target, obligation.dest,
                                  obligation.kind, obligation.hint,
                                  non_copyable=True)
    if message in obligation.applied:
        return
    if not obligation.applied:
        obligation.diag.level = level
        obligation.diag.message = message
    else:
        # Two non-copyable instantiations of one body: report both, in the
        # order they resolved.
        index = next(i for i, d in enumerate(obligation.diagnostics)
                     if d is obligation.diag)
        obligation.diagnostics.insert(
            index + len(obligation.applied),
            Diagnostic(level, message, obligation.diag.loc))
    obligation.applied.add(message)


def _discharge_function(ctx, type_ops, fi, subst: dict, seen: set) -> None:
    for obligation in fi.own_copy_obligations:
        _apply(ctx, type_ops, obligation, subst)
    for forward in fi.own_copy_forwards:
        composed = tuple(
            (name, type_ops.substitute_types(value, subst))
            for name, value in forward.subst
        )
        discharge_edge(ctx, type_ops,
                       OwnCopyEdge(forward.callee, composed, forward.is_record),
                       seen)


def _record_methods(record_info):
    """Every body the record owns: a monomorphic twin reports in all of them,
    whether or not this program calls them."""
    for overloads in record_info.methods.values():
        yield from overloads
    for prop in record_info.properties.values():
        yield prop.getter
        if prop.setter is not None:
            yield prop.setter


def _discharge_record(ctx, type_ops, record_info, subst: dict,
                      seen: set) -> None:
    for fi in _record_methods(record_info):
        _discharge_function(ctx, type_ops, fi.root, subst, seen)
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
        discharge_edge(ctx, type_ops,
                       OwnCopyEdge(parent_info, composed, True), seen)


def discharge_edge(ctx, type_ops, edge: OwnCopyEdge, seen: set) -> None:
    """Discharge one instantiation, and everything it instantiates in turn.

    `seen` stops a self-recursive generic (`f[T]` calling `f[T]`) and keeps
    a diamond of forwards from being walked twice.
    """
    key = edge.key()
    if key in seen:
        return
    seen.add(key)
    subst = dict(edge.subst)
    if edge.is_record:
        _discharge_record(ctx, type_ops, edge.callee, subst, seen)
    else:
        _discharge_function(ctx, type_ops, edge.callee, subst, seen)


