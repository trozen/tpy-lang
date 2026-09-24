"""One real session against the host named by PYTEST_HOSTS_TEST_HOST (an ssh
alias). Creates and removes a tree under that host's default root. Skipped
otherwise."""

import contextlib
import io
import os
import subprocess
import textwrap

import pytest

from pytest_hosts import cli as cli_module

HOST = os.environ.get("PYTEST_HOSTS_TEST_HOST")

pytestmark = pytest.mark.skipif(not HOST, reason="PYTEST_HOSTS_TEST_HOST not set")


@pytest.fixture
def real_project(pytester, monkeypatch):
    # pytester points HOME at its tmp dir; ssh needs the real one for its
    # config, keys and known hosts, so the hosts file goes through XDG instead
    monkeypatch.setenv("HOME", os.path.expanduser(f"~{os.environ.get('USER', '')}")
                       if os.environ.get("USER") else os.environ["HOME"])
    xdg = pytester.path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("PYTEST_HOSTS", raising=False)
    hosts = xdg / "pytest-hosts" / "hosts.toml"
    hosts.parent.mkdir(parents=True)
    hosts.write_text(textwrap.dedent(f"""
        [hosts.box]
        ssh = "{HOST}"
        workers = 12                # past sshd's 10 sessions per connection
        [projects.pytest-hosts-integration]
        local = 1
        hosts.box = {{}}
    """))
    checkout = pytester.mkdir("pytest-hosts-integration")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / "pyproject.toml").write_text(textwrap.dedent(f"""
        [project]
        name = "pytest-hosts-integration"
        version = "0"
        requires-python = ">=3.10"
        dependencies = ["pytest=={pytest.__version__}", "pytest-xdist"]
        [tool.pytest.ini_options]
        addopts = "-n 5"
        [tool.pytest-hosts]
        setup = "uv sync -q"
        setup_when = ["pyproject.toml"]
        env = ["DEMO_TOKEN"]
    """))
    (checkout / ".gitignore").write_text("__pycache__/\n*.log\nuv.lock\n")
    (checkout / "conftest.py").write_text(textwrap.dedent("""
        import os, socket
        def pytest_sessionstart(session):
            wid = os.environ.get("PYTEST_XDIST_WORKER")
            if wid:
                root = session.config.rootpath
                (root / f"out-{wid}.txt").write_text(socket.gethostname() + " " + os.environ.get("DEMO_TOKEN", "-"))
                (root / f"noise-{wid}.log").write_text("stays remote")
    """))
    (checkout / "test_it.py").write_text("\n".join(f"def test_{i}(): pass" for i in range(12)) + "\n")
    os.chdir(checkout)
    return pytester, checkout


def cli(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli_module.main(list(args))
    return rc, out.getvalue()


def test_real_host_session(real_project, monkeypatch):
    pytester, checkout = real_project
    monkeypatch.setenv("DEMO_TOKEN", "forwarded")

    rc, status = cli("status")
    assert rc == 0, status
    assert "box: reachable" in status

    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=12)
    out = result.stdout.str()
    assert "13 workers" in out  # 1 local + 12 remote, not addopts' 5
    assert "mux_client" not in out and "disabling multiplexing" not in result.stderr.str()
    # the tree's pytest is pinned to the controller's, so the fast path applies
    assert "xdist ships its own" not in out
    result.stdout.fnmatch_lines(["hosts| box: slot 0, 12 workers, tree */pytest-hosts-integration, setup ran",
                                 "hosts| box -> out-gw1.txt",
                                 "hosts| local: * tests", "hosts| box: * tests, 12 file(s) pulled back"])
    remote_host = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, "hostname"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    for i in range(1, 13):
        assert (checkout / f"out-gw{i}.txt").read_text() == f"{remote_host} forwarded"
    # the local worker's log is written in place; the remote ones stay remote
    assert [p.name for p in checkout.glob("noise-gw*.log")] == ["noise-gw0.log"]

    # second run: tree reused, setup skipped, slot free again
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-k", "test_1")
    result.assert_outcomes(passed=3)  # test_1, test_10, test_11
    assert "setup ran" not in result.stdout.str()

    rc, status = cli("status")
    assert "1/1 slot(s) free" in status and "tree present, venv present" in status
    rc, _ = cli("clean")
    assert rc == 0
    rc, status = cli("status")
    assert "no tree, no venv" in status
