"""
C++ toolchain discovery and configuration.

Compiler resolution (--cxx / $CXX / auto-detect), CppCompilerConfig, the
strict warning sets, PCH building, and runtime-source discovery. Split out
of compiler.py so the build cache's warm path can replay toolchain
resolution without importing the compiler machinery (~250ms) -- keep this
module's imports light (stdlib only).
"""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import contextlib
import functools
import glob
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

try:
    import fcntl
except ImportError:  # Windows: the cache sweep's hourly stamp alone keeps it rare
    fcntl = None  # type: ignore[assignment]


class CompilerNotFoundError(Exception):
    """Raised when a requested C++ compiler cannot be found."""
    def __init__(self, cxx: str):
        self.cxx = cxx
        super().__init__(f"C++ compiler '{cxx}' not found")


class ToolchainUnsupportedError(Exception):
    """Raised when a resolved C++ compiler cannot build TPy output (C++23)."""
    def __init__(self, compiler: list[str], log_path: Path | None = None,
                 also_rejected: tuple[str, ...] = ()):
        self.compiler = compiler
        self.log_path = log_path
        name = os.path.basename(compiler[0])
        msg = (
            f"C++ compiler '{name}' cannot build TurboPython output "
            "(C++23 required: std::expected, std::ranges).\n"
            "Install g++ >= 13 or clang++ >= 19, or add the bundled zig "
            "toolchain:\n"
            '    pip install "tpy-lang[bundled]"\n'
            "or pick one explicitly: tpy --cxx zig ..."
        )
        if also_rejected:
            msg += "\nAlso probed and rejected: " + ", ".join(also_rejected)
        if log_path is not None:
            msg += f"\n(probe log: {log_path})"
        super().__init__(msg)


def _find_all_versioned(prefix: str) -> list[tuple[str, str, int]]:
    """Find all versioned binaries matching prefix, sorted by version descending.

    Returns list of (binary_name, path, version). Unversioned binary gets version -1.
    """
    seen: dict[str, tuple[str, int]] = {}
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for fpath in glob.glob(os.path.join(d, f"{prefix}-*")):
            name = os.path.basename(fpath)
            suffix = name[len(prefix) + 1:]
            try:
                ver = int(suffix)
            except ValueError:
                continue
            if name not in seen:
                path = shutil.which(name)
                if path:
                    seen[name] = (path, ver)
    # Unversioned
    if prefix not in seen:
        path = shutil.which(prefix)
        if path:
            seen[prefix] = (path, -1)
    return [(name, path, ver) for name, (path, ver) in
            sorted(seen.items(), key=lambda x: -x[1][1])]


def _find_best_versioned(prefix: str) -> str | None:
    """Find the highest-versioned binary matching prefix (e.g. 'g++' -> 'g++-14')."""
    entries = _find_all_versioned(prefix)
    return entries[0][0] if entries else None


def _find_zig() -> str | None:
    """Find the zig binary on PATH or inside the ziglang PyPI package."""
    system, bundled = _find_all_zig()
    return system or bundled


def _find_all_zig() -> tuple[str | None, str | None]:
    """Find system and bundled zig binaries. Returns (system_path, bundled_path)."""
    system = shutil.which("zig")
    bundled = None
    try:
        import ziglang
        candidate = os.path.join(os.path.dirname(ziglang.__file__), "zig")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            # Don't count system zig as bundled
            if candidate != system:
                bundled = candidate
    except ImportError:
        pass
    return system, bundled


@dataclass(frozen=True)
class TargetPlatform:
    """How one build-target OS is named on each side of the build."""
    macro: str  # its TPY_PLATFORM_* macro (runtime/cpp/include/tpy/target_platform.hpp)
    cmake_system: str  # its CMAKE_SYSTEM_NAME
    triple_markers: tuple[str, ...]  # substrings of its target triples


# The build-target OSes, keyed by the `sys.platform` prefix CPython answers
# there (FreeBSD's value carries the major version, `freebsd14`). The one
# table: `__tpy__` "platform" filters, the include guards, the CMake link
# conditions and the host / compiler-target detection all read it.
PLATFORMS: dict[str, TargetPlatform] = {
    "linux": TargetPlatform("TPY_PLATFORM_LINUX", "Linux", ("linux",)),
    "darwin": TargetPlatform("TPY_PLATFORM_DARWIN", "Darwin", ("darwin", "apple", "macos")),
    "win32": TargetPlatform("TPY_PLATFORM_WIN32", "Windows", ("windows", "mingw", "msvc")),
    "freebsd": TargetPlatform("TPY_PLATFORM_FREEBSD", "FreeBSD", ("freebsd",)),
}


def platform_matches(platform_filter: str | None, target: str) -> bool:
    """True when a settings entry's `"platform"` admits the build `target`
    (a `PLATFORMS` key: `CppCompilerConfig.target_platform`)."""
    return platform_filter is None or platform_filter == target


# Targets whose triples also name a supported OS's marker (`linux`, `apple`)
# but that TPy does not support; target_platform.hpp defines no
# TPY_PLATFORM_* macro for them, so no "platform" filter may match either.
UNSUPPORTED_TRIPLE_OSES = ("android", "ios", "tvos", "watchos", "xros")


def _triple_to_os(triple: str) -> str:
    """Map a -dumpmachine target triple to a `PLATFORMS` key, 'unsupported'
    for a recognized target TPy does not support, or 'unknown'."""
    t = triple.lower()
    if any(part.startswith(UNSUPPORTED_TRIPLE_OSES) for part in t.split("-")):
        return "unsupported"
    return next((key for key, row in PLATFORMS.items()
                 if any(marker in t for marker in row.triple_markers)),
                "unknown")


def host_os() -> str:
    """This machine's `PLATFORMS` key, comparable to compiler_target_os()."""
    return next((key for key in PLATFORMS if sys.platform.startswith(key)),
                "unknown")


@functools.lru_cache(maxsize=None)
def compiler_dumpmachine(compiler: tuple[str, ...]) -> str:
    """The raw `-dumpmachine` target triple for a compiler command, or '' if
    the probe fails (cached per command)."""
    try:
        result = subprocess.run([*compiler, "-dumpmachine"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def compiler_target_os(compiler: tuple[str, ...]) -> str:
    """The OS (a `PLATFORMS` key) a compiler command TARGETS, probed via
    `-dumpmachine`. An osxcross clang++ answers darwin on a Linux host --
    how cross builds are detected without any flag. Falls back to the host
    OS when the probe fails or the triple is unrecognized (a compiler
    without -dumpmachine is assumed native)."""
    triple = compiler_dumpmachine(tuple(compiler))
    if not triple:
        return host_os()
    target = _triple_to_os(triple)
    return target if target != "unknown" else host_os()


# Oldest macOS TPy binaries support. libc++'s float std::to_chars (used by
# tpy/format.hpp and std::format) lives in the OS runtime and shipped in
# macOS 13.3; below that the SDK marks it unavailable. Without a pin, native
# clang defaults the deployment target to the host's OS version and osxcross
# to 11.0 -- one explicit floor makes availability checking identical
# everywhere. Bump deliberately when the runtime needs a newer OS API.
MACOS_VERSION_MIN = "13.3"


def darwin_version_min_flags(compiler: list[str], target: str) -> list[str]:
    """The deployment-target pin for a darwin `target`, [] for everything
    else (or when the command already carries its own pin). Carried on the
    compiler command itself so every driver invocation -- C++ and C
    compiles, PCH, link -- inherits it."""
    if any(a.startswith(("-mmacosx-version-min", "-mmacos-version-min"))
           for a in compiler):
        return []
    if target != "darwin":
        return []
    return [_DARWIN_VERSION_MIN_FLAG]


_DARWIN_VERSION_MIN_FLAG = f"-mmacosx-version-min={MACOS_VERSION_MIN}"


def darwin_cross_ld_flags(compiler: list[str], target: str) -> list[str]:
    """Point a darwin-cross compiler at the toolchain's Mach-O linker.

    A cross build's clang driver searches for `ld` at link time and would
    fall through to the host's ELF binutils `ld`, which rejects ld64's
    -arch/-platform_version flags ("unrecognised emulation mode"). Cross
    toolchains ship the matching linker as a triple-prefixed sibling of the
    driver (`<dir>/<triple>-ld`); pass it via --ld-path so the link uses
    ld64. [] when no such sibling exists (a native mac's clang finds ld64 on
    its own; the sibling only sits next to a cross driver), the driver isn't
    an absolute path, or an explicit linker is already selected."""
    if any(a.startswith("--ld-path") or a.startswith("-fuse-ld")
           for a in compiler):
        return []
    if target != "darwin":
        return []
    dirname = os.path.dirname(compiler[0])
    if not dirname:
        return []
    triple = compiler_dumpmachine(tuple(compiler))
    if not triple:
        return []
    ld = os.path.join(dirname, f"{triple}-ld")
    if not (os.path.isfile(ld) and os.access(ld, os.X_OK)):
        return []
    return [f"--ld-path={ld}"]


def shared_link_flags(target: str) -> list[str]:
    """Link flags that turn a link step into a loadable CPython extension.

    ELF resolves the facade's undefined `Py*` symbols against the host
    interpreter at import, so `-shared` alone suffices. Mach-O will not leave
    them undefined -- plain `-shared` fails the link outright -- so darwin
    needs `-bundle -undefined dynamic_lookup`, which is exactly what CPython's
    own `LDSHARED` uses there. Function AND data symbols (`_Py_NoneStruct`,
    the `PyExc_*` pointers) both resolve at load under it.

    Keyed on the build's TARGET os (`CppCompilerConfig.target_platform`),
    so a native mac build and an osxcross cross-build emit the same line
    (`darwin_cross_ld_flags` above keys the same way, at the same link
    site).
    """
    if target == "darwin":
        return ["-bundle", "-undefined", "dynamic_lookup"]
    return ["-shared"]


def _resolve_compiler(cxx: str) -> list[str] | None:
    """Resolve a --cxx value to a compiler command list, or None if not found.

    Accepted forms:
      gcc, g++                -> best versioned g++
      gcc-14, g++-14          -> specific version
      clang, clang++          -> best versioned clang++
      clang-19, clang++-19    -> specific version
      zig                     -> zig c++
      /path/to/compiler       -> literal path
      any-binary-name         -> looked up on PATH
    """
    # Path (absolute or relative with /)
    if "/" in cxx:
        if os.path.isfile(cxx) and os.access(cxx, os.X_OK):
            return [cxx]
        return None

    # Family aliases pick the best VIABLE version (self-heal on mixed
    # installs); a specific version (gcc-12) is honored and rejected
    # loudly by the capability check if non-viable.
    if cxx in ("gcc", "g++"):
        return _find_best_viable("g++")
    if cxx in ("clang", "clang++"):
        return _find_best_viable("clang++")
    if cxx == "zig":
        zig = _find_zig()
        return [zig, "c++"] if zig else None
    if cxx == "zig-bundled":
        _, bundled = _find_all_zig()
        return [bundled, "c++"] if bundled else None

    # clang-repl is a REPL JIT backend, not a batch compiler
    if cxx == "clang-repl" or cxx.startswith("clang-repl-"):
        return None

    # gcc-14 -> g++-14, clang-19 -> clang++-19
    if cxx.startswith("gcc-"):
        binary = "g++-" + cxx[4:]
        if shutil.which(binary):
            return [binary]
        return None
    if cxx.startswith("clang-"):
        binary = "clang++-" + cxx[6:]
        if shutil.which(binary):
            return [binary]
        return None

    # Already a binary name (g++-14, clang++-19, etc.)
    if shutil.which(cxx):
        return [cxx]
    return None


def _auto_detect_compiler() -> list[str]:
    """Auto-detect the best available C++ compiler for building.

    Candidates are capability-probed (C++23); non-viable ones (e.g. a
    sub-13 g++) are skipped so a box with only an old system compiler
    falls through to a viable clang++ or the known-good zig toolchain.
    """
    rejected: list[str] = []
    for prefix in ["g++", "clang++"]:
        for name, _path, _ver in _find_all_versioned(prefix):
            if toolchain_is_viable([name]):
                return [name]
            rejected.append(name)
    zig = _find_zig()
    if zig:
        return [zig, "c++"]
    if rejected:
        raise ToolchainUnsupportedError(
            [rejected[0]], also_rejected=tuple(rejected[1:]))
    return ["g++"]


def _is_zig(compiler: list[str]) -> bool:
    return "zig" in os.path.basename(compiler[0])


# Search-path variables the compiler driver and linker read relative to
# their cwd.
_BUILD_PATH_ENV = ("CPATH", "CPLUS_INCLUDE_PATH", "C_INCLUDE_PATH", "LIBRARY_PATH")


def _shares_build_dir(compiler: list[str], extra_flags: list[str]) -> bool:
    """True when nothing in the compiler command, `extra_flags` or the
    search-path environment came from the user: a bare compiler name PATH
    resolves to an absolute path, or an absolute one, followed only by the
    words tpyc itself adds (zig's `c++`, the darwin version pin). The
    user's settings are never parsed; any of them opts out."""
    if not compiler or extra_flags:
        return False
    if any(os.environ.get(name) for name in _BUILD_PATH_ENV):
        return False
    head, *rest = compiler
    if os.sep in head and not os.path.isabs(head):
        return False
    if os.sep not in head:
        # A relative PATH element (`.`, an empty one) finds a bare name
        # relative to the cwd.
        found = shutil.which(head)
        if found is None or not os.path.isabs(found):
            return False
    if _is_zig(compiler) and rest[:1] == ["c++"]:
        rest = rest[1:]
    return rest in ([], [_DARWIN_VERSION_MIN_FLAG])


def shared_cache_root() -> Path:
    """Shared cache root: $TPYC_SHARED_CACHE_DIR override, else XDG Base
    Directory / %LOCALAPPDATA%.

    Single source of truth -- the test harness derives its stdlib-objs/ /
    pch/ / exec-results/ cache root from this too, so probe markers live
    next to them and an override redirects everything together.
    """
    override = os.environ.get("TPYC_SHARED_CACHE_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "tpyc"
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "tpyc" / "cache"
    return Path.home() / ".cache" / "tpyc"


# --- shared cache eviction ---------------------------------------------------
# Keys of the stdlib-object and PCH caches hash the compiler source and the
# runtime headers, so every edit adds a new entry beside the old ones; nothing
# else ever removes them.

CACHE_MAX_AGE_ENV = "TPYC_CACHE_MAX_AGE"
DEFAULT_CACHE_MAX_AGE = 3 * 86400
EXEC_MARKER_MAX_AGE = 30 * 86400
CACHE_SWEEP_INTERVAL = 3600
CACHE_USED_MARK = ".used"
# The cache layout, read by the producers, `--clean` and the sweep. Each
# entry kind: its directory under the root, and a file the loader reads on
# every use, whose atime covers entries no session marked.
STDLIB_OBJS_DIR = "stdlib-objs"
PCH_DIR = "pch"  # shared_pch's per-key entries, for the CLI, the REPL and the harness
EXEC_RESULTS_DIR = "exec-results"
CACHE_ENTRY_KINDS = ((STDLIB_OBJS_DIR, "metadata.json"), (PCH_DIR, "tpy_pch.hpp.gch"))
CACHE_DIRS = (STDLIB_OBJS_DIR, PCH_DIR, EXEC_RESULTS_DIR)
UNMARKED_MIN_AGE = 14 * 86400
# The names the producers write: sha256 keys, the REPL's former
# `repl-<md5[:12]>` (kept so leftover ones age out), exec markers (and their pid-tagged temp files); anything else under the
# root is not the cache's to remove.
_CACHE_ENTRY_NAME = re.compile(r"[0-9a-f]{64}|repl-[0-9a-f]{12}")
_CACHE_TRASH_NAME = re.compile(r"\.trash-(?:[0-9a-f]{64}|repl-[0-9a-f]{12})-[0-9]+")
_EXEC_MARKER_NAME = re.compile(r"[0-9a-f]{64}(?:\.[0-9]+\.tmp)?")


@dataclass
class CacheSweep:
    entry_age: int  # seconds unused after which an entry went
    marker_age: int  # the same for an exec-result marker
    removed: int = 0  # cache entries
    markers: int = 0  # exec-result markers
    freed: int = 0  # bytes
    failed: list[str] = field(default_factory=list)


def cache_max_age() -> int:
    """`$TPYC_CACHE_MAX_AGE` in seconds: "<n>d", or 0 for never. Whole days
    only: a session marks its entries when it starts, so an age shorter than
    the longest session would sweep an entry still in use."""
    value = os.environ.get(CACHE_MAX_AGE_ENV, "").strip()
    if not value:
        return DEFAULT_CACHE_MAX_AGE
    if value == "0":
        return 0
    if not (value.endswith("d") and value[:-1].isdigit()):
        raise ValueError(f'{CACHE_MAX_AGE_ENV} must be "<n>d" (days) or 0, got {value!r}')
    return int(value[:-1]) * 86400


def mark_cache_entry_used(entry: Path) -> None:
    """Record that this session uses `entry`; best effort."""
    with contextlib.suppress(OSError):
        if entry.is_dir():
            (entry / CACHE_USED_MARK).touch()


def _expired(entry: Path, read_file: str, now: float, max_age: int) -> bool:
    """Unused for `max_age` by the session mark. An entry no session has
    marked yet (built or used by an older harness) goes by the loader's read
    of its file and its own mtime instead, and gets at least
    UNMARKED_MIN_AGE: atime does not advance on a noatime mount."""
    times = [entry.stat().st_mtime]
    try:
        times.append((entry / CACHE_USED_MARK).stat().st_mtime)
    except OSError:
        max_age = max(max_age, UNMARKED_MIN_AGE)
        with contextlib.suppress(OSError):
            times.append((entry / read_file).stat().st_atime)
    return now - max(times) > max_age


def _disk_size(path: str | Path) -> int:
    st = os.lstat(path)
    blocks = getattr(st, "st_blocks", None)  # POSIX only
    return st.st_size if blocks is None else blocks * 512


def _tree_size(path: Path) -> int:
    size = 0
    for dirpath, _, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                size += _disk_size(os.path.join(dirpath, name))
    return size


def _remove_entry(entry: Path, sweep: CacheSweep, used_after: float) -> bool:
    """Rename right after the age check, so a reader sees the entry whole or
    not at all; an entry whose build lock is held is being built and stays,
    and one marked used after `used_after` (since the age check) is revived
    and stays. A trash dir an interrupted sweep left behind goes on the next
    one."""
    trash = entry.with_name(f".trash-{entry.name}-{os.getpid()}")
    try:
        with open(entry / ".lock", "a") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Only the mark: creating .lock just moved the entry's own mtime.
            with contextlib.suppress(OSError):
                if (entry / CACHE_USED_MARK).stat().st_mtime > used_after:
                    return False
            entry.rename(trash)
            # A lock-free reader (shared_pch's fast path) may have marked and
            # accepted the entry between the check above and the rename; its
            # fresh mark sits in the trash now. Not a complete fix: a reader
            # accepting it between the two renames loses its -include file for
            # one build, and so does one whose entry another builder recreated
            # first (the rename back fails and the trash goes; an already
            # opened .gch survives the unlink).
            with contextlib.suppress(OSError):
                if (trash / CACHE_USED_MARK).stat().st_mtime > used_after:
                    trash.rename(entry)
                    return False
    except OSError:
        sweep.failed.append(entry.name)
        return False
    return _remove_trash(trash, sweep)


def _remove_trash(trash: Path, sweep: CacheSweep) -> bool:
    size = _tree_size(trash)
    try:
        shutil.rmtree(trash)
    except OSError:
        sweep.failed.append(trash.name)
        return False
    sweep.freed += size
    return True


def _remove_orphan_pch_temps(entry: Path, sweep: CacheSweep, written_before: float) -> None:
    """Temp files a killed builder left in an entry that stays: a ready
    entry is used lock-free, so no builder under its lock comes along to
    remove them (`_remove_pch_temp_files`). A live builder's temp is
    minutes old, never `written_before`."""
    with contextlib.suppress(OSError):
        for p in entry.iterdir():
            if not _PCH_TEMP_NAME.fullmatch(p.name):
                continue
            with contextlib.suppress(OSError):
                st = p.lstat()
                if stat.S_ISREG(st.st_mode) and st.st_mtime < written_before:
                    size = _disk_size(p)
                    p.unlink()
                    sweep.freed += size


def sweep_shared_cache(root: Path | None = None, *, now: float | None = None,
                       force: bool = False) -> CacheSweep | None:
    """Remove cache entries unused for `cache_max_age()`, PCH build temp
    files older than that in the entries that stay, and exec-result markers
    unused for 30 days. Only names the cache itself writes are
    touched, whatever else the root holds. At most once per hour per cache
    root (a stamp), by one process at a time (a non-blocking lock); None
    when it did not run. Raises ValueError for a malformed
    `$TPYC_CACHE_MAX_AGE`."""
    max_age = cache_max_age()
    root = root or shared_cache_root()
    if not max_age or not root.is_dir():
        return None
    now = time.time() if now is None else now
    stamp = root / ".last-sweep"
    # "a": a lock file that turns out to be a link must not lose its target's contents
    with open(root / ".sweep.lock", "a") as lock:
        if fcntl is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return None
        with contextlib.suppress(OSError):
            if not force and now - stamp.stat().st_mtime < CACHE_SWEEP_INTERVAL:
                return None
        stamp.touch()
        sweep = CacheSweep(entry_age=max_age, marker_age=max(max_age, EXEC_MARKER_MAX_AGE))
        for kind, read_file in CACHE_ENTRY_KINDS:
            for entry in sorted((root / kind).glob("*")):
                if not entry.is_dir() or entry.is_symlink():
                    continue
                if _CACHE_TRASH_NAME.fullmatch(entry.name):
                    _remove_trash(entry, sweep)
                    continue
                if not _CACHE_ENTRY_NAME.fullmatch(entry.name):
                    continue
                with contextlib.suppress(OSError):
                    if (_expired(entry, read_file, now, max_age)
                            and _remove_entry(entry, sweep, now - max_age)):
                        sweep.removed += 1
                        continue
                if kind == PCH_DIR:
                    _remove_orphan_pch_temps(entry, sweep, now - max_age)
        for marker in sorted((root / EXEC_RESULTS_DIR).glob("*")):
            if not _EXEC_MARKER_NAME.fullmatch(marker.name) or marker.is_symlink():
                continue
            with contextlib.suppress(OSError):
                st = marker.stat()
                if stat.S_ISREG(st.st_mode) and now - st.st_mtime > sweep.marker_age:
                    size = _disk_size(marker)
                    marker.unlink()
                    sweep.markers += 1
                    sweep.freed += size
        return sweep


def sweep_shared_cache_best_effort(report: Callable[[str], None],
                                   root: Path | None = None) -> CacheSweep | None:
    """`sweep_shared_cache` for a caller that must never fail or wait on it:
    a malformed `$TPYC_CACHE_MAX_AGE` is passed to `report` as one line and
    the sweep is skipped. An OSError skips it silently: a read-only cache
    root would otherwise warn on every build."""
    try:
        return sweep_shared_cache(root)
    except ValueError as exc:
        report(f"cache sweep skipped: {exc}")
    except OSError:
        pass
    return None


# Known force-enable for clang < 19 + libstdc++ (compile-verified):
# -D__cpp_concepts=202002L unlocks libstdc++'s <expected>; deliberately
# not suggested in the diagnostic -- an explicit --cxx selection already
# proceeds past the probe for users who wire that up themselves.
#
# The minimum surface TPy-generated code needs: the CONTENTS of every
# C++23 header the runtime includes (<expected>, <ranges>, <format> --
# each with a real use: clang 18 + libstdc++ ships the <expected> header
# but its __cpp_concepts value keeps the contents preprocessed away, and
# g++-12 has <expected> but not <format>) and the GCC statement-expression
# extension. Keep in sync with the runtime's include floor.
_PROBE_SOURCE = """\
#include <expected>
#include <format>
#include <ranges>
int main() {
    std::expected<int, int> e{1};
    auto v = std::views::iota(0, 3);
    auto f = std::format("{}", *v.begin());
    int s = ({ int x = e.value_or(0); x; });
    return s + static_cast<int>(f.size());
}
"""


class FileIdentity(NamedTuple):
    """The one rule by which the build caches tell a file is untouched.
    mtime alone is not enough: tar, `cp -p` and package managers restore
    it, while ctime moves on every content change, rename or replacement
    and no tool can set it back, and a replaced file has a new inode.
    mtime stays for Windows, where st_ctime is the creation time."""
    ino: int
    ctime_ns: int
    mtime_ns: int
    size: int


def file_identity(st: os.stat_result) -> FileIdentity:
    return FileIdentity(st.st_ino, st.st_ctime_ns, st.st_mtime_ns, st.st_size)


def compiler_binary_identity(
        compiler: list[str]) -> tuple[str, FileIdentity] | None:
    """(resolved path, `file_identity`) of the compiler binary.

    The one identity notion for the `pch_entry` key, the toolchain probe cache and
    build_cache.toolchain_entry: a binary replaced at the same path is a
    different compiler, even with its old mtime restored; an untouched one
    never is. None when the binary cannot be resolved at all.
    """
    argv0 = compiler[0]
    if os.path.sep in argv0:
        resolved = argv0 if os.path.isfile(argv0) else None
    else:
        resolved = shutil.which(argv0)
    if not resolved:
        return None
    try:
        st = os.stat(resolved)
    except OSError:
        return None
    return resolved, file_identity(st)


def _toolchain_probe_id(compiler: list[str]) -> str | None:
    """Probe-cache key: compiler binary identity + probe source + flags."""
    ident = compiler_binary_identity(compiler)
    if ident is None:
        return None
    resolved, identity = ident
    key = "\0".join(
        [resolved, *map(str, identity), *compiler[1:], _PROBE_SOURCE])
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _probe_toolchain(compiler: list[str]) -> tuple[bool, Path | None]:
    """Cached C++23 capability probe. Returns (viable, failure_log_path).

    zig is exempt (its bundled libc++ is known-good); an unresolvable
    binary reports viable so the build path raises its own not-found
    error instead of a misleading capability message.
    """
    if _is_zig(compiler):
        return True, None
    probe_id = _toolchain_probe_id(compiler)
    if probe_id is None:
        return True, None
    cache_dir = shared_cache_root() / "toolchain-probes"
    ok = cache_dir / f"{probe_id}.ok"
    fail = cache_dir / f"{probe_id}.fail"
    log = cache_dir / f"{probe_id}.log"
    if ok.exists():
        return True, None
    if fail.exists():
        return False, log if log.exists() else None

    with tempfile.TemporaryDirectory(prefix="tpyc-probe-") as td:
        src = Path(td) / "probe.cpp"
        src.write_text(_PROBE_SOURCE)
        try:
            r = subprocess.run(
                [*compiler, "-std=c++23", "-fsyntax-only", str(src)],
                capture_output=True, text=True, timeout=60,
            )
            viable = r.returncode == 0
            log_text = "" if viable else (r.stderr or "") + (r.stdout or "")
        except (OSError, subprocess.TimeoutExpired) as e:
            viable, log_text = False, str(e)
    # An unwritable cache root (read-only HOME/XDG_CACHE_HOME) must not
    # turn the probe into a crash -- degrade to probe-without-caching.
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        if viable:
            ok.touch()
        else:
            log.write_text(log_text)
            fail.touch()
    except OSError:
        return viable, None
    return viable, (None if viable else log)


def toolchain_is_viable(compiler: list[str]) -> bool:
    return _probe_toolchain(compiler)[0]


def _probe_is_cached(compiler: list[str]) -> bool:
    """True when calling the probe would not compile anything."""
    if _is_zig(compiler):
        return True
    probe_id = _toolchain_probe_id(compiler)
    if probe_id is None:
        return True
    cache_dir = shared_cache_root() / "toolchain-probes"
    return ((cache_dir / f"{probe_id}.ok").exists()
            or (cache_dir / f"{probe_id}.fail").exists())


def _find_best_viable(prefix: str) -> list[str] | None:
    """Best-versioned binary that passes the capability probe; falls back
    to the best-versioned one so the capability diagnostic (not a
    misleading not-found) reports when none is viable."""
    entries = _find_all_versioned(prefix)
    for name, _path, _ver in entries:
        if toolchain_is_viable([name]):
            return [name]
    return [entries[0][0]] if entries else None


def warn_toolchain_unsupported(compiler: list[str]) -> None:
    """Warn (stderr) when an EXPLICITLY selected compiler fails the probe.

    An explicit --cxx/$CXX choice is honored -- warn and proceed, so the
    user keeps an escape hatch past the probe and the build failure that
    likely follows has an explanation above it. Only auto-detect (which
    has alternatives to fall through to) hard-rejects.
    """
    viable, log = _probe_toolchain(compiler)
    if not viable:
        err = ToolchainUnsupportedError(compiler, log)
        msg = (f"Warning: {err}\nProceeding with the explicit selection; "
               "the build will likely fail.")
        # Per-line prefix so the block stands out above the build output
        # (and the compile-error wall that likely follows).
        print("\n".join(f"!! {line}" for line in msg.splitlines()),
              file=sys.stderr)


def third_party_source_driver(src: Path, cxx: list[str], std: str) -> list[str]:
    """Compiler driver for one bundled third-party source file.

    `.cpp` sources (e.g. Hinnant date's tz.cpp and the datetime tz shim)
    use the C++ driver with the configured -std; anything else uses the C
    compiler derived from it (C++ drivers reject implicit ``void*``
    conversions that PCRE2 relies on). Shared by `build_cpp_commands` and
    the test harness's stdlib-cache pre-compile so the dispatch rule can't
    drift between the two build paths.
    """
    if src.suffix == ".cpp":
        return [*cxx, f"-std={std}"]
    return _derive_c_compiler(cxx)


def _derive_c_compiler(cxx: list[str]) -> list[str]:
    """Derive the matching C compiler command from a C++ compiler command.

    Used when building bundled C dependencies (e.g. PCRE2). The C and C++
    drivers share a toolchain but differ in default language: g++ would
    reject PCRE2's implicit ``void*`` conversions that gcc accepts.

    Mappings:
      ['g++']            -> ['gcc']
      ['g++-14']         -> ['gcc-14']
      ['clang++']        -> ['clang']
      ['clang++-19']     -> ['clang-19']
      ['zig', 'c++']     -> ['zig', 'cc']
      ['/p/g++-14']      -> ['/p/gcc-14']
      ['/p/arm64-apple-darwin25.5-clang++-19']
                         -> ['/p/arm64-apple-darwin25.5-clang-19']

    Falls back to the original command if the pattern isn't recognized or
    the derived C driver doesn't exist (e.g. an unusual binary name, or a
    cross toolchain shipping only the C++ wrapper); the C++ driver may
    still compile most C correctly even if it grumbles.
    """
    if not cxx:
        return cxx
    head = cxx[0]
    rest = cxx[1:]
    # Zig: ['zig', 'c++'] -> ['zig', 'cc']
    if _is_zig(cxx) and rest and rest[0] == "c++":
        return [head, "cc", *rest[1:]]
    base = os.path.basename(head)
    dirname = os.path.dirname(head)
    # Substring, not prefix: cross toolchains prefix the target triple
    # (osxcross: arm64-apple-darwin25.5-clang++-19). clang++ first --
    # it contains "g++" as a substring.
    if "clang++" in base:
        c_base = base.replace("clang++", "clang", 1)
    elif "g++" in base:
        c_base = base.replace("g++", "gcc", 1)
    else:
        return cxx
    new_head = os.path.join(dirname, c_base) if dirname else c_base
    exists = (os.access(new_head, os.X_OK) if dirname
              else shutil.which(new_head) is not None)
    if not exists:
        return cxx
    return [new_head, *rest]


def _cxx_aliases(binary: str, is_best: bool, family_prefix: str) -> list[str]:
    """Compute --cxx aliases for a compiler binary.

    E.g. g++-14 (best) -> gcc, gcc-14; g++-13 -> gcc-13
    """
    aliases: list[str] = []
    # gcc/clang short alias only for the best version
    if is_best:
        if family_prefix == "g++":
            aliases.append("gcc")
        elif family_prefix == "clang++":
            aliases.append("clang")
    # Versioned alias: g++-14 -> gcc-14, clang++-19 -> clang-19
    if "-" in binary:
        ver = binary.split("-", 1)[1]
        if family_prefix == "g++":
            aliases.append(f"gcc-{ver}")
        elif family_prefix == "clang++":
            aliases.append(f"clang-{ver}")
    # The binary name itself
    aliases.append(binary)
    return aliases


def list_compilers() -> None:
    """Print available C++ compilers to stdout."""
    probe_dir = shared_cache_root() / "toolchain-probes"
    family_rows = [(prefix, _find_all_versioned(prefix))
                   for prefix in ["g++", "clang++"]]
    if any(not _probe_is_cached([name])
           for _prefix, rows in family_rows for name, _p, _v in rows):
        print(f"Probing C++ toolchains (one-time per compiler; cached in "
              f"{probe_dir}) ...", flush=True)

    try:
        auto = _auto_detect_compiler()
        auto_display = os.path.basename(auto[0])
        if len(auto) > 1:
            auto_display += " " + " ".join(auto[1:])
    except ToolchainUnsupportedError:
        auto_display = None

    entries: list[tuple[str, list[str], str]] = []      # (binary, aliases, note)
    unsupported: list[tuple[str, list[str]]] = []       # (binary, aliases)

    for prefix, rows in family_rows:
        viability = [toolchain_is_viable([name]) for name, _p, _v in rows]
        # The family alias (gcc / clang) sits on the row _resolve_compiler
        # actually picks -- same helper, so the two cannot desync.
        best = _find_best_viable(prefix)
        best_name = best[0] if best else None
        for i, (name, path, _ver) in enumerate(rows):
            aliases = _cxx_aliases(name, is_best=(name == best_name),
                                   family_prefix=prefix)
            if viability[i]:
                entries.append((name, aliases, ""))
            else:
                unsupported.append((name, aliases))

    # clang-repl is a JIT with its own CLI -- the batch probe doesn't apply.
    for i, (name, path, _ver) in enumerate(_find_all_versioned("clang-repl")):
        if i == 0:
            aliases = ["clang-repl", name] if name != "clang-repl" else ["clang-repl"]
        else:
            aliases = [name]
        entries.append((name, aliases, "REPL JIT"))

    system_zig, bundled_zig = _find_all_zig()
    if system_zig:
        entries.append(("zig c++", ["zig"], "system"))
    if bundled_zig:
        aliases = ["zig", "zig-bundled"] if not system_zig else ["zig-bundled"]
        entries.append(("zig c++", aliases, "bundled"))

    if not entries and not unsupported:
        print("No C++ compilers found.")
        print("Install g++, clang++, or: uv tool install \"tpy-lang[bundled]\"")
        return

    name_width = max(len(b) for b, _, _ in entries) if entries else 0
    if unsupported:
        name_width = max(name_width, max(len(b) for b, _ in unsupported))

    if entries:
        print("Available C++ compilers:")
        for binary, aliases, note in entries:
            marker = "*" if binary == auto_display else " "
            parts = []
            if aliases:
                parts.append("--cxx " + ", ".join(aliases))
            if note:
                parts.append(f"({note})")
            print(f"  {marker} {binary:<{name_width}}  {'  '.join(parts)}")
    if unsupported:
        print("\nUnsupported (cannot compile TurboPython's C++23 "
              "requirements):")
        for binary, aliases in unsupported:
            detail = "--cxx " + ", ".join(aliases) if aliases else ""
            print(f"    {binary:<{name_width}}  {detail}")

    if auto_display is not None:
        print(f"\nDefault (--cxx auto): {auto_display}")
    else:
        print("\nDefault (--cxx auto): none viable -- install g++ >= 13, "
              "clang++ >= 19, or \"tpy-lang[bundled]\"")
    print(f"Probe results cached in: {probe_dir}  (delete to re-probe)")
    print("A path to any C++ compiler binary is also accepted.")


def discover_runtime_cpp_sources(runtime_cpp_dir: Path) -> list[Path]:
    """List TPy-owned .cpp files under `runtime_cpp_dir/src/` (recursively).

    These are compiled and linked into every TPy binary alongside user-
    generated .cpp files. They exist for stdlib helpers that need direct
    access to C system struct layouts (e.g. sockets / getaddrinfo) and
    can't be expressed header-only without leaking system-header macros
    into downstream TUs. Returns [] if the directory doesn't exist.

    `*_shim.*` files are third-party glue owned by a managed lib
    (mbedtls_shim.c, date_shim.cpp): they include vendored headers, so
    they are compiled by their lib's build factory (tpyc/build/<lib>.py)
    with the lib's flags, only when a module declares the dep -- never
    linked into every binary. (.c files are skipped by the glob anyway;
    the explicit filter keeps the .cpp shims out too.)
    """
    src_dir = runtime_cpp_dir / "src"
    if not src_dir.is_dir():
        return []
    return sorted(p for p in src_dir.rglob("*.cpp")
                  if not p.stem.endswith("_shim"))


def pch_is_path_sensitive(config: CppCompilerConfig) -> bool:
    """True when a .gch is only valid for the -I root it was built against.

    Clang tracks `#pragma once` by file identity, so headers reached through a
    second checkout's -I are re-parsed and redefine everything the .gch already
    holds; GCC's libcpp compares contents instead. Unrecognized families are
    treated as sensitive. Callers sharing a .gch across checkouts must fold the
    include root into their cache key when this is True.
    """
    return _detect_compiler_family(tuple(config.compiler)) != "gcc"


def _pch_family_flags(config: CppCompilerConfig) -> list[str]:
    # Clang embeds the input header's mtime in the .gch and rejects the PCH
    # if it differs at consume time, even when content is byte-identical. The
    # cache that stores this .gch is content-addressed, so a fresh checkout
    # that only bumps tpy.hpp's mtime would invalidate every cached clang PCH.
    # -fno-pch-timestamp drops the timestamp so validation falls to content
    # (what ccache does); GCC already validates by content and needs nothing.
    if _detect_compiler_family(tuple(config.compiler)) == "clang":
        return ["-Xclang", "-fno-pch-timestamp"]
    return []


def _pch_build_flags(config: CppCompilerConfig, opt_flags: list[str]) -> list[str]:
    """The PCH compile command up to (excluding) the include root and the
    input/output paths."""
    return [
        *config.compiler, f"-std={config.std}",
        *config.extra_flags,
        *config.warn_flags,
        *opt_flags,
        *_pch_family_flags(config),
    ]


PCH_HEADER = "tpy_pch.hpp"
PCH_GCH = PCH_HEADER + ".gch"
PCH_DEPS = "deps"
PCH_DEPFILE = "deps.d"  # the compiler's make-style depfile, only ever a temp
_PCH_HEADER_TEXT = "#include <tpy/tpy.hpp>\n"


def hash_files(paths: list[Path], root: Path) -> str:
    """Hash a sequence of files by (root-relative path, content).

    Paths are made relative to *root* so the hash is stable across worktrees
    / clone locations. Files outside it (rare) fall back to their absolute
    path.
    """
    h = hashlib.sha256()
    for p in paths:
        try:
            key = str(p.relative_to(root))
        except ValueError:
            key = str(p)
        h.update(key.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _runtime_headers_hash(runtime_include_dir: Path) -> str:
    # Editor droppings (`.#x.hpp` lock links, `#x.hpp#` autosaves) match the
    # suffix but are never included.
    files = sorted(p for p in runtime_include_dir.rglob("*")
                   if p.suffix in (".hpp", ".h") and p.is_file()
                   and not p.name.startswith((".", "#")))
    return hash_files(files, runtime_include_dir)


# Flags naming a header or a header-search path, joined (`-Iinc`) or
# separate (`-I inc`); `--sysroot` joins with `=` only. Longest first, so a
# flag is never read as a shorter one joined to its path.
_INCLUDE_PATH_FLAGS = tuple(sorted(
    ("-idirafter", "-isystem", "-include", "-imacros", "-iquote", "-iframework",
     "-isysroot", "-I", "-F", "--sysroot=", "--sysroot"),
    key=len, reverse=True))
# C_INCLUDE_PATH is read by C compiles only.
_INCLUDE_PATH_ENV = ("CPATH", "CPLUS_INCLUDE_PATH")


def absolute_path(path: str | Path) -> str:
    """`path` made absolute against the cwd, with its `..` kept: the kernel
    follows a symlink before `..`, so collapsing `link/..` lexically
    (`os.path.abspath`) names another directory."""
    return str(Path(path).absolute())


def _resolve_include_path(path: str) -> str:
    # resolve(), not abspath(): the compiler follows a symlink before `..`,
    # so `link/../inc` names a directory beside the link's target.
    return str(Path(path).resolve())


def _abs_include_flags(flags: list[str]) -> list[str]:
    """`flags` with every include-search path resolved against the cwd: the
    PCH compile resolves them there, so the same spelling in another
    directory names other headers."""
    out: list[str] = []
    expect_path = False
    for arg in flags:
        if expect_path:
            out.append(_resolve_include_path(arg))
            expect_path = False
            continue
        flag = next((f for f in _INCLUDE_PATH_FLAGS if arg.startswith(f)), None)
        if flag is None:
            out.append(arg)
        elif arg == flag and not flag.endswith("="):
            out.append(arg)
            expect_path = True
        else:
            out.append(flag + _resolve_include_path(arg[len(flag):]))
    return out


def _include_path_env() -> list[tuple[str, list[str]]]:
    """The set include-search environment variables, elements resolved
    against the cwd (an empty element names the cwd)."""
    return [(name, [_resolve_include_path(p) for p in os.environ[name].split(os.pathsep)])
            for name in _INCLUDE_PATH_ENV if name in os.environ]


def _pch_entry_for(config: CppCompilerConfig, runtime_include_dir: Path,
                   opt_flags: list[str], headers_hash: str) -> Path:
    h = hashlib.sha256()
    h.update(repr((
        headers_hash,
        _abs_include_flags(_pch_build_flags(config, opt_flags)),
        _include_path_env(),
        compiler_binary_identity(config.compiler),
        # A clang .gch only works from the -I root it was built against
        # (pch_is_path_sensitive); a GCC one is shared across checkouts.
        str(runtime_include_dir.resolve()) if pch_is_path_sensitive(config) else None,
    )).encode())
    return shared_cache_root() / PCH_DIR / h.hexdigest()


def pch_entry(config: CppCompilerConfig, runtime_include_dir: Path,
              opt_flags: list[str]) -> Path:
    """The shared-cache dir holding the PCH for this configuration: keyed on
    the runtime headers' content, the PCH build command, the compiler binary
    and, where the .gch is path-sensitive, the include root."""
    return _pch_entry_for(config, runtime_include_dir, opt_flags,
                          _runtime_headers_hash(runtime_include_dir))


def _parse_depfile(text: str) -> list[str]:
    """The prerequisites of a make-style depfile (`-MD -MF`)."""
    text = text.replace("\\\r\n", " ").replace("\\\n", " ")
    _, sep, rest = text.partition(": ")
    if not sep:
        return []
    deps: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(rest):
        c = rest[i]
        if c == "\\" and i + 1 < len(rest) and rest[i + 1] in " #":
            cur.append(rest[i + 1])
            i += 2
            continue
        if c == "$" and rest.startswith("$$", i):
            cur.append("$")
            i += 2
            continue
        if c.isspace():
            if cur:
                deps.append("".join(cur))
                cur = []
        else:
            cur.append(c)
        i += 1
    if cur:
        deps.append("".join(cur))
    return deps


class _Dep(NamedTuple):
    """An external dependency the .gch was built from."""
    identity: FileIdentity
    path: str

    @classmethod
    def of(cls, path: str) -> _Dep:
        return cls(file_identity(os.stat(path)), path)


def _external_deps(depfile: Path, runtime_include_dir: Path) -> list[_Dep]:
    """Every dependency outside the runtime headers, which the key already
    covers by content."""
    root = runtime_include_dir.resolve()
    deps = []
    for dep in dict.fromkeys(_parse_depfile(depfile.read_text())):
        # Joined, not normalized: `..` after a symlink is the kernel's to resolve.
        path = os.path.join(os.getcwd(), dep)
        if Path(path).resolve().is_relative_to(root):
            continue
        deps.append(_Dep.of(path))
    return deps


def _render_deps(deps: list[_Dep]) -> str:
    return "".join(f"{' '.join(map(str, d.identity))} {d.path}\n" for d in deps)


def _read_deps(entry: Path) -> list[_Dep]:
    """The entry's `deps` list; ValueError on a malformed line."""
    deps = []
    for line in (entry / PCH_DEPS).read_text().splitlines():
        *identity, path = line.split(" ", 4)
        if len(identity) != len(FileIdentity._fields):
            raise ValueError(f"malformed deps line: {line!r}")
        deps.append(_Dep(FileIdentity(*map(int, identity)), path))
    return deps


def pch_ready(entry: Path) -> bool:
    """The entry holds a published .gch and no header outside the runtime
    changed since it was built: a system or toolchain header upgrade under
    an unchanged compiler binary would otherwise be consumed stale."""
    try:
        if not ((entry / PCH_GCH).is_file() and (entry / PCH_HEADER).is_file()):
            return False
        return all(_Dep.of(dep.path) == dep for dep in _read_deps(entry))
    except (OSError, ValueError):
        return False


def _temp_path(path: Path) -> Path:
    """The one name of a temp file written to replace `path`: matched by
    `_PCH_TEMP_NAME`."""
    return path.with_name(f"{path.name}.{os.getpid()}.tmp")


def _replace_text(path: Path, text: str) -> None:
    tmp = _temp_path(path)
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _build_pch(config: CppCompilerConfig, runtime_include_dir: Path,
               opt_flags: list[str], entry: Path, headers_hash: str) -> Path | None:
    header = entry / PCH_HEADER
    try:
        current = header.read_text()
    except OSError:
        current = None
    # Rewritten only when it differs: readers may be force-including it now.
    if current != _PCH_HEADER_TEXT:
        _replace_text(header, _PCH_HEADER_TEXT)
    gch_tmp = _temp_path(entry / PCH_GCH)
    depfile_tmp = _temp_path(entry / PCH_DEPFILE)
    try:
        # The build's start on the kernel's own file clock, which can lag
        # time.time_ns() by a tick: a header changed after this has a ctime
        # at or after it.
        depfile_tmp.write_text("")
        started_ns = depfile_tmp.stat().st_ctime_ns
        cmd = [
            *_pch_build_flags(config, opt_flags),
            "-I", str(runtime_include_dir),
            "-x", "c++-header", str(header), "-o", str(gch_tmp),
            "-MD", "-MF", str(depfile_tmp),
        ]
        if subprocess.run(cmd, capture_output=True, text=True).returncode != 0:
            return None
        # The key names the headers hashed before the build; a .gch built
        # from headers edited meanwhile must not be published under it.
        if _runtime_headers_hash(runtime_include_dir) != headers_hash:
            return None
        deps = _external_deps(depfile_tmp, runtime_include_dir)
        # The same for an external header changed during the build: its stat
        # would certify contents the compiler may not have read. A ctime past
        # now is clock skew, not a change, and must not refuse every build.
        now_ns = time.time_ns()
        # The entry's own header was rewritten just before the start and can
        # share its clock tick on a coarse-timestamp filesystem.
        own_header = header.resolve()
        if any(any(started_ns <= t <= now_ns
                   for t in (dep.identity.ctime_ns, dep.identity.mtime_ns))
               and Path(dep.path).resolve() != own_header for dep in deps):
            return None
        # No state may pair a .gch with deps that do not describe it, and
        # pch_ready requires deps: drop the old deps, publish the .gch, then
        # its deps. A failure in between leaves an entry that is not ready.
        (entry / PCH_DEPS).unlink(missing_ok=True)
        os.replace(gch_tmp, entry / PCH_GCH)
        _replace_text(entry / PCH_DEPS, _render_deps(deps))
        return header
    finally:
        gch_tmp.unlink(missing_ok=True)
        depfile_tmp.unlink(missing_ok=True)


# Every temp file the builder writes in an entry: `_temp_path` of a file the
# entry holds.
_PCH_TEMP_NAME = re.compile(
    "(?:" + "|".join(map(re.escape, (PCH_GCH, PCH_HEADER, PCH_DEPS, PCH_DEPFILE)))
    + r")\.[0-9]+\.tmp")


def _remove_pch_temp_files(entry: Path) -> None:
    """Temp files a builder killed mid-build left behind. Only the holder of
    the entry lock writes them, so under the lock none is live."""
    for p in entry.iterdir():
        if _PCH_TEMP_NAME.fullmatch(p.name):
            with contextlib.suppress(OSError):
                p.unlink()


def shared_pch(config: CppCompilerConfig, runtime_include_dir: Path,
               opt_flags: list[str], *, force: bool = False,
               on_build: Callable[[], None] | None = None) -> Path | None:
    """The precompiled runtime header for this configuration, from the
    shared cache (`pch_entry`), built there first when missing or stale.
    Returns the header to force-include (the .gch sits next to it), or None
    when it cannot be had -- a failed build, an unwritable cache root --
    and the caller compiles without a PCH. `force` rebuilds a ready entry;
    `on_build` runs just before a build starts."""
    try:
        headers_hash = _runtime_headers_hash(runtime_include_dir)
        entry = _pch_entry_for(config, runtime_include_dir, opt_flags, headers_hash)
        # The sweep renames an expired entry away under its lock; meeting
        # that once means the entry is recreated, never twice.
        for _ in range(2):
            mark_cache_entry_used(entry)
            if not force and pch_ready(entry):
                return entry / PCH_HEADER
            entry.mkdir(parents=True, exist_ok=True)
            mark_cache_entry_used(entry)
            lock_path = entry / ".lock"
            # "a": a lock file that turns out to be a link must not lose its target's contents
            with open(lock_path, "a") as lock:
                if fcntl is not None:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                held = os.fstat(lock.fileno())
                try:
                    current = os.stat(lock_path)
                except FileNotFoundError:
                    continue
                if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                    continue
                if fcntl is not None:
                    _remove_pch_temp_files(entry)
                if not force and pch_ready(entry):
                    return entry / PCH_HEADER
                if on_build is not None:
                    on_build()
                return _build_pch(config, runtime_include_dir, opt_flags, entry, headers_hash)
    except OSError:
        pass
    return None


def set_pch_ccache_sloppiness(config: CppCompilerConfig) -> None:
    """Let ccache hit on PCH-using compiles: without these it treats every
    one as a miss."""
    if not config.ccache:
        return
    slop = os.environ.get("CCACHE_SLOPPINESS", "")
    parts = {s.strip() for s in slop.split(",") if s.strip()}
    parts.update(("pch_defines", "time_macros"))
    os.environ["CCACHE_SLOPPINESS"] = ",".join(sorted(parts))


def build_step_cwd(config: CppCompilerConfig, base_dir: Path) -> Path | None:
    """The folder a build's compile and link steps run from: `base_dir` when
    `config.shares_build_dir`, else None -- the caller's cwd, where a
    relative path in the user's own compiler settings means what the user
    meant. Callers compute it once and pass it to both `run_build_step` and
    `BuildLayout.build_cpp_commands`."""
    return base_dir if config.shares_build_dir else None


def run_build_step(cmd: list[str],
                   cwd: Path | None) -> subprocess.CompletedProcess[str]:
    """Run a C/C++ compile or link command: from `cwd`, with CCACHE_BASEDIR
    set to it, when one is given (`build_step_cwd`); otherwise from the
    caller's cwd with the environment untouched.

    ccache rewrites the paths under CCACHE_BASEDIR relative to the working
    directory before hashing, so builds rooted at different paths (two
    programs' build dirs, two checkouts) hash the same generated or runtime
    TU alike and share its cache entry. `cwd` must be absolute (ccache
    ignores a relative one) and hold every path specific to this build.
    """
    if cwd is None:
        return subprocess.run(cmd, capture_output=True, text=True)
    env = {**os.environ, "CCACHE_BASEDIR": str(cwd)}
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env)


@contextlib.contextmanager
def compile_pool(n_jobs: int) -> Iterator[ThreadPoolExecutor]:
    """A thread pool for C++ compiles. A Ctrl-C inside the block drops the
    compiles still queued and waits only for those running; the pool's own
    exit would start every queued one first."""
    with ThreadPoolExecutor(max_workers=n_jobs) as pool:
        try:
            yield pool
        except KeyboardInterrupt:
            pool.shutdown(cancel_futures=True)
            raise


#: Recommended strict warning set for compiling tpy-generated code.
#: Mirrors the minimal subset we know downstream consumers run with -Werror.
#: The test suite turns these on (see tests/conftest.py); end-user CLI builds
#: do NOT enable them by default -- it's the consumer's choice whether to opt
#: in by feeding them via CXXFLAGS or a build-system flag.
#:
#: Notable choices:
#: - -Wno-unused-parameter: downstream suppresses; we may want to enable later
#:   if we want to be stricter than downstream (revisit if we add a sema pass
#:   that flags unread parameters at the source level).
#: - -Wshadow: NOT in this set. Record/dataclass constructors emit the
#:   idiomatic `T(P p) : p(p) {}` pattern, which always shadows. Downstream
#:   consumers compiling tpy-generated TUs should add -Wno-shadow per-TU.
#:
#: Family-specific suffixes follow: GCC-only and Clang-only flags split out
#: because each toolchain spells some warnings differently and emits noise
#: on patterns the other accepts. `strict_warn_flags(cxx)` returns the right
#: combined list given a compiler command.
_COMMON_WARN_FLAGS: list[str] = [
    "-Werror",
    "-Wall", "-Wextra",
    "-Wno-missing-field-initializers",
    "-Wno-unused-parameter",
    # Generated codegen artifacts that we accept as costing more than they
    # would be worth fixing piecemeal. Each has a different rationale -- see
    # the inventory in PR notes.
    "-Wno-unused-label",             # for/else and with/finally end labels
    "-Wno-unused-but-set-variable",  # tuple-unpack and error_return temps
    "-Wno-unused-but-set-parameter", # by-value params written via v.field = X but never read
    # User TPy code can write `x = compute()` followed by no read -- which
    # is silently allowed in Python. Per the "users never see C++ errors"
    # invariant we suppress at the C++ level. The user-facing signal lives
    # at the TPy layer; see TODO.md for a planned sema diagnostic.
    "-Wno-unused-variable",
    # Class-hygiene checks: zero hits today; cheap future-proofing.
    "-Wsign-compare",
    "-Wnon-virtual-dtor", "-Woverloaded-virtual",
    "-Wswitch-bool", "-Wsizeof-array-argument",
    "-Wsuggest-override",
    # Container indexing and other int32 / size_t crossings: codegen emits
    # explicit static_cast<size_t> at bounds-safe subscript sites and at
    # comprehension reserve / array-comp index sites; runtime headers cast
    # at audited internal boundaries.
    "-Wsign-conversion", "-Wconversion",
]

#: GCC-only diagnostics we want enabled. Apple clang rejects these as
#: -Wunknown-warning-option under -Werror.
_GCC_ONLY_WARN_FLAGS: list[str] = [
    "-Wbool-compare",
]

#: Clang flags codegen patterns that GCC silently accepts. Suppressing them
#: lets macOS dev builds match the GCC CI build instead of failing on noise.
#: TODO: revisit and either fix the codegen or split these into "real bugs"
#: and "harmless idioms".
_CLANG_ONLY_WARN_FLAGS: list[str] = [
    "-Wno-parentheses-equality",        # `if ((x == y))`: codegen wraps comparisons in parens
    "-Wno-pessimizing-move",            # `std::move(temp)` at construction sites
    "-Wno-shorten-64-to-32",            # std::size_t -> int32_t at varargs / comprehension boundaries
    "-Wno-tautological-overlap-compare",
    "-Wno-unused-lambda-capture",
    "-Wno-dangling-gsl",
    "-Wno-defaulted-function-deleted",
    "-Wno-float-conversion",            # implicit double -> bool in `if x` for float locals
    "-Wno-unused-value",                # `abs(0);` discards a const-attribute return
    "-Wno-self-assign",                 # `x = x` (legal Python no-op) lowers verbatim
    "-Wno-self-assign-field",           # `self.x = self.x` field self-assign, ditto
]


@functools.lru_cache(maxsize=8)
def _detect_compiler_family(cxx: tuple[str, ...]) -> str:
    """Returns 'gcc', 'clang', or 'unknown' by probing the compiler.

    Apple distributes their clang under the `g++` name, so basename matching
    misclassifies it. Running `--version` disambiguates reliably.
    """
    try:
        out = subprocess.run(
            list(cxx) + ["--version"],
            capture_output=True, text=True, timeout=5,
        ).stdout.lower()
    except (subprocess.SubprocessError, OSError):
        return "unknown"
    if "clang" in out:
        return "clang"
    if "free software foundation" in out or "gcc" in out:
        return "gcc"
    return "unknown"


@functools.lru_cache(maxsize=8)
def _gcc_major(cxx: tuple[str, ...]) -> int | None:
    try:
        out = subprocess.run(
            list(cxx) + ["-dumpversion"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    major = out.split(".")[0]
    return int(major) if major.isdigit() else None


def strict_warn_flags(cxx: list[str]) -> list[str]:
    """Strict warning set tailored to the C++ compiler family.

    Apple ships their clang as `g++`, so name-based detection is unreliable;
    `_detect_compiler_family` runs `--version` to disambiguate.
    """
    family = _detect_compiler_family(tuple(cxx))
    if family == "gcc":
        if _gcc_major(tuple(cxx)) == 13:
            # GCC 13's -Wdangling-reference takes any temporary argument
            # (a key lambda) for the referent of a returned reference;
            # GCC 14 narrowed it, and the newer toolchains keep the check.
            return (_COMMON_WARN_FLAGS + _GCC_ONLY_WARN_FLAGS
                    + ["-Wno-dangling-reference"])
        return _COMMON_WARN_FLAGS + _GCC_ONLY_WARN_FLAGS
    if family == "clang":
        return _COMMON_WARN_FLAGS + _CLANG_ONLY_WARN_FLAGS
    return list(_COMMON_WARN_FLAGS)


@dataclass
class CppCompilerConfig:
    """Configuration for the C++ compiler used to build generated code."""
    compiler: list[str] = field(default_factory=lambda: ["g++"])
    std: str = "c++23"
    extra_flags: list[str] = field(default_factory=list)
    link_flags: list[str] = field(default_factory=list)
    ccache: bool = False
    # End-user CLI builds default to no extra warnings. Tests override this to
    # `strict_warn_flags(compiler)` so we catch generated-code regressions.
    warn_flags: list[str] = field(default_factory=list)

    # The `PLATFORMS` key of the OS this config builds for: probed from
    # `compiler` at construction and by `use_compiler`, the host's when the
    # probe fails or answers an unrecognized triple. Every target-dependent
    # build decision (link entries, third-party deps, the darwin pin and
    # link flags) reads it.
    target_platform: str = field(init=False)

    # Whether build steps run from the build dir with CCACHE_BASEDIR set to
    # it (`build_step_cwd`), so builds at different paths share ccache
    # entries. Only when the user supplied no compiler settings of their
    # own: a path in them (`-include x.h`, `@file`, a relative compiler, a
    # relative CPATH element) is relative to the caller's cwd, and the
    # spellings that carry one are too many to rewrite, so such a build runs
    # from the caller's cwd and shares nothing. `extra_flags` count as the
    # caller's at construction only; tpyc appends its own defines later.
    shares_build_dir: bool = field(init=False)

    def __post_init__(self) -> None:
        self.target_platform = compiler_target_os(tuple(self.compiler))
        self.shares_build_dir = _shares_build_dir(self.compiler, self.extra_flags)

    def use_compiler(self, compiler: list[str]) -> None:
        """Replace the compiler command, re-deciding the target and
        `shares_build_dir` with it."""
        self.compiler = compiler
        self.target_platform = compiler_target_os(tuple(compiler))
        self.shares_build_dir = _shares_build_dir(compiler, self.extra_flags)

    @property
    def compiler_name(self) -> str:
        """Display name for the compiler (e.g. 'g++', 'zig c++')."""
        parts = [os.path.basename(self.compiler[0])] + self.compiler[1:]
        return " ".join(parts)

    @classmethod
    def from_env(cls, cxx: str = "auto") -> CppCompilerConfig:
        """Create config from --cxx flag value, CXX env var, or auto-detection.

        Resolution order:
        1. Explicit --cxx value (if not "auto")
        2. CXX environment variable (if set)
        3. Auto-detect: g++ > clang++ > zig c++
        """
        if cxx != "auto":
            resolved = _resolve_compiler(cxx)
            if resolved is None:
                raise CompilerNotFoundError(cxx)
            warn_toolchain_unsupported(resolved)
            compiler = resolved
        else:
            env_cxx = os.environ.get("CXX", "")
            if env_cxx:
                compiler = env_cxx.split()
                warn_toolchain_unsupported(compiler)
            else:
                compiler = _auto_detect_compiler()
        config = cls.for_compiler(compiler)
        config.ccache = shutil.which("ccache") is not None
        return config

    @classmethod
    def for_compiler(cls, compiler: list[str]) -> CppCompilerConfig:
        """The config for an already resolved compiler command, carrying
        every flag its target needs on the command itself; the CLI, the
        harness (both through `from_env`), the REPL and `build_cpp_commands`'
        default build theirs here."""
        config = cls(compiler=compiler)
        config.use_compiler([*compiler, *darwin_version_min_flags(
            compiler, config.target_platform)])
        return config
