"""The MIR diagnostics of a normal compile: warnings at calls that hand one
object to two parameters of a callee writing through one of them while it
still uses the other (`argument_alias`), and at each lifetime hazard MIR
proves (`witness.decide`): storage replaced, ended or escaping while a
holder still uses it. Every other conflict stays evidence and prints
nothing."""

from collections.abc import Callable, Iterable, Iterator
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
from .mir.place_text import (
    MIRSpokenPlace, MIRStorageNames, source_name, spoken_place, steps_spelling as _steps_spelling,
)
from .mir.shapes import REGION_STEPS, MIRTupleIndex
from .mir.dependencies import MIRReferents, resolve_referents
from .mir.nodes import (
    MIRCall, MIREdge, MIRFunction, MIRGoto, MIRPlace, MIRPoint, call_place, source_order, statement_call, statement_target, view_member,
)
from .mir.reach import referent_walk
from .mir.witness import MIRHazard, MIRHazardKind, MIRWitness
from .typesys import unwrap_readonly, view_owned_leaf
from .mir.value import MIRValidationError
from .mir.definitions import MIRDefinitions, body_calls, constructor_record_qname
from .mir_workspace import MIRProgram


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
                     callees: frozenset[th.THIRFunctionIdentity], definitions: MIRDefinitions) -> int:
    """How many calls in a THIR body (`body_calls`, constructions included)
    reach a callee with obligations: each is checked only where MIR lowers
    the body and resolves its retention."""
    return sum(callee.identity in callees for _call, callee in body_calls(body, definitions))


def _place_spelling(fn: MIRFunction, place: MIRPlace) -> str | None:
    """A caller place as the user names it: its slot's source name
    (`place_text.source_name`), then its steps (`_steps_spelling`)."""
    name = source_name(next(s for s in fn.slots if s.id == place.root))
    return None if name is None else name + _steps_spelling(place.projections)


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


def lowered_bodies(modules: Iterable[tuple[TpyModule, SemanticAnalyzer, CodeGenContext, str]],
                   reasons: IdentityMap) -> Iterator[tuple[TpyModule, MIRBodySource]]:
    """For a compile without a verdict pass: every body the verdict pass
    lowers (`enumerate_body_sources` less its refusals), the one body
    selection every warning reads."""
    for module, analyzer, ctx, name in modules:
        for source in enumerate_body_sources(module, analyzer, ctx, name, reasons):
            if source.refusal is None and source.source is not None:
                yield module, source


def analyze_source(source: MIRBodySource, program: MIRProgram) -> tuple[MIRFunction, MIRAnalyses] | None:
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

    def source_text(self, loc: SourceLocation) -> str | None:
        """The source text a one-line span covers: how a warning spells an
        expression's temporary storage."""
        lines = self._module.source_lines
        if loc.end_line != loc.line or loc.end_column is None or not 0 < loc.line <= len(lines):
            return None
        return lines[loc.line - 1][loc.column:loc.end_column] or None

    def argument(self, call: MIRCall, index: int, name: str, resolved: tuple[str, str | None]) -> TpyExpr | None:
        """The analyzed argument a call binds to parameter `index` (`name`),
        found by its position and the callable sema resolved it to
        (`resolved`, as `_resolved_key` spells it): a bound receiver is the
        object before the dot. None when the call is not found, or a starred
        argument comes first."""
        loc = call.site
        found = (self._index().get((loc.line, loc.column, *resolved), [])
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


@dataclass(frozen=True)
class _Callee:
    """How a warning names a callee: its parameter names, its Python
    spelling, and the callable sema resolved its calls to (`_resolved_key`)."""
    names: tuple[str, ...]
    spelled: str
    resolved: tuple[str, str | None]


def _callee(program: MIRProgram, identity: th.THIRFunctionIdentity) -> _Callee | None:
    """A construction `R(...)` calls its record's constructor, which takes
    no receiver: spelled by the record, resolved to the record's `__init__`."""
    definition = program.workspace.definitions.get(identity)
    if definition is None:
        return None
    source = definition[1]
    names = tuple(p.name for p in th.effective_params(source))
    if isinstance(source, th.THIRConstructor):
        return _Callee(names, source.record_name,
                       (identity.name, constructor_record_qname(identity)))
    owner = identity.owner.rsplit(".", 1)[-1] if identity.owner is not None else None
    return _Callee(names,
                   f"{owner}.{identity.name}" if owner is not None else identity.name,
                   (identity.name, identity.owner))


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
    found = _callee(program, alias.call.summary.callee.identity)
    if found is None:
        return None
    names, callee = found.names, found.spelled
    retained = sites.argument(alias.call, alias.retained, names[alias.retained], found.resolved)
    passed = _member_argument(retained, alias.member)
    analyzed, argument = passed.expr, passed.spelled
    storage = alias.storage
    if storage is None:
        member = alias.written_member
        written = _member_argument(sites.argument(alias.call, alias.written, names[alias.written], found.resolved),
                                   member).spelled
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


# --- lifetime hazards ---------------------------------------------------------------

class MIRUnspellable(Exception):
    """A definite hazard whose storage no source name or expression reaches:
    a defect of the naming, which a strict (test) run surfaces."""


@dataclass(frozen=True)
class _Words:
    """The places of one body in a warning's words (`spoken_place`)."""
    fn: MIRFunction
    names: MIRStorageNames
    source: Callable[[SourceLocation], str | None]
    strict: bool

    def place(self, place: MIRPlace) -> MIRSpokenPlace:
        spoken = spoken_place(place, self.names, self.source)
        if spoken is None:
            if self.strict:
                raise MIRUnspellable(f"no source spelling for %{place.root.index} in {self.fn.id.declaration}")
            return MIRSpokenPlace("a temporary value", "fallback")
        return spoken

    def holder(self, place: MIRPlace) -> tuple[str, str]:
        """How a warning names a holder, and the verb of what it keeps."""
        spoken = self.place(place)
        if spoken.origin == "iteration":
            return f"the iteration over {spoken.quoted}", "walks"
        if spoken.origin == "fallback":
            return spoken.name, "refers to"
        if view_member(place) is not None:
            views = True
        else:
            slot = next(s for s in self.fn.slots if s.id == place.root)
            views = not place.projections and slot.shape.cls in (MIRClass.VIEW, MIRClass.CONTAINER_VIEW)
        return spoken.quoted, "views" if views else "refers to"

    def storage(self, place: MIRPlace) -> str:
        """Storage that ends or escapes, by where it comes from."""
        spoken = self.place(place)
        match spoken.origin:
            case "bound":
                return f"the object made for {spoken.quoted}"
            case "temporary":
                return f"the temporary {spoken.quoted}"
            case "fallback":
                return spoken.name
        if spoken.region == "elements":
            return f"an element of {spoken.quoted}"
        if spoken.region == "structure":
            return f"the elements of {spoken.quoted}"
        return spoken.quoted


def _point_loc(fn: MIRFunction, point: MIRPoint) -> SourceLocation | None:
    block = next(b for b in fn.blocks if b.id == point.block)
    return block.statements[point.index].loc if point.index < len(block.statements) else block.terminator.loc


def _first_read(fn: MIRFunction, reads: tuple[MIRPoint, ...]) -> int | None:
    """The line of the first read in source order: what the text names
    when several paths read."""
    locs = [loc for loc in (_point_loc(fn, p) for p in reads) if loc is not None]
    return min(locs, key=source_order).line if locs else None


def _owned_view_fix(fn: MIRFunction, holder: MIRPlace) -> str | None:
    """A view member made owned keeps its own copy: `make 'Tok.s' an owned 'str'`."""
    member = view_member(holder)
    owned = view_owned_leaf(unwrap_readonly(member.type)) if member is not None else None
    if member is None or owned is None:
        return None
    return f"make '{member.id.owner.name}.{member.id.name}' an owned '{owned}'"


def _body_name(fn: MIRFunction) -> str:
    # A body the lowering built names itself; a hand-built one has no source name.
    return f"'{fn.source_name}'" if fn.source_name else "the function"


def _replacement_text(witness: MIRWitness, words: _Words) -> tuple[str, SourceLocation | None]:
    fn, hazard = words.fn, witness.hazard
    assert isinstance(hazard.site, MIRPoint)
    stmt = next(b for b in fn.blocks if b.id == hazard.site.block).statements[hazard.site.index]
    target = words.place(hazard.target)
    holder, verb = words.holder(hazard.holder)
    line = _first_read(fn, witness.reads)
    action = "assignment" if statement_target(stmt) == hazard.target else (
        "call" if statement_call(stmt) is not None else "statement")
    use = "continues" if verb == "walks" else "is read" if verb == "views" else "is used"
    if (owned := _owned_view_fix(fn, hazard.holder)) is not None:
        owned = f", or {owned}"
    else:
        owned = ""
    if target.region != "structure" and not _frees(fn, hazard, verb == "views"):
        what = (f"this {action} overwrites {'an element of ' if target.region == 'elements' else ''}"
                f"{target.quoted} in place")
        fix = f"copy what {holder} refers to first, or use {holder} before this {action}"
        return (f"{what}, but {holder} still refers to it and sees the new value on line {line}; {fix}{owned}",
                stmt.loc)
    if target.region == "structure":
        what = f"this {action} may move the elements of {target.quoted}"
        kept = "them"
    elif target.region == "elements":
        what = f"this {action} frees an element of {target.quoted}"
        kept = "it"
    else:
        what = f"this {action} frees the old value of {target.quoted}"
        kept = "it"
    if verb == "walks":
        fix = (f"iterate over a copy ('copy({target.name})'), or make the changes after the loop"
               if target.region is not None else "make the change after the loop")
    elif target.region == "structure":
        fix = f"look it up again after this {action}, or copy what {holder} {verb} first"
    elif not hazard.target.projections and target.origin == "named":
        fix = "give the new value its own name"
    else:
        fix = f"copy what {holder} {verb} first, or use {holder} before this {action}"
    return (f"{what}, but {holder} still {verb} {kept} and {use} on line {line}; {fix}{owned}",
            stmt.loc)


def _frees(fn: MIRFunction, hazard: MIRHazard, views: bool) -> bool:
    """Whether the replacement frees what the holder keeps, rather than
    rewriting it where it lies: below the replaced place the holder's
    referent takes a container region step (a list's elements or shape), or
    ends at an owned buffer (a `str` / `bytes`) that a view holder views. A
    record field, an Optional record payload or a whole list / dict field
    object is overwritten in place, and the holder sees the new value."""
    slots = {s.id: s for s in fn.slots}
    for retained in hazard.retained:
        for r in retained:
            for w in hazard.written:
                if r.held or r.static or not r.place.lies_under(w.place):
                    continue
                if any(isinstance(step, REGION_STEPS) for step in r.place.projections[len(w.place.projections):]):
                    return True
                if views:
                    try:
                        end = referent_walk(r.place, slots, fn.record_layouts).end
                    except MIRValidationError:
                        return True
                    if end.cls is MIRClass.OWNED_LEAF:
                        return True
    return False


def _scope_text(witness: MIRWitness, words: _Words) -> tuple[str, SourceLocation | None]:
    fn, hazard = words.fn, witness.hazard
    assert isinstance(hazard.site, MIREdge)
    term = next(b for b in fn.blocks if b.id == hazard.site.source).terminator
    exit_loc = term.exit_loc if isinstance(term, MIRGoto) else None
    loc = exit_loc or term.loc
    ended = words.place(hazard.target)
    holder, verb = words.holder(hazard.holder)
    line = _first_read(fn, witness.reads)
    use = "is read" if verb == "views" else "is used"
    if ended.origin == "temporary":
        what = f"the temporary {ended.quoted} lives only until the end of this statement"
        fix = f"bind {ended.quoted} to a variable before this statement"
    else:
        when = "at the end of this block" if exit_loc is not None else "when control leaves its block here"
        what = f"{words.storage(hazard.target)} is freed {when}"
        span = words.fn and next((s.span for s in fn.slots if s.id == hazard.target.root), None)
        made = words.source(span) if span is not None and ended.origin == "bound" else None
        holder_name = words.place(hazard.holder)
        if made and holder_name.origin == "named" and not hazard.holder.projections:
            fix = f"assign '{holder_name.name} = {made}' directly"
        elif ended.origin == "fallback":
            fix = "bind the value to a variable that outlives the block"
        else:
            fix = f"create {ended.quoted} before the block"
    if (owned := _owned_view_fix(fn, hazard.holder)) is not None:
        fix += f", or {owned}"
    return f"{what}, but {holder} still {verb} it and {use} on line {line}; {fix}", loc


def _payload_text(witness: MIRWitness, words: _Words) -> tuple[str, SourceLocation | None]:
    fn, hazard = words.fn, witness.hazard
    assert isinstance(hazard.site, MIRPoint)
    wrapper = MIRPlace(hazard.target.root, hazard.target.projections[:-1])
    holder, verb = words.holder(hazard.holder)
    line = _first_read(fn, witness.reads)
    return (f"this assignment frees the value held in {words.place(wrapper).quoted}, but {holder} still "
            f"{verb} it and is used on line {line}; copy it first", _point_loc(fn, hazard.site))


def _return_text(witness: MIRWitness, words: _Words) -> tuple[str, SourceLocation | None]:
    fn, hazard = words.fn, witness.hazard
    assert isinstance(hazard.site, MIREdge)
    term = next(b for b in fn.blocks if b.id == hazard.site.source).terminator
    returned = unwrap_readonly(fn.return_type)
    owned = view_owned_leaf(returned)
    # A view hands over a copy as its owned type; a record or container is handed over by Own.
    fix = (f"return an owned '{owned}' instead" if owned is not None
           else f"declare the return type 'Own[{returned}]' to hand it over")
    return (f"the return type '{returned}' lends the result, but {words.storage(hazard.target)} is created inside "
            f"{_body_name(fn)} and freed when it returns; {fix}", term.loc)


def _store_text(witness: MIRWitness, words: _Words) -> tuple[str, SourceLocation | None]:
    fn, hazard = words.fn, witness.hazard
    origin = words.place(hazard.target)
    holder, verb = words.holder(hazard.holder)
    if witness.reads and words.place(MIRPlace(hazard.holder.root)).origin == "temporary":
        # The returned record itself, which the source only spells as the return expression.
        record = next(s for s in fn.slots if s.id == hazard.holder.root).type
        holder = f"'{_steps_spelling(hazard.holder.projections).lstrip('.')}' of the returned '{record}'"
    keeps = "keeps a view of it" if verb == "views" else "still refers to it"
    stores = witness.stores or witness.reads
    loc = min((_point_loc(fn, p) for p in stores), key=source_order) if stores else None
    if origin.origin == "temporary":
        what = f"the temporary {origin.quoted} lives only until the end of this statement"
    else:
        what = f"{words.storage(hazard.target)} is freed when {_body_name(fn)} returns"
    fix = _owned_view_fix(fn, hazard.holder) or f"store a copy in {holder}"
    return f"{what}, but {holder} {keeps}; {fix}", loc


_TEXTS = {
    MIRHazardKind.REPLACEMENT: _replacement_text,
    MIRHazardKind.SCOPE_END: _scope_text,
    MIRHazardKind.PAYLOAD_END: _payload_text,
    MIRHazardKind.RETURN_ESCAPE: _return_text,
    MIRHazardKind.STORE_ESCAPE: _store_text,
}


def definite_hazards(analyses: MIRAnalyses) -> tuple[MIRWitness, ...]:
    """The body's definite hazards (`MIRAnalyses.witnesses`), one per
    collapse key -- the kill, the affected storage's root, the holder -- in
    body order."""
    found: dict[tuple[object, ...], MIRWitness] = {}
    for witness in analyses.witnesses:
        if not witness.definite:
            continue
        hazard = witness.hazard
        key = (hazard.kind, hazard.site, hazard.target.root, hazard.holder)
        found.setdefault(key, witness)
    return tuple(found.values())


def hazard_warnings(fn: MIRFunction, analyses: MIRAnalyses, sites: 'CallSites', *,
                    strict: bool = False) -> list[Diagnostic]:
    """One analyzed body's lifetime warnings: each definite hazard
    (`definite_hazards`) at its kill, in Python terms, naming its read or
    its return."""
    witnesses = definite_hazards(analyses)
    if not witnesses:
        return []
    words = _Words(fn, MIRStorageNames(fn), sites.source_text, strict)
    warnings = []
    for witness in witnesses:
        text, loc = _TEXTS[witness.hazard.kind](witness, words)
        warnings.append(Diagnostic(DiagnosticLevel.WARNING, text, loc))
    return warnings


def body_warnings(fn: MIRFunction, analyses: MIRAnalyses, program: MIRProgram, sites: 'CallSites', *,
                  strict: bool = False) -> list[Diagnostic]:
    """Every MIR warning of one analyzed body: its argument aliases and its
    definite lifetime hazards."""
    return body_alias_warnings(fn, analyses, program, sites) + hazard_warnings(fn, analyses, sites, strict=strict)
