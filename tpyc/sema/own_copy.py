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
`Diagnostic` the sink emitted, not its index: the loop-copy suppression in
`statements.py` deletes by index while a body is analysed, so an index
recorded here would drift onto an unrelated line, and a hedge that was
withdrawn that way is simply absent when the verdicts are composed.

It is a channel of its own rather than one of the two nearby ones because
what it defers is different: `representational_type_params` stamps a codegen
fact per call node, and `pending_borrow_checks` defers a check the analyzer
will run itself, while this defers a PROMOTION the instantiation supplies to
a diagnostic already in the list. See docs/ARCHITECTURE.md for the
comparison.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..diagnostics import Diagnostic, DiagnosticLevel, NOCOPY_REMEDIATION_HINT
from ..identity_map import IdentityMap
from ..parse import (ResultForm, TpyCall, TpyCoerce, TpyExpr, TpyFieldAccess,
                     TpyLambda, TpyName, TpySubscript, is_property_getter_read)
from ..parse.nodes import walk_expr_tree
from ..typesys import (NominalType, TpyType, TypeParamRef, unwrap_readonly,
                       unwrap_ref_type)

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


class OwnCopyVerdicts:
    """What one compilation's instantiations told the obligations they reached.

    Per compilation, keyed on the obligation by identity, so a program's
    verdict never lands on the declaring body's objects. Two calls at the
    same type report once and two calls at different types report twice, in
    the order they resolved (the corpus has one such line).
    """

    def __init__(self) -> None:
        self._by_obligation: IdentityMap = IdentityMap()

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
    `T | None` or `tuple[T, int32]` payload is just as unknowable as a bare
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


def warn_value_call_binding(ctx: 'SemanticContext', expr: TpyExpr,
                            dest: str) -> None:
    """A holder -- a local binding, a frame argument -- as the owning sink
    of a borrow-declared call sema stamped a fresh value
    (a fresh `TpyCall.result_form`): it holds a COPY (`P t =
    ::tpy::min_key(a, __tmp_1, f);`), so where a lent argument is still
    reached the copy is observable and is warned in the owning slots' words
    with `dest` named; `copy(...)` is the explicit spelling. A non-copyable
    type has no copy to make. A transient consumer is not a sink and says
    nothing."""
    found = value_call_copy_message(ctx, expr, dest)
    if found is not None:
        ctx.warning(found[1], found[0])


def value_call_copy_message(ctx: 'SemanticContext', expr: TpyExpr,
                            dest: str) -> 'tuple[TpyCall, str] | None':
    """The call and the owning-slot warning for a borrow-declared call
    stamped a fresh value whose copy into `dest` is observable, or None --
    the call itself, or a field / element read off it (`k = next(it, d).q`
    holds a copy of `q` as much as of the whole result). A non-copyable
    payload is an error here: there is no copy to make."""
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    held = expr
    while (isinstance(expr, TpySubscript)
           or (isinstance(expr, TpyFieldAccess) and expr.hidden_call is None
               and not is_property_getter_read(expr))):
        expr = expr.obj
        while isinstance(expr, TpyCoerce):
            expr = expr.expr
    if not (isinstance(expr, TpyCall) and expr.result_form.is_fresh):
        return None
    payload = unwrap_readonly(unwrap_ref_type(ctx.get_expr_type(held)))
    if type_has_type_param(payload):
        # A generic body decides the verdict before instantiation, so the
        # copy is the hedged generic contract (the instantiation answers a
        # non-copyable payload); `list[T]` in place of `Iterable[T]` lends.
        if expr.copy_observable:
            ctx.defer_own_copy_verdict(payload, payload, dest, expr)
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
    return expr, message


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
           subst: dict, verdicts: OwnCopyVerdicts) -> None:
    """Record the verdict this instantiation implies for one obligation."""
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
    verdicts.record(obligation, level, message)


def _discharge_function(ctx, type_ops, fi, subst: dict, seen: set,
                        verdicts: OwnCopyVerdicts) -> None:
    for obligation in fi.own_copy_obligations:
        _apply(ctx, type_ops, obligation, subst, verdicts)
    for forward in fi.own_copy_forwards:
        composed = tuple(
            (name, type_ops.substitute_types(value, subst))
            for name, value in forward.subst
        )
        discharge_edge(ctx, type_ops,
                       OwnCopyEdge(forward.callee, composed, forward.is_record),
                       seen, verdicts)


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
                      seen: set, verdicts: OwnCopyVerdicts) -> None:
    for fi in _record_methods(record_info):
        _discharge_function(ctx, type_ops, fi.root, subst, seen, verdicts)
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
                       OwnCopyEdge(parent_info, composed, True), seen, verdicts)


def discharge_edge(ctx, type_ops, edge: OwnCopyEdge, seen: set,
                   verdicts: OwnCopyVerdicts) -> None:
    """Discharge one instantiation, and everything it instantiates in turn.

    `seen` stops a self-recursive generic (`f[T]` calling `f[T]`) and keeps
    a diamond of forwards from being walked twice; `verdicts` is the
    compilation's table the walk records into.
    """
    key = edge.key()
    if key in seen:
        return
    seen.add(key)
    subst = dict(edge.subst)
    if edge.is_record:
        _discharge_record(ctx, type_ops, edge.callee, subst, seen, verdicts)
    else:
        _discharge_function(ctx, type_ops, edge.callee, subst, seen, verdicts)


def copy_result_under_write(receiver: TpyExpr) -> 'TpyCall | None':
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
    if isinstance(receiver, TpyCall) and receiver.result_form is ResultForm.COPY:
        return receiver
    return None


def lost_write_message(call: TpyCall) -> str:
    """A write through a call's COPY result changes nothing the program can
    see again; CPython writes the element itself."""
    return (f"the result of '{call.func_name}(...)' is a copy here (its source "
            f"is an iterator, or this is a generic body); a write through it "
            f"is lost -- bind it to a name first (the binding warns), or "
            f"spell the copy with copy(...)")


def declared_borrow_call_under(target: TpyExpr) -> 'TpyCall | None':
    """The first borrow-declared call anywhere in an augmented-assignment
    TARGET -- its receiver chain (`max(a, b, key=f).v`, a method hop
    `next(it, d).get_q().v`), an argument of a call on it (`wrap(next(it,
    d)).v`) or an index (`xs[next(it, d).v]`) -- or None. The render spells
    the whole target on both sides, which would run the key or advance the
    iterator twice; a lowering limitation of these calls
    (BUGS.md#augassign-call-receiver-double-eval), refused until aug-assign
    binds its target once. A lambda body is not entered: the call holding
    the lambda still runs twice, so a declared call inside it runs twice once
    the lambda is called -- the filed double evaluation."""
    found: list[TpyCall] = []

    def visit(node: TpyExpr) -> bool:
        if found or isinstance(node, TpyLambda):
            return False
        if (isinstance(node, TpyCall)
                and node.result_form is not ResultForm.NOT_DECLARED):
            found.append(node)
            return False
        return True

    walk_expr_tree(target, visit)
    return found[0] if found else None


def iterator_advanced_twice(exprs: 'list[TpyExpr]') -> 'tuple[TpyCall, str] | None':
    """The second borrow-declared call in one statement's expressions that
    hands back a reference to a STEP of an iterator an earlier one in the
    same statement also advances (`t = (next(g, d).v, next(g, d).v)`), with
    the iterator's name; or None. A step reference is valid only until the
    next advance, so the first reference would read the second step.

    A STOPGAP for BUGS.md#next-step-reference-outlived-by-advance, which
    MIR's step-loan model replaces: it sees only advances spelled in the
    statement, not a consumer advancing the iterator through a capture or a
    global. A COPY result holds no reference, a BORROW is a container's
    element, and a value-type element (`next(it, 0)`) is not
    borrow-declared, so none of them counts."""
    seen: set[str] = set()
    found: list[tuple[TpyCall, str]] = []

    def visit(node: TpyExpr) -> bool:
        if found or isinstance(node, TpyLambda):
            return False
        # Only a REFERENCE_VALUE result points at a step: a BORROW is a
        # container's element, which a second walk leaves in place.
        if (isinstance(node, TpyCall)
                and node.result_form is ResultForm.REFERENCE_VALUE):
            fi = node.resolved_function_info
            for idx in sorted(fi.root.element_borrows_from) if fi else ():
                if not 0 <= idx < len(node.args):
                    continue
                root = node.args[idx]
                while isinstance(root, (TpyCoerce, TpyFieldAccess,
                                        TpySubscript)):
                    root = root.expr if isinstance(root, TpyCoerce) else root.obj
                if not isinstance(root, TpyName):
                    continue
                if root.name in seen:
                    found.append((node, root.name))
                    return False
                seen.add(root.name)
        return True

    for expr in exprs:
        walk_expr_tree(expr, visit)
    return found[0] if found else None

