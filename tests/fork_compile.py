"""Forked compiles for the test harness.

A program's compile spends most of its front-end time analyzing the stdlib
modules every program imports. Each xdist worker therefore starts, on first
use, a compile server per template key: a fresh interpreter that analyzes
the template's layers once (`Compiler.prepare`) and then forks once per
request; the child finishes the program (`Compiler.finish`) and runs the
request's function, `fn(compiler, run, *args)` with `run()` analyzing the
program, and returns what it returns, which must be plain data. Two clients
use it: `test_case` (`conftest.compile_case`, writing the C++ into the case's
`__tpyc__`) and the sweeps (`program_compile.program_facts` in
scripts/thir_migration/review, through `program_runner`).

Templates: `base` prepares the implicit stdlib; `asyncio` adds a layer
rooted at `asyncio`. A `base` child whose program reaches asyncio answers
"reroute" before analyzing anything, and the request goes to `asyncio`.

The program compiles in-process instead when its compiler configuration is
not the templates', when the child finds the prepared layers do not hold for
it (`LayerReuseRefused`: a program-local module shadows a prepared one, ...),
and for the rest of the worker's session once a server fails to start or
dies. Forking is Linux-only; `--no-fork-compile` turns it off.

Protocol: length-prefixed pickles over the server's stdin/stdout. A request
is a dict (`request`); a reply is a dict whose "kind" is "result" (what the
function returned), "raise" (what it raised), "reroute", "plain" (refused
reuse) or "died" (the child exited without a reply).
"""

from __future__ import annotations

import atexit
import ctypes
import gc
import importlib.util
import inspect
import os
import pickle
import signal
import struct
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import BinaryIO, Callable

import pytest

from tpyc.compiler import Compiler, LayerReuseRefused

TESTS_DIR = Path(__file__).resolve().parent

# The root groups `Compiler.prepare` analyzes for each template after the
# implicit stdlib, one layer per group.
TEMPLATES: dict[str, list[list[str]]] = {
    "base": [],
    "asyncio": [["asyncio"]],
}
# A child of the key reroutes to the template that prepares a module its
# program reaches.
REROUTES: dict[str, dict[str, str]] = {"base": {"asyncio": "asyncio"}}

_SERVER_BOOT = ("import sys; sys.path[:0] = sys.argv[1:3]; "
                "import fork_compile; fork_compile.serve(sys.argv[3])")

_HEADER = struct.Struct("<Q")


def _send(out: BinaryIO, obj: object) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    out.write(_HEADER.pack(len(data)))
    out.write(data)
    out.flush()


def _recv(inp: BinaryIO) -> object | None:
    """The next message, or None at end of stream (the peer is gone)."""
    header = inp.read(_HEADER.size)
    if len(header) < _HEADER.size:
        return None
    size, = _HEADER.unpack(header)
    data = inp.read(size)
    if len(data) < size:
        return None
    return pickle.loads(data)


# ----- worker side -----------------------------------------------------------

class _ServerFailed(Exception):
    pass


class _Server:
    def __init__(self, key: str) -> None:
        self.key = key
        self.stderr: BinaryIO | None = None
        self.proc: subprocess.Popen | None = None
        try:
            # A file, not a pipe: nobody drains the server's stderr while it runs.
            self.stderr = tempfile.TemporaryFile()
            self.proc = subprocess.Popen(
                [sys.executable, "-c", _SERVER_BOOT,
                 str(TESTS_DIR), str(TESTS_DIR.parent), key],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr)
            ready = self._read()
        except OSError as exc:
            self.close()
            raise _ServerFailed(f"compile server '{key}' failed to start: {exc}") from exc
        except _ServerFailed:
            self.close()
            raise
        if ready.get("kind") != "ready":
            self.close()
            raise _ServerFailed(f"compile server '{key}' failed to start:\n{ready.get('error', '')}")

    def _read(self) -> dict:
        reply = _recv(self.proc.stdout)
        if reply is None:
            raise self._gone()
        return reply

    def _gone(self) -> _ServerFailed:
        try:
            code = self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait()
        self.stderr.seek(0)
        tail = self.stderr.read().decode(errors="replace")[-4000:]
        return _ServerFailed(
            f"compile server '{self.key}' exited ({_describe_status(code)})\n{tail}")

    def request(self, req: dict) -> dict:
        try:
            try:
                _send(self.proc.stdin, req)
            except OSError:
                raise self._gone() from None
            return self._read()
        except _ServerFailed:
            raise
        except BaseException:
            # Interrupted mid-request: the reply would answer the next one.
            _servers.pop(self.key, None)
            self.proc.kill()
            self.close()
            raise

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait()
            self.proc.stdout.close()
        if self.stderr is not None:
            self.stderr.close()


_servers: dict[str, _Server] = {}
_fallback: list[str] = []
# One reason per case whose child refused the prepared layers (LayerReuseRefused).
_refused: list[str] = []


def enabled(config) -> bool:
    return (sys.platform == "linux" and not _fallback
            and not config.getoption("--no-fork-compile"))


def fallback_reason() -> str | None:
    """Why this process stopped forking compiles, if it did."""
    return _fallback[0] if _fallback else None


def refusals() -> list[str]:
    """Why each case this process compiled in-process after a refusal did."""
    return list(_refused)


def _server(key: str) -> _Server:
    server = _servers.get(key)
    if server is None:
        if not _servers:
            atexit.register(shutdown)
        server = _servers[key] = _Server(key)
    return server


def shutdown() -> None:
    while _servers:
        _servers.popitem()[1].close()


def request(fn: Callable, entry: Path, args: tuple, *, default_int: str,
            lib_dirs: list[Path], cwd: str | None = None) -> dict:
    """A request to run `fn(compiler, run, *args)` over the program `entry`
    compiled with (`default_int`, `lib_dirs`). `fn` is named by its module,
    that module's file and its qualname, so the server imports it once
    instead of the request carrying code."""
    # The file comes from the function itself: two modules here share the
    # name `conftest` (the root's and tests/'s), and `sys.modules` holds
    # whichever pytest loaded last.
    return {"fn": (fn.__module__, inspect.getfile(fn), fn.__qualname__),
            "entry": Path(entry), "cwd": os.getcwd() if cwd is None else cwd,
            "args": tuple(args), "default_int": default_int,
            "lib_dirs": [Path(p) for p in lib_dirs]}


# Every template is prepared at this `default_int` over `_template_lib_dirs()`;
# a request at any other config compiles in-process.
TEMPLATE_DEFAULT_INT = "int32"


def _template_lib_dirs() -> list[Path]:
    # Imported here: conftest imports this module.
    import conftest
    return list(conftest.DEFAULT_LIB_DIRS)


def _is_template_config(req: dict) -> bool:
    return (req["default_int"] == TEMPLATE_DEFAULT_INT
            and [p.resolve() for p in req["lib_dirs"]]
            == [Path(p).resolve() for p in _template_lib_dirs()])


def compile_forked(req: dict) -> dict | None:
    """Run one request in a child of a compile server. None: compile it in
    this process instead."""
    if not _is_template_config(req):
        return None
    key = "base"
    while True:
        try:
            reply = _server(key).request(req)
        except _ServerFailed as exc:
            _fallback.append(str(exc).splitlines()[0])
            sys.stderr.write(f"tpy| {exc}\n")
            shutdown()
            return None
        kind = reply["kind"]
        if kind == "reroute":
            key = reply["key"]
            continue
        if kind == "plain":
            _refused.append(f"{req['entry']}: {reply['reason']}")
            return None
        if kind == "died":
            pytest.fail(
                f"the forked compile of {req['entry']} died "
                f"({_describe_status(reply['status'])})\n--- stderr ---\n{reply['stderr']}",
                pytrace=False)
        return reply


def result(reply: dict) -> object:
    """What the request's function returned; what it raised is raised here."""
    if reply["kind"] == "raise":
        reraise(reply["raise"])
    return reply["result"]


def program_runner(config) -> Callable:
    """The runner `program_compile.use` takes: a sweep's compile forked from
    a compile server, or None to compile it in-process."""
    def run(entry: Path, fn: Callable, args: tuple, default_int: str,
            lib_dirs: list[Path]) -> object:
        # Per request: a server that failed turns forking off mid-session.
        if not enabled(config):
            return None
        reply = compile_forked(request(fn, entry, args, default_int=default_int,
                                       lib_dirs=lib_dirs))
        return None if reply is None else result(reply)
    return run


def reraise(record: dict) -> None:
    """Raise what a forked compile raised, as the in-process compile would."""
    if record["failed"] is not None:
        msg, pytrace = record["failed"]
        pytest.fail(msg, pytrace=pytrace)
    exc: BaseException
    if record["pickled"] is not None:
        exc = pickle.loads(record["pickled"])
    else:
        exc = RuntimeError(f"{record['type']}: {record['message']}")
    exc.add_note(f"--- raised in the forked compile ---\n{record['traceback']}")
    raise exc


def _describe_status(status: int) -> str:
    """`status` as subprocess reports it: negative for a signal."""
    if status < 0:
        try:
            return f"killed by {signal.Signals(-status).name}"
        except ValueError:
            return f"killed by signal {-status}"
    return f"exit status {status}"


# ----- server side -----------------------------------------------------------

class _Reroute(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def serve(key: str) -> None:
    """The compile server's main loop; returns when the worker closes stdin."""
    requests = sys.stdin.buffer
    replies = os.fdopen(os.dup(1), "wb")
    # Anything the compiler prints must not land in the reply stream.
    os.dup2(2, 1)
    try:
        template_dir = Path(tempfile.mkdtemp(prefix="tpy-fork-template-"))
        try:
            compiler = Compiler(template_dir / "__template__.py",
                                default_int=TEMPLATE_DEFAULT_INT,
                                lib_dirs=_template_lib_dirs())
            compiler.prepare(TEMPLATES[key])
        finally:
            template_dir.rmdir()
    except BaseException:
        _send(replies, {"kind": "failed", "error": traceback.format_exc()})
        return
    # Children only read the template; keeping it out of the collector's
    # generations keeps a collection from touching (and copying) its pages.
    gc.freeze()
    libc = ctypes.CDLL(None, use_errno=True)
    _send(replies, {"kind": "ready"})
    while True:
        req = _recv(requests)
        if req is None:
            return
        _send(replies, _fork_one(compiler, key, req, replies.fileno(), libc))


_PR_SET_PDEATHSIG = 1


def _function(ref: tuple[str, str, str]) -> Callable:
    """The request's function, its module imported into the server on first
    use so no child imports it again."""
    module_name, file, qualname = ref
    module = sys.modules.get(module_name)
    if module is not None and Path(module.__file__).resolve() != Path(file).resolve():
        raise ImportError(f"the server's module {module_name!r} is {module.__file__}, "
                          f"not {file}")
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, file)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[module_name]
            raise
    fn = module
    for part in qualname.split("."):
        fn = getattr(fn, part)
    return fn


def _fork_one(compiler: Compiler, key: str, req: dict, replies_fd: int,
              libc: ctypes.CDLL) -> dict:
    try:
        fn = _function(req["fn"])
    except Exception as exc:  # noqa: BLE001 -- raised again in the worker
        return {"kind": "raise", "raise": _exception_record(exc)}
    read_fd, write_fd = os.pipe()
    stderr = tempfile.TemporaryFile()
    server_pid = os.getpid()
    pid = os.fork()
    if pid == 0:
        try:
            # The worker sees a dead server as the end of its reply stream
            # only once no process holds the stream open, and a child left
            # compiling for nobody only wastes a core: die with the server.
            libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
            if os.getppid() != server_pid:
                os._exit(1)
            os.close(replies_fd)
            devnull = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull, 0)
            os.close(devnull)
            os.close(read_fd)
            os.dup2(stderr.fileno(), 1)
            os.dup2(stderr.fileno(), 2)
            reply = _child(compiler, key, req, fn)
            with os.fdopen(write_fd, "wb") as out:
                _send(out, reply)
        except BaseException:
            traceback.print_exc()
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0)
    os.close(write_fd)
    # Read before reaping: a large reply would block the child on a full pipe.
    with os.fdopen(read_fd, "rb") as inp:
        reply = _recv(inp)
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    if reply is None or code != 0:
        stderr.seek(0)
        reply = {"kind": "died", "status": code,
                 "stderr": stderr.read().decode(errors="replace")}
    stderr.close()
    return reply


def _child(compiler: Compiler, key: str, req: dict, fn: Callable) -> dict:
    os.chdir(req["cwd"])
    routes = REROUTES.get(key, {})
    signals: list[Exception] = []

    def on_discovered(new: list[str]) -> None:
        for name in new:
            if name in routes:
                raise _Reroute(routes[name])

    def run() -> list:
        try:
            return compiler.finish(req["entry"], reuse=True, on_discovered=on_discovered)
        except (_Reroute, LayerReuseRefused) as exc:
            # Recorded before it propagates: a client catching broadly would
            # otherwise turn it into a verdict of this template's making.
            signals.append(exc)
            raise

    reply: dict | None = None
    try:
        reply = {"kind": "result", "result": fn(compiler, run, *req["args"])}
    except BaseException as exc:
        if not signals:
            reply = {"kind": "raise", "raise": _exception_record(exc)}
    if signals:
        signal_ = signals[0]
        if isinstance(signal_, _Reroute):
            return {"kind": "reroute", "key": signal_.key}
        return {"kind": "plain", "reason": str(signal_)}
    return reply


def _exception_record(exc: BaseException) -> dict:
    failed = None
    if isinstance(exc, pytest.fail.Exception):
        failed = (exc.msg, exc.pytrace)
    pickled: bytes | None = None
    try:
        data = pickle.dumps(exc)
        if type(pickle.loads(data)) is type(exc):
            pickled = data
    except Exception:  # noqa: BLE001 -- unpicklable: sent as text below
        pass
    return {"failed": failed, "pickled": pickled, "type": type(exc).__qualname__,
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(exc))}
