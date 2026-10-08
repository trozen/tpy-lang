"""Signal behavior through the real CLI, including the cached exec path, and
Ctrl-C -> KeyboardInterrupt delivery with a real SIGINT from the parent."""

import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from conftest import CPY_LIB_DIR, exec_is_cross


PROGRAM = """\
# Exercise launcher policy with the runtime's and asyncio's own SIGINT handling.
import asyncio
import signal
import sys
import time


async def handled() -> int:
    try:
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(10.0)
        return 1
    except asyncio.CancelledError:
        return 42


def main() -> None:
    mode = sys.argv[1]
    if mode == "int":
        signal.raise_signal(signal.SIGINT)
        print("survived")
    elif mode == "term":
        signal.raise_signal(signal.SIGTERM)
        print("survived")
    elif mode == "group":
        print("ready")
        sys.stdout.flush()
        while True:
            time.sleep(1.0)
    elif mode == "handled":
        print(asyncio.run(handled()))
    elif mode == "exitmsg":
        sys.exit("bye")
    elif mode == "exit256":
        sys.exit(256)
    else:
        print(sys.argv[2])
        sys.exit(7)


main()
"""

# Set disposition inside the isolated interpreter, avoiding preexec_fn in
# pytest workers and background-shell SIG_IGN contamination of the control.
LAUNCHER = """\
import signal
import sys
from tpyc.cli import main_tpy, main_tpyc

entrypoint, disposition = sys.argv[1:3]
signal.signal(signal.SIGINT, signal.SIG_IGN if disposition == "ignore"
              else signal.default_int_handler)
sys.argv = [entrypoint, *sys.argv[3:]]
sys.exit(main_tpy() if entrypoint == "tpy" else main_tpyc())
"""


def _run_cli(
    tmp_path: Path, entrypoint: str, options: list[str], mode: str,
    *, rebuild: bool, ignored: bool = False, code: str | None = None,
) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(tmp_path / "launcher.py"), entrypoint,
            "ignore" if ignored else "default", "-v", *options]
    if entrypoint == "tpyc":
        argv.append("-x")
    if rebuild:
        argv.append("--rebuild")
    # Runner options must precede the filename; later tokens are program args.
    argv.extend(["-c", code] if code is not None else ["prog.py"])
    if entrypoint == "tpyc" or code is not None:
        argv.append("--")
    argv.extend([mode, "two words --unchanged"])
    # Build progress can fill a pipe while we wait for the child's handshake.
    # Each temp dir is a fresh build tree; relativizing its paths lets
    # ccache serve the same TUs to every parametrization and every run.
    env = {**os.environ, "CCACHE_BASEDIR": str(tmp_path)}
    with tempfile.TemporaryFile(mode="w+") as stderr:
        proc = subprocess.Popen(
            argv, cwd=tmp_path, stdout=subprocess.PIPE, stderr=stderr,
            text=True, start_new_session=True, env=env,
        )
        prefix = ""
        try:
            if mode == "group":
                assert proc.stdout is not None
                with selectors.DefaultSelector() as selector:
                    selector.register(proc.stdout, selectors.EVENT_READ)
                    assert selector.select(timeout=600), "child did not become ready"
                prefix = proc.stdout.readline()
                if prefix != "ready\n":
                    stderr.seek(0)
                    pytest.fail(f"child exited before readiness: {stderr.read()}")
                os.killpg(proc.pid, signal.SIGINT)
            stdout, _ = proc.communicate(timeout=30 if mode == "group" else 600)
        except BaseException:
            # The cold launcher and native child share only this new group.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate(timeout=30)
            raise
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
        stderr.seek(0)
        return subprocess.CompletedProcess(argv, proc.returncode, prefix + stdout,
                                           stderr.read())


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
@pytest.mark.parametrize("entrypoint", ["tpyc", "tpy"])
def test_cli_signal_lifecycle(
    tmp_path: Path, request: pytest.FixtureRequest, entrypoint: str,
) -> None:
    if (exec_is_cross() or request.config.getoption("--build-only")
            or request.config.getoption("--no-exec")):
        pytest.skip("CLI signal lifecycle needs a host-runnable binary")

    (tmp_path / "prog.py").write_text(PROGRAM)
    (tmp_path / "launcher.py").write_text(LAUNCHER)
    # A per-directory PCH is baked into every TU's ccache key, which made
    # each temp dir a full cold build; without it the objects are shared.
    options = ["--cxx", request.config.getoption("--cxx"), "--no-pch"]
    if request.config.getoption("--no-ccache"):
        options.append("--no-ccache")

    # `interrupted`: the program itself reports an uncaught KeyboardInterrupt
    # (its SIGINT became one) before dying by SIGINT. `message`: a stderr line
    # the program must print (an uncaught str exit code).
    for mode, ignored, output, exitcode, interrupted, message in [
        ("int", False, "", -signal.SIGINT, True, None),
        ("term", False, "", -signal.SIGTERM, False, None),
        ("group", False, "ready\n", -signal.SIGINT, True, None),
        ("int", True, "survived\n", 0, False, None),
        ("handled", False, "42\n", 0, False, None),
        ("exit", False, "two words --unchanged\n", 7, False, None),
        ("exitmsg", False, "", 1, False, "bye"),
        # The OS keeps the low 8 bits of the status, as under CPython.
        ("exit256", False, "", 0, False, None),
    ]:
        for cold in (True, False):
            # Rebuild exercises the subprocess route with the same binary;
            # the next invocation must actually take the cached exec route.
            result = _run_cli(tmp_path, entrypoint, options, mode,
                              rebuild=cold, ignored=ignored)
            context = f"{entrypoint} {mode} cold={cold}: {result.stderr}"
            expected = 128 - exitcode if cold and exitcode < 0 else exitcode
            assert result.returncode == expected, context
            assert result.stdout == output, context
            assert ("analyzed" in result.stderr) == cold, context
            assert ("cached:" in result.stderr) != cold, context
            assert ("KeyboardInterrupt" in result.stderr.splitlines()) == interrupted, context
            if message is not None:
                assert message in result.stderr.splitlines(), context
            assert "panic" not in result.stderr, context
            assert "Traceback" not in result.stderr, context

    # Command strings have no file cache and reach the same waiting launcher.
    result = _run_cli(
        tmp_path, entrypoint, options, "int", rebuild=False,
        code="import signal\nsignal.raise_signal(signal.SIGINT)\nprint('survived')\n",
    )
    assert result.returncode == 128 + signal.SIGINT, result.stderr
    assert result.stdout == "", result.stderr
    assert "analyzed" in result.stderr, result.stderr
    assert "KeyboardInterrupt" in result.stderr.splitlines(), result.stderr
    assert "Traceback" not in result.stderr, result.stderr


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX signals")
def test_sigint_during_build(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """A Ctrl-C while the C++ compiles run ends the driver the way CPython
    ends on an uncaught KeyboardInterrupt (by SIGINT, no traceback) and
    records no build manifest. That queued compiles are dropped is pinned
    without a toolchain in tpyc/test_cli_signals.py."""
    if request.config.getoption("--no-exec"):
        pytest.skip("needs a host build")
    # No ccache: the compiles must still be running when the signal lands.
    (tmp_path / "prog.py").write_text("print(1)\n")
    (tmp_path / "launcher.py").write_text(LAUNCHER)
    argv = [sys.executable, str(tmp_path / "launcher.py"), "tpyc", "default",
            "-b", "-v", "--no-pch", "--no-ccache", "-j", "2",
            "--cxx", request.config.getoption("--cxx"), "prog.py", "-o", "out"]
    proc = subprocess.Popen(argv, cwd=tmp_path, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, bufsize=1,
                            start_new_session=True)
    assert proc.stderr is not None
    lines: list[str] = []
    deadline = time.monotonic() + 300
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stderr, selectors.EVENT_READ)
            while True:
                left = deadline - time.monotonic()
                assert left > 0 and selector.select(left), (
                    "no compile finished in time: " + "".join(lines))
                line = proc.stderr.readline()
                assert line, "the build ended before a compile finished: " + "".join(lines)
                lines.append(line)
                if line.startswith("  compiled "):
                    break
        proc.send_signal(signal.SIGINT)
        _, rest = proc.communicate(timeout=120)
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=30)
        raise
    out = "".join(lines) + rest
    assert proc.returncode == -signal.SIGINT, out
    assert "Traceback" not in out, out
    assert "tpyc: interrupted" in out.splitlines(), out
    # The premise: the parallel pool ran, and the signal landed before the link.
    compiles = [ln for ln in out.splitlines() if ln.startswith("  $ ") and " -c " in ln]
    commands = [ln for ln in out.splitlines() if ln.startswith("  $ ")]
    assert len(compiles) >= 3 and len(commands) == len(compiles), out
    # The CLI goes through the cancelling pool: a plain pool's exit would start
    # every queued compile. Exact counts race (tpyc/test_cli_signals.py pins
    # the cancel), but dropping none needs the whole queue to finish between
    # the first report and the signal.
    objects = list((tmp_path / "out").rglob("*.o"))
    assert len(objects) < len(compiles), (objects, out)
    assert not list((tmp_path / "out").rglob("build-manifest.json"))


# Ctrl-C scenarios for the runtime's SIGINT layer. The parent sends a real
# SIGINT after each readiness line, so the signal lands while the program is
# blocked in the operation under test (or spinning, for "cpu").
INTERRUPT_PROGRAM = """\
import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from tpy import int32, int64
from tpy.thread import spawn


def ready(tag: str) -> None:
    print(tag)
    sys.stdout.flush()


class Sleeper:
    secs: float

    def __init__(self, secs: float) -> None:
        self.secs = secs

    def run(self) -> int32:
        time.sleep(self.secs)
        return 7


def mode_sleep() -> None:
    try:
        ready("ready")
        time.sleep(30.0)
        print("slept (WRONG)")
    except KeyboardInterrupt:
        print("caught")
    finally:
        print("finally")


def mode_input() -> None:
    ready("ready")
    while True:
        try:
            line = input()
            print("line:" + line)
        except KeyboardInterrupt:
            ready("interrupted")
        except EOFError:
            print("eof")
            break


def mode_socket() -> None:
    a, b = socket.socketpair()
    try:
        ready("ready")
        a.recv(16)
        print("recv returned (WRONG)")
    except KeyboardInterrupt:
        print("recv interrupted")
    # The interrupted socket is still usable in blocking mode.
    b.sendall(b"xy")
    print("recv after:", len(a.recv(16)))
    srv = socket.create_server(("127.0.0.1", 0))
    try:
        ready("ready2")
        conn, peer = srv.accept()
        print("accept returned (WRONG)")
    except KeyboardInterrupt:
        print("accept interrupted")
    print("blocking:", srv.getblocking())


def mode_join() -> None:
    h = spawn(Sleeper(2.0))
    try:
        ready("ready")
        h.join()
        print("joined early (WRONG)")
    except KeyboardInterrupt:
        print("join interrupted")
    # The interrupted join left the handle unconsumed: join again.
    print("result:", h.join())


def leaves_handle() -> None:
    h = spawn(Sleeper(30.0))
    ready("ready")
    time.sleep(30.0)
    h.detach()


def mode_unjoined() -> None:
    try:
        leaves_handle()
    except KeyboardInterrupt:
        print("caught")
        raise


def spin(seconds: float) -> None:
    # No check point in here: a Ctrl-C landing now stays pending.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pass


def mode_inbuf() -> None:
    print("first:", input())
    ready("ready")
    spin(1.0)
    try:
        # the rest of stdin already sits in stdio's buffer; the pending
        # Ctrl-C raises before the prompt is written
        line = input("prompt> ")
        print("second (WRONG):", line)
    except KeyboardInterrupt:
        print("interrupted")
    # the buffered line the interrupted call did not take
    print("third:", input())


def mode_pending() -> None:
    a, b = socket.socketpair()
    b.sendall(b"x")
    ready("ready")
    spin(1.0)
    try:
        a.recv(16)  # data is ready, so the recv never waits
        print("recv returned (WRONG)")
    except KeyboardInterrupt:
        print("recv: pending interrupt delivered")
    print("recv after:", len(a.recv(16)))
    ready("ready2")
    spin(1.0)
    try:
        time.sleep(0)
        print("sleep returned (WRONG)")
    except KeyboardInterrupt:
        print("sleep(0): pending interrupt delivered")


def mode_pending_send() -> None:
    a, b = socket.socketpair()
    ready("ready")
    spin(1.0)
    try:
        a.send(b"x")  # the send completes, then the Ctrl-C is delivered
        print("send returned (WRONG)")
    except KeyboardInterrupt:
        print("send: pending interrupt delivered")
    print("send after:", len(b.recv(16)))
    ready("ready2")
    spin(1.0)
    try:
        a.sendall(b"yz")
        print("sendall returned (WRONG)")
    except KeyboardInterrupt:
        print("sendall: pending interrupt delivered")
    print("sendall after:", len(b.recv(16)))


def mode_pending_connect() -> None:
    srv = socket.create_server(("127.0.0.1", 0))
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ready("ready")
    spin(1.0)
    try:
        c.connect(srv.getsockname())  # loopback: completes without waiting
        print("connect returned (WRONG)")
    except KeyboardInterrupt:
        print("connect: pending interrupt delivered")
    conn, peer = srv.accept()
    print("connected:", c.getpeername() == srv.getsockname())


def mode_join_pending() -> None:
    h = spawn(Sleeper(0.1))
    ready("ready")
    spin(1.0)  # the worker finishes meanwhile
    try:
        h.join()
        print("joined (WRONG)")
    except KeyboardInterrupt:
        print("join: pending interrupt delivered")
    print("result:", h.join())


class SubInterrupt(KeyboardInterrupt):
    pass


def mode_raise_sub() -> None:
    raise SubInterrupt("sub")


def mode_raise_msg() -> None:
    raise KeyboardInterrupt("why")


def mode_raise_type_msg() -> None:
    raise KeyboardInterrupt("KeyboardInterrupt")


def mode_cpu() -> None:
    deadline = time.monotonic() + 20.0
    n = 0
    ready("ready")
    while time.monotonic() < deadline:
        n += 1
    print("loop ended (WRONG)", n > 0)


def mode_print() -> None:
    # No ready(): its flush is itself a check point. The loop's own lines are
    # the handshake, so the one Ctrl-C lands between two prints and the next
    # print raises it.
    n = 0
    try:
        while n < 5000000:
            print("line", n)
            n += 1
        print("loop ended (WRONG)")
    except KeyboardInterrupt:
        print("print: caught", n > 0)


class SlowDel:
    def __del__(self) -> None:
        # A Ctrl-C landing in here is deferred: the sleep runs out.
        time.sleep(0.5)
        print("del: slept")


def drop_slow() -> None:
    ready("ready")
    s = SlowDel()


def mode_del_sleep() -> None:
    t0 = time.monotonic()
    try:
        drop_slow()
        print("del: after")
        print("del: not reached (WRONG)")
    except KeyboardInterrupt:
        print("del: caught", time.monotonic() - t0 >= 0.5)


# The children's unused streams go to /dev/null: a child left behind by a
# failing run must not hold the test's own pipes open.
def mode_popen_wait() -> None:
    p = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        ready("ready")
        p.wait()
        print("wait returned (WRONG)")
    except KeyboardInterrupt:
        print("wait interrupted", p.returncode is None)
    # Only this process got the signal: the child still runs and is waited
    # for again.
    p.kill()
    print("after kill:", p.wait())


def mode_pipe_read() -> None:
    # The child writes two bytes and then holds its end of the pipe open. Its
    # byte on stderr comes after them, so once that is read the two are in
    # the pipe and the read below takes them before it blocks.
    p = subprocess.Popen(["sh", "-c", "printf ab; printf r >&2; exec sleep 30"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert p.stdout is not None
    assert p.stderr is not None
    p.stderr.read(1)
    try:
        ready("ready")
        p.stdout.read(4)
        print("read returned (WRONG)")
    except KeyboardInterrupt:
        print("read interrupted")
    # The bytes read before the Ctrl-C were not dropped with it.
    print("kept:", p.stdout.read(2))
    p.kill()
    print("after kill:", p.wait())


def mode_pipe_write() -> None:
    # The child never reads its stdin, so the pipe fills up.
    p = subprocess.Popen(["sleep", "30"], stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert p.stdin is not None
    try:
        ready("ready")
        p.stdin.write(b"x" * 1000000)
        print("write returned (WRONG)")
    except KeyboardInterrupt:
        print("write interrupted")
    p.kill()
    print("after kill:", p.wait())


def mode_popen_with() -> None:
    t0 = time.monotonic()
    pid: int64 = 0
    # Bound first: a list literal argument in a `with` item is refused
    # (BUGS.md#ctor-call-arg-temp-flush-positions).
    cmd = ["sleep", "30"]
    try:
        with subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as p:
            pid = p.pid
            ready("ready")
            p.wait()
            print("wait returned (WRONG)")
    except KeyboardInterrupt:
        # Leaving the block waited a quarter second for the child, not the
        # 30 it has left.
        waited = time.monotonic() - t0
        print("with interrupted", waited >= 0.25, waited < 10.0)
    # The child outlived the block; it is reaped here.
    os.kill(pid, signal.SIGKILL)
    res = os.waitpid(pid, 0)
    print("reaped:", os.waitstatus_to_exitcode(res[1]))


def mode_uncaught() -> None:
    ready("ready")
    print("buffered")
    time.sleep(30.0)


async def hung_cleanup() -> None:
    try:
        ready("ready")
        await asyncio.sleep(30.0)
    finally:
        ready("cleanup started")
        await asyncio.sleep(30.0)
        print("cleanup finished (WRONG)")


def mode_async_twice() -> None:
    try:
        asyncio.run(hung_cleanup())
    except KeyboardInterrupt:
        print("KeyboardInterrupt out of run")


async def hung_cleanup_outer() -> None:
    try:
        ready("ready")
        await asyncio.sleep(30.0)
    finally:
        try:
            ready("cleanup started")
            await asyncio.sleep(30.0)
            print("cleanup finished (WRONG)")
        finally:
            print("outer cleanup")


def mode_async_twice_outer() -> None:
    try:
        asyncio.run(hung_cleanup_outer())
    except KeyboardInterrupt:
        print("KeyboardInterrupt out of run")


class Resource:
    async def __aenter__(self) -> None:
        print("aenter")

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        print("aexit")


async def serve_with() -> None:
    async with Resource():
        ready("ready")
        await asyncio.sleep(30.0)
        print("body finished (WRONG)")


def mode_async_with() -> None:
    try:
        asyncio.run(serve_with())
    except KeyboardInterrupt:
        print("KeyboardInterrupt")


async def background() -> None:
    try:
        await asyncio.sleep(30.0)
    finally:
        print("background cleanup")


async def serve_spawned() -> None:
    task = asyncio.create_task(background())
    ready("ready")
    await asyncio.sleep(30.0)
    await task


def mode_async_spawned() -> None:
    try:
        asyncio.run(serve_spawned())
    except KeyboardInterrupt:
        print("KeyboardInterrupt")


async def self_signal() -> int32:
    signal.raise_signal(signal.SIGINT)
    await asyncio.sleep(0.2)
    return 1


def mode_ignored() -> None:
    signal.raise_signal(signal.SIGINT)
    print("survived")
    print("async:", asyncio.run(self_signal()))


def main() -> None:
    mode = sys.argv[1]
    if mode == "sleep":
        mode_sleep()
    elif mode == "input":
        mode_input()
    elif mode == "socket":
        mode_socket()
    elif mode == "join":
        mode_join()
    elif mode == "unjoined":
        mode_unjoined()
    elif mode == "inbuf":
        mode_inbuf()
    elif mode == "pending":
        mode_pending()
    elif mode == "pending_send":
        mode_pending_send()
    elif mode == "pending_connect":
        mode_pending_connect()
    elif mode == "join_pending":
        mode_join_pending()
    elif mode == "raise_sub":
        mode_raise_sub()
    elif mode == "raise_msg":
        mode_raise_msg()
    elif mode == "raise_type_msg":
        mode_raise_type_msg()
    elif mode == "cpu":
        mode_cpu()
    elif mode == "print":
        mode_print()
    elif mode == "del_sleep":
        mode_del_sleep()
    elif mode == "popen_wait":
        mode_popen_wait()
    elif mode == "pipe_read":
        mode_pipe_read()
    elif mode == "pipe_write":
        mode_pipe_write()
    elif mode == "popen_with":
        mode_popen_with()
    elif mode == "uncaught":
        mode_uncaught()
    elif mode == "async_twice":
        mode_async_twice()
    elif mode == "async_twice_outer":
        mode_async_twice_outer()
    elif mode == "async_with":
        mode_async_with()
    elif mode == "async_spawned":
        mode_async_spawned()
    elif mode == "ignored":
        mode_ignored()


main()
"""

# Starts the binary with SIGINT ignored, set inside an isolated interpreter
# (no preexec_fn in pytest workers), the way a shell starts a background job.
IGNORING_EXEC = ("import os, signal, sys\n"
                 "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                 "os.execv(sys.argv[1], sys.argv[1:])\n")


def _build_signal_program(work: Path, source: str,
                          request: pytest.FixtureRequest,
                          options: tuple[str, ...] = ()) -> Path:
    """Build `source` through the real CLI in `work`; the binary's path."""
    if os.name != "posix":
        pytest.skip("requires POSIX signals")
    if (exec_is_cross() or request.config.getoption("--build-only")
            or request.config.getoption("--no-exec")):
        pytest.skip("SIGINT delivery needs a host-runnable binary")
    (work / "prog.py").write_text(source)
    argv = [sys.executable, "-c",
            "import sys\nfrom tpyc.cli import main_tpyc\n"
            "sys.argv = ['tpyc', *sys.argv[1:]]\nsys.exit(main_tpyc())\n",
            "-b", "-q", "prog.py", "-o", "out",
            "--cxx", request.config.getoption("--cxx"), "--no-pch", *options]
    if request.config.getoption("--no-ccache"):
        argv.append("--no-ccache")
    env = {**os.environ, "CCACHE_BASEDIR": str(work)}
    result = subprocess.run(argv, cwd=work, env=env, capture_output=True,
                            text=True, timeout=600)
    binary = work / "out" / "release" / "prog"
    assert result.returncode == 0 and binary.exists(), result.stdout + result.stderr
    return binary


@pytest.fixture(scope="module")
def interrupt_binary(tmp_path_factory: pytest.TempPathFactory,
                     request: pytest.FixtureRequest) -> Path:
    return _build_signal_program(tmp_path_factory.mktemp("sigint"),
                                 INTERRUPT_PROGRAM, request)


class _Child:
    """One run of the interrupt program with line-level stdout handshakes."""

    def __init__(self, program: Path | list[str], mode: str, *,
                 stdin_pipe: bool = False, sigint_ignored: bool = False,
                 env: dict[str, str] | None = None) -> None:
        argv = [*program, mode] if isinstance(program, list) else [str(program), mode]
        if sigint_ignored:
            argv = [sys.executable, "-c", IGNORING_EXEC, *argv]
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            start_new_session=True, env=env)
        self.out = b""
        self._pending = b""

    def wait_line(self, expected: str, timeout: float = 30.0) -> None:
        """Read stdout up to and including the line `expected`."""
        assert self.proc.stdout is not None
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.proc.stdout, selectors.EVENT_READ)
            while True:
                line, sep, rest = self._pending.partition(b"\n")
                if sep:
                    self._pending = rest
                    self.out += line + sep
                    if line.decode() == expected:
                        return
                    continue
                left = deadline - time.monotonic()
                assert left > 0 and selector.select(left), (
                    f"no {expected!r} line; stdout so far: {self.out!r}")
                chunk = os.read(self.proc.stdout.fileno(), 4096)
                assert chunk, f"EOF before {expected!r}; stdout: {self.out!r}"
                self._pending += chunk

    def interrupt(self) -> None:
        # Give the program time to block in the operation under test; a
        # SIGINT landing earlier would still be delivered, just not mid-wait.
        time.sleep(0.2)
        self.proc.send_signal(signal.SIGINT)

    def finish(self, stdin: bytes | None = None) -> tuple[int, str, str]:
        try:
            out, err = self.proc.communicate(input=stdin, timeout=30)
        except BaseException:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.communicate(timeout=30)
            raise
        return (self.proc.returncode, (self.out + self._pending + out).decode(),
                err.decode())


@pytest.mark.parametrize("mode, handshakes, stdout", [
    # finally runs, the program continues
    ("sleep", ["ready"], "ready\ncaught\nfinally\n"),
    # the socket works afterwards; the listener stays blocking
    ("socket", ["ready", "ready2"],
     "ready\nrecv interrupted\nrecv after: 2\nready2\naccept interrupted\n"
     "blocking: True\n"),
    # join() is interruptible and the handle stays joinable
    ("join", ["ready"], "ready\njoin interrupted\nresult: 7\n"),
    # a Ctrl-C left pending by a CPU loop is delivered by an operation that
    # completes without waiting (a recv with data ready, a zero sleep)
    ("pending", ["ready", "ready2"],
     "ready\nrecv: pending interrupt delivered\nrecv after: 1\nready2\n"
     "sleep(0): pending interrupt delivered\n"),
    # ... and after a send / sendall that completed (the data went out)
    ("pending_send", ["ready", "ready2"],
     "ready\nsend: pending interrupt delivered\nsend after: 1\nready2\n"
     "sendall: pending interrupt delivered\nsendall after: 2\n"),
    # ... and after a loopback connect that completed (it stays connected)
    ("pending_connect", ["ready"],
     "ready\nconnect: pending interrupt delivered\nconnected: True\n"),
    # join()'s final check: the worker already finished, the handle stays
    # joinable
    ("join_pending", ["ready"],
     "ready\njoin: pending interrupt delivered\nresult: 7\n"),
    # a second Ctrl-C cancels the root again, so a hung cleanup await ends
    ("async_twice", ["ready", "cleanup started"],
     "ready\ncleanup started\nKeyboardInterrupt out of run\n"),
    # ... and the cleanup around that await still runs, as CPython's
    # Runner.close cancels every unfinished task once more
    ("async_twice_outer", ["ready", "cleanup started"],
     "ready\ncleanup started\nouter cleanup\nKeyboardInterrupt out of run\n"),
    # cancelling the root runs __aexit__ on the way out
    ("async_with", ["ready"], "aenter\nready\naexit\nKeyboardInterrupt\n"),
    # a live spawned task is cancelled and cleaned up too
    ("async_spawned", ["ready"], "ready\nbackground cleanup\nKeyboardInterrupt\n"),
    # a Ctrl-C during a __del__'s sleep is deferred: the sleep and the
    # destructor complete, and the next print after it raises
    ("del_sleep", ["ready"], "ready\ndel: slept\ndel: after\ndel: caught True\n"),
    # waiting for a child ends on the Ctrl-C; the child is untouched and the
    # Popen still waits for it afterwards
    ("popen_wait", ["ready"],
     "ready\nwait interrupted True\nafter kill: -9\n"),
    # a read blocked on a subprocess pipe, keeping what it had read
    ("pipe_read", ["ready"],
     "ready\nread interrupted\nkept: b'ab'\nafter kill: -9\n"),
    # a write blocked on a full subprocess pipe
    ("pipe_write", ["ready"], "ready\nwrite interrupted\nafter kill: -9\n"),
    # an interrupted with-block waits a quarter second for the child, then
    # lets the KeyboardInterrupt out; the child is left running
    ("popen_with", ["ready"], "ready\nwith interrupted True True\nreaped: -9\n"),
])
def test_sigint_raises_keyboard_interrupt(
    interrupt_binary: Path, mode: str, handshakes: list[str], stdout: str,
) -> None:
    child = _Child(interrupt_binary, mode)
    for tag in handshakes:
        child.wait_line(tag)
        child.interrupt()
    returncode, out, err = child.finish()
    assert (returncode, out, err) == (0, stdout, "")


def test_interrupted_join_retry_under_cpython(
    tmp_path: Path, request: pytest.FixtureRequest,
) -> None:
    # Guards lib/cpy/tpy/thread.py: its join() must also stay retryable after
    # a KeyboardInterrupt, or the cpy phase of a threading case drifts from
    # the TPy runtime.
    if os.name != "posix":
        pytest.skip("requires POSIX signals")
    if request.config.getoption("--no-cpy"):
        pytest.skip("the CPython phase is disabled")
    prog = tmp_path / "prog.py"
    prog.write_text(INTERRUPT_PROGRAM)
    env = {**os.environ, "PYTHONPATH": f"{CPY_LIB_DIR}{os.pathsep}{tmp_path}"}
    child = _Child([sys.executable, str(prog)], "join", env=env)
    child.wait_line("ready")
    child.interrupt()
    returncode, out, err = child.finish()
    assert (returncode, out, err) == (0, "ready\njoin interrupted\nresult: 7\n", "")


@pytest.mark.parametrize("rest, lines", [
    ("def\nsecond\n", "line:def\nline:second\n"),
    # EOF right after the interrupted line: nothing of it comes back
    ("", ""),
])
def test_sigint_during_input_discards_partial_line(
    interrupt_binary: Path, rest: str, lines: str,
) -> None:
    child = _Child(interrupt_binary, "input", stdin_pipe=True)
    child.wait_line("ready")
    assert child.proc.stdin is not None
    # A partial line is pending when the Ctrl-C lands; like CPython's input(),
    # the interrupted call drops it and the next one starts after it.
    child.proc.stdin.write(b"abc")
    child.proc.stdin.flush()
    child.interrupt()
    child.wait_line("interrupted")
    returncode, out, err = child.finish(stdin=rest.encode())
    assert (returncode, out, err) == (
        0, "ready\ninterrupted\n" + lines + "eof\n", "")


def test_sigint_before_input_keeps_buffered_line(interrupt_binary: Path) -> None:
    child = _Child(interrupt_binary, "inbuf", stdin_pipe=True)
    assert child.proc.stdin is not None
    # All three lines arrive at once, so stdio reads the rest ahead with the
    # first. Like CPython, a Ctrl-C pending at the next input() raises before
    # that call writes its prompt or takes a buffered line, so no "prompt> "
    # appears and the input() after it gets "two".
    child.proc.stdin.write(b"one\ntwo\nthree\n")
    child.proc.stdin.flush()
    child.wait_line("ready")
    child.interrupt()
    returncode, out, err = child.finish()
    assert (returncode, out, err) == (
        0, "first: one\nready\ninterrupted\nthird: two\n", "")


@pytest.mark.parametrize("mode, stdout", [
    # buffered output is flushed before the process dies
    ("uncaught", "ready\nbuffered\n"),
    # an unjoined JoinHandle dropped while the interrupt unwinds detaches
    # instead of panicking
    ("unjoined", "ready\ncaught\n"),
])
def test_uncaught_keyboard_interrupt_dies_by_sigint(
    interrupt_binary: Path, mode: str, stdout: str,
) -> None:
    child = _Child(interrupt_binary, mode)
    child.wait_line("ready")
    child.interrupt()
    returncode, out, err = child.finish()
    # Killed by SIGINT, which a shell reports as status 130, like CPython.
    assert (returncode, out, err) == (-signal.SIGINT, stdout, "KeyboardInterrupt\n")


def test_uncaught_keyboard_interrupt_report(interrupt_binary: Path) -> None:
    # A message is printed after the type, as CPython does, and the process
    # still dies by SIGINT.
    returncode, out, err = _Child(interrupt_binary, "raise_msg").finish()
    assert (returncode, out, err) == (
        -signal.SIGINT, "", "KeyboardInterrupt: why\n")
    # Only an empty message is left out, so the type's own name as the message
    # is still printed.
    returncode, out, err = _Child(interrupt_binary, "raise_type_msg").finish()
    assert (returncode, out, err) == (
        -signal.SIGINT, "", "KeyboardInterrupt: KeyboardInterrupt\n")
    # A subclass is an ordinary uncaught exception (CPython exits 1 too).
    returncode, out, err = _Child(interrupt_binary, "raise_sub").finish()
    assert (returncode, out) == (1, ""), err
    assert err.startswith("TurboPython panic: uncaught "), err
    assert err.endswith("SubInterrupt: sub\n"), err


def test_sigint_raised_by_print(interrupt_binary: Path) -> None:
    # A loop whose only operation is print() stops on the FIRST Ctrl-C: the
    # chain's trailing check raises once the line being written is out.
    child = _Child(interrupt_binary, "print")
    child.wait_line("line 2000")
    child.interrupt()
    returncode, out, err = child.finish()
    assert (returncode, err) == (0, ""), (returncode, err)
    assert out.endswith("print: caught True\n"), out[-200:]
    assert "WRONG" not in out


def test_second_sigint_kills_a_cpu_loop(interrupt_binary: Path) -> None:
    child = _Child(interrupt_binary, "cpu")
    child.wait_line("ready")
    child.interrupt()
    # A loop with no interruptible operation keeps the first Ctrl-C pending.
    time.sleep(0.5)
    assert child.proc.poll() is None
    child.interrupt()
    returncode, out, err = child.finish()
    assert (returncode, out, err) == (-signal.SIGINT, "ready\n", "")


def test_inherited_sigint_ignore_is_kept(interrupt_binary: Path) -> None:
    # Neither the synchronous layer nor asyncio.run installs a handler over an
    # inherited SIG_IGN, like CPython.
    returncode, out, err = _Child(interrupt_binary, "ignored",
                                  sigint_ignored=True).finish()
    assert (returncode, out, err) == (0, "survived\nasync: 1\n", "")


# `--no-signals`: the Ctrl-C layer compiled out. "run" passes every kind of
# check point once, so the opted-out stdlib composition is shown to work; the
# other modes show that no signal becomes a KeyboardInterrupt.
NO_SIGNALS_PROGRAM = """\
import asyncio
import signal
import socket
import subprocess
import sys
import time
from typing import Iterator
from tpy import int32
from tpy.thread import spawn


class Res:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def __del__(self) -> None:
        # A cleanup body with a check point: its deferral scope is empty here.
        print("del", self.name)


def gen() -> Iterator[int32]:
    try:
        yield 1
        yield 2
    finally:
        print("gen cleanup")


class Work:
    def run(self) -> int32:
        time.sleep(0.01)
        return 7


async def amain() -> int32:
    await asyncio.sleep(0.01)
    return 5


def mode_run() -> None:
    r = Res("a")
    for v in gen():
        print(v)
        break
    h = spawn(Work())
    print("join", h.join())
    print("async", asyncio.run(amain()))
    a, b = socket.socketpair()
    a.sendall(b"hi")
    print(b.recv(2))
    sys.stdout.write("w\\n")
    time.sleep(0)
    line = input("? ")
    print("in", line)
    # No Ctrl-C can be delivered here: the pipes and wait() are the plain
    # system calls.
    p = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert p.stdin is not None
    assert p.stdout is not None
    p.stdin.write(b"piped")
    p.stdin.close()
    print("cat", p.stdout.read(), p.wait())
    print("done", r.name)


def mode_sleep() -> None:
    try:
        print("ready")
        sys.stdout.flush()
        time.sleep(30.0)
        print("slept (WRONG)")
    except KeyboardInterrupt:
        print("caught")
    finally:
        print("finally")


def mode_raise() -> None:
    try:
        signal.raise_signal(signal.SIGINT)
        print("survived")
    except KeyboardInterrupt:
        print("caught")


def mode_raise_msg() -> None:
    raise KeyboardInterrupt("why")


async def wait_forever() -> None:
    ev = asyncio.Event()
    print("waiting")
    await ev.wait()


def mode_deadlock() -> None:
    try:
        asyncio.run(wait_forever())
        print("returned (WRONG)")
    except RuntimeError:
        print("deadlock reported")


def main() -> None:
    mode = sys.argv[1]
    if mode == "run":
        mode_run()
    elif mode == "deadlock":
        mode_deadlock()
    elif mode == "sleep":
        mode_sleep()
    elif mode == "raise":
        mode_raise()
    elif mode == "raise_msg":
        mode_raise_msg()


main()
"""


# What the two builds of one source disagree on.
RAISE_PROGRAM = """\
import signal


def main() -> None:
    try:
        signal.raise_signal(signal.SIGINT)
        print("survived")
    except KeyboardInterrupt:
        print("caught")


main()
"""


@pytest.fixture(scope="module")
def no_signals_binary(tmp_path_factory: pytest.TempPathFactory,
                      request: pytest.FixtureRequest) -> Path:
    return _build_signal_program(tmp_path_factory.mktemp("nosignals"),
                                 NO_SIGNALS_PROGRAM, request, ("--no-signals",))


def test_no_signals_program_runs(no_signals_binary: Path) -> None:
    child = _Child(no_signals_binary, "run", stdin_pipe=True)
    returncode, out, err = child.finish(stdin=b"typed\n")
    assert (returncode, out, err) == (
        0, "1\ngen cleanup\njoin 7\nasync 5\nb'hi'\nw\n? in typed\n"
        "cat b'piped' 0\ndone a\ndel a\n", "")


def test_no_signals_reaches_sources_cmake(no_signals_binary: Path) -> None:
    # A host that builds the generated C++ itself reads the define from here.
    cmake = (no_signals_binary.parent.parent / "sources.cmake").read_text()
    assert "set(TPYC_COMPILE_DEFINITIONS\n    TPY_NO_SIGNALS\n)\n" in cmake


def test_no_signals_sigint_terminates(no_signals_binary: Path) -> None:
    # No handler was installed: the SIGINT's default action ends the process
    # in the sleep, with no KeyboardInterrupt, `except` or `finally`.
    child = _Child(no_signals_binary, "sleep")
    child.wait_line("ready")
    child.interrupt()
    returncode, out, err = child.finish()
    assert (returncode, out, err) == (-signal.SIGINT, "ready\n", "")
    # raise_signal follows the process disposition too.
    returncode, out, err = _Child(no_signals_binary, "raise").finish()
    assert (returncode, out, err) == (-signal.SIGINT, "", "")


def test_no_signals_keeps_explicit_keyboard_interrupt(
    no_signals_binary: Path,
) -> None:
    # A KeyboardInterrupt the program raises itself is untouched by the option,
    # the uncaught report and the exit by SIGINT included.
    returncode, out, err = _Child(no_signals_binary, "raise_msg").finish()
    assert (returncode, out, err) == (
        -signal.SIGINT, "", "KeyboardInterrupt: why\n")


def test_no_signals_asyncio_reports_a_deadlock(no_signals_binary: Path) -> None:
    # With no SIGINT to wait for, a run whose tasks all wait on nothing can
    # never be woken: the reactor's no-progress guard raises instead of
    # blocking (a default build keeps waiting for a Ctrl-C here).
    returncode, out, err = _Child(no_signals_binary, "deadlock").finish()
    assert (returncode, out, err) == (0, "waiting\ndeadlock reported\n", "")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_no_signals_keys_the_build_cache(
    tmp_path: Path, request: pytest.FixtureRequest,
) -> None:
    # The flag changes no generated file, only the C++ build: a cached binary
    # of the other mode must not be reused.
    if (exec_is_cross() or request.config.getoption("--build-only")
            or request.config.getoption("--no-exec")):
        pytest.skip("needs a host-runnable binary")
    (tmp_path / "prog.py").write_text(RAISE_PROGRAM)
    (tmp_path / "launcher.py").write_text(LAUNCHER)
    options = ["--cxx", request.config.getoption("--cxx"), "--no-pch"]
    if request.config.getoption("--no-ccache"):
        options.append("--no-ccache")
    for flags, cold, returncode, stdout in [
        ([], True, 0, "caught\n"),
        (["--no-signals"], True, 128 + signal.SIGINT, ""),
        (["--no-signals"], False, -signal.SIGINT, ""),
        ([], True, 0, "caught\n"),
    ]:
        result = _run_cli(tmp_path, "tpyc", options + flags, "raise",
                          rebuild=False)
        context = f"{flags} cold={cold}: {result.stderr}"
        assert result.returncode == returncode, context
        assert result.stdout == stdout, context
        assert ("analyzed" in result.stderr) == cold, context
        assert ("cached:" in result.stderr) != cold, context


def test_no_signals_does_not_apply_to_the_repl(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys\nfrom tpyc.cli import main_tpy\n"
         "sys.argv = ['tpy', '-i', '--no-signals']\nsys.exit(main_tpy())\n"],
        cwd=tmp_path, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        timeout=120)
    assert result.returncode == 2, result.stderr
    assert "--no-signals does not apply to the REPL" in result.stderr
