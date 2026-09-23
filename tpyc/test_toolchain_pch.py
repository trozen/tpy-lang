"""Regression guards for the PCH builder.

Toolchain-free: captures the argv `get_or_build_pch` would run instead of
compiling, so it runs on any host (the gcc-only CI box included). Two rules
under test: clang gets -fno-pch-timestamp (it rejects a content-identical
.gch whose input header mtime changed) while GCC, validating by content,
must not; and a .gch is consumed only from the include root, compiler, -std
and flags stamped next to it, since the per-program pch/ dir outlives the
checkout and toolchain that built it.
"""

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from tpyc import repl_backends, toolchain
from tpyc.toolchain import (
    CppCompilerConfig, get_or_build_pch, pch_is_current, pch_is_path_sensitive,
)


def _capture_pch_cmd(monkeypatch, family, tmp_path):
    captured = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(toolchain, "_detect_compiler_family", lambda cxx: family)

    config = CppCompilerConfig(compiler=["clang++" if family == "clang" else "g++"])
    get_or_build_pch(config, tmp_path, opt_flags=[], pch_dir=tmp_path / "pch")
    return captured["cmd"]


def test_clang_pch_disables_timestamp(monkeypatch, tmp_path):
    cmd = _capture_pch_cmd(monkeypatch, "clang", tmp_path)
    # Both tokens, and in order, so the cc1 flag reaches the frontend.
    i = cmd.index("-Xclang")
    assert cmd[i + 1] == "-fno-pch-timestamp"


def test_gcc_pch_keeps_default_validation(monkeypatch, tmp_path):
    cmd = _capture_pch_cmd(monkeypatch, "gcc", tmp_path)
    assert "-fno-pch-timestamp" not in cmd
    assert "-Xclang" not in cmd


def _family(monkeypatch, family):
    monkeypatch.setattr(toolchain, "_detect_compiler_family", lambda cxx: family)
    return CppCompilerConfig(compiler=["cxx"])


def test_clang_pch_is_path_sensitive(monkeypatch):
    assert pch_is_path_sensitive(_family(monkeypatch, "clang"))


def test_gcc_pch_is_path_agnostic(monkeypatch):
    assert not pch_is_path_sensitive(_family(monkeypatch, "gcc"))


def test_unknown_family_is_treated_as_path_sensitive(monkeypatch):
    # Fail-safe: an unrecognized compiler gets a per-checkout PCH rather than
    # a shared one that may or may not survive a different -I root.
    assert pch_is_path_sensitive(_family(monkeypatch, "unknown"))


def _builder(monkeypatch, tmp_path):
    """A fake toolchain that records each PCH build and produces the .gch."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"gch")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(toolchain, "_detect_compiler_family", lambda cxx: "gcc")
    monkeypatch.setattr(toolchain, "compiler_binary_identity",
                        lambda cxx: ("/usr/bin/" + cxx[0], 1, 1))
    root_a = tmp_path / "checkout-a" / "include"
    root_b = tmp_path / "checkout-b" / "include"
    for root in (root_a, root_b):
        (root / "tpy").mkdir(parents=True)
        (root / "tpy" / "tpy.hpp").write_text("// runtime\n")
    pch_dir = tmp_path / "prog" / "pch"
    return calls, root_a, root_b, pch_dir


def test_same_configuration_reuses_pch(monkeypatch, tmp_path):
    calls, root_a, _, pch_dir = _builder(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["g++"])
    assert get_or_build_pch(config, root_a, ["-O2"], pch_dir) is not None
    assert get_or_build_pch(config, root_a, ["-O2"], pch_dir) is not None
    assert len(calls) == 1
    assert pch_is_current(config, root_a, ["-O2"], pch_dir)


def test_other_include_root_rebuilds_pch(monkeypatch, tmp_path):
    calls, root_a, root_b, pch_dir = _builder(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["g++"])
    get_or_build_pch(config, root_a, [], pch_dir)
    # B's headers are older than A's .gch, so only the stamp can tell them apart.
    old = time.time() - 3600
    os.utime(root_b / "tpy" / "tpy.hpp", (old, old))
    get_or_build_pch(config, root_b, [], pch_dir)
    assert len(calls) == 2
    assert str(root_b) in " ".join(calls[1])


def test_other_flags_or_compiler_rebuild_pch(monkeypatch, tmp_path):
    calls, root_a, _, pch_dir = _builder(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["g++"])
    get_or_build_pch(config, root_a, [], pch_dir)
    get_or_build_pch(config, root_a, ["-O2"], pch_dir)
    get_or_build_pch(CppCompilerConfig(compiler=["g++-14"]), root_a, ["-O2"], pch_dir)
    get_or_build_pch(CppCompilerConfig(compiler=["g++-14"], std="c++26"),
                     root_a, ["-O2"], pch_dir)
    get_or_build_pch(CppCompilerConfig(compiler=["g++-14"], std="c++26",
                                       extra_flags=["-DX"]),
                     root_a, ["-O2"], pch_dir)
    get_or_build_pch(CppCompilerConfig(compiler=["g++-14"], std="c++26",
                                       extra_flags=["-DX"], warn_flags=["-Wall"]),
                     root_a, ["-O2"], pch_dir)
    assert len(calls) == 6


def test_replaced_compiler_binary_rebuilds_pch(monkeypatch, tmp_path):
    calls, root_a, _, pch_dir = _builder(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["g++"])
    get_or_build_pch(config, root_a, [], pch_dir)
    monkeypatch.setattr(toolchain, "compiler_binary_identity",
                        lambda cxx: ("/usr/bin/g++", 2, 2))
    get_or_build_pch(config, root_a, [], pch_dir)
    assert len(calls) == 2


def test_force_rebuilds_current_pch(monkeypatch, tmp_path):
    calls, root_a, _, pch_dir = _builder(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["g++"])
    get_or_build_pch(config, root_a, [], pch_dir)
    get_or_build_pch(config, root_a, [], pch_dir, force=True)
    assert len(calls) == 2


def test_unstamped_pch_is_rebuilt(monkeypatch, tmp_path):
    """A .gch from before the stamp existed carries no provenance."""
    calls, root_a, _, pch_dir = _builder(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["g++"])
    get_or_build_pch(config, root_a, [], pch_dir)
    (pch_dir / "tpy_pch.stamp").unlink()
    assert not pch_is_current(config, root_a, [], pch_dir)
    get_or_build_pch(config, root_a, [], pch_dir)
    assert len(calls) == 2


def test_missing_pch_header_is_rebuilt(monkeypatch, tmp_path):
    """The .hpp is what -include names; a .gch without it is unusable."""
    calls, root_a, _, pch_dir = _builder(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["g++"])
    get_or_build_pch(config, root_a, [], pch_dir)
    (pch_dir / "tpy_pch.hpp").unlink()
    assert not pch_is_current(config, root_a, [], pch_dir)
    get_or_build_pch(config, root_a, [], pch_dir)
    assert len(calls) == 2


def test_edited_header_still_rebuilds_pch(monkeypatch, tmp_path):
    calls, root_a, _, pch_dir = _builder(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["g++"])
    get_or_build_pch(config, root_a, [], pch_dir)
    gch_mtime = (pch_dir / "tpy_pch.hpp.gch").stat().st_mtime
    hdr = root_a / "tpy" / "tpy.hpp"
    hdr.write_text("// edited\n")
    os.utime(hdr, (gch_mtime + 10, gch_mtime + 10))
    get_or_build_pch(config, root_a, [], pch_dir)
    assert len(calls) == 2


def test_failed_build_leaves_no_stamp(monkeypatch, tmp_path):
    calls, root_a, _, pch_dir = _builder(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["g++"])
    get_or_build_pch(config, root_a, [], pch_dir)

    def failing_run(cmd, *args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", failing_run)
    assert get_or_build_pch(config, root_a, ["-O3"], pch_dir) is None
    assert not (pch_dir / "tpy_pch.stamp").exists()
    assert not pch_is_current(config, root_a, [], pch_dir)


def _repl_backend(monkeypatch, tmp_path):
    """A CompileBackend whose PCH cache dir and runtime root are under tmp_path."""
    calls, root_a, _, pch_dir = _builder(monkeypatch, tmp_path)
    runtime_dir = root_a.parent.parent
    (runtime_dir / "cpp").mkdir()
    root_a.rename(runtime_dir / "cpp" / "include")
    monkeypatch.setattr(repl_backends, "get_runtime_dir", lambda: runtime_dir)
    (tmp_path / "repl").mkdir()
    backend = repl_backends.CompileBackend(["g++"], tmp_path / "repl", "main")
    monkeypatch.setattr(backend, "_get_pch_cache_dir", lambda: pch_dir)
    return backend, calls, pch_dir


def test_repl_pch_round_trip(monkeypatch, tmp_path):
    backend, calls, pch_dir = _repl_backend(monkeypatch, tmp_path)
    assert backend._pch_is_stale()
    backend._pch_needs_build = True
    backend._setup_pch()
    assert backend._pch_path == pch_dir / "tpy_pch.hpp"
    assert backend._pch_build_time is not None and backend._pch_build_time >= 0
    assert len(calls) == 1
    # The REPL's own opt flags reach the build; a stale check afterwards
    # sees the stamped build and reuses it.
    assert all(f in calls[0] for f in backend._OPT_FLAGS)
    assert (pch_dir / "tpy_pch.stamp").exists()
    assert not backend._pch_is_stale()


def test_repl_pch_from_other_checkout_is_stale(monkeypatch, tmp_path):
    """The REPL consults the stamp, not just header mtimes: a .gch another
    include root built (with older headers) must not be reused."""
    backend, calls, pch_dir = _repl_backend(monkeypatch, tmp_path)
    foreign = tmp_path / "other" / "include"
    (foreign / "tpy").mkdir(parents=True)
    (foreign / "tpy" / "tpy.hpp").write_text("// other\n")
    get_or_build_pch(backend._config, foreign, list(backend._OPT_FLAGS), pch_dir)
    old = time.time() - 3600
    for h in (repl_backends.get_runtime_dir() / "cpp" / "include").rglob("*.hpp"):
        os.utime(h, (old, old))
    assert backend._pch_is_stale()


def test_repl_pch_build_failure_is_signalled(monkeypatch, tmp_path):
    backend, _, _ = _repl_backend(monkeypatch, tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="boom"))
    backend._pch_needs_build = True
    backend._setup_pch()
    assert backend._pch_path is None
    assert backend._pch_build_time == -1.0
