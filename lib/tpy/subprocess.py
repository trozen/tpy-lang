# subprocess -- spawn child processes and talk to them over pipes.
# tpy: cpp_namespace("tpystd::subprocess")
"""Minimal CPython-compatible `subprocess`: `Popen` over an argument list.

`Popen(args, *, stdin=None, stdout=None, stderr=None)` spawns `args[0]`
(searched on PATH) with the current environment through posix_spawnp. Each
stream is None (inherit), PIPE, DEVNULL, an open fd, or -- for stderr only --
STDOUT. A piped stdin is an `io.BufferedWriter` (data reaches the child on
flush(), when the buffer fills, or on close()), a piped stdout / stderr an
`io.BufferedReader`. The child gets exactly the fds 0-2 it asked for and no
other descriptor of this process; the pipe ends kept here are close-on-exec.

`wait()` blocks and caches the exit code, `poll()` checks without blocking;
`returncode` is negative for death by signal. `terminate()` / `kill()` are
no-ops once the child is known to have exited. The context manager closes
the pipes and waits, in CPython's order.

Not supported: communicate(), run() and friends, text mode, cwd/env, shell
and str args, pass_fds / close_fds=False, bufsize, and reaping of Popen
objects dropped before their child exited.
"""
from typing import Final
from tpy import int64, nocopy
from io import BufferedReader, BufferedWriter, FileIO
import os
import signal
from os._native import spawn_raw as _spawn_raw


PIPE: Final[int64] = -1
STDOUT: Final[int64] = -2
DEVNULL: Final[int64] = -3


@nocopy
class Popen:
    pid: int64
    returncode: int64 | None
    stdin: BufferedWriter | None
    stdout: BufferedReader | None
    stderr: BufferedReader | None

    # The streams are keyword-only: CPython's second positional is bufsize.
    # `args` is not readonly[...] only because a list literal at a readonly
    # constructor parameter is refused (BUGS.md#readonly-container-literal-ctor-arg).
    def __init__(self, args: list[str], *, stdin: int64 | None = None,
                 stdout: int64 | None = None,
                 stderr: int64 | None = None) -> None:
        inherit: int64 = 0
        in_spec: int64 = 0
        out_spec: int64 = 0
        err_spec: int64 = 0
        if stdin is None:
            inherit |= 1
        else:
            in_spec = stdin
        if stdout is None:
            inherit |= 2
        else:
            out_spec = stdout
        if stderr is None:
            inherit |= 4
        else:
            err_spec = stderr
        spawned = _spawn_raw(args, in_spec, out_spec, err_spec, inherit)
        self.pid = spawned[0]
        self.returncode = None
        self.stdin = None
        self.stdout = None
        self.stderr = None
        if spawned[1] >= 0:
            self.stdin = BufferedWriter(FileIO(spawned[1], "wb"))
        if spawned[2] >= 0:
            self.stdout = BufferedReader(FileIO(spawned[2], "rb"))
        if spawned[3] >= 0:
            self.stderr = BufferedReader(FileIO(spawned[3], "rb"))

    def _set_status(self, status: int64) -> None:
        self.returncode = os.waitstatus_to_exitcode(status)

    def poll(self) -> int64 | None:
        if self.returncode is None:
            try:
                res = os.waitpid(self.pid, os.WNOHANG)
                if res[0] == self.pid:
                    self._set_status(res[1])
            except ChildProcessError:
                # Reaped elsewhere (or SIGCHLD ignored): the status is gone,
                # and CPython reports 0.
                self.returncode = 0
            except OSError:
                pass
        return self.returncode

    def wait(self) -> int64:
        while True:
            rc = self.returncode
            if rc is not None:
                return rc
            try:
                res = os.waitpid(self.pid, 0)
            except ChildProcessError:
                self.returncode = 0
                continue
            if res[0] == self.pid:
                self._set_status(res[1])

    def send_signal(self, sig: int64) -> None:
        # Polling first keeps a signal from reaching a recycled pid once the
        # child is known to be gone.
        if self.poll() is not None:
            return
        try:
            os.kill(self.pid, sig)
        except ProcessLookupError:
            pass

    def terminate(self) -> None:
        self.send_signal(signal.SIGTERM)

    def kill(self) -> None:
        self.send_signal(signal.SIGKILL)

    def __enter__(self) -> "Popen":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.stdout is not None:
            self.stdout.close()
        if self.stderr is not None:
            self.stderr.close()
        # Flushing stdin may raise (BrokenPipeError); the child is waited
        # for regardless, so no zombie is left behind.
        try:
            if self.stdin is not None:
                self.stdin.close()
        finally:
            self.wait()
