"""MIR's warnings through the real CLI -- the argument-alias warning and a
lifetime hazard MIR proves -- printed by a compile that emits C++ only,
and by a cold build, then replayed from the build cache by a warm one."""

import shutil
import sys
from pathlib import Path

import pytest

from .cli import main_tpyc
from .toolchain import CompilerNotFoundError, CppCompilerConfig, ToolchainUnsupportedError

_PROG = """\
from tpy import StrView


class Tok:
    s: StrView

    def __init__(self, s: StrView) -> None:
        self.s = s


def mk(c: str) -> str:
    return c * 40


def local_buf() -> None:
    buf = mk("a")
    t = Tok(buf)
    buf = mk("b")
    print(t.s)


def bound(events: list[int], tag: int, value: int) -> int:
    events.append(tag)
    return value


def alias(xs: list[int]) -> int:
    return bound(xs, 1, xs[0])


print(bound([1], 2, 3))
"""

_WARNING = ("prog.py:28: warning: argument 'xs[0]' points into 'xs', which 'bound' modifies through "
            "parameter 'events' while it still uses 'value'")
# Never run: the program only builds.
_HAZARD = ("prog.py:18: warning: this assignment frees the old value of 'buf', but 't.s' still views it "
           "and is read on line 19; give the new value its own name, or make 'Tok.s' an owned 'str'")


def _toolchain() -> bool:
    try:
        config = CppCompilerConfig.from_env(cxx="auto")
    except (CompilerNotFoundError, ToolchainUnsupportedError):
        return False
    # Auto-detection falls back to a bare "g++" when it finds no compiler.
    return shutil.which(config.compiler[0]) is not None


def _build(monkeypatch, capsys, prog: Path, out: Path) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["tpyc", str(prog), "--build", "--debug", "-o", str(out)])
    rc = main_tpyc()
    return rc, capsys.readouterr().err


@pytest.mark.skipif(not _toolchain(), reason="no C++ toolchain")
def test_the_warning_is_printed_and_replayed_by_a_warm_build(tmp_path, monkeypatch, capsys, request) -> None:
    # The option is registered by tests/conftest.py, absent from a `pytest tpyc/` run.
    if request.config.getoption("--no-exec", default=False):
        pytest.skip("a --no-exec run builds nothing")
    prog = tmp_path / "prog.py"
    prog.write_text(_PROG)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    rc, cold = _build(monkeypatch, capsys, prog, out)
    assert rc == 0 and cold.count(_WARNING) == 1 and cold.count(_HAZARD) == 1, cold
    rc, warm = _build(monkeypatch, capsys, prog, out)
    assert rc == 0 and warm.count(_WARNING) == 1 and warm.count(_HAZARD) == 1, warm


def test_a_compile_without_a_build_prints_the_warning_once(tmp_path, monkeypatch, capsys) -> None:
    prog = tmp_path / "prog.py"
    prog.write_text(_PROG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["tpyc", str(prog), "-o", str(tmp_path / "out")])
    assert main_tpyc() == 0
    err = capsys.readouterr().err
    assert err.count(_WARNING) == 1 and err.count(_HAZARD) == 1, err
