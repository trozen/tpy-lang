"""`pytest-hosts status | setup | clean` for the project in the cwd."""

from __future__ import annotations

import argparse
import shlex
import socket
import subprocess
import sys
from pathlib import Path

from . import config as cfg
from .plan import RemoteHost, plan_remote
from .session import (PROBE_TIMEOUT, HostError, HostSession, Session, control_dir, ssh_argv,
                      ssh_config_path, write_ssh_config)


def load(start: Path) -> tuple[cfg.Project, tuple[RemoteHost, ...]]:
    try:
        project = cfg.find_project(start)
        if project is None:
            sys.exit("pytest-hosts: no pyproject.toml with a [project] name above the cwd")
        hosts_file = cfg.load_hosts_file(cfg.default_hosts_path(), project.checkout)
    except cfg.ConfigError as exc:
        sys.exit(f"pytest-hosts: {exc}")
    if hosts_file is None:
        sys.exit(f"pytest-hosts: no hosts file at {cfg.default_hosts_path()}")
    resolved = cfg.project_hosts(hosts_file, project.name)
    if resolved is None:
        sys.exit(f"pytest-hosts: no [projects.{project.name}] entry in the hosts file")
    remotes = tuple(plan_remote(h, project.checkout, socket.gethostname(),
                                ssh_config=ssh_config_path(), control_dir=control_dir(),
                                use_venv=bool(project.settings.setup))
                    for h in resolved.hosts)
    return project, remotes


def make_session(project: cfg.Project, remotes: tuple[RemoteHost, ...], **kw) -> Session:
    write_ssh_config()
    return Session(checkout=project.checkout, settings=project.settings, remotes=remotes,
                   report=print, source_host=socket.gethostname(), **kw)


def status_command(remote: RemoteHost) -> str:
    """One remote round trip: which slots are free and whether the tree and
    venv exist. Each flock -n either succeeds and exits at once or fails."""
    host = remote.host
    lock_dir = shlex.quote(host.lock_dir)
    probes = " ".join(
        f'if flock -n {lock_dir}/{n} true 2>/dev/null; then echo "slot {n} free"; '
        f'else echo "slot {n} busy"; fi;' for n in range(host.slots))
    return (f"mkdir -p {lock_dir} 2>/dev/null; {probes} "
            f"[ -d {shlex.quote(remote.tree)} ] && echo tree || echo no-tree; "
            f"[ -e {shlex.quote(remote.venv)} ] && echo venv || echo no-venv")


def status(project: cfg.Project, remotes: tuple[RemoteHost, ...]) -> int:
    session = make_session(project, remotes)
    print(f"project {project.name} at {project.checkout} (this host: {session.source_host})")
    failed = 0
    for remote in remotes:
        hs = HostSession(remote=remote)
        try:
            session.probe(hs)
        except HostError as exc:
            print(f"  {exc}")  # names the host itself
            failed += 1
            continue
        try:
            proc = session.run(ssh_argv(hs.remote, status_command(hs.remote)),
                               timeout=PROBE_TIMEOUT * 3, what=f"{hs.name}: status command")
        except HostError as exc:
            print(f"  {hs.name}: reachable, but {str(exc).partition(': ')[2]}")
            failed += 1
            continue
        facts = proc.stdout.split()
        slots = [f for f in proc.stdout.splitlines() if f.startswith("slot")]
        free = sum(1 for line in slots if line.endswith("free"))
        tree = "tree present" if "tree" in facts else "no tree"
        venv = "venv present" if "venv" in facts else "no venv"
        print(f"  {hs.name}: reachable, {free}/{remote.host.slots} slot(s) free, "
              f"{remote.workers} workers, {tree}, {venv}")
        print(f"    {hs.remote.tree}")
    return 1 if failed else 0


def setup(project: cfg.Project, remotes: tuple[RemoteHost, ...]) -> int:
    session = make_session(project, remotes, force_setup=True)
    try:
        session.prepare(take_slot=False)
    except HostError as exc:
        print(f"pytest-hosts: {exc}", file=sys.stderr)
        return 1
    return 0


def clean(project: cfg.Project, remotes: tuple[RemoteHost, ...]) -> int:
    session = make_session(project, remotes)
    failed = 0
    for remote in remotes:
        hs = HostSession(remote=remote)
        try:
            session.probe(hs)
            targets = f"{shlex.quote(hs.remote.parent)} {shlex.quote(hs.remote.tmp_root(hs.tmp))}"
            session.run(ssh_argv(hs.remote, f"rm -rf {targets}"), what=f"{hs.name}: clean")
        except HostError as exc:
            print(f"  {exc}")  # names the host itself
            failed += 1
            continue
        print(f"  {hs.name}: removed {hs.remote.parent} and {hs.remote.tmp_root(hs.tmp)}")
    return 1 if failed else 0


def config_reference() -> int:
    print(cfg.HOSTS_REFERENCE)
    print(cfg.PROJECT_REFERENCE, end="")
    return 0


COMMANDS = {
    "status": "reachability, free slots, this checkout's trees",
    "setup": "sync the trees and run the setup command, no tests",
    "clean": "remove this checkout's trees, venvs and temp roots from every host",
    "config": "print the annotated reference for both config files",
}


def overview(start: Path) -> int:
    """Bare `pytest-hosts`: what the config resolves to for this checkout,
    where it came from, and the sub-commands. Informational, never fails."""
    hosts_path = cfg.default_hosts_path()
    print(f"hosts file: {hosts_path} ({'found' if hosts_path.is_file() else 'missing'})")
    project = hosts_file = resolved = None
    project_broken = hosts_broken = False
    try:
        project = cfg.find_project(start)
    except cfg.ConfigError as exc:
        print(f"config error: {exc}")
        project_broken = True
    try:
        hosts_file = cfg.load_hosts_file(hosts_path, project.checkout if project else None)
        resolved = cfg.project_hosts(hosts_file, project.name) if hosts_file and project else None
    except cfg.ConfigError as exc:
        print(f"config error: {exc}")
        hosts_broken = True
    if project_broken:
        print("project: unusable (see the error above)")
    elif project is None:
        print("project: none (no pyproject.toml with a [project] name above the cwd)")
    else:
        override = project.checkout / cfg.CHECKOUT_OVERRIDE_NAME
        if override.is_file():
            print(f"checkout override: {override}")
        print(f"project: {project.name} at {project.checkout}")
        settings = project.settings
        print(f"  setup: {settings.setup or 'none (workers use the host python3)'}"
              + (f", re-run when {', '.join(settings.setup_when)} change" if settings.setup_when else ""))
        if settings.env:
            print(f"  env forwarded: {', '.join(settings.env)}")
        if settings.ignore:
            print(f"  sync ignores: {', '.join(settings.ignore)}")
        if not settings.pull:
            print("  pull-back: off")
        if hosts_file is None and hosts_broken:
            print("  runs locally: the hosts file is unusable (see the error above)")
        elif hosts_file is None:
            print("  runs locally: no hosts file (pytest-hosts config prints a template)")
        elif resolved is None:
            print(f"  runs locally: no [projects.{project.name}] entry in the hosts file")
        else:
            print(f"  local workers: {resolved.local}"
                  + (" (every test runs on the hosts)" if resolved.local == 0 else ""))
            for host in resolved.hosts:
                extra = f", unreachable -> {host.unreachable}"
                if host.path_prepend:
                    extra += f", PATH+={host.path_prepend}"
                print(f"  {host.name}: ssh {host.ssh}, {host.workers} workers, "
                      f"{host.slots} slot(s), root {host.root}{extra}")
    print("commands:")
    for name, help_text in COMMANDS.items():
        print(f"  pytest-hosts {name:<7} {help_text}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pytest-hosts",
                                     description="remote build hosts for the project in the cwd; "
                                                 "without a command: the resolved config")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    args = parser.parse_args(argv)
    if args.command is None:
        return overview(Path.cwd())
    if args.command == "config":
        return config_reference()
    project, remotes = load(Path.cwd())
    return {"status": status, "setup": setup, "clean": clean}[args.command](project, remotes)
