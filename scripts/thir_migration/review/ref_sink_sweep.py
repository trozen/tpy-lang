#!/usr/bin/env python3
"""The SINK x source x reference-KIND x position verdict ratchet.

`arg_family_sweep.py` beside this file measures one sink -- the ARGUMENT --
across the callee families. Every other place a reference-typed expression can
land is unmeasured: a borrow local, a reseated local, a returned borrow, a
field write, a tuple element, a receiver the mutation is applied to directly, a
`yield`, a `match` capture. Each of those reaches the lowering through its own
gate, and the gates disagree about the same source expression. Nothing in the
tree says WHERE they disagree, because the corpus pins the cells someone
thought to write.

This script generates that matrix, surveys it, and writes the verdict table
next to itself, so "the record twin of this cell admits and the container twin
does not" is a diff in `ref_sink_sweep.expected.json` rather than a review
discovery. The kind axis is the reference axis the arg sweep already spans, by
name: a record, a generic record with a record type-arg, a user-`Deref`
wrapper over a record and over a container payload, the builtin containers,
`bytearray`, `Array`, and the recursive-union wrapper (a tripwire -- on the
axis by value form, but a struct whose payload is a member, so a gate that
admits it as a record or a container has bound the wrong thing).

WHAT A CELL RECORDS, the same three strings the arg sweep records: the VERDICT
(`ok`, `reject:<tag>`, `sema:<message>`, plus two the arg sweep's matrix never
reaches -- `crash:<message>` for a shape the front end asserts on, and
`stopped:` for a frame the pass never got to), the first WARNING inside the
cell's own body, and the normalised THIR of the subject statement. The render
is the
half a verdict column cannot show: two sinks that both admit while one COPIES
the source and the other binds a pointer to it differ silently, and that
difference is the copy-vs-alias divergence from CPython.

WHAT THE TABLE DOES NOT RECORD. Behaviour. Nothing is built or run for it, so
the render is as close to copy-vs-alias as the table gets. `--exec` is the
behavioural half, outside the table: every cell that compiled (`ok`) of a
kind with a write spelling, plus an ARGUMENT sink of its own (`take_arg(src)`
into a callee that writes through its parameter), becomes a program that
WRITES through what the boundary bound -- the caller writes through a
`return`'s result bound to a local (`r = f(a)`) and a `yield`'s loop
variable -- and reads the original before and
after. Built and run under TPy (`tpyc -b`, one build at a time) and under
CPython (`PYTHONPATH=<repo>/lib/cpy`), each cell records `alias` (the write
reached the original), `copy` (it did not), `fresh` (no original to read:
a temporary, or a local at its last use), or `differs: <tpy> vs <cpython>`;
a cell the TPy build refuses records `reject:` / `build_fail:`. One program
per (kind, position); a cell that stops its run or its build is taken out
and the rest go again. Results are cached per cell by the hash of the cell's
one-cell program, the tree's commit and diff, and the CPython version, under
`<repo>/build/ref_sink_exec/cache` (`--exec-dir`), so a rerun resumes;
`--exec -k REGEX` takes the cells whose name matches.

NOT AN ARM PIN. The house rule is that a THIR lowering arm is pinned by a case
under `tests/cases/`; this is a measurement over a generated corpus, like
`arg_family_sweep.py`, `property_position_sweep.py` and `container_gates.py`
in the same directory.

ONE PROGRAM PER (kind, position) for the four non-frame positions: every sink
x source cell of that pair is its own body in it, and
`collect_thir(tolerate_reject=True)` attempts every function, method and
constructor body, so hundreds of cells share one compilation.

THE TWO FRAME POSITIONS ARE DIFFERENT. A generator and an `async def` body
lower WHILE the C++ around them is written, so one rejecting frame ends the
pass and takes every later cell in the program with it -- the same constraint
`property_position_sweep.py` records. They are surveyed with a retry loop that
settles the rejecting body and recompiles what is left, and the gate, which
already knows from the committed table which cells reject, verifies those in
minimal one-cell programs instead. That is also why the frame positions carry
a REDUCED source set (`FRAME_SOURCES`): at one compilation per rejecting frame
the full source list would cost more than the whole rest of the table.

  python scripts/thir_migration/review/ref_sink_sweep.py            # check
  python scripts/thir_migration/review/ref_sink_sweep.py --update   # rewrite
  python scripts/thir_migration/review/ref_sink_sweep.py --only rec # a slice
  python scripts/thir_migration/review/ref_sink_sweep.py --raw T.json
  python scripts/thir_migration/review/ref_sink_sweep.py --exec \\
      [-k REGEX] [--exec-dir DIR] [--python python3] [--jobs 2]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arg_family_sweep import (  # noqa: E402
    BASE_DEFS, BASE_IMPORTS, BEACON, FIXTURE, KINDS, Q, SOURCES, Built,
    _kind_has, _owner, _render, _subst, _tag)
import arg_family_sweep  # noqa: E402
from program_compile import (  # noqa: E402
    compile_program, failure, first_error, program_facts, warnings_of)

REPO = arg_family_sweep.REPO

EXPECTED = Path(__file__).with_suffix(".expected.json")


# ---------------------------------------------------------------------------
# The axes.
# ---------------------------------------------------------------------------

# The reference axis, by name out of the arg sweep's kind table. `opt_rec` is
# left out: it would add a twelfth program set for a shape whose sink
# behaviour is the `rec` row behind a narrowing, and the budget buys more from
# the container twins.
KIND_NAMES = ("rec", "gen_rec", "deref_rec", "deref_list", "list_i", "list_r",
              "dict_si", "set_i", "bytearray", "array_i", "ru_wrap")

# How a cell MUTATES through whatever the sink bound, per kind. `@X@` is the
# thing to mutate. The empty entry means the kind has no in-place write at all,
# so every sink that needs one is skipped for it.
MUTATIONS: dict[str, str] = {
    "rec": "@X@.x += 1",
    "gen_rec": "@X@.get().x += 1",
    "deref_rec": "@X@.x = 9",
    "deref_list": "@X@.append(9)",
    "list_i": "@X@.append(9)",
    "list_r": "@X@.append(R(9))",
    "dict_si": "@X@[" + Q + "z" + Q + "] = 9",
    "set_i": "@X@.add(9)",
    "bytearray": "@X@.append(9)",
    "array_i": "@X@[0] = 9",
    "ru_wrap": "",
}

# The source shapes, by name out of the arg sweep's `SOURCES`. Two of its
# shapes are dropped for budget, as the least informative on this axis:
# `ternary_names` (a join of two seeds, whose sink behaviour is the seed's)
# and `copy_call` (an explicit `copy()`, whose whole point is that it copies).
# `nested_field` goes with them -- it is `obj_field` with one more receiver
# hop, and no gate here asks about the hop.
SOURCE_NAMES = ("local", "local_last", "param", "narrowed_opt_param",
                "obj_field", "subscript", "dict_subscript", "borrow_method",
                "prop_ref", "prop_own", "narrowed_opt", "own_call",
                "ctor_rvalue", "temp_accessor", "temp_field", "global_name",
                "loop_var", "closure_capture", "subclass_local")

# One compilation per rejecting frame is what the two frame positions cost, so
# they carry the source shapes that span the axis rather than all of them: a
# local at its last use and not, a parameter, a field, an element, a borrowing
# method, an owning call and a global.
FRAME_SOURCES = frozenset({"local", "local_last", "param", "obj_field",
                           "subscript", "borrow_method", "own_call",
                           "global_name"})

# Reading the ORIGINAL back after the sink has bound it: the expression to
# read, or "" where there is nothing to read back (the source was a temporary)
# or where the source shape already carries its own trailing read -- `local`
# and `subclass_local` are exactly the shapes whose trailing read is what
# distinguishes them from `local_last`, so a second one would be noise.
BACK: dict[str, str] = {
    "local": "",
    "local_last": "",
    "subclass_local": "",
    "param": "pv",
    "narrowed_opt_param": "pv",
    "obj_field": "b._v",
    "subscript": "xs[0]",
    "dict_subscript": "d[" + Q + "k" + Q + "]",
    "borrow_method": "b.v_m()",
    "prop_ref": "b.v",
    "prop_own": "",
    "narrowed_opt": "o0",
    "own_call": "",
    "ctor_rvalue": "",
    "temp_accessor": "",
    "temp_field": "",
    "global_name": "G",
    "loop_var": "s0",
    "closure_capture": "s0",
}

# The source shapes a BORROW-OUT sink (`return`, `yield`) can be asked about:
# a parameter, a field, an element, a call. A local's borrow escaping its own
# frame is a different question with a settled answer, and a shape whose
# expression sits inside a `wrap` cannot carry a return at all.
BORROWABLE = frozenset({"param", "obj_field", "subscript", "dict_subscript",
                        "borrow_method", "prop_ref", "prop_own", "own_call",
                        "ctor_rvalue", "temp_accessor", "temp_field",
                        "global_name"})

SOURCE_BY_NAME = {s.name: s for s in SOURCES}


class Sink(NamedTuple):
    """One place a reference-typed source expression can LAND.

    `body` is the cell's subject, with `@SRC@` for the source expression,
    `@MUT:<target>@` for the kind's mutation applied to `<target>`
    (`@MUT:SRC@` applies it to the source expression itself), `@READ:<expr>@`
    for a print of the kind's observation of `<expr>`, and `@BACK@` for the
    read of the ORIGINAL. A line whose placeholders all resolve to nothing is
    dropped.
    """
    name: str
    body: tuple[str, ...]
    pre: tuple[str, ...] = ()
    ret: bool = False           # the cell returns `@TY@`
    need_mut: bool = False      # the kind must have a mutation spelling
    need_field: bool = False    # the kind must be storable in a record field
    borrow_src: bool = False    # only the borrow-out source shapes
    positions: tuple[str, ...] = ()     # restricted to these, else all


SINKS: tuple[Sink, ...] = (
    # The single-assignment borrow local. FIRST in every row on purpose: a
    # sema error here is the SOURCE refusing to produce the kind at all, which
    # is type-invalidity for every other sink, so the whole row leaves in one
    # step instead of eight.
    Sink("decl_alias", ("x = @SRC@", "@MUT:x@", "@BACK@")),
    # The reseatable alias: bound once, then pointed at a second seed.
    Sink("decl_reassigned",
         ("x = @SRC@", "x = s1", "@MUT:x@", "@READ:s1@", "@BACK@"),
         pre=("s1 = @SEED@",)),
    Sink("return_borrow", ("return @SRC@",), ret=True, borrow_src=True),
    Sink("field_write", ("h.f = @SRC@", "@READ:h.f@"), pre=("h = H()",),
         need_field=True),
    Sink("tuple_elem", ("t = (@SRC@, 1)", "@MUT:t[0]@", "@BACK@"),
         need_mut=True),
    # The mutation applied to the source expression itself -- no binding in
    # between, so a gate that refuses the binding but admits this one is
    # saying the binding is what it objects to.
    Sink("recv_mut", ("@MUT:SRC@", "@BACK@"), need_mut=True),
    Sink("yield_borrow", ("yield @SRC@",), borrow_src=True,
         positions=("gen",)),
    # `case v:` rather than `case _ as v:`: the latter is an ICE, see the
    # README entry.
    Sink("match_capture",
         ("match @SRC@:", "    case v:", "        @MUT:v@", "@BACK@"),
         need_mut=True),
)

SINK_BY_NAME = {s.name: s for s in SINKS}


class Position(NamedTuple):
    """Where the cell's BODY lives. The same sink lowers through a different
    seam in each: a plain function body, a method's, a constructor's
    member-init tail, a resumable frame, a nested def inside a function."""
    name: str
    frame: bool = False     # lowers at emission -- one reject ends the pass
    ctor: bool = False      # the body is an `__init__`
    ret_ok: bool = True     # the cell can return `@TY@`


POSITIONS: tuple[Position, ...] = (
    Position("fn"),
    Position("meth"),
    Position("ctor", ctor=True, ret_ok=False),
    Position("gen", frame=True, ret_ok=False),
    Position("async", frame=True),
    Position("nested"),
)

POSITION_BY_NAME = {p.name: p for p in POSITIONS}

# The holder the `field_write` sink writes into. Its own type, not the arg
# sweep's fixture record, so the write is the cell's only diagnostic.
HOLDER_DEFS = ("class H:", "    f: @TY@", "",
               "    def __init__(self) -> None:", "        self.f = @SEED@",
               "", "")


# ---------------------------------------------------------------------------
# The matrix.
# ---------------------------------------------------------------------------

def programs() -> list[tuple[str, str, str]]:
    """Every (program name, kind name, position name)."""
    return [("%s__%s" % (k, p.name), k, p.name)
            for k in KIND_NAMES for p in POSITIONS]


def _cell_ok(kind_name: str, pos: Position, src, sink: Sink) -> bool:
    kind = KINDS[kind_name]
    if sink.positions and pos.name not in sink.positions:
        return False
    if sink.ret and not pos.ret_ok:
        return False
    if sink.need_mut and not MUTATIONS[kind_name]:
        return False
    if sink.need_field and not kind.fieldable:
        return False
    if sink.borrow_src and src.name not in BORROWABLE:
        return False
    return True


def program_cells(kind_name: str, pos_name: str) -> list[tuple[str, str]]:
    """The (source, sink) cells a program carries, in emission order.

    `decl_alias` comes first within each source so a type-invalid row is found
    and dropped in one recompilation instead of eight.
    """
    kind, pos = KINDS[kind_name], POSITION_BY_NAME[pos_name]
    out = []
    for name in SOURCE_NAMES:
        if pos.frame and name not in FRAME_SOURCES:
            continue
        src = SOURCE_BY_NAME[name]
        if not _kind_has(kind, src.need):
            continue
        out += [(name, sink.name) for sink in SINKS
                if _cell_ok(kind_name, pos, src, sink)]
    return out


def cell_name(kind_name: str, pos_name: str, source: str, sink: str) -> str:
    return "%s__%s__%s__%s" % (kind_name, pos_name, source, sink)


def all_cells() -> dict[str, str]:
    """Every cell the matrix defines, as {cell name: program name}. Generation
    only, so the gate can compare the committed table against the generator
    without paying for a run."""
    out: dict[str, str] = {}
    for name, kind_name, pos_name in programs():
        for source, sink in program_cells(kind_name, pos_name):
            out[cell_name(kind_name, pos_name, source, sink)] = name
    return out


def program_of(cell: str) -> str:
    """The program a committed cell name belongs to."""
    kind_name, pos_name, _source, _sink = cell.split("__")
    return "%s__%s" % (kind_name, pos_name)


def split_cell(cell: str) -> tuple[str, str]:
    """The (source, sink) pair out of a committed cell name."""
    _kind, _pos, source, sink = cell.split("__")
    return source, sink


# ---------------------------------------------------------------------------
# Emission.
# ---------------------------------------------------------------------------

_MUT = re.compile(r"@MUT:([^@]*)@")
_READ = re.compile(r"@READ:([^@]*)@")

# A pending-literal type prints an INSTANCE id (`PendingList[...]#75`) that
# counts allocations across the whole compilation, so the same cell answers
# with a different number depending on which other cells shared its program.
# The id is not the verdict; the rest of the spelling is.
_INSTANCE = re.compile(r"#\d+")


def _stable(text: str) -> str:
    return _INSTANCE.sub("#N", text)


def _obs(kind, expr: str) -> str:
    return kind.obs.replace("@X@", expr)


def _fill(line: str, kind_name: str, src, back: str) -> str:
    """Resolve a template line's placeholders. `@MUT:` and `@READ:` go first
    so a target spelled `SRC` still sees the source expression."""
    kind = KINDS[kind_name]
    mut = MUTATIONS[kind_name]
    line = _MUT.sub(
        lambda m: mut.replace("@X@", src.expr if m.group(1) == "SRC"
                              else m.group(1)) if mut else "", line)
    line = _READ.sub(lambda m: "print(" + _obs(kind, m.group(1)) + ")", line)
    line = line.replace("@BACK@",
                        "print(" + _obs(kind, back) + ")" if back else "")
    line = line.replace("@SRC@", src.expr)
    return _subst(line, kind).rstrip()


def _cell_body(kind_name: str, src, sink: Sink) -> list[str]:
    """The cell's own lines, relative to the body's own indent."""
    back = BACK[src.name]
    out = [_fill(p, kind_name, src, back) for p in src.pre]
    out += [_fill(p, kind_name, src, back) for p in sink.pre]
    depth = 0
    for w in src.wrap:
        out.append("    " * depth + _fill(w, kind_name, src, back))
        depth += 1
    pad = "    " * depth
    out.append(pad + "print(" + str(BEACON) + ")")
    for line in sink.body:
        filled = _fill(line, kind_name, src, back)
        if filled.strip():
            out.append(pad + filled)
    out += [_fill(p, kind_name, src, back) for p in src.post]
    return out


def _cell_def(kind_name: str, pos: Position, src, sink: Sink) -> list[str]:
    """One cell's whole definition, at module indent."""
    kind = KINDS[kind_name]
    tag = "%s__%s" % (src.name, sink.name)
    body = _cell_body(kind_name, src, sink)
    params = ["pv: " + _subst(src.param_ty, kind)] if src.param else []
    sig = ", ".join(params)
    ret = kind.ty if sink.ret else "None"
    if pos.ctor:
        head = ["class Ctor_" + tag + ":", "    tag: int32", "",
                "    def __init__(self" + ("" if not sig else ", " + sig)
                + ") -> None:", "        self.tag = 0"]
        return head + ["        " + b for b in body] + ["", ""]
    if pos.name == "meth":
        head = ["    def cell_" + tag + "(self"
                + ("" if not sig else ", " + sig) + ") -> " + ret + ":"]
        return head + ["        " + b for b in body] + [""]
    if pos.name == "gen":
        elem = kind.ty if sink.name == "yield_borrow" else "int32"
        head = ["def cell_" + tag + "(" + sig + ") -> Iterator[" + elem + "]:"]
        # Every other sink needs the body to be a frame without the source
        # being what makes it one, so it opens with a yield of its own.
        lead = [] if sink.name == "yield_borrow" else ["    yield 0"]
        return head + lead + ["    " + b for b in body] + ["", ""]
    if pos.name == "async":
        head = ["async def cell_" + tag + "(" + sig + ") -> " + ret + ":"]
        return head + ["    " + b for b in body] + ["", ""]
    if pos.name == "nested":
        call = "nested_inner(" + ("pv" if src.param else "") + ")"
        # A kind whose observation ignores its subject (the recursive-union
        # wrapper) would drop the call itself if the read were wrapped around
        # it, and the nested def would never be reached.
        consume = ("print(" + _obs(kind, call) + ")"
                   if sink.ret and "@X@" in kind.obs else call)
        head = ["def cell_" + tag + "(" + sig + ") -> None:",
                "    def nested_inner(" + sig + ") -> " + ret + ":"]
        return (head + ["        " + b for b in body]
                + ["    " + consume, "", ""])
    head = ["def cell_" + tag + "(" + sig + ") -> " + ret + ":"]
    return head + ["    " + b for b in body] + ["", ""]


def build_program(kind_name: str, pos_name: str,
                  cells: list[tuple[str, str]]) -> Built:
    """One program: the shared fixture plus one body per cell."""
    kind, pos = KINDS[kind_name], POSITION_BY_NAME[pos_name]
    lines: list[str] = list(BASE_IMPORTS)
    if pos.name == "gen":
        lines.append("from typing import Iterator")
    lines += [i for i in kind.imports if i not in lines]
    lines += [""]
    lines += list(BASE_DEFS) + [""]
    lines += list(kind.defs)
    if kind.fieldable:
        lines += [_subst(x, kind) for x in FIXTURE]
    if any(sink == "field_write" for _src, sink in cells):
        lines += [_subst(x, kind) for x in HOLDER_DEFS]
    if pos.name == "meth":
        lines += ["class Host:", "    tag: int32", "",
                  "    def __init__(self) -> None:", "        self.tag = 0",
                  ""]

    cell_lines: dict[tuple[str, str], tuple[int, int]] = {}
    for source, sink in cells:
        start = len(lines) + 1
        lines += _cell_def(kind_name, pos, SOURCE_BY_NAME[source],
                           SINK_BY_NAME[sink])
        cell_lines[(source, sink)] = (start, len(lines))
    return Built("\n".join(lines) + "\n", cell_lines, {})


def generate(out_dir: Path, only: str = "") -> dict[str, Path]:
    """Write every program in full; return {program name: path}.

    The survey rewrites a program in place as it settles bodies, so what is
    left on disk afterwards is the last subset it compiled. The full text is
    what a reader wants, which is why it is written here first.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for name, kind_name, pos_name in programs():
        if only and only not in name and name not in only:
            continue
        path = out_dir / (name + ".py")
        path.write_text(build_program(kind_name, pos_name,
                                      program_cells(kind_name, pos_name)).text)
        out[name] = path
    return out


# ---------------------------------------------------------------------------
# The survey.
# ---------------------------------------------------------------------------

def _cell_bodies(facts: dict, pos: Position) -> dict:
    """{(source, sink): the `program_facts` body record of that cell}.

    A constructor is not among the callables -- its body emits through the
    member-init driver -- so the ctor position reads the constructor records,
    named by their record.
    """
    if pos.ctor:
        return {tuple(b["name"][5:].split("__")): b for b in facts["bodies"]
                if b["kind"] == "constructor" and b["name"].startswith("Ctor_")}
    return {tuple(b["name"][5:].split("__")): b for b in facts["bodies"]
            if b["kind"] == "callable" and b["name"].startswith("cell_")}


def _verdict(body: dict | None) -> tuple[str | None, str]:
    """The cell's verdict and render, or `(None, "")` for a body an earlier
    reject stopped the pass before reaching."""
    if body is None:
        return "crash:no such body", ""
    for dump in ("function", "resumable", "constructor"):
        if body[dump] is not None:
            return "ok", _render(body[dump])
    if body["reject"] is not None:
        return "reject:" + body["reject"], ""
    return None, ""


def survey(program: str, work_dir: Path,
           cells: 'list[tuple[str, str]] | None' = None,
           expect_solo: 'list[tuple[str, str]] | None' = None,
           stem: str = "") -> dict[str, list[str]]:
    """Every cell of `program`, as {cell name: [verdict, warning, render]}.

    One compilation answers every cell that lowers ahead of emission. Three
    things still cost a recompilation. A SEMA error stops the module at its
    first one and a rejecting FRAME ends the pass where it stands: either way
    the offending cell takes its verdict, leaves the program, and what remains
    is surveyed again. An ICE names no cell at all, so the program is halved
    until it does.

    `cells` narrows the matrix to a caller-supplied set; the gate passes the
    committed one, so a row the table already records as type-invalid is never
    generated. `expect_solo` names the cells the caller already knows cost a
    recompilation -- they are left OUT so the rest are answered by a single
    compilation, and `survey_solo` checks them one minimal program each.
    """
    _name, kind_name, pos_name = next(p for p in programs() if p[0] == program)
    pos = POSITION_BY_NAME[pos_name]
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / ((stem or program) + ".py")

    active = (list(cells) if cells is not None
              else program_cells(kind_name, pos_name))
    if expect_solo:
        held = set(expect_solo)
        active = [c for c in active if c not in held]
    out: dict[str, list[str]] = {}
    while active:
        built = build_program(kind_name, pos_name, active)
        path.write_text(built.text)
        facts = compile_program(path, program_facts, repo=REPO)
        error = first_error(facts)
        if error is not None:
            line, message = error
            cell = _owner(line, built.cell_lines)
            msg = "sema:" + _stable(message.strip().split("\n")[0])[:80]
            if cell is None:
                # Not inside any cell -- the shared fixture refused, so
                # nothing in this program can be surveyed. Reported rather
                # than silently attributed.
                for c in active:
                    out[cell_name(kind_name, pos_name, *c)] = [msg, "", ""]
                return out
            out[cell_name(kind_name, pos_name, *cell)] = [msg, "", ""]
            # A row `decl_alias` refuses is one where the SOURCE cannot
            # produce the kind at all, which is type-invalidity for every
            # sink, so the whole row leaves in one step.
            drop = ({c for c in active if c[0] == cell[0]}
                    if cell[1] == "decl_alias" else {cell})
            active = [c for c in active if c not in drop]
            continue
        if facts["raised"] is not None:
            # An ICE is a verdict here.
            msg = "crash:" + _stable(
                facts["raised"]["message"].strip().split("\n")[0])[:80]
            if len(active) == 1:
                out[cell_name(kind_name, pos_name, *active[0])] = [msg, "", ""]
                return out
            # An ICE carries no location, so halve the program until it does:
            # one crashing cell must cost its own verdict, not every cell it
            # was compiled alongside.
            half = len(active) // 2
            base = stem or program
            for i, part in enumerate((active[:half], active[half:])):
                out.update(survey(program, work_dir, cells=part,
                                  stem="%s_h%d" % (base, i)))
            return out

        if facts["codegen_error"] is not None:
            raise failure(facts, "the THIR survey of %s" % path)
        bodies = _cell_bodies(facts, pos)
        warned = warnings_of(facts)
        settled: list[tuple[str, str]] = []
        for cell in active:
            verdict, render = _verdict(bodies.get(cell))
            if verdict is None:
                continue
            lo, hi = built.cell_lines[cell]
            mine = [w for w in warned if lo <= w[0] <= hi]
            mine.sort(key=lambda w: w[0])
            out[cell_name(kind_name, pos_name, *cell)] = [
                # `_stable` before the tag's own truncation, so the shorter
                # spelling does not move the cut by a character.
                _stable(verdict),
                _tag(_stable(mine[0][1])) if mine else "",
                _stable(render)]
            settled.append(cell)
        if not settled:
            # Nothing reached emission. A body that lowers ahead of it always
            # gets a verdict, so this is a FRAME position whose program holds
            # a rejecting plain function -- the deferred reject is raised when
            # emission starts, before any frame is built.
            for c in active:
                out[cell_name(kind_name, pos_name, *c)] = [
                    "stopped:an earlier reject ended emission", "", ""]
            return out
        held = set(settled)
        active = [c for c in active if c not in held]
    return out


def survey_solo(program: str, work_dir: Path,
                cells: 'list[tuple[str, str]]') -> dict[str, list[str]]:
    """Check cells the caller expects to cost a recompilation, one MINIMAL
    program each -- the fixture and that one body.

    A cell whose reject or sema error is gone answers with whatever it does
    now, so the gate still fails naming it.
    """
    out: dict[str, list[str]] = {}
    for cell in cells:
        out.update(survey(program, work_dir / "one", cells=[cell],
                          stem="%s__%s__%s" % (program, cell[0], cell[1])))
    return out


def prune(table: dict[str, list[str]]) -> tuple[dict[str, list[str]], int]:
    """Drop every (kind, position, source) row `decl_alias` answers with a
    sema error: the source cannot produce the kind at all, so the other sinks
    refusing it is a type mismatch rather than a gate difference. The survey
    already drops such a row when it finds it; this only removes what a
    partial run left behind."""
    bad = {k.rsplit("__", 1)[0] for k, v in table.items()
           if k.endswith("__decl_alias") and v[0].startswith("sema:")}
    kept = {k: v for k, v in table.items() if k.rsplit("__", 1)[0] not in bad}
    return kept, len(bad)


def run(work_dir: Path, names: list[str]) -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for i, name in enumerate(names):
        t0 = time.monotonic()
        table.update(survey(name, work_dir))
        if os.environ.get("SWEEP_PROGRESS"):
            print("  %d/%d %s %.1fs" % (i + 1, len(names), name,
                                        time.monotonic() - t0),
                  file=sys.stderr)
    return table


# ---------------------------------------------------------------------------
# Exec mode: alias or copy, against CPython.
# ---------------------------------------------------------------------------

# The argument sink exists only here: the arg sweep owns the argument
# verdicts, this mode asks one more question of it -- whether the callee's
# write reaches the caller's object. `take_arg` writes through its parameter.
ARG_SINK = Sink("arg_borrow", ("take_arg(@SRC@)",), need_mut=True)
EXEC_ONLY_SINKS = (ARG_SINK,)

# Each sink's exec body: the boundary, a write through what it bound, and
# the original read before (`@BEFORE@`) and after (`@AFTER@`). `return` and
# `yield` hand the value out, so their write is the CALLER's (`_driver`).
EXEC_BODY: dict[str, tuple[str, ...]] = {
    "decl_alias": ("@BEFORE@", "x = @SRC@", "@MUT:x@", "@AFTER@"),
    "decl_reassigned": ("@BEFORE@", "x = @SRC@", "x = s1", "@MUT:x@",
                        "@AFTER@"),
    "return_borrow": ("return @SRC@",),
    "field_write": ("@BEFORE@", "h.f = @SRC@", "@MUT:h.f@", "@AFTER@"),
    "tuple_elem": ("@BEFORE@", "t = (@SRC@, 1)", "@MUT:t[0]@", "@AFTER@"),
    "recv_mut": ("@BEFORE@", "@MUT:SRC@", "@AFTER@"),
    "yield_borrow": ("@BEFORE@", "yield @SRC@", "@AFTER@"),
    "match_capture": ("@BEFORE@", "match @SRC@:", "    case v:",
                      "        @MUT:v@", "@AFTER@"),
    "arg_borrow": ("@BEFORE@", "take_arg(@SRC@)", "@AFTER@"),
}

# The ORIGINAL a cell observes, by source: `BACK`, except that a local is
# read back here too (its own trailing read is not a before / after pair)
# and a local at its last use is not -- a read after the boundary would
# make it a live local.
EXEC_ORIG = dict(BACK, local="s0", subclass_local="s0")

# What the CALLER observes after a `return`: the object it passed in, or
# the global. Every other borrowable source's original lives in the callee.
RETURN_ORIG = {"param": "a", "narrowed_opt_param": "a", "global_name": "G"}

ARG_DEFS = ("def take_arg(p: @TY@) -> None:", "    @MUT:p@", "", "")


def _exec_fixture() -> list[str]:
    """FIXTURE without its `v_opt` getter, which no source here reads and
    whose C++ does not build (BUGS.md#property-getter-pointer-optional-return):
    a program that only compiles never meets it, a program that runs does."""
    i = FIXTURE.index("    def v_opt(self) -> Optional[@TY@]:")
    return list(FIXTURE[:i - 1]) + list(FIXTURE[i + 3:])

_EXEC_MARK = "@@ "


def _exec_orig(src, sink: Sink) -> str:
    if sink.name == "decl_reassigned":
        return "s1"
    return EXEC_ORIG[src.name]


def _exec_fill(line: str, kind_name: str, src, orig: str) -> str:
    kind = KINDS[kind_name]
    for tag, word in (("@BEFORE@", "before"), ("@AFTER@", "after")):
        line = line.replace(tag, "print(%s%s%s, %s)" % (
            Q, word, Q, _obs(kind, orig)) if orig else "")
    return _fill(line, kind_name, src, "")


def _exec_cell_body(kind_name: str, src, sink: Sink) -> list[str]:
    orig = _exec_orig(src, sink)
    out = [_fill(p, kind_name, src, "") for p in src.pre]
    out += [_fill(p, kind_name, src, "") for p in sink.pre]
    depth = 0
    for w in src.wrap:
        out.append("    " * depth + _fill(w, kind_name, src, ""))
        depth += 1
    pad = "    " * depth
    for line in EXEC_BODY[sink.name]:
        filled = _exec_fill(line, kind_name, src, orig)
        if filled.strip():
            out.append(pad + filled)
    out += [_fill(p, kind_name, src, "") for p in src.post]
    return out


def _mut(kind_name: str, target: str) -> str:
    return MUTATIONS[kind_name].replace("@X@", target)


def _exec_cell_def(kind_name: str, pos: Position, src, sink: Sink,
                   tag: str) -> list[str]:
    """The cell's definition, with its own exec body in place of the
    survey's. A nested position's `return` is consumed -- written through
    and observed -- inside the outer cell."""
    kind = KINDS[kind_name]
    body = _exec_cell_body(kind_name, src, sink)
    params = ["pv: " + _subst(src.param_ty, kind)] if src.param else []
    sig = ", ".join(params)
    ret = kind.ty if sink.ret else "None"
    if pos.ctor:
        head = ["class Ctor_" + tag + ":", "    tag: int32", "",
                "    def __init__(self" + ("" if not sig else ", " + sig)
                + ") -> None:", "        self.tag = 0"]
        return head + ["        " + b for b in body] + ["", ""]
    if pos.name == "meth":
        head = ["    def cell_" + tag + "(self"
                + ("" if not sig else ", " + sig) + ") -> " + ret + ":"]
        return head + ["        " + b for b in body] + [""]
    if pos.name == "gen":
        elem = kind.ty if sink.name == "yield_borrow" else "int32"
        head = ["def cell_" + tag + "(" + sig + ") -> Iterator[" + elem + "]:"]
        lead = [] if sink.name == "yield_borrow" else ["    yield 0"]
        return head + lead + ["    " + b for b in body] + ["", ""]
    if pos.name == "async":
        head = ["async def cell_" + tag + "(" + sig + ") -> " + ret + ":"]
        return head + ["    " + b for b in body] + ["", ""]
    if pos.name == "nested":
        call = "nested_inner(" + ("pv" if src.param else "") + ")"
        if sink.ret:
            orig = {"param": "pv", "narrowed_opt_param": "pv",
                    "global_name": "G"}.get(src.name, "")
            consume = _observed(kind_name, orig, _take_result(kind_name,
                                                              call))
        else:
            consume = [call]
        head = ["def cell_" + tag + "(" + sig + ") -> None:",
                "    def nested_inner(" + sig + ") -> " + ret + ":"]
        return (head + ["        " + b for b in body]
                + ["    " + c for c in consume] + ["", ""])
    head = ["def cell_" + tag + "(" + sig + ") -> " + ret + ":"]
    return head + ["    " + b for b in body] + ["", ""]


def _take_result(kind_name: str, call: str) -> list[str]:
    """The caller's write through a returned value. Bound to a local first:
    a write straight through a call result (`f(a).append(9)`) is refused
    for the containers (`method.recv.call`), so the observation also
    passes the caller's binding -- the `decl_alias` cell of a borrowing
    call is that binding's own measurement."""
    return ["r = " + call, _mut(kind_name, "r")]


def _observed(kind_name: str, orig: str, lines: list[str]) -> list[str]:
    """`lines` between a before and an after read of `orig`, if any."""
    if not orig:
        return list(lines)
    obs = _obs(KINDS[kind_name], orig)
    return (["print(%sbefore%s, %s)" % (Q, Q, obs)] + list(lines)
            + ["print(%safter%s, %s)" % (Q, Q, obs)])


def _driver(kind_name: str, pos: Position, src, sink: Sink, cell: str,
            tag: str) -> list[str]:
    """`drive_<tag>`: run the cell between two markers; for a `return` or a
    `yield`, write through what came out and observe the original."""
    kind = KINDS[kind_name]
    lines = ["a = " + kind.seed] if src.param else []
    args = "a" if src.param else ""
    if pos.ctor:
        call = "Ctor_%s(%s)" % (tag, args)
    elif pos.name == "meth":
        call = "Host().cell_%s(%s)" % (tag, args)
    else:
        call = "cell_%s(%s)" % (tag, args)
    pre: list[str] = []
    if pos.name == "async":
        if sink.ret:
            # The awaited result is written through inside a coroutine of
            # the driver's own.
            pre = (["async def adrive_" + tag + "() -> None:"]
                   + ["    " + ln for ln in lines + _observed(
                       kind_name, RETURN_ORIG.get(src.name, ""),
                       _take_result(kind_name, "await " + call))]
                   + ["", ""])
            run = ["asyncio.run(adrive_%s())" % tag]
            lines = []
        else:
            run = ["asyncio.run(%s)" % call]
    elif pos.name == "gen":
        if sink.name == "yield_borrow":
            run = ["for r in %s:" % call, "    " + _mut(kind_name, "r")]
        else:
            run = ["for _r in %s:" % call, "    pass"]
    elif pos.ctor:
        run = ["print(%s.tag)" % call]
    elif sink.ret and pos.name != "nested":
        run = _observed(kind_name, RETURN_ORIG.get(src.name, ""),
                        _take_result(kind_name, call))
    else:
        run = [call]
    body = ([("print(%s%s%s%s)" % (Q, _EXEC_MARK, cell, Q))] + lines + run
            + ["print(%s%send%s)" % (Q, _EXEC_MARK, Q)])
    return pre + ["def drive_" + tag + "() -> None:"] + [
        "    " + b for b in body] + ["", ""]


def build_exec_program(kind_name: str, pos_name: str,
                       cells: list[tuple[str, str]]) -> Built:
    """One runnable program: the fixture, each cell's definition and its
    driver, and a `main` that drives them in order."""
    kind, pos = KINDS[kind_name], POSITION_BY_NAME[pos_name]
    lines: list[str] = list(BASE_IMPORTS)
    if pos.name == "gen":
        lines.append("from typing import Iterator")
    if pos.name == "async":
        lines.append("import asyncio")
    lines += [i for i in kind.imports if i not in lines]
    lines += [""]
    lines += list(BASE_DEFS) + [""]
    lines += list(kind.defs)
    if kind.fieldable:
        lines += [_subst(x, kind) for x in _exec_fixture()]
    if any(sink == "field_write" for _src, sink in cells):
        lines += [_subst(x, kind) for x in HOLDER_DEFS]
    if any(sink == ARG_SINK.name for _src, sink in cells):
        lines += [_subst(x.replace("@MUT:p@", _mut(kind_name, "p")), kind)
                  for x in ARG_DEFS]
    sinks = {s.name: s for s in SINKS + EXEC_ONLY_SINKS}
    # A method cell's definition sits in `Host`, away from its driver.
    def_lines: dict[tuple[str, str], tuple[int, int]] = {}
    if pos.name == "meth":
        lines += ["class Host:", "    tag: int32", "",
                  "    def __init__(self) -> None:", "        self.tag = 0",
                  ""]
        for source, sink in cells:
            start = len(lines) + 1
            lines += _exec_cell_def(kind_name, pos, SOURCE_BY_NAME[source],
                                    sinks[sink], "%s__%s" % (source, sink))
            def_lines[(source, sink)] = (start, len(lines))
        lines.append("")
    cell_lines: dict[tuple[str, str], tuple[int, int]] = {}
    for source, sink in cells:
        tag = "%s__%s" % (source, sink)
        start = len(lines) + 1
        if pos.name != "meth":
            lines += _exec_cell_def(kind_name, pos, SOURCE_BY_NAME[source],
                                    sinks[sink], tag)
        lines += _driver(kind_name, pos, SOURCE_BY_NAME[source], sinks[sink],
                         cell_name(kind_name, pos_name, source, sink), tag)
        cell_lines[(source, sink)] = (start, len(lines))
    lines += ["def main() -> None:"] + [
        "    drive_%s__%s()" % c for c in cells] + (
            [] if cells else ["    pass"]) + ["", "", "main()"]
    return Built("\n".join(lines) + "\n", cell_lines, def_lines)


def exec_cells(table: dict[str, list[str]]) -> list[str]:
    """Every cell exec mode runs: each committed cell that compiled (`ok`)
    of a kind with a write spelling, plus the argument sink over every row
    the table keeps (a row `decl_alias` refused at sema is gone from it)."""
    rows = {c.rsplit("__", 1)[0] for c in table}
    out = [c for c, v in table.items()
           if v[0] == "ok" and MUTATIONS[c.split("__")[0]]]
    for name, kind_name, pos_name in programs():
        if not MUTATIONS[kind_name]:
            continue
        pos = POSITION_BY_NAME[pos_name]
        for source in SOURCE_NAMES:
            if pos.frame and source not in FRAME_SOURCES:
                continue
            row = "%s__%s__%s" % (kind_name, pos_name, source)
            if row in rows and _cell_ok(kind_name, pos,
                                        SOURCE_BY_NAME[source], ARG_SINK):
                out.append(row + "__" + ARG_SINK.name)
    return sorted(out)


def _fingerprint(tree: Path, python: str) -> str:
    """What a cached result depends on besides its program: the tree's
    commit, its uncommitted changes and the CPython that ran the twin."""
    def out(*cmd: str) -> str:
        return subprocess.run(list(cmd), capture_output=True, text=True,
                              cwd=tree).stdout
    head = out("git", "rev-parse", "HEAD")
    diff = out("git", "diff", "HEAD", "--", "tpyc", "lib", "runtime")
    ver = out(python, "--version")
    return hashlib.sha256((head + diff + ver).encode()).hexdigest()


def _sections(text: str) -> dict[str, list[str]]:
    """{cell: the lines it printed between its markers}."""
    out: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith(_EXEC_MARK):
            name = ln[len(_EXEC_MARK):]
            cur = None if name == "end" else name
            if cur is not None:
                out[cur] = []
        elif cur is not None:
            out[cur].append(ln)
    return out


def _complete(text: str) -> list[str]:
    """The cells whose end marker printed, in order."""
    done, cur = [], None
    for ln in text.splitlines():
        if ln == _EXEC_MARK + "end" and cur is not None:
            done.append(cur)
            cur = None
        elif ln.startswith(_EXEC_MARK):
            cur = ln[len(_EXEC_MARK):]
    return done


def _observe(lines: list[str]) -> tuple[str, list[str]]:
    """(alias | copy | fresh | mixed:..., the lines that are not
    observations) for one cell's output."""
    before = [ln[len("before "):] for ln in lines if ln.startswith("before ")]
    after = [ln[len("after "):] for ln in lines if ln.startswith("after ")]
    rest = [ln for ln in lines if not ln.startswith(("before ", "after "))]
    if not before and not after:
        return "fresh", rest
    if len(before) != len(after):
        return "unpaired", rest
    kinds = sorted({"copy" if b == a else "alias"
                    for b, a in zip(before, after)})
    return (kinds[0] if len(kinds) == 1 else "mixed:" + "+".join(kinds),
            rest)


def _run(cmd: list[str], cwd: Path, env: dict | None = None,
         timeout: int = 120) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                           env=env, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (
            exc.stdout or "")
        return -999, out, "timeout"


def _first_error(log: str) -> str:
    first = next((ln.strip() for ln in log.splitlines() if "error" in ln),
                 log.strip()[-200:])
    return first[:200]


_LOC_ERROR = re.compile(r"([^\s/:]+\.py):(\d+): error: (.*)$", re.M)


class ExecRunner:
    """Builds and runs exec programs, ONE build at a time, and keeps a
    content-addressed result per cell under `work/cache/`."""

    def __init__(self, tree: Path, work: Path, python: str, jobs: int):
        self.tree, self.work, self.python, self.jobs = tree, work, python, jobs
        self.cache = work / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.fp = _fingerprint(tree, python)
        self.seq = 0

    def key(self, kind_name: str, pos_name: str, cell: tuple[str, str]) -> str:
        text = build_exec_program(kind_name, pos_name, [cell]).text
        return hashlib.sha256((self.fp + text).encode()).hexdigest()

    def cached(self, key: str) -> dict | None:
        path = self.cache / (key + ".json")
        return json.loads(path.read_text()) if path.exists() else None

    def store(self, key: str, result: dict) -> None:
        (self.cache / (key + ".json")).write_text(json.dumps(result) + "\n")

    def _write(self, kind_name: str, pos_name: str,
               cells: list[tuple[str, str]]) -> tuple[Path, Built]:
        self.seq += 1
        d = self.work / "programs" / ("%s__%s__%d" % (kind_name, pos_name,
                                                      self.seq))
        d.mkdir(parents=True, exist_ok=True)
        built = build_exec_program(kind_name, pos_name, cells)
        path = d / "main.py"
        path.write_text(built.text)
        return path, built

    def cpython(self, kind_name: str, pos_name: str,
                cells: list[tuple[str, str]]) -> dict[tuple, dict]:
        """Every cell's CPython output; a cell that raised ends the run, so
        the rest run again without it."""
        out: dict[tuple, dict] = {}
        todo = list(cells)
        env = dict(os.environ, PYTHONPATH=str(self.tree / "lib" / "cpy"))
        while todo:
            path, _built = self._write(kind_name, pos_name, todo)
            rc, stdout, stderr = _run([self.python, str(path)], path.parent,
                                      env=env, timeout=120)
            secs = _sections(stdout)
            done = _complete(stdout)
            for src, sink in todo:
                name = cell_name(kind_name, pos_name, src, sink)
                if name in done:
                    out[(src, sink)] = {"lines": secs.get(name) or []}
            left = [c for c in todo if c not in out]
            if not left:
                break
            # The first unfinished cell is the one that raised.
            bad = left[0]
            last = (stderr.strip().splitlines() or ["rc %d" % rc])[-1]
            out[bad] = {"crash": last[:160]}
            todo = left[1:]
        return out

    def build(self, path: Path) -> tuple[Path | None, str]:
        out_dir = path.parent / "bin"
        rc, stdout, stderr = _run(
            ["uv", "run", "--project", str(self.tree), "tpyc", "-b", "-j",
             str(self.jobs), str(path), "-o", str(out_dir)],
            path.parent, env={k: v for k, v in os.environ.items()
                              if k != "VIRTUAL_ENV"}, timeout=1200)
        text = stdout + stderr
        if rc == 0:
            m = re.search(r"^Built: (.*)$", text, re.M)
            if m:
                exe = Path(m.group(1).strip())
                return (exe if exe.is_absolute() else path.parent / exe), ""
        (path.parent / "build.log").write_text(text)
        return None, text

    def tpy(self, kind_name: str, pos_name: str,
            cells: list[tuple[str, str]]) -> dict[tuple, dict]:
        """Every cell's TPy output, or why it has none: a located compile
        error takes its cell out and the rest build again; an unlocated
        one is halved until it names a cell; a run that stops mid-cell
        charges that cell and runs the rest again."""
        out: dict[tuple, dict] = {}
        todo = [list(cells)]
        fixture_ok = False
        while todo:
            group = todo.pop(0)
            if not group:
                continue
            path, built = self._write(kind_name, pos_name, group)
            exe, log = self.build(path)
            if exe is None:
                located = None
                for m in _LOC_ERROR.finditer(log):
                    if m.group(1) == path.name:
                        located = (int(m.group(2)), m.group(3).strip())
                        break
                owner = (_owner(located[0], built.cell_lines)
                         or _owner(located[0], built.family_lines)
                         if located else None)
                if owner is not None:
                    out[owner] = {"reject": _tag(located[1])}
                    todo.insert(0, [c for c in group if c != owner])
                elif len(group) == 1:
                    out[group[0]] = {"build_fail": _first_error(log)}
                elif not fixture_ok:
                    # Halving a program whose shared part does not build
                    # costs a build per cell and names none of them.
                    p0, _b0 = self._write(kind_name, pos_name, [])
                    exe0, log0 = self.build(p0)
                    if exe0 is None:
                        for c in group + [c for g in todo for c in g]:
                            out[c] = {"build_fail": "fixture: "
                                      + _first_error(log0)}
                        return out
                    fixture_ok = True
                    todo.insert(0, group)
                else:
                    half = len(group) // 2
                    todo[:0] = [group[:half], group[half:]]
                continue
            rc, stdout, stderr = _run([str(exe)], path.parent, timeout=120)
            # The program text stays for a reader; its build does not.
            shutil.rmtree(path.parent / "bin", ignore_errors=True)
            secs = _sections(stdout)
            done = _complete(stdout)
            for src, sink in group:
                name = cell_name(kind_name, pos_name, src, sink)
                if name in done:
                    out[(src, sink)] = {"lines": secs.get(name) or []}
            left = [c for c in group if c not in out]
            if left:
                last = (stderr.strip().splitlines() or ["rc %d" % rc])[-1]
                out[left[0]] = {"crash": last[:160]}
                todo.insert(0, left[1:])
        return out


def exec_verdict(tpy: dict, cpy: dict) -> dict:
    """One cell's record: what each side observed and the verdict -- the
    shared observation, or `differs: <tpy> vs <cpython>`."""
    def side(r: dict) -> tuple[str, list[str]]:
        if "lines" in r:
            return _observe(r["lines"])
        if "crash" in r:
            return "crash: " + r["crash"], []
        return "?", []
    if "reject" in tpy:
        return {"verdict": "reject: " + tpy["reject"]}
    if "build_fail" in tpy:
        return {"verdict": "build_fail: " + tpy["build_fail"]}
    (t_obs, t_rest), (c_obs, c_rest) = side(tpy), side(cpy)
    rec = {"tpy": t_obs, "cpython": c_obs}
    if t_obs != c_obs:
        rec["verdict"] = "differs: %s vs %s" % (t_obs, c_obs)
    elif t_rest != c_rest:
        rec["verdict"] = "differs: output %s vs %s" % (
            "|".join(t_rest)[:120], "|".join(c_rest)[:120])
    else:
        rec["verdict"] = t_obs
    return rec


def run_exec(cells: list[str], runner: ExecRunner,
             results_path: Path) -> dict[str, dict]:
    """Run `cells`, cached ones from the cache, grouped one program per
    (kind, position); the results file is rewritten after every group, so
    an interrupted run resumes from the cache."""
    results: dict[str, dict] = {}
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for cell in cells:
        kind_name, pos_name, source, sink = cell.split("__")
        groups.setdefault((kind_name, pos_name), []).append((source, sink))
    for i, ((kind_name, pos_name), group) in enumerate(sorted(groups.items())):
        keys = {c: runner.key(kind_name, pos_name, c) for c in group}
        todo = []
        for c in group:
            hit = runner.cached(keys[c])
            if hit is not None:
                results[cell_name(kind_name, pos_name, *c)] = hit
            else:
                todo.append(c)
        t0 = time.monotonic()
        if todo:
            cpy = runner.cpython(kind_name, pos_name, todo)
            tpy = runner.tpy(kind_name, pos_name, todo)
            for c in todo:
                rec = exec_verdict(tpy[c], cpy[c])
                runner.store(keys[c], rec)
                results[cell_name(kind_name, pos_name, *c)] = rec
        print("  %d/%d %s__%s: %d cells, %d run, %.1fs" % (
            i + 1, len(groups), kind_name, pos_name, len(group), len(todo),
            time.monotonic() - t0), file=sys.stderr)
        results_path.write_text(json.dumps(results, indent=1, sort_keys=True)
                                + "\n")
    return results


def exec_main(args) -> int:
    table = json.loads(EXPECTED.read_text())
    cells = exec_cells(table)
    if args.only:
        cells = [c for c in cells if args.only in program_of(c)]
    if args.k:
        rx = re.compile(args.k)
        cells = [c for c in cells if rx.search(c)]
    work = (args.exec_dir or REPO / "build" / "ref_sink_exec").resolve()
    runner = ExecRunner(Path(REPO), work, args.python, args.jobs)
    results = run_exec(cells, runner, work / "results.json")
    counts: dict[str, int] = {}
    for rec in results.values():
        key = rec["verdict"].split(":")[0]
        counts[key] = counts.get(key, 0) + 1
    print("%d cells: %s" % (len(results), ", ".join(
        "%s %d" % kv for kv in sorted(counts.items()))))
    for cell, rec in sorted(results.items()):
        if not rec["verdict"].startswith(("alias", "copy", "fresh")):
            print("%s: %s" % (cell, rec["verdict"]))
    print("results: %s" % (work / "results.json"))
    return 0


def main() -> int:
    global REPO
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update", action="store_true",
                    help="rewrite the committed verdict table")
    ap.add_argument("--only", default="",
                    help="only programs whose name matches this substring")
    ap.add_argument("--repo", type=Path, default=None,
                    help="the checkout to compile against")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write the generated programs")
    ap.add_argument("--raw", type=Path, default=None,
                    help="also dump the UNPRUNED table here, for analysis")
    ap.add_argument("--exec", action="store_true",
                    help="build and run every compiling cell under TPy and "
                    "CPython and record alias / copy / differs")
    ap.add_argument("-k", default="",
                    help="--exec: only cells whose name matches this regex")
    ap.add_argument("--exec-dir", type=Path, default=None,
                    help="--exec: programs, cache and results (default "
                    "<repo>/build/ref_sink_exec)")
    ap.add_argument("--python", default="python3",
                    help="--exec: the CPython that runs the twin")
    ap.add_argument("--jobs", type=int, default=2,
                    help="--exec: C++ compile jobs per build")
    args = ap.parse_args()
    if args.repo is not None:
        REPO = args.repo.resolve()
        arg_family_sweep.REPO = REPO
    if args.exec:
        return exec_main(args)
    if args.k:
        print("-k selects exec cells; the survey takes --only",
              file=sys.stderr)
        return 2

    out_dir = args.out or Path(tempfile.mkdtemp(prefix="ref_sink_sweep_"))
    names = sorted(generate(out_dir, args.only))
    t0 = time.monotonic()
    raw = run(out_dir, names)
    elapsed = time.monotonic() - t0
    print("%d programs, %d cells, %.1fs" % (len(names), len(raw), elapsed),
          file=sys.stderr)
    if args.raw:
        args.raw.write_text(json.dumps(raw, indent=1, sort_keys=True) + "\n")
    table, dropped = prune(raw)
    print("%d type-invalid rows dropped" % dropped, file=sys.stderr)

    if args.update:
        if args.only:
            print("--only cannot rewrite the table: it would drop every cell "
                  "the filter excluded", file=sys.stderr)
            return 2
        EXPECTED.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
        print("wrote %s (%d cells)" % (EXPECTED, len(table)))
        return 0

    want = json.loads(EXPECTED.read_text())
    moved = {k: (want.get(k), v) for k, v in table.items() if want.get(k) != v}
    gone = sorted(set(want) - set(table)) if not args.only else []
    if not moved and not gone:
        print("%d cells, no verdict moved" % len(table))
        return 0
    for k, (was, now) in sorted(moved.items()):
        print("MOVED %s: %s -> %s" % (k, was, now))
    for k in gone:
        print("GONE  %s: %s" % (k, want[k]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
