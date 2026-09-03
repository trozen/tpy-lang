"""
TurboPython Interactive REPL

Provides an interactive Python-like experience with:
- Multi-line input support (for function/class definitions)
- Expression auto-printing (bare `5+3` prints the result)
- Error/panic recovery (failed statements don't accumulate)
- C++ compiler selection via --cxx: clang-repl (default), clang++, g++, zig
- Verbose mode: -v for timing, -vv for generated C++
- Trailing backslash continues to next line
"""

from __future__ import annotations
import ast
import atexit
import difflib
import readline  # For history support
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .parse import ParseError, TpyExprStmt, TpyCoerce
from .sema import SemanticError, DiagnosticLevel
from .typesys import VoidType, is_any_str_type
from .type_def_registry import is_char_type
from . import get_runtime_dir
from .build.third_party import ThirdPartyBuildPlan, resolve_build_plan
from .compiler import Compiler, discover_runtime_cpp_sources
from .compilation_context import activate_compiler
from .codegen_cpp.context import get_include_path
from .repl_backends import (
    REPLBackend, BackendResult, ReplBuildDeps, detect_backend, _fmt_ms,
)


class REPLSession:
    """Interactive TurboPython REPL session."""

    def __init__(self, verbose: int = 0, preload_files: list[Path] | None = None,
                 lib_dirs: list[Path] | None = None,
                 cxx: str = "auto"):
        self.accumulated_lines: list[str] = []
        self.verbose = verbose
        self.preload_files = preload_files or []
        self.lib_dirs = lib_dirs
        self.temp_dir = Path(tempfile.mkdtemp(prefix="tpyc_repl_"))
        self.prev_cpp_lines: list[str] = []  # For verbose diff
        self._module_name = "repl"
        self._cxx = cxx
        self._backend: REPLBackend | None = None
        # Session-constant runtime impl sources + per-dep-set build plans,
        # resolved lazily for _collect_build_deps.
        self._runtime_sources: list[Path] | None = None
        self._plan_cache: dict[tuple[str, ...], ThirdPartyBuildPlan] = {}
        atexit.register(self.cleanup)

    def _collect_build_deps(self, compiler: Compiler) -> ReplBuildDeps:
        """Link-time deps of this eval's compiled module set -- the same
        three inputs the file-compile path assembles (runtime impl sources,
        the managed third-party build plan, collected link flags), so REPL
        link behavior cannot drift from `tpy file.py`. Third-party libs
        always resolve in bundled mode here (no --pcre2/--date etc. in the
        REPL)."""
        runtime_cpp = get_runtime_dir() / "cpp"
        if self._runtime_sources is None:
            self._runtime_sources = discover_runtime_cpp_sources(runtime_cpp)
        dep_names = tuple(compiler.collect_third_party_deps())
        plan = self._plan_cache.get(dep_names)
        if plan is None:
            plan = resolve_build_plan(list(dep_names), runtime_cpp, modes={})
            self._plan_cache[dep_names] = plan
        return ReplBuildDeps(
            runtime_sources=self._runtime_sources,
            third_party_sources=plan.c_sources,
            include_dirs=plan.extra_include_dirs,
            link_flags=[*plan.extra_link_flags,
                        *compiler.collect_link_flags()],
        )

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
        # Detect and start backend
        self._backend = detect_backend(
            self._cxx, self.temp_dir, self._module_name, verbose=self.verbose,
        )
        from . import __version__
        print(f"TurboPython REPL v{__version__} (backend: {self._backend.name})")
        print("Variables, functions, and classes are remembered between inputs.")
        print("Empty line unindents (or ends block at col 0). Trailing \\ continues input. Ctrl+D to exit.")
        print("Type .paste (or .p) for multiline input mode.")

        self._backend.startup()
        if self.verbose >= 1:
            print(f"Build dir: {self.temp_dir}/")
            if hasattr(self._backend, 'pch_cache_dir'):
                print(f"PCH cache: {self._backend.pch_cache_dir}/")

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
                    cont_stripped = ""
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
                            # else/elif/except/finally belong at the outer
                            # block level (no extra indent)
                            cont_stripped = cont_line.strip()
                            if cont_stripped.split()[0].rstrip(":") in (
                                "else", "elif", "except", "finally",
                            ):
                                full_input += "\n" + cont_line.strip()
                            else:
                                full_input += "\n" + indent + cont_line
                        except EOFError:
                            break
                        # For blocks (def/class/if/etc), require empty line to end
                        # For other continuations (unclosed parens), end when complete
                        if not in_block and not self._needs_continuation(full_input):
                            break
                        # Adjust indent based on what user typed
                        if cont_stripped.endswith(":"):
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

        Accumulates definitions/assignments. Skips inputs containing any
        expression-statement call (`print(x)`, `sys.stderr.write(...)`,
        `f.write(...)`, etc.) -- their side effects shouldn't be replayed.
        """
        try:
            tree = ast.parse(source)
            if not tree.body:
                return False

            # A lone expression statement binds nothing and must not be
            # replayed into later compiles -- a bare brace-init literal
            # (`{..};`) is invalid C++ as a statement and poisons the session.
            # A call still must not replay its side effect; otherwise keep it
            # only when it binds via walrus.
            if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
                value = tree.body[0].value
                if isinstance(value, ast.Call):
                    return False
                return any(isinstance(n, ast.NamedExpr)
                           for n in ast.walk(value))

            for stmt in tree.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
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
            compiler.allow_top_level_error_unwrap = True
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
                    is_str_or_char = is_any_str_type(expr_type) or is_char_type(expr_type)

                    # For @error_return calls, unwrap into a temp first
                    # so the error check happens at statement level
                    expr = user_stmt.expr
                    while isinstance(expr, TpyCoerce):
                        expr = expr.expr
                    fi = getattr(expr, 'resolved_function_info', None)
                    is_error_return = fi is not None and fi.error_return_type is not None
                    if is_error_return:
                        wrapped_source = f"__repl_val = {new_source.strip()}\nprint(__repl_val)"
                    else:
                        wrapped_source = f"print({new_source.strip()})"
                    combined = "\n".join(self.accumulated_lines + [wrapped_source])
                    combined += "\n0  # repl-noop"
                    try:
                        compiler = Compiler.from_source(
                            combined, self._module_name, lib_dirs=self.lib_dirs
                        )
                        compiler.allow_top_level_error_unwrap = True
                        compiled_modules = compiler.compile()
                        entry_module = next(m for m in compiled_modules if m.is_entry_point)
                        module = entry_module.ast
                        analyzer = entry_module.analyzer
                    except (ParseError, SyntaxError, SemanticError):
                        is_str_or_char = False  # Fall back to original if wrapping fails

        # Show diagnostics and check for errors
        diag_output = ""
        has_errors = False
        for diag in analyzer.diagnostics:
            if diag.level in (DiagnosticLevel.WARNING, DiagnosticLevel.ERROR):
                diag_output += f"{diag.format('repl')}\n"
            if diag.level == DiagnosticLevel.ERROR:
                has_errors = True
        if has_errors:
            return diag_output.rstrip()

        # Generate code for all modules
        t_codegen_start = time.monotonic()
        all_hpp_paths: list[Path] = []
        all_cpp_paths: list[Path] = []
        all_cpp_code: list[str] = []
        all_hpp_code: list[str] = []
        # get_include_path resolves against the active compiler, which codegen
        # also used to emit each module's #include -- both must read the same
        # map or the written header lands where the #include can't find it.
        with activate_compiler(compiler):
            for mod in compiled_modules:
                hpp_code, cpp_code = compiler.generate_code_to_strings(mod)

                # Write files matching the include path the codegen emits
                include_path = get_include_path(mod.name)
                if include_path is not None:
                    # e.g. "tpystd/tpy.hpp" -> write to {temp}/tpystd/tpy.{hpp,cpp}
                    rel = Path(include_path)
                    hpp_path = self.temp_dir / rel
                    cpp_path = self.temp_dir / rel.with_suffix('.cpp')
                    hpp_path.parent.mkdir(parents=True, exist_ok=True)
                else:
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
                if stripped in ("return 0;", "0;", "(void)(0);", "int main() {"):
                    continue
                if stripped == "}" and i == len(lines) - 1:
                    continue
                current_lines.append(stripped)

            diff = difflib.unified_diff(self.prev_cpp_lines, current_lines, lineterm="", n=0)
            new_lines = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]

            if new_lines:
                verbose_output = "  [C++] " + "\n  [C++] ".join(new_lines) + "\n"

            self.prev_cpp_lines = current_lines

        # Execute via backend
        deps = self._collect_build_deps(compiler)
        result = self._backend.execute(all_hpp_code, all_cpp_code,
                                       all_hpp_paths, all_cpp_paths, deps)

        if not result.success:
            error_msg = result.stderr or "Unknown error\n"
            return False, source_output + error_msg

        # Surface anything the program wrote to stderr (sys.stderr.write, ...)
        # to the REPL host's stderr; Python's interactive interpreter does the
        # same. Compiler / runtime errors take the not-success path above.
        if result.stderr:
            sys.stderr.write(result.stderr)
            sys.stderr.flush()

        # For strings/chars, wrap output in quotes like Python's REPL
        program_output = result.stdout
        if is_str_or_char and program_output:
            program_output = "'" + program_output.rstrip("\n") + "'\n"

        # -v: show timing
        timing_output = ""
        if self.verbose >= 1:
            t_total = time.monotonic() - t_start
            build_str = _fmt_ms(result.t_build) if not result.build_cached else "cached"
            timing_output = (
                f"  [{_fmt_ms(t_total)}] compile: {_fmt_ms(t_compile)}  "
                f"build: {build_str}  "
                f"run: {_fmt_ms(result.t_run)}\n"
            )

        return True, source_output + verbose_output + timing_output + diag_output + program_output

    def cleanup(self) -> None:
        """Remove temp directory and stop backend on exit."""
        if self._backend:
            self._backend.cleanup()
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
