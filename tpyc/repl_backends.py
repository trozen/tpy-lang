"""
REPL Backend Abstraction

Two backend types for executing compiled TurboPython code in the REPL:
- ClangReplBackend: Incremental JIT via clang-repl (fastest for iteration)
- CompileBackend: Traditional compile-and-run via g++ or clang++
  (with PCH caching and code-change detection)

Auto-detection order: clang-repl -> clang++ -> g++
"""

from __future__ import annotations
import abc
import difflib
import glob
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
    ) -> BackendResult:
        """Build and run the current REPL state.

        Args:
            all_hpp_code: Generated hpp source strings for all modules.
            all_cpp_code: Generated cpp source strings for all modules.
            all_hpp_paths: Paths where hpp files have been written.
            all_cpp_paths: Paths where cpp files have been written.

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
    """Traditional compile-and-run backend using g++ or clang++."""

    def __init__(self, compiler: str, temp_dir: Path, module_name: str):
        from .compiler import CppCompilerConfig
        self._compiler = compiler
        self._temp_dir = temp_dir
        self._module_name = module_name
        self._config = CppCompilerConfig(compiler=compiler)
        self._pch_path: Path | None = None
        self._prev_entry_hpp: str | None = None
        self._prev_entry_cpp: str | None = None
        self._binary_path = temp_dir / module_name

    @property
    def name(self) -> str:
        return self._compiler

    def startup(self) -> None:
        self._setup_pch()

    def execute(
        self,
        all_hpp_code: list[str],
        all_cpp_code: list[str],
        all_hpp_paths: list[Path],
        all_cpp_paths: list[Path],
    ) -> BackendResult:
        entry_hpp = all_hpp_code[-1]
        entry_cpp = all_cpp_code[-1]
        cpp_changed = (
            entry_hpp != self._prev_entry_hpp
            or entry_cpp != self._prev_entry_cpp
            or not self._binary_path.exists()
        )

        if cpp_changed:
            t_build_start = time.monotonic()
            runtime_dir = get_runtime_dir()
            compile_cmd = [
                self._config.compiler, f"-std={self._config.std}",
                *self._config.extra_flags,
                "-I", str(runtime_dir / "cpp" / "include"),
                "-I", str(self._temp_dir),
            ]
            if self._pch_path:
                compile_cmd += ["-include", str(self._pch_path)]
            compile_cmd += [
                "-o", str(self._binary_path),
                *[str(p) for p in all_cpp_paths],
                *self._config.link_flags,
            ]
            result = subprocess.run(compile_cmd, capture_output=True, text=True)
            t_build = time.monotonic() - t_build_start

            if result.returncode != 0:
                return BackendResult(False, stderr=f"C++ compilation failed:\n{result.stderr}",
                                     t_build=t_build)
            self._prev_entry_hpp = entry_hpp
            self._prev_entry_cpp = entry_cpp
        else:
            t_build = 0.0

        # Run
        t_run_start = time.monotonic()
        result = subprocess.run([str(self._binary_path)], capture_output=True, text=True)
        t_run = time.monotonic() - t_run_start

        if result.returncode != 0:
            output = result.stdout + result.stderr
            if not output.strip():
                output = f"Runtime error (exit code {result.returncode})\n"
            return BackendResult(False, stderr=output, t_build=t_build, t_run=t_run,
                                 build_cached=not cpp_changed)

        return BackendResult(True, stdout=result.stdout, t_build=t_build, t_run=t_run,
                             build_cached=not cpp_changed)

    def cleanup(self) -> None:
        pass

    # -- PCH management --

    def _get_pch_cache_dir(self) -> Path:
        runtime_dir = get_runtime_dir()
        key_data = f"{self._config.compiler}:{self._config.std}:{runtime_dir}"
        key = hashlib.md5(key_data.encode()).hexdigest()[:12]
        cache_dir = Path.home() / ".cache" / "tpyc" / f"pch_{key}"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _setup_pch(self) -> None:
        runtime_dir = get_runtime_dir()
        cache_dir = self._get_pch_cache_dir()
        pch_header = cache_dir / "tpy_pch.hpp"
        pch_gch = cache_dir / "tpy_pch.hpp.gch"

        if pch_gch.exists():
            pch_mtime = pch_gch.stat().st_mtime
            runtime_include = runtime_dir / "cpp" / "include" / "tpy"
            needs_rebuild = False
            for header in runtime_include.glob("**/*.hpp"):
                if header.stat().st_mtime > pch_mtime:
                    needs_rebuild = True
                    break
            if not needs_rebuild:
                self._pch_path = pch_header
                return

        pch_header.write_text('#include <tpy/tpy.hpp>\n')
        cmd = [
            self._config.compiler, f"-std={self._config.std}",
            *self._config.extra_flags,
            "-I", str(runtime_dir / "cpp" / "include"),
            "-x", "c++-header",
            str(pch_header), "-o", str(pch_gch),
        ]

        print("Precompiling C++ headers...", end="", flush=True)
        t0 = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.monotonic() - t0

        if result.returncode == 0:
            self._pch_path = pch_header
            print(f" done ({_fmt_ms(elapsed)})")
        else:
            print(f" failed, headers will be parsed each time")


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
    ) -> BackendResult:
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

        return BackendResult(True, stdout=stdout_output, t_build=t_build, t_run=t_run)

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
                            "return 0;", "0;", ""):
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

def _find_best_versioned(prefix: str) -> str | None:
    """Find the highest-versioned binary matching prefix (e.g. 'clang-repl-')."""
    # Check PATH directories for prefix-N binaries
    best_ver = -1
    best_name: str | None = None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for path in glob.glob(os.path.join(d, f"{prefix}-*")):
            name = os.path.basename(path)
            suffix = name[len(prefix) + 1:]
            try:
                ver = int(suffix)
            except ValueError:
                continue
            if ver > best_ver and shutil.which(name):
                best_ver = ver
                best_name = name
    # Also check unversioned
    if best_name is None and shutil.which(prefix):
        best_name = prefix
    return best_name


def detect_backend(
    requested: str,
    temp_dir: Path,
    module_name: str,
) -> REPLBackend:
    """Create a backend based on user request or auto-detection.

    Args:
        requested: One of "auto", "clang-repl", "clang", "gcc".
        temp_dir: Temp directory for build artifacts.
        module_name: Fixed module name for the REPL.

    Returns:
        An initialized (but not yet started) REPLBackend.
    """
    if requested == "auto":
        return _auto_detect(temp_dir, module_name)

    if requested == "clang-repl":
        binary = _find_best_versioned("clang-repl")
        if not binary:
            print("Warning: clang-repl not found, falling back to auto-detect",
                  file=sys.stderr)
            return _auto_detect(temp_dir, module_name)
        return ClangReplBackend(binary, temp_dir, module_name)

    if requested == "clang":
        binary = _find_best_versioned("clang++")
        if not binary:
            print("Warning: clang++ not found, falling back to auto-detect",
                  file=sys.stderr)
            return _auto_detect(temp_dir, module_name)
        return CompileBackend(binary, temp_dir, module_name)

    if requested == "gcc":
        binary = _find_best_versioned("g++")
        if not binary:
            print("Warning: g++ not found, falling back to auto-detect",
                  file=sys.stderr)
            return _auto_detect(temp_dir, module_name)
        return CompileBackend(binary, temp_dir, module_name)

    print(f"Warning: unknown backend '{requested}', falling back to auto-detect",
          file=sys.stderr)
    return _auto_detect(temp_dir, module_name)


def _auto_detect(temp_dir: Path, module_name: str) -> REPLBackend:
    """Auto-detect the best available backend."""
    # Prefer clang-repl for fastest incremental compilation
    clang_repl = _find_best_versioned("clang-repl")
    if clang_repl:
        return ClangReplBackend(clang_repl, temp_dir, module_name)

    # Fall back to clang++
    clangpp = _find_best_versioned("clang++")
    if clangpp:
        print(f"Warning: clang-repl not found, using {clangpp} (slower)",
              file=sys.stderr)
        return CompileBackend(clangpp, temp_dir, module_name)

    # Fall back to g++
    gpp = _find_best_versioned("g++")
    if gpp:
        print(f"Warning: clang-repl not found, using {gpp} (slower)",
              file=sys.stderr)
        return CompileBackend(gpp, temp_dir, module_name)

    print("Error: no C++ compiler found (tried clang-repl, clang, gcc)",
          file=sys.stderr)
    sys.exit(1)
