"""REPL unit tests (no C++ toolchain required).

The REPL writes each module's generated .hpp/.cpp to disk and hands the paths
to a build backend. The headers must land at exactly the path codegen baked
into the `#include` directives, or the build fails with "No such file".
"""
from pathlib import Path

from . import get_lib_dir
from .compilation_context import activate_compiler
from .codegen_cpp.context import module_to_include_path
from .compiler import Compiler
from .repl import REPLSession
from .repl_backends import BackendResult, REPLBackend


def _lib_dirs():
    return [get_lib_dir() / "tpy"]


class _CapturingBackend(REPLBackend):
    """Records the paths the REPL wrote, succeeds without building C++."""

    def __init__(self):
        self.hpp_paths: list[Path] = []
        self.cpp_paths: list[Path] = []

    @property
    def name(self) -> str:
        return "capturing"

    def startup(self) -> None:
        pass

    def execute(self, all_hpp_code, all_cpp_code, all_hpp_paths, all_cpp_paths):
        self.hpp_paths = list(all_hpp_paths)
        self.cpp_paths = list(all_cpp_paths)
        return BackendResult(success=True)

    def cleanup(self) -> None:
        pass


def _run_line(source: str):
    session = REPLSession(lib_dirs=_lib_dirs())
    backend = _CapturingBackend()
    session._backend = backend
    success, output = session._try_compile_and_run(source, maybe_auto_print=False)
    return session, backend, success, output


def test_repl_headers_written_where_codegen_includes_them():
    # Importing json pulls in cpp_namespace stdlib modules (e.g. tpy._typing ->
    # tpystd/typing/_typing.hpp). Regression guard: the REPL must write each
    # module header at the namespace-derived include path, not the name-derived
    # fallback -- otherwise the generated .cpp's #include can't find it.
    session, backend, success, output = _run_line("import json\n")
    assert success, output
    assert backend.hpp_paths, "backend received no header paths"

    written = {p.resolve() for p in backend.hpp_paths}
    # Every written header must exist on disk...
    for p in backend.hpp_paths:
        assert p.exists(), f"header not written: {p}"
    # ...and a cpp_namespace module must be present at its namespace path,
    # proving the include-path override (not the name fallback) was applied.
    typing_hpp = (session.temp_dir / "tpystd/typing/_typing.hpp").resolve()
    assert typing_hpp in written, (
        "tpy._typing header not at its namespace path; "
        f"written paths: {sorted(str(p) for p in written)}"
    )


def test_repl_should_accumulate_skips_bare_expressions():
    # A bare expression is a query the REPL auto-prints; it binds nothing and
    # must not be replayed into later compiles. A bare brace-init literal
    # replayed as a statement (`{1, 2, 3};`) is invalid C++ and used to poison
    # the whole session (every later input failed to build). Walrus is the
    # exception -- it binds a name that has to persist.
    session = REPLSession(lib_dirs=_lib_dirs())
    # Bare expressions: not accumulated. A call stays excluded even with a
    # nested walrus -- replaying its side effect is worse than losing the bind.
    for src in ("[1, 2, 3]", "{1, 2, 3}", "{1: 2}", "42", '"hi"', "x + 1",
                "print(x)", "xs.append(3)", "f((z := 3))"):
        assert session._should_accumulate(src) is False, src
    # Bindings / definitions: accumulated.
    for src in ("x = 5", "(y := 5)", "import math", "from math import sqrt",
                "def f(n: int) -> int:\n    return n"):
        assert session._should_accumulate(src) is True, src


def test_repl_bare_expression_does_not_poison_session():
    # End-to-end at the session level (no C++ build): an auto-printed bare
    # list literal must leave `accumulated_lines` untouched, while a real
    # assignment is remembered.
    session = REPLSession(lib_dirs=_lib_dirs())
    session._backend = _CapturingBackend()
    before = list(session.accumulated_lines)
    session._process_input("[1, 2, 3]")
    assert session.accumulated_lines == before, (
        "bare list literal was accumulated and would poison later inputs")
    session._process_input("z = 7")
    assert "z = 7" in session.accumulated_lines


def test_repl_written_paths_match_include_path_map():
    # Stronger invariant: for every compiled module, the written header path
    # equals module_to_include_path() resolved under the active compiler --
    # the exact mapping codegen uses to emit the #include line. The REPL
    # appends a noop line to force a main(); mirror that so module discovery
    # matches.
    session, backend, success, output = _run_line("import json\n")
    assert success, output

    written_rel = {p.resolve().relative_to(session.temp_dir.resolve())
                   for p in backend.hpp_paths}
    compiler = Compiler.from_source(
        "import json\n0  # repl-noop", "repl", lib_dirs=session.lib_dirs
    )
    compiler.compile()
    with activate_compiler(compiler):
        expected = {Path(module_to_include_path(name)) for name in compiler.modules}
    assert expected <= written_rel, (
        f"missing headers: {sorted(str(p) for p in expected - written_rel)}"
    )
