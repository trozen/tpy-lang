"""
TurboPython Compiler CLI

Usage:
    tpyc input.py              # Compile to C++ in __tpyc__/
    tpyc input.py -o out/      # Compile to C++ in out/
    tpyc input.py --build      # Compile to C++ and build binary
    tpyc input.py --exec       # Compile, build, and run
    tpyc --exec <<EOF          # Read from stdin, build, and run
    tpyc --dump-code <<EOF     # Print generated C++ to stdout
    tpyc --repl                # Start interactive REPL (auto-detect backend)
    tpyc --repl --backend gcc  # Force gcc backend
    tpyc --repl file.py        # Load file then start REPL
    tpyc --repl -v             # REPL with timing
    tpyc --repl -vv            # REPL with timing + generated C++
"""

from __future__ import annotations
import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .parse import ParseError
from .sema import SemanticError, DiagnosticLevel
from .codegen_cpp import CodeGenOptions, CodeGenError
from .compiler import (
    Compiler, CompileError, BuildLayout, CppCompilerConfig, DEFAULT_INT_CHOICES
)
from . import get_runtime_dir, get_lib_dir


def get_module_name(input_path: Path) -> str:
    """Get module name from source file (e.g., hello.py -> hello)."""
    name = input_path.name
    if name.endswith(".py"):
        return name[:-3]
    return name


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tpyc",
        description="TurboPython Compiler - compiles TurboPython to C++"
    )
    parser.add_argument("input", nargs="?", help="Input TurboPython source file (.py)")
    parser.add_argument("-o", "--output", help="Output directory (default: __tpyc__/ next to source)")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Verbose output (-v commands+timing, -vv +generated C++)")
    parser.add_argument("-b", "--build", action="store_true", help="Compile C++ to binary after generating")
    parser.add_argument("-x", "--exec", action="store_true", help="Build and run the program")
    parser.add_argument("-O", "--release", action="store_true", help="Build with optimizations (default: debug)")
    parser.add_argument("--emit-source", action="store_true", help="Embed Python source as comments in generated C++")
    parser.add_argument("-i", "--repl", action="store_true", help="Start interactive REPL")
    parser.add_argument("--print-types", action="store_true", help="Print documentation for all builtin types")
    parser.add_argument("--dump-code", action="store_true", help="Print generated C++ to stdout")
    parser.add_argument(
        "--default-int",
        choices=DEFAULT_INT_CHOICES,
        default="Int32",
        help="Default type for unannotated integer literals (default: Int32)",
    )
    parser.add_argument(
        "-L", "--lib", action="append", default=None,
        help="Extra library search path (can be repeated)",
    )
    parser.add_argument(
        "--no-stdlib", action="store_true",
        help="Disable standard library (tplib, stdlib modules, tpy protocols)",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "clang-repl", "clang", "gcc"],
        default="auto",
        help="REPL backend (default: auto = clang-repl -> clang -> gcc)",
    )

    args = parser.parse_args()

    # Build library search paths
    lib_dir = get_lib_dir()
    lib_dirs: list[Path] = []
    for extra in (args.lib or []):
        lib_dirs.append(Path(extra).resolve())
    if not args.no_stdlib:
        lib_dirs.append(lib_dir / "tpy")

    # Handle REPL mode
    if args.repl:
        from .repl import REPLSession
        preload_files = []
        if args.input:
            # Support multiple files separated by the input arg
            preload_files = [Path(args.input).resolve()]
        return REPLSession(verbose=args.verbose, preload_files=preload_files,
                           lib_dirs=lib_dirs, backend=args.backend).run()

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
        t_compile_start = time.monotonic()
        if reading_from_stdin:
            compiler = Compiler.from_source(source, module_name, default_int=args.default_int,
                                            lib_dirs=lib_dirs)
        else:
            compiler = Compiler(input_path, default_int=args.default_int, lib_dirs=lib_dirs)

        compiled_modules = compiler.compile()
        t_compile = time.monotonic() - t_compile_start

        has_errors = False
        for compiled in compiled_modules:
            source_name = "<stdin>" if reading_from_stdin else os.path.relpath(compiled.path)

            if compiled.analyzer:
                for diag in compiled.analyzer.diagnostics:
                    if diag.level in (DiagnosticLevel.WARNING, DiagnosticLevel.ERROR):
                        print(diag.format(source_name), file=sys.stderr)
                    if diag.level == DiagnosticLevel.ERROR:
                        has_errors = True

        if has_errors:
            return 1

        for compiled in compiled_modules:
            source_name = "<stdin>" if reading_from_stdin else os.path.relpath(compiled.path)

            if args.dump_code:
                try:
                    hpp_code, cpp_code = compiler.generate_code_to_strings(compiled, options=options)
                except CodeGenError as e:
                    if e.filename is None and not compiled.is_entry_point:
                        e.filename = source_name
                    raise
                print(f"// === include/{compiled.name}.hpp ===")
                print(hpp_code)
                if cpp_code:
                    print(f"// === src/{compiled.name}.cpp ===")
                    print(cpp_code)
                continue

            # -vv: show generated C++ inline
            if args.verbose >= 2:
                try:
                    hpp_code, cpp_code = compiler.generate_code_to_strings(compiled, options=options)
                except CodeGenError as e:
                    if e.filename is None and not compiled.is_entry_point:
                        e.filename = source_name
                    raise
                print(f"// === include/{compiled.name}.hpp ===")
                print(hpp_code)
                if cpp_code:
                    print(f"// === src/{compiled.name}.cpp ===")
                    print(cpp_code)

            try:
                hpp_path, cpp_path = compiler.generate_code(compiled, output_dir, options=options)
            except CodeGenError as e:
                if e.filename is None and not compiled.is_entry_point:
                    e.filename = source_name
                raise
            if cpp_path is not None:
                all_cpp_paths.append(cpp_path)

            if not (args.build or args.exec):
                print(f"Generated: {hpp_path}")
                if cpp_path is not None:
                    print(f"Generated: {cpp_path}")

        if args.dump_code:
            return 0

        # Build if requested
        if args.build or args.exec:
            runtime_dir = get_runtime_dir()
            build_variant = "release" if args.release else "debug"
            layout = BuildLayout(output_dir, module_name, build_variant=build_variant)
            binary_path = layout.binary_path()

            opt_flags = ["-O3", "-DNDEBUG"] if args.release else ["-g", "-O0"]
            cpp_config = CppCompilerConfig.from_env()
            cpp_config.link_flags = compiler.collect_link_flags()
            compile_cmds = layout.build_cpp_commands(
                runtime_include_dir=runtime_dir / "cpp" / "include",
                cpp_files=all_cpp_paths,
                opt_flags=opt_flags,
                config=cpp_config,
            )

            t_build_start = time.monotonic()
            for cmd in compile_cmds:
                if args.verbose >= 1:
                    print(f"  $ {' '.join(cmd)}")

                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"C++ compilation failed:", file=sys.stderr)
                    print(result.stderr, file=sys.stderr)
                    return 1
            t_build = time.monotonic() - t_build_start

            if not args.exec:
                print(f"Built: {binary_path}")

            if args.verbose >= 1:
                print(f"  compile: {t_compile*1000:.0f}ms  build: {t_build*1000:.0f}ms")

            # Run if requested
            if args.exec:
                if args.verbose:
                    print("---")
                    sys.stdout.flush()

                t_run_start = time.monotonic()
                result = subprocess.run([str(binary_path)])
                t_run = time.monotonic() - t_run_start

                if args.verbose >= 1:
                    print(f"  run: {t_run*1000:.0f}ms  total: {(t_compile+t_build+t_run)*1000:.0f}ms")

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
