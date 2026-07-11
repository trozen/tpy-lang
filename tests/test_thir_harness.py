"""Regression tests for the THIR per-case migration harness helpers."""

import contextlib
import copy
from pathlib import Path

import pytest

import conftest


def test_apply_no_thir_marker(tmp_path: Path) -> None:
    """--thir-classify's marker writer (`_apply_no_thir_marker`): add a
    no_thir.txt for a dirty (has-fallback) case, remove it for a clean one,
    idempotent both ways -- a bug here would mis-mark/unmark cases repo-wide the
    next time --thir-classify runs."""
    marker = tmp_path / "no_thir.txt"

    conftest._apply_no_thir_marker(tmp_path, dirty=True)
    assert marker.exists()

    conftest._apply_no_thir_marker(tmp_path, dirty=True)  # idempotent add
    assert marker.exists()

    conftest._apply_no_thir_marker(tmp_path, dirty=False)
    assert not marker.exists()

    conftest._apply_no_thir_marker(tmp_path, dirty=False)  # idempotent remove
    assert not marker.exists()


class _StubConfig:
    """Minimal config.getoption stand-in: unset flags read False."""

    def __init__(self, opts: dict) -> None:
        self._opts = opts

    def getoption(self, name: str):
        return self._opts.get(name, False)


def test_thir_flag_conflict() -> None:
    """The mutually-exclusive THIR flag guard: --no-thir (disable) can't pair
    with the force-on flags, and none of the force-on flags can pair with
    --update-snapshots (snapshots must be AST-authored). Anything else is fine."""
    conflict = conftest._thir_flag_conflict

    # --no-thir vs each force-on flag -> conflict.
    for forcing in ("--thir-codegen", "--thir-classify", "--thir-check-flip"):
        assert conflict(_StubConfig({"--no-thir": True, forcing: True}),
                        updating=False) is not None

    # force-on vs --update-snapshots (updating=True) -> conflict.
    assert conflict(_StubConfig({"--thir-codegen": True}), updating=True) is not None

    # Non-conflicting combinations -> None.
    assert conflict(_StubConfig({}), updating=False) is None          # default
    assert conflict(_StubConfig({"--no-thir": True}), updating=False) is None
    assert conflict(_StubConfig({"--thir-codegen": True}), updating=False) is None
    assert conflict(_StubConfig({"--no-thir": True}), updating=True) is None


def _mode(**kw):
    base = dict(thir_codegen=True, no_thir=False, ignore_markers=False,
                classify=False, check_flip=False)
    return conftest._thir_case_mode(**{**base, **kw})


def test_thir_case_mode_overlay_runs_for_marked_cases() -> None:
    """The overlay decision is INDEPENDENT of no_thir.txt. The marker is
    per-case but fallback is per-body, so a marked case still routes bodies that
    must be byte-diffed -- a fallback emits byte-identical AST, so nothing else
    would catch them regressing to AST."""
    assert _mode(no_thir=False)[0] is True
    assert _mode(no_thir=True)[0] is True
    # ...and the ratchet is the half that DOES respect the marker.
    assert _mode(no_thir=False)[1] is True
    assert _mode(no_thir=True)[1] is False


def test_thir_case_mode_off_disables_both() -> None:
    """--no-thir / --update-snapshots (thir_codegen=False): no overlay, no
    ratchet, marked or not."""
    for marked in (False, True):
        assert _mode(thir_codegen=False, no_thir=marked) == (False, False)


def test_thir_case_mode_whole_corpus_and_writers_suppress_ratchet() -> None:
    """--thir-codegen / -classify / -check-flip keep the overlay but drop the
    ratchet: they consume the raw fallback count (to measure, mark, or list
    flip candidates), so an unmarked case falling back must not fail comp."""
    assert _mode(ignore_markers=True) == (True, False)
    assert _mode(ignore_markers=True, classify=True) == (True, False)
    assert _mode(ignore_markers=True, check_flip=True) == (True, False)
    # Guard the flags independently of ignore_markers, which they happen to
    # imply today -- the ratchet must not resurrect if that coupling changes.
    assert _mode(classify=True) == (True, False)
    assert _mode(check_flip=True) == (True, False)


_TALLIES = ("_thir_tally", "_thir_cases", "_thir_faces", "_thir_fallback",
            "_thir_arm_residual", "_thir_shapes", "_thir_flip")


@contextlib.contextmanager
def _isolated_tallies():
    """Compiling a fixture case feeds the run-wide THIR tallies. Restore them,
    or these tests would inflate the very dial + coverage metrics they guard."""
    saved = {n: copy.deepcopy(getattr(conftest, n)) for n in _TALLIES}
    try:
        yield
    finally:
        for name, value in saved.items():
            getattr(conftest, name).clear()
            if isinstance(value, list):
                getattr(conftest, name).extend(value)
            else:
                getattr(conftest, name).update(value)


def _compile_fixture_case(tmp_path: Path, marked: bool):
    case = tmp_path / "case"
    (case / "src").mkdir(parents=True)
    (case / "src" / "main.py").write_text(
        "def f(x: int) -> int:\n    return x + 1\n\n\nprint(f(1))\n")
    if marked:
        (case / "no_thir.txt").write_text("marked\n")
    with _isolated_tallies():
        return conftest.compile_with_diagnostics(
            case / "src" / "main.py", tmp_path / "out")


def _require_thir():
    if not conftest.TEST_CODEGEN_OPTIONS.thir_codegen:
        pytest.skip("THIR off (--no-thir / --update-snapshots)")


def test_marked_case_still_gets_an_overlay(tmp_path: Path) -> None:
    """The integration half of `_thir_case_mode`: a no_thir-marked case must
    STILL regenerate its user modules through THIR, because the marker is
    per-case while fallback is per-body.

    Nothing else can catch a regression here. Re-gating the overlay on the
    marker leaves the whole corpus green -- a marked case byte-diffs identically
    whether or not the overlay ran, since the overlay only ADDS a check. So a
    green suite is not evidence; this assertion is."""
    _require_thir()
    result = _compile_fixture_case(tmp_path, marked=True)
    assert result.success, result.diagnostics
    assert result.thir_modules, "marked case got no THIR overlay"
    # Depends on the fixture body staying routable; if THIR ever stops routing
    # `return x + 1`, this fires on the routing, not on the overlay.
    assert result.thir_routed_names, "marked case recorded no routed bodies"
    if not conftest.THIR_IGNORE_MARKERS:
        assert result.thir_ratchet_fell is None, "ratchet must skip a marked case"


def test_unmarked_case_arms_the_ratchet(tmp_path: Path) -> None:
    """The complement: an unmarked case is governed by the ratchet, so the
    harness must surface its fallback count (0 for this clean body) rather than
    None. Pins that the ratchet stays wired to the marker after the split."""
    _require_thir()
    if (conftest.THIR_IGNORE_MARKERS or conftest.THIR_CLASSIFY_WRITE
            or conftest.THIR_CHECK_FLIP):
        pytest.skip("ratchet suppressed by the marker-ignoring flags")
    result = _compile_fixture_case(tmp_path, marked=False)
    assert result.success, result.diagnostics
    assert result.thir_modules, "unmarked case got no THIR overlay"
    assert result.thir_ratchet_fell == 0, "ratchet not armed for an unmarked case"
