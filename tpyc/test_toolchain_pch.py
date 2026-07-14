"""Regression guard for the clang PCH timestamp flag.

Toolchain-free: captures the argv `get_or_build_pch` would run instead of
compiling, so it runs on any host (the gcc-only CI box included). Clang
rejects a content-identical cached .gch when the input header's mtime
changed; -fno-pch-timestamp drops the embedded timestamp so validation
falls to content. GCC validates by content already and must not get it.
"""

import subprocess
from types import SimpleNamespace

from tpyc import toolchain
from tpyc.toolchain import CppCompilerConfig, get_or_build_pch


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
