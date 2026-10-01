"""The debug CLI inspects emitted bodies without running or generating files."""

import io
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from .. import cli
from ..mir_workspace import analyze_call_workspace
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from .call_contract import MIRSummaryState
from .collect import (
    MIRStorageCheck, MIRStorageState, MIRVerdictStatus, dump_codegen_mir, enumerate_bodies,
    enumerate_body_sources, verdict_of,
)
from .definitions import MIRDefinitions
from .nodes import MIRBodyId, MIRBodyKind, MIRFunction, function_body_kind


def dump(source: str) -> str:
    compiler, modules = _compile(source)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    return dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name,
                            MIRDefinitions(tuple(ctx.thir_constructors.values())),
                            compiler.thir_reject_by_node)


SOURCE = """\
from tpy import int32

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def read(self) -> int32:
        return self.value
    @staticmethod
    def static() -> int32:
        return 1

class Other:
    def read(self) -> int32:
        return 2

def choose(flag: bool) -> int32:
    current = Cell(1)
    saved = current
    if flag:
        current = Cell(2)
    saved.value = 7
    return current.value

def unsupported(v: int32) -> None:
    # A tuple is no leaf the print contract streams.
    print((v, v))

print(choose(True))
"""


def test_dump_uses_real_bodies_and_constructor_definitions() -> None:
    out = dump(SOURCE)
    assert "entry bb0" in out
    assert "owned-storage" in out
    assert "record writes (logical replacement; no physical lifetime-end verdict)" in out
    assert "own_site" in out
    assert "retention (possible logical-object conflicts; no lifetime-safety verdict)" in out
    assert "payload ends (possible inline scalar lifetime ends; no safety verdict)" in out
    assert "payload retention (possible conflicts at static places; no lifetime-safety verdict)" in out
    assert "branch" in out
    for name in ("Cell.read", "Other.read", "Cell.__init__", "choose"):
        assert re.search(rf"fn .*::{re.escape(name)}@[^\n]+ ->", out), out
    assert re.search(r"fn .*::Cell.static@[^\n]+ -> int32", out), out
    assert re.search(r"unsupported@[^\n]+<MIR not covered:", out)
    assert "<MIR not covered: module initialization>" in out
    assert out == dump(SOURCE)


def test_owned_tuple_dump_includes_each_inline_member() -> None:
    out = dump('''from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def owned_pair() -> int32:
    pair = (Cell(1), Cell(2))
    pair[0].value = 9
    return pair[0].value
''')
    assert re.search(r"fn .*::owned_pair@[^\n]+ ->", out)
    assert "initialize-tuple-members %0[0], %0[1]" in out
    assert "[initialize-tuple]" in out
    assert "scope ends (possible normal storage ends" in out


def test_range_dump_exposes_induction_and_unsupported_step() -> None:
    out = dump('''from tpy import int32
def covered(stop: int32) -> int32:
    last = 7
    for i in range(stop):
        last = i
    return last
def uncovered(stop: int32) -> int32:
    for i in range(0, stop, 2):
        return i
    return 7
''')
    body = out[out.index("::covered@"):].split("\nfn ", 1)[0]
    assert "<MIR not covered:" not in body
    assert "range-advance" in body
    assert "<MIR not covered: unsupported metadata: step>" in out


def test_native_dump_reports_captured_source_and_element_dependencies() -> None:
    out = dump('''from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def walk(xs: list[Cell]) -> int32:
    result = 0
    for cell in xs:
        cell.value = 7
        result = cell.value
    return result
''')
    body = out[out.index("::walk@"):].split("\nfn ", 1)[0]
    assert "<MIR not covered:" not in body
    assert "iterator-init" in body and "iterator-read" in body
    assert ".structure" in body and ".elements" in body


def test_owned_tuple_alias_dump_uses_canonical_backing() -> None:
    out = dump('''from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def alias_pair() -> int32:
    pair = (Cell(1),)
    saved = pair
    chain = saved
    chain[0].value = 9
    return pair[0].value
''')
    body = out[out.index("::alias_pair@"):]
    body = body.split("\nfn ", 1)[0]
    assert "<MIR not covered:" not in body
    assert body.count("[initialize-tuple]") == 1
    assert "tuple-copy" not in body
    assert "saved" not in body and "chain" not in body


def test_record_hoist_dump_handles_a_contradictory_assignment_path() -> None:
    out = dump('''from tpy import int32
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value

def contradictory(flag: bool) -> int32:
    if flag:
        if flag:
            return 0
        else:
            cell = Cell(1)
    else:
        return 0
    return cell.value
''')
    assert re.search(r"fn .*::contradictory@[^\n]+ -> int32", out), out
    assert "optional_assign" in out
    assert "no conflicts in covered replacement events" in out


def test_generic_and_resumable_bodies_are_not_monomorphic_functions() -> None:
    out = dump("""\
from tpy import int32
from typing import Iterator
def generic[T](x: T) -> int32:
    return 1
class Generic[T]:
    def read(self) -> int32:
        return 1
def values() -> Iterator[int32]:
    yield 1
async def ready() -> int32:
    return 1
""")
    assert out.count("<MIR not covered: generic body>") == 2, out
    assert out.count("<MIR not covered: resumable body>") == 2, out


def test_generated_clones_have_distinct_stable_identities() -> None:
    source = """\
from tpy import int32, auto_readonly
class Cell:
    @auto_readonly
    def constant(self) -> int32:
        return 1
"""
    out = dump(source)
    names = re.findall(r"^fn (\S+::Cell\.constant@\d+:\d+(?:#\d+)?)(?:: <| ->)", out, re.MULTILINE)
    assert len(names) == 2 and len(set(names)) == 2, out
    assert names[1] == names[0] + "#2", out
    assert out == dump(source)


def test_bodyless_and_rejected_and_unattempted_are_distinct() -> None:
    out = dump("""\
from tpy import int32
from tpy.extern import native
from tplib import Box
@native(binding="C")
def external(x: int32) -> int32: ...
class Point:
    x: int32
    def __init__(self, x: int32):
        self.x = x
def before() -> int32:
    return 1
def rejects() -> None:
    d = {1: Box(Point(1))}
    other = {2: Box(Point(2))}
    d = other
    print(len(d))
def after() -> int32:
    return 2
""")
    assert re.search(r"external@[^\n]+<no body to lower>", out), out
    assert re.search(r"before@[^\n]+ -> int32", out), out
    assert re.search(r"rejects@[^\n]+<THIR rejected:", out), out
    # A function body after the rejecting one is still attempted; only a unit
    # that lowers during emission is cut off by the reject.
    assert re.search(r"after@[^\n]+ -> int32", out), out
    assert re.search(r"__tpy_init: <THIR not attempted:", out), out


@pytest.mark.parametrize("runner", [False, True])
def test_cli_dump_does_not_execute_or_write_cpp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture, runner: bool) -> None:
    source = tmp_path / "main.py"
    source.write_text(SOURCE)
    monkeypatch.setattr(sys, "argv", ["tpy" if runner else "tpyc", "--dump-mir", str(source)])
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    assert cli._run_cli(runner) == 0
    output = capsys.readouterr().out
    assert "// === mir/main ===" in output
    assert "entry bb0" in output
    assert "liveness entry:" in output
    assert "dependencies (external origins may alias; no safety verdict)" in output
    assert "before 0:" in output
    assert not re.search(r"^2$", output, re.MULTILINE)
    assert not (tmp_path / "__tpyc__").exists()


@pytest.mark.parametrize("other", ["--build", "--exec", "--dump-code", "--dump-thir"])
def test_cli_rejects_conflicting_actions(monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture, other: str) -> None:
    monkeypatch.setattr(sys, "argv", ["tpyc", "--dump-mir", other, "-c", "pass"])
    with pytest.raises(SystemExit) as error:
        cli._run_cli(False)
    assert error.value.code == 2
    assert "--dump-mir cannot be combined" in capsys.readouterr().err


def test_cli_collects_imported_constructor_definitions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture) -> None:
    (tmp_path / "model.py").write_text(SOURCE.split("class Other:")[0])
    source = tmp_path / "main.py"
    source.write_text("from model import Cell\nfrom tpy import int32\n"
                      "def f() -> int32:\n    cell = Cell(3)\n    return cell.value\n")
    monkeypatch.setattr(sys, "argv", ["tpyc", "--dump-mir", str(source)])
    assert cli._run_cli(False) == 0
    out = capsys.readouterr().out
    assert "// === mir/model ===" in out
    assert re.search(r"fn main::f@[^\n]+ -> int32", out), out
    assert "missing constructor definition" not in out


@pytest.mark.parametrize("mode", ["--dump-mir", "--dump-thir"])
@pytest.mark.parametrize("inline", [False, True])
def test_dump_inline_and_stdin(mode: str, inline: bool, monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture) -> None:
    source = "from tpy import int32\ndef f() -> int32:\n    return 3\n"
    monkeypatch.setattr(sys, "argv", ["tpyc", mode, *(["-c", source] if inline else ["-"])])
    monkeypatch.setattr(sys, "stdin", io.StringIO(source))
    assert cli._run_cli(False) == 0
    out = capsys.readouterr().out
    assert "mir/" in out if mode == "--dump-mir" else "thir/" in out


def test_overloads_and_nested_closures_report_coverage_limits() -> None:
    out = dump("""\
from tpy import int32
from typing import overload
@overload
def identity(x: int32) -> int32: ...
def identity(x: int32) -> int32:
    return x
def closure(x: int32) -> int32:
    def inner() -> int32:
        return x
    return inner()
""")
    assert "<MIR not covered: overloaded callable>" in out, out
    assert re.search(r"closure@[^\n]+<MIR not covered:", out), out


KIND_SOURCE = """\
from tpy import int32

class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def read(self) -> int32:
        return self.value
    @staticmethod
    def static(value: int32) -> int32:
        return value
    @property
    def prop(self) -> int32:
        return self.value
    def __bool__(self) -> bool:
        return self.value != 0

def free(value: int32) -> int32:
    return value
"""

# Only an ordinary method carries THIR's receiver fact; an owner record alone
# (staticmethod, property, dunder) does not make a body a METHOD.
KINDS = {"read": MIRBodyKind.METHOD, "static": MIRBodyKind.FREE_FUNCTION,
         "prop": MIRBodyKind.FREE_FUNCTION, "__bool__": MIRBodyKind.FREE_FUNCTION,
         "free": MIRBodyKind.FREE_FUNCTION}


def test_both_entry_paths_take_the_kind_from_the_receiver_fact() -> None:
    compiler, modules = _compile(KIND_SOURCE)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    functions = {node.name: fn for node, fn in ctx.thir_functions.items()}
    assert {name: function_body_kind(functions[name]) for name in KINDS} == KINDS
    callee = functions["free"].resolved_callee
    with compiler.mir_analysis([(entry, ctx)]) as program:
        out = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, program.definitions,
                               compiler.thir_reject_by_node, program.workspace)
        # Only free functions carry a resolved callee today; granting one to
        # every body pins that the scheduler, too, reads the receiver fact.
        scheduled = tuple((MIRBodyId("kinds", name), replace(fn, resolved_callee=replace(
            callee, identity=th.THIRFunctionIdentity("kinds", name)))) for name, fn in functions.items()
            if name in ("read", "static", "free"))
        workspace = analyze_call_workspace(scheduled, program.definitions)
    for name in ("Cell.read", "Cell.static", "free"):
        assert re.search(rf"fn .*::{re.escape(name)}@[^\n]+ -> int32", out), out
    # Properties and dunders still lack the receiver fact their `self` needs.
    for name in ("Cell.prop", "Cell.__bool__"):
        assert re.search(rf"{re.escape(name)}@[^\n]+<MIR not covered: missing receiver fact>", out), out
    for body, _fn in scheduled:
        lowered = workspace.bodies[body]
        assert isinstance(lowered, MIRFunction), lowered
        assert lowered.kind is KINDS[body.declaration]


VERDICT_SOURCE = """\
from typing import Iterator
from tpy import int32, readonly, nocopy

@nocopy
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
    def read(self) -> int32:
        return self.value

def observe(cell: Cell) -> readonly[Cell]:
    return cell

def borrowed(value: int32) -> int32:
    # The materialized constructor argument backs the borrowed result.
    saved = observe(Cell(value))
    return saved.value

def plain(value: int32) -> int32:
    return value + 1

def countdown(n: int32) -> int32:
    if n <= 0:
        return 0
    return countdown(n - 1)

def gen(n: int32) -> Iterator[int32]:
    yield n

print(borrowed(1), plain(2), countdown(3), Cell(4).read())
"""


def _verdicts(source: str):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    with compiler.mir_analysis([(entry, ctx)]) as program:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, program.definitions,
                                    compiler.thir_reject_by_node, program.workspace)
        out = dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, program.definitions,
                               compiler.thir_reject_by_node, program.workspace)
    return verdicts, out, program.workspace


@pytest.mark.parametrize("source", [SOURCE, VERDICT_SOURCE, KIND_SOURCE], ids=["dump", "verdicts", "kinds"])
def test_every_dumped_and_scheduled_body_has_exactly_one_verdict(source: str) -> None:
    verdicts, out, workspace = _verdicts(source)
    bodies = [v.body for v in verdicts]
    assert len(bodies) == len(set(bodies))
    dumped = re.findall(r"^fn [^:\s]+::(\S+?)(?::? <| -> )", out, re.MULTILINE)
    assert [b.declaration for b in bodies] == dumped
    assert set(workspace.bodies) <= set(bodies)


def test_fresh_lowering_agrees_with_the_workspace_cache() -> None:
    compiler, modules = _compile(VERDICT_SOURCE)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, tolerate_reject=True)
    with compiler.mir_analysis([(entry, ctx)]) as program:
        sources = list(enumerate_body_sources(entry.ast, entry.analyzer, ctx, entry.name,
                                              compiler.thir_reject_by_node))
        assert sources
        for source in sources:
            cached = verdict_of(source, program.definitions, program.workspace)
            fresh = verdict_of(source, program.definitions, program.workspace, fresh=True)
            assert (fresh.status, fresh.reason, fresh.conflicts) == (cached.status, cached.reason, cached.conflicts)


def test_verdicts_climb_the_ladder() -> None:
    verdicts, _out, _workspace = _verdicts(VERDICT_SOURCE)
    by_name = {v.name: v for v in verdicts}
    borrowed = by_name["borrowed"]
    assert (borrowed.status, borrowed.storage, borrowed.conflicts) == (
        MIRVerdictStatus.CERTIFIED, MIRStorageCheck(MIRStorageState.CERTIFIED), ())
    assert borrowed.line == 15 and borrowed.kind is MIRBodyKind.FREE_FUNCTION
    # Nothing to prove is covered, never certified.
    plain = by_name["plain"]
    assert (plain.status, plain.storage) == (
        MIRVerdictStatus.COVERED, MIRStorageCheck(MIRStorageState.NO_PROOF_REQUIRED))
    assert plain.summary is not None and plain.summary.state is MIRSummaryState.KNOWN
    gen = by_name["gen"]
    assert (gen.status, gen.reason, gen.function, gen.storage) == (
        MIRVerdictStatus.UNCOVERED, "resumable body", None, None)
    countdown = by_name["countdown"]
    assert countdown.summary is not None and countdown.summary.state is MIRSummaryState.OPAQUE
    assert "recursi" in countdown.summary.reason
    ctor = by_name["__init__"]
    assert ctor.kind is MIRBodyKind.CONSTRUCTOR and ctor.summary is None and ctor.line == 7
    init, = (v for v in verdicts if v.line is None)
    assert (init.name, init.status, init.reason) == ("__tpy_init", MIRVerdictStatus.UNCOVERED,
                                                     "module initialization")
