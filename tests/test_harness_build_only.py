"""Unit tests for the pure per-case phase-decision gates: the exec-phase
decision (conftest.plan_exec_phase), the exec-flag conflict gate
(conftest._exec_flag_conflict), and the CPython-parity gate
(conftest.cpy_phase_applicable)."""

import itertools

import conftest


def test_plan_disabled_outranks_everything() -> None:
    for force, expected, hit in itertools.product((False, True), repeat=3):
        assert conftest.plan_exec_phase(
            no_exec=True, build_only=False, force_exec=force,
            expected_exists=expected, marker_hit=hit) == "disabled"


def test_plan_build_only_ignores_markers_and_force() -> None:
    # A marker hit must NOT skip the build, and --force-exec is redundant:
    # build-only always builds, exactly once per case.
    for force, expected, hit in itertools.product((False, True), repeat=3):
        assert conftest.plan_exec_phase(
            no_exec=False, build_only=True, force_exec=force,
            expected_exists=expected, marker_hit=hit) == "build"


def test_plan_normal_skip_and_run() -> None:
    # Skip requires ALL of: no force, expected file present, marker hit.
    assert conftest.plan_exec_phase(
        no_exec=False, build_only=False, force_exec=False,
        expected_exists=True, marker_hit=True) == "skipped"
    for force, expected, hit in itertools.product((False, True), repeat=3):
        if not force and expected and hit:
            continue
        assert conftest.plan_exec_phase(
            no_exec=False, build_only=False, force_exec=force,
            expected_exists=expected, marker_hit=hit) == "build+run"


def test_exec_flag_conflicts() -> None:
    conflict = conftest._exec_flag_conflict
    # Pre-existing rule: --no-exec vs anything that forces builds/updates.
    assert conflict(no_exec=True, build_only=False, force_exec=True,
                    clean=False, updating=False)
    # --build-only vs --no-exec and vs snapshot regeneration.
    assert "--no-exec" in conflict(no_exec=True, build_only=True,
                                   force_exec=False, clean=False,
                                   updating=False)
    assert "--update-snapshots" in conflict(no_exec=False, build_only=True,
                                            force_exec=False, clean=False,
                                            updating=True)
    # Allowed combinations: build-only alone, with --force-exec (redundant),
    # and with --clean (wipe caches, then build).
    assert conflict(no_exec=False, build_only=True, force_exec=True,
                    clean=True, updating=False) is None
    assert conflict(no_exec=False, build_only=False, force_exec=False,
                    clean=False, updating=False) is None


def test_cpy_phase_applicable() -> None:
    applicable = conftest.cpy_phase_applicable
    # cpy runs only when NONE of the blockers apply.
    base = dict(build_only=False, no_cpy=False, is_panic=False,
                no_cpython_marker=False, output_exists=True)
    assert applicable(**base) is True
    # Each blocker independently flips it off.
    assert applicable(**{**base, "build_only": True}) is False
    assert applicable(**{**base, "no_cpy": True}) is False
    assert applicable(**{**base, "is_panic": True}) is False
    assert applicable(**{**base, "no_cpython_marker": True}) is False
    assert applicable(**{**base, "output_exists": False}) is False
