"""The command lines the session runs, without running them."""

import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

import pytest

from pytest_hosts import config as cfg
from pytest_hosts import plan, session


HOST = cfg.HostConfig(name="big", ssh="big", workers=2, path_prepend="/opt/tc/bin",
                      lock_dir="/tmp/ph")
REMOTE = plan.plan_remote(HOST, Path("/home/me/src/demo"), "laptop", ssh_config="/rt/ssh_config",
                          control_dir="/rt", home="/home/u")
SETTINGS = cfg.ProjectSettings(setup="uv sync -q", setup_when=("pyproject.toml",),
                               ignore=("examples",))


def test_ssh_config_puts_user_config_first(tmp_path):
    text = session.ssh_config_text(tmp_path)
    assert text.startswith("Include ~/.ssh/config\nHost *\n")
    assert f"ControlPath {tmp_path}/cm-%C" in text
    assert "BatchMode yes" in text


def test_ssh_argv_applies_path_prepend_and_timeout():
    argv = session.ssh_argv(REMOTE, "true", connect_timeout=5)
    assert argv[:3] == ["ssh", "-F", "/rt/ssh_config"]
    assert argv[3:5] == ["-o", "ConnectTimeout=5"]
    assert argv[5] == "big"
    assert argv[6] == "export PATH=/opt/tc/bin:$PATH; true"
    # the probe reports the host's own PATH, so it runs without the prefix
    assert session.ssh_argv(REMOTE, session.probe_command(), raw=True)[-1] == session.probe_command()
    plain = cfg.HostConfig(name="p", ssh="p", workers=1)
    assert session.remote_shell(plain, "true") == "true"


def test_lock_command_is_non_blocking_and_shared():
    cmd = session.lock_command(HOST, 1)
    assert "chmod 1777 /tmp/ph" in cmd
    assert "flock -n /tmp/ph/1" in cmd
    assert "echo LOCKED; exec cat" in cmd


def test_rsync_argv():
    argv = session.rsync_argv(REMOTE, Path("/home/me/src/demo"), SETTINGS.ignore)
    assert argv[0] == "rsync"
    assert "--delete" in argv and "--delete-excluded" not in argv
    assert "--filter=:- .gitignore" in argv
    assert "--exclude=.git" in argv and "--exclude=examples" in argv
    assert argv[-2] == "/home/me/src/demo/"
    assert argv[-1] == f"big:{REMOTE.tree}/"
    rsync_path = next(a for a in argv if a.startswith("--rsync-path="))
    assert rsync_path == f"--rsync-path=export PATH=/opt/tc/bin:$PATH; mkdir -p {REMOTE.tree} && rsync"
    assert argv[argv.index("-e") + 1] == "ssh -F /rt/ssh_config"


def test_sync_excludes_what_git_hides_and_rsync_would_ship(tmp_path):
    """Nested repositories (worktree or clone), paths ignored only through
    info/exclude, and a wildcard-named one; a plain untracked directory and
    a .gitignore-ignored path (rsync's own filter handles it) are left to
    rsync."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "root"],
                   check=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", ".claude/worktrees/agent-1"],
                   check=True, capture_output=True)
    sibling = tmp_path / "sibling"  # a worktree outside the checkout is not nested
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(sibling)],
                   check=True, capture_output=True)
    subprocess.run(["git", "init", "-q", str(repo / "vendor" / "clone")], check=True)
    subprocess.run(["git", "init", "-q", str(repo / "wt" / "x*[1]")], check=True)
    (repo / "scratch").mkdir()
    (repo / "scratch" / "new.py").write_text("")  # untracked, still ours
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "run.log").write_text("")  # rsync's own filter drops it
    (repo / ".git" / "info" / "exclude").write_text("**/.claude/worktrees/\neditor-cache/\n")
    (repo / "editor-cache").mkdir()
    (repo / "editor-cache" / "state").write_text("")  # ignored where rsync cannot see
    excludes = session.sync_excludes(repo)
    assert excludes == [".claude/worktrees/agent-1", "editor-cache/state", "vendor/clone", "wt/x*[1]"]
    argv = session.rsync_argv(REMOTE, repo, (), excludes)
    assert "--filter=-s /.claude/worktrees/agent-1" in argv  # sender-side: the host copy is deleted
    assert "--filter=-s /wt/x\\*\\[1]" in argv  # wildcards taken literally
    assert not any("run.log" in a or "scratch" in a for a in argv)
    assert session.sync_excludes(tmp_path / "not-a-repo") == []


def test_setup_hash_tracks_inputs(tmp_path):
    (tmp_path / "pyproject.toml").write_text("a")
    one = session.setup_hash(SETTINGS, tmp_path)
    assert one == session.setup_hash(SETTINGS, tmp_path)
    (tmp_path / "pyproject.toml").write_text("b")
    assert one != session.setup_hash(SETTINGS, tmp_path)
    (tmp_path / "pyproject.toml").unlink()
    missing = session.setup_hash(SETTINGS, tmp_path)
    assert missing not in (one,)
    other = cfg.ProjectSettings(setup="make", setup_when=("pyproject.toml",))
    assert session.setup_hash(other, tmp_path) != missing


def test_after_sync_command_gates_on_venv_and_hash():
    cmd = session.after_sync_command(REMOTE, SETTINGS, "abc", force_setup=False)
    assert cmd.startswith(f"cd {REMOTE.tree} && if ")
    # the stamp comes after setup: what setup writes is not the run's output
    assert cmd.index("bash -lc") < cmd.index(f"touch {REMOTE.stamp}")
    assert f"[ ! -e {REMOTE.venv} ]" in cmd
    assert f'"$(cat {REMOTE.setup_hash_file} 2>/dev/null)" != abc' in cmd
    assert f"export UV_PROJECT_ENVIRONMENT={REMOTE.venv}; bash -lc 'uv sync -q'" in cmd
    assert f"printf %s abc > {REMOTE.setup_hash_file} && echo SETUP" in cmd

    forced = session.after_sync_command(REMOTE, SETTINGS, "abc", force_setup=True)
    assert "if " not in forced and "bash -lc" in forced

    no_setup = session.after_sync_command(REMOTE, cfg.ProjectSettings(), "abc", force_setup=True,
                                          host_tmp="/srv/tmp")
    roots = " ".join(REMOTE.worker_tmp("/srv/tmp", i) for i in range(REMOTE.workers))
    assert no_setup == f"cd {REMOTE.tree} && touch {REMOTE.stamp} && mkdir -p {roots}"


def test_quoting_survives_odd_paths():
    host = cfg.HostConfig(name="h", ssh="h", workers=1, root="/srv/my space")
    remote = plan.plan_remote(host, Path("/home/me/src/demo"), "l", ssh_config="/c f",
                              control_dir="/rt")
    cmd = session.after_sync_command(remote, cfg.ProjectSettings(), "x", force_setup=False)
    assert shlex.split(cmd.split(" && ")[0]) == ["cd", remote.tree]
    argv = session.rsync_argv(remote, Path("/home/me/src/demo"), ())
    assert argv[argv.index("-e") + 1] == "ssh -F '/c f'"


def test_host_files_and_pull_argv():
    cmd = session.host_files_command(REMOTE)
    assert cmd == (f"cd {REMOTE.tree} && find {REMOTE.stamp} -printf '%T@\\n' "
                   f"&& find . ! -type d -printf '%T@\\t%p\\0'")
    stamp, files = session.parse_host_files(
        "1700000000.5000000000\n1700000001.25\t./a/b.txt\x001699999999.0\t./c.txt\x00")
    assert stamp == "1700000000.5000000000"
    assert files == {"a/b.txt": "1700000001.25", "c.txt": "1699999999.0"}
    assert session._newer(files["a/b.txt"], stamp) and not session._newer(files["c.txt"], stamp)
    assert session._newer("1700000000.500000001", stamp)  # nanoseconds, not float rounding
    argv = session.pull_argv(REMOTE, Path("/home/me/src/demo"))
    assert argv[:5] == ["rsync", "-a", "--ignore-times", "--from0", "--files-from=-"]
    assert "--update" not in argv  # the remote version wins by design
    assert argv[-2:] == [f"big:{REMOTE.tree}/", "/home/me/src/demo/"]


def test_drop_ignored_uses_git_rules(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("*.log\n!keep.log\nbuild/\n")
    paths = ["a.txt", "run.log", "keep.log", "build/x.o", "sub/b.txt"]
    assert session.drop_ignored(tmp_path, paths) == ["a.txt", "keep.log", "sub/b.txt"]
    assert session.drop_ignored(tmp_path, []) == []


def test_drop_ignored_outside_git_keeps_everything(tmp_path):
    assert session.drop_ignored(tmp_path, ["run.log", "a.txt"]) == ["run.log", "a.txt"]


def test_after_sync_reports_and_dedups_pytest():
    cmd = session.after_sync_command(REMOTE, SETTINGS, "abc", force_setup=False, local_pytest="9.0.2")
    assert 'v=$(' + REMOTE.python + ' -c "import pytest; print(pytest.__version__)") && echo "PYTEST=$v"' in cmd
    assert f'if [ "$v" = 9.0.2 ]; then rm -rf {REMOTE.parent}/pytest {REMOTE.parent}/_pytest; fi' in cmd
    assert "PYTEST" not in session.after_sync_command(REMOTE, SETTINGS, "abc", force_setup=False)


def test_probe_reports_home_path_and_tmp():
    assert session.probe_command() == 'printf "%s\\n%s\\n%s" "$HOME" "$PATH" "${TMPDIR:-/tmp}"'


def test_worker_env_has_a_temp_root_per_worker_and_the_path_prefix():
    hs = session.HostSession(remote=REMOTE, path="/usr/bin:/bin", tmp="/srv/tmp")
    tid = REMOTE.parent.rsplit("/", 1)[1]
    assert hs.worker_env(0) == {"PYTEST_DEBUG_TEMPROOT": f"/srv/tmp/pytest-hosts-{tid}/w0",
                                "PATH": "/opt/tc/bin:/usr/bin:/bin"}
    assert hs.worker_env(7)["PYTEST_DEBUG_TEMPROOT"].endswith("/w7")
    plain = plan.plan_remote(cfg.HostConfig(name="p", ssh="p", workers=1, tmp="/fast"),
                             Path("/x"), "l", ssh_config="/c", control_dir="/d")
    env = session.HostSession(remote=plain, path="/usr/bin", tmp="/tmp").worker_env(0)
    assert set(env) == {"PYTEST_DEBUG_TEMPROOT"}
    assert env["PYTEST_DEBUG_TEMPROOT"].startswith("/fast/pytest-hosts-")  # config beats the host


# --- the slot gate, with a fake ssh holder ------------------------------------


class FakePopen:
    """Stands in for the ssh holder. `answers` maps a slot to what the
    holder prints: lines joined by "\\n", "LOCKED" somewhere in them for a
    taken slot, "" for busy (exit 1), "hang" for no answer at all; an
    `(text, exit)` tuple sets the exit code. `busy_polls` makes slot 0
    answer busy that many times first. `keep_open` leaves the write end
    open after the text, the way the remote `cat` holds the pipe once
    the lock is taken; "later:" prefixed text arrives in two writes with
    a pause, the second starting mid-line. A raw pipe backs stdout so
    select() and os.read() work as on a real process."""

    answers: dict[int, object] = {}
    started: list[int] = []
    instances: list["FakePopen"] = []
    busy_polls: int = 0
    keep_open: bool = False

    def __init__(self, argv, **kw):
        slot = int(argv[-1].split("flock -n ")[1].split()[0].rsplit("/", 1)[1])
        FakePopen.started.append(slot)
        answer = FakePopen.answers.get(slot, "")
        if slot == 0 and FakePopen.busy_polls > 0:
            FakePopen.busy_polls -= 1
            answer = ""
        text, code = answer if isinstance(answer, tuple) else (answer, None)
        read_fd, write_fd = os.pipe()
        writer = os.fdopen(write_fd, "wb", buffering=0)
        if text == "hang":
            self._write = writer  # never answers
        elif text.startswith("later:"):
            head, tail = text[len("later:"):].split("|", 1)
            writer.write(head.encode())

            def rest():
                time.sleep(0.05)
                writer.write(tail.encode() + b"\n")
                if not FakePopen.keep_open:
                    writer.close()
            threading.Thread(target=rest, daemon=True).start()
            self._write = writer
        else:
            writer.write(text.encode() + b"\n" if text else b"")
            if FakePopen.keep_open and "LOCKED" in text:
                self._write = writer
            else:
                writer.close()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self.stdin = open(os.devnull, "wb")
        locked = "LOCKED" in str(text).replace("|", "\n").split("\n")
        self.returncode = code if code is not None else (0 if locked else 1)
        self.killed = False
        FakePopen.instances.append(self)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_holder(monkeypatch):
    monkeypatch.setattr(session.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(session, "QUEUE_POLL_SECONDS", 0.05)
    monkeypatch.setattr(session, "LOCK_TIMEOUT", 1.0)  # generous: the partial-line test sleeps
    FakePopen.answers, FakePopen.started, FakePopen.busy_polls = {}, [], 0
    FakePopen.instances, FakePopen.keep_open = [], False
    return FakePopen


def make_session(slots=1, **kw):
    host = cfg.HostConfig(name="h", ssh="h", workers=1, slots=slots)
    remote = plan.plan_remote(host, Path("/x"), "l", ssh_config="/c", control_dir="/d")
    return session.Session(checkout=Path("/x"), settings=cfg.ProjectSettings(), remotes=(remote,),
                           report=lambda line: None, source_host="l", **kw)


def test_take_slot_picks_the_first_free_slot(fake_holder):
    fake_holder.answers = {0: "", 1: "LOCKED"}
    hs = session.HostSession(remote=make_session(slots=2).remotes[0])
    make_session(slots=2).take_slot(hs)
    assert hs.slot == 1 and hs.lock is not None
    assert fake_holder.started == [0, 1]


def test_take_slot_queues_until_free(fake_holder):
    fake_holder.answers = {0: "LOCKED"}
    fake_holder.busy_polls = 3
    lines = []
    s = make_session()
    s.report = lines.append
    hs = session.HostSession(remote=s.remotes[0])
    s.take_slot(hs)
    assert hs.slot == 0
    assert lines.count("hosts| h busy, queued...") == 3  # one line per poll
    assert fake_holder.started == [0, 0, 0, 0]


def test_take_slot_reads_past_ssh_notices(fake_holder):
    """A known_hosts notice arrives in the same write as the token and the
    holder keeps the pipe open, as the remote cat does: the token must be
    seen at once, not after the deadline."""
    fake_holder.keep_open = True
    fake_holder.answers = {0: "Warning: Permanently added 'h' to the list of known hosts.\nLOCKED"}
    hs = session.HostSession(remote=make_session().remotes[0])
    make_session().take_slot(hs)
    assert hs.slot == 0 and hs.lock is not None


def test_take_slot_busy_after_a_notice_is_still_busy(fake_holder):
    fake_holder.answers = {0: "Warning: something"}  # then EOF, exit 1
    with pytest.raises(session.HostError, match="all 1 slot"):
        make_session(no_wait=True).take_slot(session.HostSession(remote=make_session().remotes[0]))


def test_take_slot_waits_out_a_partial_line(fake_holder):
    """A notice arrives without its newline first; the token follows later
    in the same line stream."""
    fake_holder.keep_open = True
    fake_holder.answers = {0: "later:Warning: half|-line\nLOCKED"}
    hs = session.HostSession(remote=make_session().remotes[0])
    make_session().take_slot(hs)
    assert hs.slot == 0


def test_take_slot_reports_an_unopenable_lock_file(fake_holder):
    # util-linux flock: exit 66, with the reason on stderr
    fake_holder.answers = {0: ("flock: cannot open lock file /tmp/ph/0: Permission denied", 66)}
    with pytest.raises(session.HostError, match=r"slot check failed \(exit 66\): flock: cannot open"):
        make_session().take_slot(session.HostSession(remote=make_session().remotes[0]))
    fake_holder.answers = {0: ("", 255)}
    with pytest.raises(session.HostError, match=r"slot check failed \(exit 255\): no output"):
        make_session().take_slot(session.HostSession(remote=make_session().remotes[0]))


def test_close_releases_the_holder_and_its_pipe(fake_holder):
    fake_holder.answers = {0: "LOCKED"}
    s = make_session()
    hs = session.HostSession(remote=s.remotes[0])
    s.take_slot(hs)
    s.hosts = [hs]
    s.close()
    assert hs.lock is None
    assert hs.drain is not None and not hs.drain.is_alive()
    assert fake_holder.instances[0].stdout.closed
    s.close()  # idempotent


def test_take_slot_reports_an_ssh_failure(fake_holder):
    fake_holder.answers = {0: ("ssh: connect to host h port 22: Connection refused", 255)}
    with pytest.raises(session.HostError, match=r"slot check failed \(exit 255\): ssh: connect"):
        make_session().take_slot(session.HostSession(remote=make_session().remotes[0]))


def test_take_slot_no_wait_fails_at_once(fake_holder):
    fake_holder.answers = {0: ""}
    with pytest.raises(session.HostError, match="all 1 slot"):
        make_session(no_wait=True).take_slot(session.HostSession(remote=make_session().remotes[0]))


def test_take_slot_stops_when_the_session_is_cancelled(fake_holder):
    fake_holder.answers = {0: ""}
    s = make_session()
    hs = session.HostSession(remote=s.remotes[0])
    outcome = []

    def run():
        try:
            s.take_slot(hs)
        except session.HostError as exc:
            outcome.append(str(exc))
    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.12)
    s._cancel.set()
    t.join(timeout=2)
    assert not t.is_alive()
    assert outcome and "cancelled" in outcome[0]


def test_take_slot_times_out_on_a_silent_host(fake_holder):
    fake_holder.answers = {0: "hang"}
    with pytest.raises(session.HostError, match="no answer from the slot command"):
        make_session().take_slot(session.HostSession(remote=make_session().remotes[0]))
    assert fake_holder.started == [0]
    # the silent holder was killed, so the remote flock (if any) is released
    assert all(p.killed for p in fake_holder.instances)


def test_prepare_releases_when_one_host_fails_while_another_queues(fake_holder, monkeypatch):
    """The failing host cancels the queued one, so prepare returns instead of
    joining a thread that would wait forever, and the slot is released."""
    fake_holder.answers = {0: ""}
    good = cfg.HostConfig(name="queued", ssh="q", workers=1)
    bad = cfg.HostConfig(name="broken", ssh="b", workers=1)
    remotes = tuple(plan.plan_remote(h, Path("/x"), "l", ssh_config="/c", control_dir="/d")
                    for h in (good, bad))
    s = session.Session(checkout=Path("/x"), settings=cfg.ProjectSettings(), remotes=remotes,
                        report=lambda line: None, source_host="l")

    def probe(hs):
        if hs.name == "broken":
            raise session.HostError("broken: unreachable")
    monkeypatch.setattr(s, "probe", probe)
    monkeypatch.setattr(session, "write_ssh_config", lambda: "/c")
    monkeypatch.setattr(session, "sync_excludes", lambda checkout: [])  # git, not the fake holder
    monkeypatch.setattr(session, "synced_listing", lambda *a: set())
    outcome: list[object] = []

    def run():  # a watchdog thread: a regression must fail, not hang the suite
        try:
            s.prepare()
        except session.HostError as exc:
            outcome.append(exc)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive(), "prepare() did not return after one host failed"
    assert outcome and "broken: unreachable" in str(outcome[0])
    assert all(hs.lock is None for hs in s.hosts)


def test_pull_all_skips_hosts_that_never_prepared(monkeypatch):
    s = make_session()
    ready = session.HostSession(remote=s.remotes[0], prepared=True)
    never = session.HostSession(remote=s.remotes[0])
    s.hosts = [never, ready]
    pulled = []
    monkeypatch.setattr(s, "pull", lambda hs: pulled.append(hs))
    s.pull_all()
    assert pulled == [ready]


def test_probe_learns_home_path_and_tmp(monkeypatch):
    s = make_session()
    hs = session.HostSession(remote=s.remotes[0])
    monkeypatch.setattr(session.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, stdout="/home/u\n/usr/bin:/bin\n/scratch/\n", stderr=""))
    s.probe(hs)
    assert hs.remote.parent.startswith("/home/u/.pytest-hosts/")
    assert hs.path == "/usr/bin:/bin"
    assert hs.tmp == "/scratch/"
    assert hs.worker_env(0)["PYTEST_DEBUG_TEMPROOT"].startswith("/scratch/pytest-hosts-")


def test_run_converts_a_timeout_into_a_host_error():
    s = make_session()
    with pytest.raises(session.HostError, match="x gave no answer in 1s"):
        s.run(["sleep", "5"], timeout=1, what="x")


def test_synced_listing_is_rsyncs_own_view(tmp_path, monkeypatch):
    """Symlinks, a `!` negation rsync does not read as git does, a nested
    .gitignore pattern rsync matches at any depth, an ignore pattern and a
    git-only exclude: the listing says what rsync would send, not git."""
    repo = tmp_path / "repo"
    (repo / "sub" / "src" / "foo").mkdir(parents=True)
    (repo / "examples").mkdir()
    (repo / "nested").mkdir()
    (repo / "a.txt").write_text("")
    (repo / "link").symlink_to("a.txt")
    (repo / ".gitignore").write_text("*.log\n!keep.log\n")
    (repo / "run.log").write_text("")
    (repo / "keep.log").write_text("")  # git would keep it; rsync's filter drops it
    (repo / "sub" / ".gitignore").write_text("foo/bar\n")
    (repo / "sub" / "src" / "foo" / "bar").write_text("")  # rsync excludes at any depth
    (repo / "examples" / "ex.py").write_text("")
    (repo / "nested" / "other.py").write_text("")
    (repo / "caf\u00e9.txt").write_text("")  # non-ASCII: escaped by rsync under a C locale
    (repo / "new\nline.txt").write_text("")  # a control character: always escaped
    monkeypatch.setenv("LC_ALL", "C")
    listing = session.synced_listing(repo, ("examples",), ["nested"])
    assert listing == {"a.txt", "link", ".gitignore", "sub/.gitignore", "caf\u00e9.txt",
                       "new\nline.txt"}
    assert session.synced_listing(tmp_path / "missing", (), []) is None


def test_deletions_follow_the_run_but_local_changes_stay(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name in ("gone.txt", "kept.txt", "changed.txt", "examples/ex.py"):
        (repo / name).parent.mkdir(exist_ok=True)
        (repo / name).write_text("x")
    old = time.time() - 100
    for name in ("gone.txt", "kept.txt", "examples/ex.py"):
        os.utime(repo / name, (old, old))
    s = session.Session(checkout=repo, settings=cfg.ProjectSettings(ignore=("examples",)),
                        remotes=(REMOTE,), report=lambda line: None, source_host="l")
    s.started_at = time.time() - 50
    (repo / "lnk").symlink_to("kept.txt")
    os.utime(repo / "lnk", (old, old), follow_symlinks=False)
    (repo / "kept.txt").touch()  # a fresh target: the link's own mtime must decide
    (repo / "dangling").symlink_to("nowhere")
    os.utime(repo / "dangling", (old, old), follow_symlinks=False)
    s.synced_files = session.synced_listing(repo, ("examples",), [])
    assert "lnk" in s.synced_files and "examples/ex.py" not in s.synced_files
    (repo / "changed.txt").touch()  # changed during the run
    (repo / "born.txt").write_text("")  # born during the run: never sent, never looked at
    hs = session.HostSession(remote=REMOTE)
    host_files = {"kept.txt": "1"}  # the run deleted gone.txt, changed.txt and the symlink
    lines = []
    s.report = lines.append
    s.delete_what_the_run_deleted(hs, host_files)
    assert hs.deleted == ["dangling", "gone.txt", "lnk"]
    assert not (repo / "gone.txt").exists() and not (repo / "lnk").is_symlink()
    assert not (repo / "dangling").is_symlink()
    assert (repo / "kept.txt").read_text() == "x"  # the link's target is untouched
    assert (repo / "kept.txt").exists() and (repo / "changed.txt").exists()
    assert (repo / "examples" / "ex.py").exists() and (repo / "born.txt").exists()
    assert any("changed.txt was deleted on the host but changed locally" in l for l in lines)
    assert not any("born.txt" in l for l in lines)
