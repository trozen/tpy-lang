# subprocess.Popen: pipes, exit codes and signal deaths, poll/wait, the
# context manager, spawn errors, fds 0-2; plus os.kill / waitpid / statuses.
import os
import signal
import time
from subprocess import Popen, PIPE, STDOUT, DEVNULL


def cat_roundtrip() -> None:
    p = Popen(["cat"], stdin=PIPE, stdout=PIPE)
    # The asserts throughout narrow the streams: a method call on an unproven
    # Optional field is refused
    # (BUGS.md#unproven-optional-field-method-recv-rejects).
    assert p.stdin is not None
    assert p.stdout is not None
    # Buffered: write() reports the whole payload, close() flushes it.
    print("cat:", p.stdin.write(b"hello\nworld\n"))  # tpyc: ok
    p.stdin.close()
    print("cat:", p.stdout.readline())
    print("cat:", p.stdout.read())
    print("cat:", p.wait())


def exit_code() -> None:
    p = Popen(["sh", "-c", "exit 3"])
    print("exit:", p.wait(), p.returncode)


def signal_death() -> None:
    p = Popen(["sleep", "30"])
    print("signal:", p.poll())
    p.kill()
    # Death by signal N is returncode -N.
    print("signal:", p.wait())
    q = Popen(["sleep", "30"])
    q.terminate()
    print("signal:", q.wait())


def caching() -> None:
    p = Popen(["true"])
    rc = p.poll()
    tries = 0
    while rc is None and tries < 2000:
        time.sleep(0.005)
        rc = p.poll()
        tries += 1
    print("cache:", rc)
    # The reaped status is cached: wait() and poll() return it again, and
    # signalling an exited child is a no-op.
    print("cache:", p.wait(), p.poll())
    p.terminate()
    p.kill()
    print("cache:", p.returncode)


def with_block() -> None:
    # Bound first: a list literal argument in a `with` item is refused
    # (BUGS.md#ctor-call-arg-temp-flush-positions).
    cmd = ["cat"]
    with Popen(cmd, stdin=PIPE, stdout=PIPE) as p:
        assert p.stdin is not None
        p.stdin.write(b"x")
    # __exit__ closes stdout before stdin: the flush of b"x" happens with no
    # reader left on cat's stdout, so cat dies of SIGPIPE.
    assert p.stdin is not None
    assert p.stdout is not None
    print("with:", p.returncode, p.stdin.closed, p.stdout.closed)  # tpyc: ok


def missing_program() -> None:
    try:
        Popen(["tpy-no-such-program-xyz"])
        print("missing: spawned")
    except FileNotFoundError as e:
        print("missing:", e.errno, e.filename)
        print("missing:", str(e))


def merge_and_devnull() -> None:
    # One sh serializes both writes, so the merged order is fixed.
    p = Popen(["sh", "-c", "echo out; echo err >&2"], stdout=PIPE,
              stderr=STDOUT)
    assert p.stdout is not None
    print("merge:", p.stdout.read(), p.stderr is None)
    print("merge:", p.wait())
    q = Popen(["sh", "-c", "echo hidden; echo err2 >&2"], stdout=DEVNULL,
              stderr=PIPE)
    assert q.stderr is not None
    print("devnull:", q.stderr.read(), q.stdout is None)
    print("devnull:", q.wait())
    r = Popen(["cat"], stdin=DEVNULL, stdout=PIPE)
    assert r.stdout is not None
    print("devnull:", r.stdout.read(), r.wait())


def broken_pipe() -> None:
    p = Popen(["true"], stdin=PIPE)
    # Once the child is reaped its read end is gone; the buffered byte only
    # hits the pipe at flush().
    p.wait()
    assert p.stdin is not None
    print("epipe:", p.stdin.write(b"x"))
    try:
        p.stdin.flush()
        print("epipe: flushed")
    except BrokenPipeError as e:
        print("epipe: flush", e.errno)
    try:
        p.stdin.close()
        print("epipe: closed")
    except BrokenPipeError as e:
        print("epipe: close", e.errno)
    print("epipe:", p.stdin.closed)


def no_leaked_pipe() -> None:
    a = Popen(["cat"], stdin=PIPE, stdout=PIPE)
    # Spawned while a's stdin write end is open here: if b inherited it, a's
    # cat would never see EOF and the read below would hang.
    b = Popen(["sleep", "30"])
    assert a.stdin is not None
    assert a.stdout is not None
    a.stdin.close()
    print("leak:", a.stdout.read(), a.wait())
    print("leak:", b.poll())
    b.kill()
    print("leak:", b.wait())


def fd_swap() -> None:
    feed_r, feed_w = os.pipe()
    out_r, out_w = os.pipe()
    saved0 = os.dup(0)
    saved1 = os.dup(1)
    # Park the child's stdin source on fd 1 and its stdout target on fd 0:
    # placing them in a naive dup2 order clobbers one with the other.
    os.dup2(out_w, 0)
    os.dup2(feed_r, 1)
    p = Popen(["cat"], stdin=1, stdout=0)
    os.dup2(saved0, 0)
    os.dup2(saved1, 1)
    os.close(saved0)
    os.close(saved1)
    os.close(feed_r)
    os.close(out_w)
    os.write(feed_w, b"swapped\n")
    os.close(feed_w)
    got = b""
    while True:
        chunk = os.read(out_r, 64)
        if len(chunk) == 0:
            break
        got += chunk
    os.close(out_r)
    print("swap:", got, p.wait())


def bad_arguments() -> None:
    # CPython reports an unusable stream as a filename-less EBADF.
    try:
        Popen(["true"], stdin=STDOUT)
        print("badarg: spawned")
    except OSError as e:
        print("badarg:", e.errno, str(e))
    # Spelled with chr(0): a NUL inside a str literal in a list literal does
    # not build (BUGS.md#list-literal-str-with-nul).
    nul_arg = "a" + chr(0) + "b"
    try:
        Popen(["echo", nul_arg])
        print("badarg: spawned")
    except ValueError as e:
        print("badarg:", str(e))
    try:
        Popen([])
        print("badarg: spawned")
    except IndexError as e:
        print("badarg: IndexError", str(e))


def os_process() -> None:
    p = Popen(["sleep", "30"])
    # WNOHANG on a live child reports pid 0.
    print("osproc:", os.waitpid(p.pid, os.WNOHANG))
    os.kill(p.pid, signal.SIGKILL)
    pid, status = os.waitpid(p.pid, 0)
    print("osproc:", pid == p.pid, os.waitstatus_to_exitcode(status))
    # Reaped behind Popen's back: wait() gets ECHILD and reports 0, as CPython.
    print("osproc:", p.wait())
    try:
        res = os.waitpid(p.pid, 0)
        print("osproc: waited", res)
    except ChildProcessError as e:
        print("osproc: ChildProcessError", e.errno)
    # No process can have the largest pid on Linux or macOS.
    try:
        os.kill(2147483647, 0)
        print("osproc: signalled")
    except ProcessLookupError as e:
        print("osproc: ProcessLookupError", str(e))
    print("osproc:", os.waitstatus_to_exitcode(3 << 8))
    # Invalid on both Linux and macOS (macOS reads 0x7f as stopped, but by
    # signal 0x13, which WIFSTOPPED excludes).
    try:
        os.waitstatus_to_exitcode(0x13ff)
        print("osproc: converted")
    except ValueError as e:
        print("osproc:", str(e))
    try:
        os.waitstatus_to_exitcode(0x0a7f)
        print("osproc: converted")
    except ValueError as e:
        print("osproc:", str(e))
    # 2**40 is out of C range. The message differs between CPython versions,
    # so only the type is printed; spelled as a literal because `2**40` at an
    # int64 parameter is refused (BUGS.md#fixed-width-slot-wide-const-binop-rejects).
    try:
        os.kill(1099511627776, 0)
        print("osproc: signalled")
    except OverflowError:
        print("osproc: kill OverflowError")
    try:
        res = os.waitpid(1, 1099511627776)
        print("osproc: waited", res)
    except OverflowError:
        print("osproc: waitpid OverflowError")


def reaped_externally() -> None:
    p = Popen(["true"])
    pid, status = os.waitpid(p.pid, 0)
    print("reaped:", pid == p.pid, os.waitstatus_to_exitcode(status))
    # poll() gets ECHILD and reports 0, as CPython.
    print("reaped:", p.poll(), p.returncode)


def exit_flush_fails() -> None:
    # The child closes its stdin and only then reports on stdout, so after
    # the readline the byte written below cannot reach a reader.
    p = Popen(["sh", "-c", "exec 0<&-; echo ready"], stdin=PIPE, stdout=PIPE)
    try:
        with p:
            assert p.stdin is not None
            assert p.stdout is not None
            print("exitflush:", p.stdout.readline())
            p.stdin.write(b"x")
        print("exitflush: no error")
    except BrokenPipeError as e:
        print("exitflush: BrokenPipeError", e.errno)
    # __exit__ waited for the child although the stdin flush raised.
    print("exitflush:", p.returncode)


def main() -> None:
    cat_roundtrip()
    exit_code()
    signal_death()
    caching()
    with_block()
    missing_program()
    merge_and_devnull()
    broken_pipe()
    no_leaked_pipe()
    fd_swap()
    bad_arguments()
    os_process()
    reaped_externally()
    exit_flush_fails()


main()
