"""Unit tests for the per-face witness tally (faces.py): the helper records
on the active compiler, no-ops without one, rejects unregistered names, and
an end-to-end lowering run produces only registered witnesses."""

from __future__ import annotations

import pytest

from ..compilation_context import _current_compiler, activate_compiler
from .faces import THIR_FACES, witness
from ..codegen_cpp.context import ThirRejectError
from .reject import begin_attempt, commit_attempt, reject_attempt
from .testutil import _compile, _entry


def _reject(component: str) -> None:
    """Close an attempt on the reject side. `reject_attempt` raises the
    user-facing diagnostic; these units are about the witness journal it
    rolls back on the way out, not the message."""
    with pytest.raises(ThirRejectError):
        reject_attempt(component)

_SELF_SRC = (
    "from tpy import int32\n"
    "class A:\n"
    "    n: int32\n"
    "    def __init__(self, n: int32):\n"
    "        self.n = n\n"
    "    def helper(self) -> int32:\n"
    "        return self.n\n"
    "    def run(self) -> int32:\n"
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
    # Through the codegen entry, so the witnesses are the ones a BUILD
    # records rather than a lowering shape only a test drives.
    compiler, modules = _compile(_SELF_SRC)
    compiler.generate_code_to_strings(_entry(modules))
    w = compiler._thir_face_witnesses
    assert set(w) <= THIR_FACES
    assert w.get("self.this", 0) >= 1
    assert w.get("call.self_method", 0) >= 1


class TestWitnessRollback:
    """A body that REJECTS emits nothing, so the arms it reached during the
    failed attempt cover nothing. Without rollback an arm that witnesses
    before it can raise reads as covered forever -- the blind spot that let
    a dead arm pass this check."""

    def test_reject_undoes_the_attempt_s_witnesses(self):
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            begin_attempt()
            witness("self.this")
            witness("self.this")
            witness("optptr.none")
            _reject("body")
        assert compiler._thir_face_witnesses == {}

    def test_routed_body_keeps_its_witnesses(self):
        # The success side: no reject, so the journal is simply superseded
        # by the next attempt and the counts stand.
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            begin_attempt()
            witness("self.this")
            begin_attempt()
            witness("optptr.none")
            _reject("body")
        assert compiler._thir_face_witnesses == {"self.this": 1}

    def test_rollback_leaves_earlier_bodies_alone(self):
        # Only the failing attempt's share is subtracted -- a face witnessed by
        # a routed body AND a rejected body stays witnessed.
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            begin_attempt()
            witness("self.this")
            begin_attempt()
            witness("self.this")
            witness("self.this")
            _reject("body")
        assert compiler._thir_face_witnesses == {"self.this": 1}

    def test_reject_with_no_journal_open_is_a_hard_error(self):
        # The enforcement the 6-site begin/commit-or-fold convention would
        # otherwise lack: a reject whose begin is missing would subtract
        # whatever ran last. Loud beats a silent mis-attribution, so a second
        # reject without a new attempt raises rather than quietly no-opping.
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            begin_attempt()
            witness("self.this")
            _reject("body")
            with pytest.raises(AssertionError, match="no journal open"):
                reject_attempt("ctor")
        assert compiler._thir_face_witnesses == {}

    def test_witness_outside_any_attempt_is_never_rolled_back(self):
        # THE EMIT WINDOW. Resumable bodies lower DURING emit, so a routed
        # body's emit-time witnesses (thir/emit.py records ~27 faces) are
        # interleaved with later attempts. They belong to code that shipped
        # and must survive a neighbouring reject.
        compiler, _ = _compile("def f() -> None:\n    pass\n")
        with activate_compiler(compiler):
            witness("optptr.none")          # no attempt open
            begin_attempt()
            witness("self.this")
            _reject("body")
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
            _reject("resumable")
        assert compiler._thir_face_witnesses == {
            "self.this": 1, "optptr.none": 1}
