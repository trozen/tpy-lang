"""
REPL Backend Abstraction

Two backend types for executing compiled TurboPython code in the REPL:
- ClangReplBackend: Incremental JIT via clang-repl (fastest for iteration)
- CompileBackend: Traditional compile-and-run via any C++ compiler
  (with PCH caching and code-change detection)

Auto-detection order: g++ -> clang++ -> zig (clang-repl via explicit --cxx only)
"""

from __future__ import annotations
import abc
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .compiler import CppCompilerConfig


def _fmt_ms(seconds: float) -> str:
    """Format seconds as milliseconds string."""
    ms = seconds * 1000
    if ms < 1:
        return "<1ms"
    return f"{ms:.0f}ms"


from . import get_runtime_dir


class BackendResult:
    """Result of executing code through a backend."""
    __slots__ = ("success", "stdout", "stderr", "t_build", "t_run", "build_cached")

    def __init__(self, success: bool, stdout: str = "", stderr: str = "",
                 t_build: float = 0.0, t_run: float = 0.0,
                 build_cached: bool = False):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.t_build = t_build
        self.t_run = t_run
        self.build_cached = build_cached


class ReplBuildDeps:
    """Link-time dependencies derived from the session's compiled module set.

    Assembled per eval by the REPL session (tpyc/repl.py) from the same
    helpers the file-compile path uses (discover_runtime_cpp_sources,
    resolve_build_plan, collect_link_flags), so what a module needs to link
    cannot drift between `tpy file.py` and the REPL. Binary-building
    backends compile and link these; the JIT backend cannot (see BUGS.md).
    """
    __slots__ = ("runtime_sources", "third_party_sources", "include_dirs",
                 "link_flags")

    def __init__(self,
                 runtime_sources: list[Path] | None = None,
                 third_party_sources: list[tuple[Path, list[str]]] | None = None,
                 include_dirs: list[Path] | None = None,
                 link_flags: list[str] | None = None):
        # TPy-owned always-linked runtime impls (os_impl.cpp, socket_impl.cpp, ...)
        self.runtime_sources = runtime_sources or []
        # Bundled third-party sources with their per-source compile flags
        # (PCRE2 .c files, Hinnant date tz.cpp + date_shim.cpp, ...)
        self.third_party_sources = third_party_sources or []
        self.include_dirs = include_dirs or []
        # Managed-lib link flags + raw `# tpy: link()` flags, in that order
        self.link_flags = link_flags or []


class REPLBackend(abc.ABC):
    """Abstract base for REPL execution backends."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable backend name (e.g. 'clang-repl-18', 'g++-14')."""

    @abc.abstractmethod
    def startup(self) -> None:
        """One-time initialization (PCH build, process launch, etc.)."""

    @abc.abstractmethod
    def execute(
        self,
        all_hpp_code: list[str],
        all_cpp_code: list[str],
        all_hpp_paths: list[Path],
        all_cpp_paths: list[Path],
        deps: "ReplBuildDeps | None" = None,
    ) -> BackendResult:
        """Build and run the current REPL state.

        Args:
            all_hpp_code: Generated hpp source strings for all modules.
            all_cpp_code: Generated cpp source strings for all modules.
            all_hpp_paths: Paths where hpp files have been written.
            all_cpp_paths: Paths where cpp files have been written.
            deps: Link-time dependencies of the compiled module set.

        Returns:
            BackendResult with success, output, and timing info.
        """

    @abc.abstractmethod
    def cleanup(self) -> None:
        """Release resources (kill subprocess, etc.)."""


# ---------------------------------------------------------------------------
# Compile backend (g++ / clang++)
# ---------------------------------------------------------------------------

class CompileBackend(REPLBackend):
    """Traditional compile-and-run backend using g++ or clang++.

    Uses incremental compilation: each .cpp is compiled to .o separately,
    and only recompiled when its content changes. Stdlib modules are compiled
    once on first execution (in parallel), then reused across REPL inputs.
    """

    # Extra flags used for both PCH and .o compilation (must match)
    _OPT_FLAGS = ["-g0", "-pipe"]

    def __init__(self, compiler: list[str], temp_dir: Path, module_name: str,
                 verbose: int = 0):
        from .compiler import CppCompilerConfig
        self._temp_dir = temp_dir
        self._module_name = module_name
        self._config = CppCompilerConfig(compiler=compiler)
        self._pch_path: Path | None = None
        self._pch_thread: threading.Thread | None = None
        self._pch_build_time: float | None = None
        self._pch_needs_build = False
        self._binary_path = temp_dir / module_name
        self._build_dir = temp_dir / "build"
        self._build_dir.mkdir(exist_ok=True)
        self._verbose = verbose
        # Track content of each .cpp to detect changes
        self._cpp_hashes: dict[str, str] = {}
        # Cached .o paths keyed by .cpp path string
        self._obj_cache: dict[str, str] = {}
        # Support objects (runtime impls + bundled third-party sources),
        # keyed by source path -> (source mtime, .o path). The mtime keeps a
        # dev editing runtime .cpp mid-session from linking a stale object
        # (mirrors the PCH cache's staleness check); otherwise each source
        # compiles once, on the first eval that needs it.
        self._support_objs: dict[str, tuple[float, str]] = {}
        # Third-party include dirs from the current eval's build plan
        self._extra_include_dirs: list[Path] = []
        self._n_jobs = os.cpu_count() or 1

    @property
    def name(self) -> str:
        return self._config.compiler_name

    @property
    def pch_cache_dir(self) -> Path:
        return self._get_pch_cache_dir()

    def startup(self) -> None:
        # Check if PCH needs building before spawning the thread,
        # so we can print the message from the main thread cleanly
        self._pch_needs_build = self._pch_is_stale()
        if self._pch_needs_build:
            print("Precompiling C++ headers in background...")
        self._pch_thread = threading.Thread(target=self._setup_pch, daemon=True)
        self._pch_thread.start()

    def _wait_for_pch(self) -> None:
        thread = self._pch_thread
        if thread is not None:
            self._pch_thread = None
            thread.join()
            t = self._pch_build_time
            self._pch_build_time = None
            if t is not None and t >= 0:
                print(f"  [pch] precompiled tpy.hpp ({_fmt_ms(t)})",
                      file=sys.stderr)
            elif t is not None:
                print("  [pch] failed, headers will be parsed each time",
                      file=sys.stderr)

    def _common_flags(self) -> list[str]:
        runtime_dir = get_runtime_dir()
        flags = [
            *self._config.compiler, f"-std={self._config.std}",
            *self._config.extra_flags,
            *self._OPT_FLAGS,
            "-I", str(runtime_dir / "cpp" / "include"),
            "-I", str(self._temp_dir),
        ]
        for d in self._extra_include_dirs:
            flags += ["-I", str(d)]
        if self._pch_path:
            flags += ["-include", str(self._pch_path)]
        return flags

    def _obj_path(self, cpp_path: Path) -> Path:
        try:
            rel = cpp_path.relative_to(self._temp_dir)
            obj_name = str(rel).replace(os.sep, "_").removesuffix(".cpp") + ".o"
        except ValueError:
            obj_name = cpp_path.stem + ".o"
        return self._build_dir / obj_name

    def _compile_one(self, cpp_path: Path) -> subprocess.CompletedProcess[str]:
        obj = self._obj_path(cpp_path)
        cmd = [*self._common_flags(), "-c", "-o", str(obj), str(cpp_path)]
        return subprocess.run(cmd, capture_output=True, text=True)

    def _ensure_support_objects(
        self, deps: ReplBuildDeps | None,
    ) -> tuple[list[str], str]:
        """Compile any not-yet-built support sources; return (.o list, error).

        Runtime impl sources compile with the C++ driver + runtime include;
        third-party sources compile exactly like the file path's
        `build_cpp_commands`: per-suffix driver, only their own flags. PCH
        is omitted (each support source compiles once per session). All
        results are cached for the session.
        """
        if deps is None:
            return [], ""
        from .compiler import third_party_source_driver
        runtime_include = get_runtime_dir() / "cpp" / "include"

        def fresh_mtime(src: Path) -> float | None:
            """The source's current mtime if it needs (re)compiling, else None.
            Captured pre-compile so an edit racing the compile re-triggers."""
            mtime = src.stat().st_mtime
            cached = self._support_objs.get(str(src))
            return None if cached is not None and cached[0] == mtime else mtime

        jobs: list[tuple[Path, float, list[str]]] = []
        for src in deps.runtime_sources:
            mtime = fresh_mtime(src)
            if mtime is not None:
                # warn_flags included to match build_cpp_commands' treatment
                # of runtime sources (vendored third-party sources below get
                # only their own flags there too).
                jobs.append((src, mtime, [
                    *self._config.compiler, f"-std={self._config.std}",
                    *self._config.extra_flags, *self._config.warn_flags,
                    *self._OPT_FLAGS,
                    "-I", str(runtime_include),
                ]))
        for src, flags in deps.third_party_sources:
            mtime = fresh_mtime(src)
            if mtime is not None:
                driver = third_party_source_driver(
                    src, self._config.compiler, self._config.std)
                jobs.append((src, mtime, [*driver, *self._OPT_FLAGS, *flags]))

        def build_one(job: tuple[Path, float, list[str]]) -> tuple[Path, float, str, str]:
            src, mtime, cmd_prefix = job
            # Parent dir in the name disambiguates same-stem sources from
            # different libs (e.g. two vendored trees both shipping error.c).
            obj = self._build_dir / f"support_{src.parent.name}_{src.stem}.o"
            cmd = [*cmd_prefix, "-c", "-o", str(obj), str(src)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            return src, mtime, str(obj), r.stderr if r.returncode != 0 else ""

        if jobs:
            if self._verbose >= 1:
                names = [src.name for src, _, _ in jobs]
                print(f"  [build] compiling {len(jobs)} support files: "
                      f"{', '.join(names)}", file=sys.stderr)
            with ThreadPoolExecutor(max_workers=self._n_jobs) as pool:
                for src, mtime, obj, err in pool.map(build_one, jobs):
                    if err:
                        return [], err
                    self._support_objs[str(src)] = (mtime, obj)

        wanted = [str(s) for s in deps.runtime_sources]
        wanted += [str(s) for s, _ in deps.third_party_sources]
        return [self._support_objs[k][1] for k in wanted], ""

    def execute(
        self,
        all_hpp_code: list[str],
        all_cpp_code: list[str],
        all_hpp_paths: list[Path],
        all_cpp_paths: list[Path],
        deps: ReplBuildDeps | None = None,
    ) -> BackendResult:
        self._extra_include_dirs = deps.include_dirs if deps else []

        # Determine which .cpp files changed
        changed: list[Path] = []
        for cpp_path, cpp_code in zip(all_cpp_paths, all_cpp_code):
            key = str(cpp_path)
            if self._cpp_hashes.get(key) != cpp_code:
                changed.append(cpp_path)

        if not changed and self._binary_path.exists():
            # Nothing changed, reuse binary
            return self._run_binary(0.0, build_cached=True)

        t_build_start = time.monotonic()
        self._wait_for_pch()

        # Compile link-time support sources (runtime impls + bundled
        # third-party deps) that aren't built yet; each compiles once per
        # session. Must precede the generated-TU compile so a support
        # failure surfaces cleanly rather than as undefined refs at link.
        support_objs, support_err = self._ensure_support_objects(deps)
        if support_err:
            return BackendResult(
                False, stderr=f"C++ compilation failed:\n{support_err}",
                t_build=time.monotonic() - t_build_start)

        # Compile changed .cpp files (parallel for multiple files)
        if self._verbose >= 1 and changed:
            names = [p.stem + ".cpp" for p in changed]
            print(f"  [build] compiling {len(changed)} files: {', '.join(names)}",
                  file=sys.stderr)

        failed_stderr = ""
        if len(changed) > 1 and self._n_jobs > 1:
            with ThreadPoolExecutor(max_workers=self._n_jobs) as pool:
                futures = {pool.submit(self._compile_one, p): p for p in changed}
                for future in as_completed(futures):
                    r = future.result()
                    if r.returncode != 0 and not failed_stderr:
                        failed_stderr = r.stderr
        else:
            for cpp_path in changed:
                r = self._compile_one(cpp_path)
                if r.returncode != 0:
                    failed_stderr = r.stderr
                    break

        if failed_stderr:
            return BackendResult(False, stderr=f"C++ compilation failed:\n{failed_stderr}",
                                 t_build=time.monotonic() - t_build_start)

        # Update hash cache for successfully compiled files
        for cpp_path, cpp_code in zip(all_cpp_paths, all_cpp_code):
            key = str(cpp_path)
            self._cpp_hashes[key] = cpp_code
            self._obj_cache[key] = str(self._obj_path(cpp_path))

        # Link all .o files
        all_objs = [self._obj_cache[str(p)] for p in all_cpp_paths]
        link_cmd = [
            *self._config.compiler,
            "-o", str(self._binary_path),
            *all_objs,
            *support_objs,
            *self._config.link_flags,
            *(deps.link_flags if deps else []),
        ]
        result = subprocess.run(link_cmd, capture_output=True, text=True)
        t_build = time.monotonic() - t_build_start

        if result.returncode != 0:
            return BackendResult(False, stderr=f"C++ link failed:\n{result.stderr}",
                                 t_build=t_build)

        return self._run_binary(t_build, build_cached=False)

    def _run_binary(self, t_build: float, build_cached: bool) -> BackendResult:
        t_run_start = time.monotonic()
        result = subprocess.run([str(self._binary_path)], capture_output=True, text=True)
        t_run = time.monotonic() - t_run_start

        if result.returncode != 0:
            output = result.stdout + result.stderr
            if not output.strip():
                output = f"Runtime error (exit code {result.returncode})\n"
            return BackendResult(False, stderr=output, t_build=t_build, t_run=t_run,
                                 build_cached=build_cached)

        return BackendResult(True, stdout=result.stdout, stderr=result.stderr,
                             t_build=t_build, t_run=t_run,
                             build_cached=build_cached)

    def cleanup(self) -> None:
        pass

    # -- PCH management --

    def _get_pch_cache_dir(self) -> Path:
        runtime_dir = get_runtime_dir()
        opt = " ".join(self._OPT_FLAGS)
        key_data = f"{self._config.compiler_name}:{self._config.std}:{opt}:{runtime_dir}"
        key = hashlib.md5(key_data.encode()).hexdigest()[:12]
        cache_dir = Path.home() / ".cache" / "tpyc" / f"pch_{key}"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _pch_is_stale(self) -> bool:
        cache_dir = self._get_pch_cache_dir()
        pch_gch = cache_dir / "tpy_pch.hpp.gch"
        if not pch_gch.exists():
            return True
        runtime_dir = get_runtime_dir()
        runtime_include = runtime_dir / "cpp" / "include" / "tpy"
        pch_mtime = pch_gch.stat().st_mtime
        return any(h.stat().st_mtime > pch_mtime
                   for h in runtime_include.glob("**/*.hpp"))

    def _setup_pch(self) -> None:
        runtime_dir = get_runtime_dir()
        cache_dir = self._get_pch_cache_dir()
        pch_header = cache_dir / "tpy_pch.hpp"
        pch_gch = cache_dir / "tpy_pch.hpp.gch"

        if not self._pch_needs_build:
            self._pch_path = pch_header
            return

        pch_header.write_text('#include <tpy/tpy.hpp>\n')
        cmd = [
            *self._config.compiler, f"-std={self._config.std}",
            *self._config.extra_flags,
            *self._OPT_FLAGS,
            "-I", str(runtime_dir / "cpp" / "include"),
            "-x", "c++-header",
            str(pch_header), "-o", str(pch_gch),
        ]

        t0 = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.monotonic() - t0

        if result.returncode == 0:
            self._pch_path = pch_header
            self._pch_build_time = elapsed
        else:
            self._pch_build_time = -1.0  # signal failure


# ---------------------------------------------------------------------------
# clang-repl backend
# ---------------------------------------------------------------------------

class ClangReplBackend(REPLBackend):
    """Incremental JIT backend using clang-repl.

    Keeps a persistent clang-repl subprocess. On each REPL iteration, diffs
    the generated C++ against the previous state and sends only new/changed
    declarations to clang-repl.

    Communication uses dual sentinels: one on stderr to synchronize input
    processing, one on stdout to delimit program output.
    """

    def __init__(self, binary: str, temp_dir: Path, module_name: str):
        self._binary = binary
        self._temp_dir = temp_dir
        self._module_name = module_name
        self._proc: subprocess.Popen | None = None
        self._flat_header: Path | None = None
        self._prev_hpp_lines: list[str] = []
        self._prev_cpp_body_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lock = threading.Lock()
        self._stderr_lines: list[str] = []
        self._stderr_event = threading.Event()
        self._stderr_sentinel: str = ""

    @property
    def name(self) -> str:
        return self._binary

    def startup(self, silent: bool = False) -> None:
        self._flat_header = self._build_flat_header()
        runtime_dir = get_runtime_dir()

        if not silent:
            print("Starting clang-repl...", end="", flush=True)
        t0 = time.monotonic()
        self._proc = subprocess.Popen(
            [
                self._binary, "-Xcc=-std=c++23",
                f"-Xcc=-I{runtime_dir / 'cpp' / 'include'}",
                f"-Xcc=-I{self._temp_dir}",
                "-Xcc=-include", f"-Xcc={self._flat_header}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Start background thread to read stderr (avoids deadlocks)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        # Wait for startup by sending a no-op
        self._send_sync("int __tpy_startup = 0;")
        elapsed = time.monotonic() - t0
        if not silent:
            print(f" done ({_fmt_ms(elapsed)})")

    def execute(
        self,
        all_hpp_code: list[str],
        all_cpp_code: list[str],
        all_hpp_paths: list[Path],
        all_cpp_paths: list[Path],
        deps: ReplBuildDeps | None = None,
    ) -> BackendResult:
        # deps is accepted but unsupported: a JIT has no link step to feed
        # the support objects into, so modules needing runtime impls or
        # managed third-party libs fail with unresolved symbols here (see
        # the BUGS.md clang-repl entry). The default CompileBackend handles
        # them fully.
        if self._proc is None or self._proc.poll() is not None:
            # Auto-restart after crash
            try:
                self.startup(silent=True)
                self._resend_prev_state()
            except Exception as e:
                return BackendResult(False,
                                     stderr=f"clang-repl restart failed: {e}\n")

        t_build_start = time.monotonic()

        # Extract init body (the REPL statements from __tpy_init)
        entry_cpp = all_cpp_code[-1]
        cpp_body = self._extract_init_body(entry_cpp)

        # Extract top-level declarations (functions, records) from hpp
        entry_hpp = all_hpp_code[-1]
        hpp_decls = self._extract_toplevel_decls(entry_hpp, entry_cpp)
        new_decls = self._diff_lines(self._prev_hpp_lines, hpp_decls,
                                      insert_only=True)
        for decl in new_decls:
            err = self._send_sync(decl)
            if err and "error:" in err.lower():
                return BackendResult(False, stderr=f"clang-repl decl error:\n{err}",
                                     t_build=time.monotonic() - t_build_start)
        if new_decls:
            self._prev_hpp_lines = hpp_decls

        # Diff init body: send new statements
        new_cpp = self._diff_lines(self._prev_cpp_body_lines, cpp_body)
        if not new_cpp:
            return BackendResult(True, stdout="", t_build=0.0, t_run=0.0, build_cached=True)

        t_build = time.monotonic() - t_build_start

        # Execute new lines, capturing stdout between sentinels
        t_run_start = time.monotonic()
        code = "\n".join(new_cpp)
        stdout_output, stderr_output = self._send_and_capture(code)
        t_run = time.monotonic() - t_run_start

        # Detect if the process died (e.g. tpy_panic -> std::exit)
        if self._proc.poll() is not None:
            # Don't update _prev_cpp_body_lines so the failing statement
            # isn't replayed on restart
            return BackendResult(False,
                                 stderr="clang-repl crashed (will restart on next input)\n",
                                 t_build=t_build, t_run=t_run)

        if stderr_output and "error:" in stderr_output.lower():
            return BackendResult(False, stderr=stderr_output,
                                 t_build=t_build, t_run=t_run)

        # Update state on success
        self._prev_cpp_body_lines = cpp_body

        return BackendResult(True, stdout=stdout_output, stderr=stderr_output,
                             t_build=t_build, t_run=t_run)

    def cleanup(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=2)
            except Exception:
                self._proc.kill()

    def _resend_prev_state(self) -> None:
        """Replay previously-accepted declarations after a restart."""
        for decl in self._prev_hpp_lines:
            err = self._send_sync(decl)
            if err and "error:" in err.lower():
                raise RuntimeError(f"State replay failed:\n{err}")
        for stmt in self._prev_cpp_body_lines:
            err = self._send_sync(stmt)
            if err and "error:" in err.lower():
                raise RuntimeError(f"State replay failed:\n{err}")

    # -- Internal helpers --

    def _build_flat_header(self) -> Path:
        """Build a flat header that includes all tpy runtime headers via angle brackets."""
        runtime_dir = get_runtime_dir()
        tpy_hpp = runtime_dir / "cpp" / "include" / "tpy" / "tpy.hpp"
        content = tpy_hpp.read_text()

        # Convert relative includes to angle-bracket includes
        lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith('#include "') and stripped.endswith('"'):
                header_name = stripped[len('#include "'):-1]
                lines.append(f'#include <tpy/{header_name}>')
            elif stripped.startswith("#pragma once"):
                continue
            else:
                lines.append(line)

        flat_path = self._temp_dir / "tpy_flat.hpp"
        flat_path.write_text("\n".join(lines) + "\n")
        return flat_path

    def _read_stderr(self) -> None:
        """Background thread: read stderr lines and signal when sentinel seen."""
        while True:
            line = self._proc.stderr.readline()
            if not line:
                # Process exited -- unblock any waiter
                self._stderr_event.set()
                break
            with self._stderr_lock:
                if self._stderr_sentinel and self._stderr_sentinel in line:
                    self._stderr_event.set()
                else:
                    self._stderr_lines.append(line)

    def _send_sync(self, code: str) -> str:
        """Send code and wait for stderr sentinel (no stdout capture).

        Each line is a separate input for clang-repl, so multi-line
        declarations must be collapsed to single lines first.
        """
        sentinel = f"__SE_{time.monotonic_ns()}__"
        with self._stderr_lock:
            self._stderr_sentinel = sentinel
            self._stderr_lines.clear()
            self._stderr_event.clear()

        # Collapse to single line so clang-repl parses it as one input
        oneliner = " ".join(code.split())
        full = f'{oneliner}\nstd::cerr << "{sentinel}\\n";\n'
        self._proc.stdin.write(full)
        self._proc.stdin.flush()

        self._stderr_event.wait(timeout=30)
        with self._stderr_lock:
            return "".join(self._stderr_lines)

    def _send_and_capture(self, code: str) -> tuple[str, str]:
        """Send code and capture both stdout and stderr.

        Uses stdout sentinel to delimit program output, stderr sentinel
        to know when processing is complete. Each statement must be sent
        as a single line for clang-repl.
        """
        stdout_sentinel = f"__SO_{time.monotonic_ns()}__"
        stderr_sentinel = f"__SE_{time.monotonic_ns()}__"
        with self._stderr_lock:
            self._stderr_sentinel = stderr_sentinel
            self._stderr_lines.clear()
            self._stderr_event.clear()

        # Each statement is already a complete single-line entry from
        # _extract_init_body, so just join them with newlines.
        body = code

        full = (
            f'std::cout << "{stdout_sentinel}\\n" << std::flush;\n'
            f'{body}\n'
            f'std::cout << "{stdout_sentinel}\\n" << std::flush;\n'
            f'std::cerr << "{stderr_sentinel}\\n";\n'
        )
        self._proc.stdin.write(full)
        self._proc.stdin.flush()

        # Read stdout until we see the closing sentinel
        stdout_lines = []
        started = False
        while True:
            line = self._proc.stdout.readline()
            if not line:
                break
            if stdout_sentinel in line:
                if not started:
                    started = True
                    continue
                else:
                    break
            if started:
                stdout_lines.append(line)

        # Wait for stderr sentinel too
        self._stderr_event.wait(timeout=30)

        with self._stderr_lock:
            return "".join(stdout_lines), "".join(self._stderr_lines)

    def _extract_toplevel_decls(self, hpp_code: str, cpp_code: str) -> list[str]:
        """Extract user-defined functions/structs from generated code.

        For clang-repl, we need to send function definitions and struct
        declarations directly (not wrapped in namespaces). Extracts:
        - Struct/class definitions and operator overloads from hpp
        - Function definitions and global variable declarations from cpp
        """
        result = []

        # Extract struct definitions from hpp namespace block
        result.extend(self._extract_namespace_body(hpp_code))

        # Extract function definitions + global vars from cpp namespace block
        result.extend(self._extract_namespace_body(cpp_code))

        return result

    def _extract_namespace_body(self, code: str) -> list[str]:
        """Extract brace-balanced declarations from inside the namespace block.

        Skips includes, pragmas, comments, forward declarations of __tpy_init,
        __name__ constexpr, and stops before __tpy_init() or main().
        """
        result = []
        lines = code.splitlines()
        i = 0
        # Skip includes, pragmas, comments, blank lines before namespace
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped and not stripped.startswith(("//", "#")) and "namespace" not in stripped:
                break
            if "namespace" in stripped:
                i += 1
                break
            i += 1

        # Collect brace-balanced blocks from inside the namespace
        brace_depth = 0
        collecting = False
        current_block: list[str] = []
        while i < len(lines):
            stripped = lines[i].strip()
            if "__tpy_init()" in stripped or stripped.startswith("int main("):
                break
            if stripped.startswith("} // namespace"):
                break

            # Skip boilerplate
            if not collecting:
                if (not stripped or stripped.startswith("//")
                        or "__name__" in stripped
                        or ("__tpy_init" in stripped and ";" in stripped)):
                    i += 1
                    continue
                collecting = True

            if collecting:
                current_block.append(stripped)
                brace_depth += stripped.count("{") - stripped.count("}")
                # A block is complete when braces are balanced AND the last
                # line looks like a statement/definition end (';' or '}').
                # This keeps `template<typename T>` attached to what follows.
                if (brace_depth <= 0 and current_block
                        and (stripped.endswith(";") or stripped.endswith("}"))):
                    block = "\n".join(current_block)
                    if block.strip():
                        result.append(block)
                    current_block = []
                    collecting = False
                    brace_depth = 0
            i += 1

        return result

    def _extract_init_body(self, cpp_code: str) -> list[str]:
        """Extract complete statements from __tpy_init() body.

        Groups multi-line constructs (lambdas, for-loops, if-blocks) into
        single entries by tracking brace depth, so each returned string is
        a complete statement that clang-repl can parse on one line.
        """
        lines = cpp_code.splitlines()
        # First, collect raw body lines from inside __tpy_init
        body_lines: list[str] = []
        in_init = False
        func_depth = 0
        for line in lines:
            stripped = line.strip()
            if not in_init:
                if "__tpy_init()" in stripped and "{" in stripped:
                    in_init = True
                    func_depth = 1
                continue
            func_depth += stripped.count("{") - stripped.count("}")
            if func_depth <= 0:
                break
            if stripped in ("static bool initialized = false;",
                            "if (initialized) return;",
                            "initialized = true;",
                            "return 0;", "0;", "(void)(0);", ""):
                continue
            if stripped:
                body_lines.append(stripped)

        # Group lines into complete statements by tracking brace depth
        result: list[str] = []
        current: list[str] = []
        depth = 0
        for line in body_lines:
            current.append(line)
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                stmt = " ".join(current)
                # clang-repl executes at top level where lambdas can't use
                # capture-default. Variables are global so [] suffices.
                stmt = stmt.replace("[&]()", "[]()")
                # Hoist immediately-invoked lambdas into temp variables so
                # clang-repl JIT-compiles them in isolation (avoids stack
                # overflow from deeply nested template instantiation).
                stmt = self._hoist_lambdas(stmt, result)
                result.append(stmt)
                current = []
                depth = 0

        # Flush any remaining (shouldn't happen with well-formed code)
        if current:
            result.append(" ".join(current))

        return result

    @staticmethod
    def _hoist_lambdas(stmt: str, result: list[str]) -> str:
        """Extract immediately-invoked lambdas into temp variables.

        Transforms `...f([]() { body }())...` into:
            auto __lam_N = []() { body }();
            ...f(__lam_N)...
        """
        counter = len(result)
        while True:
            # Match [](){ ... }() -- an immediately-invoked lambda
            # Find the start: []() {
            m = re.search(r'\[\]\(\)\s*\{', stmt)
            if not m:
                break
            lam_start = m.start()
            # Find matching closing brace by tracking depth
            brace_start = m.end() - 1  # position of opening {
            depth = 1
            i = brace_start + 1
            while i < len(stmt) and depth > 0:
                if stmt[i] == '{':
                    depth += 1
                elif stmt[i] == '}':
                    depth -= 1
                i += 1
            if depth != 0:
                break
            # Check for () invocation after closing brace
            rest = stmt[i:]
            inv = re.match(r'\s*\(\)', rest)
            if not inv:
                break
            lam_end = i + inv.end()
            lam_expr = stmt[lam_start:lam_end]
            var_name = f"__lam_{counter}"
            counter += 1
            result.append(f"auto {var_name} = {lam_expr};")
            stmt = stmt[:lam_start] + var_name + stmt[lam_end:]
        return stmt

    def _diff_lines(self, old: list[str], new: list[str],
                     insert_only: bool = False) -> list[str]:
        """Return lines that changed between old and new.

        Args:
            insert_only: If True, only return "insert" ops (for declarations
                where redefinition is illegal in C++). If False, also return
                "replace" ops (for executable statements).
        """
        if not old:
            return new
        if old == new:
            return []
        include = {"insert"} if insert_only else {"insert", "replace"}
        added = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, old, new
        ).get_opcodes():
            if tag in include:
                added.extend(new[j1:j2])
        return added


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

def detect_backend(
    cxx: str,
    temp_dir: Path,
    module_name: str,
    verbose: int = 0,
) -> REPLBackend:
    """Create a backend based on --cxx value or auto-detection.

    Args:
        cxx: Compiler selection from --cxx flag (e.g. "auto", "gcc", "clang-repl").
        temp_dir: Temp directory for build artifacts.
        module_name: Fixed module name for the REPL.
        verbose: Verbosity level (0=quiet, 1=timing+build info, 2=+generated C++).

    Returns:
        An initialized (but not yet started) REPLBackend.
    """
    from .compiler import (
        _find_best_versioned, _resolve_compiler,
    )

    if cxx == "auto":
        return _auto_detect(temp_dir, module_name, verbose)

    # clang-repl: JIT backend (specific or versioned, e.g. clang-repl-18)
    if cxx == "clang-repl" or cxx.startswith("clang-repl-"):
        binary = cxx if shutil.which(cxx) else _find_best_versioned("clang-repl")
        if not binary:
            print("Warning: clang-repl not found, falling back to auto-detect",
                  file=sys.stderr)
            return _auto_detect(temp_dir, module_name, verbose)
        return ClangReplBackend(binary, temp_dir, module_name)

    # All other values: resolve via shared compiler detection
    resolved = _resolve_compiler(cxx)
    if resolved is None:
        print(f"Warning: C++ compiler '{cxx}' not found, falling back to auto-detect",
              file=sys.stderr)
        return _auto_detect(temp_dir, module_name, verbose)
    from .toolchain import warn_toolchain_unsupported
    warn_toolchain_unsupported(resolved)
    return CompileBackend(resolved, temp_dir, module_name, verbose=verbose)


def _auto_detect(temp_dir: Path, module_name: str, verbose: int = 0) -> REPLBackend:
    """Auto-detect the best available backend.

    Shares the CLI's capability-probed chain: best viable g++, then
    clang++, then zig (system or bundled).
    """
    from .toolchain import (
        _auto_detect_compiler, ToolchainUnsupportedError,
    )

    try:
        compiler = _auto_detect_compiler()
    except ToolchainUnsupportedError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if shutil.which(compiler[0]) is None:
        print("Error: no C++ compiler found (tried g++, clang++, zig)",
              file=sys.stderr)
        sys.exit(1)
    return CompileBackend(compiler, temp_dir, module_name, verbose=verbose)
