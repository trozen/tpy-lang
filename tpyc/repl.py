"""
TurboPython Interactive REPL

Provides an interactive Python-like experience with:
- Multi-line input support (for function/class definitions)
- Expression auto-printing (bare `5+3` prints the result)
- Error/panic recovery (failed statements don't accumulate)
- Verbose mode showing generated C++
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
from pathlib import Path

from .parse import Parser, ParseError, TpyExprStmt
from .sema import SemanticAnalyzer, SemanticError
from .codegen_cpp import CodeGenerator
from .typesys import VoidType


def get_runtime_dir() -> Path:
    """Get the path to the runtime directory."""
    package_root = Path(__file__).parent.parent
    return package_root / "runtime"


class REPLSession:
    """Interactive TurboPython REPL session."""

    def __init__(self, verbose: bool = False, preload_files: list[Path] | None = None):
        self.accumulated_lines: list[str] = []
        self.verbose = verbose
        self.preload_files = preload_files or []
        self.temp_dir = Path(tempfile.mkdtemp(prefix="tpyc_repl_"))
        self.counter = 0  # For unique file names
        self.prev_cpp_lines: list[str] = []  # For verbose diff
        atexit.register(self.cleanup)

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
                            ast.Import, ast.ImportFrom,
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

    def run(self) -> int:
        """Main REPL loop. Returns exit code."""
        print("TurboPython REPL v0.1")
        print("Variables, functions, and classes are remembered between inputs.")
        print("Empty line ends multi-line input. Ctrl+D to exit.")
        if self.verbose:
            print(f"[src] {self.temp_dir}/")

        # Preload files if specified
        for filepath in self.preload_files:
            print(f"Loading {filepath}...")
            if not self._preload_file(filepath):
                return 1

        # Setup readline history
        history_path = Path.home() / ".tpyc_history"
        try:
            readline.read_history_file(history_path)
        except FileNotFoundError:
            pass
        readline.set_history_length(1000)
        atexit.register(readline.write_history_file, history_path)

        while True:
            try:
                line = input(">>> ")

                # Handle special commands
                stripped = line.strip()
                if stripped in ("exit", "quit"):
                    break
                if not stripped:
                    continue

                # Check for multi-line continuation
                full_input = line
                if self._needs_continuation(full_input):
                    in_block = line.rstrip().endswith(":")
                    indent = self._get_indent_for_continuation(full_input)
                    while True:
                        try:
                            cont_line = input("... " + indent)
                            # Prepend the indent we showed in the prompt
                            full_input += "\n" + indent + cont_line
                        except EOFError:
                            break
                        # Empty line ends multi-line input
                        if not cont_line.strip():
                            break
                        # For blocks (def/class/if/etc), require empty line to end
                        # For other continuations (unclosed parens), end when complete
                        if not in_block and not self._needs_continuation(full_input):
                            break
                        indent = self._get_indent_for_continuation(full_input)

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
        combined = "\n".join(self.accumulated_lines + [new_source])

        # Ensure main() is always generated
        # (typed variable declarations become globals, not main() body)
        combined += "\n0  # repl-noop"

        # Parse
        try:
            parser = Parser()
            module = parser.parse(combined)
        except ParseError as e:
            return False, f"Parse error: {e}\n"
        except SyntaxError as e:
            # Python syntax error (e.g., IndentationError) from ast.parse
            return False, f"Syntax error: {e.msg} at line {e.lineno}\n"

        # Semantic analysis
        try:
            analyzer = SemanticAnalyzer()
            analyzer.analyze(module)
        except SemanticError as e:
            return False, f"{e.format('repl')}\n"

        # Check if we should auto-print the expression
        # The second-to-last statement is the user's input (last is the noop)
        if maybe_auto_print and len(module.top_level_stmts) >= 2:
            user_stmt = module.top_level_stmts[-2]
            if isinstance(user_stmt, TpyExprStmt):
                expr_type = analyzer.get_expr_type(user_stmt.expr)
                # Only wrap if the expression has a non-void type
                if expr_type is not None and not isinstance(expr_type, VoidType):
                    # Re-parse with print wrapper
                    wrapped_source = f"print({new_source.strip()})"
                    combined = "\n".join(self.accumulated_lines + [wrapped_source])
                    combined += "\n0  # repl-noop"
                    try:
                        parser = Parser()
                        module = parser.parse(combined)
                        analyzer = SemanticAnalyzer()
                        analyzer.analyze(module)
                    except (ParseError, SyntaxError, SemanticError):
                        pass  # Fall back to original if wrapping fails

        # Show warnings if any
        warning_output = ""
        for diag in analyzer.diagnostics:
            if diag.level == "warning":
                warning_output += f"{diag.format('repl')}\n"

        # Use unique module name for each compilation
        self.counter += 1
        module_name = f"repl_{self.counter}"

        # Code generation
        codegen = CodeGenerator(analyzer)
        hpp_code, cpp_code = codegen.generate(module, module_name)

        # Verbose mode: show diff of generated C++
        verbose_output = ""
        if self.verbose:
            # Get current lines (stripped, non-empty, skip boilerplate)
            current_lines = []
            lines = [line.strip() for line in cpp_code.split("\n") if line.strip()]
            for i, stripped in enumerate(lines):
                # Skip boilerplate and noop
                if stripped.startswith("//") or stripped.startswith("#"):
                    continue
                if stripped in ("return 0;", "0;", "int main() {"):
                    continue
                # Skip the final closing brace of main()
                if stripped == "}" and i == len(lines) - 1:
                    continue
                current_lines.append(stripped)

            # Use difflib to find added lines (preserves order and duplicates)
            diff = difflib.unified_diff(self.prev_cpp_lines, current_lines, lineterm="", n=0)
            new_lines = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]

            if new_lines:
                verbose_output = "[C++] " + "\n[C++] ".join(new_lines) + "\n"

            # Update previous lines for next diff
            self.prev_cpp_lines = current_lines

        # Write to temp files (do this before adding path to verbose output)
        hpp_path = self.temp_dir / f"{module_name}.hpp"
        cpp_path = self.temp_dir / f"{module_name}.cpp"
        binary_path = self.temp_dir / module_name

        hpp_path.write_text(hpp_code)
        cpp_path.write_text(cpp_code)

        # Compile
        runtime_dir = get_runtime_dir()
        compile_cmd = [
            "g++", "-std=c++23",
            "-I", str(runtime_dir),
            "-I", str(self.temp_dir),  # For generated header
            "-o", str(binary_path),
            str(cpp_path),
            "-lgmp"
        ]

        result = subprocess.run(compile_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False, f"C++ compilation failed:\n{result.stderr}"

        # Run
        result = subprocess.run([str(binary_path)], capture_output=True, text=True)
        if result.returncode != 0:
            # Runtime panic
            output = result.stdout + result.stderr
            if not output.strip():
                output = f"Runtime error (exit code {result.returncode})\n"
            return False, output

        return True, verbose_output + warning_output + result.stdout

    def cleanup(self) -> None:
        """Remove temp directory on exit."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
