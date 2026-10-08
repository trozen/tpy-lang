"""The two config files: the personal hosts file and the project's pyproject.

Nothing here touches the network or pytest. Loading returns plain frozen
records that the planner and the plugin consume.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOSTS_FILE_NAME = "hosts.toml"
CHECKOUT_OVERRIDE_NAME = ".pytest-hosts.toml"
TOOL_TABLE = "pytest-hosts"

TOP_KEYS = ("max_age", "local", "hosts", "projects")
LOCAL_KEYS = ("workers",)
HOST_KEYS = ("ssh", "workers", "slots", "root", "unreachable", "ssh_config", "path_prepend",
             "lock_dir", "tmp", "max_age")
PROJECT_KEYS = ("local", "hosts")
PROJECT_HOST_KEYS = ("workers",)
SETTINGS_KEYS = ("setup", "setup_when", "env", "ignore", "pull")

# `pytest-hosts config` prints these; the tests parse them back through the
# loaders, so the reference cannot drift from the schema.
HOSTS_REFERENCE = """\
# ~/.config/pytest-hosts/hosts.toml -- personal: hosts once, and which
# projects use them. A gitignored .pytest-hosts.toml in a checkout is
# layered on top of this file for one-off experiments.

max_age = "14d"             # remove a host's trees unused this many days; 0: never

[local]
workers = "auto"            # local cap for every project; a number or "auto"; 0: none

[hosts.bigbox]
ssh = "bigbox"              # an ssh alias; user/key/port come from ~/.ssh/config
workers = 60                # xdist workers on this host
slots = 1                   # concurrent sessions this host accepts; others queue
# root = "/srv/pytest-hosts"   # where synced trees and venvs live; $XDG_CACHE_HOME/pytest-hosts
                              # (~/.cache/pytest-hosts) on the host when unset
unreachable = "error"       # or "local": run without this host, loudly
lock_dir = "/tmp/pytest-hosts"  # slot lock files; shared by every user of the box
# tmp = "/scratch"                    # workers' temp root; the host's TMPDIR when unset
# ssh_config = "~/.ssh/bigbox.cfg"    # replaces the generated ssh config for this host
# path_prepend = "/opt/toolchain/bin"  # in front of PATH for workers and setup
# max_age = "30d"                     # overrides the top-level max_age for this host

[projects.my-project]       # key: the [project] name in the project's pyproject.toml
local = 4                   # overrides [local] workers for this project
hosts.bigbox = { workers = 40 }   # naming a host selects it; the body overrides
"""

PROJECT_REFERENCE = """\
# pyproject.toml of the project -- facts every developer needs. All keys
# are optional; without a setup command the workers run the host's python3.

[tool.pytest-hosts]
setup = "uv sync -q"                          # run in the synced tree via bash -lc
setup_when = ["pyproject.toml", "uv.lock"]    # re-run setup when these change
env = []                                      # controller env vars forwarded to remote workers
ignore = []                                   # sync ignores on top of .gitignore
pull = true                                   # mirror what the remote run wrote or deleted
"""

Workers = int | str  # an int, or "auto"
DEFAULT_MAX_AGE = 14 * 86400
DEFAULT_ROOT = "~/.cache/pytest-hosts"  # what an unset root means where the host sets no XDG_CACHE_HOME


class ConfigError(Exception):
    """A config file is malformed. The message names the file and the key."""


@dataclass(frozen=True)
class HostConfig:
    name: str
    ssh: str
    workers: int
    slots: int = 1
    root: str | None = None  # the host's $XDG_CACHE_HOME/pytest-hosts when unset
    unreachable: str = "error"
    ssh_config: str | None = None
    path_prepend: str | None = None
    lock_dir: str = "/tmp/pytest-hosts"
    tmp: str | None = None  # workers' temp root; the host's TMPDIR when unset
    max_age: int = DEFAULT_MAX_AGE  # seconds; trees unused longer are swept, 0: never


@dataclass(frozen=True)
class HostsFile:
    local_workers: Workers
    hosts: dict[str, HostConfig]
    projects: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ProjectHosts:
    """The hosts file resolved for one project: what this session may use."""

    project: str
    local: Workers
    hosts: tuple[HostConfig, ...]

    def host(self, name: str) -> HostConfig | None:
        for h in self.hosts:
            if h.name == name:
                return h
        return None


@dataclass(frozen=True)
class ProjectSettings:
    """`[tool.pytest-hosts]` from the project's pyproject.toml."""

    setup: str | None = None
    setup_when: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    pull: bool = True


@dataclass(frozen=True)
class Project:
    checkout: Path
    name: str
    settings: ProjectSettings


def default_hosts_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / TOOL_TABLE / HOSTS_FILE_NAME


def read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"{path}: {exc}") from None


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two TOML trees; scalars and lists in `override` win."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def load_hosts_file(hosts_path: Path, checkout: Path | None = None) -> HostsFile | None:
    """Layer the personal hosts file with the checkout's override.

    Returns None when neither file exists: the plugin then stays silent.
    A checkout override alone is a valid config, so an experiment needs
    no personal file.
    """
    layers: list[tuple[Path, dict[str, Any]]] = []
    if hosts_path.is_file():
        layers.append((hosts_path, read_toml(hosts_path)))
    if checkout is not None:
        override = checkout / CHECKOUT_OVERRIDE_NAME
        if override.is_file():
            layers.append((override, read_toml(override)))
    if not layers:
        return None
    raw: dict[str, Any] = {}
    for _, tree in layers:
        raw = merge(raw, tree)
    return parse_hosts(raw, source=" + ".join(str(p) for p, _ in layers))


def _workers(value: Any, where: str, *, local: bool) -> Workers:
    """A host's count is at least 1 (a host with no workers is a host not
    selected); the local count may be 0, which keeps every test off this
    machine."""
    if local and value == "auto":
        return "auto"
    if isinstance(value, bool) or not isinstance(value, int) or value < (0 if local else 1):
        expected = '0, a positive integer or "auto"' if local else "a positive integer"
        raise ConfigError(f"{where}: workers must be {expected}, got {value!r}")
    return value


def _known(table: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    for key in table:
        if key not in keys:
            raise ConfigError(f"{where}: unknown key {key!r} (valid: {', '.join(keys)})")


def _str(table: dict[str, Any], key: str, where: str, default: str | None = None) -> str | None:
    value = table.get(key, default)
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"{where}: {key} must be a string, got {value!r}")
    return value


def _path(table: dict[str, Any], key: str, where: str) -> str | None:
    """An absolute or `~`-relative host path; a relative one would be taken
    against whatever cwd the command happens to run in."""
    value = _str(table, key, where)
    if value is not None and not (value.startswith("/") or value == "~" or value.startswith("~/")):
        raise ConfigError(f"{where}: {key} must be an absolute path or start with ~, got {value!r}")
    return value


def _max_age(value: Any, where: str) -> int:
    """Days only: the stamp is touched when a run starts, so an age shorter
    than the longest run would sweep a tree that is still in use."""
    if value == "0" or (type(value) is int and value == 0):
        return 0
    if not (isinstance(value, str) and value.endswith("d") and value[:-1].isdigit()):
        raise ConfigError(f'{where}: max_age must be "<n>d" (days) or 0, got {value!r}')
    return int(value[:-1]) * 86400


def _host(name: str, table: Any, where: str, max_age: int = DEFAULT_MAX_AGE) -> HostConfig:
    if not isinstance(table, dict):
        raise ConfigError(f"{where}: must be a table")
    ssh = _str(table, "ssh", where)
    if not ssh:
        raise ConfigError(f"{where}: ssh is required (an ssh host alias)")
    if "workers" not in table:
        raise ConfigError(f"{where}: workers is required")
    slots = table.get("slots", 1)
    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 1:
        raise ConfigError(f"{where}: slots must be a positive integer, got {slots!r}")
    unreachable = _str(table, "unreachable", where, "error")
    if unreachable not in ("error", "local"):
        raise ConfigError(f'{where}: unreachable must be "error" or "local", got {unreachable!r}')
    _known(table, HOST_KEYS, where)
    return HostConfig(
        name=name,
        ssh=ssh,
        workers=_workers(table["workers"], where, local=False),
        slots=slots,
        root=_str(table, "root", where),
        unreachable=unreachable or "error",
        ssh_config=_str(table, "ssh_config", where),
        path_prepend=_str(table, "path_prepend", where),
        lock_dir=_str(table, "lock_dir", where, "/tmp/pytest-hosts") or "/tmp/pytest-hosts",
        tmp=_path(table, "tmp", where),
        max_age=_max_age(table["max_age"], where) if "max_age" in table else max_age,
    )


def parse_hosts(raw: dict[str, Any], source: str = HOSTS_FILE_NAME) -> HostsFile:
    for key in raw:
        if key not in TOP_KEYS:
            raise ConfigError(f"{source}: unknown top-level key {key!r} "
                              f"(valid: {', '.join(TOP_KEYS)})")
    local = raw.get("local", {})
    if not isinstance(local, dict):
        raise ConfigError(f"{source}: [local] must be a table")
    _known(local, LOCAL_KEYS, f"{source}: [local]")
    local_workers = _workers(local.get("workers", "auto"), f"{source}: [local]", local=True)

    hosts_raw = raw.get("hosts", {})
    if not isinstance(hosts_raw, dict):
        raise ConfigError(f"{source}: [hosts] must be a table")
    max_age = _max_age(raw["max_age"], source) if "max_age" in raw else DEFAULT_MAX_AGE
    hosts = {name: _host(name, table, f"{source}: [hosts.{name}]", max_age)
             for name, table in hosts_raw.items()}
    by_alias: dict[str, str] = {}
    for name, host in hosts.items():
        # the alias is how a session tells its hosts apart, on the specs and
        # in the tally, so two entries for one alias would collapse into one
        if host.ssh in by_alias:
            raise ConfigError(f"{source}: [hosts.{name}] and [hosts.{by_alias[host.ssh]}] share "
                              f"the ssh alias {host.ssh!r}; every host needs its own")
        by_alias[host.ssh] = name

    projects = raw.get("projects", {})
    if not isinstance(projects, dict):
        raise ConfigError(f"{source}: [projects] must be a table")
    for pname, ptable in projects.items():
        where = f"{source}: [projects.{pname}]"
        if not isinstance(ptable, dict):
            raise ConfigError(f"{where}: must be a table")
        _known(ptable, PROJECT_KEYS, where)
        selected = ptable.get("hosts", {})
        if not isinstance(selected, dict):
            raise ConfigError(f"{where}: hosts must be a table keyed by host name, "
                              f"e.g. hosts.{next(iter(hosts), 'bigbox')} = {{}}")
        for name, override in selected.items():
            if name not in hosts:
                raise ConfigError(f"{where}: hosts.{name} names no [hosts.{name}] table")
            if not isinstance(override, dict):
                raise ConfigError(f"{where}: hosts.{name} must be a table, got {override!r}")
            _known(override, PROJECT_HOST_KEYS, f"{where}: hosts.{name}")
            if "workers" in override:
                _workers(override["workers"], f"{where}: hosts.{name}", local=False)
        if "local" in ptable:
            _workers(ptable["local"], f"{where}: local", local=True)
    return HostsFile(local_workers=local_workers, hosts=hosts, projects=projects)


def project_hosts(hosts_file: HostsFile, project: str) -> ProjectHosts | None:
    """Resolve the hosts file for one project, applying per-host overrides."""
    table = hosts_file.projects.get(project)
    if table is None:
        return None
    local = table.get("local", hosts_file.local_workers)
    resolved = []
    for name, override in table.get("hosts", {}).items():
        base = hosts_file.hosts[name]
        if "workers" in override:
            base = HostConfig(**{**base.__dict__, "workers": override["workers"]})
        resolved.append(base)
    return ProjectHosts(project=project, local=local, hosts=tuple(resolved))


def find_project(start: Path) -> Project | None:
    """Walk up from `start` to the nearest pyproject.toml with a `[project]`
    name. The checkout is the resolved path: xdist resolves the roots it
    rewrites path args against, and the tree on the host is named after
    the checkout, so a symlinked checkout must resolve to one name."""
    start = start.resolve()
    for directory in (start, *start.parents):
        candidate = directory / "pyproject.toml"
        if not candidate.is_file():
            continue
        raw = read_toml(candidate)
        table = raw.get("project")
        name = table.get("name") if isinstance(table, dict) else None
        if not isinstance(name, str):
            continue
        return Project(checkout=directory, name=name, settings=parse_settings(raw, candidate))
    return None


def parse_settings(pyproject: dict[str, Any], path: Path | str = "pyproject.toml") -> ProjectSettings:
    tool = pyproject.get("tool", {})
    table = tool.get(TOOL_TABLE, {}) if isinstance(tool, dict) else {}
    where = f"{path}: [tool.{TOOL_TABLE}]"
    if not isinstance(table, dict):
        raise ConfigError(f"{where} must be a table")
    _known(table, SETTINGS_KEYS, where)

    def strings(key: str) -> tuple[str, ...]:
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{where}: {key} must be a list of strings")
        return tuple(value)

    pull = table.get("pull", True)
    if not isinstance(pull, bool):
        raise ConfigError(f"{where}: pull must be true or false")
    return ProjectSettings(
        setup=_str(table, "setup", where),
        setup_when=strings("setup_when"),
        env=strings("env"),
        ignore=strings("ignore"),
        pull=pull,
    )
