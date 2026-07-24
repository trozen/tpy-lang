"""
C++ toolchain discovery and configuration.

Compiler resolution (--cxx / $CXX / auto-detect), CppCompilerConfig, the
strict warning sets, PCH building, and runtime-source discovery. Split out
of compiler.py so the build cache's warm path can replay toolchain
resolution without importing the compiler machinery (~250ms) -- keep this
module's imports light (stdlib only).
"""

from __future__ import annotations
import functools
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


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


def _triple_to_os(triple: str) -> str:
    """Map a -dumpmachine target triple to an OS token ('darwin', 'linux',
    'windows', or 'unknown')."""
    t = triple.lower()
    if "darwin" in t or "apple" in t or "macos" in t:
        return "darwin"
    if "linux" in t:
        return "linux"
    if "windows" in t or "mingw" in t or "msvc" in t:
        return "windows"
    return "unknown"


def host_os() -> str:
    """This machine's OS token, comparable to compiler_target_os()."""
    return {"linux": "linux", "darwin": "darwin",
            "win32": "windows"}.get(sys.platform, "unknown")


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
    """The OS a compiler command TARGETS, probed via `-dumpmachine`. An
    osxcross clang++ answers darwin on a Linux host -- how cross builds are
    detected without any flag. Falls back to the host OS when the probe fails
    or the triple is unrecognized (a compiler without -dumpmachine is assumed
    native)."""
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


def darwin_version_min_flags(compiler: list[str]) -> list[str]:
    """The deployment-target pin for darwin-targeting compilers, [] for
    everything else (or when the command already carries its own pin).
    Carried on the compiler command itself so every driver invocation --
    C++ and C compiles, PCH, link -- inherits it."""
    if any(a.startswith(("-mmacosx-version-min", "-mmacos-version-min"))
           for a in compiler):
        return []
    if compiler_target_os(tuple(compiler)) != "darwin":
        return []
    return [f"-mmacosx-version-min={MACOS_VERSION_MIN}"]


def darwin_cross_ld_flags(compiler: list[str]) -> list[str]:
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
    if compiler_target_os(tuple(compiler)) != "darwin":
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


def _toolchain_probe_id(compiler: list[str]) -> str | None:
    """Probe-cache key: resolved binary path + stat + probe source + flags.

    Same identity notion as build_cache.toolchain_entry: a replaced binary
    at the same path (new size/mtime) re-probes; an untouched one never
    does. None when the binary cannot be resolved at all.
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
    key = "\0".join(
        [resolved, str(st.st_size), str(st.st_mtime_ns),
         *compiler[1:], _PROBE_SOURCE])
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


def get_or_build_pch(
    config: CppCompilerConfig,
    runtime_include_dir: Path,
    opt_flags: list[str],
    pch_dir: Path,
) -> Path | None:
    """Return path to cached PCH header (with .gch next to it), or None on failure.

    The PCH is stored in pch_dir (typically inside the build output directory).
    Rebuilds only when runtime headers are newer than the cached .gch.
    """
    pch_dir.mkdir(parents=True, exist_ok=True)
    pch_header = pch_dir / "tpy_pch.hpp"
    pch_gch = pch_dir / "tpy_pch.hpp.gch"

    # Staleness check
    if pch_gch.exists():
        pch_mtime = pch_gch.stat().st_mtime
        runtime_tpy = runtime_include_dir / "tpy"
        stale = any(h.stat().st_mtime > pch_mtime
                    for h in runtime_tpy.glob("**/*.hpp"))
        if not stale:
            return pch_header

    pch_header.write_text('#include <tpy/tpy.hpp>\n')
    # Clang embeds the input header's mtime in the .gch and rejects the PCH
    # if it differs at consume time, even when content is byte-identical. The
    # cache that stores this .gch is content-addressed, so a fresh checkout
    # that only bumps tpy.hpp's mtime would invalidate every cached clang PCH.
    # -fno-pch-timestamp drops the timestamp so validation falls to content
    # (what ccache does); GCC already validates by content and needs nothing.
    family_flags: list[str] = []
    if _detect_compiler_family(tuple(config.compiler)) == "clang":
        family_flags = ["-Xclang", "-fno-pch-timestamp"]
    cmd = [
        *config.compiler, f"-std={config.std}",
        *config.extra_flags,
        *config.warn_flags,
        *opt_flags,
        *family_flags,
        "-I", str(runtime_include_dir),
        "-x", "c++-header",
        str(pch_header), "-o", str(pch_gch),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return pch_header
    return None


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


def strict_warn_flags(cxx: list[str]) -> list[str]:
    """Strict warning set tailored to the C++ compiler family.

    Apple ships their clang as `g++`, so name-based detection is unreliable;
    `_detect_compiler_family` runs `--version` to disambiguate.
    """
    family = _detect_compiler_family(tuple(cxx))
    if family == "gcc":
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
        compiler = [*compiler, *darwin_version_min_flags(compiler)]
        ccache = not _is_zig(compiler) and shutil.which("ccache") is not None
        return cls(compiler=compiler, ccache=ccache)
