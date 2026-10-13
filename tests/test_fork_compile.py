"""Forked compiles (tests/fork_compile.py): a case compiled in a child of a
compile server gives what the in-process compile gives, and every way out
of the forked path lands where the design puts it."""

import dataclasses
import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

import conftest
import fork_compile
from conftest import CASES_DIR, CompileResult, case_fork_request, case_request, compile_case
from tpyc.compiler import Compiler

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="forked compiles are Linux-only")


@pytest.fixture
def servers(monkeypatch):
    """Servers and fallback state of this test's own, so a test that kills a
    server does not turn forking off for the cases sharing the worker."""
    monkeypatch.setattr(fork_compile, "_servers", {})
    monkeypatch.setattr(fork_compile, "_fallback", [])
    monkeypatch.setattr(fork_compile, "_refused", [])
    yield
    fork_compile.shutdown()


def _tree(out: Path) -> dict[str, str]:
    return {str(p.relative_to(out)): p.read_text()
            for p in sorted(out.rglob("*")) if p.is_file()}


def _plain(req) -> CompileResult:
    compiler = Compiler(req.src_file, default_int=req.default_int,
                        lib_dirs=list(conftest.DEFAULT_LIB_DIRS))
    return compile_case(compiler, compiler.compile, req)


def _forked(req) -> CompileResult:
    reply = fork_compile.compile_forked(case_fork_request(req))
    assert reply is not None, fork_compile.fallback_reason()
    return fork_compile.result(reply)


def _copy_case(case: Path, tmp_path: Path) -> Path:
    """The case's sources in a scratch case directory, so neither compile
    writes into the checked-in tree."""
    dest = tmp_path / case.name
    shutil.copytree(case / "src", dest / "src")
    return dest / "src" / "main.py"


@pytest.mark.parametrize("case", [
    "imports/cross_module_qualified_ctor_aliased_reexport",  # several local modules
    "async/async_assert_message_await",  # asyncio
    "async/await_assign",  # `async def` without asyncio
    "imports/error_all_augment_no_base",  # rejected
    "macros/call_macro_pkg",  # program-local macro package
    "macros/builder_smoke",  # stdlib builder macro
    "tplib/arc_weak_after_drop",
    "stdlib/http_client",
])
def test_forked_compile_matches_in_process(case, tmp_path, servers):
    src = _copy_case(CASES_DIR / case, tmp_path)
    out = src.parent.parent / "__tpyc__"
    req = case_request(src, out)
    forked = _forked(req)
    forked_tree = _tree(out)
    shutil.rmtree(out, ignore_errors=True)
    plain = _plain(req)
    assert dataclasses.asdict(forked) == dataclasses.asdict(plain)
    assert forked_tree == _tree(out)
    assert forked.success == (not Path(case).name.startswith("error_"))
    if forked.success:
        # Equal could be both empty: the session's THIR tally reads these
        # (an async-only case lowers frames, not plain bodies).
        assert forked.thir_faces


def test_asyncio_program_is_rerouted(tmp_path, servers):
    src = _copy_case(CASES_DIR / "async/async_assert_message_await", tmp_path)
    req = case_fork_request(case_request(src, src.parent.parent / "__tpyc__"))
    assert fork_compile._server("base").request(req) == {"kind": "reroute", "key": "asyncio"}
    assert fork_compile.compile_forked(req)["kind"] == "result"
    assert set(fork_compile._servers) == {"base", "asyncio"}


@pytest.mark.parametrize("local", ["typing", "asyncio"])
def test_program_shadowing_a_prepared_module_compiles_in_process(local, tmp_path, servers):
    src_dir = tmp_path / "case" / "src"
    src_dir.mkdir(parents=True)
    # Shadowing `asyncio` reroutes first; the asyncio template then refuses.
    (src_dir / f"{local}.py").write_text("def helper() -> int:\n    return 1\n")
    src = src_dir / "main.py"
    src.write_text(f"import {local}\n\nprint({local}.helper())\n")
    req = case_fork_request(case_request(src, tmp_path / "case" / "__tpyc__"))
    assert fork_compile._server("base").request(req)["kind"] == (
        "plain" if local == "typing" else "reroute")
    assert fork_compile.compile_forked(req) is None
    assert fork_compile.fallback_reason() is None
    # counted for the session summary, so a check refusing everything shows
    [refusal] = fork_compile.refusals()
    assert refusal.startswith(f"{src}: ") and local in refusal


def _children(pid: int) -> list[int]:
    path = Path(f"/proc/{pid}/task/{pid}/children")
    return [int(p) for p in path.read_text().split()]


def _gone(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return True
    return stat.rsplit(")", 1)[1].split()[0] == "Z"


def _blocked_compile(tmp_path: Path) -> tuple[dict, threading.Thread, list, list]:
    """A forked compile that blocks in its child until someone intervenes:
    reading a FIFO no one writes. Returns the request, the thread running
    `compile_forked` on it, and that call's result / exception lists."""
    src_dir = tmp_path / "blocked" / "src"
    src_dir.mkdir(parents=True)
    src = src_dir / "main.py"
    os.mkfifo(src)
    req = case_fork_request(case_request(src, tmp_path / "blocked" / "__tpyc__"))
    results: list = []
    raised: list[BaseException] = []

    def compile_it() -> None:
        try:
            results.append(fork_compile.compile_forked(req))
        except BaseException as exc:  # noqa: BLE001 -- inspected by the caller
            raised.append(exc)

    return req, threading.Thread(target=compile_it), results, raised


def _wait_for_child(server_pid: int) -> int:
    deadline = time.monotonic() + 60
    while not _children(server_pid):
        assert time.monotonic() < deadline, "the server never forked"
        time.sleep(0.05)
    return _children(server_pid)[0]


def test_child_killed_by_a_signal_fails_the_case(tmp_path, servers):
    server = fork_compile._server("base")
    _req, worker, _results, raised = _blocked_compile(tmp_path)
    worker.start()
    os.kill(_wait_for_child(server.proc.pid), signal.SIGKILL)
    worker.join(60)
    assert len(raised) == 1 and isinstance(raised[0], pytest.fail.Exception)
    assert "died (killed by SIGKILL)" in str(raised[0])
    # The server reaped the child and goes on serving.
    assert fork_compile.fallback_reason() is None
    good = _copy_case(CASES_DIR / "tplib/arc_weak_after_drop", tmp_path)
    assert _forked(case_request(good, good.parent.parent / "__tpyc__")).success


def test_server_dying_mid_compile_falls_back_and_takes_its_child(tmp_path, servers):
    server = fork_compile._server("base")
    _req, worker, results, raised = _blocked_compile(tmp_path)
    worker.start()
    child = _wait_for_child(server.proc.pid)
    server.proc.kill()
    # The child held no copy of the reply stream, so the worker sees the end
    # of it at once and compiles in-process.
    worker.join(30)
    assert not worker.is_alive() and raised == [] and results == [None]
    assert "killed by SIGKILL" in fork_compile.fallback_reason()
    deadline = time.monotonic() + 10
    while not _gone(child):
        assert time.monotonic() < deadline, "the child outlived its server"
        time.sleep(0.05)


def test_server_that_cannot_start_falls_back(tmp_path, servers, monkeypatch):
    def no_process(*args, **kwargs):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(fork_compile.subprocess, "Popen", no_process)
    src = _copy_case(CASES_DIR / "tplib/arc_weak_after_drop", tmp_path)
    req = case_fork_request(case_request(src, src.parent.parent / "__tpyc__"))
    assert fork_compile.compile_forked(req) is None
    assert fork_compile.fallback_reason() == (
        "compile server 'base' failed to start: [Errno 24] Too many open files")
    assert fork_compile._servers == {}


def test_request_naming_a_missing_function_raises_in_the_worker(tmp_path, servers):
    src = tmp_path / "main.py"
    src.write_text("print(1)\n")
    req = fork_compile.request(compile_case, src, (), default_int="int32",
                               lib_dirs=conftest.DEFAULT_LIB_DIRS)
    req["fn"] = ("conftest", conftest.__file__, "no_such_function")
    reply = fork_compile.compile_forked(req)
    assert reply["kind"] == "raise"
    with pytest.raises(AttributeError, match="no_such_function"):
        fork_compile.result(reply)
    # The root conftest shares the name; the server must not take one for the other.
    req["fn"] = ("conftest", str(conftest.PROJECT_ROOT / "conftest.py"), "compile_case")
    with pytest.raises(ImportError, match="not .*/conftest.py"):
        fork_compile.result(fork_compile.compile_forked(req))


def test_server_reporting_failure_does_not_start(servers):
    with pytest.raises(fork_compile._ServerFailed,
                       match=r"(?s)compile server 'nope' failed to start:.*KeyError: 'nope'"):
        fork_compile._Server("nope")


def test_interrupted_request_never_answers_the_next(tmp_path, servers, monkeypatch):
    first = _copy_case(CASES_DIR / "tplib/arc_weak_after_drop", tmp_path)
    second = _copy_case(CASES_DIR / "imports/aliased_flat_reexport", tmp_path)
    stale = fork_compile._server("base")

    def interrupted(inp):
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(fork_compile, "_recv", interrupted)
        with pytest.raises(KeyboardInterrupt):
            fork_compile.compile_forked(case_fork_request(case_request(first, first.parent.parent / "__tpyc__")))
    assert stale.proc.poll() is not None and "base" not in fork_compile._servers
    # The first case's reply was never read; the next request starts over.
    result = _forked(case_request(second, second.parent.parent / "__tpyc__"))
    assert fork_compile._servers["base"] is not stale
    assert second.parent.parent / "__tpyc__" in result.cpp_path.parents
    assert {"b", "c", "main"} <= {name for name, *_ in result.all_modules}


def _raise_reply(req) -> dict:
    reply = fork_compile.compile_forked(case_fork_request(req))
    assert reply is not None and reply["kind"] == "raise", reply
    return reply


def test_forked_compile_raises_what_in_process_raises(tmp_path, servers):
    src = _copy_case(CASES_DIR / "tplib/arc_weak_after_drop", tmp_path)
    out = src.parent.parent / "__tpyc__"
    # pytest.fail: a snapshot_lib_modules pattern that matches no module.
    req = dataclasses.replace(case_request(src, out), snapshot_lib_modules=frozenset({"nomatch.*"}))
    reply = _raise_reply(req)
    with pytest.raises(pytest.fail.Exception) as forked:
        fork_compile.reraise(reply["raise"])
    with pytest.raises(pytest.fail.Exception) as plain:
        _plain(req)
    assert (forked.value.msg, forked.value.pytrace) == (plain.value.msg, plain.value.pytrace)
    assert "nomatch.*" in forked.value.msg
    # An internal error: the output directory is a file.
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    req = case_request(src, blocker)
    reply = _raise_reply(req)
    with pytest.raises(OSError) as forked_os:
        fork_compile.reraise(reply["raise"])
    with pytest.raises(OSError) as plain_os:
        _plain(req)
    assert type(forked_os.value) is type(plain_os.value)
    assert str(forked_os.value) == str(plain_os.value)
    assert any("raised in the forked compile" in note for note in forked_os.value.__notes__)


class _TwoArgError(Exception):
    def __init__(self, what: str, detail: str) -> None:
        super().__init__(what)
        self.detail = detail


def test_unpicklable_exception_is_raised_as_text():
    try:
        raise _TwoArgError("broken", "x")
    except _TwoArgError as exc:
        record = fork_compile._exception_record(exc)
    assert record["pickled"] is None
    with pytest.raises(RuntimeError) as raised:
        fork_compile.reraise(record)
    assert raised.value.args == ("_TwoArgError: broken",)
    assert "raise _TwoArgError" in raised.value.__notes__[0]


def test_dead_server_falls_back_once(tmp_path, servers, capsys):
    src = _copy_case(CASES_DIR / "tplib/arc_weak_after_drop", tmp_path)
    req = case_fork_request(case_request(src, src.parent.parent / "__tpyc__"))
    server = fork_compile._server("base")
    server.proc.kill()
    server.proc.wait()
    assert fork_compile.compile_forked(req) is None
    reason = fork_compile.fallback_reason()
    assert reason is not None and "compile server 'base' exited" in reason
    assert capsys.readouterr().err.count("tpy| compile server") == 1
    # Off for the rest of the session: no server is started again.
    config = type("Config", (), {"getoption": staticmethod(lambda name: False)})()
    assert not fork_compile.enabled(config)
    assert fork_compile._servers == {}


@pytest.mark.parametrize("update", [False, True])
def test_live_annotation_checks_follow_update_mode(update, tmp_path, servers):
    src_dir = tmp_path / "case" / "src"
    src_dir.mkdir(parents=True)
    src = src_dir / "main.py"
    # Every annotation below is wrong.
    src.write_text(
        "def main() -> None:  # tpyc: mir(conflict /nothing/)\n"
        "    x = 1  # tpyc: type(str)\n"
        "    y = x  # tpyc: mir_owned(y)\n"
        "    print(y)\n"
        "\n"
        "\n"
        "main()\n")
    req = dataclasses.replace(case_request(src, tmp_path / "case" / "__tpyc__"), update=update)
    forked = _forked(req)
    assert dataclasses.asdict(forked) == dataclasses.asdict(_plain(req))
    errors = (forked.type_annotation_errors, forked.mir_annotation_errors,
              forked.mir_fact_annotation_errors)
    if update:
        assert errors == ([], [], [])
    else:
        assert errors == (
            ["Line 2: expected type 'str' for 'x' but got 'int32'"],
            ["Line 1: expected mir(conflict /nothing/) for 'main' but MIR says covered "
             "(storage: no_proof_required)"],
            ["Line 3: expected mir_owned(y) in 'main' but MIR writes y as scalar"])



def test_request_and_result_records_cross_the_pipe():
    """The server protocol pickles the request and the result; a field that
    does not round-trip would fail every forked case at run time."""
    req = case_fork_request(conftest.case_request(Path("/x/src/main.py"), Path("/x/__tpyc__")))
    assert fork_compile.pickle.loads(fork_compile.pickle.dumps(req)) == req
    result = CompileResult(**{f.name: f.default if f.default is not dataclasses.MISSING
                              else None for f in dataclasses.fields(CompileResult)})
    assert fork_compile.pickle.loads(fork_compile.pickle.dumps(result)) == result


# ----- the sweeps' client: program_compile.program_facts ---------------------

_FORKING = type("Config", (), {"getoption": staticmethod(lambda name: False)})()

# A warning, a lowered function, a frame and a constructor: every kind of
# fact the sweeps read.
_SWEEP_PROGRAM = """\
from typing import Iterator


class P:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


def read(o: P | None) -> int:
    return o.x


def count(n: int) -> Iterator[int]:
    yield n


def main() -> None:
    print(read(P(1)))
    for v in count(2):
        print(v)


main()
"""


@pytest.fixture
def program_compile(servers, monkeypatch):
    if not conftest.REVIEW_SCRIPTS_DIR.exists():
        pytest.skip("scripts/thir_migration/review is not part of this checkout")
    module = conftest.import_program_compile()
    monkeypatch.setattr(module, "_runner", fork_compile.program_runner(_FORKING))
    return module


def _in_process(program_compile, src: Path, *args):
    compiler = Compiler(src, default_int="int32", lib_dirs=list(conftest.DEFAULT_LIB_DIRS))
    return program_compile.program_facts(compiler, compiler.compile, *args)


def _facts_request(program_compile, src: Path, *args) -> dict:
    return fork_compile.request(program_compile.program_facts, src, args,
                                default_int="int32", lib_dirs=conftest.DEFAULT_LIB_DIRS)


def test_sweep_client_matches_in_process(tmp_path, program_compile):
    src = tmp_path / "prog.py"
    src.write_text(_SWEEP_PROGRAM)
    forked = program_compile.compile_program(src, program_compile.program_facts)
    assert set(fork_compile._servers) == {"base"} and fork_compile.refusals() == []
    assert forked == _in_process(program_compile, src)
    bodies = {b["name"]: b for b in forked["bodies"]}
    assert bodies["read"]["function"] and bodies["count"]["resumable"]
    assert bodies["P"]["kind"] == "constructor" and bodies["P"]["constructor"]
    assert [w for w in program_compile.warnings_of(forked) if "None access" in w[1]]
    # With a codegen directory the child writes the C++, as in-process.
    out_forked, out_plain = tmp_path / "forked", tmp_path / "plain"
    forked = program_compile.compile_program(src, program_compile.program_facts, out_forked)
    assert forked == _in_process(program_compile, src, out_plain)
    assert forked["codegen_error"] is None
    assert _tree(out_forked) == _tree(out_plain) != {}


def test_sweep_client_asyncio_program_is_rerouted(tmp_path, program_compile):
    src = _copy_case(CASES_DIR / "async/async_assert_message_await", tmp_path)
    # program_facts catches what `run()` raises; the reroute still wins.
    reply = fork_compile._server("base").request(_facts_request(program_compile, src))
    assert reply == {"kind": "reroute", "key": "asyncio"}
    forked = program_compile.compile_program(src, program_compile.program_facts)
    assert set(fork_compile._servers) == {"base", "asyncio"}
    assert forked == _in_process(program_compile, src)
    assert forked["raised"] is None and forked["bodies"]


def test_sweep_client_refused_program_compiles_in_process(tmp_path, program_compile):
    (tmp_path / "typing.py").write_text("def helper() -> int:\n    return 1\n")
    src = tmp_path / "main.py"
    src.write_text("import typing\n\nprint(typing.helper())\n")
    # program_facts catches what `run()` raises; the refusal still wins.
    reply = fork_compile._server("base").request(_facts_request(program_compile, src))
    assert reply["kind"] == "plain"
    assert program_compile.compile_program(src, program_compile.program_facts) == \
        _in_process(program_compile, src)
    [refusal] = fork_compile.refusals()
    assert refusal.startswith(f"{src}: ")
    assert fork_compile.fallback_reason() is None


def test_request_off_the_template_config_compiles_in_process(tmp_path, servers):
    src = tmp_path / "main.py"
    src.write_text("print(1)\n")
    for config in ({"default_int": "int64", "lib_dirs": conftest.DEFAULT_LIB_DIRS},
                   {"default_int": "int32", "lib_dirs": [tmp_path]}):
        req = fork_compile.request(compile_case, src, (), **config)
        assert fork_compile.compile_forked(req) is None
    assert fork_compile._servers == {}


def test_sweep_runner_stops_forking_once_a_server_failed(tmp_path, program_compile,
                                                         monkeypatch):
    starts = []

    def no_process(*args, **kwargs):
        starts.append(args)
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(fork_compile.subprocess, "Popen", no_process)
    src = tmp_path / "prog.py"
    src.write_text(_SWEEP_PROGRAM)
    expected = _in_process(program_compile, src)
    for _ in range(2):
        assert program_compile.compile_program(src, program_compile.program_facts) == expected
    # The runner re-checks per request, so the failed start is not retried.
    assert len(starts) == 1 and fork_compile.fallback_reason() is not None


def test_sweep_runner_honours_no_fork_compile(tmp_path, program_compile, monkeypatch):
    plain = type("Config", (), {"getoption": staticmethod(lambda name: True)})()
    monkeypatch.setattr(program_compile, "_runner", fork_compile.program_runner(plain))
    src = tmp_path / "prog.py"
    src.write_text(_SWEEP_PROGRAM)
    assert program_compile.compile_program(src, program_compile.program_facts) == \
        _in_process(program_compile, src)
    assert fork_compile._servers == {}
