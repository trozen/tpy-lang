"""Unit tests for the per-face witness tally (faces.py): the helper records
on the active compiler, no-ops without one, rejects unregistered names, and
an end-to-end lowering run produces only registered witnesses."""

from __future__ import annotations

import pytest

from ..compilation_context import _current_compiler, activate_compiler
from .faces import THIR_FACES, witness
from .fallback import begin_attempt, commit_attempt, fold_attempt
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


class TestWitnessRollback:
    """A body that FALLS BACK emits its whole tree through the AST path, so the
    arms it reached during the failed attempt cover nothing. Without rollback an
    arm that witnesses before it can raise reads as covered forever -- the
    blind spot that let a dead arm pass this check."""

    def test_fallback_undoes_the_attempt_s_witnesses(self):
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            begin_attempt()
            witness("self.this")
            witness("self.this")
            witness("optptr.none")
            fold_attempt("body")
        assert compiler._thir_face_witnesses == {}

    def test_routed_body_keeps_its_witnesses(self):
        # The success side: no fold_attempt, so the journal is simply
        # superseded by the next attempt and the counts stand.
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            begin_attempt()
            witness("self.this")
            begin_attempt()
            witness("optptr.none")
            fold_attempt("body")
        assert compiler._thir_face_witnesses == {"self.this": 1}

    def test_rollback_leaves_earlier_bodies_alone(self):
        # Only the failing attempt's share is subtracted -- a face witnessed by
        # a routed body AND a fallback body stays witnessed.
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            begin_attempt()
            witness("self.this")
            begin_attempt()
            witness("self.this")
            witness("self.this")
            fold_attempt("body")
        assert compiler._thir_face_witnesses == {"self.this": 1}

    def test_fold_with_no_journal_open_is_a_hard_error(self):
        # The enforcement the 6-site begin/commit-or-fold convention would
        # otherwise lack: a fold whose begin is missing would subtract
        # whatever ran last. Loud beats a silent mis-attribution, so a second
        # fold without a new attempt raises rather than quietly no-opping.
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            begin_attempt()
            witness("self.this")
            fold_attempt("body")
            with pytest.raises(AssertionError, match="no journal open"):
                fold_attempt("ctor")
        assert compiler._thir_face_witnesses == {}

    def test_witness_outside_any_attempt_is_never_rolled_back(self):
        # THE EMIT WINDOW. Resumable bodies lower DURING emit, so a routed
        # body's emit-time witnesses (thir/emit.py records ~27 faces) are
        # interleaved with later attempts. They belong to code that shipped
        # and must survive a neighbouring fallback.
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            witness("optptr.none")          # no attempt open
            begin_attempt()
            witness("self.this")
            fold_attempt("body")
        assert compiler._thir_face_witnesses == {"optptr.none": 1}

    def test_commit_closes_the_window_so_later_emit_witnesses_survive(self):
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            begin_attempt()
            witness("self.this")
            commit_attempt()                # body routed
            witness("optptr.none")           # its emit, journal now closed
            begin_attempt()
            witness("call.self_method")
            fold_attempt("resumable")
        assert compiler._thir_face_witnesses == {
            "self.this": 1, "optptr.none": 1}
