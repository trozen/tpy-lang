"""`tpyc -vv` prints the C++ the compile writes, from the one codegen pass
that writes it: a second pass would build each resumable frame again over
the AST the first build's pre-pass rewrote, and reject the lifted awaits."""

import sys

from .cli import main_tpyc

_PROG = """\
import asyncio


async def step(n: int) -> int:
    return n


async def f() -> int:
    return (await step(1)) + (await step(2))


print(asyncio.run(f()))
"""


def test_vv_prints_the_written_sources_of_an_async_program(tmp_path, monkeypatch, capsys) -> None:
    prog = tmp_path / "prog.py"
    prog.write_text(_PROG)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["tpyc", str(prog), "-vv", "-o", str(out)])
    assert main_tpyc() == 0
    printed = capsys.readouterr().out
    [cpp] = out.rglob("prog.cpp")
    assert f"// === src/prog.cpp ===\n{cpp.read_text()}\n" in printed
