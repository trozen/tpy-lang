"""Shared fixtures and utilities for TurboPython tests."""

import concurrent.futures
import dataclasses
import difflib
import fcntl
import functools
import hashlib
import json
import os
import atexit
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, field

import pytest

# When set, tests update expected files instead of comparing
UPDATE_EXPECTED = os.environ.get("UPDATE_EXPECTED", "").lower() in ("1", "true")
# Keep linked per-case test binaries after a passing exec phase. Default
# is to delete them: nothing reads a stale binary (exec skips via
# fingerprints or rebuilds from scratch), so the only effect of keeping
# them is ~2.3MB x ~3000 cases of dead executables per worktree.
KEEP_TEST_BINARIES = os.environ.get("TPY_KEEP_TEST_BINARIES", "").lower() in ("1", "true")

# Prefix for harness-emitted terminal lines so they stand out from pytest's
# own output (which owns the unprefixed terminal). Greppable, ASCII.
_LOG_PREFIX = "tpy|"


def _log(msg: str, *, err: bool = False) -> None:
    """Print a harness status/diagnostic line with the shared prefix."""
    print(f"{_LOG_PREFIX} {msg}", file=sys.stderr if err else sys.stdout)


# Import the compiler
sys.path.insert(0, str(Path(__file__).parent.parent))
from tpyc.cli import get_module_name
from tpyc.codegen_cpp import CodeGenOptions, CodeGenError
from tpyc.compilation_context import activate_compiler
from tpyc.parse import Parser, ParseError
from tpyc.sema import SemanticAnalyzer, SemanticError, Diagnostic, DiagnosticLevel
from tpyc.compiler import (
    Compiler, CompileError, BuildLayout, CppCompilerConfig, strict_warn_flags,
    get_or_build_pch, list_compilers, CompilerNotFoundError,
)
from tpyc.toolchain import (
    compiler_target_os, host_os, pch_is_path_sensitive, shared_cache_root,
)
from tpyc.build.third_party import (
    resolve_build_plan, ThirdPartyMode, THIRD_PARTY_MODES, known_lib_names,
)
from tpyc.thir.faces import THIR_FACES
from tpyc import move_audit
from tpyc.thir import fallback as thir_fallback
from tpyc.thir.fallback import arm_universe

# Default options for tests: emit source comments for easier debugging
TEST_CODEGEN_OPTIONS = CodeGenOptions(emit_source_comments=True, comment_line_numbers=False)

# Shared C++ compiler config (auto-detects ccache).
# Tests run with the strict warning set so regressions in generated code or
# runtime headers fail the build instead of slipping through. End-user CLI
# builds (`tpy`, `tpyc`) keep an empty default -- consumers opt in via their
# own toolchain flags. See _COMMON_WARN_FLAGS / _GCC_ONLY_WARN_FLAGS /
# _CLANG_ONLY_WARN_FLAGS in tpyc/compiler.py for the per-flag rationale.
CPP_CONFIG = CppCompilerConfig.from_env()
CPP_CONFIG.warn_flags = strict_warn_flags(CPP_CONFIG.compiler)
# xdist workers return early from pytest_configure, so --no-ccache can't be
# applied there. The controller exports this flag and every conftest import
# (workers included) honors it -- otherwise workers, which do the per-case
# compiles, would keep using ccache despite the flag.
if os.environ.get("TPY_TEST_NO_CCACHE") == "1":
    CPP_CONFIG.ccache = False

# Third-party dependency mode overrides for the exec build (--dep-mode
# lib=mode), mirroring `tpyc --<lib>=<mode>`. Empty means each lib's
# default_mode (bundled). Filled in pytest_configure before the xdist-worker
# early return -- workers do the per-case compiles, so they parse it too.
# Scope: the tests/cases exec phase; the interop harness builds through the
# real `tpyc -b` CLI and keeps the CLI defaults.
DEP_MODES: dict[str, ThirdPartyMode] = {}


def _parse_dep_modes(specs: list[str]) -> dict[str, ThirdPartyMode]:
    """Parse --dep-mode values ("lib=mode", comma-separable, repeatable) into
    a modes dict for resolve_build_plan. Raises ValueError on an unknown lib
    or mode. Extracted from pytest_configure so it is unit-testable.

    Two tpyc modes are excluded: "none" -- the stdlib prewarm always
    resolves every declared dep (re/ssl are compiled into the shared .o set
    regardless of what a case imports), so a disabled lib can only crash the
    session via DisabledLibError; and "auto" -- once it learns real system
    probing, the probe OUTCOME would not be captured by the stdlib cache key
    (which folds only the mode string), inviting stale caches."""
    allowed = [m for m in THIRD_PARTY_MODES if m not in ("none", "auto")]
    modes: dict[str, ThirdPartyMode] = {}
    for spec in specs:
        for item in spec.split(","):
            item = item.strip()
            if not item:
                continue
            lib, sep, mode = item.partition("=")
            lib, mode = lib.strip(), mode.strip()
            if not sep or lib not in known_lib_names() or mode not in allowed:
                raise ValueError(
                    f"--dep-mode: invalid spec {item!r} (expected <lib>=<mode> "
                    f"with lib in {known_lib_names()} and mode in {allowed})"
                )
            modes[lib] = mode  # type: ignore[assignment]
    return modes

# Paths
TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"    # All tests (grouped by feature)
INTEROP_DIR = TESTS_DIR / "interop"  # CPython extension ext-exec cases
PROJECT_ROOT = TESTS_DIR.parent
LIB_DIR = PROJECT_ROOT / "lib"
CPY_LIB_DIR = LIB_DIR / "cpy"
TPY_LIB_DIR = LIB_DIR / "tpy"
DEFAULT_LIB_DIRS = [TPY_LIB_DIR]
RUNTIME_DIR = PROJECT_ROOT / "runtime" / "cpp" / "include"
TPYC_DIR = PROJECT_ROOT / "tpyc"


# ---------------------------------------------------------------------------
# Pre-compiled stdlib cache
# ---------------------------------------------------------------------------
# Compiles the implicit stdlib .o files once per session so that the exec
# phase only needs to compile each test's unique main.cpp.

@dataclass
class _StdlibCache:
    """Pre-compiled stdlib object files shared across all exec-phase runs."""
    objects: list[str]          # absolute paths to .o files
    cpp_relpaths: set[str]      # relative paths (under src/) to exclude from per-test compile
    # Hash of the generated C++ for the WHOLE stdlib set (every module, not
    # just the ones a given case imports). Every case binary links this full
    # `.o` set with no dead-stripping, so an unimported-but-linked module's
    # codegen still determines the binary -- a case's exec fingerprint must
    # fold this in or a change there would be a stale-green skip.
    output_hash: str = ""
    # Link flags for third-party deps resolved in system mode (--dep-mode):
    # their .o are then NOT compiled into the cache, but stdlib .o still
    # reference their symbols, so every case linking the cache needs these
    # flags -- imported-or-not. Empty in bundled mode.
    link_flags: list[str] = field(default_factory=list)


_stdlib_cache: _StdlibCache | None = None
_stdlib_cache_initialized = False


def _is_macro_module(path: Path) -> bool:
    """True when a .py file has a '# tpy: macro_module' directive near the top."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                if "tpy: macro_module" in line:
                    return True
    except OSError:
        pass
    return False


def _discover_stdlib_imports() -> list[str]:
    """Enumerate every importable runtime module under lib/tpy/ for the stub.

    Walks lib/tpy/ and returns dotted module names for files that produce
    runtime code. Skips:
      - __init__.py (parent package import brings these in)
      - _-prefixed path components (private; e.g. _macro_helpers, tpy._bootstrap)
      - __pycache__ directories
      - modules marked '# tpy: macro_module' (compile-time only, no runtime code)

    Output is deterministic (sorted) so the stub hashes stably.
    """
    modules: list[str] = []
    for p in sorted(TPY_LIB_DIR.rglob("*.py")):
        if p.name == "__init__.py":
            continue
        rel = p.relative_to(TPY_LIB_DIR)
        parts = rel.with_suffix("").parts
        if any(part.startswith("_") or part == "__pycache__" for part in parts):
            continue
        if _is_macro_module(p):
            continue
        modules.append(".".join(parts))
    return modules


def _setup_stdlib_cache(cache_dir: Path) -> _StdlibCache:
    """Generate and compile stdlib .o files into *cache_dir*.

    Stub imports every runtime module discovered under lib/tpy/ so the
    compiler emits .hpp/.cpp for each, which we then precompile into .o
    files that per-case builds link against instead of recompiling per
    test. Macro-only modules are filtered out during discovery.
    """
    stub_src = cache_dir / "_stub.py"
    imports = _discover_stdlib_imports()
    stub_src.write_text("\n".join(f"import {m}" for m in imports) + "\n")

    from tpyc.compilation_context import activate_compiler

    compiler = Compiler(stub_src, default_int="Int32", lib_dirs=DEFAULT_LIB_DIRS)
    compiled_modules = compiler.compile()
    entry = next(m for m in compiled_modules if m.is_entry_point)

    build_dir = cache_dir / "build"
    build_dir.mkdir()
    # `generate_code` and `layout.cpp_path` both consult the active
    # compiler's `include_path_map` to honor `# tpy: include` overrides
    # (e.g. lib/tpy/re.py -> `tpystd/re.hpp`). The path comparison below
    # needs the same override resolution as the writer used, so both
    # passes happen under the same `activate_compiler` block.
    with activate_compiler(compiler):
        for mod in compiled_modules:
            compiler.generate_code(mod, build_dir,
                                   entry_module_name=entry.name,
                                   options=TEST_CODEGEN_OPTIONS)

        layout = BuildLayout(build_dir, entry.name, build_variant="debug")

        # Collect non-local (stdlib) generated C++. The .cpp become .o; the
        # .hpp matter too -- both feed `output_hash`, the identity of the
        # full stdlib set every case links against.
        stdlib_cpps: list[Path] = []
        stdlib_gen_files: list[Path] = []
        for mod in compiled_modules:
            if mod.is_entry_point:
                continue
            cpp = layout.cpp_path(mod.name)
            if cpp.exists():
                stdlib_cpps.append(cpp)
                stdlib_gen_files.append(cpp)
            hpp = layout.hpp_path(mod.name)
            if hpp.exists():
                stdlib_gen_files.append(hpp)

    if not stdlib_cpps:
        return _StdlibCache(objects=[], cpp_relpaths=set())

    output_hash = _hash_files(sorted(stdlib_gen_files))

    # Compile each stdlib .cpp -> .o.
    # Parallelized across CPU cores; uses PCH (when available) so each
    # compile skips re-parsing the runtime headers.
    obj_dir = cache_dir / "obj"
    obj_dir.mkdir()

    pch_header = get_pch_header()
    pch_include = [] if pch_header is None else ["-include", str(pch_header)]

    # Add include dirs for any third-party deps declared by stdlib modules
    # (e.g. `# tpy: link_third_party("pcre2")` in _bindings/pcre2.py needs the
    # PCRE2 header on -I). Modes come from --dep-mode (default: each lib's
    # default_mode, bundled); _stdlib_cache_key folds any overrides so
    # per-mode caches never collide.
    third_party_plan = resolve_build_plan(
        dep_names=compiler.collect_third_party_deps(),
        runtime_cpp_dir=RUNTIME_DIR.parent,
        modes=DEP_MODES,
    )
    third_party_includes: list[str] = []
    for d in third_party_plan.extra_include_dirs:
        third_party_includes += ["-I", str(d)]

    common = [
        *CPP_CONFIG.compiler, f"-std={CPP_CONFIG.std}",
        *CPP_CONFIG.extra_flags,
        *CPP_CONFIG.warn_flags,
        "-I", str(RUNTIME_DIR),
        "-I", str(layout.include_dir),
        *third_party_includes,
        *pch_include,
    ]

    def _compile_one(cpp: Path) -> tuple[str, str]:
        """Compile a single stdlib .cpp to .o; raise on failure."""
        rel = str(cpp.relative_to(layout.src_dir))
        obj_name = rel.replace(os.sep, "_").removesuffix(".cpp") + ".o"
        obj_path = obj_dir / obj_name
        prefix = ["ccache"] if CPP_CONFIG.ccache else []
        cmd = [*prefix, *common, "-c", "-o", str(obj_path), str(cpp)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"stdlib pre-compilation failed for {rel}:\n{result.stderr}"
            )
        return rel, str(obj_path)

    # Cap thread count to the cgroup CPU quota so we don't over-subscribe
    # when run inside Docker --cpus=N.
    max_workers = _cgroup_cpu_quota() or os.cpu_count() or 8
    max_workers = max(1, min(max_workers, len(stdlib_cpps)))

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            # list() forces evaluation and propagates the first exception
            results = list(ex.map(_compile_one, stdlib_cpps))
    except RuntimeError as exc:
        _log(f"WARNING: {exc}", err=True)
        return _StdlibCache(objects=[], cpp_relpaths=set())

    relpaths = {rel for rel, _ in results}
    objects = [obj for _, obj in results]

    # Also pre-compile bundled third-party deps (e.g. PCRE2 .c files, date's
    # tz.cpp). These are referenced by stdlib .o files (e.g. re.o links
    # against pcre2_*), so any test linking the stdlib cache needs them
    # available -- even tests that don't import the relevant module, because
    # the cache .o set is shared. Per-file driver via the same
    # `third_party_source_driver` that `build_cpp_commands` uses.
    #
    # No opt_flags applied here, mirroring the .cpp pre-compile above:
    # tests always run a single shared cache regardless of build variant.
    # End-user `tpyc -xO` builds go through `BuildLayout.build_cpp_commands`
    # (not this cache) and DO get -O3 on the same sources.
    if third_party_plan.c_sources:
        from tpyc.compiler import third_party_source_driver
        for c_src, c_flags in third_party_plan.c_sources:
            obj_path = obj_dir / (c_src.stem + ".o")
            prefix = ["ccache"] if CPP_CONFIG.ccache else []
            driver = third_party_source_driver(
                c_src, CPP_CONFIG.compiler, CPP_CONFIG.std)
            cmd = [*prefix, *driver, *CPP_CONFIG.extra_flags,
                   *c_flags, "-c", "-o", str(obj_path), str(c_src)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"third-party pre-compilation failed for {c_src.name}:\n"
                    f"{result.stderr}"
                )
            objects.append(str(obj_path))

    # TPy-owned runtime .cpp files (e.g. socket / getaddrinfo helper). Always
    # pre-compiled and linked into every test binary -- same rationale as the
    # stdlib .o above: avoids recompiling per-case. Cache key is invalidated
    # via _runtime_hash() which includes runtime/cpp/src/. Parallelized the
    # same way as the stdlib compile above; errors handled the same way too
    # -- one bad runtime source degrades the whole cache to empty with a
    # warning, matching the stdlib-failure path, rather than propagating an
    # uncaught RuntimeError to pytest.
    from tpyc.compiler import discover_runtime_cpp_sources
    runtime_cpp_sources = discover_runtime_cpp_sources(RUNTIME_DIR.parent)
    if runtime_cpp_sources:
        rt_src_root = RUNTIME_DIR.parent / "src"

        def _compile_runtime_one(rt_cpp: Path) -> str:
            """Compile a single runtime .cpp to .o; raise on failure."""
            try:
                rel = rt_cpp.relative_to(rt_src_root)
                obj_name = "rt_" + str(rel).replace(os.sep, "_").removesuffix(".cpp") + ".o"
            except ValueError:
                obj_name = "rt_" + rt_cpp.stem + ".o"
            obj_path = obj_dir / obj_name
            prefix = ["ccache"] if CPP_CONFIG.ccache else []
            cmd = [*prefix, *common, "-c", "-o", str(obj_path), str(rt_cpp)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"runtime .cpp pre-compilation failed for "
                    f"{rt_cpp.name}:\n{result.stderr}"
                )
            return str(obj_path)

        rt_max = max(1, min(max_workers, len(runtime_cpp_sources)))
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=rt_max) as ex:
                rt_objs = list(ex.map(_compile_runtime_one, runtime_cpp_sources))
        except RuntimeError as exc:
            _log(f"WARNING: {exc}", err=True)
            return _StdlibCache(objects=[], cpp_relpaths=set())
        objects.extend(rt_objs)

    return _StdlibCache(objects=objects, cpp_relpaths=relpaths, output_hash=output_hash,
                        link_flags=list(third_party_plan.extra_link_flags))


def _shared_cache_root() -> Path:
    """Root dir for shared caches -- the compiler's derivation
    ($TPYC_SHARED_CACHE_DIR override, else XDG / %LOCALAPPDATA%)."""
    return shared_cache_root()


@functools.cache
def _libtpy_hash() -> str:
    """Hash of all .py files under lib/tpy/ (the stdlib source). Session-cached."""
    files = sorted(TPY_LIB_DIR.rglob("*.py"))
    return _hash_files(files)


@functools.cache
def _tpyc_hash() -> str:
    """Hash of all .py files under tpyc/ (the compiler source). Session-cached.

    Compiler changes can mutate the C++ emitted for a given stdlib source
    (auto-readonly inference of `*args` slots, codegen-rendering tweaks,
    new coercions) without touching `lib/tpy/`. Without this in the cache
    key, stale .o files with the previous signature would silently linger
    and break linking against newly-compiled test mains.
    """
    files = sorted(TPYC_DIR.rglob("*.py"))
    return _hash_files(files)


@functools.cache
def _stdlib_cache_key() -> str:
    """Content-addressed key for the persistent stdlib .o cache.

    Captures everything that affects the produced .o files: runtime headers,
    stdlib Python source, compiler source (tpyc/), and C++ build config.
    """
    h = hashlib.sha256()
    h.update(_runtime_hash().encode())
    h.update(b"\0")
    h.update(_libtpy_hash().encode())
    h.update(b"\0")
    h.update(_tpyc_hash().encode())
    h.update(b"\0")
    h.update(repr((
        CPP_CONFIG.compiler,
        CPP_CONFIG.std,
        CPP_CONFIG.extra_flags,
        CPP_CONFIG.warn_flags,
    )).encode())
    # Dep-mode overrides change the .o set (bundled third-party .c compiled
    # in vs. left to the system lib). Folded only when non-empty so default
    # (bundled) runs keep their existing cache keys.
    if DEP_MODES:
        h.update(b"\0dep-modes:")
        h.update(repr(sorted(DEP_MODES.items())).encode())
    return h.hexdigest()


def _load_persistent_stdlib_cache(cache_dir: Path) -> _StdlibCache | None:
    """Read a previously-built persistent cache from disk.

    Returns None when metadata is missing/corrupt OR when any referenced .o
    file is missing on disk (e.g. an aggressive /tmp cleaner removed individual
    files but left the directory). In that case the caller rebuilds.
    """
    metadata = cache_dir / "metadata.json"
    try:
        data = json.loads(metadata.read_text())
        objects = list(data["objects"])
        if not all(Path(p).is_file() for p in objects):
            return None
        return _StdlibCache(
            objects=objects,
            cpp_relpaths=set(data["cpp_relpaths"]),
            # Empty fallbacks for pre-output_hash / pre-link_flags metadata are
            # safe: this dir is content-addressed by _stdlib_cache_key, so
            # legacy .o are reused only while byte-identical -- any change
            # re-keys and rebuilds (and legacy dirs are all bundled-mode, whose
            # link_flags are genuinely empty).
            output_hash=data.get("output_hash", ""),
            link_flags=list(data.get("link_flags", [])),
        )
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        return None


def _build_persistent_stdlib_cache(cache_dir: Path) -> _StdlibCache | None:
    """Build the stdlib .o cache atomically into cache_dir.

    Compiles into a sibling tempdir, then renames the produced obj/ dir into
    place. Writes metadata.json with the path/relpath info. Touches .ready
    when complete -- readers gate on this marker.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    build_tmp = Path(tempfile.mkdtemp(prefix=".build_", dir=cache_dir))
    try:
        cache = _setup_stdlib_cache(build_tmp)
        if cache is None or not cache.objects:
            return None

        # Move .o files into place. _setup_stdlib_cache writes them into
        # build_tmp/obj/. Rename obj/ into cache_dir/obj/ atomically.
        obj_src = build_tmp / "obj"
        obj_dst = cache_dir / "obj"
        if obj_dst.exists():
            shutil.rmtree(obj_dst)
        obj_src.rename(obj_dst)

        # Rewrite paths to point at the final location
        rebased_objects = [str(obj_dst / Path(p).name) for p in cache.objects]

        metadata = {
            "objects": rebased_objects,
            "cpp_relpaths": sorted(cache.cpp_relpaths),
            "output_hash": cache.output_hash,
            "link_flags": cache.link_flags,
        }
        (cache_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (cache_dir / ".ready").touch()
        return _StdlibCache(objects=rebased_objects, cpp_relpaths=cache.cpp_relpaths,
                            output_hash=cache.output_hash, link_flags=cache.link_flags)
    finally:
        if build_tmp.exists():
            shutil.rmtree(build_tmp, ignore_errors=True)


def _get_or_build_persistent_stdlib_cache(root: Path) -> _StdlibCache | None:
    """Get-or-build the shared stdlib cache. Coordinated via fcntl.flock."""
    cache_dir = root / "stdlib-objs" / _stdlib_cache_key()
    ready_marker = cache_dir / ".ready"

    # Fast path: already built
    if ready_marker.exists():
        loaded = _load_persistent_stdlib_cache(cache_dir)
        if loaded is not None:
            return loaded

    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / ".lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        # Re-check: another worker/process may have built while we waited
        if ready_marker.exists():
            loaded = _load_persistent_stdlib_cache(cache_dir)
            if loaded is not None:
                return loaded
        t0 = time.monotonic()
        cache = _build_persistent_stdlib_cache(cache_dir)
        if cache is not None:
            _log(
                f"stdlib cache: built {len(cache.objects)} .o "
                f"({time.monotonic() - t0:.1f}s)"
            )
        return cache


# ---------------------------------------------------------------------------
# Persistent PCH (precompiled header) cache
# ---------------------------------------------------------------------------
# Builds tpy_pch.hpp.gch once per session (per content key) so that every
# per-case main.cpp compile reuses the precompiled runtime headers instead
# of re-parsing the entire tpy.hpp template chain. Major win on cold ccache.

_pch_path: Path | None = None
_pch_initialized: bool = False


@functools.cache
def _pch_cache_key() -> str:
    """Content-addressed key for the PCH cache (runtime headers + compiler config).

    opt_flags is intentionally NOT in the key: tests always invoke
    build_cpp_commands without opt_flags, so the PCH is always built with
    opt_flags=[] and is valid for any test compile. If a future test variant
    starts passing opt_flags, this key must grow or PCHs will be incompatible.
    """
    h = hashlib.sha256()
    h.update(_runtime_hash().encode())
    h.update(b"\0")
    h.update(repr((
        CPP_CONFIG.compiler,
        CPP_CONFIG.std,
        CPP_CONFIG.extra_flags,
        CPP_CONFIG.warn_flags,
    )).encode())
    # This cache root is machine-wide, so byte-identical runtime headers in two
    # checkouts hash the same -- but on clang the .gch is only usable from the
    # -I root it was built against (see pch_is_path_sensitive). Give each
    # checkout its own key there; on GCC the .gch stays shared across worktrees.
    if pch_is_path_sensitive(CPP_CONFIG):
        h.update(b"\0runtime-root:")
        h.update(str(RUNTIME_DIR.resolve()).encode())
    return h.hexdigest()


def _set_pch_ccache_sloppiness() -> None:
    """Set CCACHE_SLOPPINESS to allow PCH-using compiles to hit the cache.

    Without these flags ccache treats every PCH-using compile as a miss
    (cli.py:478 does the same when building with PCH).
    """
    if not CPP_CONFIG.ccache:
        return
    slop = os.environ.get("CCACHE_SLOPPINESS", "")
    parts = {s.strip() for s in slop.split(",") if s.strip()}
    parts.update(("pch_defines", "time_macros"))
    os.environ["CCACHE_SLOPPINESS"] = ",".join(sorted(parts))


def get_pch_header() -> Path | None:
    """Return path to the cached PCH header for tests, building or reusing as needed.

    The .gch file lives next to the .hpp; passing the .hpp via -include lets
    the compiler discover the .gch automatically. Persists across pytest
    invocations under $TPYC_SHARED_CACHE_DIR/pch/<key>/. Returns None if
    PCH building fails or the shared cache root is unwritable -- per-case
    compiles then fall back to parsing the runtime headers from scratch.
    """
    global _pch_path, _pch_initialized
    if _pch_initialized:
        return _pch_path
    _pch_initialized = True

    try:
        pch_dir = _shared_cache_root() / "pch" / _pch_cache_key()
        pch_header = pch_dir / "tpy_pch.hpp"
        pch_gch = pch_dir / "tpy_pch.hpp.gch"

        # Fast path: PCH already built for this content key. Skip locking
        # entirely so warm-cache workers don't queue on LOCK_EX at startup.
        if pch_gch.exists() and pch_header.exists():
            _pch_path = pch_header
            _set_pch_ccache_sloppiness()
            return _pch_path

        pch_dir.mkdir(parents=True, exist_ok=True)
        lock_path = pch_dir / ".lock"
        with open(lock_path, "w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            # Re-check after acquiring the lock: another worker may have
            # built the PCH while we waited.
            if pch_gch.exists() and pch_header.exists():
                _pch_path = pch_header
            else:
                t0 = time.monotonic()
                _pch_path = get_or_build_pch(
                    CPP_CONFIG, RUNTIME_DIR, opt_flags=[], pch_dir=pch_dir,
                )
                if _pch_path is not None:
                    _log(f"PCH: built tpy_pch.hpp.gch ({time.monotonic() - t0:.1f}s)")
            if _pch_path is not None:
                _set_pch_ccache_sloppiness()
    except Exception as exc:
        # Catch broadly (matches get_stdlib_cache): a PCH failure must not
        # abort the worker -- per-case compiles will fall back to parsing
        # the runtime headers from scratch.
        _log(f"WARNING: PCH cache unavailable ({exc}); compiling without PCH", err=True)
        _pch_path = None
    return _pch_path


def get_stdlib_cache() -> _StdlibCache | None:
    """Return the pre-compiled stdlib cache, building or reusing as needed.

    Persists across pytest invocations and is shared across xdist workers via
    a content-addressed dir under $TPYC_SHARED_CACHE_DIR/stdlib-objs/<key>/
    (default $XDG_CACHE_HOME/tpyc/stdlib-objs/<key>/, i.e. ~/.cache/tpyc/
    stdlib-objs/<key>/ on Linux/macOS; %LOCALAPPDATA%\\tpyc\\cache\\stdlib-objs\\
    <key>\\ on Windows). Cache key invalidates when runtime headers, lib/tpy
    stdlib, or C++ build config change. Falls back to a per-process tempdir if
    the shared root is unwritable.
    """
    global _stdlib_cache, _stdlib_cache_initialized
    if _stdlib_cache_initialized:
        return _stdlib_cache
    _stdlib_cache_initialized = True

    try:
        _stdlib_cache = _get_or_build_persistent_stdlib_cache(_shared_cache_root())
        if _stdlib_cache is not None:
            return _stdlib_cache
    except OSError as exc:
        _log(
            f"WARNING: persistent stdlib cache unavailable ({exc}); "
            f"falling back to ephemeral",
            err=True,
        )

    # Fallback: ephemeral per-process build
    cache_dir = Path(tempfile.mkdtemp(prefix="tpyc_stdlib_"))
    atexit.register(shutil.rmtree, str(cache_dir), True)
    try:
        _stdlib_cache = _setup_stdlib_cache(cache_dir)
    except Exception as exc:
        _log(f"WARNING: stdlib pre-compilation failed: {exc}", err=True)
        _stdlib_cache = None
    return _stdlib_cache


def run_cpython(src_file: Path) -> str:
    """Run a TurboPython file with CPython using the test harness."""
    env = os.environ.copy()
    # Include CPython stubs (for tpy module + tplib symlink) and source dir
    # (for multi-module imports). lib/tpy/ is NOT included -- CPython uses
    # its own stdlib, and tpy types come from the cpy stubs.
    src_dir = src_file.parent
    env["PYTHONPATH"] = f"{CPY_LIB_DIR}{os.pathsep}{src_dir}"
    # Pin terminal width so CPython argparse (and any other terminal-aware
    # stdlib code) wraps at the same column we pre-render against. Without
    # this, the cpy phase is non-deterministic across developer terminals.
    env["COLUMNS"] = "80"

    result = subprocess.run(
        [sys.executable, str(src_file)],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        stderr_text = _filter_lib_traceback(result.stderr)
        pytest.fail(
            f"CPython execution failed for {src_file} (exit code {result.returncode}).\n"
            f"--- stderr ---\n{stderr_text}",
            pytrace=False,
        )

    return result.stdout


@dataclass
class CompileResult:
    """Result of compiling a TurboPython file."""
    success: bool
    diagnostics: str  # Full diagnostic output
    hpp_path: Path | None = None
    cpp_path: Path | None = None
    # For multi-module compilation: list of (module_name, hpp_path, cpp_path, is_local) tuples
    # is_local=True for modules from the test's src/ dir, False for library modules
    # hpp_path/cpp_path are None for native_module (no generated code)
    all_modules: list[tuple[str, Path | None, Path | None, bool]] = field(default_factory=list)
    # Resolved types for variable declarations (from sema), for # tpyc: type(...) validation
    declared_var_types: dict[tuple[int, str], object] | None = None
    # Ptr dereference facts (from sema), for # tpyc: non_null/nullable validation
    ptr_deref_facts: dict[tuple[int, str], bool] | None = None
    # Subscript bounds facts (from sema), for # tpyc: bounds_safe/bounds_checked validation
    subscript_bounds_facts: dict[tuple[int, str], bool] | None = None
    # Division non-zero facts (from sema), for # tpyc: div_safe/div_checked validation
    div_zero_facts: dict[tuple[int, str], bool] | None = None
    # Cast safety facts (from sema), for # tpyc: cast_safe/cast_checked validation
    cast_safe_facts: dict[tuple[int, str], bool] | None = None
    # (is_send, is_sync) per declared variable, for # tpyc: is_send/is_sync validation
    send_sync_facts: dict[tuple[int, str], tuple[bool, bool]] | None = None
    # (frame is_send, is_sync) per async/generator def, for # tpyc: frame_send/frame_sync
    frame_facts: dict[tuple[int, str], tuple[bool, bool]] | None = None
    # Linker flags from # tpy: link() directives
    link_flags: list[str] = field(default_factory=list)
    # Third-party (link_third_party) build inputs: include dirs for the C
    # bindings, extra link flags (system mode), and bundled C source files
    # to compile into the test binary (e.g. PCRE2 .c files in bundled mode).
    third_party_include_dirs: list[Path] = field(default_factory=list)
    third_party_link_flags: list[str] = field(default_factory=list)
    third_party_c_sources: list[tuple[Path, list[str]]] = field(default_factory=list)
    # Per-module names of THIR-routed bodies (None when THIR is off entirely).
    # Feeds the divergence reporter: a snapshot mismatch is labeled with the
    # enclosing function and whether THIR routed it.
    thir_routed_names: dict[str, frozenset[str]] | None = None
    # THIR-overlay generated paths for local modules: (name, hpp, cpp). None only
    # when THIR is off entirely (--no-thir / --update-snapshots) -- a no_thir case
    # still gets an overlay. Byte-compared to the same AST-authored snapshot as
    # all_modules -- so the AST (oracle) and THIR paths are both checked per run.
    thir_modules: list[tuple[str, Path | None, Path | None]] | None = None
    # --thir-stdlib only: per non-local (lib/tpy + stdlib) module, the AST and
    # THIR generated paths from THIS run: (name, ast_hpp, ast_cpp, thir_hpp,
    # thir_cpp). Stdlib emission has no committed snapshot anywhere, so the
    # same-run AST output is its only available oracle (cutover gate D4).
    thir_lib_modules: list[
        tuple[str, Path | None, Path | None, Path | None, Path | None]
    ] | None = None
    # THIR ratchet count: user-body fallbacks for a case the ratchet governs
    # (default run, unmarked). None when it doesn't apply (marked case,
    # whole-corpus/classify runs, or --update-snapshots). >0 fails the comp
    # phase -- an unmarked case must route every user body through THIR.
    thir_ratchet_fell: int | None = None


def _validate_default_int_name(name: str) -> str:
    allowed = {"Int32", "Int64", "BigInt"}
    if name not in allowed:
        pytest.fail(
            f"Invalid default int '{name}'. Expected one of: {', '.join(sorted(allowed))}"
        )
    return name


_ALLOWED_OPTIONS_KEYS = {"default_int", "plugin", "dsl_opts"}


def _parse_options_file(path: Path) -> dict:
    """Read and validate one options.json file. Returns {} when the
    file is absent. Schema: `default_int` (str), `plugin` (str path
    relative to PROJECT_ROOT), `dsl_opts` (dict[str, str] passed to
    the frontend plugin)."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        pytest.fail(f"{path}: invalid JSON ({e.msg})")
    if not isinstance(raw, dict):
        pytest.fail(f"{path}: expected JSON object")
    unknown = sorted(k for k in raw if k not in _ALLOWED_OPTIONS_KEYS)
    if unknown:
        pytest.fail(f"{path}: unsupported keys: {', '.join(unknown)}")
    if "default_int" in raw:
        if not isinstance(raw["default_int"], str):
            pytest.fail(f"{path}: 'default_int' must be a string")
        _validate_default_int_name(raw["default_int"])
    if "plugin" in raw and not isinstance(raw["plugin"], str):
        pytest.fail(f"{path}: 'plugin' must be a string path")
    if "dsl_opts" in raw:
        if not isinstance(raw["dsl_opts"], dict):
            pytest.fail(f"{path}: 'dsl_opts' must be a dict")
        for k, v in raw["dsl_opts"].items():
            if not isinstance(v, str):
                pytest.fail(
                    f"{path}: dsl_opts {k!r} must be a string value")
    return raw


def load_case_options(case_dir: Path) -> dict:
    """Resolve the effective options for a test case.

    Settings are gathered by walking up from `case_dir` to
    `CASES_DIR`, layering options.json files so that ancestor
    declarations act as defaults and the per-case file overrides
    them. Typical layout: a `tests/cases/<group>/options.json`
    declares the frontend plugin once for every case in the group;
    individual cases drop their own options.json only when they
    need to override a setting (e.g. `default_int`).
    """
    layers: list[Path] = []
    cur = case_dir.resolve()
    stop = CASES_DIR.resolve()
    while True:
        layers.append(cur / "options.json")
        if cur == stop:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    merged: dict = {}
    # Walk from ancestor to case dir so the case overrides defaults.
    for layer in reversed(layers):
        cfg = _parse_options_file(layer)
        for k, v in cfg.items():
            if k == "dsl_opts" and "dsl_opts" in merged:
                # Merge dsl_opts dicts key-wise so a per-case
                # options.json can override a single key without
                # repeating the rest of the group's settings.
                combined = dict(merged["dsl_opts"])
                combined.update(v)
                merged["dsl_opts"] = combined
            else:
                merged[k] = v
    return merged


def get_case_default_int(case_dir: Path) -> str:
    """Resolve default integer mode for a test case."""
    options = load_case_options(case_dir)
    return _validate_default_int_name(options.get("default_int", "Int32"))


def plugin_extensions_for(src_file: Path) -> frozenset[str]:
    """Return the source extensions claimed by the frontend plugin
    declared for the test that owns `src_file`, or an empty
    frozenset for plain-Python cases. Used by `tests/test_case.py`
    to strip plugin-claimed extensions when computing the module
    name -- mirrors the logic the CLI runs internally."""
    reg, _ = _frontend_registry_for(src_file)
    if reg is None:
        return frozenset()
    return reg.all_extensions()


def _frontend_registry_for(src_file: Path):
    """Return (registry, extra_lib_dirs) for the test that owns
    `src_file`, or (None, []) for ordinary plain-Python cases.

    The plugin and its options come from the case's effective
    options.json (with walk-up merging from ancestor options.json
    files). Library paths come from the plugin's `library_paths()`
    hook -- conftest no longer hard-codes a frontend.
    """
    cfg = load_case_options(src_file.parent.parent)
    plugin_spec = cfg.get("plugin")
    if not plugin_spec:
        return None, []
    from tpyc.frontend_plugin import FrontendRegistry, load_plugin
    plugin_path = (PROJECT_ROOT / plugin_spec).resolve()
    if not plugin_path.is_file():
        pytest.fail(
            f"plugin file not found at {plugin_path} "
            f"(declared in options.json for {src_file})")
    plugin = load_plugin(str(plugin_path), dict(cfg.get("dsl_opts", {})))
    reg = FrontendRegistry()
    reg.register(plugin)
    extra_lib_dirs = [Path(p).resolve() for p in plugin.library_paths()]
    return reg, extra_lib_dirs


def compile_with_diagnostics(src_file: Path, output_dir: Path, default_int: str | None = None) -> CompileResult:
    """Compile a TurboPython file and capture diagnostics.

    Returns CompileResult with success status, diagnostics, and output paths.
    Warnings are collected but don't cause failure. Errors cause failure.
    Uses Compiler for multi-module support.
    """
    frontend_registry, extra_lib_dirs = _frontend_registry_for(src_file)
    plugin_extensions = (frontend_registry.all_extensions()
                         if frontend_registry is not None else frozenset())
    module_name = get_module_name(src_file, plugin_extensions)
    default_int = _validate_default_int_name(default_int or "Int32")
    # Plugin libraries come ahead of the implicit TPy stdlib so the
    # search order matches what the CLI uses (`cli._run_cli` slots
    # plugin lib dirs in front of the stdlib for the same reason).
    # Without this, a plugin-shipped module that happens to share a
    # name with a stdlib module would resolve differently under
    # `tpyc foo.pas` vs. `pytest`.
    lib_dirs = list(extra_lib_dirs) + list(DEFAULT_LIB_DIRS)

    try:
        # Use Compiler for multi-module support
        compiler = Compiler(src_file, default_int=default_int, lib_dirs=lib_dirs,
                            frontend_registry=frontend_registry)
        compiled_modules = compiler.compile()

        # Collect diagnostics from compiler and all analyzers
        all_diags = []
        has_errors = False
        for d in compiler.diagnostics:
            all_diags.append(d.format("tpyc"))
        for mod in compiled_modules:
            for d in mod.analyzer.diagnostics:
                all_diags.append(d.format(mod.path.name))
                if d.level == DiagnosticLevel.ERROR:
                    has_errors = True
        diagnostics = "\n".join(all_diags) + "\n" if all_diags else ""

        if has_errors:
            return CompileResult(success=False, diagnostics=diagnostics)

        # AST is ALWAYS the emitted + oracle artifact: it feeds exec and is
        # byte-compared to the (AST-authored) snapshot, so the oracle path is
        # exercised for EVERY case -- migrated or not. THIR, when active, is an
        # OVERLAY generated alongside for the migrated user modules and compared
        # to the SAME snapshot (a divergence is a THIR bug), so migrating a case
        # never silently drops AST coverage of the oracle.
        entry_module = next(m for m in compiled_modules if m.is_entry_point)
        src_dir = src_file.parent.resolve()
        case_dir = (src_file.parent.parent if src_file.parent.name == "src"
                    else src_file.parent)
        no_thir = (case_dir / "no_thir.txt").exists()

        ast_opts = dataclasses.replace(TEST_CODEGEN_OPTIONS, thir_codegen=False)
        all_modules = []
        local_mods = []
        for mod in compiled_modules:
            hpp_path, cpp_path = compiler.generate_code(
                mod, output_dir, entry_module_name=entry_module.name,
                options=ast_opts
            )
            is_local = False
            try:
                mod.path.resolve().relative_to(src_dir)
                is_local = True
            except ValueError:
                pass
            # cpp_path is None for native_module (binding-only) modules
            all_modules.append((mod.name, hpp_path, cpp_path, is_local))
            if is_local:
                local_mods.append(mod)

        # THIR overlay: regenerate the USER modules through THIR to a separate
        # dir (stdlib is user-scoped-out and already AST-tested above);
        # test_case byte-compares these to the same snapshot. Fills the THIR
        # tallies (_thir_fallback / _thir_routed_names) read below. Runs for
        # EVERY case -- a marked case still routes bodies (see _thir_case_mode).
        thir_active, thir_ratchet = _thir_case_mode(
            thir_codegen=TEST_CODEGEN_OPTIONS.thir_codegen, no_thir=no_thir,
            ignore_markers=THIR_IGNORE_MARKERS, classify=THIR_CLASSIFY_WRITE,
            check_flip=THIR_CHECK_FLIP)
        thir_modules = None
        thir_ratchet_fell = None
        if thir_active:
            thir_dir = output_dir / "_thir"
            thir_modules = []
            for mod in local_mods:
                hpp_path, cpp_path = compiler.generate_code(
                    mod, thir_dir, entry_module_name=entry_module.name,
                    options=TEST_CODEGEN_OPTIONS
                )
                thir_modules.append((mod.name, hpp_path, cpp_path))

        # Feed the THIR non-vacuity gate + per-case ratchet/dial (the overlay
        # filled the tallies above; all 0 when THIR is off).
        record_thir_routed(compiler._thir_routed_bodies)
        record_thir_faces(compiler._thir_face_witnesses)
        record_thir_fallback(compiler._thir_fallback)
        record_thir_arm_residual(compiler._thir_arm_residual)
        record_thir_shapes(compiler._thir_shapes)
        # Cross-path move-verdict join. Scoped to ROUTED bodies by the journal
        # in move_audit, so a fallback body's verdicts (which drove no emitted
        # C++) cannot raise it. Any hit is a move-vs-copy divergence the
        # byte-diff structurally cannot see.
        # Label relative to the corpus when it lives there; the harness's own
        # synthetic cases are built in a tmpdir and have no corpus-relative
        # path, so fall back to the leaf name rather than raising.
        try:
            _mv_label = str(case_dir.relative_to(CASES_DIR))
        except ValueError:
            _mv_label = case_dir.name
        move_diffs = move_audit.disagreements(compiler)
        record_move_verdicts(_mv_label, move_diffs,
                             joined=move_audit.joined(compiler))
        if move_diffs:
            rows = "\n".join(
                f"  {n} in `{fn or '?'}`: ast={a} thir={t}"
                for n, a, t, fn in move_diffs)
            pytest.fail(
                f"move-verdict divergence ({len(move_diffs)}): a name the two "
                f"paths judge differently at its last use -- one moves where "
                f"the other copies.\n{rows}\nA wrong verdict at a site whose "
                f"render ignores it emits identical C++, so the byte-diff "
                f"cannot catch this. See tpyc/move_audit.py.")
        if thir_active:
            fell = sum(compiler._thir_fallback.values())
            if THIR_CLASSIFY_WRITE:
                _apply_no_thir_marker(case_dir, dirty=(fell > 0))
            elif THIR_CHECK_FLIP:
                if no_thir and fell == 0:
                    record_thir_flip_candidate(str(case_dir))
            elif thir_ratchet:  # default per-case run, UNMARKED case
                # A fallback emits byte-identical AST, so the THIR snapshot
                # compare can't see a silent THIR->AST regression. Surface the
                # count so the comp phase fails the case (the ratchet): unmarked
                # => every user body must route THIR.
                thir_ratchet_fell = fell
                record_thir_case(fell)
            elif not THIR_IGNORE_MARKERS:
                # Marked case on the default run: its overlay still byte-diffs
                # the bodies THIR does route, but the ratchet stays off (the
                # case is allowed to fall back) and the dial counts it
                # not-migrated from the marker, not from this measurement.
                record_thir_case_marked()
            else:
                # --thir-codegen whole-corpus: no ratchet, but keep the dial --
                # same accounting as the default run (marker => un-migrated
                # regardless of the fallback count just measured; unmarked =>
                # clean iff zero user bodies fell back).
                if no_thir:
                    record_thir_case_marked()
                else:
                    record_thir_case(fell)
        thir_routed_names = (dict(compiler._thir_routed_names)
                             if thir_active else None)

        # Stdlib oracle (--thir-stdlib): regenerate the NON-local modules
        # through THIR and hand test_case both paths to byte-compare. Runs
        # AFTER every record_thir_* call above -- stdlib bodies would otherwise
        # land in the same tallies the dial and the ratchet read.
        thir_lib_modules = None
        if thir_active and THIR_STDLIB:
            lib_dir = output_dir / "_thir_lib"
            lib_opts = dataclasses.replace(TEST_CODEGEN_OPTIONS,
                                           thir_all_modules=True)
            thir_lib_modules = []
            ast_paths = {name: (hpp, cpp)
                         for name, hpp, cpp, is_local in all_modules
                         if not is_local}
            for mod in compiled_modules:
                if mod.name not in ast_paths:
                    continue
                hpp_path_t, cpp_path_t = compiler.generate_code(
                    mod, lib_dir, entry_module_name=entry_module.name,
                    options=lib_opts
                )
                ast_hpp, ast_cpp = ast_paths[mod.name]
                thir_lib_modules.append(
                    (mod.name, ast_hpp, ast_cpp, hpp_path_t, cpp_path_t))

        # Return paths for the entry point module
        layout = BuildLayout(output_dir, entry_module.name)
        hpp_path = layout.hpp_path(entry_module.name)
        cpp_path = layout.cpp_path(entry_module.name)
        ctx = entry_module.analyzer.ctx if entry_module.analyzer else None
        declared_var_types = ctx.declared_var_types if ctx else None
        # is_send()/is_sync() consult the active compiler's dynamic TypeDef
        # view, so the walk must run inside the compiler context.
        send_sync_facts = None
        frame_facts = None
        if ctx is not None:
            from tpyc.sema.frame_traits import frame_traits_of_function
            with activate_compiler(compiler):
                if declared_var_types is not None:
                    send_sync_facts = {
                        key: (t.is_send(), t.is_sync())
                        for key, t in declared_var_types.items()
                    }
                frame_facts = {
                    key: frame_traits_of_function(fi)
                    for key, fi in ctx.frame_fact_fns.items()
                }
        ptr_deref_facts = ctx.ptr_deref_facts if ctx else None
        subscript_bounds_facts = ctx.subscript_bounds_facts if ctx else None
        div_zero_facts = ctx.div_zero_facts if ctx else None
        cast_safe_facts = ctx.cast_safe_facts if ctx else None
        link_flags = compiler.collect_link_flags()
        # Resolve third-party deps for this case's own imports; modes come
        # from --dep-mode (default: bundled).
        tp_plan = resolve_build_plan(
            dep_names=compiler.collect_third_party_deps(),
            runtime_cpp_dir=RUNTIME_DIR.parent,
            modes=DEP_MODES,
        )
        return CompileResult(success=True, diagnostics=diagnostics, hpp_path=hpp_path, cpp_path=cpp_path,
                             thir_routed_names=thir_routed_names,
                             thir_modules=thir_modules,
                             thir_lib_modules=thir_lib_modules,
                             thir_ratchet_fell=thir_ratchet_fell,
                             all_modules=all_modules, declared_var_types=declared_var_types,
                             ptr_deref_facts=ptr_deref_facts,
                             subscript_bounds_facts=subscript_bounds_facts,
                             div_zero_facts=div_zero_facts,
                             cast_safe_facts=cast_safe_facts,
                             send_sync_facts=send_sync_facts,
                             frame_facts=frame_facts,
                             link_flags=link_flags,
                             third_party_include_dirs=list(tp_plan.extra_include_dirs),
                             third_party_link_flags=list(tp_plan.extra_link_flags),
                             third_party_c_sources=list(tp_plan.c_sources))

    except CompileError as e:
        return CompileResult(success=False, diagnostics=e.format() + "\n")
    except SemanticError as e:
        diag = e.format(src_file.name)
        return CompileResult(success=False, diagnostics=diag + "\n")
    except ParseError as e:
        diag = e.format(src_file.name)
        return CompileResult(success=False, diagnostics=diag + "\n")
    except CodeGenError as e:
        diag = e.format(src_file.name)
        return CompileResult(success=False, diagnostics=diag + "\n")


@dataclass
class RunResult:
    """Result of running a compiled program."""
    success: bool      # exit code == 0
    stdout: str
    stderr: str
    returncode: int
    cpp_build_failed: bool = False


def pytest_addoption(parser):
    """Register custom pytest options."""
    parser.addoption(
        "--force-exec",
        action="store_true",
        default=False,
        help="Run exec and cpython phases unconditionally, bypassing fingerprint-based auto-skip.",
    )
    parser.addoption(
        "--no-exec",
        action="store_true",
        default=False,
        help=(
            "Skip the exec phase (C++ build + run) entirely; comp and cpy "
            "phases still run. Fast iteration when only diagnostics/codegen "
            "matter. Mutually exclusive with --force-exec."
        ),
    )
    parser.addoption(
        "--no-cpy",
        action="store_true",
        default=False,
        help=(
            "Skip the CPython-parity phase (comp + exec still run). CPython "
            "output is toolchain-independent, so the nightly's C++-toolchain "
            "rows pass this and let a dedicated CPython-version axis own "
            "parity instead of re-checking it on every toolchain."
        ),
    )
    parser.addoption(
        "--build-only",
        action="store_true",
        default=False,
        help=(
            "Build (compile+link) every case's binary but skip the run, the "
            "output/panic compare, and the cpy phase. Never reads or writes "
            "the exec-results cache. Engages AUTOMATICALLY when the --cxx "
            "toolchain targets a different OS (cross builds cannot run "
            "here); the flag is the manual just-check-it-links mode. "
            "Mutually exclusive with --no-exec and --update-snapshots."
        ),
    )
    parser.addoption(
        "--clean",
        action="store_true",
        default=False,
        help=(
            "Wipe shared PCH and stdlib object caches before running. Implies --force-exec. "
            "Use to recover from suspected cache corruption or to re-verify runtime builds."
        ),
    )
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help=(
            "Regenerate expected files (diag.txt, generated code, output.txt, .fingerprints) "
            "instead of comparing. Implies --force-exec. Equivalent to UPDATE_EXPECTED=1."
        ),
    )
    parser.addoption(
        "--no-ccache",
        action="store_true",
        default=False,
        help="Do not invoke ccache for this run. Does not wipe the ccache store.",
    )
    parser.addoption(
        "--cxx",
        default="auto",
        help=(
            "C++ toolchain for the exec phase (mirrors `tpyc --cxx`): auto, "
            "list, gcc, gcc-14, clang, clang-19, zig, ... (default: auto). "
            "Switching toolchain re-keys the stdlib/PCH/exec caches, so the "
            "next run re-verifies under the new compiler."
        ),
    )
    parser.addoption(
        "--dep-mode",
        action="append",
        default=[],
        metavar="LIB=MODE",
        help=(
            "Third-party dependency mode override for the exec build, "
            "mirroring `tpyc --<lib>=<mode>`: e.g. --dep-mode pcre2=system. "
            "Repeat or comma-separate for multiple libs. Modes: bundled, "
            "system (default: each lib's default, bundled). "
            "Applies to tests/cases; the interop harness keeps CLI defaults."
        ),
    )
    parser.addoption(
        "--thir-codegen",
        action="store_true",
        default=False,
        help=(
            "THIR is ON BY DEFAULT and every user case byte-diffs THIR vs the "
            "AST snapshots; no_thir.txt only exempts a case from the RATCHET "
            "(it may fall back bodies), not from the diff. This flag ignores "
            "the markers entirely: no ratchet anywhere, plus the whole-corpus "
            "faces/shapes coverage metrics, and it implies --thir-stdlib (the "
            "stdlib oracle rides every measurement run). Pair with --no-exec "
            "for a fast comp-only run. Off (and conflicting) under "
            "--update-snapshots (snapshots must be AST-authored)."
        ),
    )
    parser.addoption(
        "--no-thir",
        action="store_true",
        default=False,
        help=(
            "Disable THIR entirely: every case emits + byte-diffs via the AST "
            "path only (no THIR overlay, no ratchet). The pure-AST mode for fast "
            "iteration on AST codegen. Conflicts with --thir-codegen and with "
            "--thir-classify / --thir-check-flip."
        ),
    )
    parser.addoption(
        "--thir-check-flip",
        action="store_true",
        default=False,
        help=(
            "Run THIR on ALL cases (ignoring no_thir.txt) and list the marked "
            "cases whose user modules now route clean -- candidates to un-mark "
            "(delete no_thir.txt). Read-only; the porting-progress query. Pair "
            "with --no-exec."
        ),
    )
    parser.addoption(
        "--thir-stdlib",
        action="store_true",
        default=False,
        help=(
            "Also route lib/tpy + the stdlib through THIR and byte-diff the "
            "result against the SAME RUN's AST output (cutover gate A5/D4: "
            "stdlib emission has no committed snapshot, so this is its only "
            "oracle). Nothing is written to expected/. Implied by "
            "--thir-codegen. Costs ~10-15% wall on a comp-only run (the extra "
            "codegen pass over the stdlib modules) -- pair with --no-exec, "
            "and with -k for a subset."
        ),
    )
    parser.addoption(
        "--thir-classify",
        action="store_true",
        default=False,
        help=(
            "(Re)write no_thir.txt markers: add for a case whose user module has "
            "any THIR fallback, remove for a clean one. The one-time bootstrap / "
            "maintenance of the per-case migration state (writes files, like "
            "--update-snapshots does)."
        ),
    )


def _cgroup_cpu_quota() -> int | None:
    """Return the effective CPU quota from cgroup v2, or None if unlimited/unavailable.

    Docker --cpus=N sets /sys/fs/cgroup/cpu.max to "<quota> <period>" where
    quota / period is the fractional CPU allowance. os.cpu_count() ignores
    this, so pytest-xdist's "-n auto" over-provisions workers in containers.
    """
    try:
        content = Path("/sys/fs/cgroup/cpu.max").read_text().strip()
    except OSError:
        return None
    parts = content.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota = int(parts[0])
        period = int(parts[1])
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    # Round up: e.g. quota=150000, period=100000 -> 2 CPUs
    return max(1, (quota + period - 1) // period)


def _exec_flag_conflict(*, no_exec: bool, build_only: bool, force_exec: bool,
                        clean: bool, updating: bool) -> str | None:
    """Return the error message for a conflicting exec-flag combination, or
    None. Extracted from pytest_configure so the flag-conflict logic is
    unit-testable (mirrors _thir_flag_conflict). `updating` is the resolved
    --update-snapshots / UPDATE_EXPECTED state."""
    if no_exec and (force_exec or clean or updating):
        return "--no-exec conflicts with --force-exec/--clean/--update-snapshots"
    if build_only and no_exec:
        return "--build-only conflicts with --no-exec (one builds, one skips building)"
    if build_only and updating:
        return ("--build-only conflicts with --update-snapshots: regenerating "
                "output.txt/panic.txt requires running the binaries")
    return None


def _thir_flag_conflict(config, updating: bool) -> str | None:
    """Return the error message for a conflicting THIR-flag combination, or None.
    Extracted from pytest_configure so the flag-conflict logic is unit-testable
    without invoking the whole (side-effecting) configure hook. `updating` is the
    resolved --update-snapshots / UPDATE_EXPECTED state."""
    forcing = (config.getoption("--thir-codegen")
               or config.getoption("--thir-classify")
               or config.getoption("--thir-check-flip"))
    if forcing and updating:
        return ("--thir-codegen / --thir-classify / --thir-check-flip conflict "
                "with --update-snapshots: snapshots must capture the default "
                "(AST) codegen path, never THIR -- the byte diff is the gate, "
                "not the baseline")
    if config.getoption("--no-thir") and forcing:
        return ("--no-thir conflicts with --thir-codegen / --thir-classify / "
                "--thir-check-flip: it disables THIR, they force it on")
    if config.getoption("--thir-stdlib") and (updating
                                              or config.getoption("--no-thir")):
        return ("--thir-stdlib needs THIR active: it conflicts with "
                "--update-snapshots and --no-thir")
    return None


def pytest_configure(config):
    """Print ccache status; manage session fingerprint file."""
    global UPDATE_EXPECTED  # assigned below; declared here so the guard can read it
    global TEST_CODEGEN_OPTIONS

    # THIR is woven into the DEFAULT run: every UNMARKED user case (no
    # no_thir.txt) asserts THIR via the per-local-module snapshot compare
    # (snapshots are AST-generated, so any diff is a THIR divergence). It is off
    # only while regenerating snapshots (which must be AST-authored). Flip before
    # the worker guard below: xdist workers do the compiling.
    global THIR_IGNORE_MARKERS, THIR_CLASSIFY_WRITE, THIR_CHECK_FLIP
    global THIR_STDLIB
    updating = bool(config.getoption("--update-snapshots")) or UPDATE_EXPECTED
    # THIR is off while regenerating snapshots (must be AST-authored) and under
    # --no-thir (pure-AST mode: emit + diff via AST only, no overlay, no ratchet).
    if not updating and not config.getoption("--no-thir"):
        TEST_CODEGEN_OPTIONS = dataclasses.replace(
            TEST_CODEGEN_OPTIONS, thir_codegen=True)
        # The cross-path move-verdict join (tpyc/move_audit.py): on whenever
        # the overlay runs, since it needs BOTH passes over the same nodes.
        # Same footing as the face tally -- it is the only detector for a
        # move-vs-copy divergence at a site whose render ignores the verdict,
        # which the byte-diff cannot see, so it must not need remembering.
        move_audit.set_enabled(True)
    # --thir-codegen / --thir-check-flip / --thir-classify run THIR on ALL user
    # cases (ignoring no_thir.txt) -- the whole-corpus check / classification;
    # the default run respects the markers so only migrated cases assert THIR.
    if (config.getoption("--thir-codegen") or config.getoption("--thir-check-flip")
            or config.getoption("--thir-classify")):
        THIR_IGNORE_MARKERS = True
    if config.getoption("--thir-classify"):
        THIR_CLASSIFY_WRITE = True
    if config.getoption("--thir-check-flip"):
        THIR_CHECK_FLIP = True
    # --thir-codegen implies the stdlib oracle: a whole-corpus measurement run
    # should also check the only oracle stdlib emission has (~10-15% wall on a
    # comp-only run, measured 2026-07-30).
    if config.getoption("--thir-stdlib") or config.getoption("--thir-codegen"):
        THIR_STDLIB = True
    # ...and the arm-residual census, on the same footing as the face tally: a
    # deletion metric that needs an env var remembered is a metric that gets
    # forgotten. The walk covers fallback bodies only -- noise against a
    # whole-corpus run. $THIR_ARM_RESIDUAL_JSON now gates only the DUMP.
    if THIR_IGNORE_MARKERS:
        thir_fallback._ARM_RESIDUAL_ON = True

    # --dep-mode: parsed before the xdist-worker early return -- workers do
    # the per-case compiles, so they need the same modes as the master.
    try:
        dep_modes = _parse_dep_modes(config.getoption("--dep-mode"))
    except ValueError as exc:
        pytest.exit(str(exc), returncode=1)
    DEP_MODES.clear()
    DEP_MODES.update(dep_modes)

    is_master = os.environ.get("PYTEST_XDIST_WORKER") is None
    if not is_master:
        return

    exec_conflict = _exec_flag_conflict(
        no_exec=config.getoption("--no-exec"),
        build_only=config.getoption("--build-only"),
        force_exec=config.getoption("--force-exec"),
        clean=config.getoption("--clean"),
        updating=updating,
    )
    if exec_conflict:
        pytest.exit(exec_conflict, returncode=1)

    conflict = _thir_flag_conflict(
        config, bool(config.getoption("--update-snapshots")) or UPDATE_EXPECTED)
    if conflict:
        pytest.exit(conflict, returncode=1)

    # Resolve the C++ toolchain first: --cxx rebuilds CPP_CONFIG (which the
    # ccache status, cache keys, and prewarm below all read) and is propagated
    # to xdist workers via $CXX -- their conftest import re-derives from it.
    cxx_opt = config.getoption("--cxx")
    if cxx_opt == "list":
        list_compilers()
        pytest.exit("--cxx list", returncode=0)
    if cxx_opt != "auto":
        try:
            chosen = CppCompilerConfig.from_env(cxx=cxx_opt)
        except CompilerNotFoundError as exc:
            pytest.exit(str(exc), returncode=1)
        chosen.warn_flags = strict_warn_flags(chosen.compiler)
        # Mutate in place so every module-level CPP_CONFIG reference sees it.
        CPP_CONFIG.compiler = chosen.compiler
        CPP_CONFIG.std = chosen.std
        CPP_CONFIG.extra_flags = chosen.extra_flags
        CPP_CONFIG.ccache = chosen.ccache
        CPP_CONFIG.warn_flags = chosen.warn_flags
        os.environ["CXX"] = " ".join(chosen.compiler)
        # The cache-key memoizers read CPP_CONFIG; drop any value computed
        # against the pre-mutation toolchain so the new --cxx re-keys cleanly.
        _stdlib_cache_key.cache_clear()
        _pch_cache_key.cache_clear()

    # After --cxx resolution so the probe sees the final compiler.
    if exec_is_cross() and updating:
        pytest.exit("cannot --update-snapshots with a cross toolchain: the "
                    "binaries cannot run here to produce output.txt",
                    returncode=1)

    if config.getoption("--no-ccache"):
        # Every compile path is gated on CPP_CONFIG.ccache, so flipping it
        # to False is sufficient -- we stop prepending `ccache` entirely
        # rather than spawning it with CCACHE_DISABLE=1 as a pass-through.
        CPP_CONFIG.ccache = False
        os.environ["TPY_TEST_NO_CCACHE"] = "1"  # propagate to xdist workers

    if config.getoption("--update-snapshots"):
        UPDATE_EXPECTED = True
        # Propagate to xdist workers (subprocesses inherit os.environ, and
        # their conftest import reads the env var at module load time).
        os.environ["UPDATE_EXPECTED"] = "1"

    if config.getoption("--clean"):
        root = _shared_cache_root()
        wiped = []
        for sub in ("pch", "stdlib-objs", "exec-results"):
            target = root / sub
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
                wiped.append(sub)
        if wiped:
            _log(f"--clean: wiped {root}/{{{','.join(wiped)}}}")
        else:
            _log(f"--clean: no cache dirs to wipe under {root}")

    if UPDATE_EXPECTED:
        # Refresh the single source-of-truth session fingerprint file once on
        # the master process. Workers see it via the file system.
        write_session_fingerprints(compute_session_fingerprints())
        return

    # Normal mode: warn when the recorded cpy-stub fingerprint is stale so the
    # user refreshes -- the CPython phase is still gated on committed
    # fingerprints, so a stale stub hash makes it re-run for every applicable
    # case. The exec phase is gated on the local exec cache now, so runtime /
    # libtpy drift no longer forces re-runs here. Skipped silently when no
    # session file exists (first-ever bootstrap).
    recorded = read_session_fingerprints()
    if recorded and compute_session_fingerprints()["cpy_stubs"] != recorded.get("cpy_stubs"):
        # This start-of-session line scrolls off the top of a long run, so also
        # record it for the end-of-run banner (surfaced in terminal_summary).
        _session_stale["cpy_stubs"] = True
        _log(
            "WARNING: cpy-stub fingerprint stale; the CPython phase will re-run "
            "for every applicable case. Refresh via update_snapshots.py.",
            err=True,
        )

    # Pre-warm persistent caches on master before workers spawn so each
    # worker hits the on-disk fast path immediately rather than serializing
    # on LOCK_EX. Without this, the first exec-phase test in each worker
    # would appear to take ~cache-build-time (~20s cold) even though the
    # actual test is quick. --no-exec runs no exec phase, so skip the build
    # (and every other exec-cache path below) -- it then needs no C++
    # toolchain at all, so a --no-exec-only run can omit the compiler.
    no_exec = config.getoption("--no-exec")
    if not no_exec:
        get_pch_header()
        get_stdlib_cache()

    # Forced modes re-verify every case by flag (the report header says so),
    # so only announce a shared-input change in normal mode -- but always
    # refresh the recorded signature so the next run has a baseline.
    forced = config.getoption("--force-exec") or config.getoption("--clean")
    if should_record_exec_env(no_exec=no_exec,
                              build_only=config.getoption("--build-only"),
                              is_cross=exec_is_cross()):
        report_exec_env_change(announce=not forced)


def pytest_report_header(config):
    """Static toolchain / build-mode summary, under the session-starts bar.

    Runs after pytest_configure (so CPP_CONFIG reflects --cxx / --no-ccache)
    and once on the xdist controller -- no manual worker guard needed. Live
    build progress stays in pytest_configure since it must print as it happens.
    """
    # Each line: current state first, then the flag that changes it.
    if config.getoption("--no-ccache"):
        ccache_state = "ccache disabled via --no-ccache"
    elif CPP_CONFIG.ccache:
        ccache_state = "using ccache  (--no-ccache to disable)"
    else:
        ccache_state = "ccache not found (install for faster re-runs)"

    if config.getoption("--no-exec"):
        exec_state = "skipped via --no-exec (comp + cpy only)"
    elif exec_is_cross():
        exec_state = (f"build-only, no run/compare/cpy (toolchain targets "
                      f"{exec_target_os()}; binaries cannot run here)")
    elif config.getoption("--build-only"):
        exec_state = "build-only: compile+link every case, no run/compare/cpy"
    elif config.getoption("--update-snapshots"):
        exec_state = "regenerating snapshots"
    elif config.getoption("--clean"):
        exec_state = "caches wiped, re-verifying every case"
    elif config.getoption("--force-exec"):
        exec_state = "re-verifying every case (forced)"
    else:
        exec_state = "verify-once-then-cache per case"

    if not TEST_CODEGEN_OPTIONS.thir_codegen:
        thir_state = ("off (--no-thir: pure AST)"
                      if config.getoption("--no-thir")
                      else "off (regenerating AST snapshots)")
    elif THIR_IGNORE_MARKERS:
        thir_state = "no ratchet (ignore no_thir.txt) + coverage metrics"
    else:
        thir_state = "default: all cases byte-diff vs AST; ratchet on unmarked"

    # Short lines (hints on their own indented lines) so nothing wraps at ~80 cols.
    dep_mode_lines = []
    if DEP_MODES:
        modes = ", ".join(f"{lib}={mode}" for lib, mode in sorted(DEP_MODES.items()))
        dep_mode_lines = [f"{_LOG_PREFIX} dep modes: {modes} (via --dep-mode)"]
    # The oracle is green, so a failure here is a fresh regression -- say that
    # where the failures are seen rather than in CLAUDE.md, which nobody opens
    # mid-run. The banner keeps the since-date: it tells a reader mid-run which
    # baseline they are being measured against.
    stdlib_lines = []
    if THIR_STDLIB:
        stdlib_lines = [
            f"{_LOG_PREFIX} thir stdlib oracle: ON -- lib/tpy + stdlib routed "
            f"through THIR and diffed vs this run's AST output",
            f"{_LOG_PREFIX}   EXPECTED GREEN since 2026-07-29. A failure here "
            f"is a new divergence, not the old backlog.",
        ]
    return [
        f"{_LOG_PREFIX} toolchain: {CPP_CONFIG.compiler_name}",
        f"{_LOG_PREFIX}   --cxx=<gcc|clang|gcc-14|zig|...> or --cxx=list to enumerate",
        *dep_mode_lines,
        f"{_LOG_PREFIX} C++ compilation: {ccache_state}",
        f"{_LOG_PREFIX} exec: {exec_state}",
        f"{_LOG_PREFIX}   --force-exec re-run all / --no-exec skip / --clean wipe caches",
        f"{_LOG_PREFIX}   --update-snapshots regenerate expected",
        f"{_LOG_PREFIX} thir: {thir_state}",
        f"{_LOG_PREFIX}   --no-thir          pure AST (no THIR overlay, no ratchet)",
        f"{_LOG_PREFIX}   --thir-codegen     no ratchet + coverage metrics + stdlib oracle",
        f"{_LOG_PREFIX}   --thir-check-flip  list marked cases now clean (un-mark)",
        f"{_LOG_PREFIX}   --thir-classify    (re)write no_thir.txt markers",
        f"{_LOG_PREFIX}   --thir-stdlib      also route lib/tpy + stdlib, diff vs AST",
        *stdlib_lines,
    ]


def pytest_xdist_auto_num_workers(config):
    """Cap '-n auto' to the cgroup v2 CPU quota.

    Under Docker --cpus=N, /sys/fs/cgroup/cpu.max enforces fractional CPU
    allowance but os.cpu_count() (which xdist uses by default) ignores it.
    Without this cap, xdist spawns one worker per visible core and they
    fight over the smaller quota, adding context-switch overhead.
    Returning None falls back to xdist's default behavior.
    """
    quota_cpus = _cgroup_cpu_quota()
    if quota_cpus is None:
        return None
    visible = os.cpu_count() or 1
    if quota_cpus < visible:
        _log(
            f"xdist: capping workers to {quota_cpus} "
            f"(cgroup v2 CPU quota; {visible} cores visible)"
        )
        return quota_cpus
    return None


def find_extra_src_files(case_dir: Path) -> list[Path]:
    """Find extra C++ source files in the test's src/ directory.

    These are compiled alongside the generated C++ to provide stub
    implementations for native functions.
    """
    src_dir = case_dir / "src"
    return sorted(src_dir.glob("*.cpp"))


def find_extra_include_dirs(case_dir: Path) -> list[Path]:
    """Find extra include directories for C++ compilation.

    If the test's src/ directory contains .h files (e.g., native type
    definitions for interop tests), returns it as an include directory.
    """
    src_dir = case_dir / "src"
    if any(src_dir.glob("*.hpp")) or any(src_dir.glob("*.h")):
        return [src_dir]
    return []


def find_force_includes(case_dir: Path) -> list[Path]:
    """Find headers to force-include before all generated code.

    Returns .h files from the test's src/ directory. These are injected
    via -include so that native type definitions are visible in generated
    headers without needing # tpy: include() directives.
    """
    src_dir = case_dir / "src"
    return sorted(src_dir.glob("*.hpp"))


# ---------------------------------------------------------------------------
# Fingerprint helpers
# ---------------------------------------------------------------------------
# Fingerprints record what inputs produced the expected runtime output. They
# split into two layers:
#
# Session-level (tests/.session_fingerprints.json, single source of truth):
#   - runtime    -- hash of runtime/cpp/include/** (affects every binary)
#   - libtpy     -- hash of lib/tpy/**             (stdlib source compiled into
#                                                   every binary; the user's
#                                                   main.cpp snapshot won't
#                                                   reflect stdlib-only codegen
#                                                   changes, so exec must re-run)
#   - cpy_stubs  -- hash of lib/cpy/tpy/**         (affects every CPython run)
#
# Per-case (tests/cases/<case>/expected/.fingerprints, optional keys):
#   - extra_src  -- hash of hand-written case_dir/src/*.{cpp,hpp,h} files
#                   (native-interop companion sources; omitted when there are none)
#   - main       -- hash of main.py
#
# Skip exec when: runtime + libtpy + extra_src all match recorded AND output exists.
# Skip cpy  when: cpy_stubs + main both match recorded AND output.txt exists.
# Generated code is verified to match expected/include + expected/src in the
# comp phase, so the binary derived from those would be identical too.

_FINGERPRINT_FILE = ".fingerprints"
SESSION_FINGERPRINT_FILE = TESTS_DIR / ".session_fingerprints.json"


def _hash_files(paths: list[Path]) -> str:
    """Hash a sequence of files by (project-relative path, content).

    Paths are made relative to PROJECT_ROOT so the hash is stable across
    worktrees / clone locations. Files outside PROJECT_ROOT (rare) fall
    back to their absolute path.
    """
    h = hashlib.sha256()
    for p in paths:
        try:
            key = str(p.relative_to(PROJECT_ROOT))
        except ValueError:
            key = str(p)
        h.update(key.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


@functools.cache
def _runtime_hash() -> str:
    """Hash of all runtime headers under runtime/cpp/include/, TPy-owned
    runtime .cpp files under runtime/cpp/src/, and vendored third_party/
    source trees. Session-cached.

    Including third_party/ ensures that bumping a vendored library version
    (PCRE2 etc.) invalidates the stdlib .o cache automatically, so the
    next test run rebuilds with the new sources. Including src/ does the
    same for TPy-owned runtime helpers.
    """
    files = sorted(p for p in RUNTIME_DIR.rglob("*") if p.is_file())
    runtime_src_dir = RUNTIME_DIR.parent / "src"
    if runtime_src_dir.is_dir():
        files += sorted(p for p in runtime_src_dir.rglob("*") if p.is_file())
    third_party_dir = RUNTIME_DIR.parent / "third_party"
    if third_party_dir.is_dir():
        files += sorted(p for p in third_party_dir.rglob("*") if p.is_file())
    return _hash_files(files)


@functools.cache
def _cpy_stubs_hash() -> str:
    """Hash of the CPython stub tree: lib/cpy/tpy/** plus the root-level
    lib/cpy/*.py (sitecustomize's runtime-@overload dispatcher, any future
    stub). Session-cached. Both feed every CPython run, so a change to either
    must re-key the cpy fingerprints."""
    stubs = sorted((CPY_LIB_DIR / "tpy").rglob("*.py"))
    stubs += sorted(CPY_LIB_DIR.glob("*.py"))
    return _hash_files(stubs)


def compute_session_fingerprints() -> dict[str, str]:
    """Compute current session-level fingerprints (runtime + libtpy + cpy stubs)."""
    return {
        "runtime": _runtime_hash(),
        "libtpy": _libtpy_hash(),
        "cpy_stubs": _cpy_stubs_hash(),
    }


def read_session_fingerprints() -> dict[str, str]:
    """Read tests/.session_fingerprints.json. Returns empty dict when missing/malformed."""
    if not SESSION_FINGERPRINT_FILE.exists():
        return {}
    try:
        data = json.loads(SESSION_FINGERPRINT_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_session_fingerprints(fingerprints: dict[str, str]) -> None:
    """Atomically write the session fingerprint file."""
    payload = json.dumps(fingerprints, indent=2, sort_keys=True) + "\n"
    tmp = SESSION_FINGERPRINT_FILE.with_suffix(".json.tmp")
    tmp.write_text(payload)
    tmp.replace(SESSION_FINGERPRINT_FILE)


def compute_extra_src_fingerprint(case_dir: Path) -> str | None:
    """Hash of hand-written C++ files in case_dir/src/ (native-interop companions).

    Picks up the same .cpp/.hpp/.h files as find_extra_src_files / find_force_includes.
    Returns None when there are no such files (most cases).
    """
    src_dir = case_dir / "src"
    inputs: list[Path] = []
    for pattern in ("*.cpp", "*.hpp", "*.h"):
        inputs.extend(src_dir.glob(pattern))
    if not inputs:
        return None
    inputs.sort()
    return _hash_files(inputs)


def compute_main_fingerprint(main_src: Path) -> str:
    """Hash of main.py content alone."""
    return hashlib.sha256(main_src.read_bytes()).hexdigest()


def read_fingerprints(case_dir: Path) -> dict[str, str]:
    """Read expected/.fingerprints. Returns empty dict when missing/malformed."""
    path = case_dir / "expected" / _FINGERPRINT_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_fingerprints(case_dir: Path, fingerprints: dict[str, str]) -> None:
    """Write expected/.fingerprints as JSON, or remove it when empty."""
    path = case_dir / "expected" / _FINGERPRINT_FILE
    if not fingerprints:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fingerprints, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Local exec-pass cache (gitignored, content-addressed)
# ---------------------------------------------------------------------------
# Exec skip is local, not committed: a committed fingerprint records what
# produced a snapshot, not whether the C++ build+run passes on THIS toolchain.
# A marker named by the full input set (toolchain + runtime + stdlib +
# generated code + companions + link flags + stdin fixture) means "this exact
# build ran green here". Pure content-addressing makes it safe to share across
# worktrees -- identical cases dedup, divergent ones can't collide.

def _exec_results_dir() -> Path:
    return _shared_cache_root() / "exec-results"


def merge_link_flags(case_flags: list[str], cache_flags: list[str]) -> list[str]:
    """Case-own third-party link flags + the stdlib cache's (system-mode)
    flags, order-preserving and deduped. Every case links the full cache .o
    set, so the cache's flags apply imported-or-not."""
    return list(case_flags) + [f for f in cache_flags if f not in case_flags]


def exec_target_os() -> str:
    """OS the exec-phase toolchain targets (probed once per compiler)."""
    return compiler_target_os(tuple(CPP_CONFIG.compiler))


def exec_is_cross() -> bool:
    """True when exec builds target a different OS than the host: the
    binaries cannot run here, so the exec phase auto-degrades to
    build-only."""
    return exec_target_os() != host_os()


def plan_exec_phase(*, no_exec: bool, build_only: bool, force_exec: bool,
                    expected_exists: bool, marker_hit: bool) -> str:
    """Pure per-case exec-phase decision: 'disabled' | 'skipped' | 'build' |
    'build+run'.

    'build' (--build-only) compiles+links but never runs; it ignores the
    marker cache in BOTH directions -- no skip on a hit (the mode's answer
    must be current) and no pass recorded (built != ran green) -- and it
    outranks --force-exec (redundant but allowed). 'disabled' (--no-exec)
    outranks everything; the no_exec+build_only combination is rejected by
    _exec_flag_conflict before this is reached."""
    if no_exec:
        return "disabled"
    if build_only:
        return "build"
    if not force_exec and expected_exists and marker_hit:
        return "skipped"
    return "build+run"


def cpy_phase_applicable(*, build_only: bool, no_cpy: bool, is_panic: bool,
                         no_cpython_marker: bool, output_exists: bool) -> bool:
    """Pure per-case CPython-parity-phase decision (mirrors plan_exec_phase
    so the multi-flag gate is unit-testable). Skipped for: build-only / cross
    (toolchain check only, parity covered by ordinary runs), --no-cpy (the
    nightly's C++-toolchain rows, where a dedicated CPython-version axis owns
    parity), a panic case (compared against panic.txt, not output.txt), a
    no_cpython.txt marker, or a case with no committed output.txt."""
    return (not build_only and not no_cpy and not is_panic
            and not no_cpython_marker and output_exists)


def should_record_exec_env(*, no_exec: bool, build_only: bool,
                           is_cross: bool) -> bool:
    """Pure decision: does this run record/announce the shared exec-env
    signature (which itself builds the stdlib cache via _exec_shared_env)?
    Only when the run actually exercises exec -- --no-exec / --build-only / a
    cross toolchain neither read nor write exec state. Recording there would,
    for --no-exec, defeat the toolchain-free skip, and for build-only make the
    NEXT normal run announce a spurious whole-suite re-verify."""
    return not no_exec and not build_only and not is_cross


def compute_exec_fingerprint(
    case_dir: Path,
    all_modules: list[tuple[str, Path | None, Path | None, bool]],
    link_flags: list[str],
    third_party_link_flags: list[str],
    stdlib_output_hash: str = "",
) -> str:
    """Hash everything that determines a case's binary and its stdout.

    Keyed on compiler *output*, not *source*: the generated C++ for every
    module the case pulls in, the build environment (runtime headers via
    `_runtime_hash`, which also covers `third_party/`; and the toolchain),
    plus per-case companions/link-flags/`src/input.txt`. `_tpyc_hash`/
    `_libtpy_hash` are deliberately NOT folded in -- compiler/stdlib source
    reaches the binary only through the generated C++ hashed here, so an edit
    that leaves the emitted C++ identical reuses the cache.

    `stdlib_output_hash` pins the WHOLE precompiled stdlib set: every binary
    links that full `.o` set with no dead-stripping, so an unimported module's
    change still alters the binary -- without this it would be a stale-green
    skip. Empty when no cache is used (the case then compiles+links only its
    imports, which `all_modules` already covers).

    System-mode third-party libs (--dep-mode) are captured only via their
    link FLAGS -- a system-lib upgrade does not re-key, so a system-mode run
    can stale-green skip across it (the nightly neutralizes this with
    --force-exec).
    """
    h = hashlib.sha256()
    h.update(_runtime_hash().encode())
    h.update(b"\0")
    h.update(stdlib_output_hash.encode())
    h.update(b"\0")
    h.update(repr((
        CPP_CONFIG.compiler,
        CPP_CONFIG.std,
        CPP_CONFIG.extra_flags,
        CPP_CONFIG.warn_flags,
    )).encode())
    h.update(b"\0")
    gen_files: list[Path] = []
    for _name, hpp, cpp, _is_local in all_modules:
        for p in (hpp, cpp):
            if p is not None and p.exists():
                gen_files.append(p)
    gen_files.sort()
    h.update(_hash_files(gen_files).encode())
    h.update(b"\0")
    h.update((compute_extra_src_fingerprint(case_dir) or "").encode())
    h.update(b"\0")
    h.update(repr((list(link_flags), list(third_party_link_flags))).encode())
    h.update(b"\0")
    input_txt = case_dir / "src" / "input.txt"
    if input_txt.exists():
        h.update(input_txt.read_bytes())
    return h.hexdigest()


def exec_pass_is_cached(fingerprint: str) -> bool:
    """True when a green-exec marker for this fingerprint exists locally."""
    try:
        return (_exec_results_dir() / fingerprint).exists()
    except OSError:
        return False


def record_exec_pass(fingerprint: str, case_id: str) -> None:
    """Record that the build+run for this fingerprint passed on this machine.

    Best-effort: a cache that can't be written just means exec re-runs next
    time, never a failure. The marker stores the case id for debuggability;
    written via a pid-tagged temp + atomic rename so concurrent xdist workers
    don't clobber each other.
    """
    try:
        d = _exec_results_dir()
        d.mkdir(parents=True, exist_ok=True)
        marker = d / fingerprint
        if marker.exists():
            return
        tmp = d / f"{fingerprint}.{os.getpid()}.tmp"
        tmp.write_text(case_id + "\n")
        tmp.replace(marker)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CPython interop (ext-exec) cases
# ---------------------------------------------------------------------------
# Cases under tests/interop/<case>/: an `# tpy: ext_module` source plus a
# driver.py (and optional ext_checks.py). The ext-exec harness builds the
# source into an importable .so, runs driver.py under CPython against it, and
# asserts the SAME driver over the TPy source (lib/cpy stubs) matches.

def discover_interop_cases() -> list[tuple[str, Path, Path]]:
    """Discover (name, case_dir, module_py) for every ext-exec interop case.

    A case is any tests/interop/<case>/ directory whose src/ holds a
    driver.py; the module source is the lone `# tpy: ext_module` .py beside
    it (driver.py and ext_checks.py are excluded). Inputs live under src/,
    mirroring tests/cases; expected/ and the gitignored __tpyc__/ build
    output stay at the case root.
    """
    cases: list[tuple[str, Path, Path]] = []
    if not INTEROP_DIR.is_dir():
        return cases
    for case_dir in sorted(p for p in INTEROP_DIR.iterdir() if p.is_dir()):
        src_dir = case_dir / "src"
        if not (src_dir / "driver.py").exists():
            continue
        mod_py: Path | None = None
        for py in sorted(src_dir.glob("*.py")):
            if py.name in ("driver.py", "ext_checks.py"):
                continue
            if "# tpy: ext_module" in py.read_text():
                mod_py = py
                break
        if mod_py is not None:
            cases.append((case_dir.name, case_dir, mod_py))
    return cases


def compute_ext_exec_fingerprint(
    gen_cpp_files: list[Path], companion_files: list[Path]
) -> str:
    """Hash everything that determines an ext-exec case's .so and its driver
    output: the generated module + glue C++, the runtime (which includes the
    `tpy/interop/` facade + marshallers via `_runtime_hash`), the toolchain,
    and the driver.py / ext_checks.py companions. Compiler/stdlib source reach
    the .so only through the generated C++ hashed here, so an edit that leaves
    the emission identical reuses the marker -- same contract as
    `compute_exec_fingerprint`."""
    h = hashlib.sha256()
    h.update(_runtime_hash().encode())
    h.update(b"\0")
    h.update(repr((
        CPP_CONFIG.compiler, CPP_CONFIG.std,
        CPP_CONFIG.extra_flags, CPP_CONFIG.warn_flags,
    )).encode())
    h.update(b"\0")
    h.update(_hash_files(sorted(p for p in gen_cpp_files if p.exists())).encode())
    h.update(b"\0")
    h.update(_hash_files(sorted(p for p in companion_files if p.exists())).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Wholesale-reverify cause diagnostic
# ---------------------------------------------------------------------------
# Three inputs are shared by EVERY case's exec fingerprint -- toolchain,
# runtime headers, and the full stdlib output (the whole .o set is linked
# into every binary). When any changes, all markers re-key and the entire
# suite rebuilds. We record this signature per-checkout and, at the next
# normal run, name what changed so a wholesale re-verify isn't a mystery.

_EXEC_ENV_LABELS = {
    "runtime": "runtime headers",
    "toolchain": "toolchain",
    "stdlib_output": "stdlib output",
}


def _exec_shared_env() -> dict[str, str]:
    cache = get_stdlib_cache()
    return {
        "runtime": _runtime_hash(),
        "toolchain": repr((
            CPP_CONFIG.compiler, CPP_CONFIG.std,
            CPP_CONFIG.extra_flags, CPP_CONFIG.warn_flags,
        )),
        "stdlib_output": cache.output_hash if cache else "",
    }


def _exec_env_record_path() -> Path:
    # Per-checkout (not shared across worktrees): "since the last run" should
    # mean this tree's last run, not a sibling worktree with different output.
    key = hashlib.sha256(str(PROJECT_ROOT).encode()).hexdigest()[:16]
    return _shared_cache_root() / f"exec-env-{key}.json"


def report_exec_env_change(announce: bool) -> None:
    """Name a shared-input change that invalidates every exec marker.

    Best-effort: always refreshes the recorded signature (so the next run has
    a baseline), but only emits the `tpy|` line when *announce* -- forced
    modes re-verify by flag and the report header already says so.
    """
    path = _exec_env_record_path()
    current = _exec_shared_env()
    try:
        prior = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        prior = {}
    if announce and isinstance(prior, dict) and prior:
        changed = [_EXEC_ENV_LABELS[k] for k in _EXEC_ENV_LABELS
                   if prior.get(k) != current[k]]
        if changed:
            _log(f"exec: {' + '.join(changed)} changed since last run "
                 f"-> every case re-verifies (build+run)")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # temp + rename so a concurrent run in this checkout can't read a torn file
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        tmp.replace(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Exec-phase tally (reported in the terminal summary)
# ---------------------------------------------------------------------------
# Each test increments one bucket. Under xdist each worker tallies into its
# own process-local dict, ships it home via `workeroutput` at session end, and
# the controller folds the per-worker dicts into `_exec_tally_agg`. The
# terminal summary (controller-side) sums both: under xdist the controller's
# own `_exec_tally` stays at zero (it runs no tests), and without xdist the
# aggregate stays zero -- so the sum is correct either way.

_exec_tally = {"ran": 0, "skipped": 0, "disabled": 0, "built": 0}
_exec_tally_agg = {"ran": 0, "skipped": 0, "disabled": 0, "built": 0}


def record_exec_outcome(outcome: str) -> None:
    """Tally one case's exec-phase outcome: 'ran', 'skipped', 'disabled',
    or 'built' (--build-only: linked, not run)."""
    if outcome in _exec_tally:
        _exec_tally[outcome] += 1


# Cases whose committed cpy fingerprint was stale (forced a cpy re-run). A
# plain warnings.warn is easy to miss mid-run and leaves the session GREEN, so
# these are also tallied and surfaced as a bold-yellow banner at session end.
_stale_fp: list[str] = []
_stale_fp_agg: list[str] = []

# Session-global stale-fingerprint flags (currently just the cpy-stub hash).
# Set in pytest_configure, read by the end-of-run banner. Unlike the per-case
# tally these need no worker->controller plumbing: pytest_configure and
# pytest_terminal_summary both run on the controller, so the value set there is
# the one the banner reads.
_session_stale = {"cpy_stubs": False}


def record_stale_fingerprint(case_id: str) -> None:
    """Tally one case whose committed cpy fingerprint was stale."""
    _stale_fp.append(case_id)


def stale_fingerprint_cases() -> list[str]:
    """Sorted, de-duplicated union of this process's and folded workers' stale cases."""
    return sorted(set(_stale_fp) | set(_stale_fp_agg))


# THIR routed-body tally -- the non-vacuity guard for the --thir-codegen gate.
# Mirrors the exec tally's per-worker -> controller aggregation. Under forced
# THIR a full run that routes zero bodies means the flag stopped reaching
# codegen (a wiring regression), so the byte-diff gate would pass vacuously --
# fail loudly. A filtered run (-k or an explicit path) may legitimately select
# only ineligible cases, so there we warn instead of failing.
_thir_tally = {"bodies": 0, "cases": 0}
_thir_tally_agg = {"bodies": 0, "cases": 0}


def record_thir_routed(bodies: int) -> None:
    """Tally one case's count of function bodies lowered through THIR."""
    _thir_tally["bodies"] += bodies
    if bodies:
        _thir_tally["cases"] += 1


# Per-case user-module THIR cleanliness (THIR is scoped to user modules; lib/
# stdlib stay AST). A case is "clean" when zero user bodies fell back -- i.e.
# the gate isn't hiding any gap, so it stays byte-green when the gate is
# dropped -> a THIR-active candidate (no_thir=false). The starting count is the
# migration's real per-case metric (see CLAUDE.md "THIR migration").
_thir_cases: dict[str, int] = {"clean": 0, "total": 0}
_thir_cases_agg: dict[str, int] = {"clean": 0, "total": 0}


def record_thir_case(fell_back: int) -> None:
    """One THIR-active (non-no_thir) case: clean iff no user body fell back."""
    _thir_cases["total"] += 1
    if fell_back == 0:
        _thir_cases["clean"] += 1


def record_thir_case_marked() -> None:
    """One no_thir-marked case on the default run: counted not-clean in the
    denominator from the MARKER, deliberately ignoring the fallback count the
    overlay just measured for it. Marked-but-clean is benign porting progress
    (`--thir-check-flip` turns it into un-mark candidates), so the dial must not
    read it as migrated on its own."""
    _thir_cases["total"] += 1


# --thir-codegen / --thir-check-flip / --thir-classify run THIR on every case
# ignoring markers; classify (re)writes them; check-flip reports un-mark
# candidates. Set in pytest_configure.
THIR_IGNORE_MARKERS = False
THIR_CLASSIFY_WRITE = False
THIR_CHECK_FLIP = False
THIR_STDLIB = False

# no_thir.txt-marked cases that came back clean under --thir-check-flip: the
# un-mark candidates. Aggregated worker -> controller like the tallies.
_thir_flip: list[str] = []
_thir_flip_agg: list[str] = []


def record_thir_flip_candidate(case: str) -> None:
    _thir_flip.append(case)


_NO_THIR_BODY = (
    "user module has bodies outside the THIR slice; not yet migrated.\n"
    "Auto-managed by --thir-classify; run --thir-check-flip to see if this\n"
    "case is now clean enough to un-mark (delete this file).\n"
)


def _thir_case_mode(*, thir_codegen: bool, no_thir: bool, ignore_markers: bool,
                    classify: bool, check_flip: bool) -> tuple[bool, bool]:
    """`(overlay_runs, ratchet_applies)` for one case -- the two decisions the
    marker used to conflate.

    The overlay runs for EVERY case whenever THIR is on. `no_thir.txt` is
    per-CASE but fallback is per-BODY: a marked case still routes the bodies
    that do lower, and those must be byte-diffed against the AST oracle or they
    can silently regress to AST (a fallback emits byte-identical C++, so no
    other check sees it).

    The ratchet stays marker-gated: only an unmarked case must route every user
    body. Classify/check-flip consume the raw fallback count instead.
    """
    overlay = thir_codegen
    ratchet = (thir_codegen and not no_thir and not ignore_markers
               and not classify and not check_flip)
    return overlay, ratchet


def _apply_no_thir_marker(case_dir: Path, dirty: bool) -> None:
    """Add no_thir.txt for a dirty case, remove it for a clean one."""
    marker = case_dir / "no_thir.txt"
    if dirty and not marker.exists():
        marker.write_text(_NO_THIR_BODY)
    elif not dirty and marker.exists():
        marker.unlink()


# tests/interop ext-exec cases: their own THIR dial, deliberately NOT folded
# into _thir_cases. The migration's steering number and the wave tooling's
# fallback histogram are both keyed to the tests/cases corpus; mixing a second
# corpus into either would move the denominator and make every recorded wave
# measurement non-comparable.
#
# The per-FACE witness tally is excluded on the same grounds, and the exclusion
# is knowing rather than incidental: a face only an interop case reaches then
# reads as zero-witness. That errs toward building a witness that already
# exists -- wasted work, never a missed divergence -- which is the direction to
# err in for a metric whose job is to name shapes nothing exercises.
_interop_thir: dict[str, int] = {"clean": 0, "total": 0, "routed": 0}
_interop_thir_agg: dict[str, int] = {"clean": 0, "total": 0, "routed": 0}


@dataclasses.dataclass
class InteropThirResult:
    """One interop case's overlay outcome: byte-diff reports (empty when the
    two paths agree) and the user-body fallback count.

    `ratchet_fell` is None when the ratchet does not govern this case (marked,
    or a marker-writing flag run) -- distinct from 0, which is a governed case
    that routed everything. Collapsing the two would make a marked-case test
    unable to tell marker gating from an empty count.
    """
    divergences: list[str]
    ratchet_fell: 'int | None'


def run_interop_thir_overlay(mod_py: Path, case_dir: Path,
                             out_dir: Path) -> 'InteropThirResult | None':
    """Emit an interop case's user modules twice -- AST oracle and THIR -- and
    diff the pair. None when THIR is off for this run (--no-thir / updating).

    Unlike the tests/cases overlay this compares the two emissions to EACH
    OTHER rather than to expected/: the ext-exec harness drives the real tpyc
    CLI, which emits at the default emit_source_comments=False, so its
    snapshots carry no source comments and a THIR-vs-snapshot diff would be
    blind to the whole comment-trivia class. Emitting both sides here with
    comments on (the tests/cases setting) restores that sensitivity; the
    snapshot check itself stays with the CLI emit, which is unaffected.
    """
    if not TEST_CODEGEN_OPTIONS.thir_codegen:
        return None
    no_thir = (case_dir / "no_thir.txt").exists()
    overlay, ratchet = _thir_case_mode(
        thir_codegen=TEST_CODEGEN_OPTIONS.thir_codegen, no_thir=no_thir,
        ignore_markers=THIR_IGNORE_MARKERS, classify=THIR_CLASSIFY_WRITE,
        check_flip=THIR_CHECK_FLIP)
    if not overlay:
        return None

    compiler = Compiler(mod_py, lib_dirs=DEFAULT_LIB_DIRS)
    compiled_modules = compiler.compile()
    entry_module = next(m for m in compiled_modules if m.is_entry_point)
    src_dir = mod_py.parent.resolve()
    local_mods = []
    for mod in compiled_modules:
        try:
            mod.path.resolve().relative_to(src_dir)
        except ValueError:
            continue
        local_mods.append(mod)

    # no_main mirrors what the CLI does for an `# tpy: ext_module` (a .so has
    # no main()); both sides share it, so it cannot itself cause a diff.
    base = dataclasses.replace(TEST_CODEGEN_OPTIONS,
                               no_main=compiler.is_ext_module_build())
    emitted: dict[str, dict[str, Path | None]] = {}
    for label, thir in (("ast", False), ("thir", True)):
        opts = dataclasses.replace(base, thir_codegen=thir)
        for mod in local_mods:
            hpp_path, cpp_path = compiler.generate_code(
                mod, out_dir / label, entry_module_name=entry_module.name,
                options=opts)
            files = emitted.setdefault(mod.name, {})
            files[f"{label}.hpp"] = hpp_path
            files[f"{label}.cpp"] = cpp_path
            # The CPython glue TU rides alongside the module .cpp. It has no
            # THIR path today, so diffing it pins that it stays insensitive.
            if cpp_path is not None:
                glue = Path(cpp_path).with_name(f"{Path(cpp_path).stem}_ext.cpp")
                files[f"{label}.ext"] = glue if glue.exists() else None

    fell = sum(compiler._thir_fallback.values())
    routed_names = dict(compiler._thir_routed_names)
    divergences: list[str] = []
    for mod_name, files in sorted(emitted.items()):
        names = routed_names.get(mod_name, frozenset())
        for kind in ("hpp", "cpp", "ext"):
            ast_path = files.get(f"ast.{kind}")
            thir_path = files.get(f"thir.{kind}")
            if ast_path is None and thir_path is None:
                continue  # neither path emits this file (no glue, no .cpp)
            if ast_path is None or thir_path is None:
                # Emitted on one path only -- itself a divergence, and one a
                # text compare can never reach.
                emitted_on = "THIR" if ast_path is None else "the AST oracle"
                divergences.append(
                    f"{mod_name}.{kind}: emitted by {emitted_on} only")
                continue
            oracle = Path(ast_path).read_text()
            actual = Path(thir_path).read_text()
            if oracle == actual:
                continue
            label = _thir_divergence_label(oracle, actual, names)
            divergences.append(
                f"{mod_name}.{kind}: THIR diverges from the AST oracle "
                f"{label}\n"
                + _format_unified_diff(oracle, actual, "ast", "thir"))

    # The move-verdict join, on the same footing as the main corpus path: both
    # passes ran on THIS compiler, so the verdicts are joinable here too.
    # Without this the interop half records verdicts nobody ever compares,
    # while CLAUDE.md promises the gate covers every routed body.
    move_diffs = move_audit.disagreements(compiler)
    record_move_verdicts(f"interop/{case_dir.name}", move_diffs,
                         joined=move_audit.joined(compiler))
    for name, ast_v, thir_v, fn in move_diffs:
        divergences.append(
            f"move-verdict divergence in `{fn or '?'}`: `{name}` "
            f"ast={ast_v} thir={thir_v} -- "
            f"one path moves where the other copies. A wrong verdict at a "
            f"site whose render ignores it emits identical C++, so the text "
            f"compare above cannot catch this.")

    _interop_thir["routed"] += compiler._thir_routed_bodies
    if THIR_CLASSIFY_WRITE:
        _apply_no_thir_marker(case_dir, dirty=(fell > 0))
    elif THIR_CHECK_FLIP:
        if no_thir and fell == 0:
            record_thir_flip_candidate(str(case_dir))
    else:
        _interop_thir["total"] += 1
        # Marked cases count not-clean from the MARKER, as in the main dial.
        if ratchet and fell == 0:
            _interop_thir["clean"] += 1
    return InteropThirResult(divergences=divergences,
                             ratchet_fell=fell if ratchet else None)


# THIR per-face witness tally (tpyc/thir/faces.py) -- byte-diff green only
# proves the ROUTED code matched; a face no corpus case reaches is invisible
# to it, so the summary names registered faces with zero witnesses across the
# run. Aggregated worker -> controller like the tallies above.
_thir_faces: dict[str, int] = {}
_thir_faces_agg: dict[str, int] = {}


def record_thir_faces(witnesses: dict[str, int]) -> None:
    """Fold one case's per-face witness counts (empty when the flag is off)."""
    for face, n in witnesses.items():
        _thir_faces[face] = _thir_faces.get(face, 0) + n


# THIR per-component AST-fallback tally (tpyc/thir/fallback.py) -- the
# routed tally's complement: `component:reason` counts of bodies the gate
# rejected, so the gap to each deletion target is measured. Aggregated
# worker -> controller like the tallies above.
_thir_fallback: dict[str, int] = {}
_thir_fallback_agg: dict[str, int] = {}


def record_thir_fallback(counts: dict[str, int]) -> None:
    """Fold one case's fallback-reason counts (empty when the flag is off)."""
    for key, n in counts.items():
        _thir_fallback[key] = _thir_fallback.get(key, 0) + n


# Cross-path move-verdict divergences (tpyc/move_audit.py): `case -> [(name,
# ast, thir)]` for names the two paths judge differently at a last use in a
# ROUTED body. FAILS the case -- the corpus is at zero, and a wrong verdict at
# a site whose render ignores it emits identical C++, so this is the only
# thing standing between such a divergence and a silent miscompile later.
# The summary line still prints at zero: an absent line and a silently
# unwired detector would otherwise look the same.
_move_verdicts: dict[str, list] = {}
_move_verdicts_agg: dict[str, list] = {}


_move_joined = [0]
_move_joined_agg = [0]


def record_move_verdicts(case: str, diffs: list, joined: int) -> None:
    # `joined` is REQUIRED, not defaulted: the denominator exists because "0
    # divergences" over an unknown count is a silently-unwired detector, and a
    # default of 0 would reintroduce exactly that one layer up.
    _move_joined[0] += joined
    if diffs:
        _move_verdicts[case] = diffs


# Per-construct arm residual: fallback bodies CONTAINING each construct (the
# deletion metric, gated on $THIR_ARM_RESIDUAL_JSON). See
# tpyc/thir/fallback.record_arm_residual.
_thir_arm_residual: dict[str, int] = {}
_thir_arm_residual_agg: dict[str, int] = {}


def record_thir_arm_residual(counts: dict[str, int]) -> None:
    """Fold one case's per-construct arm-residual (empty when the flag is off)."""
    for key, n in counts.items():
        _thir_arm_residual[key] = _thir_arm_residual.get(key, 0) + n


# THIR per-shape tally (tpyc/thir/shape.py) -- the distinct-shape complement of
# the body-weighted routed count. `signature -> {slot: count}`; a signature is
# fully routed iff it has no non-`routed` slot. Aggregated worker -> controller
# by additive inner-dict merge, like the tallies above.
_thir_shapes: dict[str, dict[str, int]] = {}
_thir_shapes_agg: dict[str, dict[str, int]] = {}


def _fold_shapes(dst: dict[str, dict[str, int]], src: dict[str, dict[str, int]]) -> None:
    for sig, inner in src.items():
        d = dst.setdefault(sig, {})
        for slot, n in inner.items():
            d[slot] = d.get(slot, 0) + n


def record_thir_shapes(shapes: dict[str, dict[str, int]]) -> None:
    """Fold one case's per-shape slot counts (empty when the flag is off)."""
    _fold_shapes(_thir_shapes, shapes)


# THIR divergence reporter -- one label per failed generated-code snapshot
# under --thir-codegen ("<case> <file>: in `fn` [THIR-routed]"), aggregated
# worker -> controller like the tallies and echoed in the terminal summary so
# a failing byte-diff names its diverging functions without scanning diffs.
_thir_divergences: list[str] = []
_thir_divergences_agg: list[str] = []


def record_thir_divergence(label: str) -> None:
    _thir_divergences.append(label)


def _thir_gate_verdict(config) -> str:
    """Verdict for the THIR non-vacuity gate: 'off' (THIR disabled, e.g.
    --update-snapshots), 'ok' (routed > 0), 'warn' (routed 0 on a filtered run),
    or 'fail' (routed 0 over a full run -- a regression made the byte-diff gate
    vacuous). THIR is on by default now, so this keys on the resolved option."""
    if not TEST_CODEGEN_OPTIONS.thir_codegen:
        return "off"
    if _thir_tally["bodies"] + _thir_tally_agg["bodies"] > 0:
        return "ok"
    # file_or_dir holds positional path/nodeid args, keyword holds -k; either
    # means a deliberate subset, where selecting zero eligible cases is fine.
    # Coarse: explicit paths that still cover the full corpus (`pytest tests
    # tpyc`) read as filtered, and `--collect-only` reads as full -- both
    # mis-verdict non-canonical runs. The canonical gate is path-less; see TODO.
    filtered = bool(getattr(config.option, "keyword", "")) or \
        bool(getattr(config.option, "file_or_dir", []))
    return "warn" if filtered else "fail"


def pytest_sessionfinish(session):
    """xdist worker: ship this process's tallies to the controller. Controller
    (or non-xdist): fail the session if the --thir-codegen gate was vacuous."""
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput["exec_tally"] = dict(_exec_tally)
        workeroutput["thir_tally"] = dict(_thir_tally)
        workeroutput["thir_faces"] = dict(_thir_faces)
        workeroutput["thir_fallback"] = dict(_thir_fallback)
        workeroutput["thir_arm_residual"] = dict(_thir_arm_residual)
        workeroutput["thir_shapes"] = _thir_shapes
        workeroutput["thir_divergences"] = list(_thir_divergences)
        workeroutput["move_verdicts"] = dict(_move_verdicts)
        workeroutput["move_joined"] = _move_joined[0]
        workeroutput["thir_cases"] = dict(_thir_cases)
        workeroutput["interop_thir"] = dict(_interop_thir)
        workeroutput["thir_flip"] = list(_thir_flip)
        workeroutput["stale_fp"] = list(_stale_fp)
        return
    if _thir_gate_verdict(session.config) == "fail":
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_testnodedown(node, error):
    """xdist controller: fold a finished worker's tallies into the totals."""
    wo = getattr(node, "workeroutput", {})
    tally = wo.get("exec_tally")
    if tally:
        for k in _exec_tally_agg:
            _exec_tally_agg[k] += tally.get(k, 0)
    thir = wo.get("thir_tally")
    if thir:
        for k in _thir_tally_agg:
            _thir_tally_agg[k] += thir.get(k, 0)
    for face, n in wo.get("thir_faces", {}).items():
        _thir_faces_agg[face] = _thir_faces_agg.get(face, 0) + n
    for key, n in wo.get("thir_fallback", {}).items():
        _thir_fallback_agg[key] = _thir_fallback_agg.get(key, 0) + n
    for key, n in wo.get("thir_arm_residual", {}).items():
        _thir_arm_residual_agg[key] = _thir_arm_residual_agg.get(key, 0) + n
    _fold_shapes(_thir_shapes_agg, wo.get("thir_shapes", {}))
    _thir_divergences_agg.extend(wo.get("thir_divergences", []))
    _move_verdicts_agg.update(wo.get("move_verdicts", {}))
    _move_joined_agg[0] += wo.get("move_joined", 0)
    tc = wo.get("thir_cases")
    if tc:
        for k in _thir_cases_agg:
            _thir_cases_agg[k] += tc.get(k, 0)
    it = wo.get("interop_thir")
    if it:
        for k in _interop_thir_agg:
            _interop_thir_agg[k] += it.get(k, 0)
    _thir_flip_agg.extend(wo.get("thir_flip", []))
    _stale_fp_agg.extend(wo.get("stale_fp", []))


def _emit_stale_fingerprint_banner(terminalreporter):
    """Emit the bold-yellow STALE FINGERPRINTS banner (a no-op when nothing is
    stale). Stale committed fingerprints only warn (the session stays GREEN) and
    the signals scatter -- the cpy-stub line scrolls off the top at session
    start, the per-case warnings hide in the warnings summary -- so one banner
    collects both, and a stale fingerprint can't be missed whichever kind fired.

    Kept standalone (not inline in pytest_terminal_summary) so it can be tested
    against a fake reporter in isolation: driving the whole summary would also
    emit the exec/thir tally lines from shared session state, coupling any
    banner assertion to unrelated globals.
    """
    stale_cases = stale_fingerprint_cases()
    if not (_session_stale["cpy_stubs"] or stale_cases):
        return
    terminalreporter.write_sep(
        "=", "STALE FINGERPRINTS", yellow=True, bold=True)
    if _session_stale["cpy_stubs"]:
        terminalreporter.write_line(
            f"{_LOG_PREFIX} cpy-stub fingerprint stale -- the CPython phase "
            f"re-ran for every applicable case. Refresh the whole suite:",
            yellow=True, bold=True)
        terminalreporter.write_line(
            f"{_LOG_PREFIX}   uv run python tests/update_snapshots.py",
            yellow=True)
    if stale_cases:
        terminalreporter.write_line(
            f"{_LOG_PREFIX} {len(stale_cases)} case(s) re-ran cpy on a stale "
            f"committed fingerprint -- refresh individually:",
            yellow=True, bold=True)
        for c in stale_cases:
            terminalreporter.write_line(
                f"{_LOG_PREFIX}   uv run python "
                f"tests/update_snapshots.py -k {c}",
                yellow=True)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    _emit_stale_fingerprint_banner(terminalreporter)

    total = {k: _exec_tally[k] + _exec_tally_agg[k] for k in _exec_tally}
    considered = total["ran"] + total["skipped"]
    if total["disabled"]:
        terminalreporter.write_line(
            f"{_LOG_PREFIX} exec: disabled via --no-exec "
            f"({total['disabled']} cases not built/run)"
        )
    elif total["built"]:
        terminalreporter.write_line(
            f"{_LOG_PREFIX} exec: {total['built']} built without running "
            f"({'cross target' if exec_is_cross() else '--build-only'})"
        )
    elif considered:
        terminalreporter.write_line(
            f"{_LOG_PREFIX} exec: {total['ran']} built+run, "
            f"{total['skipped']} skipped via cache ({considered} cases)"
        )

    if THIR_CHECK_FLIP:
        flips = sorted(set(_thir_flip) | set(_thir_flip_agg))
        terminalreporter.write_line(
            f"{_LOG_PREFIX} thir flip candidates: {len(flips)} no_thir case(s) "
            f"now clean -- remove no_thir.txt to make THIR-active:"
        )
        for c in flips:
            terminalreporter.write_line(f"{_LOG_PREFIX}   {c}")

    verdict = _thir_gate_verdict(config)
    if verdict != "off":
        # The interop corpus reports before the tests/cases block and outside
        # its `ok` gate: that gate is the tests/cases vacuity check, which a
        # run filtered to the interop module always trips, and the interop
        # overlay ran regardless. Kept out of the migration dial on purpose --
        # that number is keyed to tests/cases.
        icl = _interop_thir["clean"] + _interop_thir_agg["clean"]
        itot = _interop_thir["total"] + _interop_thir_agg["total"]
        irouted = _interop_thir["routed"] + _interop_thir_agg["routed"]
        if itot:
            terminalreporter.write_line(
                f"{_LOG_PREFIX} interop thir: {icl}/{itot} migrated; "
                f"{irouted} bodies routed"
            )
        bodies = _thir_tally["bodies"] + _thir_tally_agg["bodies"]
        cases = _thir_tally["cases"] + _thir_tally_agg["cases"]
        if verdict == "ok":
            terminalreporter.write_line(
                f"{_LOG_PREFIX} thir: {bodies} bodies routed via THIR "
                f"across {cases} cases"
            )
            tcl = _thir_cases["clean"] + _thir_cases_agg["clean"]
            ttot = _thir_cases["total"] + _thir_cases_agg["total"]
            if ttot:
                marked = ttot - tcl
                # "un-migrated", not "on AST": every case's overlay is
                # byte-diffed; the marker only exempts a case from the ratchet.
                terminalreporter.write_line(
                    f"{_LOG_PREFIX} thir cases: {tcl}/{ttot} migrated "
                    f"(unmarked, zero fallback); {marked} un-migrated (no_thir)"
                )
            # Per-face coverage (and shape % below) stay behind the explicit
            # metrics flags: the default run now routes the whole corpus too, so
            # these ARE meaningful there -- but they are long, slow-moving lines
            # that belong to a coverage query, not to every test run.
            if THIR_IGNORE_MARKERS:
                hit = set(_thir_faces) | set(_thir_faces_agg)
                zero = sorted(THIR_FACES - hit)
                if zero:
                    terminalreporter.write_line(
                        f"{_LOG_PREFIX} thir faces: "
                        f"{len(THIR_FACES) - len(zero)}/{len(THIR_FACES)} "
                        f"witnessed; zero-witness: {', '.join(zero)}"
                    )
                else:
                    terminalreporter.write_line(
                        f"{_LOG_PREFIX} thir faces: all {len(THIR_FACES)} "
                        f"witnessed"
                    )
            # Per-component AST-fallback breakdown: how many candidate bodies
            # the gate rejected, by first-reject reason -- the measured gap to
            # each deletion target ("body" = gen_body/gen_expr, "ctor" = the
            # MIL emit). A coverage-query metric like faces/shapes, so it hides
            # behind the same flag: the default run's fallback is dominated by
            # the marked cases it now also routes, which is three long lines of
            # standing backlog, not news about this run. ($THIR_FALLBACK_JSON
            # still dumps whole-corpus counts from any run that asks for them.)
            fallback = dict(_thir_fallback)
            for key, n in _thir_fallback_agg.items():
                fallback[key] = fallback.get(key, 0) + n
            if THIR_IGNORE_MARKERS:
                for component in ("body", "ctor", "resumable", "top_level"):
                    pre = component + ":"
                    items = sorted(
                        ((k[len(pre):], n) for k, n in fallback.items()
                         if k.startswith(pre)),
                        key=lambda kv: (-kv[1], kv[0]))
                    if not items:
                        continue
                    total = sum(n for _, n in items)
                    top = ", ".join(f"{r} {n}" for r, n in items[:12])
                    more = len(items) - 12
                    tail = f", +{more} more kinds" if more > 0 else ""
                    terminalreporter.write_line(
                        f"{_LOG_PREFIX} thir fallback: {component} {total} -- "
                        f"{top}{tail}"
                    )
            dump_path = os.environ.get("THIR_FALLBACK_JSON")
            if dump_path and fallback:
                Path(dump_path).write_text(
                    json.dumps(dict(sorted(fallback.items())), indent=2)
                    + "\n")
                terminalreporter.write_line(
                    f"{_LOG_PREFIX} thir fallback: full counts written to "
                    f"{dump_path}"
                )
            # Per-construct ARM RESIDUAL: fallback bodies containing each
            # construct -- the deletion metric (CLAUDE.md THIR loop step 1).
            # The SMALLEST residual is the arm closest to deletable; report it
            # ascending so the next target reads off the top.
            arm_dump = os.environ.get("THIR_ARM_RESIDUAL_JSON")
            observed = dict(_thir_arm_residual)
            for key, n in _thir_arm_residual_agg.items():
                observed[key] = observed.get(key, 0) + n
            if observed:
                # An arm at residual 0 is ABSENT from the counter, so seed the
                # universe first: without it "deletable" is empty whatever the
                # tree, which is what the metric exists to report.
                universe = arm_universe()
                residual = {k: 0 for k in universe} | observed
                if arm_dump:
                    Path(arm_dump).write_text(
                        json.dumps(dict(sorted(residual.items(),
                                               key=lambda kv: kv[1])), indent=2)
                        + "\n")
                deletable = sorted(k for k, v in residual.items() if v == 0)
                terminalreporter.write_line(
                    f"{_LOG_PREFIX} thir arm-residual: {len(deletable)}/"
                    f"{len(residual)} arms DELETABLE (residual 0)"
                    + (f": {', '.join(deletable)}" if deletable else "")
                )
                smallest = sorted(((k, v) for k, v in residual.items() if v),
                                  key=lambda kv: kv[1])[:10]
                nearest = ", ".join(f"{k} {v}" for k, v in smallest)
                terminalreporter.write_line(
                    f"{_LOG_PREFIX} thir arm-residual (bodies keeping each AST "
                    f"arm alive; smallest = closest to deletable): {nearest}"
                )
                drift = sorted(set(observed) - universe)
                if drift:
                    terminalreporter.write_line(
                        f"{_LOG_PREFIX} thir arm-residual: WARNING -- observed "
                        f"kinds missing from arm_universe(): {', '.join(drift)}"
                    )
                if arm_dump:
                    terminalreporter.write_line(
                        f"{_LOG_PREFIX} thir arm-residual: full counts written "
                        f"to {arm_dump}"
                    )
            # Distinct-SHAPE coverage: the de-inflated complement of the routed
            # count (which repeats the stdlib body per case). `R/T distinct
            # shapes routed` is the honest progress %; the top blocked shapes are
            # ranked by which reject reason blocks the most DISTINCT shapes.
            shapes = dict(_thir_shapes)
            _fold_shapes(shapes, _thir_shapes_agg)
            if shapes and THIR_IGNORE_MARKERS:  # whole-corpus metric (see faces)
                from tpyc.thir.shape import summarize_shapes
                s = summarize_shapes(shapes)
                by_reason: dict[str, int] = {}
                for _sig, _n, reason in s["blocked"]:
                    by_reason[reason] = by_reason.get(reason, 0) + 1
                top = ", ".join(
                    f"{r} {n}" for r, n in sorted(
                        by_reason.items(), key=lambda kv: (-kv[1], kv[0]))[:10])
                terminalreporter.write_line(
                    f"{_LOG_PREFIX} thir shapes: {s['routed']}/{s['total']} "
                    f"distinct shapes routed ({s['pct']:.1f}%); "
                    f"{s['partial']} partial; {len(s['blocked'])} blocked "
                    f"(top reasons by distinct shapes: {top})"
                )
                shapes_dump = os.environ.get("THIR_SHAPES_JSON")
                if shapes_dump:
                    Path(shapes_dump).write_text(
                        json.dumps({
                            "summary": {k: s[k] for k in ("total", "routed",
                                                          "partial", "pct")},
                            "blocked_by_leverage": [
                                {"leverage": n, "reason": r, "shape": sig}
                                for sig, n, r in s["blocked"]],
                            "shapes": dict(sorted(shapes.items())),
                        }, indent=2) + "\n")
                    terminalreporter.write_line(
                        f"{_LOG_PREFIX} thir shapes: full detail written to "
                        f"{shapes_dump}"
                    )
        elif verdict == "warn":
            terminalreporter.write_line(
                f"{_LOG_PREFIX} thir: WARNING -- THIR routed 0 bodies "
                f"(filtered run; the subset may hold no eligible case)"
            )
        else:
            terminalreporter.write_line(
                f"{_LOG_PREFIX} thir: ERROR -- THIR routed 0 bodies over "
                f"the full corpus; the byte-diff gate is vacuous (wiring "
                f"regression?). Session failed."
            )
        divergences = _thir_divergences + _thir_divergences_agg
        if divergences:
            shown = sorted(divergences)[:30]
            terminalreporter.write_line(
                f"{_LOG_PREFIX} thir divergences: {len(divergences)} snapshot "
                f"mismatch(es), first-divergent-function labels:"
            )
            for label in shown:
                terminalreporter.write_line(f"{_LOG_PREFIX}   {label}")
            if len(divergences) > len(shown):
                terminalreporter.write_line(
                    f"{_LOG_PREFIX}   ... and {len(divergences) - len(shown)} "
                    f"more (see individual failures)"
                )
        # The move-verdict join (tpyc/move_audit.py). A hit here is a
        # move-vs-copy divergence in a ROUTED body that the byte-diff cannot
        # see, so it is worth a line even at zero -- an absent line would be
        # indistinguishable from the detector having been silently unwired.
        mv = {**_move_verdicts, **_move_verdicts_agg}
        if mv:
            rows = sum(len(v) for v in mv.values())
            terminalreporter.write_line(
                f"{_LOG_PREFIX} thir move-verdicts: {rows} divergence(s) in "
                f"{len(mv)} case(s) -- one path moves where the other copies:"
            )
            for case in sorted(mv)[:10]:
                names = ", ".join(f"{n} in `{fn or '?'}` (ast={a} thir={t})"
                                  for n, a, t, fn in mv[case])
                terminalreporter.write_line(f"{_LOG_PREFIX}   {case}: {names}")
        elif _thir_tally["bodies"] or _thir_tally_agg["bodies"]:
            # The denominator is the point: "0 divergences" over 0 joined
            # nodes is a silently-unwired detector, not a clean run.
            terminalreporter.write_line(
                f"{_LOG_PREFIX} thir move-verdicts: 0 divergences over "
                f"{_move_joined[0] + _move_joined_agg[0]} joined nodes")


def case_binary_path(build_dir: Path, module_name: str) -> Path:
    """Path where a case's linked debug binary lives (existing or not)."""
    return BuildLayout(build_dir, module_name, build_variant="debug").binary_path()


def build_and_run(build_dir: Path, module_name: str,
                  all_cpp_files: list[Path] | None = None,
                  extra_src_files: list[Path] | None = None,
                  extra_include_dirs: list[Path] | None = None,
                  force_includes: list[Path] | None = None,
                  link_flags: list[str] | None = None,
                  build_variant: str = "debug",
                  precompiled_objects: list[str] | None = None,
                  exclude_cpp_relpaths: set[str] | None = None,
                  extra_link_flags: list[str] | None = None,
                  c_sources: list[tuple[Path, list[str]]] | None = None,
                  run: bool = True) -> RunResult:
    """Compile generated C++ and run, capturing all output (including panics).

    Args:
        build_dir: Directory containing generated C++ files.
        module_name: Name of the entry point module.
        all_cpp_files: List of all C++ files to compile (for multi-module).
                       If None, compiles only the entry module.
        extra_src_files: Additional C++ source files to include in the build
                         (e.g., stub implementations for native functions).
        extra_include_dirs: Additional include directories for C++ compilation
                            (e.g., directories containing native type headers).
        force_includes: Headers to force-include via -include before all source
                        (e.g., native type definitions for interop tests).
        link_flags: Extra linker flags from # tpy: link() directives.
        build_variant: Build variant ("debug" or "release").
        precompiled_objects: Pre-compiled .o files to link (e.g. stdlib objects).
        exclude_cpp_relpaths: Relative paths (under src/) to skip compiling
                              when using precompiled_objects.
        run: False (--build-only) returns success right after the link --
             for toolchains whose binaries cannot execute on this host.
    """
    layout = BuildLayout(build_dir, module_name, build_variant=build_variant)

    # Determine C++ files to compile
    if all_cpp_files is None:
        cpp_files = [layout.cpp_path(module_name)]
    else:
        cpp_files = list(all_cpp_files)

    if extra_src_files:
        cpp_files.extend(extra_src_files)

    # Filter out pre-compiled stdlib .cpp files
    if exclude_cpp_relpaths and precompiled_objects:
        cpp_files = [f for f in cpp_files
                     if not _matches_stdlib_relpath(f, layout.src_dir, exclude_cpp_relpaths)]

    # Apply per-test link flags if provided
    config = CPP_CONFIG
    if link_flags:
        config = dataclasses.replace(config, link_flags=link_flags)

    # Prepend the cached PCH header (if available) so each per-case main.cpp
    # picks up tpy_pch.hpp.gch via -include rather than re-parsing tpy.hpp.
    pch_header = get_pch_header()
    all_force_includes: list[Path] = []
    if pch_header is not None:
        all_force_includes.append(pch_header)
    if force_includes:
        all_force_includes.extend(force_includes)

    # Compile C++ with include path for cross-module references
    compile_cmds = layout.build_cpp_commands(
        runtime_include_dir=RUNTIME_DIR,
        cpp_files=cpp_files,
        config=config,
        extra_objects=precompiled_objects,
        extra_include_dirs=extra_include_dirs or None,
        force_includes=all_force_includes or None,
        extra_link_flags=extra_link_flags or None,
        c_sources=c_sources or None,
    )
    for cmd in compile_cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return RunResult(
                success=False,
                stdout="",
                stderr=result.stderr,
                returncode=result.returncode,
                cpp_build_failed=True,
            )

    if not run:
        # --build-only: linked successfully; nothing to execute.
        return RunResult(success=True, stdout="", stderr="", returncode=0)

    # Run and capture output. If a `src/input.txt` fixture exists in
    # the case directory (one level above `build_dir`), pipe it to the
    # binary's stdin -- enables snapshot-testing programs that read
    # from stdin (M8 number guesser, etc.).
    exe_file = layout.binary_path()
    stdin_input = None
    case_input = build_dir.parent / "src" / "input.txt"
    if case_input.exists():
        stdin_input = case_input.read_text()
    # Run with cwd=build_dir so any files the program writes via a
    # relative path (e.g. Pascal `Assign(f, 'out.txt')`) land in the
    # gitignored build directory rather than the test runner's cwd.
    result = subprocess.run(
        [str(exe_file)],
        input=stdin_input,
        cwd=str(build_dir),
        capture_output=True, text=True,
    )
    return RunResult(
        success=(result.returncode == 0),
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )


def _matches_stdlib_relpath(cpp_file: Path, src_dir: Path, relpaths: set[str]) -> bool:
    """Check if cpp_file's path relative to src_dir is in the stdlib set."""
    try:
        rel = str(cpp_file.relative_to(src_dir))
        return rel in relpaths
    except ValueError:
        return False


@dataclass
class Annotation:
    """A diagnostic annotation from source code."""
    line: int
    level: str  # "error", "warning", "ok"
    pattern: str | None  # Regex pattern (None for "ok")


def parse_annotations(source: str) -> list[Annotation]:
    """Parse # tpyc: annotations from source code.

    Supports:
      # tpyc: error(/pattern/)
      # tpyc: warning(/pattern/)
      # tpyc: ok

    Multiple annotations per line: # tpyc: warning(/a/) warning(/b/)
    Inline annotations (after code) apply to their own line.
    Standalone annotations (comment-only lines) apply to the next line.
    """
    annotations = []
    # Matches the first annotation (with # tpyc: prefix) to locate the start
    prefix_re = re.compile(r'#\s*tpyc:\s')
    # Matches each annotation item (no prefix needed)
    item_re = re.compile(r'\b(error|warning|ok)\b(?:\s*\(\s*/(.+?)/\s*\))?')

    standalone_re = re.compile(r'^#\s*tpyc:\s')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            # Standalone annotation (e.g. "# tpyc: warning(...)") applies to next line
            if standalone_re.match(stripped):
                m = prefix_re.search(line)
                tail = line[m.end():]
                for match in item_re.finditer(tail):
                    level = match.group(1)
                    regex = match.group(2)
                    annotations.append(Annotation(line=lineno + 1, level=level, pattern=regex))
            # Skip other comment-only lines (e.g. commented-out code)
            continue
        m = prefix_re.search(line)
        if m:
            tail = line[m.end():]
            for match in item_re.finditer(tail):
                level = match.group(1)
                regex = match.group(2)
                annotations.append(Annotation(line=lineno, level=level, pattern=regex))

    return annotations


def validate_annotations(src_file: Path, diagnostics: str) -> list[str]:
    """Validate that diagnostics match inline annotations.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_annotations(source)
    errors = []

    # Parse diagnostics into (line, level, message) tuples
    diag_pattern = re.compile(rf'^{re.escape(src_file.name)}:(\d+):\s*(error|warning):\s*(.+)$', re.MULTILINE)
    diag_by_line: dict[int, list[tuple[str, str]]] = {}
    for match in diag_pattern.finditer(diagnostics):
        line = int(match.group(1))
        level = match.group(2)
        message = match.group(3)
        diag_by_line.setdefault(line, []).append((level, message))

    # Check each annotation
    for ann in annotations:
        line_diags = diag_by_line.get(ann.line, [])

        if ann.level == "ok":
            # Expect no diagnostics on this line
            if line_diags:
                errors.append(f"{src_file.name}:{ann.line}: expected no diagnostics but got: {line_diags}")
        else:
            # Expect a matching diagnostic
            found = False
            for level, message in line_diags:
                if level == ann.level:
                    if ann.pattern is None or re.search(ann.pattern, message):
                        found = True
                        break
            if not found:
                errors.append(
                    f"{src_file.name}:{ann.line}: expected {ann.level}(/{ann.pattern}/) but got: {line_diags or 'nothing'}"
                )

    return errors


@dataclass
class TypeAnnotation:
    """A type annotation from source code (# tpyc: type(...))."""
    line: int
    expected_type: str


def parse_type_annotations(source: str) -> list[TypeAnnotation]:
    """Parse # tpyc: type(...) annotations from source code."""
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*type\(\s*(.+?)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        match = pattern.search(line)
        if match:
            annotations.append(TypeAnnotation(line=lineno, expected_type=match.group(1)))

    return annotations


_VAR_NAME_RE = re.compile(r'\s*(\w+)\s*(?::\s*[\w\[\], .|]+\s*)?(?:\s*,\s*\w+)*\s*=')
_FOR_LOOP_VAR_RE = re.compile(r'\s*for\s+(\w+)(?:\s*,\s*\w+)*\s+in\b')


def validate_type_annotations(
    src_file: Path,
    declared_var_types: dict[tuple[int, str], object],
) -> list[str]:
    """Validate # tpyc: type(...) annotations against compiler-resolved types.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_type_annotations(source)
    errors = []

    for ann in annotations:
        # Extract variable name from the source line. Supports vardecl /
        # tuple-unpack (`x = ...`, `a, b = ...`) and for-loop forms
        # (`for n in xs:`, `for u, v in pairs:`). For tuple targets the first
        # name is checked -- targets of the same unpack share the resolved
        # element type, so one is sufficient.
        line_text = source.splitlines()[ann.line - 1]
        var_match = _VAR_NAME_RE.match(line_text) or _FOR_LOOP_VAR_RE.match(line_text)
        if not var_match:
            errors.append(f"Line {ann.line}: could not extract variable name from line")
            continue
        var_name = var_match.group(1)

        key = (ann.line, var_name)
        actual_type = declared_var_types.get(key)
        if actual_type is None:
            errors.append(
                f"Line {ann.line}: no declared type found for '{var_name}'"
            )
            continue

        actual_str = str(actual_type)
        expected = ann.expected_type

        if expected.startswith('/') and expected.endswith('/'):
            # Regex match
            if not re.search(expected[1:-1], actual_str):
                errors.append(
                    f"Line {ann.line}: expected type matching /{expected[1:-1]}/ "
                    f"for '{var_name}' but got '{actual_str}'"
                )
        else:
            if actual_str != expected:
                errors.append(
                    f"Line {ann.line}: expected type '{expected}' "
                    f"for '{var_name}' but got '{actual_str}'"
                )

    return errors


@dataclass
class NonNullAnnotation:
    """A non-null annotation from source code (# tpyc: non_null/nullable(var))."""
    line: int
    var_name: str
    expected_non_null: bool  # True for non_null, False for nullable


def parse_non_null_annotations(source: str) -> list[NonNullAnnotation]:
    """Parse # tpyc: non_null(var) and # tpyc: nullable(var) annotations."""
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*(non_null|nullable)\(\s*([\w.]+)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        match = pattern.search(line)
        if match:
            kind = match.group(1)
            var_name = match.group(2)
            annotations.append(NonNullAnnotation(
                line=lineno,
                var_name=var_name,
                expected_non_null=(kind == "non_null"),
            ))

    return annotations


def validate_non_null_annotations(
    src_file: Path,
    ptr_deref_facts: dict[tuple[int, str], bool],
) -> list[str]:
    """Validate # tpyc: non_null/nullable annotations against compiler facts.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_non_null_annotations(source)
    errors = []

    for ann in annotations:
        key = (ann.line, ann.var_name)
        actual = ptr_deref_facts.get(key)
        if actual is None:
            errors.append(
                f"Line {ann.line}: no ptr dereference found for '{ann.var_name}'"
            )
            continue
        if ann.expected_non_null and not actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' to be non_null "
                f"but deref_check is used"
            )
        elif not ann.expected_non_null and actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' to be nullable "
                f"but deref_check is skipped (proven non-null)"
            )

    return errors


@dataclass
class BoundsSafeAnnotation:
    """A bounds annotation from source code (# tpyc: bounds_safe/bounds_checked)."""
    line: int
    var_name: str
    expected_safe: bool  # True for bounds_safe, False for bounds_checked


def parse_bounds_annotations(source: str) -> list[BoundsSafeAnnotation]:
    """Parse # tpyc: bounds_safe and # tpyc: bounds_checked annotations."""
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*(bounds_safe|bounds_checked)\(\s*(\w+)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        match = pattern.search(line)
        if match:
            kind = match.group(1)
            var_name = match.group(2)
            annotations.append(BoundsSafeAnnotation(
                line=lineno,
                var_name=var_name,
                expected_safe=(kind == "bounds_safe"),
            ))

    return annotations


def validate_bounds_annotations(
    src_file: Path,
    subscript_bounds_facts: dict[tuple[int, str], bool],
) -> list[str]:
    """Validate # tpyc: bounds_safe/bounds_checked annotations against compiler facts.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_bounds_annotations(source)
    errors = []

    for ann in annotations:
        key = (ann.line, ann.var_name)
        actual = subscript_bounds_facts.get(key)
        if actual is None:
            errors.append(
                f"Line {ann.line}: no subscript access found for '{ann.var_name}'"
            )
            continue
        if ann.expected_safe and not actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' subscript to be bounds_safe "
                f"but bounds check is used"
            )
        elif not ann.expected_safe and actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' subscript to be bounds_checked "
                f"but bounds check is skipped (proven safe)"
            )

    return errors


@dataclass
class DivSafeAnnotation:
    """A division annotation from source code (# tpyc: div_safe/div_checked)."""
    line: int
    var_name: str
    expected_safe: bool  # True for div_safe, False for div_checked


def parse_div_annotations(source: str) -> list[DivSafeAnnotation]:
    """Parse # tpyc: div_safe and # tpyc: div_checked annotations."""
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*(div_safe|div_checked)\(\s*(\w+)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        match = pattern.search(line)
        if match:
            kind = match.group(1)
            var_name = match.group(2)
            annotations.append(DivSafeAnnotation(
                line=lineno,
                var_name=var_name,
                expected_safe=(kind == "div_safe"),
            ))

    return annotations


def validate_div_annotations(
    src_file: Path,
    div_zero_facts: dict[tuple[int, str], bool],
) -> list[str]:
    """Validate # tpyc: div_safe/div_checked annotations against compiler facts.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_div_annotations(source)
    errors = []

    for ann in annotations:
        key = (ann.line, ann.var_name)
        actual = div_zero_facts.get(key)
        if actual is None:
            errors.append(
                f"Line {ann.line}: no division/modulo found for '{ann.var_name}'"
            )
            continue
        if ann.expected_safe and not actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' divisor to be div_safe "
                f"but zero check is used"
            )
        elif not ann.expected_safe and actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' divisor to be div_checked "
                f"but zero check is skipped (proven non-zero)"
            )

    return errors


@dataclass
class CastSafeAnnotation:
    """A cast annotation from source code (# tpyc: cast_safe/cast_checked)."""
    line: int
    type_name: str
    expected_safe: bool  # True for cast_safe, False for cast_checked


def parse_cast_annotations(source: str) -> list[CastSafeAnnotation]:
    """Parse # tpyc: cast_safe and # tpyc: cast_checked annotations."""
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*(cast_safe|cast_checked)\(\s*(\w+)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        match = pattern.search(line)
        if match:
            kind = match.group(1)
            type_name = match.group(2)
            annotations.append(CastSafeAnnotation(
                line=lineno,
                type_name=type_name,
                expected_safe=(kind == "cast_safe"),
            ))

    return annotations


def validate_cast_annotations(
    src_file: Path,
    cast_safe_facts: dict[tuple[int, str], bool],
) -> list[str]:
    """Validate # tpyc: cast_safe/cast_checked annotations against compiler facts.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_cast_annotations(source)
    errors = []

    for ann in annotations:
        key = (ann.line, ann.type_name)
        actual = cast_safe_facts.get(key)
        if actual is None:
            errors.append(
                f"Line {ann.line}: no int cast to '{ann.type_name}' found"
            )
            continue
        if ann.expected_safe and not actual:
            errors.append(
                f"Line {ann.line}: expected cast to '{ann.type_name}' to be cast_safe "
                f"but range check is used"
            )
        elif not ann.expected_safe and actual:
            errors.append(
                f"Line {ann.line}: expected cast to '{ann.type_name}' to be cast_checked "
                f"but range check is skipped (proven safe)"
            )

    return errors


@dataclass
class SendSyncAnnotation:
    """A Send/Sync annotation from source code (# tpyc: is_send/is_sync(yes|no))."""
    line: int
    trait: str  # "is_send" or "is_sync"
    expected: bool


def parse_send_sync_annotations(source: str) -> list[SendSyncAnnotation]:
    """Parse # tpyc: is_send(yes|no) and # tpyc: is_sync(yes|no) annotations.

    Both may appear in one comment: # tpyc: is_send(yes) is_sync(no)
    """
    annotations = []
    comment_re = re.compile(r'#\s*tpyc:(.*)$')
    trait_re = re.compile(r'\b(is_send|is_sync)\(\s*(yes|no)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        comment = comment_re.search(line)
        if not comment:
            continue
        for match in trait_re.finditer(comment.group(1)):
            annotations.append(SendSyncAnnotation(
                line=lineno,
                trait=match.group(1),
                expected=(match.group(2) == "yes"),
            ))

    return annotations


def validate_send_sync_annotations(
    src_file: Path,
    send_sync_facts: dict[tuple[int, str], tuple[bool, bool]],
) -> list[str]:
    """Validate # tpyc: is_send/is_sync annotations against the declared
    variable's computed traits.

    The annotation sits on a variable declaration (or for-loop) line, like
    # tpyc: type(...). Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_send_sync_annotations(source)
    errors = []

    for ann in annotations:
        line_text = source.splitlines()[ann.line - 1]
        var_match = _VAR_NAME_RE.match(line_text) or _FOR_LOOP_VAR_RE.match(line_text)
        if not var_match:
            errors.append(f"Line {ann.line}: could not extract variable name from line")
            continue
        var_name = var_match.group(1)

        facts = send_sync_facts.get((ann.line, var_name))
        if facts is None:
            errors.append(
                f"Line {ann.line}: no declared type found for '{var_name}'"
            )
            continue

        actual = facts[0] if ann.trait == "is_send" else facts[1]
        if actual != ann.expected:
            errors.append(
                f"Line {ann.line}: expected {ann.trait}({'yes' if ann.expected else 'no'}) "
                f"for '{var_name}' but compiler says "
                f"{ann.trait}({'yes' if actual else 'no'})"
            )

    return errors


_DEF_NAME_RE = re.compile(r'\s*(?:async\s+)?def\s+(\w+)')


def parse_frame_annotations(source: str) -> list[SendSyncAnnotation]:
    """Parse # tpyc: frame_send(yes|no) / frame_sync(yes|no) annotations
    (on async def / generator def lines)."""
    annotations = []
    comment_re = re.compile(r'#\s*tpyc:(.*)$')
    trait_re = re.compile(r'\b(frame_send|frame_sync)\(\s*(yes|no)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        comment = comment_re.search(line)
        if not comment:
            continue
        for match in trait_re.finditer(comment.group(1)):
            annotations.append(SendSyncAnnotation(
                line=lineno,
                trait=match.group(1),
                expected=(match.group(2) == "yes"),
            ))

    return annotations


def validate_frame_annotations(
    src_file: Path,
    frame_facts: dict[tuple[int, str], tuple[bool, bool]],
) -> list[str]:
    """Validate # tpyc: frame_send/frame_sync annotations against the
    function's computed FrameType traits. The annotation sits on the
    `def` / `async def` line. Returns list of validation errors.
    """
    source = src_file.read_text()
    annotations = parse_frame_annotations(source)
    errors = []

    for ann in annotations:
        line_text = source.splitlines()[ann.line - 1]
        def_match = _DEF_NAME_RE.match(line_text)
        if not def_match:
            errors.append(f"Line {ann.line}: could not extract function name from line")
            continue
        fn_name = def_match.group(1)

        facts = frame_facts.get((ann.line, fn_name))
        if facts is None:
            errors.append(
                f"Line {ann.line}: no frame recorded for '{fn_name}' "
                f"(only async / generator defs carry frames)"
            )
            continue

        actual = facts[0] if ann.trait == "frame_send" else facts[1]
        if actual != ann.expected:
            errors.append(
                f"Line {ann.line}: expected {ann.trait}({'yes' if ann.expected else 'no'}) "
                f"for '{fn_name}' but compiler says "
                f"{ann.trait}({'yes' if actual else 'no'})"
            )

    return errors


def check_or_update(actual: str, expected_file: Path, description: str,
                    *, compare_only: bool = False,
                    thir_routed_names: frozenset[str] | None = None) -> None:
    """Compare actual with expected, or update expected if UPDATE_EXPECTED is set.

    In update mode, creates parent directories and writes the file.
    In test mode, asserts that actual matches expected (missing file = empty expected).

    Pass ``compare_only=True`` to always compare and never write -- used by the
    cpy phase to verify CPython output against the canonical output.txt
    produced by the exec phase, even in update mode.

    ``thir_routed_names`` (generated-code snapshots under --thir-codegen only)
    activates the THIR divergence reporter: the failure is prefixed with the
    function enclosing the first divergent hunk and whether THIR routed it, and
    the divergence is tallied into the ``tpy| thir divergences`` summary.
    """
    if UPDATE_EXPECTED and not compare_only:
        expected_file.parent.mkdir(parents=True, exist_ok=True)
        expected_file.write_text(actual)
    else:
        expected = expected_file.read_text() if expected_file.exists() else ""
        if actual != expected:
            diff = _format_unified_diff(expected, actual, fromfile=str(expected_file), tofile="actual")
            thir_note = ""
            if thir_routed_names is not None:
                label = _thir_divergence_label(
                    expected, actual, thir_routed_names)
                record_thir_divergence(
                    f"{_case_label(expected_file)} {description}: {label}")
                thir_note = f"THIR divergence: {label}\n"
            pytest.fail(
                f"{description} differs: {expected_file}\n{thir_note}{diff}",
                pytrace=False,
            )


def _case_label(expected_file: Path) -> str:
    """`<group>/<case>` for a path under tests/cases, else the file name."""
    try:
        rel = expected_file.relative_to(CASES_DIR)
        return "/".join(rel.parts[:rel.parts.index("expected")])
    except ValueError:
        return expected_file.name


_THIR_DEF_MARKER_RE = re.compile(r"^\s*// def (\w+)\(")
_THIR_CLASS_MARKER_RE = re.compile(r"^\s*// class (\w+)")


def first_divergent_line(expected: str, actual: str) -> int | None:
    """0-based index into `actual`'s lines of the first divergence, or None."""
    sm = difflib.SequenceMatcher(a=expected.splitlines(),
                                 b=actual.splitlines(), autojunk=False)
    for tag, _i1, _i2, j1, _j2 in sm.get_opcodes():
        if tag != "equal":
            return j1
    return None


def enclosing_function(lines: list[str], idx: int) -> str | None:
    """Name the function enclosing generated-code line `idx` by scanning
    backwards for the `// def name(...)` source marker codegen emits before
    every function/method body. A `// class` marker hit first means the
    divergence sits in the record declaration itself (field layout etc.);
    ctor markers (`__init__` sits inside the struct) qualify with the record
    name to match the `Rec.__init__` form the routed-names set uses.
    """
    for i in range(min(idx, len(lines) - 1), -1, -1):
        m = _THIR_DEF_MARKER_RE.match(lines[i])
        if m:
            name = m.group(1)
            if name != "__init__":
                return name
            for k in range(i - 1, -1, -1):
                cm = _THIR_CLASS_MARKER_RE.match(lines[k])
                if cm:
                    return f"{cm.group(1)}.__init__"
            return name
        cm = _THIR_CLASS_MARKER_RE.match(lines[i])
        if cm:
            return f"class {cm.group(1)}"
    return None


def _thir_divergence_label(expected: str, actual: str,
                           routed_names: frozenset[str]) -> str:
    idx = first_divergent_line(expected, actual)
    if idx is None:
        return "whitespace/trailing-newline only"
    # Anchor on whichever side names an enclosing function: a THIR emit that
    # DROPS lines can put actual's divergence point before any marker while
    # the expected side still sits inside one.
    fn = (enclosing_function(actual.splitlines(), idx)
          or enclosing_function(expected.splitlines(),
                                first_divergent_expected_line(expected, actual)))
    if fn is None:
        return f"outside any function marker (line {idx + 1})"
    routed = fn in routed_names
    return f"in `{fn}` [{'THIR-routed' if routed else 'NOT THIR-routed'}]"


def first_divergent_expected_line(expected: str, actual: str) -> int:
    """0-based index into `expected`'s lines of the first divergence (0 when
    the texts are equal -- callers only reach this on a known mismatch)."""
    sm = difflib.SequenceMatcher(a=expected.splitlines(),
                                 b=actual.splitlines(), autojunk=False)
    for tag, i1, _i2, _j1, _j2 in sm.get_opcodes():
        if tag != "equal":
            return i1
    return 0


def _format_unified_diff(expected: str, actual: str, fromfile: str, tofile: str, context: int = 3) -> str:
    """Return a readable unified diff for expected vs actual text."""
    diff_lines = list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
            n=context,
        )
    )
    if not diff_lines:
        return "(no visible line diff; content may differ by trailing newline or whitespace)"
    return "\n".join(diff_lines)


def _filter_lib_traceback(stderr: str) -> str:
    """Hide lib/tpy frames from traceback output while preserving the error."""
    lines = stderr.splitlines()
    filtered: list[str] = []
    for line in lines:
        normalized = line.replace("\\", "/")
        if '/lib/tpy/' in normalized or '/lib/cpy/' in normalized:
            continue
        filtered.append(line)
    out = "\n".join(filtered).strip()
    return out or stderr.strip()



_SKIP_DIRS = {"__tpyc__", "expected", "__pycache__"}


@functools.lru_cache(maxsize=1)
def _all_plugin_entry_extensions() -> tuple[str, ...]:
    """Collect every source extension claimed by any frontend plugin
    declared in an options.json under `tests/cases/`. Used by case
    discovery to glob `src/main.<ext>` for non-Python frontends
    without conftest having to know which DSLs exist.

    Plain `.py` is always included so plain-Python cases stay
    discoverable without an options.json. Plugins are loaded once
    per session via the lru_cache.
    """
    exts: set[str] = {".py"}
    if not CASES_DIR.exists():
        return tuple(sorted(exts))
    from tpyc.frontend_plugin import resolve_plugin_class
    seen_specs: set[str] = set()
    for opts_path in CASES_DIR.rglob("options.json"):
        try:
            cfg = json.loads(opts_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(cfg, dict):
            continue
        spec = cfg.get("plugin")
        if not isinstance(spec, str) or spec in seen_specs:
            continue
        seen_specs.add(spec)
        plugin_path = (PROJECT_ROOT / spec).resolve()
        if not plugin_path.is_file():
            continue
        # Read `extensions` off the class (a ClassVar) rather than
        # instantiating the plugin -- discovery runs once per session
        # at pytest startup, so calling `__init__` here would force
        # every plugin to support empty `dsl_opts` just for probing.
        # A plugin that fails class-load (missing PLUGIN, wrong
        # api_version) raises FrontendPluginError; let it propagate
        # so we surface real bugs instead of swallowing them.
        plugin_cls = resolve_plugin_class(str(plugin_path))
        for ext in plugin_cls.extensions:
            exts.add(ext)
    return tuple(sorted(exts))


def _discover_from_dirs(base_dirs: list[Path]):
    """Discover test cases from the given directories.

    Walks recursively looking for `src/` directories whose entry
    point matches one of the registered extensions (`.py` plus any
    extension a frontend plugin in an options.json claims). Build
    artifact trees (__tpyc__, expected, __pycache__) are skipped to
    avoid scanning thousands of irrelevant directories.

    Returns list of (name, case_dir, main_src) tuples.
    """
    entry_exts = _all_plugin_entry_extensions()
    glob_patterns = tuple(f"*{ext}" for ext in entry_exts)
    cases = []

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue

        prefix = base_dir.name

        for dirpath, dirnames, _filenames in os.walk(base_dir):
            dirnames[:] = [
                d for d in dirnames if d not in _SKIP_DIRS
            ]

            cur = Path(dirpath)
            if cur.name != "src":
                continue

            case_dir = cur.parent
            src_files: list[Path] = []
            for pat in glob_patterns:
                src_files.extend(cur.glob(pat))
            if not src_files:
                continue

            # Pick the entry point. A plugin-claimed extension wins
            # over `.py` so a frontend case can keep helper `.py`
            # files in src/ alongside its main source; within the
            # plugin-extension set we prefer one named `main.*`.
            plugin_exts = [e for e in entry_exts if e != ".py"]
            main_src = _pick_main_src(src_files, plugin_exts)

            rel_path = case_dir.relative_to(base_dir)
            name = f"{prefix}/{rel_path}".replace("\\", "/")
            cases.append((name, case_dir, main_src))

    return cases


def _pick_main_src(src_files: list[Path],
                   plugin_exts: list[str]) -> Path:
    """Choose one entry-point file from `src_files`. A file whose
    extension is registered by a frontend plugin wins over `.py`;
    within either group a name `main.*` wins over anything else;
    ties broken alphabetically. Always returns a value -- callers
    ensure `src_files` is non-empty."""
    plugin_set = set(plugin_exts)
    plugin_files = [p for p in src_files if p.suffix in plugin_set]
    py_files = [p for p in src_files if p.suffix == ".py"]
    candidates = plugin_files or py_files or src_files
    for c in candidates:
        if c.stem == "main":
            return c
    return sorted(candidates, key=lambda p: p.name)[0]


@functools.cache
def discover_cases():
    """Discover all test cases from cases/ directory.

    Returns list of (name, case_dir, main_src) tuples.
    Cached because multiple test files call this during parametrization.
    """
    return _discover_from_dirs([CASES_DIR])


def discover_success_cases():
    """Discover success test cases only (excludes error_ and panic_ prefixed tests).

    Returns list of (name, case_dir, main_src) tuples.
    """
    return [
        (name, case_dir, main_src)
        for name, case_dir, main_src in discover_cases()
        if not case_dir.name.startswith(("error_", "panic_"))
    ]
