"""The declaration order of a resumable frame's fields.

A frame struct declares every piece of state its body keeps across a
suspension: hoisted locals, argument lifts, the `using` aliases loops spell
their fields through, loop / `with` / `try` machinery, rebind slots, frame
objects and sub-futures. C++ destroys members in reverse declaration order,
and a class-scope type can only name what precedes it, so two relations
decide the order:

* LIFETIME -- a field borrows another's storage. A field whose destruction
  may run user code (a generator's `finally`, a `__del__`) must be declared
  after everything it borrows, transitively, so it dies first. A field whose
  destruction runs nothing (a pointer, an owned list of ints) may sit
  anywhere: its borrows constrain nothing.
* SPELLING -- a field's C++ type names another member (`decltype` of a loop
  source that reads a local, a loop var spelled off that alias). The named
  member must come first whatever either one's destruction does.

`order_frame_fields` is a stable topological sort over both: fields keep the
order they are given unless an edge pulls something ahead of its turn.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .context import CodeGenError

if TYPE_CHECKING:
    from ..parse.nodes import SourceLocation


@dataclass
class FrameField:
    """One member of a frame struct (or a `using` alias among them)."""
    name: str
    # The declaration, indented and newline-terminated.
    decl: str
    # LIFETIME edges: the fields whose storage this one may point into.
    borrows: frozenset[str] = frozenset()
    # SPELLING edges: the members this one's C++ type names.
    spelled_from: frozenset[str] = frozenset()
    # Destroying this field may run user code (`TpyType.drop_runs_user_code`
    # of what it holds, False for a field that owns nothing).
    drop_runs_user_code: bool = False
    # What this field borrows is not known (a frame object bound from a shape
    # sema does not trace), so it cannot be declared ahead of anything.
    borrows_untraced: bool = False
    # The user-visible local this field backs, for diagnostics.
    local: str | None = None
    # A `using` alias rather than a data member.
    is_alias: bool = False
    loc: 'SourceLocation | None' = None


def order_frame_fields(fields: 'list[FrameField]') -> 'list[FrameField]':
    """`fields` in declaration order: each after its spelling edges and, when
    its destruction may run user code, after everything it borrows through
    any chain of lifetime edges. Otherwise the given order is kept -- a field
    is only ever pulled AHEAD of its turn, next to the one that needs it.

    A field whose borrows are untraced cannot be pulled ahead of anything it
    might borrow: pulled by a spelling edge it is a located reject, and a
    lifetime edge to it is not followed (it stays in its turn). A cycle is a
    located reject."""
    by_name = {f.name: f for f in fields}
    turn = {f.name: i for i, f in enumerate(fields)}
    closure_memo: dict[str, 'frozenset[str]'] = {}

    def lifetime_closure(name: str) -> 'frozenset[str]':
        """Every field `name` reaches through lifetime edges, stopping at an
        untraced one (whose own borrows are unknown)."""
        if name in closure_memo:
            return closure_memo[name]
        closure_memo[name] = frozenset()  # cycle guard: a cycle adds nothing
        out: set[str] = set()
        for b in by_name[name].borrows:
            dep = by_name.get(b)
            if dep is None or b == name:
                continue
            if dep.borrows_untraced and dep.drop_runs_user_code:
                continue
            out.add(b)
            out |= lifetime_closure(b)
        closure_memo[name] = frozenset(out)
        return closure_memo[name]

    ordered: list[FrameField] = []
    done: set[str] = set()

    def visit(f: FrameField, stack: 'list[FrameField]',
              root: FrameField, via_spelling: bool) -> None:
        if f.name in done:
            return
        for i, s in enumerate(stack):
            if s is f:
                _raise_cycle(stack[i:])
        if (f is not root and turn[f.name] > turn[root.name]
                and via_spelling and f.borrows_untraced
                and f.drop_runs_user_code):
            loc = next((s.loc for s in reversed(stack) if s.is_alias), f.loc)
            raise CodeGenError(
                f"cannot loop over generator '{f.local or f.name}' here: what "
                f"it borrows cannot be traced; loop over it in a helper "
                f"function",
                loc=loc)
        deps: list[tuple[str, bool]] = [
            (n, True) for n in f.spelled_from if n in by_name]
        if f.drop_runs_user_code:
            deps += [(n, False) for n in lifetime_closure(f.name)]
        for n, spelling in sorted(deps, key=lambda d: turn[d[0]]):
            visit(by_name[n], stack + [f], root, spelling)
        done.add(f.name)
        ordered.append(f)

    for f in fields:
        visit(f, [], f, False)
    return ordered


def _raise_cycle(cycle: 'list[FrameField]') -> 'None':
    alias = next((f for f in cycle if f.is_alias), None)
    if alias is not None:
        raise CodeGenError(
            "this loop's source reads the variable the loop itself "
            "binds, which the frame cannot lay out; rename the loop "
            "variable", loc=alias.loc)
    named = next((f for f in cycle if f.local is not None), cycle[0])
    raise CodeGenError(
        f"cannot hold '{named.local or named.name}' in this generator or "
        f"async function: it borrows storage that is itself declared "
        f"from it; bind it inside a helper function",
        loc=named.loc)
