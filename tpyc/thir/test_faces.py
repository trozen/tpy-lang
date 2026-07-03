"""Unit tests for the per-face witness tally (faces.py): the helper records
on the active compiler, no-ops without one, rejects unregistered names, and
an end-to-end lowering run produces only registered witnesses."""

from __future__ import annotations

import pytest

from ..compilation_context import _current_compiler, activate_compiler
from .faces import THIR_FACES, witness
from .lower import lower_module
from .testutil import _compile, _entry

_SELF_SRC = (
    "from tpy import Int32\n"
    "class A:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n"
    "        self.n = n\n"
    "    def helper(self) -> Int32:\n"
    "        return self.n\n"
    "    def run(self) -> Int32:\n"
    "        return self.helper()\n"
)


def test_witness_records_on_active_compiler():
    compiler, _ = _compile("def f() -> None:\n    pass\n")
    with activate_compiler(compiler):
        assert witness("self.this") is True
        witness("self.this")
        witness("optptr.none")
    assert compiler._thir_face_witnesses == {"self.this": 2, "optptr.none": 1}


def test_witness_noop_without_active_compiler():
    # Outside a compilation (the default codegen path never even calls it)
    # the helper records nowhere and still returns True for gate conjunctions.
    # The autouse fixture activates a stub compiler, so clear the ContextVar
    # explicitly to exercise the truly-inactive path.
    token = _current_compiler.set(None)
    try:
        assert witness("self.this") is True
    finally:
        _current_compiler.reset(token)


def test_unregistered_face_rejected():
    with pytest.raises(AssertionError):
        witness("no.such.face")


def test_compile_without_lowering_records_nothing():
    compiler, _ = _compile(_SELF_SRC)
    assert compiler._thir_face_witnesses == {}


def test_lowering_witnesses_self_faces():
    compiler, modules = _compile(_SELF_SRC)
    entry = _entry(modules)
    with activate_compiler(compiler):
        lower_module(entry.ast, entry.analyzer)
    w = compiler._thir_face_witnesses
    assert set(w) <= THIR_FACES
    assert w.get("self.this", 0) >= 1
    assert w.get("call.self_method", 0) >= 1
