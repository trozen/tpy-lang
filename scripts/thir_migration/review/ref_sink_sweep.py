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

WHAT IT DOES NOT RECORD. Behaviour. Nothing is built or run, so the render is
as close to copy-vs-alias as this instrument gets; the corpus cases stay the
place that observes a mutation through an alias.

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
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arg_family_sweep import (  # noqa: E402
    BASE_DEFS, BASE_IMPORTS, BEACON, FIXTURE, KINDS, Q, SOURCES, Built,
    _kind_has, _owner, _render, _subst, _tag, _tpyc)
import arg_family_sweep  # noqa: E402

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
SOURCE_NAMES = ("local", "local_last", "param", "obj_field", "subscript",
                "dict_subscript", "borrow_method", "prop_ref", "prop_own",
                "narrowed_opt", "own_call", "ctor_rvalue", "temp_accessor",
                "global_name", "loop_var", "closure_capture",
                "subclass_local")

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
                        "ctor_rvalue", "temp_accessor", "global_name"})

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
    params = ["pv: " + kind.ty] if src.param else []
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

def _thir_dump():
    """The three body renderers, imported through the arg sweep's loader so
    `--repo` retargeting stays in one place."""
    _tpyc()
    from tpyc.thir.dump import (_constructor_lines, _function_lines,
                                _resumable_lines)
    from tpyc.thir.lower import iter_module_callables
    from tpyc.thir.lower.functions import iter_module_constructors
    return (_function_lines, _resumable_lines, _constructor_lines,
            iter_module_callables, iter_module_constructors)


def _cell_nodes(entry, pos: Position) -> dict:
    """{(source, sink): the body node of that cell}.

    A constructor is not in `iter_module_callables` -- its body emits through
    the member-init driver -- so the ctor position reads the sibling feed.
    """
    (_fl, _rl, _cl, iter_module_callables,
     iter_module_constructors) = _thir_dump()
    if pos.ctor:
        return {tuple(rec.name[5:].split("__")): init
                for rec, init, _s in iter_module_constructors(entry.ast,
                                                              entry.analyzer)
                if rec.name.startswith("Ctor_")}
    return {tuple(fn.name[5:].split("__")): fn
            for fn, _s in iter_module_callables(entry.ast, entry.analyzer)
            if fn.name.startswith("cell_")}


def _verdict(ctx, compiler, node) -> tuple[str | None, str]:
    """The cell's verdict and render, or `(None, "")` for a body an earlier
    reject stopped the pass before reaching."""
    (_function_lines, _resumable_lines, _constructor_lines,
     _imc, _imk) = _thir_dump()
    if node is None:
        return "crash:no such body", ""
    if node in ctx.thir_functions:
        return "ok", _render(_function_lines(ctx.thir_functions[node]))
    res = ctx.thir_resumables.get(node)
    if res is not None:
        return "ok", _render(_resumable_lines(node.name, res))
    if node in ctx.thir_constructors:
        return "ok", _render(_constructor_lines(node.name,
                                                ctx.thir_constructors[node]))
    why = compiler.thir_reject_by_node.get(node)
    if why is not None:
        return "reject:" + str(why), ""
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
    (Compiler, CodeGenOptions, DiagnosticLevel, SemanticError,
     _function_lines, _imc) = _tpyc()
    _name, kind_name, pos_name = next(p for p in programs() if p[0] == program)
    pos = POSITION_BY_NAME[pos_name]
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / ((stem or program) + ".py")
    lib = REPO / "lib" / "tpy"

    active = (list(cells) if cells is not None
              else program_cells(kind_name, pos_name))
    if expect_solo:
        held = set(expect_solo)
        active = [c for c in active if c not in held]
    out: dict[str, list[str]] = {}
    while active:
        built = build_program(kind_name, pos_name, active)
        path.write_text(built.text)
        try:
            compiler = Compiler(path, default_int="int32", lib_dirs=[lib])
            modules = compiler.compile()
            diags = list(compiler.diagnostics)
            for mod in modules:
                an = getattr(mod, "analyzer", None)
                if an is not None:
                    diags += list(getattr(an, "diagnostics", []))
            errors = [d for d in diags if d.level == DiagnosticLevel.ERROR]
            if errors:
                raise SemanticError(str(errors[0].message),
                                    getattr(errors[0], "loc", None))
        except SemanticError as exc:
            line = getattr(getattr(exc, "loc", None), "line", 0) or 0
            cell = _owner(line, built.cell_lines)
            msg = "sema:" + _stable(str(exc).strip().split("\n")[0])[:80]
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
        except Exception as exc:   # noqa: BLE001 -- an ICE is a verdict here
            msg = "crash:" + _stable(str(exc).strip().split("\n")[0])[:80]
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

        entry = next(m for m in modules if m.is_entry_point)
        ctx = compiler.collect_thir(entry, CodeGenOptions(),
                                    tolerate_reject=True)
        nodes = _cell_nodes(entry, pos)
        warnings = [d for d in diags if d.level == DiagnosticLevel.WARNING]
        settled: list[tuple[str, str]] = []
        for cell in active:
            verdict, render = _verdict(ctx, compiler, nodes.get(cell))
            if verdict is None:
                continue
            lo, hi = built.cell_lines[cell]
            mine = [w for w in warnings
                    if lo <= (getattr(getattr(w, "loc", None), "line", 0)
                              or 0) <= hi]
            mine.sort(key=lambda w: getattr(getattr(w, "loc", None),
                                            "line", 0) or 0)
            out[cell_name(kind_name, pos_name, *cell)] = [
                # `_stable` before the tag's own truncation, so the shorter
                # spelling does not move the cut by a character.
                _stable(verdict),
                _tag(_stable(mine[0].message)) if mine else "",
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
    args = ap.parse_args()
    if args.repo is not None:
        REPO = args.repo.resolve()
        arg_family_sweep.REPO = REPO

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
