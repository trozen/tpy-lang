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

# Import the compiler
sys.path.insert(0, str(Path(__file__).parent.parent))
from tpyc.cli import get_module_name
from tpyc.codegen_cpp import CodeGenOptions, CodeGenError
from tpyc.parse import Parser, ParseError
from tpyc.sema import SemanticAnalyzer, SemanticError, Diagnostic, DiagnosticLevel
from tpyc.compiler import (
    Compiler, CompileError, BuildLayout, CppCompilerConfig, STRICT_WARN_FLAGS,
    get_or_build_pch,
)
from tpyc.build.third_party import resolve_build_plan

# Default options for tests: emit source comments for easier debugging
TEST_CODEGEN_OPTIONS = CodeGenOptions(emit_source_comments=True, comment_line_numbers=False)

# Shared C++ compiler config (auto-detects ccache).
# Tests run with the strict warning set so regressions in generated code or
# runtime headers fail the build instead of slipping through. End-user CLI
# builds (`tpy`, `tpyc`) keep an empty default -- consumers opt in via their
# own toolchain flags. See STRICT_WARN_FLAGS for the rationale per flag.
CPP_CONFIG = CppCompilerConfig.from_env()
CPP_CONFIG.warn_flags = list(STRICT_WARN_FLAGS)

# Paths
TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"    # All tests (grouped by feature)
PROJECT_ROOT = TESTS_DIR.parent
LIB_DIR = PROJECT_ROOT / "lib"
CPY_LIB_DIR = LIB_DIR / "cpy"
TPY_LIB_DIR = LIB_DIR / "tpy"
DEFAULT_LIB_DIRS = [TPY_LIB_DIR]
RUNTIME_DIR = PROJECT_ROOT / "runtime" / "cpp" / "include"


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

    compiler = Compiler(stub_src, default_int="Int32", lib_dirs=DEFAULT_LIB_DIRS)
    compiled_modules = compiler.compile()
    entry = next(m for m in compiled_modules if m.is_entry_point)

    build_dir = cache_dir / "build"
    build_dir.mkdir()
    for mod in compiled_modules:
        compiler.generate_code(mod, build_dir, entry_module_name=entry.name,
                               options=TEST_CODEGEN_OPTIONS)

    layout = BuildLayout(build_dir, entry.name, build_variant="debug")

    # Collect non-local (stdlib) .cpp files
    stdlib_cpps: list[Path] = []
    for mod in compiled_modules:
        if mod.is_entry_point:
            continue
        cpp = layout.cpp_path(mod.name)
        if cpp.exists():
            stdlib_cpps.append(cpp)

    if not stdlib_cpps:
        return _StdlibCache(objects=[], cpp_relpaths=set())

    # Compile each stdlib .cpp -> .o.
    # Parallelized across CPU cores; uses PCH (when available) so each
    # compile skips re-parsing the runtime headers.
    obj_dir = cache_dir / "obj"
    obj_dir.mkdir()

    pch_header = get_pch_header()
    pch_include = [] if pch_header is None else ["-include", str(pch_header)]

    # Add include dirs for any third-party deps declared by stdlib modules
    # (e.g. `# tpy: link_third_party("pcre2")` in _bindings/pcre2.py needs the
    # PCRE2 header on -I). We always resolve in bundled mode here -- the
    # cache key includes runtime + lib hashes so the cache is per-mode-implicit.
    third_party_plan = resolve_build_plan(
        dep_names=compiler.collect_third_party_deps(),
        runtime_cpp_dir=RUNTIME_DIR.parent,
        modes={},  # use each lib's default_mode (bundled for pcre2)
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
        print(f"WARNING: {exc}", file=sys.stderr)
        return _StdlibCache(objects=[], cpp_relpaths=set())

    relpaths = {rel for rel, _ in results}
    objects = [obj for _, obj in results]

    # Also pre-compile bundled C deps (e.g. PCRE2 .c files). These are
    # referenced by stdlib .o files (e.g. re.o links against pcre2_*),
    # so any test linking the stdlib cache needs them available -- even
    # tests that don't import the relevant module, because the cache .o
    # set is shared. Compiled with the C compiler derived from the C++ one.
    #
    # No opt_flags applied here, mirroring the .cpp pre-compile above:
    # tests always run a single shared cache regardless of build variant.
    # End-user `tpyc -xO` builds go through `BuildLayout.build_cpp_commands`
    # (not this cache) and DO get -O3 on the same .c sources.
    if third_party_plan.c_sources:
        from tpyc.compiler import _derive_c_compiler
        c_compiler = _derive_c_compiler(CPP_CONFIG.compiler)
        for c_src, c_flags in third_party_plan.c_sources:
            obj_path = obj_dir / (c_src.stem + ".o")
            prefix = ["ccache"] if CPP_CONFIG.ccache else []
            cmd = [*prefix, *c_compiler, *CPP_CONFIG.extra_flags,
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
            print(f"WARNING: {exc}", file=sys.stderr)
            return _StdlibCache(objects=[], cpp_relpaths=set())
        objects.extend(rt_objs)

    return _StdlibCache(objects=objects, cpp_relpaths=relpaths)


_SHARED_CACHE_ROOT_ENV = "TPYC_SHARED_CACHE_DIR"


def _default_shared_cache_root() -> Path:
    """Default shared-cache root, following XDG Base Directory on Linux/macOS
    and %LOCALAPPDATA% on Windows.

    Preference order: `$XDG_CACHE_HOME/tpyc` > `%LOCALAPPDATA%\\tpyc\\cache`
    (Windows) > `~/.cache/tpyc`. Matches ccache / cargo / uv / pip convention;
    persists across reboots, unlike /tmp.
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "tpyc"
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "tpyc" / "cache"
    return Path.home() / ".cache" / "tpyc"


def _shared_cache_root() -> Path:
    """Root dir for shared caches (overridable via TPYC_SHARED_CACHE_DIR)."""
    override = os.environ.get(_SHARED_CACHE_ROOT_ENV)
    return Path(override) if override else _default_shared_cache_root()


@functools.cache
def _libtpy_hash() -> str:
    """Hash of all .py files under lib/tpy/ (the stdlib source). Session-cached."""
    files = sorted(TPY_LIB_DIR.rglob("*.py"))
    return _hash_files(files)


@functools.cache
def _stdlib_cache_key() -> str:
    """Content-addressed key for the persistent stdlib .o cache.

    Captures everything that affects the produced .o files: runtime headers,
    stdlib Python source (compiled into the .o files), and C++ build config.
    """
    h = hashlib.sha256()
    h.update(_runtime_hash().encode())
    h.update(b"\0")
    h.update(_libtpy_hash().encode())
    h.update(b"\0")
    h.update(repr((
        CPP_CONFIG.compiler,
        CPP_CONFIG.std,
        CPP_CONFIG.extra_flags,
        CPP_CONFIG.warn_flags,
    )).encode())
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
        }
        (cache_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (cache_dir / ".ready").touch()
        return _StdlibCache(objects=rebased_objects, cpp_relpaths=cache.cpp_relpaths)
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
            print(
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
                    print(f"PCH: built tpy_pch.hpp.gch ({time.monotonic() - t0:.1f}s)")
            if _pch_path is not None:
                _set_pch_ccache_sloppiness()
    except Exception as exc:
        # Catch broadly (matches get_stdlib_cache): a PCH failure must not
        # abort the worker -- per-case compiles will fall back to parsing
        # the runtime headers from scratch.
        print(
            f"WARNING: PCH cache unavailable ({exc}); compiling without PCH",
            file=sys.stderr,
        )
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
        print(
            f"WARNING: persistent stdlib cache unavailable ({exc}); "
            f"falling back to ephemeral",
            file=sys.stderr,
        )

    # Fallback: ephemeral per-process build
    cache_dir = Path(tempfile.mkdtemp(prefix="tpyc_stdlib_"))
    atexit.register(shutil.rmtree, str(cache_dir), True)
    try:
        _stdlib_cache = _setup_stdlib_cache(cache_dir)
    except Exception as exc:
        print(f"WARNING: stdlib pre-compilation failed: {exc}", file=sys.stderr)
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
    # Linker flags from # tpy: link() directives
    link_flags: list[str] = field(default_factory=list)
    # Third-party (link_third_party) build inputs: include dirs for the C
    # bindings, extra link flags (system mode), and bundled C source files
    # to compile into the test binary (e.g. PCRE2 .c files in bundled mode).
    third_party_include_dirs: list[Path] = field(default_factory=list)
    third_party_link_flags: list[str] = field(default_factory=list)
    third_party_c_sources: list[tuple[Path, list[str]]] = field(default_factory=list)


def _validate_default_int_name(name: str) -> str:
    allowed = {"Int32", "Int64", "BigInt"}
    if name not in allowed:
        pytest.fail(
            f"Invalid default int '{name}'. Expected one of: {', '.join(sorted(allowed))}"
        )
    return name


def load_case_options(case_dir: Path) -> dict[str, str]:
    """Load and validate optional per-case test options.json."""
    options_path = case_dir / "options.json"
    if not options_path.exists():
        return {}

    try:
        raw = json.loads(options_path.read_text())
    except json.JSONDecodeError as e:
        pytest.fail(f"{options_path}: invalid JSON ({e.msg})")

    if not isinstance(raw, dict):
        pytest.fail(f"{options_path}: expected JSON object")

    allowed_keys = {"default_int"}
    unknown = sorted(k for k in raw.keys() if k not in allowed_keys)
    if unknown:
        pytest.fail(f"{options_path}: unsupported keys: {', '.join(unknown)}")

    if "default_int" in raw:
        value = raw["default_int"]
        if not isinstance(value, str):
            pytest.fail(f"{options_path}: 'default_int' must be a string")
        _validate_default_int_name(value)

    return raw


def get_case_default_int(case_dir: Path) -> str:
    """Resolve default integer mode for a test case."""
    options = load_case_options(case_dir)
    return _validate_default_int_name(options.get("default_int", "Int32"))


def compile_with_diagnostics(src_file: Path, output_dir: Path, default_int: str | None = None) -> CompileResult:
    """Compile a TurboPython file and capture diagnostics.

    Returns CompileResult with success status, diagnostics, and output paths.
    Warnings are collected but don't cause failure. Errors cause failure.
    Uses Compiler for multi-module support.
    """
    module_name = get_module_name(src_file)
    default_int = _validate_default_int_name(default_int or "Int32")

    try:
        # Use Compiler for multi-module support
        compiler = Compiler(src_file, default_int=default_int, lib_dirs=DEFAULT_LIB_DIRS)
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

        # Generate code for all modules and track paths
        entry_module = next(m for m in compiled_modules if m.is_entry_point)
        src_dir = src_file.parent.resolve()
        all_modules = []
        for mod in compiled_modules:
            hpp_path, cpp_path = compiler.generate_code(
                mod, output_dir, entry_module_name=entry_module.name,
                options=TEST_CODEGEN_OPTIONS
            )
            is_local = False
            try:
                mod.path.resolve().relative_to(src_dir)
                is_local = True
            except ValueError:
                pass
            # cpp_path is None for native_module (binding-only) modules
            all_modules.append((mod.name, hpp_path, cpp_path, is_local))


        # Return paths for the entry point module
        layout = BuildLayout(output_dir, entry_module.name)
        hpp_path = layout.hpp_path(entry_module.name)
        cpp_path = layout.cpp_path(entry_module.name)
        ctx = entry_module.analyzer.ctx if entry_module.analyzer else None
        declared_var_types = ctx.declared_var_types if ctx else None
        ptr_deref_facts = ctx.ptr_deref_facts if ctx else None
        subscript_bounds_facts = ctx.subscript_bounds_facts if ctx else None
        div_zero_facts = ctx.div_zero_facts if ctx else None
        cast_safe_facts = ctx.cast_safe_facts if ctx else None
        link_flags = compiler.collect_link_flags()
        # Resolve third-party deps in bundled mode (default for tests). When
        # a system-mode test variant is needed, switch the modes dict here.
        tp_plan = resolve_build_plan(
            dep_names=compiler.collect_third_party_deps(),
            runtime_cpp_dir=RUNTIME_DIR.parent,
            modes={},
        )
        return CompileResult(success=True, diagnostics=diagnostics, hpp_path=hpp_path, cpp_path=cpp_path,
                             all_modules=all_modules, declared_var_types=declared_var_types,
                             ptr_deref_facts=ptr_deref_facts,
                             subscript_bounds_facts=subscript_bounds_facts,
                             div_zero_facts=div_zero_facts,
                             cast_safe_facts=cast_safe_facts,
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


def pytest_configure(config):
    """Print ccache status; manage session fingerprint file."""
    is_master = os.environ.get("PYTEST_XDIST_WORKER") is None
    if not is_master:
        return

    if config.getoption("--no-ccache"):
        # Every compile path is gated on CPP_CONFIG.ccache, so flipping it
        # to False is sufficient -- we stop prepending `ccache` entirely
        # rather than spawning it with CCACHE_DISABLE=1 as a pass-through.
        CPP_CONFIG.ccache = False
        print("C++ compilation: ccache disabled via --no-ccache")
    elif CPP_CONFIG.ccache:
        print("C++ compilation: using ccache")
    else:
        print("C++ compilation: ccache not found (install for faster re-runs)")

    if config.getoption("--update-snapshots"):
        global UPDATE_EXPECTED
        UPDATE_EXPECTED = True
        # Propagate to xdist workers (subprocesses inherit os.environ, and
        # their conftest import reads the env var at module load time).
        os.environ["UPDATE_EXPECTED"] = "1"

    if config.getoption("--clean"):
        root = _shared_cache_root()
        wiped = []
        for sub in ("pch", "stdlib-objs"):
            target = root / sub
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
                wiped.append(sub)
        if wiped:
            print(f"--clean: wiped {root}/{{{','.join(wiped)}}}")
        else:
            print(f"--clean: no cache dirs to wipe under {root}")

    if UPDATE_EXPECTED:
        # Refresh the single source-of-truth session fingerprint file once on
        # the master process. Workers see it via the file system.
        write_session_fingerprints(compute_session_fingerprints())
        return

    # Normal mode: warn when the recorded session fingerprints are stale so
    # users notice and refresh -- otherwise the affected runtime phases keep
    # re-running on every invocation. Skipped silently when no session file
    # exists (first-ever bootstrap).
    recorded = read_session_fingerprints()
    if recorded:
        current = compute_session_fingerprints()
        stale = [k for k in ("runtime", "libtpy", "cpy_stubs") if current[k] != recorded.get(k)]
        if stale:
            print(
                f"WARNING: session fingerprint stale ({', '.join(stale)}); "
                f"runtime phase{'s' if len(stale) > 1 else ''} will re-run for "
                f"every applicable case. Refresh via update_snapshots.py."
            )

    # Pre-warm persistent caches on master before workers spawn so each
    # worker hits the on-disk fast path immediately rather than serializing
    # on LOCK_EX. Without this, the first exec-phase test in each worker
    # would appear to take ~cache-build-time (~20s cold) even though the
    # actual test is quick.
    get_pch_header()
    get_stdlib_cache()


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
        print(
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
    """Hash of all CPython stub files under lib/cpy/tpy/. Session-cached."""
    stubs = sorted((CPY_LIB_DIR / "tpy").rglob("*.py"))
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
                  c_sources: list[tuple[Path, list[str]]] | None = None) -> RunResult:
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

    # Run and capture output
    exe_file = layout.binary_path()
    result = subprocess.run([str(exe_file)], capture_output=True, text=True)
    return RunResult(
        success=(result.returncode == 0),
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode
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


_VAR_NAME_RE = re.compile(r'\s*(\w+)\s*(?::\s*[\w\[\], .|]+\s*)?=')


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
        # Extract variable name from the source line
        line_text = source.splitlines()[ann.line - 1]
        var_match = _VAR_NAME_RE.match(line_text)
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


def check_or_update(actual: str, expected_file: Path, description: str,
                    *, compare_only: bool = False) -> None:
    """Compare actual with expected, or update expected if UPDATE_EXPECTED is set.

    In update mode, creates parent directories and writes the file.
    In test mode, asserts that actual matches expected (missing file = empty expected).

    Pass ``compare_only=True`` to always compare and never write -- used by the
    cpy phase to verify CPython output against the canonical output.txt
    produced by the exec phase, even in update mode.
    """
    if UPDATE_EXPECTED and not compare_only:
        expected_file.parent.mkdir(parents=True, exist_ok=True)
        expected_file.write_text(actual)
    else:
        expected = expected_file.read_text() if expected_file.exists() else ""
        if actual != expected:
            diff = _format_unified_diff(expected, actual, fromfile=str(expected_file), tofile="actual")
            pytest.fail(
                f"{description} differs: {expected_file}\n{diff}",
                pytrace=False,
            )


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


def _discover_from_dirs(base_dirs: list[Path]):
    """Discover test cases from the given directories.

    Walks recursively looking for ``src/`` directories that contain ``.py``
    files, but skips build artifact trees (__tpyc__, expected, __pycache__)
    to avoid scanning thousands of irrelevant directories.

    Returns list of (name, case_dir, main_src) tuples.
    """
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
            src_files = list(cur.glob("*.py"))
            if not src_files:
                continue

            # Prefer main.py as entry point, otherwise pick first alphabetically
            main_src = None
            for sf in src_files:
                if sf.name == "main.py":
                    main_src = sf
                    break
            if main_src is None:
                main_src = sorted(src_files, key=lambda p: p.name)[0]

            rel_path = case_dir.relative_to(base_dir)
            name = f"{prefix}/{rel_path}".replace("\\", "/")
            cases.append((name, case_dir, main_src))

    return cases


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
