"""Regression guards for the shared PCH cache (`toolchain.shared_pch`).

Toolchain-free: a fake compiler stands in for the PCH build (it writes the
.gch and a make-style depfile), so these run on any host (the gcc-only CI box
included). The rules under test: clang gets -fno-pch-timestamp (it rejects a
content-identical .gch whose input header mtime changed) while GCC,
validating by content, must not; one entry per runtime-header content, build
command, compiler binary and -- on clang only -- include root, so every
consumer with the same flags shares one .gch; a .gch is published whole or
not at all and is rebuilt when a header outside the runtime changed.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tpyc import repl_backends, toolchain
from tpyc.toolchain import (
    PCH_DEPS, PCH_GCH, PCH_HEADER, CppCompilerConfig, pch_entry, pch_is_path_sensitive,
    pch_ready, shared_pch,
)

DAY = 86400


@pytest.fixture(autouse=True)
def _no_target_probe(monkeypatch):
    # A config probes its target at construction; the faked subprocess.run
    # here stands for the PCH build only.
    monkeypatch.setattr(toolchain, "compiler_dumpmachine", lambda compiler: "")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setenv("TPYC_SHARED_CACHE_DIR", str(root))
    return root


def _family(monkeypatch, family):
    monkeypatch.setattr(toolchain, "_detect_compiler_family", lambda cxx: family)
    return CppCompilerConfig(compiler=["cxx"])


class FakeCompiler:
    """Records each PCH build; writes the .gch and a depfile naming the
    entry's header, a runtime header and `external` (a system header)."""

    def __init__(self, external: Path):
        self.calls: list[list[str]] = []
        self.external = external
        self.fail = False
        self.gch = b"gch"  # what the next build writes
        self.during = None  # run inside the build, before it returns

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(cmd)
        if self.during is not None:
            self.during()
        if self.fail:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_bytes(self.gch)
        header = cmd[cmd.index("-x") + 2]
        root = Path(cmd[cmd.index("-I") + 1])
        ext = str(self.external).replace(" ", "\\ ")
        Path(cmd[cmd.index("-MF") + 1]).write_text(
            f"{out}: {header} \\\n {root / 'tpy' / 'tpy.hpp'} \\\n {ext}\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _edit(path: Path, text: str, age: float = DAY) -> None:
    """Write `path` as an edit made before the next build: a header whose
    ctime is at or after a build's start keeps that build from being
    published, and ctime cannot be back-dated, so return only once the
    kernel's file clock (coarse: one tick can be milliseconds) has moved
    past the edit. `age` back-dates the mtime, which the rules ignore."""
    path.write_text(text)
    t = time.time() - age
    os.utime(path, (t, t))
    edited = path.stat().st_ctime_ns
    probe = path.with_name(path.name + ".tick")
    deadline = time.monotonic() + 5
    while probe.touch() or probe.stat().st_ctime_ns <= edited:  # touch: utime stamps the ctime
        assert time.monotonic() < deadline, "the file clock did not tick in 5s"
    probe.unlink()


@pytest.fixture
def cxx(monkeypatch, tmp_path, cache):
    external = tmp_path / "sys include" / "vector"
    external.parent.mkdir()
    _edit(external, "// system\n")
    fake = FakeCompiler(external)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(toolchain, "_detect_compiler_family", lambda cxx: "gcc")
    monkeypatch.setattr(toolchain, "compiler_binary_identity",
                        lambda cxx: ("/usr/bin/" + cxx[0], (1, 1, 1, 1)))
    return fake


def _root(tmp_path, name="checkout-a"):
    root = tmp_path / name / "include"
    if not root.exists():
        (root / "tpy").mkdir(parents=True)
        (root / "tpy" / "tpy.hpp").write_text("// runtime\n")
    return root


GCC = CppCompilerConfig(compiler=["g++"])


# -- the build command -----------------------------------------------------

def test_clang_pch_disables_timestamp(monkeypatch, tmp_path, cxx):
    monkeypatch.setattr(toolchain, "_detect_compiler_family", lambda c: "clang")
    shared_pch(CppCompilerConfig(compiler=["clang++"]), _root(tmp_path), [])
    cmd = cxx.calls[0]
    # Both tokens, and in order, so the cc1 flag reaches the frontend.
    i = cmd.index("-Xclang")
    assert cmd[i + 1] == "-fno-pch-timestamp"


def test_gcc_pch_keeps_default_validation(tmp_path, cxx):
    shared_pch(GCC, _root(tmp_path), [])
    cmd = cxx.calls[0]
    assert "-fno-pch-timestamp" not in cmd
    assert "-Xclang" not in cmd


def test_clang_pch_is_path_sensitive(monkeypatch):
    assert pch_is_path_sensitive(_family(monkeypatch, "clang"))


def test_gcc_pch_is_path_agnostic(monkeypatch):
    assert not pch_is_path_sensitive(_family(monkeypatch, "gcc"))


def test_unknown_family_is_treated_as_path_sensitive(monkeypatch):
    # Fail-safe: an unrecognized compiler gets a per-checkout PCH rather than
    # a shared one that may or may not survive a different -I root.
    assert pch_is_path_sensitive(_family(monkeypatch, "unknown"))


# -- the key ---------------------------------------------------------------

def test_one_configuration_shares_one_entry(tmp_path, cache, cxx):
    """Two programs built with the same flags use one .gch, built once."""
    root = _root(tmp_path)
    first = shared_pch(GCC, root, ["-O3"])
    second = shared_pch(GCC, root, ["-O3"])
    assert first == second == pch_entry(GCC, root, ["-O3"]) / PCH_HEADER
    assert first.parent.parent == cache / toolchain.PCH_DIR
    assert (first.parent / PCH_GCH).read_bytes() == b"gch"
    assert len(cxx.calls) == 1


def test_debug_and_release_get_their_own_entries(tmp_path, cxx):
    root = _root(tmp_path)
    debug = shared_pch(GCC, root, ["-g", "-O0"])
    release = shared_pch(GCC, root, ["-O3"])
    assert debug.parent != release.parent
    assert len(cxx.calls) == 2
    assert "-O0" in cxx.calls[0] and "-O3" in cxx.calls[1]


def test_flags_and_compiler_key_the_entry(tmp_path, cxx):
    root = _root(tmp_path)
    configs = [
        GCC,
        CppCompilerConfig(compiler=["g++-14"]),
        CppCompilerConfig(compiler=["g++-14"], std="c++26"),
        CppCompilerConfig(compiler=["g++-14"], std="c++26", extra_flags=["-DX"]),
        CppCompilerConfig(compiler=["g++-14"], std="c++26", extra_flags=["-DX"],
                          warn_flags=["-Wall"]),
    ]
    assert len({pch_entry(c, root, ["-O2"]) for c in configs}) == len(configs)


def test_clang_separates_checkouts(monkeypatch, tmp_path, cxx):
    """Clang re-parses headers reached through a second checkout's -I and
    redefines everything the .gch holds, so each checkout gets its own."""
    monkeypatch.setattr(toolchain, "_detect_compiler_family", lambda c: "clang")
    config = CppCompilerConfig(compiler=["clang++"])
    a, b = _root(tmp_path, "checkout-a"), _root(tmp_path, "checkout-b")
    assert pch_entry(config, a, []) != pch_entry(config, b, [])
    assert pch_entry(config, a, []) == pch_entry(config, a, [])


def test_gcc_shares_across_checkouts(tmp_path, cxx):
    """GCC dedupes guarded headers by content, so worktrees keep sharing one
    .gch (and with it their ccache entries for every PCH-using compile)."""
    a, b = _root(tmp_path, "checkout-a"), _root(tmp_path, "checkout-b")
    assert pch_entry(GCC, a, []) == pch_entry(GCC, b, [])
    shared_pch(GCC, a, [])
    shared_pch(GCC, b, [])
    assert len(cxx.calls) == 1


def test_runtime_header_content_keys_the_entry(tmp_path, cxx):
    root = _root(tmp_path)
    before = pch_entry(GCC, root, [])
    (root / "tpy" / "notes.txt").write_text("not a header\n")
    (root / "tpy" / ".#tpy.hpp").symlink_to(tmp_path / "gone")  # an editor lock link
    assert pch_entry(GCC, root, []) == before
    (root / "tpy" / "tpy.hpp").write_text("// edited\n")
    assert pch_entry(GCC, root, []) != before
    (root / "tpy" / "tpy.hpp").write_text("// runtime\n")
    (root / "tpy" / "extra.h").write_text("// new\n")
    assert pch_entry(GCC, root, []) != before


def test_replaced_compiler_binary_keys_the_entry(monkeypatch, tmp_path, cxx):
    root = _root(tmp_path)
    before = shared_pch(GCC, root, [])
    monkeypatch.setattr(toolchain, "compiler_binary_identity",
                        lambda cxx: ("/usr/bin/g++", (2, 2, 2, 2)))
    assert shared_pch(GCC, root, []) != before
    assert len(cxx.calls) == 2


# -- ready and rebuild -----------------------------------------------------

def test_deps_list_only_headers_outside_the_runtime(tmp_path, cxx):
    root = _root(tmp_path)
    entry = shared_pch(GCC, root, []).parent
    assert _deps_paths(entry) == [str(entry / PCH_HEADER), str(cxx.external)]
    st = cxx.external.stat()
    assert toolchain._read_deps(entry)[1] == ((
        st.st_ino, st.st_ctime_ns, st.st_mtime_ns, st.st_size), str(cxx.external))


def _replace_back_dated(path: Path, text: str, like: os.stat_result) -> None:
    """Replace `path` with a new file whose mtime is `like`'s, as tar,
    `cp -p` or a package manager staging a file leave it."""
    staged = path.with_name(path.name + ".staged")
    staged.write_text(text)
    os.utime(staged, ns=(like.st_atime_ns, like.st_mtime_ns))
    os.replace(staged, path)


def test_dependency_replaced_with_an_older_mtime_is_not_ready(tmp_path, cxx):
    """Same size, same mtime, other contents: only the inode and the ctime
    tell."""
    root = _root(tmp_path)
    entry = shared_pch(GCC, root, []).parent
    before = cxx.external.stat()
    _replace_back_dated(cxx.external, "// SYSTEM\n", before)
    after = cxx.external.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)
    assert not pch_ready(entry)


def test_dependency_replaced_with_an_older_mtime_during_build_publishes_nothing(
        tmp_path, cxx):
    root = _root(tmp_path)
    entry = pch_entry(GCC, root, [])
    old = cxx.external.stat()
    cxx.during = lambda: _replace_back_dated(cxx.external, "// mid-build\n", old)
    assert shared_pch(GCC, root, []) is None
    assert not (entry / PCH_GCH).exists()
    assert _leftovers(entry) == set()


def _stamp_deps_at_start(monkeypatch, cxx, name: str, offset_ns: int = 0) -> None:
    """Give the dependency `name` the build's start ctime (plus `offset_ns`)
    as `_build_pch` sees it, whatever the filesystem's clock granularity."""
    start = []

    def during():
        # The fake compiler writes the depfile after this; until then it is
        # the empty temp whose ctime is the build's start.
        cmd = cxx.calls[-1]
        start.append(os.stat(cmd[cmd.index("-MF") + 1]).st_ctime_ns)

    real = toolchain._external_deps

    def external_deps(depfile, runtime_include_dir):
        return [d._replace(identity=d.identity._replace(ctime_ns=start[-1] + offset_ns))
                if Path(d.path).name == name else d
                for d in real(depfile, runtime_include_dir)]

    cxx.during = during
    monkeypatch.setattr(toolchain, "_external_deps", external_deps)


def test_own_header_sharing_the_start_tick_still_publishes(monkeypatch, tmp_path, cxx):
    """`tpy_pch.hpp` is rewritten just before the build and can share the
    start's tick on a coarse-timestamp filesystem."""
    _stamp_deps_at_start(monkeypatch, cxx, PCH_HEADER)
    assert shared_pch(GCC, _root(tmp_path), []) is not None


def test_external_header_changed_in_the_start_tick_publishes_nothing(
        monkeypatch, tmp_path, cxx):
    _stamp_deps_at_start(monkeypatch, cxx, cxx.external.name)
    assert shared_pch(GCC, _root(tmp_path), []) is None


def test_changed_external_header_rebuilds_in_place(tmp_path, cxx):
    """A system-header upgrade under an unchanged compiler binary."""
    root = _root(tmp_path)
    header = shared_pch(GCC, root, [])
    assert pch_ready(header.parent)
    _edit(cxx.external, "// upgraded libstdc++\n", age=3600)
    assert not pch_ready(header.parent)
    assert shared_pch(GCC, root, []) == header
    assert len(cxx.calls) == 2
    assert pch_ready(header.parent)


def test_touched_external_header_is_not_ready(tmp_path, cxx):
    root = _root(tmp_path)
    entry = shared_pch(GCC, root, []).parent
    old = time.time() - DAY
    os.utime(cxx.external, (old, old))
    assert not pch_ready(entry)


def test_interrupted_build_is_rebuilt(tmp_path, cxx):
    """An entry with a header but no .gch, or no deps list, was never
    published (or predates the deps list)."""
    root = _root(tmp_path)
    entry = shared_pch(GCC, root, []).parent
    (entry / PCH_DEPS).unlink()
    assert not pch_ready(entry)
    shared_pch(GCC, root, [])
    (entry / PCH_GCH).unlink()
    assert not pch_ready(entry)
    shared_pch(GCC, root, [])
    assert len(cxx.calls) == 3
    assert pch_ready(entry)


def test_missing_pch_header_is_rebuilt(tmp_path, cxx):
    """The .hpp is what -include names; a .gch without it is unusable."""
    root = _root(tmp_path)
    entry = shared_pch(GCC, root, []).parent
    (entry / PCH_HEADER).unlink()
    assert not pch_ready(entry)
    shared_pch(GCC, root, [])
    assert (entry / PCH_HEADER).read_text() == "#include <tpy/tpy.hpp>\n"
    assert len(cxx.calls) == 2


def _deps_paths(entry: Path) -> list[str]:
    return [dep.path for dep in toolchain._read_deps(entry)]


def _leftovers(entry: Path) -> set[str]:
    return {p.name for p in entry.iterdir()} - {
        PCH_HEADER, PCH_GCH, PCH_DEPS, ".lock", toolchain.CACHE_USED_MARK}


def test_failed_build_publishes_nothing(tmp_path, cxx):
    root = _root(tmp_path)
    cxx.fail = True
    assert shared_pch(GCC, root, []) is None
    entry = pch_entry(GCC, root, [])
    assert not (entry / PCH_GCH).exists()
    assert not pch_ready(entry)
    assert _leftovers(entry) == set()


def test_headers_edited_during_build_publish_nothing(tmp_path, cxx):
    root = _root(tmp_path)
    entry = pch_entry(GCC, root, [])
    cxx.during = lambda: (root / "tpy" / "tpy.hpp").write_text("// mid-build edit\n")
    assert shared_pch(GCC, root, []) is None
    assert not (entry / PCH_GCH).exists()
    assert _leftovers(entry) == set()


def test_interrupt_during_build_removes_temp_files(tmp_path, cxx):
    root = _root(tmp_path)

    def interrupt():
        raise KeyboardInterrupt

    cxx.during = interrupt
    with pytest.raises(KeyboardInterrupt):
        shared_pch(GCC, root, [])
    assert _leftovers(pch_entry(GCC, root, [])) == set()


def test_unwritable_cache_root_means_no_pch(tmp_path, monkeypatch, cxx):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setenv("TPYC_SHARED_CACHE_DIR", str(blocker))
    assert shared_pch(GCC, _root(tmp_path), []) is None
    assert cxx.calls == []


def test_force_rebuilds_a_ready_entry(tmp_path, cxx):
    root = _root(tmp_path)
    header = shared_pch(GCC, root, [])
    assert shared_pch(GCC, root, [], force=True) == header
    assert len(cxx.calls) == 2


def test_on_build_runs_only_when_building(tmp_path, cxx):
    root = _root(tmp_path)
    builds = []
    shared_pch(GCC, root, [], on_build=lambda: builds.append(1))
    shared_pch(GCC, root, [], on_build=lambda: builds.append(1))
    assert builds == [1]


def test_entry_swept_while_waiting_for_the_lock_is_recreated(monkeypatch, tmp_path, cxx):
    """The sweep renames an expired entry away under its lock; a builder
    that got the lock afterwards holds it on the trash, not the entry."""
    root = _root(tmp_path)
    entry = pch_entry(GCC, root, [])
    real_flock = toolchain.fcntl.flock
    swept = []

    def flock(fd, op):
        real_flock(fd, op)
        if not swept:
            swept.append(entry.with_name(f".trash-{entry.name}-1"))
            entry.rename(swept[0])

    monkeypatch.setattr(toolchain.fcntl, "flock", flock)
    header = shared_pch(GCC, root, [])
    assert header == entry / PCH_HEADER
    assert pch_ready(entry)
    assert not (swept[0] / PCH_GCH).exists()


@pytest.mark.parametrize("failing", [PCH_GCH, PCH_DEPS])
def test_failed_publication_never_pairs_a_gch_with_foreign_deps(
        monkeypatch, tmp_path, cxx, failing):
    """A rebuild against another system header fails at one publication
    step: whatever the entry then holds is either not ready or a .gch with
    the deps of its own build."""
    root = _root(tmp_path)
    entry = shared_pch(GCC, root, []).parent
    built_with = {b"gch": str(cxx.external)}
    other = tmp_path / "sys include" / "vector-2"
    _edit(other, "// another system header\n")
    cxx.external, cxx.gch = other, b"gch-2"
    built_with[b"gch-2"] = str(other)
    real_replace = os.replace

    def replace(src, dst):
        if Path(dst).name == failing:
            raise OSError("disk full")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    assert shared_pch(GCC, root, [], force=True) is None
    if pch_ready(entry):
        assert built_with[(entry / PCH_GCH).read_bytes()] in _deps_paths(entry)
    monkeypatch.setattr(os, "replace", real_replace)
    # The next run rebuilds what is not ready, under the lock.
    assert shared_pch(GCC, root, []) == entry / PCH_HEADER
    assert built_with[(entry / PCH_GCH).read_bytes()] in _deps_paths(entry)
    assert _leftovers(entry) == set()


def test_external_header_edited_during_build_publishes_nothing(tmp_path, cxx):
    """The compiler may have read the header before the edit; stat-ing it
    afterwards would certify contents the .gch does not hold."""
    root = _root(tmp_path)
    entry = pch_entry(GCC, root, [])
    cxx.during = lambda: cxx.external.write_text("// edited mid-build\n")
    assert shared_pch(GCC, root, []) is None
    assert not (entry / PCH_GCH).exists()
    assert _leftovers(entry) == set()
    cxx.during = None
    assert shared_pch(GCC, root, []) == entry / PCH_HEADER


def test_external_header_dated_in_the_future_still_publishes(monkeypatch, tmp_path, cxx):
    """Clock skew (a ctime past now) is not a change during the build."""
    _stamp_deps_at_start(monkeypatch, cxx, cxx.external.name, offset_ns=DAY * 10**9)
    assert shared_pch(GCC, _root(tmp_path), []) is not None
    assert len(cxx.calls) == 1


def test_builder_removes_temp_files_a_killed_build_left(tmp_path, cxx):
    root = _root(tmp_path)
    entry = pch_entry(GCC, root, [])
    entry.mkdir(parents=True)
    orphans = [f"{PCH_GCH}.4242.tmp", f"{PCH_HEADER}.4242.tmp",
               f"{PCH_DEPS}.4242.tmp", "deps.d.4242.tmp"]
    others = ["notes.tmp", f"{PCH_GCH}.x42.tmp", "deps.d.4242", "deps.4242.d.tmp",
              "other.4242.tmp", ".lock.4242.tmp"]
    for name in orphans + others:
        (entry / name).write_text("")
    assert shared_pch(GCC, root, []) == entry / PCH_HEADER
    assert _leftovers(entry) == set(others)


def test_every_builder_temp_is_named_by_the_one_rule(tmp_path, cxx):
    """`<name>.<pid>.tmp` of a file the entry holds, so `_PCH_TEMP_NAME`
    removes whatever a killed build left."""
    root = _root(tmp_path)
    shared_pch(GCC, root, [])
    cmd = cxx.calls[0]
    pid = os.getpid()
    temps = [Path(cmd[cmd.index("-o") + 1]).name, Path(cmd[cmd.index("-MF") + 1]).name]
    assert temps == [f"{PCH_GCH}.{pid}.tmp", f"deps.d.{pid}.tmp"]
    assert all(toolchain._PCH_TEMP_NAME.fullmatch(t) for t in temps)


# -- the key: include-search context ---------------------------------------

def test_relative_include_flags_key_by_their_resolved_path(monkeypatch, tmp_path, cxx):
    """A relative -I names other headers from another directory; the same
    spelling from the same directory, and absolute spellings anywhere,
    share."""
    root = _root(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    for flags in (["-Iinc"], ["-I", "inc"], ["-isystem", "inc"], ["-isysteminc"],
                  ["-iquote", "inc"], ["-iquoteinc"], ["-idirafter", "inc"],
                  ["-idirafterinc"], ["-include", "pre.h"], ["-includepre.h"],
                  ["-imacros", "m.h"], ["-imacrosm.h"], ["-iframework", "fw"],
                  ["-iframeworkfw"], ["-F", "fw"], ["-Ffw"], ["-isysroot", "sr"],
                  ["-isysrootsr"], ["--sysroot", "sr"], ["--sysroot=sr"]):
        config = CppCompilerConfig(compiler=["g++"], extra_flags=flags)
        monkeypatch.chdir(tmp_path / "a")
        in_a = pch_entry(config, root, [])
        assert pch_entry(config, root, []) == in_a
        monkeypatch.chdir(tmp_path / "b")
        assert pch_entry(config, root, []) != in_a, flags
    absolute = CppCompilerConfig(compiler=["g++"], extra_flags=["-I/opt/inc", "-DX"])
    monkeypatch.chdir(tmp_path / "a")
    in_a = pch_entry(absolute, root, [])
    monkeypatch.chdir(tmp_path / "b")
    assert pch_entry(absolute, root, []) == in_a
    assert pch_entry(GCC, root, []) == pch_entry(GCC, root, [])


def test_include_path_flags_resolve_only_their_path():
    rel = str(Path("rel").resolve())
    assert toolchain._abs_include_flags(
        ["g++", "-DX", "-I/abs", "-Irel", "-isystem", "rel", "-include", "/p.h",
         "--sysroot=rel", "--sysroot", "rel", "-fno-rtti", "-O2"]
    ) == ["g++", "-DX", "-I/abs", "-I" + rel, "-isystem", rel, "-include", "/p.h",
          "--sysroot=" + rel, "--sysroot", rel, "-fno-rtti", "-O2"]


def test_include_paths_resolve_symlinks_before_dotdot(monkeypatch, tmp_path, cxx):
    """The compiler reads `link/../inc` beside the link's target; collapsed
    lexically it would name `./inc` and share that directory's entry."""
    root = _root(tmp_path)
    (tmp_path / "elsewhere" / "child").mkdir(parents=True)
    (tmp_path / "elsewhere" / "inc").mkdir()
    (tmp_path / "a" / "inc").mkdir(parents=True)
    (tmp_path / "a" / "link").symlink_to(tmp_path / "elsewhere" / "child")
    monkeypatch.chdir(tmp_path / "a")
    for var in ("CPATH", "CPLUS_INCLUDE_PATH"):
        monkeypatch.delenv(var, raising=False)

    def key(flags):
        return pch_entry(CppCompilerConfig(compiler=["g++"], extra_flags=flags), root, [])

    assert key(["-Ilink/../inc"]) != key(["-Iinc"])
    assert key(["-Ilink/../inc"]) == key([f"-I{tmp_path / 'elsewhere' / 'inc'}"])
    monkeypatch.setenv("CPATH", "link/../inc")
    via_link = pch_entry(GCC, root, [])
    monkeypatch.setenv("CPATH", "inc")
    assert pch_entry(GCC, root, []) != via_link


def test_c_include_path_does_not_key_the_entry(monkeypatch, tmp_path, cxx):
    """A C++ compile never reads it."""
    root = _root(tmp_path)
    monkeypatch.delenv("C_INCLUDE_PATH", raising=False)
    unset = pch_entry(GCC, root, [])
    monkeypatch.setenv("C_INCLUDE_PATH", "/opt/one")
    assert pch_entry(GCC, root, []) == unset


@pytest.mark.parametrize("var", ["CPATH", "CPLUS_INCLUDE_PATH"])
def test_include_path_environment_keys_the_entry(monkeypatch, tmp_path, cxx, var):
    root = _root(tmp_path)
    for name in ("CPATH", "CPLUS_INCLUDE_PATH"):
        monkeypatch.delenv(name, raising=False)
    unset = pch_entry(GCC, root, [])
    monkeypatch.setenv(var, "/opt/one")
    one = pch_entry(GCC, root, [])
    monkeypatch.setenv(var, "/opt/two")
    assert len({unset, one, pch_entry(GCC, root, [])}) == 3
    monkeypatch.setenv(var, "rel")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    monkeypatch.chdir(tmp_path / "a")
    in_a = pch_entry(GCC, root, [])
    monkeypatch.chdir(tmp_path / "b")
    assert pch_entry(GCC, root, []) != in_a


# -- the depfile and the deps list -----------------------------------------

def test_parse_depfile_escapes_and_continuations():
    text = ("out.gch: /usr/include/a$$b.h /x/has\\#hash.h \\\r\n"
            " rel/dir/c.h /sp\\ ace/d.h\n")
    assert toolchain._parse_depfile(text) == [
        "/usr/include/a$b.h", "/x/has#hash.h", "rel/dir/c.h", "/sp ace/d.h"]


def test_relative_depfile_paths_are_recorded_absolute(monkeypatch, tmp_path, cxx):
    root = _root(tmp_path)
    monkeypatch.chdir(cxx.external.parent)
    depfile = tmp_path / "d.d"
    depfile.write_text(f"out.gch: {cxx.external.name}\n")
    [dep] = toolchain._external_deps(depfile, root)
    assert dep.path == str(cxx.external)


def test_depfile_paths_keep_dotdot_after_a_symlink(monkeypatch, tmp_path, cxx):
    """GCC writes `.../14/../../../../include/...`; collapsing `..` lexically
    would stat another file where a component is a symlink."""
    root = _root(tmp_path)
    (tmp_path / "elsewhere" / "child").mkdir(parents=True)
    real = tmp_path / "elsewhere" / "x.h"
    real.write_text("// the header the compiler read\n")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.h").write_text("// a lexical twin\n")
    (tmp_path / "a" / "link").symlink_to(tmp_path / "elsewhere" / "child")
    monkeypatch.chdir(tmp_path / "a")
    depfile = tmp_path / "d.d"
    depfile.write_text("out.gch: link/../x.h\n")
    [dep] = toolchain._external_deps(depfile, root)
    assert dep.identity.ino == real.stat().st_ino


@pytest.mark.parametrize("line", ["1 2 3 /usr/include/vector", "1x 2 3 4 /p", "1 2x 3 4 /p",
                                  "1 2 3x 4 /p", "1 2 3 4x /p", "garbage"])
def test_malformed_deps_line_is_not_ready(tmp_path, cxx, line):
    root = _root(tmp_path)
    entry = shared_pch(GCC, root, []).parent
    assert pch_ready(entry)
    (entry / PCH_DEPS).write_text(f"{line}\n")
    assert not pch_ready(entry)


# -- cache aging -----------------------------------------------------------

def test_entry_marked_used_after_the_age_check_survives_the_sweep(
        monkeypatch, tmp_path, cxx, cache):
    root = _root(tmp_path)
    entry = shared_pch(GCC, root, []).parent
    old = time.time() - 30 * DAY
    for p in (entry / toolchain.CACHE_USED_MARK, entry):
        os.utime(p, (old, old))
    real_expired = toolchain._expired

    def expired_then_used(e, read_file, now, max_age):
        result = real_expired(e, read_file, now, max_age)
        toolchain.mark_cache_entry_used(e)  # another session, right after the check
        return result

    monkeypatch.setattr(toolchain, "_expired", expired_then_used)
    sweep = toolchain.sweep_shared_cache(cache, force=True)
    assert (sweep.removed, sweep.failed) == (0, [])
    assert pch_ready(entry)


def test_unused_entry_is_swept(tmp_path, cxx, cache):
    root = _root(tmp_path)
    entry = shared_pch(GCC, root, []).parent
    old = time.time() - 30 * DAY
    for p in (entry / toolchain.CACHE_USED_MARK, entry):
        os.utime(p, (old, old))
    assert toolchain.sweep_shared_cache(cache, force=True).removed == 1
    assert not entry.exists()


# -- the REPL --------------------------------------------------------------

def _repl_backend(monkeypatch, tmp_path):
    """A CompileBackend whose runtime root is under tmp_path."""
    root = _root(tmp_path)
    runtime_dir = tmp_path / "runtime"
    (runtime_dir / "cpp").mkdir(parents=True)
    root.rename(runtime_dir / "cpp" / "include")
    monkeypatch.setattr(repl_backends, "get_runtime_dir", lambda: runtime_dir)
    (tmp_path / "repl").mkdir()
    return repl_backends.CompileBackend(["g++"], tmp_path / "repl", "main")


def test_repl_pch_round_trip(monkeypatch, tmp_path, cxx, cache):
    backend = _repl_backend(monkeypatch, tmp_path)
    assert backend._pch_is_stale()
    backend._pch_needs_build = True
    backend._setup_pch()
    assert backend._pch_path == backend.pch_cache_dir / PCH_HEADER
    assert backend.pch_cache_dir.parent == cache / toolchain.PCH_DIR
    assert backend._pch_build_time is not None and backend._pch_build_time >= 0
    assert len(cxx.calls) == 1
    # The REPL's own opt flags reach the build; afterwards the entry is ready.
    assert all(f in cxx.calls[0] for f in backend._OPT_FLAGS)
    assert not backend._pch_is_stale()


def test_repl_reuses_a_ready_entry_silently(monkeypatch, tmp_path, cxx):
    backend = _repl_backend(monkeypatch, tmp_path)
    backend._setup_pch()
    second = repl_backends.CompileBackend(["g++"], tmp_path / "repl", "main")
    assert not second._pch_is_stale()
    second._setup_pch()
    assert second._pch_path == backend._pch_path
    assert second._pch_build_time is None
    assert len(cxx.calls) == 1


def test_repl_carries_the_darwin_pin_like_the_cli(monkeypatch, tmp_path, cxx):
    """The REPL's compiles and its PCH target the same macOS deployment
    version as the CLI's, so the two can share one PCH entry."""
    monkeypatch.setattr(toolchain, "compiler_dumpmachine",
                        lambda compiler: "arm64-apple-darwin23.4.0")
    monkeypatch.setattr(toolchain, "_resolve_compiler", lambda cxx: ["g++"])
    monkeypatch.setattr(toolchain, "warn_toolchain_unsupported", lambda compiler: None)
    pin = toolchain.darwin_version_min_flags(["g++"], "darwin")
    assert pin
    backend = _repl_backend(monkeypatch, tmp_path)
    assert backend._config.compiler == CppCompilerConfig.from_env("gcc").compiler
    assert pin[0] in backend._common_flags()
    backend._setup_pch()
    assert pin[0] in cxx.calls[0]


def test_build_commands_default_config_carries_the_darwin_pin(monkeypatch, tmp_path):
    from tpyc.compiler import BuildLayout

    monkeypatch.setattr(toolchain, "compiler_dumpmachine",
                        lambda compiler: "arm64-apple-darwin23.4.0")
    pin = toolchain.darwin_version_min_flags(["g++"], "darwin")
    assert pin
    layout = BuildLayout(tmp_path, "main")
    cmds = layout.build_cpp_commands(tmp_path, [tmp_path / "main.cpp"])
    assert all(pin[0] in cmd for cmd in cmds)


def test_repl_pch_is_stale_after_an_external_header_change(monkeypatch, tmp_path, cxx):
    backend = _repl_backend(monkeypatch, tmp_path)
    backend._setup_pch()
    cxx.external.write_text("// upgraded\n")
    assert backend._pch_is_stale()


def test_repl_unwritable_cache_root(monkeypatch, tmp_path, cxx):
    backend = _repl_backend(monkeypatch, tmp_path)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setenv("TPYC_SHARED_CACHE_DIR", str(blocker))
    assert backend._pch_is_stale()
    backend._pch_needs_build = True
    backend._setup_pch()
    assert backend._pch_path is None
    assert backend._pch_build_time == -1.0


def test_repl_pch_build_failure_is_signalled(monkeypatch, tmp_path, cxx):
    backend = _repl_backend(monkeypatch, tmp_path)
    cxx.fail = True
    backend._pch_needs_build = True
    backend._setup_pch()
    assert backend._pch_path is None
    assert backend._pch_build_time == -1.0


def test_repl_startup_sweeps_and_reports_a_malformed_max_age(monkeypatch, tmp_path, cxx, capsys):
    backend = _repl_backend(monkeypatch, tmp_path)
    reports = []
    monkeypatch.setattr(repl_backends, "sweep_shared_cache_best_effort",
                        lambda report: reports.append(report))
    backend.startup()
    backend._wait_for_pch()
    assert len(reports) == 1
    monkeypatch.setattr(repl_backends, "sweep_shared_cache_best_effort",
                        toolchain.sweep_shared_cache_best_effort)
    monkeypatch.setenv(toolchain.CACHE_MAX_AGE_ENV, "a week")
    backend.startup()
    backend._wait_for_pch()
    assert "warning: cache sweep skipped" in capsys.readouterr().err


# -- the CLI ---------------------------------------------------------------

class _CliBuild:
    """`tpyc prog.py --build` with the toolchain faked: the compile and link
    steps create their outputs, `shared_pch` and the sweep are recorded."""

    def __init__(self, monkeypatch, tmp_path, *, pch_builds: bool):
        from tpyc import cli
        self.events: list[str] = []
        self.pch_kwargs: list[dict] = []
        self.pch_lines: list[float] = []
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        header = tmp_path / "entry" / PCH_HEADER
        monkeypatch.setenv("TPYC_SHARED_CACHE_DIR", str(tmp_path / "cache"))

        def fake_shared_pch(config, runtime_include_dir, opt_flags, **kwargs):
            self.events.append("pch")
            self.pch_kwargs.append(kwargs)
            if pch_builds:
                kwargs["on_build"]()
            return header

        def fake_run(cmd, *args, **kwargs):
            self.events.append("link" if "-c" not in cmd else "compile")
            if "-o" in cmd:
                out = Path(cmd[cmd.index("-o") + 1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(cli, "shared_pch", fake_shared_pch)
        monkeypatch.setattr(cli, "sweep_shared_cache_best_effort",
                            lambda report: self.events.append("sweep"))
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(cli, "_resolve_cpp_config",
                            lambda args: CppCompilerConfig(compiler=["g++"]))
        monkeypatch.setattr(cli.ProgressPrinter, "pch",
                            lambda printer, elapsed: self.pch_lines.append(elapsed))

    def run(self, *extra: str) -> int:
        from tpyc import cli
        prog = self.tmp_path / "prog.py"
        prog.write_text("print(1)\n")
        self.monkeypatch.setattr(sys, "argv", [
            "tpyc", str(prog), "--build", "-j", "1", "--no-ccache",
            "-o", str(self.tmp_path / "out"), *extra])
        return cli.main_tpyc()


def test_cli_rebuild_forces_the_pch(monkeypatch, tmp_path):
    build = _CliBuild(monkeypatch, tmp_path, pch_builds=True)
    assert build.run("--rebuild") == 0
    assert build.pch_kwargs[0]["force"] is True


def test_cli_plain_build_does_not_force_the_pch(monkeypatch, tmp_path):
    build = _CliBuild(monkeypatch, tmp_path, pch_builds=False)
    assert build.run() == 0
    assert build.pch_kwargs[0]["force"] is False


def test_cli_reports_the_pch_only_when_it_was_built(monkeypatch, tmp_path):
    built = _CliBuild(monkeypatch, tmp_path / "built", pch_builds=True)
    (tmp_path / "built").mkdir()
    assert built.run() == 0
    assert len(built.pch_lines) == 1
    reused = _CliBuild(monkeypatch, tmp_path / "reused", pch_builds=False)
    (tmp_path / "reused").mkdir()
    assert reused.run() == 0
    assert reused.pch_lines == []


def test_cli_sweeps_after_the_build(monkeypatch, tmp_path):
    build = _CliBuild(monkeypatch, tmp_path, pch_builds=False)
    assert build.run() == 0
    assert build.events[0] == "pch"
    assert build.events[-1] == "sweep" and "link" in build.events[:-1]
    assert build.events.count("sweep") == 1


def test_cli_reports_a_malformed_max_age(monkeypatch, tmp_path, capsys):
    build = _CliBuild(monkeypatch, tmp_path, pch_builds=False)
    from tpyc import cli
    monkeypatch.setattr(cli, "sweep_shared_cache_best_effort",
                        toolchain.sweep_shared_cache_best_effort)
    monkeypatch.setenv(toolchain.CACHE_MAX_AGE_ENV, "a week")
    assert build.run() == 0
    assert "warning: cache sweep skipped" in capsys.readouterr().err
