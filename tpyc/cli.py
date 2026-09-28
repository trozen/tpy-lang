"""
TurboPython CLI

Two entry points share this module:

  tpy   -- user-facing runner (default: run script, or REPL with no args)
  tpyc  -- compiler (default: emit .hpp/.cpp)

Both accept the same flags; only the default action differs.

Usage (tpy -- runner):
    tpy                        # Start interactive REPL (auto-detect backend)
    tpy input.py               # Run the program
    tpy -c "print(1 + 2)"      # Run an inline snippet
    tpy -b input.py            # Compile and build binary (no run)
    tpy --dump-code input.py   # Print generated C++ to stdout

Usage (tpyc -- compiler):
    tpyc input.py                # Compile to C++ in __tpyc__/
    tpyc input.py -o out/        # Compile to C++ in out/
    tpyc input.py --build        # Compile to C++ and build binary
    tpyc input.py --exec         # Compile, build, and run
    tpyc input.py -x -- a b      # Run with program args (sys.argv[1:] = ["a","b"])
    tpyc --exec <<EOF            # Read from stdin, build, and run
    tpyc --dump-code <<EOF       # Print generated C++ to stdout
    tpyc --repl                  # Start interactive REPL (auto-detect backend)
    tpyc --repl --cxx gcc        # Force gcc backend
    tpyc --repl file.py          # Load file then start REPL
    tpyc --repl -v               # REPL with timing
    tpyc --repl -vv              # REPL with timing + generated C++

For tpyc, program arguments for --exec must be passed after `--` so tpyc's
own options can appear in any position (e.g. `tpyc foo.py -o out/ -x`).
"""

from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .build.third_party import ThirdPartyBuildPlan
    from .compiler import Compiler, CompiledModule

# Only light modules at top level: the build cache's warm path (unchanged
# inputs -> exec the recorded binary) must not pay the compiler machinery's
# ~250ms import cost. Everything heavy (parse/sema/codegen/compiler,
# frontend plugins) is imported inside _run_cli after the warm-path check.
from . import (
    __version__, DEFAULT_INT_CHOICES, get_git_commit, get_runtime_dir,
    get_lib_dir, get_docs_dir,
)
from .toolchain import (
    CompilerNotFoundError, ToolchainUnsupportedError, CppCompilerConfig,
    list_compilers, get_or_build_pch,
)


def _fmt_ms(seconds: float) -> str:
    """Format seconds as a human-readable duration."""
    ms = seconds * 1000
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _run_program(argv: list[str]) -> int:
    """Wait for a native program and return its shell-compatible exit status."""
    prev_sigint = signal.getsignal(signal.SIGINT)
    if prev_sigint != signal.SIG_IGN:
        # Callable handlers reset on exec; SIG_IGN would make the child
        # ignore Ctrl-C too. Preserve an intentionally inherited SIG_IGN.
        signal.signal(signal.SIGINT, lambda signum, frame: None)
    try:
        result = subprocess.run(argv)
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
    return 128 - result.returncode if result.returncode < 0 else result.returncode


def _print_info(prog_name: str) -> None:
    """Print compiler version, paths, and environment info."""
    commit = get_git_commit()
    print(f"{prog_name} {__version__} ({commit})")
    print()

    # Paths
    pkg_dir = Path(__file__).parent
    lib_dir = get_lib_dir()
    runtime_dir = get_runtime_dir()
    docs_dir = get_docs_dir()
    print(f"compiler:  {pkg_dir}")
    print(f"lib:       {lib_dir / 'tpy'}")
    print(f"runtime:   {runtime_dir}")
    print(f"docs:      {docs_dir}")
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
    except ToolchainUnsupportedError:
        print("cxx:       found but unsupported (C++23 required; "
              "see `tpy --cxx list`)")
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
               variant: str = "release", n_jobs: int = 1) -> None:
        if not self.enabled:
            return
        if config is not None:
            cxx = config.compiler_name
            if config.ccache:
                cxx += " + ccache"
            job_s = "job" if n_jobs == 1 else "jobs"
            sys.stderr.write(f"TurboPython v{__version__} ({cxx}, {variant}, {n_jobs} {job_s})\n")
        else:
            sys.stderr.write(f"TurboPython v{__version__}\n")
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


def get_module_name(input_path: Path,
                    extra_extensions: frozenset[str] = frozenset()) -> str:
    """Get module name from source file (e.g., hello.py -> hello).

    `extra_extensions` lets frontend-plugin source files (e.g. `.pas`)
    contribute additional strippable suffixes.
    """
    name = input_path.name
    if name.endswith(".py"):
        return name[:-3]
    for ext in extra_extensions:
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _build_frontend_registry(plugin_specs: list[str],
                             dsl_opts: list[str]) -> FrontendRegistry | None:
    """Load plugins listed on the command line and return a registry.

    Two-phase: resolve each plugin's class (so we learn its `name`
    ClassVar without running `__init__`), route `--dsl-opt` values to
    each plugin's options dict, *then* instantiate with the routed
    dict. Plugins that validate required options in `__init__` see
    the real values on the first call; reassigning `self.options`
    after construction wouldn't trigger that path. Returns None when
    no plugins were requested.
    """
    from .frontend_plugin import (
        FrontendPluginError, FrontendRegistry, resolve_plugin_class,
        route_dsl_opts,
    )
    if not plugin_specs:
        if dsl_opts:
            from .diagnostics import Diagnostic, DiagnosticLevel
            raise FrontendPluginError(Diagnostic(
                level=DiagnosticLevel.ERROR,
                message=("--dsl-opt requires at least one --dsl-plugin"),
            ))
        return None
    plugin_classes = [resolve_plugin_class(spec) for spec in plugin_specs]
    routed = route_dsl_opts(dsl_opts, [cls.name for cls in plugin_classes])
    registry = FrontendRegistry()
    for cls in plugin_classes:
        registry.register(cls(routed.get(cls.name, {})))
    return registry


def _split_tpyc_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split tpyc argv on the first `--` separator.

    Returns (compiler_argv, script_args). Only the first `--` is honored; any
    later `--` tokens are preserved as literal program args.

    Raises ValueError if `--` is the first token (nothing for tpyc to parse).
    """
    if "--" not in argv:
        return argv, []
    i = argv.index("--")
    if i == 0 and argv[1:]:
        raise ValueError("input file must appear before '--'")
    return argv[:i], argv[i + 1:]


class _VersionAction(argparse.Action):
    """`--version` with the git commit resolved only when requested --
    `get_git_commit` shells out to `git describe` in dev checkouts, which
    would otherwise tax every run (the warm path in particular)."""

    def __call__(self, parser, namespace, values, option_string=None):
        print(f"{parser.prog} {__version__} ({get_git_commit()})")
        parser.exit()


def _build_variant(args: argparse.Namespace) -> str:
    """Name of the build flavor: keys the per-variant output dir, the PCH
    dir and the whole-run cache manifest, so the two never clobber."""
    return "debug" if args.debug else "release"


def _opt_flags(args: argparse.Namespace) -> list[str]:
    """C++ optimization flags per variant. The default carries no -DNDEBUG:
    the runtime's NDEBUG-gated checks (dead frame slots, storage lifecycle)
    stay on, since eliding them must be a named opt-in. They are not all free
    at -O3; see the TODO.md generator-frame perf entry."""
    return ["-g", "-O0"] if args.debug else ["-O3"]


# The managed third-party libraries, each with a `--<name>` mode flag. A
# literal rather than the registry's names, so the warm build-cache path does
# not import the build layer; a unit test pins it to the registry.
THIRD_PARTY_LIBS: tuple[str, ...] = ("pcre2", "mbedtls", "date", "zlib")


def _third_party_modes(args: argparse.Namespace) -> dict[str, str]:
    return {name: getattr(args, name) for name in THIRD_PARTY_LIBS}


def _cache_options_key(args: argparse.Namespace, input_path: Path,
                       lib_dirs: list[Path],
                       config: CppCompilerConfig) -> dict:
    """Output-affecting configuration that isn't a tracked input file.

    Must be derivable identically before compilation (warm path) and after
    (record time), so raw args/config only -- no compile results.
    """
    return {
        "tpyc_version": __version__,
        "entry": str(input_path),
        "variant": _build_variant(args),
        "default_int": args.default_int,
        "compiler": list(config.compiler),
        "std": config.std,
        "ccache": config.ccache,
        "extra_flags": list(config.extra_flags),
        "warn_flags": list(config.warn_flags),
        "pch": bool(args.pch),
        "no_main": bool(args.no_main),
        "emit_source": bool(args.emit_source),
        "lib_dirs": [str(d) for d in lib_dirs],
        "third_party": _third_party_modes(args),
    }


def _record_build_manifest(cache_key: dict, build_dir: Path, input_path: Path,
                           compiled_modules: list[CompiledModule],
                           compiler: Compiler, cpp_config: CppCompilerConfig,
                           runtime_dir: Path, runtime_cpp_sources: list[Path],
                           third_party_plan: ThirdPartyBuildPlan,
                           warning_messages: list[str],
                           binary_path: Path) -> None:
    """Write the up-to-date manifest after a successful build.

    Best-effort: failing to record must never fail the build (the next run
    just rebuilds cold).
    """
    from . import build_cache as bc

    # Known blind spot (documented at --rebuild): system-mode third-party
    # libs (-lfoo) resolve at link time outside these inputs, like ccache.
    toolchain = bc.toolchain_entry(cpp_config.compiler[0])
    if toolchain is None:
        return

    files: dict[str, dict] = {}

    def add(path: Path | str) -> bool:
        p = os.path.abspath(str(path))
        if p not in files:
            entry = bc.file_entry(p)
            if entry is None:
                return False
            files[p] = entry
        return True

    macro_reg = compiler.macro_registry
    pkg_dir = Path(__file__).parent
    include_dir = runtime_dir / "cpp" / "include"

    # Source files the compiler consumed use their READ-TIME entries
    # (captured before each read): the manifest must certify the content
    # the binary was built from, not the on-disk state after a
    # multi-second C++ build (a mid-build edit must miss, not warm-hit).
    # Module and macro captures share one table (the macro registry is
    # constructed with the Compiler's dict).
    captured = compiler.input_file_entries
    consumed: list = [input_path]
    consumed += (m.path for m in compiled_modules if m.path is not None)
    consumed += macro_reg.loaded_files.values()
    consumed += macro_reg.probe_rejected
    for p in consumed:
        ap = os.path.abspath(str(p))
        entry = captured.get(ap)
        if entry is None:
            return  # no read-time capture -> don't certify this build
        files.setdefault(ap, entry)

    # Inputs not user-edited mid-build (compiler's own sources, runtime,
    # vendored third-party) are safe to stat/hash post-link.
    inputs: list = []
    inputs += pkg_dir.rglob("*.py")
    inputs += (p for p in include_dir.rglob("*") if p.is_file())
    inputs += runtime_cpp_sources
    inputs += (src for src, _flags in (third_party_plan.c_sources or []))
    for d in (third_party_plan.extra_include_dirs or []):
        inputs += (p for p in Path(d).rglob("*") if p.is_file())
    for p in inputs:
        if not add(p):
            return  # an input vanished mid-build; skip recording

    # Name-set fingerprints catch files *added* to enumerated input sets,
    # which per-file stats can't see.
    listings = [bc.dir_listing_entry(pkg_dir, "*.py"),
                bc.dir_listing_entry(include_dir, "*")]
    runtime_src_dir = runtime_dir / "cpp" / "src"
    if runtime_src_dir.is_dir():
        listings.append(bc.dir_listing_entry(runtime_src_dir, "*"))

    binary_entry = bc.file_entry(os.path.abspath(str(binary_path)))
    if binary_entry is None:
        return
    resolver = compiler.resolver
    manifest = bc.BuildManifest(
        options_key=cache_key,
        toolchain=toolchain,
        files=list(files.values()),
        dir_listings=listings,
        resolver={
            "base_dir": str(resolver.base_dir),
            "extra_dirs": [str(d) for d in resolver.extra_dirs],
            "extra_extensions": list(resolver.extra_extensions),
        },
        resolutions=dict(resolver.resolution_log),
        must_not_exist=list(macro_reg.probe_missing),
        warnings=list(warning_messages),
        binary=binary_entry,
    )
    try:
        bc.write_manifest(build_dir, manifest)
    except OSError:
        pass


def _run_cli(is_runner: bool) -> int:
    prog_name = "tpy" if is_runner else "tpyc"
    parser = argparse.ArgumentParser(
        prog=prog_name,
        description=(
            "TurboPython - run programs or start an interactive REPL"
            if is_runner else
            "TurboPython Compiler - compiles TurboPython to C++"
        ),
    )
    # NOTE: any new option that affects the emitted C++ or the binary must
    # also be added to _cache_options_key, or the build cache will serve
    # stale binaries across flag changes.
    parser.add_argument("--version", action=_VersionAction, nargs=0,
                        help="show program's version number and exit")
    parser.add_argument("input", nargs="?", help="Input TurboPython source file (.py)")
    if is_runner:
        # tpy: REMAINDER captures everything after the input positional, including
        # flags like -O, matching `python script.py -O`. Limitation: flags that
        # also exist as tpy options (e.g. -j) AND appear *before* the input
        # positional are still consumed by tpy -- with `-c CMD`, there is no
        # input positional to separate them. Use `--` to force forwarding:
        # `tpy -c CMD -- -j arg`.
        parser.add_argument("script_args", nargs=argparse.REMAINDER,
                            help="Arguments forwarded to the running program as sys.argv[1:]")
    # tpyc: no REMAINDER positional -- tpyc's own options must be parseable in
    # any position (including after the input file). Program args for --exec
    # are pre-split off from argv on `--` before parse_args runs below.
    parser.add_argument("-c", dest="cmd", metavar="CMD", help="Execute CMD as a TurboPython program string")
    parser.add_argument("-o", "--output", help="Output directory (default: __tpyc__/ next to source)")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Verbose output (-v commands+timing, -vv +generated C++)")
    parser.add_argument("-b", "--build", action="store_true", help="Compile C++ to binary after generating")
    parser.add_argument("-x", "--exec", action="store_true", help="Build and run the program")
    parser.add_argument("--debug", action="store_true",
                        help="Build unoptimized with debug info (-g -O0); the default is -O3")
    parser.add_argument("--emit-source", action="store_true", help="Embed Python source as comments in generated C++")
    parser.add_argument("-i", "--repl", action="store_true", help="Start interactive REPL")
    parser.add_argument("--print-types", action="store_true", help="Print API reference (builtins, tplib, bundled stdlib) as markdown")
    parser.add_argument("--install-agent-docs", metavar="DIR",
                        help="Install TPy agent docs (TPY_FOR_AGENTS.md, TPY_LANGUAGE_FEATURES.md, "
                             "TPY_STDLIB_ROADMAP.md, TPY_API_REFERENCE.md) into DIR and print "
                             "an AGENTS.md snippet to stdout")
    parser.add_argument("--dump-code", action="store_true", help="Print generated C++ to stdout")
    parser.add_argument("--dump-thir", action="store_true",
                        help="Print the lowered THIR for every body, naming the rejected ones and why, and exit (debug)")
    parser.add_argument("--dump-mir", action="store_true",
                        help="Print supported MIR control-flow graphs and reasons for uncovered bodies, and exit (debug)")
    parser.add_argument("--explain-send", metavar="TYPE",
                        help="Print the Send derivation tree for TYPE (e.g. 'list[Order]') and exit")
    parser.add_argument("--explain-sync", metavar="TYPE",
                        help="Print the Sync derivation tree for TYPE and exit")
    parser.add_argument(
        "--default-int",
        choices=DEFAULT_INT_CHOICES,
        default="int32",
        help="Default type for unannotated integer literals (default: int32)",
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
        help="C++ compiler: auto, list, gcc, gcc-14, clang, clang-19, zig, ... (default: auto)",
    )
    ccache_group = parser.add_mutually_exclusive_group()
    ccache_group.add_argument("--ccache", action="store_true", default=None,
                              help="Force ccache usage")
    ccache_group.add_argument("--no-ccache", dest="ccache", action="store_false",
                              help="Disable ccache")
    parser.add_argument("--no-pch", dest="pch", action="store_false", default=True,
                        help="Disable precompiled header caching")
    parser.add_argument("--rebuild", action="store_true",
                        help="Ignore the up-to-date check and rebuild from scratch, "
                             "precompiled header included (escape hatch, e.g. "
                             "after upgrading a system library)")
    parser.add_argument("--no-bundle-runtime", dest="bundle_runtime",
                        action="store_false", default=True,
                        help="Don't copy runtime headers into the output directory")
    parser.add_argument(
        "--pcre2", choices=["bundled", "system", "auto", "none"], default="bundled",
        help="PCRE2 source for the `re` module: bundled (vendored, default), "
             "system (find_package / -lpcre2-8), auto (system, fall back to "
             "bundled), or none (disabled -- any module that imports `re` "
             "becomes a compile error, useful for embedded targets that want "
             "to strip out regex)",
    )
    parser.add_argument(
        "--mbedtls", choices=["bundled", "system", "auto", "none"], default="bundled",
        help="mbedTLS source for the `ssl` module (HTTPS): bundled (vendored, "
             "default), system (find_package / -lmbedtls -lmbedx509 "
             "-lmbedcrypto), auto (system, fall back to bundled), or none "
             "(disabled -- any module that imports `ssl` becomes a compile "
             "error, useful for targets that want to strip out TLS)",
    )
    parser.add_argument(
        "--date", choices=["bundled", "system", "auto", "none"], default="bundled",
        help="Howard Hinnant date source for the `datetime` module's timezone "
             "backend: bundled (vendored, default), system (find_package / "
             "-ldate-tz), auto (system, fall back to bundled), or none "
             "(disabled -- any module that imports `datetime` becomes a "
             "compile error)",
    )
    parser.add_argument(
        "--zlib", choices=["bundled", "system", "auto", "none"], default="bundled",
        help="zlib source for the `zlib` and `gzip` modules: bundled (vendored, "
             "default), system (find_package / -lz), auto (system, fall back "
             "to bundled), or none (disabled -- any module that imports `zlib` "
             "or `gzip` becomes a compile error)",
    )
    parser.add_argument(
        "--dsl-plugin", action="append", default=None, metavar="SPEC",
        help="Load a frontend plugin (path to .py file or importable module). "
             "Can be repeated.",
    )
    parser.add_argument(
        "--dsl-opt", action="append", default=None, metavar="NAME.KEY=VALUE",
        help="Pass an option to a frontend plugin. Can be repeated.",
    )
    parser.add_argument("-j", "--jobs", type=int, default=None,
                        help="Parallel compile jobs (default: number of CPUs)")
    parser.add_argument("--no-main", dest="no_main", action="store_true",
                        help="Skip main() generation (emit __tpy_main instead, for linking with external C++)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress progress lines (show only errors and program output)")
    parser.add_argument("--info", action="store_true",
                        help="Print compiler version, paths, and environment info")

    if is_runner:
        args = parser.parse_args()
    else:
        # tpyc: program args (for --exec) must follow `--`, so tpyc's own
        # options can appear anywhere -- including after the input file.
        try:
            tpyc_argv, script_args = _split_tpyc_argv(sys.argv[1:])
        except ValueError as e:
            parser.error(str(e))
        args = parser.parse_args(tpyc_argv)
        args.script_args = script_args

    # tpy (runner) default action: bare `tpy` -> REPL; `tpy foo.py` -> run.
    # Explicit actions (-b, -x, --dump-code, -i) and informational flags
    # take precedence -- we only set a default when nothing else was requested.
    # Piped stdin counts as input (matches `python < script.py`).
    if is_runner:
        has_input = bool(args.input or args.cmd) or not sys.stdin.isatty()
        has_action = (
            args.build or args.exec or args.dump_code or args.dump_thir or args.dump_mir or args.repl
            or args.info or args.print_types
            or args.install_agent_docs is not None
            or args.explain_send is not None or args.explain_sync is not None
            or args.cxx == "list"
        )
        if not has_action:
            if has_input:
                args.exec = True
            else:
                args.repl = True

    # Build library search paths
    lib_dir = get_lib_dir()
    lib_dirs: list[Path] = []
    for extra in (args.lib or []):
        lib_dirs.append(Path(extra).resolve())
    if not args.no_stdlib:
        lib_dirs.append(lib_dir / "tpy")

    # Handle --info
    if args.info:
        _print_info(prog_name)
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

    # Handle --install-agent-docs (before --print-types so it's not silently dropped)
    if args.install_agent_docs is not None:
        from .install_docs import install_agent_docs, agents_md_snippet
        target = Path(args.install_agent_docs).resolve()
        try:
            written = install_agent_docs(target)
        except (NotADirectoryError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        for p in written:
            print(f"Wrote {p}", file=sys.stderr)
        print(agents_md_snippet(Path(args.install_agent_docs)))
        return 0

    # Handle --print-types
    if args.print_types:
        from .dump_types import dump_builtin_types
        dump_builtin_types()
        return 0

    # Handle -c: implies -x unless a dump action or -b is set.
    # With -c, a positional ends up in args.input due to nargs="?" -- prepend
    # it to forwarded script args (matches `python -c CMD a b`). For tpyc,
    # args.script_args may already contain tokens from a post-`--` separator;
    # the input positional precedes them in sys.argv[1:] order.
    if args.cmd is not None:
        if args.input:
            args.script_args = [args.input, *args.script_args]
            args.input = None
        if not (args.dump_code or args.dump_thir or args.dump_mir or args.build):
            args.exec = True

    # Auto-detect stdin when no input file given and stdin is piped/heredoc
    if not args.cmd and not args.input and not sys.stdin.isatty():
        args.input = "-"

    # Require input file for non-REPL modes
    if not args.cmd and not args.input:
        parser.error("the following arguments are required: input (or -c CMD)")

    # script_args only makes sense when the program is actually run.
    # For tpy (runner), these come from REMAINDER after the input positional.
    # For tpyc, they come either from after a `--` separator or from the -c
    # shuffle above. In compile-only / --build / --dump-code modes, reject
    # them with argparse's native "unrecognized arguments" phrasing.
    if args.script_args and not args.exec:
        parser.error(f"unrecognized arguments: {' '.join(args.script_args)}")

    # Handle inline/stdin source
    reading_from_stdin = args.cmd is not None or args.input == "-"
    temp_dir = None

    # Load frontend plugins, if any, before deriving the module name so
    # that plugin-claimed extensions (e.g. `.pas`) are stripped.
    frontend_registry = None
    if args.dsl_plugin or args.dsl_opt:
        from .frontend_plugin import FrontendPluginError
        try:
            frontend_registry = _build_frontend_registry(
                args.dsl_plugin or [], args.dsl_opt or [])
        except FrontendPluginError as e:
            print(f"error: {e.diagnostic.message}", file=sys.stderr)
            return 1
    plugin_extensions = (frontend_registry.all_extensions()
                         if frontend_registry is not None else frozenset())
    # Plugins may contribute extra library search dirs (e.g. the
    # Pascal frontend ships its stdlib at `pascal/lib/`). Append them
    # before the implicit TPy stdlib so the user doesn't need a `-L`
    # for each plugin-owned directory.
    if frontend_registry is not None:
        plugin_libs: list[Path] = []
        for p in frontend_registry.plugins:
            for d in p.library_paths():
                plugin_libs.append(Path(d).resolve())
        stdlib_pos = len(lib_dirs)
        if not args.no_stdlib:
            # `lib_dirs` ends with the TPy stdlib (`lib/tpy/`) when
            # not --no-stdlib; keep plugin libs ahead of it so they
            # don't shadow stdlib lookups but still come after user
            # `-L` dirs.
            stdlib_pos = len(lib_dirs) - 1
        lib_dirs[stdlib_pos:stdlib_pos] = plugin_libs

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
        module_name = get_module_name(input_path, plugin_extensions)

    if args.dump_code and (args.build or args.exec):
        parser.error("--dump-code cannot be combined with --build or --exec")
    if args.dump_thir and (args.build or args.exec):
        parser.error("--dump-thir cannot be combined with --build or --exec")
    if args.dump_mir and (args.build or args.exec or args.dump_code or args.dump_thir):
        parser.error("--dump-mir cannot be combined with --build, --exec, --dump-code or --dump-thir")
    if args.jobs is not None and args.jobs < 1:
        parser.error("-j/--jobs must be a positive integer")
    explain_type = args.explain_send or args.explain_sync
    building = args.build or args.exec
    quiet = args.dump_code or args.dump_thir or args.dump_mir or args.quiet or explain_type is not None
    explicit_output = bool(args.output)
    n_jobs = args.jobs or os.cpu_count() or 1
    progress = ProgressPrinter(enabled=not quiet)

    # Up-to-date check: a binary-producing run whose recorded inputs are all
    # unchanged skips the whole pipeline (front-end, codegen, C++ build) and
    # executes the recorded binary directly. Caching is off for stdin/-c
    # input (fresh temp dir every run), frontend plugins (plugin-side module
    # resolution isn't replayable without loading the plugin), and -vv
    # (wants the generated C++ printed).
    cache_key: dict | None = None
    key_config: CppCompilerConfig | None = None
    if (building and not reading_from_stdin
            and frontend_registry is None and args.verbose < 2):
        from . import build_cache
        try:
            key_config = CppCompilerConfig.from_env(cxx=args.cxx)
        except (CompilerNotFoundError, ToolchainUnsupportedError):
            key_config = None  # cold path reports the error properly
        if key_config is not None:
            if args.ccache is not None:
                key_config.ccache = args.ccache
            cache_key = _cache_options_key(args, input_path, lib_dirs, key_config)
        # --rebuild skips the check but still falls through to record a
        # fresh manifest, so the *next* plain run can go warm.
        if cache_key is not None and not args.rebuild:
            cache_build_dir = build_cache.compute_build_dir(
                output_dir, module_name,
                _build_variant(args), flat=explicit_output)
            hit = build_cache.check_up_to_date(cache_build_dir, cache_key)
            if hit is not None:
                # Diagnostics stay consistent across warm runs: replay the
                # warnings recorded at build time.
                for msg in hit.warnings:
                    print(msg, file=sys.stderr)
                if args.verbose >= 1:
                    print(f"  cached: {hit.binary} (inputs unchanged)",
                          file=sys.stderr)
                if args.exec:
                    sys.stdout.flush()
                    sys.stderr.flush()
                    try:
                        os.execv(hit.binary, [hit.binary, *args.script_args])
                    except OSError:
                        pass  # binary vanished since the check -> rebuild
                else:
                    label = ("Built extension" if hit.binary.endswith(".so")
                             else "Built")
                    print(f"{label}: {hit.binary}")
                    return 0

    # Heavy imports, deferred past the warm path (see top-of-module note).
    from .parse import ParseError
    from .sema import SemanticError, DiagnosticLevel, format_diagnostics
    from .explain import explain_send_sync
    from .codegen_cpp import (CodeGenOptions, CodeGenError,
                              stamp_codegen_error_file)
    from .compiler import Compiler, CompileError, BuildLayout

    try:
        options = CodeGenOptions(emit_source_comments=args.emit_source,
                                 no_main=args.no_main)
        all_cpp_paths = []

        cpp_config: CppCompilerConfig | None = None
        if building:
            # Reuse the warm-check resolution when it ran: the recorded key
            # and the actual build config stay identical by construction.
            if key_config is not None:
                cpp_config = key_config
            else:
                cpp_config = CppCompilerConfig.from_env(cxx=args.cxx)
                if args.ccache is not None:
                    cpp_config.ccache = args.ccache
            progress.header(cpp_config, _build_variant(args), n_jobs)
        else:
            progress.header()

        # Create compiler (unified for both stdin and file input)
        t_compile_start = time.monotonic()
        if reading_from_stdin:
            compiler = Compiler.from_source(source, module_name, default_int=args.default_int,
                                            lib_dirs=lib_dirs,
                                            frontend_registry=frontend_registry)
        else:
            compiler = Compiler(input_path, default_int=args.default_int, lib_dirs=lib_dirs,
                                frontend_registry=frontend_registry)

        compiled_modules = compiler.compile()
        t_compile = time.monotonic() - t_compile_start

        n_py = len(compiled_modules)
        user_modules = [m.name for m in compiled_modules if compiler.is_user_module(m)]
        n_stdlib = n_py - len(user_modules)

        # Collect diagnostics: errors abort immediately, warnings are
        # deferred. Errors from the compiler driver itself (frontend plugin
        # parse failures, module-resolution failures, etc.) halt the build
        # the same way analyzer-level errors do -- without that a plugin's
        # parse error surfaces as a downstream C++ compile failure, because
        # the empty-module fallback gets fed into the build pipeline.
        has_errors = False
        warning_messages: list[str] = []
        n_warnings = 0
        for diag, line in format_diagnostics(
                compiler, compiled_modules, prog_name=prog_name,
                source_name=lambda m: (
                    "<stdin>" if reading_from_stdin
                    else os.path.relpath(m.path))):
            if diag.level == DiagnosticLevel.ERROR:
                has_errors = True
                print(line, file=sys.stderr)
            else:
                n_warnings += 1
                warning_messages.append(line)

        progress.analyzed(user_modules, n_stdlib, n_warnings, t_compile)

        if has_errors:
            return 1

        # A `# tpy: ext_module` builds a CPython extension `.so`, not an
        # executable: there is no main() to emit or run.
        ext_module_build = compiler.is_ext_module_build()
        if ext_module_build:
            if args.exec:
                print("error: cannot --exec a `# tpy: ext_module` -- it builds "
                      "an importable .so, not a runnable program", file=sys.stderr)
                return 1
            options.no_main = True

        if explain_type is not None:
            return explain_send_sync(
                compiled_modules, compiler, explain_type,
                send=args.explain_send is not None)

        # Print warnings immediately when not building (no summary to defer to)
        if not building:
            for msg in warning_messages:
                print(msg, file=sys.stderr)

        t_codegen_start = time.monotonic()
        if args.dump_mir:
            from .mir.collect import call_definitions, dump_codegen_mir
            from .mir.definitions import MIRDefinitions
            from .mir_workspace import analyze_call_workspace

            collected = []
            for compiled in compiled_modules:
                if not compiler.is_user_module(compiled):
                    continue
                source_name = "<stdin>" if reading_from_stdin else os.path.relpath(compiled.path)
                with stamp_codegen_error_file(source_name, compiled.is_entry_point):
                    ctx = compiler.collect_thir(compiled, options, tolerate_reject=True)
                collected.append((compiled, ctx))
            definitions = MIRDefinitions(tuple(
                ctor for _compiled, ctx in collected for ctor in ctx.thir_constructors.values()))
            workspace = analyze_call_workspace(tuple(
                item for compiled, ctx in collected for item in call_definitions(ctx, compiled.name)), definitions)
            for compiled, ctx in collected:
                assert compiled.analyzer is not None
                print(f"// === mir/{compiled.name} ===")
                print(dump_codegen_mir(compiled.ast, compiled.analyzer, ctx,
                                       compiled.name, definitions,
                                       compiler.thir_reject_by_node, workspace), end="")
            return 0

        for i, compiled in enumerate(compiled_modules, 1):
            source_name = "<stdin>" if reading_from_stdin else os.path.relpath(compiled.path)

            if args.dump_thir:
                # User modules only -- the implicit stdlib would bury the
                # user's functions in noise.
                if not compiler.is_user_module(compiled):
                    continue
                from .thir.dump import dump_codegen_thir
                from .codegen_cpp.context import CodeGenOptions
                assert compiled.analyzer is not None
                print(f"// === thir/{compiled.name} ===")
                # Run codegen and read the bodies it actually lowered:
                # resumable bodies only lower at frame emission (their CFG
                # needs live codegen state), so nothing short of a real
                # codegen pass sees every body kind. The C++ is discarded.
                with stamp_codegen_error_file(source_name,
                                              compiled.is_entry_point):
                    ctx = compiler.collect_thir(compiled, CodeGenOptions(),
                                                tolerate_reject=True)
                print(dump_codegen_thir(compiled.ast, compiled.analyzer, ctx,
                                        compiler.thir_reject_by_node), end="")
                continue

            if args.dump_code:
                with stamp_codegen_error_file(source_name,
                                              compiled.is_entry_point):
                    hpp_code, cpp_code, inl_code = compiler.generate_inl_and_code_to_strings(compiled, options=options)
                if hpp_code:
                    print(f"// === include/{compiled.name}.hpp ===")
                    print(hpp_code)
                if inl_code:
                    print(f"// === include/{compiled.name}_inl.hpp ===")
                    print(inl_code)
                if cpp_code:
                    print(f"// === src/{compiled.name}.cpp ===")
                    print(cpp_code)
                continue

            # -vv: show generated C++ inline
            if args.verbose >= 2:
                with stamp_codegen_error_file(source_name,
                                              compiled.is_entry_point):
                    hpp_code, cpp_code, inl_code = compiler.generate_inl_and_code_to_strings(compiled, options=options)
                print(f"// === include/{compiled.name}.hpp ===")
                print(hpp_code)
                if inl_code:
                    print(f"// === include/{compiled.name}_inl.hpp ===")
                    print(inl_code)
                if cpp_code:
                    print(f"// === src/{compiled.name}.cpp ===")
                    print(cpp_code)

            t_file_start = time.monotonic()
            with stamp_codegen_error_file(source_name,
                                          compiled.is_entry_point):
                hpp_path, cpp_path = compiler.generate_code(compiled, output_dir, options=options,
                                                            flat=explicit_output)
            t_file = time.monotonic() - t_file_start
            if cpp_path is not None:
                all_cpp_paths.append(cpp_path)
                progress.translated(compiled.name, t_file)

        t_codegen = time.monotonic() - t_codegen_start

        if args.dump_code or args.dump_thir:
            return 0

        # The CPython extension glue TUs (one per ext_module) are emitted
        # alongside their module .cpp during generate_code but tracked
        # separately; fold them into the link set now.
        if ext_module_build:
            all_cpp_paths.extend(compiler.collect_ext_glue_paths())

        n_cpp = len(all_cpp_paths)

        # Generate sources.cmake for CMake integration
        runtime_dir = get_runtime_dir()
        link_flags = compiler.collect_link_flags()

        # Resolve third-party deps (e.g. PCRE2 declared via
        # `# tpy: link("pcre2", managed=True)`) into concrete build inputs.
        # DisabledLibError is raised when the user passed `--<lib>=none` but
        # a module in the compile graph needs that lib -- surface it as a
        # clean compile error.
        from .build.third_party import (
            resolve_build_plan, DisabledLibError, SystemLibVersionError,
        )
        third_party_modes = _third_party_modes(args)
        try:
            third_party_plan = resolve_build_plan(
                dep_names=compiler.collect_third_party_deps(),
                runtime_cpp_dir=runtime_dir / "cpp",
                modes=third_party_modes,
            )
        except (DisabledLibError, SystemLibVersionError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        from .compiler import discover_runtime_cpp_sources
        runtime_cpp_sources = discover_runtime_cpp_sources(runtime_dir / "cpp")

        cmake_layout = BuildLayout(output_dir, module_name, flat=explicit_output)
        cmake_layout.generate_cmake(
            runtime_include_dir=runtime_dir / "cpp" / "include",
            cpp_files=all_cpp_paths,
            link_flags=link_flags,
            bundle_runtime=args.bundle_runtime and not building and explicit_output,
            third_party_libs=third_party_plan.libs,
            runtime_cpp_sources=runtime_cpp_sources or None,
        )

        # Build if requested
        if building:
            assert cpp_config is not None
            layout = BuildLayout(output_dir, module_name, build_variant=_build_variant(args),
                                   flat=explicit_output)
            binary_path = layout.so_path() if ext_module_build else layout.binary_path()

            opt_flags = _opt_flags(args)
            cpp_config.link_flags = link_flags

            # Build or reuse precompiled header. Skipped for ext_module
            # builds: the shared PCH is compiled without -fPIC, and
            # force-including it into the -fPIC extension TUs is a GCC
            # "PCH compiled with different -fPIC" mismatch.
            pch_includes: list[Path] = []
            if args.pch and not ext_module_build:
                t_pch_start = time.monotonic()
                pch_path = get_or_build_pch(
                    cpp_config, runtime_dir / "cpp" / "include", opt_flags,
                    pch_dir=layout.build_dir / "pch", force=args.rebuild,
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
                output=binary_path,
                opt_flags=opt_flags,
                config=cpp_config,
                force_includes=pch_includes or None,
                extra_include_dirs=third_party_plan.extra_include_dirs or None,
                extra_link_flags=third_party_plan.extra_link_flags or None,
                c_sources=third_party_plan.c_sources or None,
                runtime_cpp_sources=runtime_cpp_sources or None,
                shared=ext_module_build,
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

            if cache_key is not None:
                _record_build_manifest(
                    cache_key, layout.build_dir, input_path, compiled_modules,
                    compiler, cpp_config, runtime_dir, runtime_cpp_sources,
                    third_party_plan, warning_messages, binary_path)

            if not args.exec:
                label = "Built extension" if ext_module_build else "Built"
                print(f"{label}: {binary_path}")

            # Run if requested
            if args.exec:
                progress.separator()
                t_run_start = time.monotonic()
                returncode = _run_program([str(binary_path), *args.script_args])
                t_run = time.monotonic() - t_run_start

                if args.verbose >= 1:
                    print(f"  run: {t_run*1000:.0f}ms  total: {(t_compile+t_codegen+t_build+t_run)*1000:.0f}ms",
                          file=sys.stderr)

                return returncode

        return 0

    except (CompilerNotFoundError, ToolchainUnsupportedError) as e:
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


def _require_python_floor() -> None:
    # requires-python in pyproject gates pip installs only; a source
    # checkout run under an older interpreter would otherwise die with an
    # opaque SyntaxError on PEP-695 syntax in the compiler modules.
    if sys.version_info < (3, 12):
        raise SystemExit(
            "TurboPython requires Python 3.12+ "
            f"(running {sys.version.split()[0]})")


def main_tpyc() -> int:
    """Entry point for the `tpyc` command (compiler mode)."""
    _require_python_floor()
    return _run_cli(is_runner=False)


def main_tpy() -> int:
    """Entry point for the `tpy` command (runner mode)."""
    _require_python_floor()
    return _run_cli(is_runner=True)


if __name__ == "__main__":
    sys.exit(main_tpyc())
