"""The debug CLI inspects emitted bodies without running or generating files."""

import io
import re
import sys
from pathlib import Path

import pytest

from .. import cli
from ..thir.testutil import _compile, _entry
from .collect import dump_codegen_mir
from .definitions import MIRDefinitions


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

def unsupported() -> None:
    print(1)

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
    assert re.search(r"Cell.static@[^\n]+<MIR not covered: body kind and receiver mismatch>", out)
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
    names = re.findall(r"^fn ([^\n]+::Cell.constant@[^\n]+?): <MIR not covered:", out, re.MULTILINE)
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
    assert re.search(r"after@[^\n]+<THIR not attempted:", out), out


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
