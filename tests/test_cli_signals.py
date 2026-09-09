"""Signal behavior through the real CLI, including the cached exec path."""

import os
import selectors
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from conftest import exec_is_cross


PROGRAM = """\
# Exercise launcher policy using native default and asyncio-owned SIGINT.
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
    with tempfile.TemporaryFile(mode="w+") as stderr:
        proc = subprocess.Popen(
            argv, cwd=tmp_path, stdout=subprocess.PIPE, stderr=stderr,
            text=True, start_new_session=True,
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
    options = ["--cxx", request.config.getoption("--cxx"), "-j", "1"]
    if request.config.getoption("--no-ccache"):
        options.append("--no-ccache")

    for mode, ignored, output, exitcode in [
        ("int", False, "", -signal.SIGINT),
        ("term", False, "", -signal.SIGTERM),
        ("group", False, "ready\n", -signal.SIGINT),
        ("int", True, "survived\n", 0),
        ("handled", False, "42\n", 0),
        ("exit", False, "two words --unchanged\n", 7),
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
            assert "KeyboardInterrupt" not in result.stderr, context
            assert "Traceback" not in result.stderr, context

    # Command strings have no file cache and reach the same waiting launcher.
    result = _run_cli(
        tmp_path, entrypoint, options, "int", rebuild=False,
        code="import signal\nsignal.raise_signal(signal.SIGINT)\nprint('survived')\n",
    )
    assert result.returncode == 128 + signal.SIGINT, result.stderr
    assert result.stdout == "", result.stderr
    assert "analyzed" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
