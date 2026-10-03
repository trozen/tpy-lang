"""The waiting launcher absorbs SIGINT without changing the child's outcome."""

import signal
import subprocess
from collections.abc import Iterator
from types import FrameType

import pytest

from . import cli


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
