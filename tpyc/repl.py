"""
TurboPython Interactive REPL

Provides an interactive Python-like experience with:
- Multi-line input support (for function/class definitions)
- Expression auto-printing (bare `5+3` prints the result)
- Error/panic recovery (failed statements don't accumulate)
- Precompiled headers for fast C++ compilation
- Verbose mode: -v for timing, -vv for generated C++
- Trailing backslash continues to next line
"""

from __future__ import annotations
import ast
import atexit
import difflib
import readline  # For history support
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .parse import ParseError, TpyExprStmt
from .sema import SemanticError, DiagnosticLevel
from .typesys import VoidType, CharType, is_any_str_type
from .compiler import Compiler, CppCompilerConfig


def get_runtime_dir() -> Path:
    """Get the path to the runtime directory."""
    package_root = Path(__file__).parent.parent
    return package_root / "runtime"


def _fmt_ms(seconds: float) -> str:
    """Format seconds as milliseconds string."""
    ms = seconds * 1000
    if ms < 1:
        return "<1ms"
    return f"{ms:.0f}ms"


class REPLSession:
    """Interactive TurboPython REPL session."""

    def __init__(self, verbose: int = 0, preload_files: list[Path] | None = None,
                 lib_dirs: list[Path] | None = None):
        self.accumulated_lines: list[str] = []
        self.verbose = verbose
        self.preload_files = preload_files or []
        self.lib_dirs = lib_dirs
        self.temp_dir = Path(tempfile.mkdtemp(prefix="tpyc_repl_"))
        self.prev_cpp_lines: list[str] = []  # For verbose diff
        self._module_name = "repl"
        self._cpp_config: CppCompilerConfig | None = None
        self._pch_path: Path | None = None
        self._prev_entry_hpp: str | None = None
        self._prev_entry_cpp: str | None = None
        self._binary_path = self.temp_dir / self._module_name
        atexit.register(self.cleanup)

    def _get_cpp_config(self) -> CppCompilerConfig:
        if self._cpp_config is None:
            self._cpp_config = CppCompilerConfig.from_env()
        return self._cpp_config

    def _get_pch_cache_dir(self) -> Path:
        """Get the cache directory for precompiled headers."""
        import hashlib
        config = self._get_cpp_config()
        runtime_dir = get_runtime_dir()
        # Cache key: compiler + standard + runtime location
        key_data = f"{config.compiler}:{config.std}:{runtime_dir}"
        key = hashlib.md5(key_data.encode()).hexdigest()[:12]
        cache_dir = Path.home() / ".cache" / "tpyc" / f"pch_{key}"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _setup_pch(self) -> None:
        """Pre-compile tpy runtime header for faster subsequent compilations.

        Caches the PCH in ~/.cache/tpyc/ and reuses across sessions.
        Rebuilds only when runtime headers change.
        """
        runtime_dir = get_runtime_dir()
        config = self._get_cpp_config()
        cache_dir = self._get_pch_cache_dir()

        pch_header = cache_dir / "tpy_pch.hpp"
        pch_gch = cache_dir / "tpy_pch.hpp.gch"

        # Check if cached PCH is still valid
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

        # Build PCH (first session or after runtime header changes)
        pch_header.write_text('#include <tpy/tpy.hpp>\n')
        cmd = [
            config.compiler, f"-std={config.std}",
            *config.extra_flags,
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

    def _preload_file(self, filepath: Path) -> bool:
        """Load and execute a file, accumulating its definitions.

        Returns True on success, False on error.
        """
        try:
            source = filepath.read_text()
        except FileNotFoundError:
            print(f"Error: File not found: {filepath}", file=sys.stderr)
            return False
        except IOError as e:
            print(f"Error reading {filepath}: {e}", file=sys.stderr)
            return False

        # Try to compile and run the file
        success, output = self._try_compile_and_run(source, maybe_auto_print=False)

        if success:
            # Accumulate all definitions from the file
            if self._should_accumulate(source):
                self.accumulated_lines.append(source)
            else:
                # For files with mixed content, extract just the definitions
                try:
                    tree = ast.parse(source)
                    definitions = []
                    for stmt in tree.body:
                        if isinstance(stmt, (
                            ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                            ast.Assign, ast.AnnAssign, ast.AugAssign,
                            ast.Import, ast.ImportFrom, ast.TypeAlias,
                        )):
                            # Get the source lines for this statement
                            start = stmt.lineno - 1
                            end = stmt.end_lineno if stmt.end_lineno else stmt.lineno
                            lines = source.split('\n')[start:end]
                            definitions.append('\n'.join(lines))
                    if definitions:
                        self.accumulated_lines.extend(definitions)
                except SyntaxError:
                    pass
            if output:
                print(output, end="")
            return True
        else:
            print(output, end="", file=sys.stderr)
            return False

    def _setup_readline(self) -> None:
        """Configure readline for history."""
        history_path = Path.home() / ".tpyc_history"
        try:
            readline.read_history_file(history_path)
        except FileNotFoundError:
            pass
        readline.set_history_length(1000)
        atexit.register(readline.write_history_file, history_path)

    def _read_paste_mode(self) -> str | None:
        """Read multiline input in paste mode. Returns None if cancelled."""
        print("Paste mode: paste your code, then Ctrl+D to execute (Ctrl+C to cancel)")
        lines = []
        try:
            while True:
                try:
                    line = input("... ")
                    lines.append(line)
                except EOFError:
                    break
        except KeyboardInterrupt:
            print("\nPaste cancelled.")
            return None

        print()  # Newline after Ctrl+D
        return "\n".join(lines)

    def run(self) -> int:
        """Main REPL loop. Returns exit code."""
        print("TurboPython REPL v0.1")
        print("Variables, functions, and classes are remembered between inputs.")
        print("Empty line unindents (or ends block at col 0). Trailing \\ continues input. Ctrl+D to exit.")
        print("Type .paste (or .p) for multiline input mode.")

        # Precompile runtime headers
        self._setup_pch()

        if self.verbose >= 2:
            print(f"[src] {self.temp_dir}/")

        # Preload files if specified
        for filepath in self.preload_files:
            print(f"Loading {filepath}...")
            if not self._preload_file(filepath):
                return 1

        # Auto-import tpy types if no files were preloaded
        if not self.preload_files:
            self.accumulated_lines.append("from tpy import *")
            print("(imported tpy types: Int32, Char, Array, Span, Ptr, ...)")

        # Setup readline
        self._setup_readline()

        while True:
            try:
                line = input(">>> ")

                # Handle special commands
                stripped = line.strip()
                if stripped in ("exit", "quit"):
                    break
                if not stripped:
                    continue

                # Dot commands
                if stripped.startswith("."):
                    if stripped in (".paste", ".p"):
                        pasted = self._read_paste_mode()
                        if pasted and pasted.strip():
                            self._process_input(pasted)
                    else:
                        print(f"Unknown command: {stripped.split()[0]}")
                    continue

                # Alt+Enter inserts literal newlines into readline buffer,
                # so input() may return multiline strings directly
                full_input = line

                # Handle backslash continuation (fallback for terminals
                # where Alt+Enter doesn't work)
                while full_input.rstrip().endswith("\\"):
                    full_input = full_input.rstrip()[:-1]  # Remove trailing backslash
                    try:
                        cont_line = input("... ")
                        full_input += "\n" + cont_line
                    except EOFError:
                        break

                # Check for multi-line continuation (incomplete syntax)
                if self._needs_continuation(full_input):
                    in_block = full_input.rstrip().endswith(":")
                    indent_level = 1  # Start with one indent level
                    while True:
                        try:
                            indent = "\t" * indent_level
                            cont_line = input("... " + indent)
                            # Empty line: reduce indent or end block
                            if not cont_line.strip():
                                if indent_level > 1:
                                    indent_level -= 1
                                    continue  # Don't add empty line, just reduce indent
                                else:
                                    break  # At first block level (or zero), end block
                            full_input += "\n" + indent + cont_line
                        except EOFError:
                            break
                        # For blocks (def/class/if/etc), require empty line to end
                        # For other continuations (unclosed parens), end when complete
                        if not in_block and not self._needs_continuation(full_input):
                            break
                        # Adjust indent based on what user typed
                        if cont_line.rstrip().endswith(":"):
                            indent_level += 1

                self._process_input(full_input)

            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                continue

        return 0

    def _needs_continuation(self, source: str) -> bool:
        """Check if input needs continuation lines."""
        stripped = source.rstrip()

        # Ends with colon (def, class, if, for, while, etc.)
        if stripped.endswith(":"):
            return True

        # Check for unclosed brackets/parens
        opens = source.count("(") + source.count("[") + source.count("{")
        closes = source.count(")") + source.count("]") + source.count("}")
        if opens > closes:
            return True

        # Check for incomplete block (has def/class/if/etc but only header)
        try:
            ast.parse(source)
            return False
        except SyntaxError:
            return True

    def _get_indent_for_continuation(self, source: str) -> str:
        """Calculate indentation for the next continuation line."""
        lines = source.split("\n")
        last_line = lines[-1]

        # Get current indentation of last line
        current_indent = len(last_line) - len(last_line.lstrip())

        # If last line ends with ':', increase indent
        if last_line.rstrip().endswith(":"):
            return " " * (current_indent + 4)

        # Otherwise, maintain the same indentation as the last non-empty line
        for line in reversed(lines):
            if line.strip():
                return " " * (len(line) - len(line.lstrip()))

        return ""

    def _is_bare_expression(self, source: str) -> bool:
        """Check if input is a single bare expression (not a print call)."""
        try:
            tree = ast.parse(source)
            if len(tree.body) != 1:
                return False
            stmt = tree.body[0]
            if not isinstance(stmt, ast.Expr):
                return False
            # print() calls are already printing, don't wrap
            if isinstance(stmt.value, ast.Call):
                if isinstance(stmt.value.func, ast.Name):
                    if stmt.value.func.id == "print":
                        return False
            return True
        except SyntaxError:
            return False

    def _should_accumulate(self, source: str) -> bool:
        """Check if input should be accumulated for future compilations.

        Accumulates everything except print() calls, which are output
        side-effects that shouldn't be replayed.
        """
        try:
            tree = ast.parse(source)
            if not tree.body:
                return False

            # Don't accumulate if ANY statement is a print() call
            for stmt in tree.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    func = stmt.value.func
                    if isinstance(func, ast.Name) and func.id == "print":
                        return False

            return True
        except SyntaxError:
            return False

    def _process_input(self, source: str) -> None:
        """Process a single input and optionally accumulate if successful."""
        # Check if this might be an expression that should auto-print
        # (actual type checking happens in _try_compile_and_run)
        maybe_auto_print = self._is_bare_expression(source)

        success, output = self._try_compile_and_run(source, maybe_auto_print)

        if success:
            # Only accumulate definitions/assignments, not side-effect statements
            if self._should_accumulate(source):
                self.accumulated_lines.append(source)
            if output:
                print(output, end="")
        else:
            # Show error, don't accumulate
            print(output, end="", file=sys.stderr)

    def _try_compile_and_run(
        self, new_source: str, maybe_auto_print: bool = False
    ) -> tuple[bool, str]:
        """Attempt to compile accumulated + new source, run it.

        Returns (success, output) where output is either the program output
        or the error message.
        """
        t_start = time.monotonic()

        all_lines = self.accumulated_lines + [new_source]
        combined = "\n".join(all_lines)

        # Ensure main() is always generated
        # (typed variable declarations become globals, not main() body)
        combined += "\n0  # repl-noop"

        # -v: show accumulated tpy source being compiled
        source_output = ""
        if self.verbose >= 1:
            for line in all_lines:
                for sub in line.split("\n"):
                    source_output += f"  [tpy] {sub}\n"

        # Compile via Compiler.from_source (supports multi-module imports)
        try:
            compiler = Compiler.from_source(
                combined, self._module_name, lib_dirs=self.lib_dirs
            )
            compiled_modules = compiler.compile()
        except ParseError as e:
            return False, source_output + f"Parse error: {e}\n"
        except SyntaxError as e:
            return False, source_output + f"Syntax error: {e.msg} at line {e.lineno}\n"
        except SemanticError as e:
            return False, source_output + f"{e.format('repl')}\n"
        except Exception as e:
            return False, source_output + f"Compile error: {e}\n"

        t_compile = time.monotonic() - t_start

        # Find the entry module
        entry_module = next(m for m in compiled_modules if m.is_entry_point)
        module = entry_module.ast
        analyzer = entry_module.analyzer

        # Check if we should auto-print the expression
        # The second-to-last statement is the user's input (last is the noop)
        is_str_or_char = False
        if maybe_auto_print and len(module.top_level_stmts) >= 2:
            user_stmt = module.top_level_stmts[-2]
            if isinstance(user_stmt, TpyExprStmt):
                expr_type = analyzer.get_expr_type(user_stmt.expr)
                # Only wrap if the expression has a non-void type
                if expr_type is not None and not isinstance(expr_type, VoidType):
                    # Track if we're printing a string/char for post-processing
                    is_str_or_char = is_any_str_type(expr_type) or isinstance(expr_type, CharType)

                    # Re-compile with print wrapper
                    wrapped_source = f"print({new_source.strip()})"
                    combined = "\n".join(self.accumulated_lines + [wrapped_source])
                    combined += "\n0  # repl-noop"
                    try:
                        compiler = Compiler.from_source(
                            combined, self._module_name, lib_dirs=self.lib_dirs
                        )
                        compiled_modules = compiler.compile()
                        entry_module = next(m for m in compiled_modules if m.is_entry_point)
                        module = entry_module.ast
                        analyzer = entry_module.analyzer
                    except (ParseError, SyntaxError, SemanticError):
                        is_str_or_char = False  # Fall back to original if wrapping fails

        # Show warnings if any
        warning_output = ""
        for diag in analyzer.diagnostics:
            if diag.level == DiagnosticLevel.WARNING:
                warning_output += f"{diag.format('repl')}\n"

        # Generate code for all modules
        t_codegen_start = time.monotonic()
        all_hpp_paths: list[Path] = []
        all_cpp_paths: list[Path] = []
        all_cpp_code: list[str] = []
        all_hpp_code: list[str] = []
        for mod in compiled_modules:
            hpp_code, cpp_code = compiler.generate_code_to_strings(mod)

            # Write dependency headers preserving directory structure for #include
            mod_parts = mod.name.split('.')
            if len(mod_parts) > 1:
                hpp_subdir = self.temp_dir / Path(*mod_parts[:-1])
                hpp_subdir.mkdir(parents=True, exist_ok=True)
                hpp_path = hpp_subdir / f"{mod_parts[-1]}.hpp"
                cpp_path = hpp_subdir / f"{mod_parts[-1]}.cpp"
            else:
                hpp_path = self.temp_dir / f"{mod.name}.hpp"
                cpp_path = self.temp_dir / f"{mod.name}.cpp"

            hpp_path.write_text(hpp_code)
            cpp_path.write_text(cpp_code)
            all_hpp_paths.append(hpp_path)
            all_cpp_paths.append(cpp_path)
            all_hpp_code.append(hpp_code)
            all_cpp_code.append(cpp_code)

        t_codegen = time.monotonic() - t_codegen_start

        # -vv: show diff of generated C++ (entry module only)
        verbose_output = ""
        if self.verbose >= 2:
            entry_cpp_code = all_cpp_code[-1]
            current_lines = []
            lines = [line.strip() for line in entry_cpp_code.split("\n") if line.strip()]
            for i, stripped in enumerate(lines):
                if stripped.startswith("//") or stripped.startswith("#"):
                    continue
                if stripped in ("return 0;", "0;", "int main() {"):
                    continue
                if stripped == "}" and i == len(lines) - 1:
                    continue
                current_lines.append(stripped)

            diff = difflib.unified_diff(self.prev_cpp_lines, current_lines, lineterm="", n=0)
            new_lines = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]

            if new_lines:
                verbose_output = "  [C++] " + "\n  [C++] ".join(new_lines) + "\n"

            self.prev_cpp_lines = current_lines

        # Check if C++ changed -- skip build if identical to previous
        entry_hpp = all_hpp_code[-1]
        entry_cpp = all_cpp_code[-1]
        cpp_changed = (
            entry_hpp != self._prev_entry_hpp
            or entry_cpp != self._prev_entry_cpp
            or not self._binary_path.exists()
        )

        if cpp_changed:
            # Build
            t_build_start = time.monotonic()
            runtime_dir = get_runtime_dir()
            config = self._get_cpp_config()
            compile_cmd = [
                config.compiler, f"-std={config.std}",
                *config.extra_flags,
                "-I", str(runtime_dir / "cpp" / "include"),
                "-I", str(self.temp_dir),
            ]
            if self._pch_path:
                compile_cmd += ["-include", str(self._pch_path)]
            compile_cmd += [
                "-o", str(self._binary_path),
                *[str(p) for p in all_cpp_paths],
                *config.link_flags,
            ]

            result = subprocess.run(compile_cmd, capture_output=True, text=True)
            t_build = time.monotonic() - t_build_start

            if result.returncode != 0:
                return False, f"C++ compilation failed:\n{result.stderr}"

            self._prev_entry_hpp = entry_hpp
            self._prev_entry_cpp = entry_cpp
        else:
            t_build = 0.0

        # Run
        t_run_start = time.monotonic()
        result = subprocess.run([str(self._binary_path)], capture_output=True, text=True)
        t_run = time.monotonic() - t_run_start

        if result.returncode != 0:
            # Runtime panic
            output = result.stdout + result.stderr
            if not output.strip():
                output = f"Runtime error (exit code {result.returncode})\n"
            return False, output

        # For strings/chars, wrap output in quotes like Python's REPL
        program_output = result.stdout
        if is_str_or_char and program_output:
            program_output = "'" + program_output.rstrip("\n") + "'\n"

        # -v: show timing
        timing_output = ""
        if self.verbose >= 1:
            t_total = time.monotonic() - t_start
            build_str = _fmt_ms(t_build) if cpp_changed else "cached"
            timing_output = (
                f"  [{_fmt_ms(t_total)}] compile: {_fmt_ms(t_compile)}  "
                f"build: {build_str}  "
                f"run: {_fmt_ms(t_run)}\n"
            )

        return True, source_output + verbose_output + timing_output + warning_output + program_output

    def cleanup(self) -> None:
        """Remove temp directory on exit."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
