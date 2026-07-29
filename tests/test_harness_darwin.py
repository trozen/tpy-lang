"""Unit tests for darwin toolchain detection: target-OS probing and the
darwin-specific command adjustments (deployment-target pin, Mach-O ld-path,
cross-prefixed C-driver derivation) the harness relies on for macOS builds."""

import os
import shutil
from pathlib import Path

import conftest
from tpyc.toolchain import (
    MACOS_VERSION_MIN,
    CppCompilerConfig,
    _derive_c_compiler,
    _triple_to_os,
    compiler_target_os,
    darwin_cross_ld_flags,
    darwin_version_min_flags,
    shared_link_flags,
)


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


def _fake_compiler(tmp_path: Path, name: str, triple: str) -> Path:
    fake = tmp_path / name
    fake.write_text(f"#!/bin/sh\necho {triple}\n")
    fake.chmod(0o755)
    return fake


def test_shared_link_flags(tmp_path: Path) -> None:
    """The extension link recipe is per-TARGET, not per-host: Mach-O refuses
    to leave the facade's `Py*` symbols undefined under plain `-shared` (the
    link fails outright), so a darwin target gets the bundle recipe CPython's
    own LDSHARED uses, while ELF keeps `-shared`.

    Keying on the target is what makes a native mac build and an osxcross
    cross-build emit the same line -- a host-keyed check would silently give
    the cross row the wrong recipe."""
    darwin = _fake_compiler(tmp_path, "sl-clang++", "arm64-apple-darwin25.5")
    assert shared_link_flags([str(darwin)]) == [
        "-bundle", "-undefined", "dynamic_lookup"]

    linux = _fake_compiler(tmp_path, "sl-g++", "x86_64-linux-gnu")
    assert shared_link_flags([str(linux)]) == ["-shared"]


def test_darwin_version_min_flags(tmp_path: Path) -> None:
    """Darwin-targeting compilers get the deployment-target pin; native
    ones don't; an existing pin (either spelling) is never duplicated."""
    pin = f"-mmacosx-version-min={MACOS_VERSION_MIN}"
    darwin = _fake_compiler(tmp_path, "oa64-clang++", "arm64-apple-darwin25.5")
    assert darwin_version_min_flags([str(darwin)]) == [pin]

    linux = _fake_compiler(tmp_path, "lx-g++", "x86_64-linux-gnu")
    assert darwin_version_min_flags([str(linux)]) == []

    assert darwin_version_min_flags([str(darwin), pin]) == []
    assert darwin_version_min_flags(
        [str(darwin), "-mmacos-version-min=14.0"]) == []


def test_darwin_cross_ld_flags(tmp_path: Path) -> None:
    """A darwin-cross driver is pointed at its sibling Mach-O ld
    (`<dir>/<triple>-ld`) so the link doesn't fall through to the host's
    ELF linker; native/linux targets and pre-selected linkers get nothing."""
    triple = "arm64-apple-darwin25.5"
    cxx = _fake_compiler(tmp_path, f"{triple}-clang++-19", triple)

    # No sibling ld yet -> no flag (bare command falls back to host ld).
    assert darwin_cross_ld_flags([str(cxx)]) == []

    ld = tmp_path / f"{triple}-ld"
    ld.write_text("#!/bin/sh\n")
    ld.chmod(0o755)
    assert darwin_cross_ld_flags([str(cxx)]) == [f"--ld-path={ld}"]

    # An explicit linker choice is never overridden.
    assert darwin_cross_ld_flags([str(cxx), "-fuse-ld=lld"]) == []
    assert darwin_cross_ld_flags([str(cxx), f"--ld-path={ld}"]) == []

    # A native (non-darwin) target gets nothing.
    linux = _fake_compiler(tmp_path, "lx2-g++", "x86_64-linux-gnu")
    assert darwin_cross_ld_flags([str(linux)]) == []


def test_darwin_cross_ld_flags_bare_name(tmp_path: Path,
                                         monkeypatch) -> None:
    """A darwin driver given as a bare (PATH-resolved) name yields no
    --ld-path: with no directory component the sibling ld can't be located,
    which is correct for a native mac (its clang finds ld64 itself)."""
    triple = "arm64-apple-darwin25.5"
    _fake_compiler(tmp_path, "od2-clang++", triple)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    assert darwin_cross_ld_flags(["od2-clang++"]) == []


def test_derive_c_compiler_bare_name_mappings(monkeypatch) -> None:
    """Bare (PATH-resolved) driver names map C++ -> C via shutil.which. The
    substring match must check clang++ before g++ (clang++ contains 'g++'),
    so clang++ resolves to clang, not gcc."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert _derive_c_compiler(["g++"]) == ["gcc"]
    assert _derive_c_compiler(["g++-14"]) == ["gcc-14"]
    assert _derive_c_compiler(["clang++"]) == ["clang"]
    assert _derive_c_compiler(["clang++-19"]) == ["clang-19"]

    # Derived C driver not on PATH -> fall back to the C++ command as-is.
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _derive_c_compiler(["g++"]) == ["g++"]


def test_from_env_pins_darwin_target(tmp_path: Path) -> None:
    darwin = _fake_compiler(tmp_path, "od-clang++", "arm64-apple-darwin25.5")
    config = CppCompilerConfig.from_env(cxx=str(darwin))
    assert config.compiler[-1] == f"-mmacosx-version-min={MACOS_VERSION_MIN}"

    linux = _fake_compiler(tmp_path, "lg-g++", "x86_64-linux-gnu")
    config = CppCompilerConfig.from_env(cxx=str(linux))
    assert config.compiler == [str(linux)]


def test_derive_c_compiler_cross_prefixed(tmp_path: Path) -> None:
    """The C driver derives from cross-prefixed names too (osxcross), and
    falls back to the C++ driver when the derived binary doesn't exist."""
    cxx = tmp_path / "arm64-apple-darwin25.5-clang++-19"
    cxx.write_text("#!/bin/sh\n")
    cxx.chmod(0o755)
    cc = tmp_path / "arm64-apple-darwin25.5-clang-19"

    # Derived C driver missing -> fall back to the C++ command as-is.
    assert _derive_c_compiler([str(cxx)]) == [str(cxx)]

    cc.write_text("#!/bin/sh\n")
    cc.chmod(0o755)
    # Trailing args (e.g. the darwin deployment pin) carry over.
    assert _derive_c_compiler([str(cxx), "-mmacosx-version-min=13.3"]) == \
        [str(cc), "-mmacosx-version-min=13.3"]
