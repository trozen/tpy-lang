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
    TpyCall, TpyExpr, TpyMethodCall, TpyStarUnpack, TpySubscript, walk_body_stmts, walk_expr_tree,
)
from .sema.analyzer import SemanticAnalyzer
from .sema.type_join import expression_spelling
from .thir import nodes as th
from .value_category import peel_coerce
from .thir.lower import iter_module_callables, iter_module_constructors
from .mir.call_contract import MIRCallSummary, MIRSummaryState
from .mir.collect import MIRAnalyses, MIRBodySource, enumerate_body_sources, lower_and_analyze
from .mir.coverage import call_place
from .mir.dependencies import MIRReferents, resolve_referents
from .mir.nodes import (
    MIRCall, MIRContainerElements, MIRContainerStructure, MIRField, MIRFunction, MIRPlace,
    MIRSlotKind, source_order, statement_call,
)
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
    # for `written` and the fields `written_fields` the callee writes under it.
    storage: str | None
    written_fields: tuple[str, ...]
    relation: 'MIRAliasRelation'


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
    while projections and isinstance(projections[-1], (MIRContainerStructure, MIRContainerElements)):
        projections = projections[:-1]
    return MIRPlace(place.root, projections)


def _relation(call: MIRCall, retained: int, written: int, written_place: MIRPlace, fn: MIRFunction,
              state: MIRReferents) -> MIRAliasRelation:
    """The relation of the argument for `retained` to the written storage:
    each argument's own place (its referent with the written one's root)
    against the object the write reaches."""
    slots = {slot.id: slot for slot in fn.slots}
    records = fn.record_layouts
    target = _object(written_place)

    def argument(index: int) -> MIRPlace | None:
        refs = resolve_referents(call_place(call, index, (), slots), state, slots, records)
        return next((r.place for r in refs if r.place.root == target.root), None)

    held, passed = argument(retained), argument(written)
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


def _place_spelling(fn: MIRFunction, place: MIRPlace) -> str | None:
    """A caller place as the user names it: its slot's source name, then
    each field; a container region is the container itself."""
    slot = next(s for s in fn.slots if s.id == place.root)
    name = slot.global_id.name if slot.kind is MIRSlotKind.GLOBAL and slot.global_id is not None else slot.name
    if not name or name.startswith("__") or slot.kind is MIRSlotKind.TEMPORARY:
        return None
    for projection in place.projections:
        if isinstance(projection, MIRField):
            name += f".{projection.id.name}"
        elif not isinstance(projection, (MIRContainerStructure, MIRContainerElements)):
            break
    return name


def argument_aliases(fn: MIRFunction, analyses: MIRAnalyses) -> tuple[MIRArgumentAlias, ...]:
    """The `argument_alias` conflicts the body's analyses classified, one per
    call and pair of parameters."""
    classes = analyses.retention_classes
    if classes is None:
        return ()
    blocks = {block.id: block for block in fn.blocks}
    found: dict[tuple[object, int, int], list[MIRArgumentAlias]] = {}
    for conflict in classes.argument_aliases:
        obligation = conflict.call
        assert obligation is not None
        key = (conflict.point, obligation.parameter, obligation.written.parameter)
        stmt = blocks[conflict.point.block].statements[conflict.point.index]
        call = statement_call(stmt)
        assert call is not None
        fields = tuple(step.name for step in obligation.written.path if isinstance(step, th.THIRFieldIdentity))
        relation = _relation(call, obligation.parameter, obligation.written.parameter, conflict.affected.place,
                             fn, analyses.dependencies.referents[conflict.point])
        found.setdefault(key, []).append(MIRArgumentAlias(
            call, call.site or stmt.loc, obligation.parameter, obligation.written.parameter,
            _place_spelling(fn, conflict.affected.place), fields, relation))
    # The retention conflicts come in slot-number order: the one a call
    # reports, and the order of a line's reports, follow the source instead.
    return tuple(sorted((min(aliases, key=_alias_order) for aliases in found.values()), key=_alias_order))


def _alias_order(alias: MIRArgumentAlias) -> tuple[object, ...]:
    return (source_order(alias.loc), alias.retained, alias.written, alias.storage is None, alias.storage or "",
            alias.written_fields, alias.relation.name)


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


def _remedy(summary: MIRCallSummary, index: int, argument: str | None) -> str:
    """A value-type argument is copied by a local binding of it; a reference
    type's object only by `copy()`."""
    if summary.parameters[index].type.is_value_type():
        return f"bind it to a local first ('v = {argument}')" if argument else "bind it to a local first"
    return f"pass a copy ('copy({argument})')" if argument else "pass a copy"


def alias_warning(alias: MIRArgumentAlias, program: MIRProgram, sites: CallSites) -> Diagnostic | None:
    identity = alias.call.summary.callee.identity
    names = _parameter_names(program, identity)
    if names is None:
        return None
    owner = identity.owner.rsplit(".", 1)[-1] if identity.owner is not None else None
    callee = f"{owner}.{identity.name}" if owner is not None else identity.name
    analyzed = sites.argument(alias.call, alias.retained, names[alias.retained])
    argument = _spelled(analyzed)
    storage = alias.storage
    if storage is None:
        written = _spelled(sites.argument(alias.call, alias.written, names[alias.written]))
        storage = written and written + "".join(f".{field}" for field in alias.written_fields)
    held, writer = names[alias.retained], names[alias.written]
    remedy = f"{_remedy(alias.call.summary, alias.retained, argument)} or restructure so the call does not hand over both"
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
