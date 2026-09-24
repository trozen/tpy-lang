"""The pytest hooks. Everything with a decision in it lives in plan.py."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from . import config as cfg
from .plan import (Distributed, LocalOnly, Mode, Untouched, absolute_args,
                   cmdline_has_numprocesses, decide, remote_specs)
from .session import HostError, Session, control_dir, ssh_config_path

ENV_SWITCH = "PYTEST_HOSTS"

mode_key = pytest.StashKey[Mode]()
project_key = pytest.StashKey[cfg.Project]()
session_key = pytest.StashKey[Session]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("hosts", "pytest-hosts: remote build hosts")
    group.addoption("--hosts-local", action="store_true", default=False,
                    help="run on this machine only, with the config's local worker count")
    group.addoption("--hosts-only", metavar="NAME", default=None,
                    help="this run: only the named configured host, no local workers")
    group.addoption("--hosts-no-pull", action="store_true", default=False,
                    help="do not mirror what the remote run wrote or deleted back here")
    group.addoption("--hosts-no-wait", action="store_true", default=False,
                    help="fail instead of queueing when every slot of a host is busy")
    group.addoption("--hosts-setup", action="store_true", default=False,
                    help="run the project's setup command on every host this run")


def _local_switch(config: pytest.Config) -> str | None:
    """Which local-only switch was given, if any, for the startup line."""
    if config.option.hosts_local:
        return "--hosts-local"
    if os.environ.get(ENV_SWITCH, "").strip().lower() in ("0", "false", "no", "off"):
        return f"{ENV_SWITCH}=0"
    return None


def _value_options(config: pytest.Config) -> set[str]:
    """Option strings that consume the next arg, so a path-like value is not
    mistaken for a positional. Read off pytest's argparse parser (the
    `optparser` attribute since pytest 9, built by `_getparser()` before);
    private, so a missing attribute degrades to "no option takes a value"."""
    try:
        parser = config._parser
        argparser = parser.optparser if hasattr(parser, "optparser") else parser._getparser()
        return {name for action in argparser._actions if action.nargs != 0
                for name in action.option_strings}
    except AttributeError:
        return set()


def _worker_count(config: pytest.Config, workers: cfg.Workers) -> int:
    if workers == "auto":
        n = config.hook.pytest_xdist_auto_num_workers(config=config)
    else:
        n = int(workers)
    if config.option.maxprocesses:
        n = min(n, config.option.maxprocesses)  # xdist's cap applies to our count too
    return n


def _workers(n: int) -> str:
    return f"{n} worker" if n == 1 else f"{n} workers"


def _apply(config: pytest.Config, tx: list[str]) -> None:
    config.option.tx = tx
    if not tx:
        config.option.dist = "no"
    elif config.option.dist == "no":
        config.option.dist = "load"


# Plain (not tryfirst) so it runs after xdist's own pytest_cmdline_main,
# which has already turned an addopts `-n` into popen specs we replace.
def pytest_cmdline_main(config: pytest.Config) -> None:
    if os.environ.get("PYTEST_XDIST_WORKER") or not config.pluginmanager.hasplugin("xdist"):
        return
    try:
        project = cfg.find_project(config.rootpath)
        if project is not None:
            config.stash[project_key] = project
        hosts_file = cfg.load_hosts_file(cfg.default_hosts_path(),
                                         project.checkout if project else None)
        mode = decide(
            hosts_file=hosts_file,
            project=project.name if project else None,
            checkout=project.checkout if project else None,
            source_host=socket.gethostname(),
            cmdline_n=cmdline_has_numprocesses(config.invocation_params.args),
            local_only=_local_switch(config),
            only=config.option.hosts_only,
            ssh_config=ssh_config_path(),
            control_dir=control_dir(),
            use_venv=bool(project.settings.setup) if project else False,
        )
    except (cfg.ConfigError, ValueError) as exc:
        raise pytest.UsageError(str(exc)) from None
    config.stash[mode_key] = mode

    if isinstance(mode, Untouched):
        if mode.warning:
            config.issue_config_time_warning(pytest.PytestConfigWarning(mode.warning), stacklevel=2)
        return
    if isinstance(mode, LocalOnly):
        _apply(config, ["popen"] * _worker_count(config, mode.workers))
        return
    assert isinstance(mode, Distributed)
    # invocation_params is a plain attribute of Config (frozen dataclass
    # value, not a property), which is what makes the rewrite possible.
    params = config.invocation_params
    config.invocation_params = pytest.Config.InvocationParams(
        args=absolute_args(params.args, params.dir, list(config.args),
                           typed=config.args_source == pytest.Config.ArgsSource.ARGS,
                           takes_value=_value_options(config)),
        plugins=params.plugins, dir=params.dir)
    _apply(config, _distributed_tx(config, mode.local, mode.remotes))


def _distributed_tx(config: pytest.Config, local: cfg.Workers, remotes) -> list[str]:
    tx = ["popen"] * _worker_count(config, local)
    for remote in remotes:
        tx += remote_specs(remote)
    return tx


def _report(config: pytest.Config):
    tr = config.pluginmanager.getplugin("terminalreporter")
    if tr is None:
        return lambda line: print(line, flush=True)

    def write(line: str) -> None:
        # A line of progress dots may be open; the reporter's own
        # ensure_newline only knows about file-path lines.
        if tr._tw.width_of_current_line:
            tr._tw.line()
        tr.write_line(line)
    return write


# Before DSession's own sessionstart (trylast), which turns option.tx into
# nodes: the hosts must be probed, gated and synced by then, and the specs
# rewritten with the absolute paths the probe learned.
@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    mode = config.stash.get(mode_key, None)
    report = _report(config)
    if isinstance(mode, LocalOnly):
        # The plugin replaced the addopts -n, so say so: a silent run looks
        # like plain pytest with a different worker count.
        report(f"hosts| local only ({mode.reason}): "
               f"{_workers(_worker_count(config, mode.workers))}")
        return
    if not isinstance(mode, Distributed):
        return
    report("hosts| distributed per hosts file (see: pytest-hosts command)")
    if mode.local:
        report(f"hosts| local: {_workers(_worker_count(config, mode.local))} "
               f"(--hosts-local: stay on this machine)")
    project = config.stash[project_key]
    hosts = Session(
        checkout=project.checkout,
        settings=project.settings,
        remotes=mode.remotes,
        report=_report(config),
        source_host=socket.gethostname(),
        no_wait=bool(config.option.hosts_no_wait),
        force_setup=bool(config.option.hosts_setup),
        local_pytest=pytest.__version__,
        only=config.option.hosts_only,
    )
    config.stash[session_key] = hosts
    try:
        hosts.prepare()
    except HostError as exc:
        raise pytest.UsageError(str(exc)) from None
    remotes = tuple(hs.remote for hs in hosts.active)
    if not remotes and mode.local == 0:
        raise pytest.UsageError("pytest-hosts: no host is usable and no local workers are configured")
    config.stash[mode_key] = Distributed(local=mode.local, remotes=remotes)
    _apply(config, _distributed_tx(config, mode.local, remotes))


def _host_of(config: pytest.Config, report) -> str | None:
    node = getattr(report, "node", None)
    if node is None:
        return None
    spec = node.gateway.spec
    if spec.popen:
        return "local"
    hosts = config.stash.get(session_key, None)
    if hosts is None:
        return spec.ssh
    alias = spec.ssh.split()[-1]
    for hs in hosts.active:
        if hs.remote.host.ssh == alias:
            return hs.name
    return alias


tally_key = pytest.StashKey[dict[str, int]]()


class Tally:
    """Counts tests per host from the controller's view of the reports.
    Registered as a plugin object because the logreport hook carries no
    config of its own."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config

    def pytest_runtest_logreport(self, report) -> None:
        # One count per test: its call phase, or a setup phase that ended it.
        if report.when != "call" and not (report.when == "setup" and report.outcome != "passed"):
            return
        name = _host_of(self.config, report)
        if name is None:
            return
        tally = self.config.stash.setdefault(tally_key, {})
        tally[name] = tally.get(name, 0) + 1


def pytest_configure(config: pytest.Config) -> None:
    if isinstance(config.stash.get(mode_key, None), Distributed):
        config.pluginmanager.register(Tally(config), "pytest_hosts_tally")


# After DSession's sessionfinish has torn the workers down, so the tree is
# quiet when its written files are listed. pytest calls this hook from a
# finally, so it also runs after Ctrl-C and internal errors.
@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    hosts = config.stash.get(session_key, None)
    if hosts is None:
        return
    try:
        if hosts.settings.pull and not config.option.hosts_no_pull:
            hosts.pull_all()
    finally:
        hosts.close()


def pytest_terminal_summary(terminalreporter, config: pytest.Config) -> None:
    hosts = config.stash.get(session_key, None)
    if hosts is None:
        return
    tally = config.stash.get(tally_key, {})
    mode = config.stash[mode_key]
    assert isinstance(mode, Distributed)
    lines = [f"local: {tally.get('local', 0)} tests"] if mode.local else []
    for hs in hosts.hosts:
        if hs.dropped:
            lines.append(f"{hs.name}: not used ({hs.dropped})")
            continue
        line = f"{hs.name}: {tally.get(hs.name, 0)} tests"
        if hs.pulled:
            line += f", {len(hs.pulled)} file(s) pulled back"
        if hs.deleted:
            line += f", {len(hs.deleted)} deleted"
        if hs.pull_error:
            line += ", pull-back FAILED"
        lines.append(line)
    for line in lines:
        terminalreporter.write_line(f"hosts| {line}")


def pytest_unconfigure(config: pytest.Config) -> None:
    # Also reached on Ctrl-C and internal errors, when sessionfinish may not be.
    hosts = config.stash.get(session_key, None)
    if hosts is not None:
        hosts.close()


@pytest.hookimpl(optionalhook=True)  # absent when xdist is disabled
def pytest_xdist_setupnodes(config: pytest.Config, specs) -> None:
    mode = config.stash.get(mode_key, None)
    if not isinstance(mode, Distributed):
        return
    project = config.stash[project_key]
    hosts = config.stash[session_key]
    checkout = Path(project.checkout)
    # xdist rewrites path args against its rsync roots and ships each root
    # with its own (deprecated) rsync, one serial transfer per worker.
    # Registering the checkout as a root that every spec already has keeps
    # the rewriting and skips the ship; our own sync put the tree there.
    # Its other roots are its own pytest packages, which a tree whose venv
    # holds the controller's pytest version already has. `_rsynced_specs`
    # is the one underscored xdist attribute this plugin touches.
    nodemanager = config.pluginmanager.getplugin("dsession").nodemanager
    if checkout not in nodemanager.roots:
        nodemanager.roots.append(checkout)
    forwarded = {name: os.environ[name] for name in project.settings.env if name in os.environ}
    by_alias = {hs.remote.host.ssh: hs for hs in hosts.active}
    index_on_host: dict[str, int] = {}
    for spec in specs:
        nodemanager._rsynced_specs.add((spec, checkout))
        if spec.ssh is None:
            continue
        spec.env.update(forwarded)
        alias = spec.ssh.split()[-1]
        hs = by_alias.get(alias)
        if hs is None:
            continue
        index = index_on_host.get(alias, 0)
        index_on_host[alias] = index + 1
        spec.env.update(hs.worker_env(index))
        if hs.uses_tree_pytest(pytest.__version__):
            for root in nodemanager.roots:
                nodemanager._rsynced_specs.add((spec, root))

