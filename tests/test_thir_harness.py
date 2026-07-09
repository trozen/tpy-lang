"""Regression tests for the THIR per-case migration harness helpers."""

from pathlib import Path

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
