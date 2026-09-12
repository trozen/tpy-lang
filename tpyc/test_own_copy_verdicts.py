"""The owning-slot copy verdicts of one compilation (sema/own_copy.py)."""

import dataclasses

import pytest

from . import get_lib_dir
from .compiler import Compiler
from .diagnostics import DiagnosticLevel

_STDLIB_DIRS = [get_lib_dir() / "tpy"]

_SLOTS = (
    "from tpy import int32\n"
    "\n"
    "\n"
    "def insert_slot[T](v: T) -> int32:\n"
    "    xs: list[T] = []\n"
    "    xs.append(v)\n"
    "    return len(xs)\n"
)

_NONCOPYABLE_MAIN = (
    "from tpy import int32\n"
    "from slots import insert_slot\n"
    "\n"
    "\n"
    "class Pinned:\n"
    "    n: int32\n"
    "\n"
    "    def __init__(self, n: int32) -> None:\n"
    "        self.n = n\n"
    "\n"
    "    def __del__(self) -> None:\n"
    "        self.n = 0\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    print(insert_slot(Pinned(3)))\n"
    "\n"
    "\n"
    "main()\n"
)

_COPYABLE_MAIN = (
    "from slots import insert_slot\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    print(insert_slot(3))\n"
    "\n"
    "\n"
    "main()\n"
)


def _compile(tmp_path, main_source):
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    (lib / "slots.py").write_text(_SLOTS)
    compiler = Compiler.from_source(main_source, lib_dirs=[lib] + _STDLIB_DIRS)
    modules = {m.name: m for m in compiler.compile()}
    return compiler, modules["slots"].analyzer


def test_verdict_is_recorded_beside_the_body_not_on_it(tmp_path):
    compiler, slots = _compile(tmp_path, _NONCOPYABLE_MAIN)
    (obligation,) = slots.ctx.own_copy_obligations

    # The hedge the body recorded is what it always was.
    assert obligation.diag.level == DiagnosticLevel.WARNING
    assert obligation.diag.message.startswith("may copy T into owned storage")
    with pytest.raises(dataclasses.FrozenInstanceError):
        obligation.diag = None

    # The program's answer sits in its own table...
    (verdict,) = compiler.own_copy_verdicts.promoted(obligation)
    assert verdict.level == DiagnosticLevel.ERROR
    assert "cannot copy non-copyable type 'Pinned'" in verdict.message
    assert verdict.loc == obligation.diag.loc

    # ...and the module's output shows the verdict at the hedge's line, with
    # the hedge gone.
    lines = [(d.level, d.loc.line) for d in slots.diagnostics]
    assert (DiagnosticLevel.ERROR, obligation.diag.loc.line) in lines
    assert all(not d.message.startswith("may copy") for d in slots.diagnostics)


def test_copyable_instantiation_leaves_no_verdict(tmp_path):
    compiler, slots = _compile(tmp_path, _COPYABLE_MAIN)
    (obligation,) = slots.ctx.own_copy_obligations
    assert len(compiler.own_copy_verdicts) == 0
    assert compiler.own_copy_verdicts.promoted(obligation) == []
    assert [d.message for d in slots.diagnostics
            if d.loc.line == obligation.diag.loc.line
            ] == [obligation.diag.message]


_LOCKED = (
    "class Locked:\n"
    "    n: int32\n"
    "\n"
    "    def __init__(self, n: int32) -> None:\n"
    "        self.n = n\n"
    "\n"
    "    def __del__(self) -> None:\n"
    "        self.n = 1\n"
)


def _errors_at(analyzer, line):
    return [d.message for d in analyzer.diagnostics
            if d.level == DiagnosticLevel.ERROR and d.loc.line == line]


def test_two_types_report_twice_in_order_and_a_repeat_once(tmp_path):
    main = _NONCOPYABLE_MAIN.replace(
        "def main() -> None:\n",
        _LOCKED + "\n\ndef main() -> None:\n"
        "    print(insert_slot(Pinned(1)))\n"
        "    print(insert_slot(Locked(2)))\n")
    compiler, slots = _compile(tmp_path, main)
    (obligation,) = slots.ctx.own_copy_obligations
    verdicts = compiler.own_copy_verdicts.promoted(obligation)
    assert ["'Pinned'" in v.message for v in verdicts] == [True, False]
    assert "'Locked'" in verdicts[1].message
    # The verdicts take the hedge's line, in the order they resolved; the
    # third call, at Pinned again, adds nothing.
    assert _errors_at(slots, obligation.diag.loc.line) == [
        v.message for v in verdicts]


def test_clones_of_one_body_collapse_to_one_report(tmp_path):
    # An `except (A, B)` handler is analyzed once per type, so the sink in it
    # records one obligation per clone; the promoted output is still one line.
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    (lib / "slots.py").write_text(
        "from tpy import int32\n"
        "\n"
        "\n"
        "def insert_slot[T](v: T) -> int32:\n"
        "    xs: list[T] = []\n"
        "    try:\n"
        "        raise ValueError('x')\n"
        "    except (ValueError, TypeError):\n"
        "        xs.append(v)\n"
        "    return len(xs)\n")
    compiler = Compiler.from_source(_NONCOPYABLE_MAIN,
                                    lib_dirs=[lib] + _STDLIB_DIRS)
    slots = {m.name: m for m in compiler.compile()}["slots"].analyzer
    obligations = slots.ctx.own_copy_obligations
    assert len(obligations) >= 2
    (line,) = {o.diag.loc.line for o in obligations}
    assert len(_errors_at(slots, line)) == 1


def test_withdrawn_hedge_takes_no_verdict(tmp_path):
    # A consuming loop moves its elements, so the loop-copy path withdraws
    # the hedge during body analysis; a non-copyable instantiation must not
    # bring it back as an error.
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    (lib / "slots.py").write_text(
        "from tpy import int32, Own\n"
        "\n"
        "\n"
        "def drain[T](xs: Own[list[T]]) -> int32:\n"
        "    out: list[T] = []\n"
        "    for x in xs:\n"
        "        out.append(x)\n"
        "    return len(out)\n")
    main = _NONCOPYABLE_MAIN.replace("from slots import insert_slot",
                                     "from slots import drain").replace(
        "    print(insert_slot(Pinned(3)))\n",
        "    print(drain([Pinned(3)]))\n")
    compiler = Compiler.from_source(main, lib_dirs=[lib] + _STDLIB_DIRS)
    slots = {m.name: m for m in compiler.compile()}["slots"].analyzer
    (obligation,) = slots.ctx.own_copy_obligations
    assert all(d is not obligation.diag for d in slots.diagnostics)
    assert not [d for d in slots.diagnostics
                if d.level == DiagnosticLevel.ERROR]
