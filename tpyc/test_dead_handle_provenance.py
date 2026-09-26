"""The element-copy warning on a dead iterator local reads the provenance of
the binding it was moved from, through aliases and unpack temps.

A unit test rather than sections of tests/cases/list/warn_ctor_iter_ref_provenance
because the quiet shapes alias an adapter that owns its temporary, and that
alias does not build yet (BUGS.md#iterator-alias-copies-position)."""

from . import get_lib_dir
from .compiler import Compiler
from .diagnostics import DiagnosticLevel

_STDLIB_DIRS = [get_lib_dir() / "tpy"]

_SOURCE = (
    "class C:\n"
    "    def __init__(self, v: int) -> None:\n"
    "        self.v = v\n"
    "\n"
    "\n"
    "def alias_live(cs: list[C]) -> int:\n"
    "    z = reversed(cs)\n"
    "    w = z\n"
    "    return len(list(w))\n"                       # line 9
    "\n"
    "\n"
    "def unpack_live(cs: list[C]) -> int:\n"
    "    p, q = reversed(cs), 1\n"
    "    return len(list(p)) + q\n"                   # line 14
    "\n"
    "\n"
    "def alias_temp() -> int:\n"
    "    z = reversed([C(1), C(2)])\n"
    "    w = z\n"
    "    return len(list(w))\n"                       # line 20
    "\n"
    "\n"
    "def unpack_temp() -> int:\n"
    "    p, q = reversed([C(1), C(2)]), 1\n"
    "    return len(list(p)) + q\n"                   # line 25
    "\n"
    "\n"
    "def alias_chain_temp() -> int:\n"
    "    z = reversed([C(1), C(2)])\n"
    "    w = z\n"
    "    v = w\n"
    "    return len(list(v))\n"                       # line 32
    "\n"
    "\n"
    "def alias_source_still_read() -> int:\n"
    "    z = reversed([C(1), C(2)])\n"
    "    w = z\n"
    "    n = len(list(w))\n"                          # line 38
    "    return n + len(list(z))\n"
)


def _copy_warning_lines() -> set[int]:
    compiler = Compiler.from_source(_SOURCE, lib_dirs=_STDLIB_DIRS)
    entry = [m for m in compiler.compile() if m.is_entry_point][0]
    return {d.loc.line for d in entry.analyzer.diagnostics
            if d.level == DiagnosticLevel.WARNING
            and "copies C elements" in d.message}


def test_alias_and_unpack_follow_the_moved_from_binding():
    lines = _copy_warning_lines()
    # Over a live list the alias and the unpack target lend its elements.
    assert {9, 14} <= lines
    # Over a temporary only the adapter holds the elements.
    assert not lines & {20, 25, 32}
    # A source still read after the alias shares the adapter: conservative.
    assert 38 in lines
