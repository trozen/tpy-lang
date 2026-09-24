"""Everything that talks to a host: probe, slot gate, sync, setup, release.

Command lines are built by pure functions so they can be unit-tested; the
`Session` runs them. Every ssh goes through one generated config that
turns on a ControlMaster, so the probe opens the connection and the sync,
the lock holder and execnet's workers all multiplex over it.
"""

from __future__ import annotations

import hashlib
import os
import select
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import HostConfig, ProjectSettings
from .plan import RemoteHost, with_home

PROBE_TIMEOUT = 10
LOCK_TIMEOUT = 60  # for the slot command to answer once the session is open
COMMAND_TIMEOUT = 1800  # any other ssh or rsync round trip: a sync or a setup can be long
QUEUE_POLL_SECONDS = 30
LOCKED_TOKEN = "LOCKED"
PENDING_LIMIT = 65536  # bytes of an unterminated holder line kept for the error message
SEEN_LIMIT = 50  # holder lines kept for the error message

Report = Callable[[str], None]


class HostError(Exception):
    """A host could not be used. The message says which and why."""


class Cancelled(HostError):
    """A host gave up because the session was cancelled; never the root cause."""


# --- generated ssh config -------------------------------------------------


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(os.path.expanduser("~"), ".cache")
    path = Path(base) / "pytest-hosts"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def ssh_config_text(control_dir: Path) -> str:
    # The user's own config comes first so anything it sets per host wins
    # (ssh keeps the first value it sees for an option).
    return (
        "Include ~/.ssh/config\n"
        "Host *\n"
        "    ControlMaster auto\n"
        f"    ControlPath {control_dir}/cm-%C\n"
        "    ControlPersist 300\n"
        "    BatchMode yes\n"
        "    ServerAliveInterval 30\n"
    )


def ssh_config_path() -> str:
    return str(runtime_dir() / "ssh_config")


def control_dir() -> str:
    return str(runtime_dir())


def write_ssh_config() -> str:
    path = Path(ssh_config_path())
    text = ssh_config_text(path.parent)
    if not path.exists() or path.read_text() != text:
        path.write_text(text)
        path.chmod(0o600)
    return str(path)


# --- command lines ----------------------------------------------------------


def remote_shell(host: HostConfig, command: str) -> str:
    """Wrap a remote command so a configured PATH prefix applies to it."""
    if host.path_prepend:
        return f"export PATH={shlex.quote(host.path_prepend)}:$PATH; {command}"
    return command


def ssh_argv(remote: RemoteHost, command: str, *, connect_timeout: int | None = None,
             raw: bool = False) -> list[str]:
    """`raw` skips the PATH prefix: the probe reports the host's own PATH,
    which is what the prefix is later put in front of."""
    argv = ["ssh", "-F", remote.ssh_config]
    if connect_timeout is not None:
        argv += ["-o", f"ConnectTimeout={connect_timeout}"]
    return argv + [remote.host.ssh, command if raw else remote_shell(remote.host, command)]


def probe_command() -> str:
    """Home (to make the root absolute), the non-interactive PATH (the base
    a configured path_prepend goes in front of for the workers) and the
    host's temp dir (where the workers' temp roots go)."""
    return 'printf "%s\\n%s\\n%s" "$HOME" "$PATH" "${TMPDIR:-/tmp}"'


def lock_command(host: HostConfig, slot: int) -> str:
    """Hold slot `slot` for as long as stdin stays open. flock's `-n` fails
    at once when the slot is taken, so the caller sees exit 1 instead of
    the token. The dir is world-writable so users of one box share the
    gate."""
    lock_dir = shlex.quote(host.lock_dir)
    return (f"mkdir -p {lock_dir} 2>/dev/null; chmod 1777 {lock_dir} 2>/dev/null; "
            f"exec flock -n {lock_dir}/{slot} -c 'echo {LOCKED_TOKEN}; exec cat'")


def rsync_argv(remote: RemoteHost, checkout: Path, ignore: tuple[str, ...]) -> list[str]:
    """Mirror the checkout into the tree. gitignore files act as per-directory
    filters, and `--delete` without `--delete-excluded` leaves ignored files
    the host already has (build output, caches) in place across syncs."""
    argv = ["rsync", "-a", "--delete", "--exclude=.git", "--filter=:- .gitignore"]
    argv += [f"--exclude={pattern}" for pattern in ignore]
    mkdir = remote_shell(remote.host, f"mkdir -p {shlex.quote(remote.tree)} && rsync")
    argv += [f"--rsync-path={mkdir}", "-e", f"ssh -F {shlex.quote(remote.ssh_config)}"]
    argv += [f"{checkout}/", f"{remote.host.ssh}:{remote.tree}/"]
    return argv


def setup_hash(settings: ProjectSettings, checkout: Path) -> str:
    h = hashlib.sha256((settings.setup or "").encode())
    for name in settings.setup_when:
        path = checkout / name
        h.update(b"\0" + name.encode() + b"\0")
        h.update(path.read_bytes() if path.is_file() else b"<missing>")
    return h.hexdigest()[:16]


def after_sync_command(remote: RemoteHost, settings: ProjectSettings, digest: str, *,
                       force_setup: bool, local_pytest: str | None = None,
                       host_tmp: str = "/tmp") -> str:
    """Run the setup command when the venv is missing, the setup inputs
    changed, or it was asked for; then touch the stamp, so what setup wrote
    in the tree (a lockfile) does not count as written by the run. Prints
    SETUP when setup ran, and PYTEST=<version> for the tree's pytest.

    xdist ships its own pytest package to every worker's chdir, one serial
    transfer per worker. When the tree's pytest is the controller's version
    the session tells xdist the roots are in place instead, so any copy a
    previous controller shipped there is removed here: it would shadow the
    venv's."""
    parts = []
    if settings.setup:
        run = (f"export UV_PROJECT_ENVIRONMENT={shlex.quote(remote.venv)}; "
               f"bash -lc {shlex.quote(settings.setup)} && "
               f"printf %s {digest} > {shlex.quote(remote.setup_hash_file)} && echo SETUP")
        if force_setup:
            parts.append(run)
        else:
            stale = (f'[ ! -e {shlex.quote(remote.venv)} ] || '
                     f'[ "$(cat {shlex.quote(remote.setup_hash_file)} 2>/dev/null)" != {digest} ]')
            parts.append(f"if {stale}; then {run}; fi")
    parts.append(f"touch {shlex.quote(remote.stamp)}")
    # pytest needs the temp root to exist; one per worker
    roots = " ".join(shlex.quote(remote.worker_tmp(host_tmp, i)) for i in range(remote.workers))
    parts.append(f"mkdir -p {roots}")
    if local_pytest is not None:
        version = (f'v=$({shlex.quote(remote.python)} -c "import pytest; print(pytest.__version__)")'
                   f' && echo "PYTEST=$v"')
        stale_copies = " ".join(shlex.quote(f"{remote.parent}/{name}") for name in ("pytest", "_pytest"))
        parts.append(f'{version} && if [ "$v" = {shlex.quote(local_pytest)} ]; '
                     f'then rm -rf {stale_copies}; fi')
    return f"cd {shlex.quote(remote.tree)} && " + " && ".join(parts)


def written_files_command(remote: RemoteHost) -> str:
    """Paths under the tree with an mtime after the stamp: what the run wrote.
    Synced files keep their local mtimes, which predate the stamp."""
    return (f"cd {shlex.quote(remote.tree)} && "
            f"find . -type f -newer {shlex.quote(remote.stamp)} -print0")


def drop_ignored(checkout: Path, paths: list[str]) -> list[str]:
    """Filter out paths the checkout's gitignore rules cover. git answers
    with the real rules (nested files, negations, tracked-beats-ignored);
    outside a git checkout nothing is dropped."""
    if not paths:
        return []
    try:
        proc = subprocess.run(["git", "-C", str(checkout), "check-ignore", "--stdin", "-z"],
                              input="\0".join(paths), capture_output=True, text=True)
    except OSError:
        return paths
    if proc.returncode not in (0, 1):  # 128: not a git repository
        return paths
    ignored = set(proc.stdout.split("\0"))
    return [p for p in paths if p not in ignored]


def pull_argv(remote: RemoteHost, checkout: Path) -> list[str]:
    """Copy the listed files (NUL-separated on stdin) from the tree back.
    No `--update`: the remote run's version wins by design. `--ignore-times`
    because the list is already decided, and rsync's size-and-mtime quick
    check would skip a same-size file both sides wrote in the same second."""
    return ["rsync", "-a", "--ignore-times", "--from0", "--files-from=-",
            "-e", f"ssh -F {shlex.quote(remote.ssh_config)}",
            f"{remote.host.ssh}:{remote.tree}/", f"{checkout}/"]


# --- the session --------------------------------------------------------------


@dataclass
class HostSession:
    remote: RemoteHost
    slot: int | None = None
    lock: subprocess.Popen | None = None
    setup_ran: bool = False
    prepared: bool = False  # synced and set up: the only hosts pulled back from
    drain: threading.Thread | None = None  # reads what ssh says after LOCKED
    path: str = ""  # the host's non-interactive PATH, from the probe
    tmp: str = "/tmp"  # the host's temp dir, from the probe
    pytest_version: str | None = None  # the tree's; None until after_sync
    ran_tests: int = 0
    pulled: list[str] = field(default_factory=list)
    pull_error: str | None = None
    dropped: str | None = None  # why this host is not part of the run

    @property
    def name(self) -> str:
        return self.remote.host.name

    def worker_env(self, index: int) -> dict[str, str]:
        """What worker `index` on this host gets on top of the forwarded env.
        execnet launches the worker's python straight over ssh, so a
        configured path_prepend has to be applied there by hand. Each
        worker gets its own temp root: xdist hands a local worker its own
        basetemp but an ssh worker nothing, and eighty workers sharing one
        pytest-of-<user> dir race each other's numbered-dir cleanup."""
        env = {"PYTEST_DEBUG_TEMPROOT": self.remote.worker_tmp(self.tmp, index)}
        prepend = self.remote.host.path_prepend
        if prepend:
            env["PATH"] = f"{prepend}:{self.path}" if self.path else prepend
        return env

    def uses_tree_pytest(self, local_pytest: str) -> bool:
        return self.pytest_version == local_pytest


@dataclass
class Session:
    checkout: Path
    settings: ProjectSettings
    remotes: tuple[RemoteHost, ...]
    report: Report
    source_host: str
    no_wait: bool = False
    force_setup: bool = False
    local_pytest: str | None = None  # the controller's pytest version
    only: str | None = None  # --hosts-only: this host was asked for by name
    hosts: list[HostSession] = field(default_factory=list)
    started_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # Set when any host fails or the user interrupts: a host still queued
    # for a slot must stop waiting, or the pool never joins and the other
    # hosts' slots stay held.
    _cancel: threading.Event = field(default_factory=threading.Event)

    def say(self, line: str) -> None:
        with self._lock:
            self.report(f"hosts| {line}")

    def run(self, argv: list[str], *, check: bool = True, timeout: int = COMMAND_TIMEOUT,
            what: str = "", stdin: str | None = None) -> subprocess.CompletedProcess:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=stdin)
        except subprocess.TimeoutExpired:
            raise HostError(f"{what} gave no answer in {timeout}s") from None
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise HostError(f"{what} failed (exit {proc.returncode}): {detail}")
        return proc

    # -- steps, one host each --

    def probe(self, hs: HostSession) -> None:
        argv = ssh_argv(hs.remote, probe_command(), connect_timeout=PROBE_TIMEOUT, raw=True)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT * 3)
        except subprocess.TimeoutExpired:
            raise HostError(f"{hs.name}: unreachable (ssh timed out)") from None
        if proc.returncode != 0:
            raise HostError(f"{hs.name}: unreachable: {proc.stderr.strip()}")
        home, path, tmp = (proc.stdout.strip().split("\n") + ["", ""])[:3]
        if not home.startswith("/"):
            raise HostError(f"{hs.name}: probe returned no home directory ({home!r})")
        hs.remote = with_home(hs.remote, home, self.checkout, self.source_host)
        hs.path = path
        hs.tmp = tmp or "/tmp"

    def take_slot(self, hs: HostSession) -> None:
        host = hs.remote.host
        while True:
            for slot in range(host.slots):
                if self._try_slot(hs, slot):
                    return
            if self.no_wait:
                raise HostError(f"{hs.name}: all {host.slots} slot(s) busy (--hosts-no-wait)")
            self.say(f"{hs.name} busy, queued...")  # every poll: the run is alive
            if self._cancel.wait(QUEUE_POLL_SECONDS):
                raise Cancelled(f"{hs.name}: gave up waiting for a slot (session cancelled)")

    def _try_slot(self, hs: HostSession, slot: int) -> bool:
        """Start the holder for `slot`; True when it reports the lock. stderr
        rides on stdout so nothing is left unread on a pipe for the whole
        run, which means ssh notices (a known_hosts addition, a banner)
        can precede the token: bytes are read unbuffered after each
        select, so a token that arrived with a notice or a line without
        its newline yet cannot hide from the deadline, and lines are
        split by hand until the token or EOF. A daemon thread drains
        whatever comes later."""
        proc = subprocess.Popen(ssh_argv(hs.remote, lock_command(hs.remote.host, slot)),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=0)
        fd = proc.stdout.fileno()
        deadline = time.monotonic() + LOCK_TIMEOUT
        pending = b""
        seen: list[str] = []
        while True:
            ready, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                proc.kill()
                proc.wait()
                raise HostError(f"{hs.name}: no answer from the slot command in {LOCK_TIMEOUT}s"
                                + (": " + " / ".join(seen) if seen else ""))
            chunk = os.read(fd, 65536)
            if not chunk:  # EOF: flock did not run our command
                break
            pending = (pending + chunk)[-PENDING_LIMIT:]
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                text = line.decode(errors="replace").strip()
                if text == LOCKED_TOKEN:
                    hs.slot, hs.lock = slot, proc
                    hs.drain = threading.Thread(target=proc.stdout.read, daemon=True)
                    hs.drain.start()
                    return True
                seen.append(text)
                del seen[:-SEEN_LIMIT]
        if pending.strip():
            seen.append(pending.decode(errors="replace").strip())
        proc.wait()
        # util-linux flock exits 1 only for a taken lock (a lock file it
        # cannot open is 66), so any other code is ssh or the host
        if proc.returncode != 1:
            raise HostError(f"{hs.name}: slot check failed (exit {proc.returncode}): "
                            + (" / ".join(seen) or "no output"))
        return False

    def sync(self, hs: HostSession) -> None:
        self.run(rsync_argv(hs.remote, self.checkout, self.settings.ignore), what=f"{hs.name}: sync")

    def after_sync(self, hs: HostSession) -> None:
        digest = setup_hash(self.settings, self.checkout)
        command = after_sync_command(hs.remote, self.settings, digest, force_setup=self.force_setup,
                                     local_pytest=self.local_pytest, host_tmp=hs.tmp)
        proc = self.run(ssh_argv(hs.remote, command), what=f"{hs.name}: setup")
        lines = proc.stdout.splitlines()
        hs.setup_ran = "SETUP" in lines
        for line in lines:
            if line.startswith("PYTEST="):
                hs.pytest_version = line[len("PYTEST="):].strip()

    def prepare_host(self, hs: HostSession, take_slot: bool = True) -> None:
        self.say(f"{hs.name}: connecting...")
        try:
            self.probe(hs)
        except HostError as exc:
            if hs.remote.host.unreachable != "local":
                raise
            if self.only == hs.name:
                # Asked for by name: an explicit request outranks the host's
                # degrade policy, otherwise --hosts-only would silently run
                # nothing at all.
                raise HostError(f'{exc}; --hosts-only names it, so its unreachable = '
                                f'"local" policy does not apply') from None
            hs.dropped = str(exc)
            self.say(f"{exc}; running without {hs.name}")
            return
        if take_slot:
            self.take_slot(hs)
            self.say(f"{hs.name}: slot {hs.slot} taken, syncing the tree...")
        else:
            self.say(f"{hs.name}: syncing the tree...")
        self.sync(hs)
        self.after_sync(hs)
        hs.prepared = True
        slot = f"slot {hs.slot}, " if take_slot else ""
        note = ", setup ran" if hs.setup_ran else ""
        if self.local_pytest is not None and not hs.uses_tree_pytest(self.local_pytest):
            note += (f", tree has pytest {hs.pytest_version}, not {self.local_pytest}: "
                     f"xdist ships its own to every worker (slow)")
        self.say(f"{hs.name}: {slot}{hs.remote.workers} workers, tree {hs.remote.tree}{note}")

    def prepare(self, take_slot: bool = True) -> list[HostSession]:
        """Probe, gate, sync and set up every host, in parallel. Raises the
        first HostError after releasing whatever was taken."""
        write_ssh_config()
        self.started_at = time.time()
        self.hosts = [HostSession(remote=r) for r in self.remotes]

        def one(hs: HostSession) -> None:
            try:
                self.prepare_host(hs, take_slot)
            except BaseException:
                self._cancel.set()  # from inside: the pool joins before any outer handler runs
                raise

        pool = ThreadPoolExecutor(max_workers=max(1, len(self.hosts)))
        futures = [pool.submit(one, hs) for hs in self.hosts]
        try:
            wait_futures(futures)
        except BaseException:
            self._cancel.set()  # Ctrl-C arrives here, in the main thread
            wait_futures(futures)
            pool.shutdown(wait=True)
            self.close()
            raise
        pool.shutdown(wait=True)
        errors = [f.exception() for f in futures if f.exception() is not None]
        if errors:
            self.close()
            # the host that failed, not the one that stopped queueing because of it
            raise next((e for e in errors if not isinstance(e, Cancelled)), errors[0])
        return self.hosts

    def pull(self, hs: HostSession) -> None:
        """Bring back what the run wrote. The remote version wins; a local
        file also modified since the session started is overwritten with a
        warning. Files the run did not write are never touched."""
        proc = self.run(ssh_argv(hs.remote, written_files_command(hs.remote)),
                        what=f"{hs.name}: listing written files")
        paths = [p[2:] if p.startswith("./") else p for p in proc.stdout.split("\0") if p]
        paths = drop_ignored(self.checkout, sorted(paths))
        if not paths:
            return
        for path in paths:
            local = self.checkout / path
            try:
                if local.stat().st_mtime > self.started_at:
                    self.say(f"{hs.name}: {path} also changed locally during the run; "
                             f"the remote version wins")
            except OSError:
                pass
        self.run(pull_argv(hs.remote, self.checkout), stdin="\0".join(paths),
                 what=f"{hs.name}: pull-back")
        hs.pulled = paths
        for path in paths:
            self.say(f"{hs.name} -> {path}")

    def pull_all(self) -> None:
        """Best effort, so it also runs after Ctrl-C and internal errors. Only
        hosts that finished prepare have a tree to pull from."""
        for hs in self.hosts:
            if not hs.prepared:
                continue
            try:
                self.pull(hs)
            except (HostError, OSError) as exc:
                hs.pull_error = str(exc).strip()
                self.say(f"{hs.name}: pull-back failed: {hs.pull_error}")

    @property
    def active(self) -> list[HostSession]:
        return [hs for hs in self.hosts if hs.dropped is None]

    def close(self) -> None:
        for hs in self.hosts:
            if hs.lock is not None:
                proc, hs.lock = hs.lock, None
                try:
                    proc.stdin.close()  # the remote holder is `cat`: EOF ends it
                    proc.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    proc.kill()
                    proc.wait()
                if hs.drain is not None:
                    hs.drain.join(timeout=5)  # at EOF once the holder is gone
                if proc.stdout is not None and (hs.drain is None or not hs.drain.is_alive()):
                    # never under a thread still reading it: a holder that
                    # outlived the kill keeps its fd until the process ends
                    proc.stdout.close()
