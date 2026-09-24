import os
import textwrap
from pathlib import Path

import pytest

from pytest_hosts import config as cfg


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


HOSTS = """
    [local]
    workers = 8

    [hosts.bigbox]
    ssh = "bigbox"
    workers = 60
    slots = 2
    unreachable = "local"

    [hosts.small]
    ssh = "user@small"
    workers = 4
    root = "/srv/ph"

    [projects.tpyc]
    local = 4
    hosts.bigbox = { workers = 40 }
    hosts.small = {}

    [projects.other]
    hosts.small = {}
"""


def test_parse_and_resolve(tmp_path):
    hf = cfg.load_hosts_file(write(tmp_path / "hosts.toml", HOSTS))
    assert hf.local_workers == 8
    assert hf.hosts["bigbox"].slots == 2
    assert hf.hosts["bigbox"].unreachable == "local"
    assert hf.hosts["small"].root == "/srv/ph"
    assert hf.hosts["small"].unreachable == "error"

    tpyc = cfg.project_hosts(hf, "tpyc")
    assert tpyc.local == 4
    assert [h.name for h in tpyc.hosts] == ["bigbox", "small"]
    assert tpyc.host("bigbox").workers == 40  # per-project override
    assert tpyc.host("small").workers == 4
    assert hf.hosts["bigbox"].workers == 60  # the base is untouched

    other = cfg.project_hosts(hf, "other")
    assert other.local == 8  # falls back to [local]
    assert [h.name for h in other.hosts] == ["small"]
    assert cfg.project_hosts(hf, "unknown") is None


def test_missing_files_mean_no_config(tmp_path):
    assert cfg.load_hosts_file(tmp_path / "hosts.toml", tmp_path) is None


def test_checkout_override_layers_on_top(tmp_path):
    hosts = write(tmp_path / "hosts.toml", HOSTS)
    checkout = tmp_path / "co"
    write(checkout / cfg.CHECKOUT_OVERRIDE_NAME, """
        [hosts.bigbox]
        workers = 2
        [projects.tpyc]
        local = 1
    """)
    hf = cfg.load_hosts_file(hosts, checkout)
    assert hf.hosts["bigbox"].workers == 2
    assert hf.hosts["bigbox"].ssh == "bigbox"  # kept from the base layer
    tpyc = cfg.project_hosts(hf, "tpyc")
    assert tpyc.local == 1
    assert tpyc.host("bigbox").workers == 40  # project override still wins


def test_checkout_override_alone_is_a_config(tmp_path):
    checkout = tmp_path / "co"
    write(checkout / cfg.CHECKOUT_OVERRIDE_NAME, """
        [hosts.x]
        ssh = "x"
        workers = 1
        [projects.p]
        hosts.x = {}
    """)
    hf = cfg.load_hosts_file(tmp_path / "absent.toml", checkout)
    assert cfg.project_hosts(hf, "p").hosts[0].ssh == "x"


@pytest.mark.parametrize("text, message", [
    ("[hosts.a]\nworkers = 1", "ssh is required"),
    ('[hosts.a]\nssh = "a"', "workers is required"),
    ('[hosts.a]\nssh = "a"\nworkers = "auto"', "positive integer"),
    ('[hosts.a]\nssh = "a"\nworkers = 0', "positive integer"),
    ('[hosts.a]\nssh = "a"\nworkers = 1\nslots = 0', "slots"),
    ('[hosts.a]\nssh = "a"\nworkers = 1\nunreachable = "skip"', "unreachable"),
    ('[hosts.a]\nssh = "a"\nworkers = 1\nbogus = 1', "unknown key 'bogus' (valid: ssh, workers, slots"),
    ('[local]\nworkers = -1', "positive integer"),
    ('[local]\nworker = 8', "[local]: unknown key 'worker' (valid: workers)"),
    ('[projects.p]\nbogus = 1', "[projects.p]: unknown key 'bogus' (valid: local, hosts)"),
    ('[hosts.a]\nssh = "box"\nworkers = 1\n[hosts.b]\nssh = "box"\nworkers = 2',
     "[hosts.b] and [hosts.a] share the ssh alias 'box'"),
    ('[projects.p]\nhosts.nope = {}', "names no [hosts.nope]"),
    ('[hosts.a]\nssh = "a"\nworkers = 1\n[projects.p]\nhosts = ["a"]', "table keyed by host name"),
    ('[hosts.a]\nssh = "a"\nworkers = 1\n[projects.p]\nhosts.a = { slots = 3 }', "unknown key 'slots' (valid: workers)"),
    ('[projects.p]\nlocal = "many"', "positive integer"),
    ('[wat]\nx = 1', "unknown top-level table 'wat' (valid: local, hosts, projects)"),
    ('[hosts.a\n', "hosts.toml:"),
])
def test_rejects(tmp_path, text, message):
    path = write(tmp_path / "hosts.toml", text)
    with pytest.raises(cfg.ConfigError, match=__import__("re").escape(message)):
        cfg.load_hosts_file(path)


def test_local_workers_default_auto(tmp_path):
    hf = cfg.load_hosts_file(write(tmp_path / "hosts.toml", "[hosts]\n"))
    assert hf.local_workers == "auto"


def test_find_project_walks_up(tmp_path):
    write(tmp_path / "pyproject.toml", """
        [project]
        name = "demo"
        [tool.pytest-hosts]
        setup = "uv sync -q"
        setup_when = ["pyproject.toml", "uv.lock"]
        env = ["UPDATE_EXPECTED"]
        ignore = ["examples"]
        pull = false
    """)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    # a nameless pyproject in between (a tool-only file) is skipped
    write(tmp_path / "a" / "pyproject.toml", "[tool.ruff]\nline-length = 100\n")
    project = cfg.find_project(nested)
    assert project.checkout == tmp_path
    assert project.name == "demo"
    assert project.settings == cfg.ProjectSettings(
        setup="uv sync -q", setup_when=("pyproject.toml", "uv.lock"),
        env=("UPDATE_EXPECTED",), ignore=("examples",), pull=False)


def test_find_project_none(tmp_path):
    assert cfg.find_project(tmp_path) is None or cfg.find_project(tmp_path).checkout != tmp_path


def test_settings_defaults_and_errors():
    assert cfg.parse_settings({}) == cfg.ProjectSettings()
    with pytest.raises(cfg.ConfigError, match=r"unknown key 'rsync' \(valid: setup, setup_when, env, ignore, pull\)"):
        cfg.parse_settings({"tool": {"pytest-hosts": {"rsync": True}}})
    with pytest.raises(cfg.ConfigError, match="list of strings"):
        cfg.parse_settings({"tool": {"pytest-hosts": {"env": "CXX"}}})
    with pytest.raises(cfg.ConfigError, match="pull"):
        cfg.parse_settings({"tool": {"pytest-hosts": {"pull": "yes"}}})


def test_find_project_resolves_symlinks(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    write(real / "pyproject.toml", "[project]\nname = 'demo'\n")
    link = tmp_path / "link"
    link.symlink_to(real)
    assert cfg.find_project(link).checkout == real.resolve()


def test_reference_matches_the_schema():
    """`pytest-hosts config` prints these; they must load, and they must
    mention every key the loaders accept."""
    import tomllib
    hosts = cfg.parse_hosts(tomllib.loads(cfg.HOSTS_REFERENCE))
    assert hosts.hosts["bigbox"].workers == 60
    assert cfg.project_hosts(hosts, "my-project").host("bigbox").workers == 40
    for key in cfg.HOST_KEYS:
        assert key in cfg.HOSTS_REFERENCE
    settings = cfg.parse_settings(tomllib.loads(cfg.PROJECT_REFERENCE))
    assert settings.setup == "uv sync -q"
    for key in cfg.SETTINGS_KEYS:
        assert key in cfg.PROJECT_REFERENCE


def test_find_project_skips_a_non_table_project_key(tmp_path):
    write(tmp_path / "pyproject.toml", 'project = "not a table"\n')
    assert cfg.find_project(tmp_path) is None or cfg.find_project(tmp_path).checkout != tmp_path


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_unreadable_file_is_a_config_error(tmp_path):
    path = write(tmp_path / "hosts.toml", "[hosts]\n")
    path.chmod(0)
    try:
        with pytest.raises(cfg.ConfigError, match="hosts.toml"):
            cfg.load_hosts_file(path)
    finally:
        path.chmod(0o600)


def test_settings_survive_a_non_table_tool_key():
    assert cfg.parse_settings({"tool": 1}) == cfg.ProjectSettings()
