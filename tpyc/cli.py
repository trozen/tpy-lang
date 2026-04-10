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
    tpyc --repl --cxx gcc      # Force gcc backend
    tpyc --repl file.py        # Load file then start REPL
    tpyc --repl -v             # REPL with timing
    tpyc --repl -vv            # REPL with timing + generated C++
"""

from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    Compiler, CompileError, CompilerNotFoundError, BuildLayout, CppCompilerConfig,
    DEFAULT_INT_CHOICES, list_compilers, get_or_build_pch,
)
from . import __version__, get_git_commit, get_runtime_dir, get_lib_dir


def _fmt_ms(seconds: float) -> str:
    """Format seconds as a human-readable duration."""
    ms = seconds * 1000
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _print_info() -> None:
    """Print compiler version, paths, and environment info."""
    commit = get_git_commit()
    print(f"tpyc {__version__} ({commit})")
    print()

    # Paths
    pkg_dir = Path(__file__).parent
    lib_dir = get_lib_dir()
    runtime_dir = get_runtime_dir()
    print(f"compiler:  {pkg_dir}")
    print(f"lib:       {lib_dir / 'tpy'}")
    print(f"runtime:   {runtime_dir}")
    print()

    # C++ compiler
    try:
        config = CppCompilerConfig.from_env(cxx="auto")
        cxx_desc = config.compiler_name
        if config.ccache:
            cxx_desc += " + ccache"
        print(f"cxx:       {cxx_desc} ({' '.join(config.compiler)})")
    except CompilerNotFoundError:
        print("cxx:       not found")
    print()

    # Python
    print(f"python:    {sys.version.split()[0]} ({sys.executable})")


class ProgressPrinter:
    """Prints build progress lines to stderr."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _write(self, msg: str) -> None:
        if not self.enabled:
            return
        sys.stderr.write(msg)
        sys.stderr.flush()

    @staticmethod
    def _module_path(name: str) -> str:
        """Convert dot-separated module name to path format."""
        return name.replace('.', '/')

    def header(self, config: CppCompilerConfig | None = None,
               release: bool = False, n_jobs: int = 1) -> None:
        if not self.enabled:
            return
        if config is not None:
            variant = "release" if release else "debug"
            cxx = config.compiler_name
            if config.ccache:
                cxx += " + ccache"
            job_s = "job" if n_jobs == 1 else "jobs"
            sys.stderr.write(f"TurboPython compiler v{__version__} ({cxx}, {variant}, {n_jobs} {job_s})\n")
        else:
            sys.stderr.write(f"TurboPython compiler v{__version__}\n")
        sys.stderr.flush()

    def analyzed(self, user_modules: list[str], n_stdlib: int,
                 n_warnings: int, elapsed: float) -> None:
        n_total = len(user_modules) + n_stdlib
        self._write(f"  analyzed {n_total} modules ({_fmt_ms(elapsed)})\n")
        for i, name in enumerate(user_modules):
            is_last = i == len(user_modules) - 1
            suffix = f" (+ {n_stdlib} stdlib)\n" if is_last and n_stdlib else "\n"
            self._write(f"    {self._module_path(name)}.py{suffix}")
        if n_warnings:
            w = "warning" if n_warnings == 1 else "warnings"
            self._write(f"    {n_warnings} {w}\n")

    def pch(self, elapsed: float) -> None:
        if elapsed >= 0.1:
            self._write(f"  precompiled tpy.hpp ({_fmt_ms(elapsed)})\n")

    def translated(self, name: str, elapsed: float) -> None:
        self._write(f"  translated {self._module_path(name)}.py ({_fmt_ms(elapsed)})\n")

    def compiled(self, name: str, elapsed: float) -> None:
        self._write(f"  compiled {name} ({_fmt_ms(elapsed)})\n")

    def linked(self, name: str, elapsed: float) -> None:
        self._write(f"  linked {name} ({_fmt_ms(elapsed)})\n")

    def separator(self) -> None:
        self._write("-- \n")

    def summary(self, n_modules: int,
                t_compile: float, t_codegen: float, t_build: float) -> None:
        if not self.enabled:
            return
        total = t_compile + t_codegen + t_build
        sys.stderr.write(
            f"{n_modules} modules compiled in {_fmt_ms(total)}"
            f" (py {_fmt_ms(t_compile)}, codegen {_fmt_ms(t_codegen)},"
            f" build {_fmt_ms(t_build)})\n"
        )
        sys.stderr.flush()


def _timed_run(cmd: list[str]) -> tuple[subprocess.CompletedProcess[str], float]:
    """Run a command and return (result, elapsed_seconds)."""
    t = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r, time.monotonic() - t


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
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__} ({get_git_commit()})")
    parser.add_argument("input", nargs="?", help="Input TurboPython source file (.py)")
    parser.add_argument("-c", dest="cmd", metavar="CMD", help="Execute CMD as a TurboPython program string")
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
        "--cxx", default="auto",
        help="C++ compiler: auto, list, gcc, gcc-14, clang, clang-18, zig, ... (default: auto)",
    )
    ccache_group = parser.add_mutually_exclusive_group()
    ccache_group.add_argument("--ccache", action="store_true", default=None,
                              help="Force ccache usage")
    ccache_group.add_argument("--no-ccache", dest="ccache", action="store_false",
                              help="Disable ccache")
    parser.add_argument("--no-pch", dest="pch", action="store_false", default=True,
                        help="Disable precompiled header caching")
    parser.add_argument("-j", "--jobs", type=int, default=None,
                        help="Parallel compile jobs (default: number of CPUs)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress progress lines (show only errors and program output)")
    parser.add_argument("--info", action="store_true",
                        help="Print compiler version, paths, and environment info")

    args = parser.parse_args()

    # Build library search paths
    lib_dir = get_lib_dir()
    lib_dirs: list[Path] = []
    for extra in (args.lib or []):
        lib_dirs.append(Path(extra).resolve())
    if not args.no_stdlib:
        lib_dirs.append(lib_dir / "tpy")

    # Handle --info
    if args.info:
        _print_info()
        return 0

    # Handle --cxx list
    if args.cxx == "list":
        list_compilers()
        return 0

    # Handle REPL mode
    if args.repl:
        from .repl import REPLSession
        preload_files = []
        if args.input:
            # Support multiple files separated by the input arg
            preload_files = [Path(args.input).resolve()]
        return REPLSession(verbose=args.verbose, preload_files=preload_files,
                           lib_dirs=lib_dirs, cxx=args.cxx).run()

    # Handle --print-types
    if args.print_types:
        from .dump_types import dump_builtin_types
        dump_builtin_types()
        return 0

    # Handle -c: implies -x unless --dump-code or -b is set
    if args.cmd is not None:
        if args.input:
            parser.error("-c cannot be combined with an input file")
        if not args.dump_code and not args.build:
            args.exec = True

    # Auto-detect stdin when no input file given and stdin is piped/heredoc
    if not args.cmd and not args.input and not sys.stdin.isatty():
        args.input = "-"

    # Require input file for non-REPL modes
    if not args.cmd and not args.input:
        parser.error("the following arguments are required: input (or -c CMD)")

    # Handle inline/stdin source
    reading_from_stdin = args.cmd is not None or args.input == "-"
    temp_dir = None

    if reading_from_stdin:
        source = args.cmd if args.cmd is not None else sys.stdin.read()
        module_name = "main"
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
    if args.jobs is not None and args.jobs < 1:
        parser.error("-j/--jobs must be a positive integer")
    building = args.build or args.exec
    quiet = args.dump_code or args.quiet
    explicit_output = bool(args.output)
    n_jobs = args.jobs or os.cpu_count() or 1
    progress = ProgressPrinter(enabled=not quiet)

    try:
        options = CodeGenOptions(emit_source_comments=args.emit_source)
        all_cpp_paths = []

        cpp_config: CppCompilerConfig | None = None
        if building:
            cpp_config = CppCompilerConfig.from_env(cxx=args.cxx)
            if args.ccache is not None:
                cpp_config.ccache = args.ccache
            progress.header(cpp_config, args.release, n_jobs)
        else:
            progress.header()

        # Create compiler (unified for both stdin and file input)
        t_compile_start = time.monotonic()
        if reading_from_stdin:
            compiler = Compiler.from_source(source, module_name, default_int=args.default_int,
                                            lib_dirs=lib_dirs)
        else:
            compiler = Compiler(input_path, default_int=args.default_int, lib_dirs=lib_dirs)

        compiled_modules = compiler.compile()
        t_compile = time.monotonic() - t_compile_start

        n_py = len(compiled_modules)
        user_modules = [m.name for m in compiled_modules if compiler.is_user_module(m)]
        n_stdlib = n_py - len(user_modules)

        # Collect diagnostics: errors abort immediately, warnings are deferred
        has_errors = False
        warning_messages: list[str] = []
        n_warnings = 0
        for diag in compiler.diagnostics:
            n_warnings += 1
            warning_messages.append(diag.format("tpyc"))
        for compiled in compiled_modules:
            source_name = "<stdin>" if reading_from_stdin else os.path.relpath(compiled.path)
            if compiled.analyzer:
                for diag in compiled.analyzer.diagnostics:
                    if diag.level == DiagnosticLevel.ERROR:
                        has_errors = True
                        print(diag.format(source_name), file=sys.stderr)
                    elif diag.level == DiagnosticLevel.WARNING:
                        n_warnings += 1
                        warning_messages.append(diag.format(source_name))

        progress.analyzed(user_modules, n_stdlib, n_warnings, t_compile)

        if has_errors:
            return 1

        # Print warnings immediately when not building (no summary to defer to)
        if not building:
            for msg in warning_messages:
                print(msg, file=sys.stderr)

        t_codegen_start = time.monotonic()
        for i, compiled in enumerate(compiled_modules, 1):
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

            t_file_start = time.monotonic()
            try:
                hpp_path, cpp_path = compiler.generate_code(compiled, output_dir, options=options,
                                                                flat=explicit_output)
            except CodeGenError as e:
                if e.filename is None and not compiled.is_entry_point:
                    e.filename = source_name
                raise
            t_file = time.monotonic() - t_file_start
            if cpp_path is not None:
                all_cpp_paths.append(cpp_path)
                progress.translated(compiled.name, t_file)

        t_codegen = time.monotonic() - t_codegen_start

        if args.dump_code:
            return 0

        n_cpp = len(all_cpp_paths)

        # Generate sources.cmake for CMake integration
        runtime_dir = get_runtime_dir()
        link_flags = compiler.collect_link_flags()
        cmake_layout = BuildLayout(output_dir, module_name, flat=explicit_output)
        cmake_layout.generate_cmake(
            runtime_include_dir=runtime_dir / "cpp" / "include",
            cpp_files=all_cpp_paths,
            link_flags=link_flags,
        )

        # Build if requested
        if building:
            assert cpp_config is not None
            build_variant = "release" if args.release else "debug"
            layout = BuildLayout(output_dir, module_name, build_variant=build_variant,
                                   flat=explicit_output)
            binary_path = layout.binary_path()

            opt_flags = ["-O3", "-DNDEBUG"] if args.release else ["-g", "-O0"]
            cpp_config.link_flags = link_flags

            # Build or reuse precompiled header
            pch_includes: list[Path] = []
            if args.pch:
                t_pch_start = time.monotonic()
                pch_path = get_or_build_pch(
                    cpp_config, runtime_dir / "cpp" / "include", opt_flags,
                    pch_dir=layout.root_dir / "pch",
                )
                t_pch = time.monotonic() - t_pch_start
                if pch_path:
                    pch_includes.append(pch_path)
                    progress.pch(t_pch)
                    # ccache needs these sloppiness flags for PCH support
                    if cpp_config.ccache:
                        slop = os.environ.get("CCACHE_SLOPPINESS", "")
                        parts = {s.strip() for s in slop.split(",") if s.strip()}
                        parts.update(("pch_defines", "time_macros"))
                        os.environ["CCACHE_SLOPPINESS"] = ",".join(sorted(parts))

            compile_cmds = layout.build_cpp_commands(
                runtime_include_dir=runtime_dir / "cpp" / "include",
                cpp_files=all_cpp_paths,
                opt_flags=opt_flags,
                config=cpp_config,
                force_includes=pch_includes or None,
            )

            compile_steps = compile_cmds[:-1]
            link_step = compile_cmds[-1]

            t_build_start = time.monotonic()

            def _cpp_name(cmd: list[str]) -> str:
                """Extract .cpp filename from a compile command."""
                src = cmd[-1]
                return os.path.basename(src)

            # Compile steps in parallel (or serial for single file / -j1)
            if len(compile_steps) <= 1 or n_jobs <= 1:
                for cmd in compile_steps:
                    if args.verbose >= 1:
                        print(f"  $ {' '.join(cmd)}", file=sys.stderr)
                    t_step = time.monotonic()
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        print(f"C++ compilation failed:", file=sys.stderr)
                        print(result.stderr, file=sys.stderr)
                        return 1
                    progress.compiled(_cpp_name(cmd), time.monotonic() - t_step)
            else:
                if args.verbose >= 1:
                    for cmd in compile_steps:
                        print(f"  $ {' '.join(cmd)}", file=sys.stderr)
                failed_stderr = ""
                with ThreadPoolExecutor(max_workers=n_jobs) as pool:
                    futures = {
                        pool.submit(_timed_run, cmd): cmd
                        for cmd in compile_steps
                    }
                    for future in as_completed(futures):
                        r, elapsed = future.result()
                        if r.returncode != 0 and not failed_stderr:
                            failed_stderr = r.stderr
                        else:
                            progress.compiled(_cpp_name(futures[future]), elapsed)
                if failed_stderr:
                    print(f"C++ compilation failed:", file=sys.stderr)
                    print(failed_stderr, file=sys.stderr)
                    return 1

            # Link step
            if args.verbose >= 1:
                print(f"  $ {' '.join(link_step)}", file=sys.stderr)
            t_link = time.monotonic()
            result = subprocess.run(link_step, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"C++ link failed:", file=sys.stderr)
                print(result.stderr, file=sys.stderr)
                return 1
            progress.linked(module_name, time.monotonic() - t_link)
            t_build = time.monotonic() - t_build_start

            progress.summary(n_py, t_compile, t_codegen, t_build)

            for msg in warning_messages:
                print(msg, file=sys.stderr)

            if not args.exec:
                print(f"Built: {binary_path}")

            # Run if requested
            if args.exec:
                progress.separator()
                t_run_start = time.monotonic()
                result = subprocess.run([str(binary_path)])
                t_run = time.monotonic() - t_run_start

                if args.verbose >= 1:
                    print(f"  run: {t_run*1000:.0f}ms  total: {(t_compile+t_codegen+t_build+t_run)*1000:.0f}ms",
                          file=sys.stderr)

                return result.returncode

        return 0

    except CompilerNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
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
