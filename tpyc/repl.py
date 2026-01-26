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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexers import PythonLexer

from .parse import Parser, ParseError
from .sema import SemanticAnalyzer, SemanticError
from .codegen_cpp import CodeGenerator


def get_runtime_dir() -> Path:
    """Get the path to the runtime directory."""
    package_root = Path(__file__).parent.parent
    return package_root / "runtime"


class REPLSession:
    """Interactive TurboPython REPL session."""

    def __init__(self, verbose: bool = False):
        self.accumulated_lines: list[str] = []
        self.verbose = verbose
        self.temp_dir = Path(tempfile.mkdtemp(prefix="tpyc_repl_"))
        self.counter = 0  # For unique file names
        atexit.register(self.cleanup)

    def run(self) -> int:
        """Main REPL loop. Returns exit code."""
        print("TurboPython REPL v0.1 (Ctrl+D to exit, Alt+Enter for newline)")

        # Key bindings for multi-line input with auto-indent
        bindings = KeyBindings()

        @bindings.add("enter")
        def _(event):
            """Submit on Enter."""
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")  # Alt+Enter
        def _(event):
            """Insert newline with auto-indent on Alt+Enter."""
            buf = event.current_buffer
            text = buf.text
            cursor_pos = buf.cursor_position

            # Get text up to cursor
            text_before_cursor = text[:cursor_pos]
            lines = text_before_cursor.split("\n")
            current_line = lines[-1] if lines else ""

            # Calculate indent
            current_indent = len(current_line) - len(current_line.lstrip())
            if current_line.rstrip().endswith(":"):
                indent = " " * (current_indent + 4)
            else:
                indent = " " * current_indent

            buf.insert_text("\n" + indent)

        history_path = Path.home() / ".tpyc_history"
        session: PromptSession[str] = PromptSession(
            lexer=PygmentsLexer(PythonLexer),
            history=FileHistory(str(history_path)),
            key_bindings=bindings,
            multiline=True,
        )

        while True:
            try:
                text = session.prompt(">>> ", prompt_continuation="... ")

                # Handle special commands
                stripped = text.strip()
                if stripped in ("exit", "quit"):
                    break
                if not stripped:
                    continue

                self._process_input(text)

            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                continue

        return 0

    def _is_expression(self, source: str) -> bool:
        """Check if input is a bare expression that should auto-print."""
        try:
            tree = ast.parse(source)
            if len(tree.body) != 1:
                return False
            stmt = tree.body[0]
            if not isinstance(stmt, ast.Expr):
                return False
            # Don't auto-print function calls that are likely side-effecting
            if isinstance(stmt.value, ast.Call):
                if isinstance(stmt.value.func, ast.Name):
                    # print() and similar shouldn't be wrapped
                    if stmt.value.func.id in ("print",):
                        return False
            return True
        except SyntaxError:
            return False

    def _wrap_expression(self, source: str) -> str:
        """Wrap a bare expression in print()."""
        return f"print({source.strip()})"

    def _process_input(self, source: str) -> None:
        """Process a single input and optionally accumulate if successful."""
        # Check if this is an expression that should auto-print
        original_source = source
        if self._is_expression(source):
            source = self._wrap_expression(source)

        success, output = self._try_compile_and_run(source)

        if success:
            # Accumulate the original source (not the wrapped version)
            self.accumulated_lines.append(original_source)
            if output:
                print(output, end="")
        else:
            # Show error, don't accumulate
            print(output, end="", file=sys.stderr)

    def _try_compile_and_run(self, new_source: str) -> tuple[bool, str]:
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

        # Verbose mode: show generated C++
        verbose_output = ""
        if self.verbose:
            lines = cpp_code.strip().split("\n")
            output_lines = []

            # Find global declarations (between #include and first function/main)
            in_globals = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("#include"):
                    in_globals = True
                    continue
                if in_globals:
                    if stripped.startswith("int main()") or (stripped and not stripped.startswith("//")):
                        # Check if it's a global var declaration (not a function)
                        if "=" in stripped and not stripped.startswith("int main"):
                            output_lines.append(stripped)
                        elif stripped.startswith("int main"):
                            break

            # Find main() body content
            in_main = False
            brace_depth = 0
            for line in lines:
                if "int main()" in line:
                    in_main = True
                    brace_depth = 1  # Opening brace of main
                    continue
                if in_main:
                    stripped = line.strip()

                    # Track brace depth
                    brace_depth += stripped.count("{") - stripped.count("}")

                    # Stop at end of main (depth returns to 0)
                    if brace_depth <= 0:
                        break

                    if stripped == "return 0;":
                        continue
                    # Skip noop
                    if stripped == "0;":
                        continue
                    if stripped:
                        output_lines.append(stripped)

            if output_lines:
                verbose_output = "[C++] " + "\n[C++] ".join(output_lines) + "\n"

        # Write to temp files
        hpp_path = self.temp_dir / f"{module_name}.hpp"
        cpp_path = self.temp_dir / f"{module_name}.cpp"
        binary_path = self.temp_dir / module_name

        hpp_path.write_text(hpp_code)
        cpp_path.write_text(cpp_code)

        # Compile
        runtime_dir = get_runtime_dir()
        compile_cmd = [
            "g++", "-std=c++20",
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
