"""The waiting launcher absorbs SIGINT without changing the child's outcome;
an interrupted build drops its queued C++ compiles."""

import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future
from types import FrameType

import pytest

from . import cli, toolchain


@pytest.fixture
def restore_sigint() -> Iterator[None]:
    original = signal.getsignal(signal.SIGINT)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, original)


@pytest.mark.usefixtures("restore_sigint")
@pytest.mark.parametrize("incoming", ["default", "python", "custom", "ignore"])
@pytest.mark.parametrize("returncode, expected", [
    (0, 0), (7, 7), (130, 130), (255, 255),
    (-signal.SIGINT, 128 + signal.SIGINT),
    (-signal.SIGTERM, 128 + signal.SIGTERM),
])
def test_run_program_signal_lifecycle(
    monkeypatch: pytest.MonkeyPatch, incoming: str, returncode: int, expected: int,
) -> None:
    calls: list[int] = []

    def custom(signum: int, frame: FrameType | None) -> None:
        calls.append(signum)

    handler = {
        "default": signal.SIG_DFL,
        "python": signal.default_int_handler,
        "custom": custom,
        "ignore": signal.SIG_IGN,
    }[incoming]
    signal.signal(signal.SIGINT, handler)
    argv = ["/some program", "two words", "--flag", ""]

    def run(command: list[str]) -> subprocess.CompletedProcess:
        assert command == argv
        active = signal.getsignal(signal.SIGINT)
        if incoming == "ignore":
            assert active == signal.SIG_IGN
        else:
            assert callable(active)
        # A group interrupt must not override a child's handled/normal exit.
        signal.raise_signal(signal.SIGINT)
        assert calls == []
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(cli.subprocess, "run", run)
    assert cli._run_program(argv) == expected
    assert signal.getsignal(signal.SIGINT) == handler


@pytest.mark.usefixtures("restore_sigint")
@pytest.mark.parametrize("failure", [FileNotFoundError, KeyboardInterrupt])
@pytest.mark.parametrize("ignored", [False, True])
def test_run_program_restores_sigint_on_failure(
    monkeypatch: pytest.MonkeyPatch, failure: type[BaseException], ignored: bool,
) -> None:
    def custom(signum: int, frame: FrameType | None) -> None:
        pytest.fail("the caller's handler ran while waiting for the child")

    handler = signal.SIG_IGN if ignored else custom
    signal.signal(signal.SIGINT, handler)

    def run(command: list[str]) -> subprocess.CompletedProcess:
        raise failure("launch failed")

    monkeypatch.setattr(cli.subprocess, "run", run)
    with pytest.raises(failure, match="launch failed"):
        cli._run_program(["missing"])
    assert signal.getsignal(signal.SIGINT) == handler


@pytest.mark.usefixtures("restore_sigint")
@pytest.mark.parametrize("entry, prog", [(cli.main_tpyc, "tpyc"), (cli.main_tpy, "tpy")])
def test_entry_point_reports_an_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    entry, prog: str,
) -> None:
    # A KeyboardInterrupt out of the run (the front end, the C++ build, the
    # link) is reported in one line and the process ends by SIGINT: the
    # default disposition restored, then the signal sent to itself.
    def run_cli(is_runner: bool) -> int:
        raise KeyboardInterrupt

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(cli, "_run_cli", run_cli)
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    signal.signal(signal.SIGINT, lambda signum, frame: None)
    assert entry() == 130
    assert sent == [(cli.os.getpid(), signal.SIGINT)]
    assert signal.getsignal(signal.SIGINT) == signal.SIG_DFL
    assert capsys.readouterr().err == f"{prog}: interrupted\n"


def test_compile_pool_interrupt_drops_queued_jobs() -> None:
    started: list[int] = []
    release = threading.Event()
    futures: list[Future[None]] = []

    def job(index: int) -> None:
        started.append(index)
        assert release.wait(30)

    def release_once_queue_cancelled() -> None:
        # The running jobs may finish only after the cancel, or a freed worker
        # would take a queued job before it.
        deadline = time.monotonic() + 10
        while not all(f.cancelled() for f in futures[2:]) and time.monotonic() < deadline:
            time.sleep(0.01)
        release.set()

    releaser = threading.Thread(target=release_once_queue_cancelled)
    with pytest.raises(KeyboardInterrupt):
        with toolchain.compile_pool(2) as pool:
            futures.extend(pool.submit(job, index) for index in range(6))
            deadline = time.monotonic() + 30
            while len(started) < 2:
                if time.monotonic() >= deadline:
                    # Released first: the pool's exit waits for every job.
                    release.set()
                    pytest.fail(f"the pool started only {started}")
                time.sleep(0.01)
            releaser.start()
            raise KeyboardInterrupt
    releaser.join()
    assert sorted(started) == [0, 1]
    assert all(f.done() and not f.cancelled() for f in futures[:2])
    assert all(f.cancelled() for f in futures[2:])
