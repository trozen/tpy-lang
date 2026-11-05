"""Which nested `def` a read of its name reaches, from the function's
lexical structure.

A nested `def` is a C++ local of the block it is written in (a function
body, an `if` / `else` arm, a loop body, a `with` body, a `match` case, and
each of a `try`'s body, handlers, `else` and `finally`). A read reaches the
`def` of its name written earlier in its own block or in a block enclosing
it; a `def` in any other block is out of its reach. TPy refuses a `def`
that rebinds a name such an earlier `def` already bound, so at most one
`def` reaches any read.

The answer is computed once per function from the parse tree (statement
positions as block and statement indices), never from the analysis-time
statement stack: a function-state snapshot copies the statement lists.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..identity_map import IdentityMap
from ..parse.nodes import (TpyForEach, TpyFunction, TpyIf, TpyMatch,
                           TpyNestedDef, TpyStmt, TpyTry, TpyWhile, TpyWith)

# How a diagnostic names the compound statement a nested `def` sits in.
BLOCK_KEYWORD = {
    TpyIf: "if",
    TpyWhile: "while",
    TpyForEach: "for",
    TpyTry: "try",
    TpyWith: "with",
    TpyMatch: "match",
}

# A statement's position: per enclosing block, (which body of its owner,
# index in that body); the function body is body 0 of no owner.
Path = tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Def:
    node: TpyNestedDef
    path: Path
    # The compound statement whose body holds the def (None at the top).
    owner: TpyStmt | None
    # Walk index, in source order.
    order: int


@dataclass(frozen=True)
class Reach:
    """What a read of a nested def's name reaches: the def (`reached`),
    or the def bound in a block the read is past (`dead`, with the
    phrase naming that block), or neither."""
    reached: TpyNestedDef | None = None
    dead: TpyNestedDef | None = None
    dead_block: str | None = None


def block_phrase(owner: TpyStmt | None) -> str | None:
    """The compound statement `owner`, named for a diagnostic."""
    if owner is None:
        return None
    keyword = BLOCK_KEYWORD.get(type(owner))
    where = f" on line {owner.loc.line}" if owner.loc is not None else ""
    if keyword is None:
        return f"the enclosing block{where}"
    return f"the '{keyword}' block{where}"


def _source_order(stmt: TpyStmt) -> list[tuple[int, list[TpyStmt]]]:
    """`stmt`'s bodies with their `sub_bodies()` index, in source order: a
    `try` lists its handlers after its `else` and `finally` there."""
    bodies = list(enumerate(stmt.sub_bodies()))
    if isinstance(stmt, TpyTry):
        # sub_bodies(): try body, else, finally, then the handlers.
        bodies = [bodies[0], *bodies[3:], bodies[1], bodies[2]]
    return bodies


class DefReach:
    """The lexical positions of one function body's statements and of the
    nested defs among them."""

    def __init__(self, body: list[TpyStmt]) -> None:
        self.paths: IdentityMap = IdentityMap()
        self.owners: IdentityMap = IdentityMap()
        self.order: IdentityMap = IdentityMap()
        self.by_func: IdentityMap = IdentityMap()
        self.defs: dict[str, list[_Def]] = {}
        self._walk(body, (), 0, None)

    def _walk(self, body: list[TpyStmt], prefix: Path, which: int,
              owner: TpyStmt | None) -> None:
        for i, stmt in enumerate(body):
            path = prefix + ((which, i),)
            self.paths[stmt] = path
            self.owners[stmt] = owner
            self.order[stmt] = len(self.order)
            if isinstance(stmt, TpyNestedDef):
                self.by_func[stmt.func] = stmt
                self.defs.setdefault(stmt.func.name, []).append(
                    _Def(stmt, path, owner, self.order[stmt]))
                continue
            for k, sub in _source_order(stmt):
                self._walk(sub, path, k, stmt)

    def knows(self, stmt: TpyStmt) -> bool:
        return stmt in self.paths

    @staticmethod
    def _before_in_scope(d: _Def, at: Path) -> bool:
        """Whether `d` is written earlier in the block `at` sits in or in
        one enclosing it."""
        depth = len(d.path) - 1
        if depth >= len(at) or d.path[:depth] != at[:depth]:
            return False
        which, index = d.path[depth]
        return at[depth][0] == which and index < at[depth][1]

    def _inside(self, stmt: TpyStmt, region: TpyStmt) -> bool:
        """Whether `stmt` sits in compound statement `region`."""
        owner = self.owners.get(stmt)
        while owner is not None:
            if owner is region:
                return True
            owner = self.owners.get(owner)
        return False

    def _in_block(self, stmt: TpyStmt, d: _Def) -> bool:
        """Whether `stmt` sits in the very body that holds `d`."""
        at = self.paths[stmt]
        depth = len(d.path) - 1
        return (len(at) > depth and at[:depth] == d.path[:depth]
                and at[depth][0] == d.path[depth][0])

    def reach(self, name: str, stmt: TpyStmt) -> Reach:
        """What a read of `name` at statement `stmt` reaches. A def the
        read is past: written earlier, in a block the read is no longer in
        -- after its compound statement ends, or, for a `try`, in another
        of its bodies. A def in another arm of a compound statement the
        read is still in is neither (that path never bound it)."""
        at = self.paths.get(stmt)
        if at is None:
            return Reach()
        order = self.order[stmt]
        reached = None
        dead = None
        for d in self.defs.get(name, ()):
            if self._before_in_scope(d, at):
                reached = d.node
            elif d.order < order and d.owner is not None:
                past = (not self._in_block(stmt, d)
                        if isinstance(d.owner, TpyTry)
                        else not (stmt is d.owner
                                  or self._inside(stmt, d.owner)))
                if past:
                    dead = d
        if reached is not None:
            return Reach(reached=reached)
        if dead is not None:
            return Reach(dead=dead.node, dead_block=block_phrase(dead.owner))
        return Reach()


def def_reach(cache: IdentityMap, func: TpyFunction,
              stmt: TpyStmt | None = None) -> DefReach:
    """The `DefReach` of `func`'s body, kept in `cache`; computed again when
    `stmt` is not among its statements (one spliced into the body since)."""
    found = cache.get(func)
    if found is None or (stmt is not None and not found.knows(stmt)):
        found = DefReach(func.body)
        cache[func] = found
    return found


def reach_at(cache: IdentityMap, func: TpyFunction, name: str,
             stmt: TpyStmt) -> Reach:
    """`DefReach.reach` of a read of `name` at `stmt` in `func`."""
    return def_reach(cache, func, stmt).reach(name, stmt)
