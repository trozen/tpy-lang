"""Forked compiles for the test harness.

A case's compile spends most of its front-end time analyzing the stdlib
modules every program imports. Each xdist worker therefore starts, on first
use, a compile server per template key: a fresh interpreter that analyzes
the template's layers once (`Compiler.prepare`) and then forks once per
case; the child finishes the program (`Compiler.finish`) and runs the same
`conftest.compile_case` the in-process path runs, writing the C++ into the
case's `__tpyc__` and returning the result as plain data.

Templates: `base` prepares the implicit stdlib; `asyncio` adds a layer
rooted at `asyncio`. A `base` child whose program reaches asyncio answers
"reroute" before analyzing anything, and the case goes to `asyncio`.

The case compiles in-process instead when the child finds the prepared
layers do not hold for it (`LayerReuseRefused`: a program-local module
shadows a prepared one, ...), and for the rest of the worker's session once
a server fails to start or dies. Forking is Linux-only; `--no-fork-compile`
turns it off.

Protocol: length-prefixed pickles over the server's stdin/stdout. A request
is a `CaseRequest` as a field dict; a reply is a dict whose "kind" is
"result" (the `CompileResult` fields), "raise" (what the compile raised),
"reroute", "plain" (refused reuse) or "died" (the child exited without a
reply).
"""

from __future__ import annotations

import atexit
import ctypes
import gc
import os
import pickle
import signal
import struct
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import BinaryIO

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


def compile_forked(req: dict) -> dict | None:
    """Compile one case in a child of a compile server. None: compile it in
    this process instead."""
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
            _refused.append(f"{req['src_file']}: {reply['reason']}")
            return None
        if kind == "died":
            pytest.fail(
                f"the forked compile of {req['src_file']} died "
                f"({_describe_status(reply['status'])})\n--- stderr ---\n{reply['stderr']}",
                pytrace=False)
        return reply


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
        # Imported here: conftest imports this module.
        import conftest
        template_dir = Path(tempfile.mkdtemp(prefix="tpy-fork-template-"))
        try:
            compiler = Compiler(template_dir / "__template__.py", default_int="int32",
                                lib_dirs=list(conftest.DEFAULT_LIB_DIRS))
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


def _fork_one(compiler: Compiler, key: str, req: dict, replies_fd: int,
              libc: ctypes.CDLL) -> dict:
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
            reply = _child(compiler, key, req)
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


def _child(compiler: Compiler, key: str, req: dict) -> dict:
    import conftest
    request = conftest.CaseRequest(**req)
    os.chdir(request.cwd)
    routes = REROUTES.get(key, {})

    def on_discovered(new: list[str]) -> None:
        for name in new:
            if name in routes:
                raise _Reroute(routes[name])

    def run() -> list:
        return compiler.finish(request.src_file, reuse=True, on_discovered=on_discovered)

    reply: dict
    try:
        result = conftest.compile_case(compiler, run, request)
        reply = {"kind": "result",
                 "result": {f: getattr(result, f) for f in result.__dataclass_fields__}}
    except _Reroute as exc:
        return {"kind": "reroute", "key": exc.key}
    except LayerReuseRefused as exc:
        return {"kind": "plain", "reason": str(exc)}
    except BaseException as exc:
        reply = {"kind": "raise", "raise": _exception_record(exc)}
    reply["thir_tally"] = dict(conftest._thir_tally)
    reply["thir_faces"] = dict(conftest._thir_faces)
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
