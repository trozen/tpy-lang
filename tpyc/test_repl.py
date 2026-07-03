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
from .repl_backends import BackendResult, REPLBackend, ReplBuildDeps


def _lib_dirs():
    return [get_lib_dir() / "tpy"]


class _CapturingBackend(REPLBackend):
    """Records the paths the REPL wrote, succeeds without building C++."""

    def __init__(self):
        self.hpp_paths: list[Path] = []
        self.cpp_paths: list[Path] = []
        self.deps: ReplBuildDeps | None = None

    @property
    def name(self) -> str:
        return "capturing"

    def startup(self) -> None:
        pass

    def execute(self, all_hpp_code, all_cpp_code, all_hpp_paths, all_cpp_paths,
                deps=None):
        self.hpp_paths = list(all_hpp_paths)
        self.cpp_paths = list(all_cpp_paths)
        self.deps = deps
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


def test_repl_deps_carry_runtime_and_managed_third_party():
    # Regression guard for the REPL link gap: `from datetime import datetime`
    # needs the vendored Hinnant date sources (tz.cpp + date_shim.cpp) and the
    # always-linked runtime impls (os_impl.cpp etc.) on the link line -- the
    # REPL backend previously received neither and every managed-lib import
    # died with undefined references.
    session, backend, success, output = _run_line(
        "from datetime import datetime\n")
    assert success, output
    deps = backend.deps
    assert deps is not None, "backend received no build deps"
    runtime_names = {p.name for p in deps.runtime_sources}
    assert "os_impl.cpp" in runtime_names, runtime_names
    tp_names = {src.name for src, _flags in deps.third_party_sources}
    assert {"tz.cpp", "date_shim.cpp"} <= tp_names, tp_names
    # Shims must come via the third-party plan, never the always-linked set.
    assert not any(n.endswith("_shim.cpp") for n in runtime_names)


def test_repl_deps_re_pulls_pcre2():
    session, backend, success, output = _run_line("import re\n")
    assert success, output
    tp_names = {src.name for src, _flags in backend.deps.third_party_sources}
    assert "pcre2_match.c" in tp_names, tp_names
    # The plan's include dirs must ride along (pcre2 exports one).
    assert backend.deps.include_dirs, "pcre2 include dir missing from deps"


def test_repl_plan_cache_resolves_once_per_dep_set():
    # Two evals with the same third-party dep set must reuse the cached
    # build plan (a per-eval re-resolve would be a silent perf regression).
    session = REPLSession(lib_dirs=_lib_dirs())
    backend = _CapturingBackend()
    session._backend = backend
    success, output = session._try_compile_and_run(
        "import re\n", maybe_auto_print=False)
    assert success, output
    assert len(session._plan_cache) == 1
    plan_first = next(iter(session._plan_cache.values()))
    session.accumulated_lines.append("import re")
    success, output = session._try_compile_and_run(
        "x = 1\n", maybe_auto_print=False)
    assert success, output
    assert len(session._plan_cache) == 1, session._plan_cache.keys()
    assert next(iter(session._plan_cache.values())) is plan_first


def test_backend_support_objects_compile_once_and_in_order(monkeypatch, tmp_path):
    # _ensure_support_objects must invoke the compiler once per source per
    # session (mtime-keyed cache) and return objects in runtime-then-
    # third-party order. No real toolchain: subprocess.run is stubbed.
    import subprocess as sp
    from .repl_backends import CompileBackend

    calls: list[str] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd[-1])
        return sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tpyc.repl_backends.subprocess.run", fake_run)
    backend = CompileBackend(["g++"], tmp_path, "repl")

    rt = tmp_path / "rt_impl.cpp"
    tp = tmp_path / "vendor.c"
    rt.write_text("// rt")
    tp.write_text("// tp")
    deps = ReplBuildDeps(runtime_sources=[rt],
                         third_party_sources=[(tp, ["-DX"])])

    objs1, err1 = backend._ensure_support_objects(deps)
    assert err1 == ""
    assert len(objs1) == 2 and len(calls) == 2
    assert objs1[0].endswith("rt_impl.o") and objs1[1].endswith("vendor.o")

    objs2, err2 = backend._ensure_support_objects(deps)
    assert err2 == ""
    assert objs2 == objs1
    assert len(calls) == 2, "support sources recompiled despite cache"

    # Touching a source (newer mtime) must trigger exactly one recompile.
    import os
    os.utime(rt, (rt.stat().st_atime, rt.stat().st_mtime + 10))
    objs3, err3 = backend._ensure_support_objects(deps)
    assert err3 == ""
    assert objs3 == objs1
    assert len(calls) == 3, "mtime change did not invalidate the cache"


def test_repl_deps_plain_eval_has_no_third_party():
    # Inverse guard: an eval that imports no managed lib must not drag
    # vendored sources into the build (runtime impls are always linked,
    # matching the file-compile path).
    session, backend, success, output = _run_line("x = 5\n")
    assert success, output
    deps = backend.deps
    assert deps is not None
    assert deps.third_party_sources == [], deps.third_party_sources
    assert {p.name for p in deps.runtime_sources} >= {"os_impl.cpp"}


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
