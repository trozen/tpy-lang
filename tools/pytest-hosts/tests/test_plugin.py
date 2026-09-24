"""End-to-end runs through pytest itself: the local precedence shapes, and
real distributed sessions over a fake `ssh` that runs commands locally."""

import contextlib
import io
import os
import re
import shutil
import subprocess
import textwrap

import pytest

from fake_ssh_helper import install as install_fake_ssh, venv_setup_command
from pytest_hosts import cli

pytest_plugins = ["pytester"]

needs_tools = pytest.mark.skipif(
    not (shutil.which("rsync") and shutil.which("flock")), reason="needs rsync and flock")


@pytest.fixture
def project(pytester, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("PYTEST_HOSTS", raising=False)
    pytester.makepyprojecttoml("""
        [project]
        name = "demo"
        version = "0"
        [tool.pytest.ini_options]
        addopts = "-n 2"
    """)
    pytester.makepyfile(test_a="""
        def test_worker(): pass
    """)
    return pytester


def hosts_file(pytester, text):
    path = pytester.path / ".config" / "pytest-hosts" / "hosts.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


def workers_line(result, n):
    """The `N workers [M items]` line of the run, so 1 is not matched by 11."""
    return re.search(rf"(?<!\d){n} workers? \[", result.stdout.str()) is not None


def run(pytester, *args):
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def test_no_hosts_file_leaves_addopts_n(project):
    result = run(project)
    result.assert_outcomes(passed=1)
    assert workers_line(result, 2)


def test_no_project_entry_warns_once(project):
    hosts_file(project, """
        [hosts.x]
        ssh = "x"
        workers = 1
    """)
    result = run(project)
    result.assert_outcomes(passed=1, warnings=1)
    result.stdout.fnmatch_lines(["*no [[]projects.demo[]] entry*"])


def test_hosts_local_replaces_addopts_n(project):
    hosts_file(project, """
        [hosts.x]
        ssh = "x"
        workers = 1
        [projects.demo]
        local = 1
        hosts.x = {}
    """)
    result = run(project, "--hosts-local")
    result.assert_outcomes(passed=1)
    assert workers_line(result, 1)
    result.stdout.fnmatch_lines(["hosts| local only (--hosts-local): 1 worker"])


def test_env_switch_is_local_only(project, monkeypatch):
    hosts_file(project, """
        [hosts.x]
        ssh = "x"
        workers = 1
        [projects.demo]
        local = 1
        hosts.x = {}
    """)
    monkeypatch.setenv("PYTEST_HOSTS", "0")
    result = run(project)
    result.assert_outcomes(passed=1)
    assert workers_line(result, 1)
    result.stdout.fnmatch_lines(["hosts| local only (PYTEST_HOSTS=0): 1 worker"])


def test_project_without_hosts_says_so(project):
    hosts_file(project, """
        [projects.demo]
        local = 1
    """)
    result = run(project)
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["hosts| local only ([[]projects.demo[]] selects no hosts): 1 worker"])


def test_both_local_switches_contradict(project):
    hosts_file(project, """
        [hosts.x]
        ssh = "x"
        workers = 1
        [projects.demo]
        hosts.x = {}
    """)
    result = run(project, "--hosts-local", "--hosts-only=x")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--hosts-local and --hosts-only=x contradict*"])


def test_cmdline_n_is_local_and_ignores_hosts(project):
    hosts_file(project, """
        [hosts.x]
        ssh = "nonexistent.invalid"
        workers = 1
        [projects.demo]
        hosts.x = {}
    """)
    result = run(project, "-n", "1")
    result.assert_outcomes(passed=1)
    assert workers_line(result, 1)


def test_bad_hosts_file_is_a_usage_error(project):
    hosts_file(project, "[hosts.x]\nworkers = 1\n")
    result = run(project)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*ssh is required*"])


def test_hosts_only_unknown_is_a_usage_error(project):
    hosts_file(project, """
        [hosts.x]
        ssh = "x"
        workers = 1
        [projects.demo]
        hosts.x = {}
    """)
    result = run(project, "--hosts-only=y")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--hosts-only=y*configured: x*"])


# --- a real distributed session over the fake ssh ---------------------------


@pytest.fixture
def remote_project(pytester, monkeypatch):
    """A project whose hosts file names a fake host answered by this machine."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("PYTEST_HOSTS", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(pytester.path / "rt"))
    monkeypatch.setenv("FAKE_SSH_LOG", str(pytester.path / "ssh.log"))
    monkeypatch.setenv("PATH", str(install_fake_ssh(pytester.path / "bin").parent)
                       + os.pathsep + os.environ["PATH"])
    root = pytester.path / "root"
    hosts_file(pytester, f"""
        [hosts.box]
        ssh = "box"
        workers = 2
        root = "{root}"
        lock_dir = "{pytester.path / 'locks'}"
        path_prepend = "/opt/fake-toolchain/bin"
        [projects.demo]
        local = 1
        hosts.box = {{}}
    """)
    checkout = pytester.mkdir("demo")
    # a git checkout, so the pull-back filter has gitignore rules to apply
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / "pyproject.toml").write_text(textwrap.dedent(f"""
        [project]
        name = "demo"
        version = "0"
        [tool.pytest.ini_options]
        addopts = "-n 7"
        [tool.pytest-hosts]
        setup = "{venv_setup_command(pytester.path)}"
        setup_when = ["pyproject.toml"]
        env = ["DEMO_TOKEN"]
        ignore = ["scratch"]
    """))
    (checkout / ".gitignore").write_text("*.log\n__pycache__/\n")
    (checkout / "junk.log").write_text("ignored by gitignore")
    (checkout / "scratch").mkdir()
    (checkout / "scratch" / "big.bin").write_text("ignored by config")
    # every worker records where it runs and what it was handed; xdist
    # does not forward worker stdout, so a file is the observable
    (checkout / "conftest.py").write_text(textwrap.dedent("""
        import os
        def pytest_sessionstart(session):
            wid = os.environ.get("PYTEST_XDIST_WORKER")
            if wid:
                with open(f"where-{os.getpid()}.txt", "w") as f:
                    f.write(os.getcwd() + " TOKEN=" + os.environ.get("DEMO_TOKEN", "-")
                            + " PATH0=" + os.environ["PATH"].split(":")[0])
                # by rootdir: lands in the tree on a remote worker, so it must be pulled back
                root = session.config.rootpath
                (root / f"out-{wid}.txt").write_text("from " + wid)
                (root / "shared.txt").write_text("from " + wid)
                (root / f"scratch-{wid}.log").write_text("gitignored, stays remote")
    """))
    (checkout / "test_where.py").write_text(textwrap.dedent("""
        def test_one(): pass
        def test_two(): pass
        def test_three(): pass
        def test_four(): pass
    """))
    pytester.chdir()
    os.chdir(checkout)
    return pytester, checkout, root


@needs_tools
def test_distributed_session_over_fake_ssh(remote_project, monkeypatch):
    pytester, checkout, root = remote_project
    monkeypatch.setenv("DEMO_TOKEN", "forwarded")
    result = run(pytester)
    result.assert_outcomes(passed=4)
    out = result.stdout.str()
    assert workers_line(result, 3)  # 1 local + 2 "remote", not addopts' 7
    result.stdout.fnmatch_lines(["hosts| distributed per hosts file (see: pytest-hosts command)",
                                 "hosts| local: 1 worker (--hosts-local: stay on this machine)",
                                 "hosts| box: slot 0, 2 workers, tree */root/*/demo, setup ran"])

    trees = list(root.iterdir())
    assert len(trees) == 1
    parent = trees[0]
    assert (parent / "demo" / "test_where.py").exists()
    assert (parent / "demo" / "pyproject.toml").exists()
    assert not (parent / "demo" / "junk.log").exists()      # gitignore filter
    assert not (parent / "demo" / "scratch").exists()       # config ignore
    assert (parent / "venv" / "bin" / "python").exists()   # beside the tree
    assert (parent / "stamp").exists()
    assert (parent / "setup.hash").exists()
    # setup wrote into the tree before the stamp, so it is not pulled back
    assert (parent / "demo" / "setup-wrote.txt").exists()
    assert not (checkout / "setup-wrote.txt").exists()
    # the tree's pytest is the controller's version, so xdist shipped nothing
    assert not (parent / "_pytest").exists() and not (parent / "pytest").exists()
    assert "xdist ships its own" not in result.stdout.str()

    # remote workers run with the tree's parent as cwd (xdist's layout) and
    # the tree as rootdir; both got the forwarded env
    remote_marks = sorted(p.read_text() for p in parent.glob("where-*.txt"))
    assert remote_marks == [f"{parent} TOKEN=forwarded PATH0=/opt/fake-toolchain/bin"] * 2
    local_marks = [p.read_text() for p in checkout.glob("where-*.txt")]
    assert len(local_marks) == 1 and local_marks[0].startswith(f"{checkout} TOKEN=forwarded PATH0=")
    assert "fake-toolchain" not in local_marks[0]  # path_prepend is per host
    # pull-back: what the remote workers wrote by rootdir is in the checkout
    # now, gitignored output is not, and the summary says so per host
    assert sorted(p.name for p in checkout.glob("out-*.txt")) == ["out-gw0.txt", "out-gw1.txt",
                                                                  "out-gw2.txt"]
    assert (checkout / "out-gw1.txt").read_text() == "from gw1"
    assert not list(checkout.glob("scratch-gw[12].log"))  # gitignored: stays remote
    assert (parent / "demo" / "scratch-gw1.log").exists()
    result.stdout.fnmatch_lines(["hosts| box: shared.txt also changed locally*remote version wins",
                                 "hosts| box -> out-gw1.txt", "hosts| box -> out-gw2.txt",
                                 "hosts| local: * tests", "hosts| box: * tests, * file(s) pulled back"])
    assert (checkout / "shared.txt").read_text() in ("from gw1", "from gw2")
    tallies = {line.split()[1].rstrip(":"): int(line.split()[2])
               for line in result.outlines if line.startswith("hosts|") and " tests" in line}
    assert tallies["local"] + tallies["box"] == 4 and tallies["box"] >= 1

    # the lock is released at the end: taking it again succeeds at once
    lock = pytester.path / "locks" / "0"
    assert lock.exists()
    assert subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 0

    # a second run reuses the tree and skips the setup; --hosts-no-pull leaves
    # the remote output where it is
    for stale in checkout.glob("out-gw[12].txt"):
        stale.unlink()
    result = run(pytester, "--hosts-no-pull")
    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines(["hosts| box: slot 0, 2 workers, tree */root/*/demo"])
    assert "setup ran" not in result.stdout.str()
    assert not list(checkout.glob("out-gw[12].txt"))
    assert "pulled back" not in result.stdout.str()

    # changing a setup_when input runs it again; --hosts-setup forces it
    (checkout / "pyproject.toml").write_text((checkout / "pyproject.toml").read_text() + "\n# x\n")
    result = run(pytester)
    assert "setup ran" in result.stdout.str()
    result = run(pytester, "--hosts-setup")
    assert "setup ran" in result.stdout.str()

    # a typed relative path is what absolute_args exists for
    result = run(pytester, "test_where.py")
    result.assert_outcomes(passed=4)
    assert workers_line(result, 3)


@needs_tools
def test_busy_slot_fails_fast_with_no_wait(remote_project):
    pytester, checkout, root = remote_project
    locks = pytester.path / "locks"
    locks.mkdir()
    holder = subprocess.Popen(["flock", str(locks / "0"), "sleep", "30"])
    try:
        result = run(pytester, "--hosts-no-wait")
        assert result.ret == pytest.ExitCode.USAGE_ERROR
        result.stderr.fnmatch_lines(["*box: all 1 slot(s) busy*"])
    finally:
        holder.kill()


@needs_tools
def test_unreachable_host_errors_or_degrades(remote_project):
    pytester, checkout, root = remote_project
    hosts_file_path = pytester.path / ".config" / "pytest-hosts" / "hosts.toml"
    text = hosts_file_path.read_text().replace('ssh = "box"', 'ssh = "box"\nunreachable = "local"')
    # the fake ssh fails when told to: a host alias the script cannot run
    bad_bin = pytester.path / "bin" / "ssh"
    bad_bin.write_text("#!/usr/bin/env bash\nexit 255\n")
    result = run(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*box: unreachable*"])

    hosts_file_path.write_text(text)
    result = run(pytester)
    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines(["hosts| box: unreachable*running without box",
                                 "hosts| box: not used (box: unreachable*"])
    assert workers_line(result, 1)

    # asked for by name, the degrade policy does not apply
    result = run(pytester, "--hosts-only=box")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*--hosts-only names it*"])


@needs_tools
def test_cli_status_setup_clean(remote_project):
    pytester, checkout, root = remote_project

    assert cli.main(["setup"]) == 0
    parent = next(root.iterdir())
    assert (parent / "demo" / "test_where.py").exists()
    assert (parent / "venv" / "bin" / "python").exists()
    assert not (pytester.path / "locks").exists()  # setup takes no slot

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(["status"]) == 0
    text = out.getvalue()
    assert "box: reachable, 1/1 slot(s) free, 2 workers, tree present, venv present" in text
    assert str(parent / "demo") in text

    assert cli.main(["clean"]) == 0
    assert not parent.exists()
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli.main(["status"]) == 0


@needs_tools
def test_cli_status_and_clean_report_failures(remote_project, monkeypatch, capsys):
    pytester, checkout, root = remote_project
    # the probe passes, the status command does not: reachable but broken
    monkeypatch.setenv("FAKE_SSH_FAIL_MATCH", "flock -n")
    assert cli.main(["status"]) == 1
    assert "box: reachable, but status command failed (exit 3): fake ssh: refusing" in capsys.readouterr().out
    monkeypatch.delenv("FAKE_SSH_FAIL_MATCH")
    # an unreachable host: both commands say so and exit 1
    (pytester.path / "bin" / "ssh").write_text("#!/usr/bin/env bash\nexit 255\n")
    assert cli.main(["status"]) == 1
    out = capsys.readouterr().out
    assert "  box: unreachable" in out and "box: box:" not in out
    assert cli.main(["clean"]) == 1
    out = capsys.readouterr().out
    assert "  box: unreachable" in out and "box: box:" not in out


@needs_tools
def test_hosts_only_runs_nothing_locally(remote_project):
    pytester, checkout, root = remote_project
    result = run(pytester, "--hosts-only=box")
    result.assert_outcomes(passed=4)
    assert workers_line(result, 2)
    assert not [line for line in result.outlines if line.startswith("hosts| local")]
    assert not list(checkout.glob("where-*.txt"))  # no local worker ran


@needs_tools
def test_failed_pull_back_is_reported(remote_project, monkeypatch):
    pytester, checkout, root = remote_project
    monkeypatch.setenv("FAKE_SSH_FAIL_MATCH", "find .")  # the written-files listing
    result = run(pytester)
    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines(["hosts| box: pull-back failed: box: listing written files failed (exit 3)*",
                                 "hosts| box: * tests, pull-back FAILED"])
    assert not list(checkout.glob("out-gw[12].txt"))


def test_value_options_come_from_the_real_parser(pytester):
    """The private parser attribute this reads changed name in pytest 9; a
    lookup that silently found nothing would leave `-k tests tests`
    rewriting the -k value again."""
    from pytest_hosts import plugin
    config = pytester.parseconfig()
    options = plugin._value_options(config)
    assert {"-k", "-m", "-p", "-c"} <= options
    assert "-x" not in options and "-v" not in options  # flags take no value
    # a parser without the private attributes degrades to "no option takes a value"
    assert plugin._value_options(type("C", (), {"_parser": object()})()) == set()


@needs_tools
def test_pull_false_leaves_remote_output(remote_project):
    pytester, checkout, root = remote_project
    pyproject = checkout / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "pull = false\n")
    result = run(pytester)
    result.assert_outcomes(passed=4)
    assert not list(checkout.glob("out-gw[12].txt"))
    assert "pulled back" not in result.stdout.str()


@needs_tools
def test_two_hosts(remote_project):
    """Both hosts are prepared, each gets its own tree, tally and pull-back."""
    pytester, checkout, root = remote_project
    hosts_file_path = pytester.path / ".config" / "pytest-hosts" / "hosts.toml"
    second_root = pytester.path / "root2"
    hosts_file_path.write_text(hosts_file_path.read_text() + textwrap.dedent(f"""
        [hosts.box2]
        ssh = "box2"
        workers = 1
        root = "{second_root}"
        lock_dir = "{pytester.path / 'locks2'}"
        [projects.demo.hosts.box2]
    """))
    (checkout / "test_more.py").write_text(
        "\n".join(f"def test_m{i}(): pass" for i in range(8)) + "\n")
    result = run(pytester)
    result.assert_outcomes(passed=12)
    assert workers_line(result, 4)
    # hosts prepare in parallel, so their lines come in either order
    result.stdout.fnmatch_lines(["hosts| box: slot 0, 2 workers, tree */root/*/demo*"])
    result.stdout.fnmatch_lines(["hosts| box2: slot 0, 1 workers, tree */root2/*/demo*"])
    tallies = {line.split()[1].rstrip(":"): int(line.split()[2])
               for line in result.outlines if line.startswith("hosts|") and " tests" in line}
    assert set(tallies) == {"local", "box", "box2"} and sum(tallies.values()) == 12
    assert tallies["box2"] >= 1
    assert sorted(p.name for p in checkout.glob("out-gw*.txt")) == [f"out-gw{i}.txt" for i in range(4)]
    assert (next(second_root.iterdir()) / "demo" / "test_more.py").exists()


def test_cli_overview(pytester, monkeypatch, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert cli.main([]) == 0  # nothing configured: still informational
    out = capsys.readouterr().out
    assert "hosts file: " in out and "(missing)" in out
    assert "project: none" in out and "pytest-hosts status " in out

    pytester.makepyprojecttoml("[project]\nname = 'demo'\nversion = '0'\n"
                               "[tool.pytest-hosts]\nsetup = 'make'\n")
    hosts_file(pytester, "[hosts.x]\nssh = 'x'\nworkers = 3\n[projects.demo]\nlocal = 2\nhosts.x = {}\n")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "(found)" in out and "project: demo at" in out
    assert "setup: make" in out and "local workers: 2" in out
    assert "x: ssh x, 3 workers, 1 slot(s), root ~/.pytest-hosts, unreachable -> error" in out

    (pytester.path / ".config" / "pytest-hosts" / "hosts.toml").write_text("[hosts.x]\nworkers = 1\n")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "config error: " in out and "runs locally: the hosts file is unusable" in out
    assert "project: demo at" in out  # the project survives a broken hosts file

    # a project with no hosts file at all
    (pytester.path / ".config" / "pytest-hosts" / "hosts.toml").unlink()
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "(missing)" in out and "runs locally: no hosts file" in out

    # a broken project table: the project itself is what is unusable
    pytester.makepyprojecttoml("[project]\nname = 'demo'\nversion = '0'\n"
                               "[tool.pytest-hosts]\nbogus = 1\n")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "config error: " in out and "project: unusable" in out

    # no project at all and a broken hosts file: each named correctly
    (pytester.path / "pyproject.toml").unlink()
    hosts_file(pytester, "[hosts.x]\nworkers = 1\n")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "config error: " in out and "project: none" in out and "unusable" not in out


def test_cli_config_needs_no_project(pytester, capsys):
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# ~/.config/pytest-hosts/hosts.toml")
    assert "[tool.pytest-hosts]" in out


def test_cli_errors(pytester, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    with pytest.raises(SystemExit, match="no pyproject.toml"):
        cli.main(["status"])
    pytester.makepyprojecttoml("[project]\nname = 'demo'\nversion = '0'\n")
    with pytest.raises(SystemExit, match="no hosts file"):
        cli.main(["status"])
    hosts_file(pytester, "[hosts.x]\nssh = 'x'\nworkers = 1\n")
    with pytest.raises(SystemExit, match=r"no \[projects.demo\] entry"):
        cli.main(["status"])
    pytester.makepyprojecttoml("[project]\nname = 'demo'\nversion = '0'\n[tool.pytest-hosts]\nbogus = 1\n")
    with pytest.raises(SystemExit, match="unknown key 'bogus'"):
        cli.main(["status"])


def test_bad_project_table_is_a_usage_error(project):
    project.makepyprojecttoml("""
        [project]
        name = "demo"
        version = "0"
        [tool.pytest-hosts]
        setup = 3
    """)
    hosts_file(project, "[hosts.x]\nssh = 'x'\nworkers = 1\n[projects.demo]\nhosts.x = {}\n")
    result = run(project)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*setup must be a string*"])
