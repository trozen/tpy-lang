"""Regression tests for test failure reporting helpers."""

from pathlib import Path

import pytest

import conftest


def test_check_or_update_reports_unified_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismatch should surface a unified diff instead of raw expected/got blobs."""
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", False)

    expected_file = tmp_path / "output.txt"
    expected_file.write_text("line1\nline2\n")
    actual = "line1\nCHANGED\n"

    with pytest.raises(pytest.fail.Exception) as exc:
        conftest.check_or_update(actual, expected_file, "Output")

    msg = str(exc.value)
    assert "Output differs:" in msg
    assert f"--- {expected_file}" in msg
    assert "+++ actual" in msg
    assert "-line2" in msg
    assert "+CHANGED" in msg
