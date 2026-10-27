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

def _isolate_build_settings(monkeypatch, tmp_path) -> Path:
    """A fake `cxx` (and `zig`) on an absolute PATH entry, and none of the
    search-path variables set: the default settings that share the build
    dir. Returns the bin dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("cxx", "zig"):
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))
    for name in BUILD_PATH_ENV:
        monkeypatch.delenv(name, raising=False)
    # A Linux target whatever the host: a darwin one adds tpyc's own pin.
    monkeypatch.setattr(toolchain, "compiler_dumpmachine",
                        lambda compiler: "x86_64-pc-linux-gnu")
    return bin_dir


BUILD_PATH_ENV = ("CPATH", "CPLUS_INCLUDE_PATH", "C_INCLUDE_PATH", "LIBRARY_PATH")


def _plain_config() -> CppCompilerConfig:
    return CppCompilerConfig(compiler=["cxx"])


class _CliBuild:
    """`tpyc prog.py --build` with the toolchain faked: the compile and link
    steps create their outputs, `shared_pch` and the sweep are recorded."""

    def __init__(self, monkeypatch, tmp_path, *, pch_builds: bool, config=None):
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

        self.runs: list[tuple[list[str], dict]] = []

        def fake_run(cmd, *args, **kwargs):
            self.runs.append((cmd, kwargs))
            self.events.append("link" if "-c" not in cmd else "compile")
            if "-o" in cmd:
                out = Path(kwargs.get("cwd") or ".") / cmd[cmd.index("-o") + 1]
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(cli, "shared_pch", fake_shared_pch)
        monkeypatch.setattr(cli, "sweep_shared_cache_best_effort",
                            lambda report: self.events.append("sweep"))
        monkeypatch.setattr(subprocess, "run", fake_run)
        _isolate_build_settings(monkeypatch, tmp_path)
        monkeypatch.setattr(cli, "_resolve_cpp_config",
                            lambda args: (config or _plain_config)())
        monkeypatch.setattr(cli.ProgressPrinter, "pch",
                            lambda printer, elapsed: self.pch_lines.append(elapsed))

    def run(self, *extra: str) -> int:
        from tpyc import cli
        self.tmp_path.mkdir(parents=True, exist_ok=True)
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
    assert built.run() == 0
    assert len(built.pch_lines) == 1
    reused = _CliBuild(monkeypatch, tmp_path / "reused", pch_builds=False)
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


# -- build steps share ccache entries across programs ------------------------

def _record_runs(monkeypatch) -> list[tuple[list[str], dict]]:
    runs: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        runs.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return runs


def test_build_step_runs_from_its_base_dir_only_when_it_shares(monkeypatch, tmp_path):
    bin_dir = _isolate_build_settings(monkeypatch, tmp_path)
    assert toolchain.build_step_cwd(_plain_config(), tmp_path) == tmp_path
    assert toolchain.build_step_cwd(
        CppCompilerConfig(compiler=[str(bin_dir / "cxx"), "-DX"]), tmp_path) is None
    runs = _record_runs(monkeypatch)
    monkeypatch.setenv("CCACHE_SLOPPINESS", "pch_defines")
    toolchain.run_build_step(["ccache", "g++", "-c", "x.cpp"], tmp_path)
    [(_, shared)] = runs
    assert shared["cwd"] == tmp_path
    assert shared["env"]["CCACHE_BASEDIR"] == str(tmp_path)
    assert shared["env"]["CCACHE_SLOPPINESS"] == "pch_defines"
    runs.clear()
    toolchain.run_build_step(["g++", "-c", "x.cpp"], None)
    # The caller's cwd and environment, as a plain subprocess.run has them.
    [(_, own)] = runs
    assert "cwd" not in own and "env" not in own


# Each kind of setting the user can give, as the config it yields: a word
# in the compiler command ($CXX), a relative compiler (--cxx or $CXX), a
# bare name a relative PATH element finds, construction-time extra flags.
_USER_SETTING_CONFIGS = {
    "cxx-word": lambda: CppCompilerConfig.for_compiler(["cxx", "-Iinc", "-include", "probe.h"]),
    "cxx-response-file": lambda: CppCompilerConfig.for_compiler(["cxx", "@opts"]),
    "relative-compiler": lambda: CppCompilerConfig.for_compiler(["bin/cxx"]),
    "dot-compiler": lambda: CppCompilerConfig.for_compiler(["./bin/cxx"]),
    "extra-flags": lambda: CppCompilerConfig(compiler=["cxx"], extra_flags=["-Iinc"]),
}


@pytest.mark.parametrize("kind", sorted(_USER_SETTING_CONFIGS))
def test_user_compiler_settings_do_not_share_the_build_dir(monkeypatch, tmp_path, kind):
    _isolate_build_settings(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    config = _USER_SETTING_CONFIGS[kind]()
    assert not config.shares_build_dir


@pytest.mark.parametrize("var", BUILD_PATH_ENV)
def test_a_search_path_variable_does_not_share_the_build_dir(monkeypatch, tmp_path, var):
    _isolate_build_settings(monkeypatch, tmp_path)
    monkeypatch.setenv(var, "")
    assert _plain_config().shares_build_dir, "an empty variable names nothing"
    monkeypatch.setenv(var, "inc")
    assert not _plain_config().shares_build_dir


def test_a_relative_path_element_finding_the_compiler_does_not_share(monkeypatch, tmp_path):
    _isolate_build_settings(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "bin")
    assert not _plain_config().shares_build_dir


def test_tpycs_own_compiler_command_shares_the_build_dir(monkeypatch, tmp_path):
    bin_dir = _isolate_build_settings(monkeypatch, tmp_path)
    assert _plain_config().shares_build_dir
    assert CppCompilerConfig(compiler=[str(bin_dir / "cxx")]).shares_build_dir
    # zig's subcommand and the darwin pin are tpyc's own words.
    assert CppCompilerConfig.for_compiler([str(bin_dir / "zig"), "c++"]).shares_build_dir
    monkeypatch.setattr(toolchain, "compiler_dumpmachine",
                        lambda compiler: "arm64-apple-darwin23.0.0")
    darwin = CppCompilerConfig.for_compiler(["cxx"])
    assert darwin.compiler == ["cxx", f"-mmacosx-version-min={toolchain.MACOS_VERSION_MIN}"]
    assert darwin.shares_build_dir
    assert not CppCompilerConfig.for_compiler(["cxx", "-DX"]).shares_build_dir
    # Re-decided with the command that replaces it.
    darwin.use_compiler(["cxx", "-Iinc"])
    assert not darwin.shares_build_dir


def test_cxx_from_the_environment_or_a_relative_path_is_not_rewritten(monkeypatch, tmp_path):
    _isolate_build_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(toolchain, "warn_toolchain_unsupported", lambda compiler: None)
    monkeypatch.chdir(tmp_path)
    relative = CppCompilerConfig.from_env("bin/cxx")
    assert relative.compiler == ["bin/cxx"] and not relative.shares_build_dir
    monkeypatch.setenv("CXX", "cxx -Iinc @opts")
    from_env = CppCompilerConfig.from_env()
    assert from_env.compiler == ["cxx", "-Iinc", "@opts"] and not from_env.shares_build_dir
    monkeypatch.setenv("CXX", "cxx")
    assert CppCompilerConfig.from_env().shares_build_dir


_USER_WORDS = ["-Iinc", "-include", "probe.h", "@opts"]


@pytest.mark.parametrize("kind", ["cxx-word", "extra-flags", "relative-compiler",
                                  *BUILD_PATH_ENV])
@pytest.mark.parametrize("jobs", ["1", "2"])
def test_cli_with_user_settings_runs_from_the_callers_cwd(monkeypatch, tmp_path, kind, jobs):
    configs = {
        "cxx-word": lambda: CppCompilerConfig.for_compiler(["cxx", *_USER_WORDS]),
        "extra-flags": lambda: CppCompilerConfig(compiler=["cxx"], extra_flags=_USER_WORDS),
        "relative-compiler": lambda: CppCompilerConfig.for_compiler(["bin/cxx"]),
    }
    build = _CliBuild(monkeypatch, tmp_path, pch_builds=False, config=configs.get(kind))
    if kind in BUILD_PATH_ENV:
        monkeypatch.setenv(kind, "inc")
    monkeypatch.chdir(tmp_path)
    assert build.run("-j", jobs) == 0
    assert build.runs
    for cmd, kwargs in build.runs:
        assert "cwd" not in kwargs and "env" not in kwargs
        # Run from the caller's cwd, so the build dir's paths stay absolute.
        assert os.path.isabs(cmd[cmd.index("-o") + 1])
    compiles = [cmd for cmd, _ in build.runs if "-c" in cmd]
    if kind == "cxx-word":
        assert all(cmd[:5] == ["cxx", *_USER_WORDS] for cmd in compiles)
    elif kind == "extra-flags":
        assert all(any(cmd[i:i + 4] == _USER_WORDS for i in range(len(cmd)))
                   for cmd in compiles if not cmd[-1].endswith(".c"))
    elif kind == "relative-compiler":
        assert all(cmd[0] == "bin/cxx" for cmd, _ in build.runs)


def _runtime_step(cmd: list[str]) -> bool:
    return "-c" in cmd and os.sep + "rt_" in cmd[cmd.index("-o") + 1]


@pytest.mark.parametrize("extra", [(), ("--debug",), ("-j", "2")])
def test_cli_build_steps_run_from_the_program_build_dir(monkeypatch, tmp_path, extra):
    from tpyc import cli
    build = _CliBuild(monkeypatch, tmp_path, pch_builds=False)
    timed = []
    real_timed_run = cli._timed_run

    def timed_run(cmd, cwd):
        timed.append(cmd)
        return real_timed_run(cmd, cwd)

    monkeypatch.setattr(cli, "_timed_run", timed_run)
    assert build.run(*extra) == 0
    base = tmp_path / "out"
    assert {"compile", "link"} <= set(build.events)
    for _, kwargs in build.runs:
        assert kwargs["cwd"] == base
        assert kwargs["env"]["CCACHE_BASEDIR"] == str(base)
    compiles = [cmd for cmd, _ in build.runs if "-c" in cmd]
    # -j 2 compiles through the pool, the others one by one.
    assert len(timed) == (len(compiles) if "-j" in extra else 0) and len(compiles) > 1
    if "--debug" in extra:
        # Debug info keeps absolute source paths, so a debugger finds them
        # from any directory.
        assert all("-g" in cmd for cmd in compiles)
        assert not any(arg.startswith("-fdebug-prefix-map")
                       for cmd, _ in build.runs for arg in cmd)


def test_cli_runtime_tus_take_neither_the_program_headers_nor_the_pch(monkeypatch, tmp_path):
    build = _CliBuild(monkeypatch, tmp_path, pch_builds=False)
    assert build.run() == 0
    base = str(tmp_path / "out")
    pch = str(tmp_path / "entry" / PCH_HEADER)
    runtime = [cmd for cmd, _ in build.runs if _runtime_step(cmd)]
    assert runtime
    for cmd in runtime:
        assert "-include" not in cmd and pch not in cmd
        assert os.path.join(base, "include") not in cmd
    generated = [cmd for cmd, _ in build.runs if "-c" in cmd and not _runtime_step(cmd)]
    assert generated and all(pch in cmd for cmd in generated)


def test_cli_build_steps_are_the_same_in_every_build_dir(monkeypatch, tmp_path):
    # zig's object cache keys on the literal command line: byte-identical
    # steps are what lets a second program hit every stdlib and runtime
    # object the first one stored.
    steps = []
    for name in ("a", "b"):
        build = _CliBuild(monkeypatch, tmp_path / name, pch_builds=False)
        assert build.run() == 0
        # The fake PCH sits next to each program; the real one is shared.
        pch_dir = str(tmp_path / name / "entry")
        base = str(tmp_path / name / "out")
        assert not any(base in arg for cmd, _ in build.runs for arg in cmd)
        steps.append([[arg.replace(pch_dir, "<PCH>") for arg in cmd]
                      for cmd, _ in build.runs])
    assert steps[0] == steps[1]
    assert any(cmd[-1].startswith("src/") for cmd in steps[0])


def _layout_commands(tmp_path, config, *, step_cwd="base", opt_flags=None, debug=False):
    from tpyc.compiler import BuildLayout
    layout = BuildLayout(tmp_path / "out", "main", "release")
    rt_include = tmp_path / "rt" / "include"
    return layout, layout.build_cpp_commands(
        rt_include, [layout.src_dir / "main.cpp"], config=config, opt_flags=opt_flags,
        runtime_cpp_sources=[tmp_path / "rt" / "src" / "x.cpp"],
        step_cwd=layout.base_dir if step_cwd == "base" else step_cwd, debug=debug)


def test_build_commands_name_build_dir_paths_relative_to_it(monkeypatch, tmp_path):
    _isolate_build_settings(monkeypatch, tmp_path)
    layout, cmds = _layout_commands(tmp_path, _plain_config())
    generated, runtime, link = cmds
    assert generated[-4:] == ["-c", "-o", "release/main.o", "src/main.cpp"]
    assert generated[generated.index("include") - 1] == "-I"
    assert runtime[-3:] == ["-o", "release/runtime/rt_x.o", str(tmp_path / "rt" / "src" / "x.cpp")]
    assert str(tmp_path / "rt" / "include") in generated
    assert link[link.index("-o") + 1:][:3] == ["release/main", "release/main.o",
                                               "release/runtime/rt_x.o"]
    assert not any(str(layout.base_dir) in arg for cmd in cmds for arg in cmd)


@pytest.mark.parametrize("why", ["caller-cwd", "debug"])
def test_build_commands_stay_absolute_unless_run_from_the_build_dir(
        monkeypatch, tmp_path, why):
    _isolate_build_settings(monkeypatch, tmp_path)
    layout, cmds = _layout_commands(
        tmp_path, _plain_config(), step_cwd=None if why == "caller-cwd" else "base",
        opt_flags=["-g", "-O0"] if why == "debug" else None, debug=why == "debug")
    generated = cmds[0]
    assert generated[-1] == str(layout.src_dir / "main.cpp")
    for cmd in cmds:
        assert cmd[cmd.index("-o") + 1].startswith(str(layout.base_dir) + os.sep)


def test_build_commands_take_the_debug_fact_not_a_flag_scan(monkeypatch, tmp_path):
    # A release build's -g0 is no debug build: its paths stay relative.
    _isolate_build_settings(monkeypatch, tmp_path)
    _, cmds = _layout_commands(tmp_path, _plain_config(), opt_flags=["-g0", "-O3"])
    assert cmds[0][-1] == "src/main.cpp"


def test_link_step_takes_the_build_opt_flags(monkeypatch, tmp_path):
    # zig builds its bundled libc++ at the link line's optimization level, so
    # a release link without them pulls in a Debug libc++.
    _isolate_build_settings(monkeypatch, tmp_path)
    _, cmds = _layout_commands(tmp_path, _plain_config(), opt_flags=["-g0", "-O3"])
    link = cmds[-1]
    assert link[link.index("-o") - 2:link.index("-o")] == ["-g0", "-O3"]


def test_build_commands_are_relative_to_the_cwd_the_caller_names(monkeypatch, tmp_path):
    # The caller decides the cwd once (the harness: its checkout root, for any
    # config); the commands follow it rather than re-deciding from the config.
    _isolate_build_settings(monkeypatch, tmp_path)
    config = CppCompilerConfig(compiler=["cxx"], extra_flags=["-DX"])
    assert not config.shares_build_dir
    _, cmds = _layout_commands(tmp_path, config, step_cwd=tmp_path)
    generated, runtime, link = cmds
    assert generated[-1] == os.path.join("out", "main.d", "src", "main.cpp")
    assert runtime[-1] == os.path.join("rt", "src", "x.cpp")
    assert link[link.index("-o") + 1] == os.path.join("out", "main.d", "release", "main")
    assert not any(str(tmp_path) in arg for cmd in cmds for arg in cmd)


def test_runtime_tus_take_no_third_party_include_dirs(monkeypatch, tmp_path):
    from tpyc import get_runtime_dir
    from tpyc.build.third_party import resolve_build_plan
    from tpyc.compiler import BuildLayout

    _isolate_build_settings(monkeypatch, tmp_path)
    plan = resolve_build_plan(["pcre2"], get_runtime_dir() / "cpp", {})
    assert plan.extra_include_dirs and plan.c_sources
    rt_include = get_runtime_dir() / "cpp" / "include"
    rt_sources = toolchain.discover_runtime_cpp_sources(get_runtime_dir() / "cpp")
    assert rt_sources

    def commands(name, with_plan):
        layout = BuildLayout(tmp_path / name, "main", "release")
        return layout.build_cpp_commands(
            rt_include, [layout.src_dir / "main.cpp"], config=_plain_config(),
            extra_include_dirs=plan.extra_include_dirs if with_plan else None,
            c_sources=plan.c_sources if with_plan else None,
            runtime_cpp_sources=rt_sources, step_cwd=layout.base_dir)[:-1]

    def runtime(cmds):
        return [cmd for cmd in cmds if _runtime_step(cmd)]

    with_re, without_re = commands("a", True), commands("b", False)
    assert runtime(with_re) == runtime(without_re) and runtime(with_re)
    tp_dirs = [str(d) for d in plan.extra_include_dirs]
    [generated] = [cmd for cmd in with_re if cmd[-1] == "src/main.cpp"]
    assert all(d in generated for d in tp_dirs)
    # The library's own sources carry its include dir in their own flags.
    vendored = [cmd for cmd in with_re if cmd[-1].endswith(".c")]
    assert vendored and all(any(d in arg for d in tp_dirs for arg in cmd) for cmd in vendored)


def test_zig_builds_use_ccache_when_it_is_installed(monkeypatch, tmp_path):
    bin_dir = _isolate_build_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(toolchain, "warn_toolchain_unsupported", lambda compiler: None)
    monkeypatch.setattr(toolchain, "_resolve_compiler",
                        lambda cxx: [str(bin_dir / "zig"), "c++"])
    assert not CppCompilerConfig.from_env("zig").ccache
    (bin_dir / "ccache").write_text("#!/bin/sh\n")
    (bin_dir / "ccache").chmod(0o755)
    zig = CppCompilerConfig.from_env("zig")
    assert zig.ccache and zig.shares_build_dir


def test_build_commands_are_absolute_for_relative_inputs(monkeypatch, tmp_path):
    from tpyc.compiler import BuildLayout

    monkeypatch.chdir(tmp_path)
    layout = BuildLayout(Path("rel"), "main")
    cmds = layout.build_cpp_commands(
        Path("rt/include"), [Path("rel/main.d/src/main.cpp")],
        extra_include_dirs=[Path("tp/include")], force_includes=[Path("pre/pch.hpp")],
        c_sources=[(Path("tp/src/vendor.c"), ["-DV"])],
        runtime_cpp_sources=[Path("rt/src/x.cpp")])
    assert layout.base_dir == tmp_path / "rel" / "main.d"
    for cmd in cmds:
        paths = [a for a in cmd if "/" in a]
        assert paths and all(os.path.isabs(p) for p in paths)
    tp_include = ["-I", str(tmp_path / "tp" / "include")]
    pre = ["-include", str(tmp_path / "pre" / "pch.hpp")]

    def has(cmd, pair):
        return any(cmd[i:i + 2] == pair for i in range(len(cmd)))

    [vendor] = [cmd for cmd in cmds if cmd[-1].endswith("vendor.c")]
    assert vendor[-1] == str(tmp_path / "tp" / "src" / "vendor.c")
    [runtime] = [cmd for cmd in cmds if cmd[-1].endswith("x.cpp")]
    assert not has(runtime, tp_include) and not has(runtime, pre)
    [generated] = [cmd for cmd in cmds if cmd[-1].endswith("main.cpp")]
    assert has(generated, tp_include) and has(generated, pre)


@pytest.mark.parametrize("from_base_dir", [False, True])
def test_build_commands_keep_dotdot_after_a_symlinked_output_dir(
        monkeypatch, tmp_path, from_base_dir):
    # `-o alias/../out` with `alias` a symlink names `out` beside the link's
    # target; a lexically collapsed `./out` is another directory.
    from tpyc.compiler import BuildLayout

    _isolate_build_settings(monkeypatch, tmp_path)
    (tmp_path / "elsewhere" / "child").mkdir(parents=True)
    (tmp_path / "alias").symlink_to(tmp_path / "elsewhere" / "child")
    monkeypatch.chdir(tmp_path)
    layout = BuildLayout(Path("alias/../out"), "main", "release")
    cmds = layout.build_cpp_commands(Path("rt/include"), [layout.src_dir / "main.cpp"],
                                     config=_plain_config(),
                                     step_cwd=layout.base_dir if from_base_dir else None)
    real_build_dir = tmp_path / "elsewhere" / "out" / "main.d" / "release"
    assert real_build_dir.is_dir() and not (tmp_path / "out").exists()
    assert layout.base_dir.resolve() == real_build_dir.parent
    # A relative output is joined onto the cwd the step runs from.
    cwd = layout.base_dir if from_base_dir else tmp_path
    outputs = [cwd / cmd[cmd.index("-o") + 1] for cmd in cmds]
    assert all(os.path.isabs(cmd[cmd.index("-o") + 1]) != from_base_dir for cmd in cmds)
    assert outputs and all(o.parent.resolve() == real_build_dir for o in outputs)
