"""The MIR diagnostics of a normal compile: warnings at calls that hand one
object to two parameters of a callee writing through one of them while it
still uses the other (`argument_alias`)."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import Enum, auto

from .codegen_cpp.context import CodeGenContext
from .diagnostics import Diagnostic, DiagnosticLevel
from .identity_map import IdentityMap
from .parse import SourceLocation, TpyModule
from .parse.nodes import (
    TpyCall, TpyExpr, TpyMethodCall, TpyStarUnpack, TpySubscript, TpyTupleLiteral, walk_body_stmts,
    walk_expr_tree,
)
from .sema.analyzer import SemanticAnalyzer
from .sema.type_join import expression_spelling
from .thir import nodes as th
from .thir.scalar_leaves import MIRClass
from .value_category import peel_coerce
from .thir.lower import iter_module_callables, iter_module_constructors
from .mir.call_contract import MIRAliasObligation, MIRCallSummary, MIRSummaryState
from .mir.collect import MIRAnalyses, MIRBodySource, enumerate_body_sources, lower_and_analyze
from .mir.shapes import REGION_STEPS, MIRTupleIndex, MIRUnionPayload
from .mir.dependencies import MIRReferents, resolve_referents
from .mir.nodes import MIRCall, MIRFunction, MIRPlace, MIRSlotKind, source_order, statement_call, call_place
from .mir.value import MIRField, MIRValidationError
from .mir_workspace import MIRProgram, resolved_calls


@dataclass(frozen=True)
class MIRArgumentAlias:
    """One call breaking a callee's no-alias obligation on one object: the
    argument for parameter `retained` points into `storage`, which the
    callee writes through parameter `written`."""
    call: MIRCall
    loc: SourceLocation | None
    retained: int
    written: int
    # The written storage in the caller's source spelling; None for storage
    # no source name reaches (a temporary), which is spelled by the argument
    # for `written` and the path `written_path` the callee writes under it.
    storage: str | None
    written_path: tuple[object, ...]
    relation: 'MIRAliasRelation'
    # The wrapper member of the retained parameter the callee still uses
    # (`object_selector` of the obligation's path): what the argument is
    # spelled by.
    member: tuple[object, ...] = ()
    # The member of the written parameter the write goes through.
    written_member: tuple[object, ...] = ()


class MIRAliasRelation(Enum):
    """How the argument the callee still uses relates to the storage it
    writes through the other parameter, by their places in the caller."""
    BOTH = auto()      # one object, passed to both parameters
    IS = auto()        # the argument is the written storage, reached through the other one
    INTO = auto()      # the argument lies strictly under the written storage
    CONTAINS = auto()  # the written storage is a proper part of the argument


def _object(place: MIRPlace) -> MIRPlace:
    """The object a write reaches: a container's shape write is a write of
    the container."""
    projections = place.projections
    while projections and isinstance(projections[-1], REGION_STEPS):
        projections = projections[:-1]
    return MIRPlace(place.root, projections)


def _selector(call: MIRCall, parameter: int, path: tuple[object, ...]) -> tuple[object, ...]:
    """The wrapper member a summary path under a call's parameter starts in
    (`MIRParameterBinding.object_selector`); a published path always enters
    one under a wrapper."""
    selector = call.summary.parameters[parameter].object_selector(path)
    if selector is None:
        raise MIRValidationError("published path enters no lent member")
    return selector


def _relation(call: MIRCall, obligation: MIRAliasObligation, written_place: MIRPlace, fn: MIRFunction,
              state: MIRReferents) -> MIRAliasRelation:
    """The relation of the argument for the retained parameter to the
    written storage: each argument's own object (its referent with the
    written one's root; a wrapper argument's member the path selects)
    against the object the write reaches."""
    slots = {slot.id: slot for slot in fn.slots}
    records = fn.record_layouts
    target = _object(written_place)

    def argument(index: int, path: tuple[object, ...]) -> MIRPlace | None:
        refs = resolve_referents(call_place(call, index, _selector(call, index, path), slots), state, slots, records)
        return next((r.place for r in refs if r.place.root == target.root), None)

    held = argument(obligation.parameter, obligation.path)
    passed = argument(obligation.written.parameter, obligation.written.path)
    if held == target:
        return MIRAliasRelation.BOTH if passed == target else MIRAliasRelation.IS
    if held is not None and target.projections[:len(held.projections)] == held.projections:
        return MIRAliasRelation.CONTAINS
    return MIRAliasRelation.INTO


def obligation_callees(program: MIRProgram) -> frozenset[th.THIRFunctionIdentity]:
    """The callees whose KNOWN summaries carry no-alias obligations."""
    return frozenset(identity for identity, result in program.workspace.summaries.items()
                     if result.state is MIRSummaryState.KNOWN and result.summary.obligations)


def obligation_calls(body: th.THIRFunction | th.THIRConstructor,
                     callees: frozenset[th.THIRFunctionIdentity]) -> int:
    """How many calls in a THIR body reach a callee with obligations: each is
    checked only where MIR lowers the body and resolves its retention."""
    return sum(call.resolved_callee.identity in callees for call in resolved_calls(body))


def _steps_spelling(steps: Iterable[object]) -> str:
    """Place or summary-path steps as Python spells them after their root,
    up to the first step it has no spelling for: a field `.name`, a tuple
    index `[i]`; a sum's payload has no token (`o.items`, `u.items`). A
    write in or under a container region is spelled by the container."""
    text = ""
    for step in steps:
        match step:
            case MIRField():
                text += f".{step.id.name}"
            case th.THIRFieldIdentity():
                text += f".{step.name}"
            case MIRTupleIndex():
                text += f"[{step.index}]"
            case MIRUnionPayload():
                pass
            case _:
                break
    return text


def _place_spelling(fn: MIRFunction, place: MIRPlace) -> str | None:
    """A caller place as the user names it: its slot's source name, then
    its steps (`_steps_spelling`)."""
    slot = next(s for s in fn.slots if s.id == place.root)
    name = slot.global_id.name if slot.kind is MIRSlotKind.GLOBAL and slot.global_id is not None else slot.name
    if not name or name.startswith("__") or slot.kind is MIRSlotKind.TEMPORARY:
        return None
    return name + _steps_spelling(place.projections)


@dataclass(frozen=True)
class _MemberArgument:
    """How a call's argument passes the wrapper member the callee uses:
    the expression it is passed as (a tuple literal's element, else the
    argument), its spelling, and whether that expression is the wrapper
    itself rather than the member (a named tuple, a union name)."""
    expr: TpyExpr | None
    spelled: str | None
    named: str | None = None


def _member_argument(expr: TpyExpr | None, member: tuple[object, ...]) -> _MemberArgument:
    """The argument a wrapper member is passed as: a tuple literal's element
    (`h.q` of `(h.q, 1)`), else the argument followed by the member's
    selectors (`t[0]`, a union's `u`)."""
    if member and expr is not None:
        literal = peel_coerce(expr)
        selector = member[0]
        if (isinstance(selector, MIRTupleIndex) and isinstance(literal, TpyTupleLiteral)
                and selector.index < len(literal.elements)):
            element = literal.elements[selector.index]
            return _MemberArgument(element, _spelled(element))
    spelled = _spelled(expr)
    return _MemberArgument(expr, spelled and spelled + _steps_spelling(member), spelled if member else None)


def argument_aliases(fn: MIRFunction, analyses: MIRAnalyses) -> tuple[MIRArgumentAlias, ...]:
    """The `argument_alias` conflicts the body's analyses classified, one per
    call and pair of parameters."""
    classes = analyses.retention_classes
    if classes is None:
        return ()
    blocks = {block.id: block for block in fn.blocks}
    found: dict[tuple[object, ...], list[MIRArgumentAlias]] = {}
    for conflict in classes.argument_aliases:
        obligation = conflict.call
        assert obligation is not None
        stmt = blocks[conflict.point.block].statements[conflict.point.index]
        call = statement_call(stmt)
        assert call is not None
        member = _selector(call, obligation.parameter, tuple(obligation.path))
        written_member = _selector(call, obligation.written.parameter, tuple(obligation.written.path))
        key = (conflict.point, obligation.parameter, member, obligation.written.parameter, written_member)
        relation = _relation(call, obligation, conflict.affected.place,
                             fn, analyses.dependencies.referents[conflict.point])
        found.setdefault(key, []).append(MIRArgumentAlias(
            call, call.site or stmt.loc, obligation.parameter, obligation.written.parameter,
            _place_spelling(fn, conflict.affected.place), tuple(obligation.written.path), relation,
            member, written_member))
    # The retention conflicts come in slot-number order: the one a call
    # reports, and the order of a line's reports, follow the source instead.
    return tuple(sorted((min(aliases, key=_alias_order) for aliases in found.values()), key=_alias_order))


def _alias_order(alias: MIRArgumentAlias) -> tuple[object, ...]:
    return (source_order(alias.loc), alias.retained, alias.written, alias.storage is None, alias.storage or "",
            _steps_spelling(alias.written_path), alias.relation.name, _steps_spelling(alias.member),
            _steps_spelling(alias.written_member))


def alias_check_bodies(modules: Iterable[tuple[TpyModule, SemanticAnalyzer, CodeGenContext, str]],
                       program: MIRProgram, reasons: IdentityMap
                       ) -> Iterator[tuple[TpyModule, MIRBodySource]]:
    """For a compile without a verdict pass: the bodies that call a callee
    with obligations, the only ones whose analyses can hold an argument
    alias."""
    callees = obligation_callees(program)
    if not callees:
        return
    for module, analyzer, ctx, name in modules:
        for source in enumerate_body_sources(module, analyzer, ctx, name, reasons):
            if (source.refusal is None and source.source is not None
                    and obligation_calls(source.source, callees)):
                yield module, source


def analyze_alias_body(source: MIRBodySource, program: MIRProgram) -> tuple[MIRFunction, MIRAnalyses] | None:
    """A body's lowering and analyses exactly as the verdict pass makes them
    (`collect.lower_and_analyze`)."""
    lowered, analyses = lower_and_analyze(source, program.definitions, program.workspace)
    return None if analyses is None else (lowered, analyses)


# --- spelling ---------------------------------------------------------------------

def _resolved_key(node: TpyCall | TpyMethodCall) -> tuple[str, str | None] | None:
    """The callable sema resolved a call to, as a callee identity names it."""
    fi = node.resolved_function_info
    if fi is None:
        return None
    root = fi.root
    return root.name, root.owning_type_qname if root.is_method else None


class CallSites:
    """A module's analyzed calls by source position and resolved callee:
    what a warning spells its arguments from."""

    def __init__(self, module: TpyModule, analyzer: SemanticAnalyzer) -> None:
        self._module, self._analyzer = module, analyzer
        self._calls: dict[tuple[int, int, str, str | None], list[TpyCall | TpyMethodCall]] | None = None

    def _index(self) -> dict[tuple[int, int, str, str | None], list[TpyCall | TpyMethodCall]]:
        if self._calls is None:
            calls: dict[tuple[int, int, str, str | None], list[TpyCall | TpyMethodCall]] = {}
            bodies = [func.body for func, _owner in iter_module_callables(self._module, self._analyzer)]
            bodies += [ctor.body for _record, ctor, _owner in iter_module_constructors(self._module, self._analyzer)]
            for body in (*bodies, self._module.top_level_stmts):
                found: dict[tuple[int, int, str, str | None], list[TpyCall | TpyMethodCall]] = {}

                def visit(expr: TpyExpr) -> bool:
                    if isinstance(expr, (TpyCall, TpyMethodCall)) and expr.loc is not None:
                        key = _resolved_key(expr)
                        if key is not None:
                            found.setdefault((expr.loc.line, expr.loc.column, *key), []).append(expr)
                    return True

                walk_body_stmts(body, lambda expr: walk_expr_tree(expr, visit), lambda _stmt: None)
                # A clone of an `@auto_readonly` or `auto_own` def copies its
                # body at the same positions: the first body's calls answer.
                for key, nodes in found.items():
                    calls.setdefault(key, nodes)
            self._calls = calls
        return self._calls

    def argument(self, call: MIRCall, index: int, name: str) -> TpyExpr | None:
        """The analyzed argument a call binds to parameter `index` (`name`):
        a bound receiver is the object before the dot. None when the call
        is not found, or a starred argument comes first."""
        identity = call.summary.callee.identity
        loc = call.site
        found = (self._index().get((loc.line, loc.column, identity.name, identity.owner), [])
                 if loc is not None else [])
        if len(found) != 1:
            return None
        node = found[0]
        if call.bound_receiver:
            if not isinstance(node, TpyMethodCall):
                return None
            if index == 0:
                return node.obj
            index -= 1
        if any(isinstance(arg, TpyStarUnpack) for arg in node.args[:index + 1]):
            return None
        if index < len(node.args):
            return node.args[index]
        return node.kwargs.get(name)


def _spelled(expr: TpyExpr | None) -> str | None:
    return expression_spelling(expr) if expr is not None else None


def _parameter_names(program: MIRProgram, identity: th.THIRFunctionIdentity) -> tuple[str, ...] | None:
    definition = program.workspace.definitions.get(identity)
    return None if definition is None else tuple(p.name for p in th.effective_params(definition[1]))


def _remedy(summary: MIRCallSummary, index: int, member: tuple[object, ...], passed: _MemberArgument) -> str:
    """A value-type argument is copied by a local binding of it; a reference
    type's object only by `copy()`. A wrapper argument is judged by the
    member the callee still uses (a tuple is a value type even when it lends
    a record), and its copy is bound to a local first: neither a union nor a
    tuple argument takes a `copy()` call in place
    (BUGS.md#record-field-to-wrapper-parameter-rejects; a tuple element lent
    out of a temporary is refused), and a named tuple is rebuilt around the
    bound copy. An Optional keeps `copy(o)`: no copy of one compiles yet in
    any form (BUGS.md#optional-member-own-copy-no-render)."""
    binding = summary.parameters[index]
    lent = binding.lent_object(member) if member else None
    argument = passed.spelled
    if (lent.type if lent is not None else binding.type).is_value_type():
        return f"bind it to a local first ('v = {argument}')" if argument else "bind it to a local first"
    if argument and binding.cls is MIRClass.TUPLE and member:
        bound = f"pass a copy bound to a local ('v = copy({argument})'"
        if passed.named is None:
            return bound + ")"
        elements = ["v" if k == member[0].index else f"{passed.named}[{k}]"
                    for k in range(len(binding.value.members))]
        # A one-element tuple needs its trailing comma.
        return bound + f", then '({', '.join(elements)}{',' if len(elements) == 1 else ''})')"
    if passed.named is not None and binding.cls is MIRClass.UNION:
        return f"pass a copy bound to a local ('v = copy({passed.named})')"
    return f"pass a copy ('copy({argument})')" if argument else "pass a copy"


def alias_warning(alias: MIRArgumentAlias, program: MIRProgram, sites: CallSites) -> Diagnostic | None:
    identity = alias.call.summary.callee.identity
    names = _parameter_names(program, identity)
    if names is None:
        return None
    owner = identity.owner.rsplit(".", 1)[-1] if identity.owner is not None else None
    callee = f"{owner}.{identity.name}" if owner is not None else identity.name
    retained = sites.argument(alias.call, alias.retained, names[alias.retained])
    passed = _member_argument(retained, alias.member)
    analyzed, argument = passed.expr, passed.spelled
    storage = alias.storage
    if storage is None:
        member = alias.written_member
        written = _member_argument(sites.argument(alias.call, alias.written, names[alias.written]), member).spelled
        storage = written and written + _steps_spelling(alias.written_path[len(member):])
    held, writer = names[alias.retained], names[alias.written]
    if alias.retained == alias.written:
        # Two members of one tuple parameter: each is named by its member.
        held += _steps_spelling(alias.member)
        writer += _steps_spelling(alias.written_member)
    remedy = (f"{_remedy(alias.call.summary, alias.retained, alias.member, passed)} "
              "or restructure so the call does not hand over both")
    if alias.relation is MIRAliasRelation.BOTH:
        both = argument or storage
        subject = f"'{both}' is passed" if both else "one object is passed"
        message = (f"{subject} as both '{held}' and '{writer}' of '{callee}', which modifies it through "
                   f"'{writer}' while it still uses '{held}'; {remedy}")
        return Diagnostic(DiagnosticLevel.WARNING, message, alias.loc)
    subject = f"argument '{argument}'" if argument else f"the argument for parameter '{held}'"
    # A slice of an owned leaf views its storage under the leaf's own place
    # (MIR places have no slice step), so the argument lies strictly inside.
    # An Optional field passed as itself resolves to its payload, a step with
    # no Python spelling: spelled as the storage it is, it IS that storage.
    relation = alias.relation
    if relation is MIRAliasRelation.INTO and argument is not None and argument == storage:
        relation = MIRAliasRelation.IS
    if relation is MIRAliasRelation.IS and not (
            analyzed is not None and isinstance(peel_coerce(analyzed), TpySubscript)):
        message = (f"{subject} is what '{callee}' modifies through parameter '{writer}' "
                   f"while it still uses '{held}'; {remedy}")
        return Diagnostic(DiagnosticLevel.WARNING, message, alias.loc)
    target = f"'{storage}'" if storage else "storage"
    verb = "contains" if alias.relation is MIRAliasRelation.CONTAINS else "points into"
    message = (f"{subject} {verb} {target}, which '{callee}' modifies through parameter "
               f"'{writer}' while it still uses '{held}'; {remedy}")
    return Diagnostic(DiagnosticLevel.WARNING, message, alias.loc)


def body_alias_warnings(fn: MIRFunction, analyses: MIRAnalyses, program: MIRProgram,
                        sites: CallSites) -> list[Diagnostic]:
    """One analyzed body's argument-alias warnings."""
    return [w for w in (alias_warning(alias, program, sites) for alias in argument_aliases(fn, analyses))
            if w is not None]
