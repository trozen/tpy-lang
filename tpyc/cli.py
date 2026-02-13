"""
TurboPython Compiler CLI

Usage:
    tpyc input.tp.py              # Compile to C++ in __tpyc__/
    tpyc input.tp.py -o out/      # Compile to C++ in out/
    tpyc input.tp.py --build      # Compile to C++ and build binary
    tpyc input.tp.py --exec       # Compile, build, and run
    tpyc --exec <<EOF             # Read from stdin, build, and run
    tpyc --dump-code <<EOF        # Print generated C++ to stdout
    tpyc --repl                   # Start interactive REPL
    tpyc --repl file.tp.py        # Load file then start REPL
    tpyc --repl --verbose         # REPL with C++ output shown
"""

from __future__ import annotations
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from .parse import ParseError
from .sema import SemanticError, DiagnosticLevel
from .codegen_cpp import CodeGenOptions, CodeGenError
from .compiler import Compiler, CompileError, BuildLayout, CppCompilerConfig


def get_runtime_dir() -> Path:
    """Get the path to the runtime directory."""
    # Runtime is at package_root/runtime/
    package_root = Path(__file__).parent.parent
    return package_root / "runtime"


def get_module_name(input_path: Path) -> str:
    """Get module name from source file (e.g., hello.tp.py -> hello)."""
    name = input_path.name
    # Strip .tp.py or .py suffix
    if name.endswith(".tp.py"):
        return name[:-6]
    elif name.endswith(".py"):
        return name[:-3]
    return name


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tpyc",
        description="TurboPython Compiler - compiles TurboPython to C++"
    )
    parser.add_argument("input", nargs="?", help="Input TurboPython source file (.tp.py)")
    parser.add_argument("-o", "--output", help="Output directory (default: __tpyc__/ next to source)")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Verbose output (-v for info, -vv for commands)")
    parser.add_argument("-b", "--build", action="store_true", help="Compile C++ to binary after generating")
    parser.add_argument("-x", "--exec", action="store_true", help="Build and run the program")
    parser.add_argument("-O", "--release", action="store_true", help="Build with optimizations (default: debug)")
    parser.add_argument("--emit-source", action="store_true", help="Embed Python source as comments in generated C++")
    parser.add_argument("-i", "--repl", action="store_true", help="Start interactive REPL")
    parser.add_argument("--print-types", action="store_true", help="Print documentation for all builtin types")
    parser.add_argument("--dump-code", action="store_true", help="Print generated C++ to stdout")

    args = parser.parse_args()

    # Handle REPL mode
    if args.repl:
        from .repl import REPLSession
        preload_files = []
        if args.input:
            # Support multiple files separated by the input arg
            preload_files = [Path(args.input).resolve()]
        return REPLSession(verbose=args.verbose, preload_files=preload_files).run()

    # Handle --print-types
    if args.print_types:
        from .dump_types import dump_builtin_types
        dump_builtin_types()
        return 0

    # Auto-detect stdin when no input file given and stdin is piped/heredoc
    if not args.input and not sys.stdin.isatty():
        args.input = "-"

    # Require input file for non-REPL modes
    if not args.input:
        parser.error("the following arguments are required: input")

    # Handle stdin input (specified as "-" or auto-detected)
    reading_from_stdin = args.input == "-"
    temp_dir = None

    if reading_from_stdin:
        source = sys.stdin.read()
        module_name = "main"
        # Use a temp directory for stdin input
        temp_dir = tempfile.mkdtemp(prefix="tpyc_")
        if args.output:
            output_dir = Path(args.output)
        else:
            output_dir = Path(temp_dir)
        input_path = None
    else:
        input_path = Path(args.input).resolve()

        if not input_path.exists():
            print(f"Error: Input file not found: {input_path}", file=sys.stderr)
            return 1

        # Determine output directory
        if args.output:
            output_dir = Path(args.output)
        else:
            # Default: __tpyc__/ next to source file
            output_dir = input_path.parent / "__tpyc__"

        # Get module name for output paths
        module_name = get_module_name(input_path)

    if args.dump_code and (args.build or args.exec):
        parser.error("--dump-code cannot be combined with --build or --exec")

    try:
        options = CodeGenOptions(emit_source_comments=args.emit_source)
        all_cpp_paths = []

        # Create compiler (unified for both stdin and file input)
        if reading_from_stdin:
            compiler = Compiler.from_source(source, module_name)
        else:
            compiler = Compiler(input_path)

        compiled_modules = compiler.compile()

        for compiled in compiled_modules:
            source_name = "<stdin>" if reading_from_stdin else str(compiled.path)
            if args.verbose:
                print(f"Compiling {source_name}...")
                print(f"  Parsed {len(compiled.ast.records)} records, {len(compiled.ast.functions)} functions")

            if compiled.analyzer:
                for diag in compiled.analyzer.diagnostics:
                    if diag.level == DiagnosticLevel.WARNING:
                        print(diag.format(source_name), file=sys.stderr)

            if args.verbose:
                print("  Semantic analysis passed")

            if args.dump_code:
                hpp_code, cpp_code = compiler.generate_code_to_strings(compiled, options=options)
                print(f"// === include/{compiled.name}.hpp ===")
                print(hpp_code)
                print(f"// === src/{compiled.name}.cpp ===")
                print(cpp_code)
                continue

            hpp_path, cpp_path = compiler.generate_code(compiled, output_dir, options=options)
            all_cpp_paths.append(cpp_path)

            if args.verbose or not (args.build or args.exec):
                print(f"Generated: {hpp_path}")
                print(f"Generated: {cpp_path}")

        if args.dump_code:
            return 0

        # Build if requested
        if args.build or args.exec:
            runtime_dir = get_runtime_dir()
            build_variant = "release" if args.release else "debug"
            layout = BuildLayout(output_dir, module_name, build_variant=build_variant)
            binary_path = layout.binary_path()

            if args.verbose:
                print(f"Building {binary_path}...")

            opt_flags = ["-O3", "-DNDEBUG"] if args.release else ["-g", "-O0"]
            cpp_config = CppCompilerConfig.from_env()
            compile_cmds = layout.build_cpp_commands(
                runtime_include_dir=runtime_dir / "cpp" / "include",
                cpp_files=all_cpp_paths,
                opt_flags=opt_flags,
                config=cpp_config,
            )

            for cmd in compile_cmds:
                if args.verbose >= 2:
                    print(f"  $ {' '.join(cmd)}")

                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"C++ compilation failed:", file=sys.stderr)
                    print(result.stderr, file=sys.stderr)
                    return 1

            if args.verbose or not args.exec:
                print(f"Built: {binary_path}")

            # Run if requested
            if args.exec:
                if args.verbose:
                    print(f"Running {binary_path}...")
                    print("---")

                result = subprocess.run([str(binary_path)])
                return result.returncode

        return 0

    except CompileError as e:
        print(e.format(), file=sys.stderr)
        return 1
    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 1
    except SemanticError as e:
        error_filename = "<stdin>" if reading_from_stdin else input_path.name
        print(e.format(error_filename), file=sys.stderr)
        return 1
    except CodeGenError as e:
        error_filename = "<stdin>" if reading_from_stdin else input_path.name
        print(e.format(error_filename), file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Internal error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
