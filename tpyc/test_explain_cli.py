"""Tests for `tpyc --explain-send` / `--explain-sync` (tpyc/explain.py).

Drives the real path -- parse the type string through the entry module's
TypeResolver, walk, render -- via the in-process Compiler, covering the
holds branch, the chain branch, and the unresolvable-type error branch."""
from . import get_lib_dir
from .compiler import Compiler
from .explain import explain_send_sync

_STDLIB_DIRS = [get_lib_dir() / "tpy"]

_PROG = """\
from tpy import Int32, Ptr


class Order:
    handler: Ptr[Int32]

    def __init__(self, h: Ptr[Int32]) -> None:
        self.handler = h


def main() -> None:
    print("ok")


main()
"""


def _compile():
    compiler = Compiler.from_source(_PROG, lib_dirs=_STDLIB_DIRS)
    return compiler.compile(), compiler


def test_explain_send_holds(capsys):
    modules, compiler = _compile()
    rc = explain_send_sync(modules, compiler, "Int32", send=True)
    assert rc == 0
    assert capsys.readouterr().out.strip() == "Int32 is Send"


def test_explain_send_chain(capsys):
    modules, compiler = _compile()
    rc = explain_send_sync(modules, compiler, "list[Order]", send=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "list[Order] is not Send" in out
    assert "field 'handler: Ptr[Int32]' is not Send (raw pointer" in out


def test_explain_sync_mutable_container(capsys):
    modules, compiler = _compile()
    rc = explain_send_sync(modules, compiler, "list[Int32]", send=False)
    assert rc == 0
    assert "list[Int32] is not Sync (mutable container" in capsys.readouterr().out


def test_explain_unknown_type_errors(capsys):
    modules, compiler = _compile()
    rc = explain_send_sync(modules, compiler, "Nonexistent", send=True)
    assert rc == 1
    assert "cannot resolve type 'Nonexistent'" in capsys.readouterr().err
