"""Decide what a session does and turn that into xdist worker specs.

Pure functions over config records and a few facts about the invocation,
so the precedence rules are unit-testable without pytest running.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from .config import HostConfig, HostsFile, Workers, project_hosts

TREE_ID_LENGTH = 8
VENV_NAME = "venv"
# sshd's default MaxSessions is 10 per connection; past it ssh falls back to
# a fresh connection per worker, noisily. Workers share master connections
# in groups below that, leaving headroom for the plugin's own sessions.
SESSIONS_PER_CONNECTION = 8


@dataclass(frozen=True)
class RemoteHost:
    """One host's share of the session, with the remote paths it uses.

    `parent` is the spec's chdir: xdist rewrites path args to
    `<checkout name>/<rel>` under it, so the tree must keep the checkout's
    own name and the per-checkout hash sits on the parent.
    """

    host: HostConfig
    workers: int
    parent: str
    tree: str
    venv: str
    ssh_config: str
    control_dir: str
    python: str
    tmp_base: str | None = None  # the configured `tmp`, with `~` expanded like `root`
    slot: int | None = None  # the host slot this session holds, once taken

    @property
    def stamp(self) -> str:
        return f"{self.parent}/stamp"

    @property
    def setup_hash_file(self) -> str:
        return f"{self.parent}/setup.hash"

    def tmp_root(self, host_tmp: str) -> str:
        """Where this checkout's workers keep their temp files on the host:
        under the configured `tmp`, else the host's own temp dir."""
        base = (self.tmp_base or host_tmp).rstrip("/")
        return f"{base}/pytest-hosts-{self.parent.rsplit('/', 1)[1]}"

    def worker_tmp(self, host_tmp: str, index: int) -> str:
        return f"{self.tmp_root(host_tmp)}/w{index}"


def tree_id(source_host: str, checkout: Path) -> str:
    """Eight hex chars naming this checkout on this machine; never shared."""
    digest = hashlib.sha256(f"{source_host}\0{checkout.resolve()}".encode()).hexdigest()
    return digest[:TREE_ID_LENGTH]


def plan_remote(host: HostConfig, checkout: Path, source_host: str, *, ssh_config: str,
                control_dir: str, workers: int | None = None, home: str | None = None,
                use_venv: bool = True) -> RemoteHost:
    """Lay out one host. `home` replaces a leading `~` in the host's root
    once the probe has learned it (execnet chdirs without expanding it).
    Without a setup command there is no venv to point at, so the workers
    run whatever `python3` the ssh session finds."""
    root = _expand_home(host.root, home)
    parent = f"{root.rstrip('/')}/{tree_id(source_host, checkout)}"
    venv = f"{parent}/{VENV_NAME}"
    return RemoteHost(
        host=host,
        workers=host.workers if workers is None else workers,
        parent=parent,
        tree=f"{parent}/{checkout.resolve().name}",
        venv=venv,
        ssh_config=host.ssh_config or ssh_config,
        control_dir=control_dir,
        python=f"{venv}/bin/python" if use_venv else "python3",
        tmp_base=_expand_home(host.tmp, home) if host.tmp else None,
    )


def _expand_home(path: str, home: str | None) -> str:
    """A leading `~` becomes the probed home; until the probe ran it stays
    (execnet chdirs without expanding it, so nothing may use it before)."""
    if home is not None and (path == "~" or path.startswith("~/")):
        return home + path[1:]
    return path


def with_home(remote: RemoteHost, home: str, checkout: Path, source_host: str) -> RemoteHost:
    return plan_remote(remote.host, checkout, source_host, ssh_config=remote.ssh_config,
                       control_dir=remote.control_dir, workers=remote.workers, home=home,
                       use_venv=remote.python != "python3")


def remote_spec(remote: RemoteHost, index: int) -> str:
    """The execnet spec string for worker `index` on `remote`. execnet splits
    the `ssh=` value into ssh arguments, which is how each worker names the
    control socket of its group (ControlMaster auto in the generated config
    makes the group's first worker the master). The slot is part of the
    socket name because concurrent sessions from this machine would
    otherwise pile their workers onto one master and overrun MaxSessions."""
    group = index // SESSIONS_PER_CONNECTION
    slot = "" if remote.slot is None else f"s{remote.slot}-"
    control = f"{remote.control_dir}/cm-{remote.host.name}-{slot}{group}"
    return (f"ssh=-o ControlPath={control} {remote.host.ssh}//python={remote.python}"
            f"//chdir={remote.parent}//ssh_config={remote.ssh_config}")


def remote_specs(remote: RemoteHost) -> list[str]:
    return [remote_spec(remote, i) for i in range(remote.workers)]


@dataclass(frozen=True)
class Untouched:
    """Leave xdist's own decision alone. `warning` is printed when set."""

    warning: str | None = None


@dataclass(frozen=True)
class LocalOnly:
    workers: Workers
    reason: str  # the switch, or why the config leaves nothing to distribute


@dataclass(frozen=True)
class Distributed:
    local: Workers
    remotes: tuple[RemoteHost, ...]


Mode = Untouched | LocalOnly | Distributed


def cmdline_has_numprocesses(args: list[str] | tuple[str, ...]) -> bool:
    """Whether `-n` / `--numprocesses` was typed, as opposed to coming from addopts."""
    for arg in args:
        if arg == "--":
            return False
        if arg == "-n" or arg.startswith("-n") and not arg.startswith("--"):
            return True
        if arg == "--numprocesses" or arg.startswith("--numprocesses="):
            return True
    return False


def decide(*, hosts_file: HostsFile | None, project: str | None, checkout: Path | None,
           source_host: str, cmdline_n: bool, local_only: str | None, only: str | None,
           ssh_config: str = "", control_dir: str = "", use_venv: bool = True) -> Mode:
    """The precedence rules from the README, in order:

    a command-line `-n` beats everything; no config or no project entry
    means nothing changes; `--hosts-local` / `PYTEST_HOSTS=0` (`local_only`
    names the one given) pin the session to this machine with the
    config's local count; `--hosts-only` drops the local workers and
    every other host. Both switches at once contradict each other.
    """
    if cmdline_n or hosts_file is None:
        return Untouched()
    if project is None or checkout is None:
        return Untouched(warning="pytest-hosts: no pyproject.toml with a [project] name "
                                 "above the rootdir; running locally")
    resolved = project_hosts(hosts_file, project)
    if resolved is None:
        return Untouched(warning=f"pytest-hosts: no [projects.{project}] entry in the hosts "
                                 f"file; running locally (add one to use remote hosts)")
    if local_only and only is not None:
        raise ValueError(f"{local_only} and --hosts-only={only} contradict each other")
    if local_only:
        return LocalOnly(resolved.local, reason=local_only)
    remotes = tuple(plan_remote(h, checkout, source_host, ssh_config=ssh_config,
                                control_dir=control_dir, use_venv=use_venv)
                    for h in resolved.hosts)
    local: Workers = resolved.local
    if only is not None:
        chosen = [r for r in remotes if r.host.name == only]
        if not chosen:
            names = ", ".join(h.name for h in resolved.hosts) or "none"
            raise ValueError(f"--hosts-only={only}: not a host of [projects.{project}] "
                             f"(configured: {names})")
        remotes = tuple(chosen)
        local = 0
    if not remotes:
        return LocalOnly(local, reason=f"[projects.{project}] selects no hosts")
    return Distributed(local=local, remotes=remotes)


def absolute_args(invocation_args: tuple[str, ...] | list[str], invocation_dir: Path,
                  effective_args: list[str], typed: bool,
                  takes_value: Collection[str] = ()) -> list[str]:
    """The invocation args a distributed session sends to its workers.

    xdist ships the typed args and rewrites each existing path against its
    rsync roots, which fails for a relative path; with no path typed a
    remote worker would take its chdir as rootdir, since the ini file is
    a level down. So typed paths become absolute, and when none were typed
    the paths pytest settled on (testpaths or the invocation dir) are
    appended explicitly, absolute, so every worker collects the same ids.

    `effective_args` is what pytest took as paths (config.args); when
    typed, only those are rewritten, and only where they stand as
    positionals: an arg that follows an option in `takes_value` is that
    option's value even when it spells a path (`-k tests tests`).
    """
    out: list[str] = []
    for i, arg in enumerate(invocation_args):
        is_value = i > 0 and invocation_args[i - 1] in takes_value
        if typed and arg in effective_args and not is_value:
            path, sep, rest = arg.partition("::")
            out.append(str((invocation_dir / path).resolve()) + sep + rest)
        else:
            out.append(arg)
    if not typed:
        out += [str((invocation_dir / a).resolve()) for a in effective_args]
    return out
