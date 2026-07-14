"""Unit tests for the no_macos.txt marker: target-OS probing, marker
read/apply, and the exec-flag conflict gate."""

from pathlib import Path

import conftest
from tpyc.toolchain import _triple_to_os, compiler_target_os


def test_triple_to_os() -> None:
    assert _triple_to_os("arm64-apple-darwin24") == "darwin"
    assert _triple_to_os("aarch64-apple-macos14") == "darwin"
    assert _triple_to_os("x86_64-linux-gnu") == "linux"
    assert _triple_to_os("x86_64-pc-windows-msvc") == "windows"
    assert _triple_to_os("x86_64-w64-mingw32") == "windows"
    assert _triple_to_os("wasm32-unknown-unknown") == "unknown"


def test_compiler_target_os_probe(tmp_path: Path) -> None:
    """A fake compiler that answers -dumpmachine with a darwin triple is
    detected as targeting darwin; a broken one falls back to the host."""
    fake = tmp_path / "oa64-clang++"
    fake.write_text("#!/bin/sh\necho arm64-apple-darwin24\n")
    fake.chmod(0o755)
    assert compiler_target_os((str(fake),)) == "darwin"

    broken = tmp_path / "weird-cc"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    assert compiler_target_os((str(broken),)) == conftest.host_os()

    # A recognized probe answering an unmapped triple also falls back to
    # the host (a target we can't classify is assumed native).
    exotic = tmp_path / "wasm-cc"
    exotic.write_text("#!/bin/sh\necho wasm32-unknown-unknown\n")
    exotic.chmod(0o755)
    assert compiler_target_os((str(exotic),)) == conftest.host_os()


def test_macos_classify_flag_conflicts() -> None:
    conflict = conftest._exec_flag_conflict
    base = dict(build_only=False, force_exec=False, clean=False)
    assert "--no-exec" in conflict(no_exec=True, updating=False,
                                   macos_classify=True, **base)
    assert "--update-snapshots" in conflict(no_exec=False, updating=True,
                                            macos_classify=True, **base)
    assert conflict(no_exec=False, updating=False, macos_classify=True,
                    **base) is None


def test_read_marker(tmp_path: Path) -> None:
    assert conftest.read_macos_marker(tmp_path) is None
    (tmp_path / conftest.MACOS_MARKER).write_text("")
    assert conftest.read_macos_marker(tmp_path) == ""
    (tmp_path / conftest.MACOS_MARKER).write_text(" epoll companion \n")
    assert conftest.read_macos_marker(tmp_path) == "epoll companion"


def test_apply_marker_add_remove_idempotent(tmp_path: Path) -> None:
    """Mirrors _apply_no_thir_marker's contract; additionally an existing
    marker's hand-written reason survives while the case still fails."""
    marker = tmp_path / conftest.MACOS_MARKER

    conftest.apply_macos_marker(tmp_path, expect_fail=True)
    assert marker.exists()
    conftest.apply_macos_marker(tmp_path, expect_fail=True)  # idempotent
    assert marker.exists()

    marker.write_text("hand-written reason\n")
    conftest.apply_macos_marker(tmp_path, expect_fail=True)
    assert marker.read_text() == "hand-written reason\n"  # not clobbered

    conftest.apply_macos_marker(tmp_path, expect_fail=False)
    assert not marker.exists()
    conftest.apply_macos_marker(tmp_path, expect_fail=False)  # idempotent
    assert not marker.exists()
