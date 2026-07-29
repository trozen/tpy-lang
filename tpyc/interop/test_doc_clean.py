"""`cpython_clean_doc` against CPython's own compiler.

Every expectation here was measured on CPython 3.14.5 by compiling a
function whose body is the raw literal and reading its `__doc__`; the
same literals under 3.12 come back unchanged (the dedent landed in 3.13).
The pairs are the oracle -- tpyc must produce the same text on either
interpreter, so the test cannot ask the running one.

A measured table cannot notice CPython changing the algorithm under it,
so the last test re-derives the expectations from the interpreter when
it is new enough to dedent -- the table itself stays the oracle for what
tpyc emits, on every host.
"""
import sys

import pytest

from .export_shape import cpython_clean_doc

# (raw literal, __doc__ as CPython 3.13+ reports it)
MEASURED = [
    # No continuation lines to dedent.
    ("Single line.", "Single line."),
    ("", ""),
    ("\n", "\n"),
    # The first line is always lstripped, whatever the common indent is.
    ("   just one line   ", "just one line   "),
    ("   first\nzero\n", "first\nzero\n"),
    ("\tfirst\n    body\n    ", "first\nbody\n"),
    ("      first line deep\n    body\n    ", "first line deep\nbody\n"),
    # Common indent comes off the rest; the closing-quote line goes with it.
    ("First line.\n\n        Deeper.\n      Shallower.\n    Base.\n    ",
     "First line.\n\n    Deeper.\n  Shallower.\nBase.\n"),
    ("\n    Starts on line two.\n      Deeper.\n    ",
     "\nStarts on line two.\n  Deeper.\n"),
    ("head\n    body\n", "head\nbody\n"),
    # Tabs count to the next multiple of 8 and come back as spaces.
    ("Tabs.\n\t\tTabbed twice.\n\t Tab then space.\n    ",
     "Tabs.\n       Tabbed twice.\nTab then space.\n"),
    ("head\n\tone tab\n\t\ttwo tabs\n\t", "head\none tab\n        two tabs\n"),
    ("head\n \tsptab\n\t sptab2\n  ", "head\nsptab\n sptab2\n"),
    # Whitespace-only lines never set the indent, but are dedented by it.
    ("head\n    body\n        \n    tail\n    ", "head\nbody\n    \ntail\n"),
    ("head\n  \n    body\n    ", "head\n\nbody\n"),
    ("head\n    \n      \n", "head\n    \n      \n"),
    ("head\n    body\n\n    tail\n    ", "head\nbody\n\ntail\n"),
    # A line at column 0 pins the indent there: nothing after the first moves.
    ("No continuation indent.\nnext line at col 0.\n    indented line.\n    ",
     "No continuation indent.\nnext line at col 0.\n    indented line.\n    "),
    # '\f' and '\v' are content, not indentation -- so they pin it at 0 too.
    ("head\n\x0c   ff line\n    body\n    ",
     "head\n\x0c   ff line\n    body\n    "),
    ("head\n\x0b   vt line\n    body\n    ",
     "head\n\x0b   vt line\n    body\n    "),
    ("\x0c first\n    body\n    ", "\x0c first\nbody\n"),
    # Trailing whitespace on a content line survives.
    ("head\n    body with trail   \n    ", "head\nbody with trail   \n"),
]


@pytest.mark.parametrize("raw,expected", MEASURED)
def test_matches_cpython(raw, expected):
    assert cpython_clean_doc(raw) == expected


@pytest.mark.parametrize("raw,expected", MEASURED)
def test_idempotent(raw, expected):
    assert cpython_clean_doc(expected) == expected


@pytest.mark.skipif(sys.version_info < (3, 13),
                    reason="the compile-time dedent landed in CPython 3.13")
@pytest.mark.parametrize("raw,expected", MEASURED)
def test_table_still_matches_the_interpreter(raw, expected):
    """Guards the measurements against a future CPython changing the dedent:
    the table would keep passing while tpyc silently drifted from the
    language. The literal is spelled via repr() -- the dedent runs on the
    string VALUE, so an escaped one-line literal and a physical multi-line
    one produce the same `__doc__`, and repr() survives any content."""
    ns: dict = {}
    exec(compile("def _f():\n    %s\n" % (repr(raw),), "<doc>", "exec"), ns)
    assert ns["_f"].__doc__ == expected
