"""The order CPython runs a program's imports in, and the import-time reads
of a module still being imported that CPython fails.

CPython runs a module's top-level statements once, when it is first
imported: from the entry module, depth first, each module's imports in
source order, a dotted module's parents first, and a module already
started returning at once. While a module R runs, a module P that started
R's chain is suspended at that import statement and has bound only what
its statements before it bound; an import-time read in R of any other
name of P fails under CPython. TPy binds every top-level name of a module
before reading any, so such a program compiles and runs; the interpreter
here runs the program's top-level statements (`TpyModule.run_statements`)
as CPython does, keeping each module's namespace over time, so the
compiler can warn at the reads CPython rejects.

It runs once every read is known (`drain`): a star import's TPy bindings
and the macro-added names are only then. Library and plugin modules are
opaque: CPython does not run their TPy source, so they are complete at
their first import, with TPy's surface as their namespace.
"""
from __future__ import annotations

import bisect
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping

from .parse.imports import EvalKind
from .parse.nodes import (
    AllUpdate, ImportRecord, RelativeImportKey, RunStatement, SourceLocation,
    TpyVarDecl,
)
from .prescan import local_names_of
from .symbol_binding import lookup_imported, star_exportable

if TYPE_CHECKING:
    from .compiler import CompiledModule


_OUTSIDE = "move the shared definitions into a module outside the cycle"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _quoted(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


class ReadKind(Enum):
    """How a failed import-time read reads its module."""
    # `P.X`: an attribute of the module.
    MEMBER = "member"
    # `pkg.a` while `pkg.a` itself is still being imported.
    SUBMODULE = "submodule"
    # `from P import X`.
    FROM_IMPORT = "from-import"
    # `from P import *` binding fewer names under CPython than under TPy.
    STAR = "star"
    # `from P import *` reading a name P's bound `__all__` lists.
    STAR_ALL = "star-all"


class OriginKind(Enum):
    """How a name came to be bound in a module's namespace."""
    # A statement that runs unconditionally.
    BOUND = "bound"
    # A compound statement (`if`, `for`, `try`, ...), which may not bind
    # it: taken as bound from its step, so an untaken branch never warns.
    UNCERTAIN = "uncertain"
    # A submodule, bound on its package once it finishes.
    SUBMODULE = "submodule"


@dataclass(frozen=True)
class Origin:
    """One binding of a name: at global step `step`, by run statement
    `stmt` of the namespace's module (None for a submodule's attachment);
    `module` is the module the name is bound to, when it is one."""
    kind: OriginKind
    step: int
    stmt: RunStatement | None
    module: str | None = None


class _Cause(Enum):
    """Why CPython's module lacks a name TPy's binds at a read."""
    DEFINED_LATER = "defined"
    ALL_LATE = "all-late"
    ALL_UPDATE = "all-update"
    DECLARED = "declared"


@dataclass(frozen=True)
class _CauseAt:
    """A cause with the statement it names (the `__all__` update, the
    declaration) and, for a declaration, the module that makes it."""
    kind: _Cause
    stmt: RunStatement | None = None
    declarer: str | None = None


@dataclass(frozen=True, eq=False)
class Frame:
    """A module suspended at an import statement: `module` runs the import
    `stmt` of `target`, which leads to the reader; `parent` is the frame
    `module` itself runs under."""
    module: str
    stmt: RunStatement
    target: str
    parent: Frame | None

    @property
    def line(self) -> int:
        return self.stmt.line


@dataclass(eq=False)
class _Namespace:
    """A program module as the interpreter runs it."""
    name: str
    statements: list[RunStatement]
    # The global step each run statement starts at, in order.
    steps: list[int] = field(default_factory=list)
    names: dict[str, list[Origin]] = field(default_factory=dict)
    # A name only a bare declaration (`x: T`) names: (declarer, statement).
    declared: dict[str, tuple[str, RunStatement]] = field(default_factory=dict)
    # `__all__` as it stands, and each update with the step it ran at.
    all_value: list[str] | None = None
    # `__all__` stands at a value that is not a literal.
    all_unknown: bool = False
    all_updates: list[tuple[int, AllUpdate, RunStatement]] = field(
        default_factory=list)
    start: int = 0
    finish: int | None = None

    def __post_init__(self) -> None:
        self._starts = [(s.loc.line, s.loc.column) for s in self.statements]

    def bound(self, name: str, step: int | None = None) -> Origin | None:
        """The binding of `name` in force before `step` (now, when None)."""
        found = None
        for origin in self.names.get(name, ()):
            if step is not None and origin.step >= step:
                break
            found = origin
        return found

    def statement_at(self, loc: SourceLocation) -> int | None:
        """The index of the run statement whose span holds `loc`."""
        k = bisect.bisect_right(self._starts, (loc.line, loc.column)) - 1
        if k >= 0 and self.statements[k].contains(loc):
            return k
        return None


@dataclass(frozen=True)
class _FailedRead:
    """An import-time read CPython fails, warned once every read of the
    program is known (the remedy depends on the peer's reads): `reader`
    reads `names` of `module` (the submodule's name, for SUBMODULE) at
    global step `step`."""
    reader: str
    loc: SourceLocation | None
    module: str
    names: tuple[str, ...]
    frame: Frame
    evaluated: EvalKind
    read: ReadKind
    step: int


@dataclass(frozen=True)
class _StarCopy:
    """A `from source import *` in `reader` that ran at `step` (statement
    `stmt`), copying `copied` with `__all__` standing at `all_value`."""
    reader: str
    stmt: RunStatement
    source: str
    step: int
    copied: frozenset[str]
    all_value: tuple[str, ...] | None
    # The names a bound `__all__` lists that the copy found missing.
    missing: tuple[str, ...]


class ImportOrder:
    """CPython's run of a program's imports: each program module's
    namespace over time, the modules suspended below each one while it
    runs (`stacks`), and the reads of them CPython fails."""

    def __init__(self, modules: Mapping[str, CompiledModule], entry: str,
                 walkable: Callable[[str], bool], base_dir: Path | None,
                 cycles: Mapping[str, frozenset[str]]):
        self._modules = modules
        # Each import-cycle member's cycle, as the cycle gate sees it.
        self._cycles = cycles
        self._walkable = walkable
        self._base_dir = base_dir
        self._entry = entry
        # The frame each module started under (None for the entry).
        self.stacks: dict[str, Frame | None] = {}
        self._spaces: dict[str, _Namespace] = {}
        self._opaque: set[str] = set()
        self._step = 0
        self._ran = False
        # The modules each module's import statements name.
        self._imports_of: dict[str, set[str]] = {}
        # (reader, module): `reader` reads `module` at import.
        self._reads: set[tuple[str, str]] = set()
        self._pending: list[tuple[str, str, list[str], EvalKind,
                                  SourceLocation | None]] = []
        self._from_misses: list[tuple[str, RunStatement, str, str, int]] = []
        self._stars: list[_StarCopy] = []
        self._failed: list[_FailedRead] = []
        self._views: dict[str, ReaderView] = {}
        self._reorders: dict[tuple[str, str], str | None] = {}

    # -- the run ---------------------------------------------------------------

    def _tick(self) -> int:
        self._step += 1
        return self._step

    def _run(self, name: str, frame: Frame | None) -> None:
        space = _Namespace(name, self._modules[name].ast.run_statements)
        self._spaces[name] = space
        self.stacks[name] = frame
        space.start = self._tick()
        for stmt in space.statements:
            space.steps.append(self._tick())
            self._execute(space, stmt)
        space.finish = self._tick()
        parent, _, child = name.rpartition(".")
        parent_space = self._spaces.get(parent)
        if parent_space is not None:
            self._bind(parent_space, child, OriginKind.SUBMODULE, None,
                       module=name)

    def _bind(self, space: _Namespace, name: str, kind: OriginKind,
              stmt: RunStatement | None, module: str | None = None) -> None:
        space.names.setdefault(name, []).append(
            Origin(kind, self._tick(), stmt, module))

    def _started(self, name: str) -> bool:
        return name in self._spaces or name in self._opaque

    def _load(self, space: _Namespace, stmt: RunStatement,
              target: str) -> None:
        """Import `target` from `space`'s statement `stmt`: its parent
        packages first, a module already started returning at once."""
        parts = target.split(".")
        for k in range(1, len(parts) + 1):
            name = ".".join(parts[:k])
            if name not in self._modules or name == space.name:
                continue
            self._imports_of.setdefault(space.name, set()).add(name)
            if self._started(name):
                continue
            if not self._walkable(name):
                self._opaque.add(name)
                continue
            self._run(name, Frame(space.name, stmt, name,
                                  self.stacks[space.name]))

    def _execute(self, space: _Namespace, stmt: RunStatement) -> None:
        node = stmt.node
        if isinstance(node, ImportRecord):
            self._execute_import(space, stmt, node)
        elif isinstance(node, AllUpdate):
            if node.names is None:
                space.all_value, space.all_unknown = None, True
            elif node.augment and space.all_value is not None:
                space.all_value.extend(
                    n for n in node.names if n not in space.all_value)
            elif not node.augment:
                space.all_value = list(node.names)
                space.all_unknown = False
            space.all_updates.append((space.steps[-1], node, stmt))
        elif isinstance(node, list):
            native = self._modules[space.name].ast.directives.native_module
            for s in node:
                if isinstance(s, TpyVarDecl):
                    # A bare declaration binds nothing -- except in a
                    # native module, whose C++ side provides the value.
                    if s.init is not None or native:
                        self._bind(space, s.name, OriginKind.BOUND, stmt)
                    elif s.name not in space.names:
                        space.declared.setdefault(s.name, (space.name, stmt))
                    continue
                for name in local_names_of([s]):
                    self._bind(space, name, OriginKind.UNCERTAIN, stmt)
        elif isinstance(node, str):
            self._bind(space, node, OriginKind.BOUND, stmt)
        elif node is not None:
            self._bind(space, node.name, OriginKind.BOUND, stmt)

    def _execute_import(self, space: _Namespace, stmt: RunStatement,
                        record: ImportRecord) -> None:
        if record.from_module is None:
            for dotted, alias in record.modules:
                self._load(space, stmt, dotted)
                local = alias or dotted.split(".")[0]
                self._bind(space, local, OriginKind.BOUND, stmt,
                           module=dotted if alias else local)
            return
        source = record.from_module
        if RelativeImportKey.is_placeholder(source) or source not in self._modules:
            for _original, local in record.names:
                self._bind(space, local, OriginKind.BOUND, stmt)
            return
        self._reads.add((space.name, source))
        self._load(space, stmt, source)
        src = self._spaces.get(source)
        # A module whose `__all__` is not a literal at this step copies
        # what no one can tell: TPy's star binds it.
        if src is None or (record.star and src.all_unknown):
            locals_ = ([local for _o, local in
                        self._star_bucket(space.name, source)]
                       if record.star else
                       [local for _o, local in record.names])
            for local in locals_:
                self._bind(space, local, OriginKind.BOUND, stmt)
            return
        if record.star:
            self._execute_star(space, stmt, source, src)
            return
        # CPython loads the whole from-list (a name that is no attribute
        # of a package is imported as its submodule), then binds it.
        if self._is_package(source):
            for original, _local in record.names:
                if src.bound(original) is None:
                    self._load(space, stmt, f"{source}.{original}")
        for original, local in record.names:
            origin = src.bound(original)
            sub = f"{source}.{original}"
            if origin is not None:
                self._bind(space, local, OriginKind.BOUND, stmt,
                           module=origin.module)
            elif self._started(sub):
                # Not attached yet: CPython takes it from `sys.modules`.
                self._bind(space, local, OriginKind.BOUND, stmt, module=sub)
            else:
                self._from_misses.append(
                    (space.name, stmt, source, original, space.steps[-1]))
                declared = src.declared.get(original)
                if declared is not None:
                    space.declared.setdefault(local, declared)
                else:
                    self._bind(space, local, OriginKind.BOUND, stmt)

    def _execute_star(self, space: _Namespace, stmt: RunStatement,
                      source: str, src: _Namespace) -> None:
        step = space.steps[-1]
        listed = list(src.all_value) if src.all_value is not None else None
        if listed is not None and self._is_package(source):
            for name in listed:
                if src.bound(name) is None:
                    self._load(space, stmt, f"{source}.{name}")
        names = [n for n in (listed if listed is not None else src.names)
                 if star_exportable(n, listed)]
        missing = tuple(n for n in names if src.bound(n) is None)
        copied = [n for n in names if src.bound(n) is not None]
        for name in copied:
            self._bind(space, name, OriginKind.BOUND, stmt,
                       module=src.bound(name).module)
        for name, declared in src.declared.items():
            if star_exportable(name, listed) and space.bound(name) is None:
                space.declared.setdefault(name, declared)
        self._stars.append(_StarCopy(
            space.name, stmt, source, step, frozenset(copied),
            tuple(listed) if listed is not None else None, missing))

    def _is_package(self, name: str) -> bool:
        return self._modules[name].is_package_init

    def _star_bucket(self, reader: str,
                     source: str) -> set[tuple[str, str]]:
        """What TPy's `from source import *` in `reader` binds."""
        bucket = self._modules[reader].ast.imports.get(source)
        return bucket if isinstance(bucket, set) else set()

    # -- what a module is missing -----------------------------------------------

    def _frames(self, reader: str) -> list[Frame]:
        """The frames below `reader`, from the entry module's."""
        out: list[Frame] = []
        frame = self.stacks.get(reader)
        while frame is not None:
            out.append(frame)
            frame = frame.parent
        out.reverse()
        return out

    def suspended_frame(self, reader: str, module: str) -> Frame | None:
        """The frame `module` is suspended at while `reader` runs, if it is
        (started and not finished)."""
        frame = self.stacks.get(reader)
        while frame is not None:
            if frame.module == module:
                return frame
            frame = frame.parent
        return None

    def _declaration(self, module: str,
                     name: str) -> tuple[str, RunStatement] | None:
        """The bare declaration of `name`, when that is all `module` ever
        has of it (its own, or one a star or from import brought)."""
        space = self._spaces.get(module)
        if space is None or space.bound(name) is not None:
            return None
        return space.declared.get(name)

    def _provenance(self, module: str,
                    name: str) -> tuple[str, RunStatement] | None:
        """The program run statement TPy's binding of `name` in `module`
        traces to: `module`'s own binding or declaration, else the one the
        binding's defining module makes; None when no run statement makes
        it (a stub, builtin or macro name)."""
        found = self._own_provenance(module, name)
        if found is not None:
            return found
        cell = self._modules[module].module_attributes.get(name)
        binding = cell.binding if cell is not None else None
        if binding is None or binding.defining_module is None:
            return None
        return self._own_provenance(binding.defining_module,
                                    binding.canonical_name)

    def _own_provenance(self, module: str,
                        name: str) -> tuple[str, RunStatement] | None:
        space = self._spaces.get(module)
        if space is None:
            return None
        stmt = next((o.stmt for o in space.names.get(name, ())
                     if o.stmt is not None), None)
        if stmt is not None:
            return (module, stmt)
        return space.declared.get(name)

    # -- the reads ---------------------------------------------------------------

    def read(self, reader: str, start: str, names: list[str],
             kind: EvalKind, loc: SourceLocation | None) -> None:
        """Note the import-time read of `start.names...` in `reader` at
        `loc`, as `kind` evaluates it (`pkg.a.Item`: `a` of `pkg`, then
        `Item` of `pkg.a`); a read CPython fails is warned at `drain`."""
        if kind is not EvalKind.LAZY:
            self._pending.append((reader, start, names, kind, loc))

    def drain(self) -> list[tuple[str, SourceLocation | None, str]]:
        """The warnings for the reads noted so far that CPython fails, each
        as (reader module, location, message). The first call runs the
        program: every module's surface is known by then."""
        if not self._ran:
            self._ran = True
            self._run(self._entry, None)
            self._judge_from_misses()
            self._judge_stars()
        pending, self._pending = self._pending, []
        for reader, start, names, kind, loc in pending:
            self._judge_path(reader, start, names, kind, loc)
        failed, self._failed = self._failed, []
        return [(f.reader, f.loc, self._message(f)) for f in failed]

    def _known_absent(self, module: str, name: str) -> bool:
        """Whether a program statement accounts for `name` in `module`:
        missing it at some step is then CPython's failure, not a name TPy
        has from elsewhere (a macro adds it)."""
        return self._provenance(module, name) is not None

    def _judge_path(self, reader: str, start: str, names: list[str],
                    kind: EvalKind, loc: SourceLocation | None) -> None:
        if start in self._modules:
            self._reads.add((reader, start))
        space = self._spaces.get(reader)
        if space is None or loc is None or loc.line <= 0:
            return
        k = space.statement_at(loc)
        if k is None:
            return
        step = space.steps[k]
        module = start
        for name in names:
            if module in self._modules:
                self._reads.add((reader, module))
            peer = self._spaces.get(module)
            if peer is None or module == reader:
                return
            origin = peer.bound(name, step)
            if origin is not None:
                if origin.module is None or origin.module not in self._modules:
                    return
                module = origin.module
                continue
            sub = f"{module}.{name}"
            if sub in self._modules:
                frame = self.suspended_frame(reader, sub)
                if frame is not None and sub != reader:
                    self._failed.append(_FailedRead(
                        reader, loc, module, (name,), frame, kind,
                        ReadKind.SUBMODULE, step))
                return
            frame = self.suspended_frame(reader, module)
            if frame is not None and self._known_absent(module, name):
                self._failed.append(_FailedRead(
                    reader, loc, module, (name,), frame, kind,
                    ReadKind.MEMBER, step))
            return

    def _judge_from_misses(self) -> None:
        for reader, stmt, source, name, step in self._from_misses:
            frame = self.suspended_frame(reader, source)
            if frame is None or not self._known_absent(source, name):
                # A finished module missing a name only declared there is
                # BUGS.md#finished-module-declared-only-import-accepted.
                continue
            self._failed.append(_FailedRead(
                reader, stmt.node.loc, source, (name,), frame,
                EvalKind.EAGER, ReadKind.FROM_IMPORT, step))

    def _judge_stars(self) -> None:
        for star in self._stars:
            record = star.stmt.node
            sub_frames = [
                (name, self.suspended_frame(star.reader,
                                            f"{star.source}.{name}"))
                for name in star.missing
                if f"{star.source}.{name}" in self._modules]
            sub_missing = [(n, f) for n, f in sub_frames if f is not None]
            if sub_missing:
                name, frame = sub_missing[0]
                self._failed.append(_FailedRead(
                    star.reader, record.loc, star.source, (name,), frame,
                    EvalKind.EAGER, ReadKind.SUBMODULE, star.step))
                continue
            frame = self.suspended_frame(star.reader, star.source)
            if frame is None:
                continue
            if star.missing:
                listed = [n for n in star.missing
                          if f"{star.source}.{n}" not in self._modules
                          and self._known_absent(star.source, n)]
                if listed:
                    listed.sort(key=lambda n: (
                        self._late_binding(star.source, n, frame)[1], n))
                    self._failed.append(_FailedRead(
                        star.reader, record.loc, star.source, tuple(listed),
                        frame, EvalKind.EAGER, ReadKind.STAR_ALL, star.step))
                continue
            listed = self._star_difference(star)
            if listed:
                listed.sort(key=lambda n: (
                    self._late_binding(star.source, n, frame)[1], n))
                self._failed.append(_FailedRead(
                    star.reader, record.loc, star.source, tuple(listed),
                    frame, EvalKind.EAGER, ReadKind.STAR, star.step))

    def _star_difference(self, star: _StarCopy) -> list[str]:
        """The names TPy's star import binds and CPython's copy lacks, where
        TPy's final binding in the reader is the one this star brings and
        no other statement of the reader binds that same object."""
        reader = self._spaces[star.reader]
        out: list[str] = []
        for _original, name in self._star_bucket(star.reader, star.source):
            if name in star.copied:
                continue
            brought = self._identity(star.source, name)
            if self._identity(star.reader, name) != brought:
                continue
            if any(o.stmt is not None and o.stmt is not star.stmt
                   and self._binds_object(star.reader, o.stmt, name) == brought
                   for o in reader.names.get(name, ())):
                continue
            last = self._spaces[star.source].bound(name)
            if last is not None and last.module is not None:
                continue
            if self._provenance(star.source, name) is None:
                continue
            out.append(name)
        return out

    def _identity(self, module: str, name: str) -> tuple[str, str]:
        """What TPy binds `name` to in `module`: its defining module and
        its name there."""
        # A local definition is its own identity; a name with no table entry
        # gets the same key, which no run statement's `_provenance` matches.
        return (lookup_imported(self._modules[module].module_attributes, name)
                or (module, name))

    def _binds_object(self, module: str, stmt: RunStatement,
                      name: str) -> tuple[str, str] | None:
        """What `module`'s statement `stmt` binds `name` to (None for a
        module, or a source TPy does not compile)."""
        record = stmt.node
        if not isinstance(record, ImportRecord):
            return (module, name)
        source = record.from_module
        if source is None or source not in self._modules:
            return None
        if record.star:
            return self._identity(source, name)
        original = next((o for o, local in record.names if local == name),
                        None)
        return (self._identity(source, original)
                if original is not None else None)

    def _causes(self, f: _FailedRead, name: str) -> list[_CauseAt]:
        """Why `f.module` lacks `name` at `f.step` under CPython."""
        source = self._spaces[f.module]
        out: list[_CauseAt] = []
        declared = self._declaration(f.module, name)
        first = next(iter(source.names.get(name, ())), None)
        if declared is not None:
            out.append(_CauseAt(_Cause.DECLARED, declared[1], declared[0]))
        elif first is None or first.step > f.step:
            out.append(_CauseAt(_Cause.DEFINED_LATER))
        if f.read is ReadKind.STAR and self._modules[f.module].ast.module_all is not None:
            bound_before = [u for step, u, _s in source.all_updates
                            if step < f.step]
            if not any(not u.augment for u in bound_before):
                if name.startswith("_"):
                    out.append(_CauseAt(_Cause.ALL_LATE))
            else:
                update = next((s for step, u, s in source.all_updates
                               if step > f.step and u.names is not None
                               and name in u.names), None)
                if update is not None:
                    out.append(_CauseAt(_Cause.ALL_UPDATE, update))
        return out or [_CauseAt(_Cause.DEFINED_LATER)]

    # -- the message ---------------------------------------------------------

    def _file(self, module: str) -> str:
        path = self._modules[module].path
        if self._base_dir is not None:
            try:
                return os.path.relpath(path, self._base_dir)
            except ValueError:
                pass
        return path.name

    def _chain(self, reader: str, upto: Frame) -> str:
        """How the entry module's imports reach `upto`'s suspension."""
        steps: list[str] = []
        for frame in self._frames(reader):
            if frame.module == self._entry:
                steps.append(f"{self._file(frame.module)} imports "
                             f"'{frame.target}'")
            else:
                steps.append(f"'{frame.module}' imports '{frame.target}' "
                             f"({self._file(frame.module)}:{frame.line})")
            if frame is upto:
                break
        if len(steps) == 1:
            return steps[0]
        return ", ".join(steps[:-1]) + ", and " + steps[-1]

    def _reorder_remedy(self, reader: str, peer: str) -> str | None:
        key = (reader, peer)
        if key not in self._reorders:
            self._reorders[key] = self._find_reorder(reader, peer)
        return self._reorders[key]

    def _find_reorder(self, reader: str, peer: str) -> str | None:
        """Importing `reader` before `peer` from the entry module, offered
        only where that provably removes the failure without causing
        another: a two-module cycle the entry module enters at `peer`,
        neither module a package of the other (importing `pkg.sub` runs
        `pkg` first), and `peer` -- which then runs while `reader` is
        suspended -- reading nothing of `reader` at import."""
        frames = self._frames(reader)
        if len(frames) != 2 or frames[0].target != peer:
            return None
        if (peer, reader) in self._reads:
            return None
        if reader.startswith(f"{peer}.") or peer.startswith(f"{reader}."):
            return None
        # A third module that imports either one while `peer` is suspended
        # joins the cycle: the reversed order may fail there.
        for module in self._spaces:
            if module == reader or self.suspended_frame(module, peer) is None:
                continue
            if self._imports_of.get(module, set()) & {reader, peer}:
                return None
        return (f"import '{reader}' before '{peer}' in "
                f"{self._file(self._entry)}")

    def _declared_remedy(self, f: _FailedRead, name: str,
                         declarer: str) -> str:
        if (declarer == f.module
                or self.suspended_frame(f.reader, declarer) is not None):
            # The cycle gate refuses a valued global in a cycle member; a
            # name `__all__` lists must be bound in the module itself.
            where = (f"in {self._file(f.module)} above that import"
                     if f.read is ReadKind.STAR_ALL else "from there")
            return (f"give '{name}' a value in a module outside the cycle "
                    f"and import it {where}")
        return f"give '{name}' a value in {self._file(declarer)}"

    def _late_binding(self, module: str, name: str,
                      frame: Frame) -> tuple[RunStatement | None, int]:
        """The import statement of `module` that binds `name` (None when a
        definition -- a def, class or assignment -- binds it), and the line
        that binds it."""
        found = self._provenance(module, name)
        if found is not None and found[0] == module:
            stmt = found[1]
            return (stmt if isinstance(stmt.node, ImportRecord) else None,
                    stmt.line)
        return None, frame.line

    def _completes_first(self, f: _FailedRead, stmt: RunStatement) -> bool:
        """Whether `f.module`'s import `stmt`, moved above the import it is
        suspended at, finishes before the reader starts: neither that
        import itself nor one loading a module of their import cycle."""
        if stmt is f.frame.stmt:
            return False
        record = stmt.node
        assert isinstance(record, ImportRecord)
        source = record.from_module
        # A from-list name may load its submodule.
        targets = ([source] + [f"{source}.{original}"
                               for original, _local in record.names]
                   if source is not None
                   else [dotted for dotted, _alias in record.modules])
        cycle = self._cycles.get(f.module, frozenset())
        return not any(target in cycle for target in targets)

    def _explain(self, f: _FailedRead) -> tuple[str, str, bool]:
        """Why CPython's `f.module` lacks `f.names` at the read, as the
        clause that follows the import chain, the remedy sentence (every
        cause is a step of it), and whether a cause is a bare
        declaration (the remedy is then a value, never a reorder)."""
        module, file = f.module, self._file(f.module)
        # A star import's message names a definition without its line: a
        # name the peer took through its own star import of a suspended
        # module has no binding statement there, so the only line to
        # name would be the peer's suspending import.
        star = f.read is ReadKind.STAR
        defined: list[tuple[str, int]] = []
        imported: dict[int, tuple[RunStatement, list[str]]] = {}
        all_late: list[str] = []
        updates: dict[int, tuple[RunStatement, list[str]]] = {}
        declared: list[tuple[str, _CauseAt]] = []
        for name in f.names:
            for cause in self._causes(f, name):
                if cause.kind is _Cause.DEFINED_LATER:
                    stmt, line = self._late_binding(module, name, f.frame)
                    if stmt is None:
                        defined.append((name, line))
                    else:
                        imported.setdefault(id(stmt), (stmt, []))[1].append(
                            name)
                elif cause.kind is _Cause.ALL_LATE:
                    all_late.append(name)
                elif cause.kind is _Cause.ALL_UPDATE and cause.stmt is not None:
                    updates.setdefault(id(cause.stmt),
                                       (cause.stmt, []))[1].append(name)
                elif cause.kind is _Cause.DECLARED and cause.stmt is not None:
                    declared.append((name, cause))
        why: list[str] = []
        steps: list[str] = []
        if defined:
            names = tuple(n for n, _line in defined)
            what = (", ".join(f"'{n}' ({file}:{line})" for n, line in defined)
                    if not star else _quoted(names))
            why.append(f"before it defines {what}")
            them = _quoted(names) if len(names) == 1 else "them"
            steps.append(f"define {them} above that import in {file}")
        if imported:
            what = ", ".join(f"'{n}' ({file}:{stmt.line})"
                             for stmt, names in imported.values()
                             for n in names)
            why.append(f"before it binds {what}")
            for stmt, names in imported.values():
                if not self._completes_first(f, stmt):
                    steps.append(_OUTSIDE)
                elif stmt.line != f.frame.line:
                    steps.append(
                        f"move the import at {file}:{stmt.line} above that "
                        f"import")
                else:
                    # A statement on the cycle import's own line cannot move
                    # above it: the name is bound by a statement of its own.
                    steps.append(f"bind {_quoted(tuple(names))} above that "
                                 f"import in {file}")
        if all_late:
            why.append(f"before it binds `__all__`, which lists "
                       f"{_quoted(tuple(all_late))}")
            steps.append(f"define `__all__` above that import in {file}")
        for stmt, names in updates.values():
            why.append(f"before its `__all__` update ({file}:"
                       f"{stmt.line}) adds {_quoted(tuple(names))}")
            steps.append(f"move the `__all__` update ({file}:"
                         f"{stmt.line}) above that import")
        for name, cause in declared:
            declarer = cause.declarer or module
            why.append(f"'{name}' is only declared ({self._file(declarer)}:"
                       f"{cause.stmt.line}), which binds nothing")
            steps.append(self._declared_remedy(f, name, declarer))
        steps = list(dict.fromkeys(steps))
        remedy = (steps[0] if len(steps) == 1 else
                  ", ".join(steps[:-1]) + " and " + steps[-1])
        if not declared:
            reorder = self._reorder_remedy(f.reader, module)
            if reorder is not None:
                remedy += f", or {reorder}"
            elif not all_late and not updates and _OUTSIDE not in steps:
                remedy += f", or {_OUTSIDE}"
        lead = " " if why[0].startswith("before") else ", and "
        return (lead + ", and ".join(why), f"{_sentence(remedy)}.",
                bool(declared))

    def _star_message(self, f: _FailedRead, chain: str) -> str:
        """The star-import warning: TPy's star binds names CPython's lacks."""
        why, remedy, _declared = self._explain(f)
        one = len(f.names) == 1
        binds = "does not bind it" if one else "binds none of them"
        # A name the reader bound above the star keeps that binding.
        reader = self._spaces[f.reader]
        held = [n for n in f.names if reader.bound(n, f.step) is not None]
        if not held:
            result = "raises NameError"
        elif len(held) == len(f.names):
            result = (f"reads the binding {'it' if one else 'they'} had "
                      f"before that import")
        else:
            result = ("raises NameError or reads the binding it had before "
                      "that import")
        return (f"CPython cannot import this as TPy does: '{f.module}' is "
                f"still being imported -- {chain}{why}; "
                f"CPython's star import {binds} here, so a use of "
                f"{'it' if one else 'them'} {result}. {remedy}")

    def _star_all_message(self, f: _FailedRead, chain: str) -> str:
        """`from P import *` reading names P's `__all__` lists and P has
        not bound."""
        module = f.module
        why, remedy, _declared = self._explain(f)
        return (f"CPython cannot import this: `from {module} import *` "
                f"reads {_quoted(f.names)}, listed in `__all__` of "
                f"'{module}', while '{module}' is still being imported -- "
                f"{chain}{why}; CPython raises "
                f"AttributeError (partially initialized module '{module}' "
                f"has no attribute '{f.names[0]}'). {remedy}")

    def _message(self, f: _FailedRead) -> str:
        annotation = f.evaluated is EvalKind.ANNOTATION
        who = "CPython 3.12 and 3.13" if annotation else "CPython"
        raises = (f"{who} evaluate this annotation at import and raise"
                  if annotation else "CPython raises")
        quote = ("Quote the whole annotation or add `from __future__ "
                 "import annotations`.")
        chain = self._chain(f.reader, f.frame)
        module = f.module
        if f.read is ReadKind.SUBMODULE:
            sub = f"{module}.{f.names[0]}"
            reorder = self._reorder_remedy(f.reader, sub)
            remedy = quote if annotation else f"{_sentence(reorder or _OUTSIDE)}."
            error = (f"partially initialized module '{module}' has no "
                     f"attribute '{f.names[0]}'"
                     if self.suspended_frame(f.reader, module) is not None
                     else f"cannot access submodule '{f.names[0]}' of module "
                          f"'{module}'")
            return (f"{who} cannot import this: '{sub}' is read while '{sub}' "
                    f"is still being imported -- {chain}; a submodule is "
                    f"bound on its package only once it finishes, so {raises} "
                    f"AttributeError ({error}). {remedy}")
        if f.read is ReadKind.STAR:
            return self._star_message(f, chain)
        if f.read is ReadKind.STAR_ALL:
            return self._star_all_message(f, chain)
        name = f.names[0]
        if annotation:
            what = f"the annotation reads '{module}.{name}'"
        elif f.read is ReadKind.MEMBER:
            what = f"'{module}.{name}' is read"
        else:
            what = f"'{name}' is imported from '{module}'"
        error = (f"AttributeError (partially initialized module '{module}' "
                 f"has no attribute '{name}')" if f.read is ReadKind.MEMBER
                 else f"ImportError (cannot import name '{name}' from "
                      f"partially initialized module '{module}')")
        why, remedy, declared = self._explain(f)
        if annotation and not declared:
            remedy = quote
        return (f"{who} cannot import this: {what} while '{module}' is still "
                f"being imported -- {chain}{why}; {raises} {error}. "
                f"{remedy}")

    def reader(self, module: str) -> ReaderView | None:
        """The view a read site in `module` asks, or None for a module
        CPython does not run (a library or plugin module)."""
        if not self._walkable(module):
            return None
        view = self._views.get(module)
        if view is None:
            view = self._views[module] = ReaderView(self, module)
        return view


class ReaderView:
    """`ImportOrder` as one module's read sites ask it."""

    def __init__(self, order: ImportOrder, module: str):
        self.order = order
        self.module = module

    def read(self, start: str, names: list[str], kind: EvalKind,
             loc: SourceLocation | None) -> None:
        """Note the import-time read of `start.names...` at `loc`."""
        self.order.read(self.module, start, names, kind, loc)
