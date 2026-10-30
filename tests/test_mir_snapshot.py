"""`expected/mir.txt`, the per-case MIR verdict snapshot: the body names it
spells, the library modules it covers, and the comp phase comparing it.

The names are `--dump-mir`'s body identities, so they are pinned on a program
holding every shape that once leaked an internal spelling (an enum method, a
generator expression) beside module init, a second module and an
`@auto_readonly` twin. Only refusals decided by the body's kind are pinned
verbatim; the other verdicts move with MIR, as the corpus snapshots do.
"""
from pathlib import Path

import pytest

import conftest
import test_case as harness

MAIN = """\
from enum import Enum

from tpy import Span, auto_readonly, int32

from helper import twice


class Color(Enum):
    RED = 1
    GREEN = 2

    def is_red(self) -> bool:
        return self == Color.RED


class Buffer:
    data: list[int32]

    def __init__(self) -> None:
        self.data = [1, 2, 3]

    @auto_readonly
    def view(self) -> Span[auto_readonly[int32]]:
        return self.data


def total(xs: list[int32]) -> int32:
    return sum(x * 2 for x in xs)


counter = twice(3)
print(counter, Color.RED.is_red(), total([1, 2]), Buffer().view()[0])
"""

HELPER = """\
from tpy import int32


def twice(n: int32) -> int32:
    return n * 2
"""

# The declaration of every line, in order: modules by name, each module's
# bodies in `--dump-mir` order.
NAMES = [
    "helper::__tpy_init",
    "helper::twice@4:0",
    "main::__tpy_init",
    "main::total@27:0",
    "main::<genexpr>@28:14",
    "main::Color.is_red@12:4",
    "main::Buffer.view@23:4",
    "main::Buffer.view@23:4#2",
    "main::Buffer.__init__@19:4",
]


def names_program(root: Path) -> Path:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "helper.py").write_text(HELPER)
    main = src / "main.py"
    main.write_text(MAIN)
    return main


def test_lines_name_every_body_of_every_local_module(tmp_path):
    main = names_program(tmp_path)
    result = conftest.compile_with_diagnostics(main, tmp_path / "__tpyc__")
    assert result.success, result.diagnostics
    lines = result.mir_text.splitlines()
    assert [line.split(": ", 1)[0] for line in lines] == NAMES
    verdicts = dict(line.split(": ", 1) for line in lines)
    assert verdicts["main::__tpy_init"] == "uncovered (module initialization)"
    assert verdicts["main::<genexpr>@28:14"] == "uncovered (generator expression)"


def test_snapshotted_library_modules_follow_in_name_order(tmp_path):
    main = names_program(tmp_path)
    result = conftest.compile_with_diagnostics(main, tmp_path / "__tpyc__",
                                               snapshot_lib_modules=frozenset({"typing"}))
    assert result.success, result.diagnostics
    modules = [line.split("::", 1)[0] for line in result.mir_text.splitlines()]
    assert modules == sorted(modules)
    assert "typing" in modules
    # A snapshotted library module gets verdicts of its own; the case's own
    # lines are the ones it has without it.
    alone = conftest.compile_with_diagnostics(main, tmp_path / "__tpyc_alone__")
    assert [m for m in result.mir_text.splitlines() if not m.startswith("typing::")] == \
        alone.mir_text.splitlines()
    # Its warnings stay out of the case's diagnostics.
    assert result.diagnostics == alone.diagnostics


class _Request:
    """The slice of pytest's `request` the comp phase reads."""

    def __init__(self, request):
        self.config = request.config
        self.node = request.node
        self.finalizers = []

    def addfinalizer(self, fn):
        self.finalizers.append(fn)


def _case(tmp_path: Path, name: str, source: str) -> tuple[Path, Path]:
    case_dir = tmp_path / name
    (case_dir / "src").mkdir(parents=True)
    main = case_dir / "src" / "main.py"
    main.write_text(source)
    return case_dir, main


@pytest.fixture
def compare_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", False)
    monkeypatch.setattr(harness, "UPDATE_EXPECTED", False)
    # The options.json walk stops at the cases root; a temp case needs one.
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path)


def test_a_changed_verdict_fails_the_compare(tmp_path, compare_mode, request):
    case_dir, main = _case(tmp_path, "probe", "def f() -> int:\n    return 1\n\n\nprint(f())\n")
    actual = conftest.compile_with_diagnostics(main, case_dir / "__tpyc__").mir_text
    assert "main::f@1:0: " in actual
    mir = case_dir / "expected" / "mir.txt"
    mir.parent.mkdir()
    mir.write_text(actual.replace("main::f@1:0: ", "main::f@1:0: certified, was "))
    with pytest.raises(pytest.fail.Exception) as info:
        harness.test_case(case_dir, main, _Request(request))
    assert "MIR verdicts differs" in str(info.value)


def test_the_written_snapshot_is_the_one_compared(tmp_path, monkeypatch):
    main = names_program(tmp_path)
    actual = conftest.compile_with_diagnostics(main, tmp_path / "__tpyc__").mir_text
    mir = tmp_path / "expected" / "mir.txt"
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", True)
    conftest.check_or_update(actual, mir, "MIR verdicts")
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", False)
    again = conftest.compile_with_diagnostics(main, tmp_path / "__tpyc_again__").mir_text
    conftest.check_or_update(again, mir, "MIR verdicts")


ERROR_SOURCE = ("from tpy import int32\n\n"
                "too_big: int32 = 3000000000  # tpyc: error(/int32 range/)\n")


def test_a_stale_file_beside_a_case_without_codegen_fails(tmp_path, compare_mode, request):
    case_dir, main = _case(tmp_path, "error_probe", ERROR_SOURCE)
    expected = case_dir / "expected"
    expected.mkdir()
    (expected / "diag.txt").write_text(
        conftest.compile_with_diagnostics(main, case_dir / "__tpyc__").diagnostics)
    (expected / "mir.txt").write_text("main::__tpy_init: uncovered (module initialization)\n")
    with pytest.raises(pytest.fail.Exception) as info:
        harness.test_case(case_dir, main, _Request(request))
    assert "mir.txt is left from a compile that reached codegen" in str(info.value)


def test_update_mode_wipes_a_stale_file(tmp_path, monkeypatch, request):
    case_dir, main = _case(tmp_path, "error_probe", ERROR_SOURCE)
    mir = case_dir / "expected" / "mir.txt"
    mir.parent.mkdir()
    mir.write_text("stale\n")
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(harness, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path)
    harness.test_case(case_dir, main, _Request(request))
    assert not mir.exists()
    assert (case_dir / "expected" / "diag.txt").exists()
