from pathlib import Path

import pytest

from pytest_hosts import config as cfg
from pytest_hosts import plan


def hosts_file(**projects):
    raw = {
        "local": {"workers": 8},
        "hosts": {
            "big": {"ssh": "big", "workers": 3},
            "small": {"ssh": "u@small", "workers": 2, "root": "/srv/ph",
                      "ssh_config": "/home/me/.ssh/small.cfg"},
        },
        "projects": projects,
    }
    return cfg.parse_hosts(raw)


HF = hosts_file(demo={"local": 4, "hosts": {"big": {"workers": 1}, "small": {}}})
CHECKOUT = Path("/home/me/src/demo")
CFG = "/run/user/1/pytest-hosts/ssh_config"


def plan_remote(host, **kw):
    kw.setdefault("ssh_config", CFG)
    kw.setdefault("control_dir", "/run/user/1/pytest-hosts")
    return plan.plan_remote(host, CHECKOUT, kw.pop("source_host", "laptop"), **kw)


def test_tree_layout_keeps_checkout_name():
    remote = plan_remote(HF.hosts["big"])
    tid = plan.tree_id("laptop", CHECKOUT)
    assert len(tid) == 8
    assert remote.parent == f"~/.pytest-hosts/{tid}"
    assert remote.tree == f"{remote.parent}/demo"  # xdist resolves <name>/<rel> under chdir
    assert remote.venv == f"{remote.parent}/venv"  # beside the tree, not inside it
    assert remote.python == f"{remote.venv}/bin/python"
    assert remote.stamp == f"{remote.parent}/stamp"
    assert plan_remote(HF.hosts["big"], use_venv=False).python == "python3"


def test_tree_id_separates_machines_and_checkouts():
    a = plan.tree_id("laptop", CHECKOUT)
    assert a == plan.tree_id("laptop", CHECKOUT)
    assert a != plan.tree_id("desktop", CHECKOUT)
    assert a != plan.tree_id("laptop", Path("/home/me/src/demo-wt2"))


def test_tmp_base_expands_home_and_drops_a_trailing_slash():
    host = cfg.HostConfig(name="t", ssh="t", workers=1, tmp="~/fast/tmp/")
    before = plan.plan_remote(host, CHECKOUT, "l", ssh_config=CFG, control_dir="/d")
    assert before.tmp_base == "~/fast/tmp/"  # untouched until the probe knows home
    after = plan.with_home(before, "/home/u", CHECKOUT, "l")
    tid = plan.tree_id("l", CHECKOUT)
    assert after.tmp_root("/tmp") == f"/home/u/fast/tmp/pytest-hosts-{tid}"
    assert after.worker_tmp("/tmp", 3).endswith(f"/pytest-hosts-{tid}/w3")
    # no tmp configured: the probed host temp dir, trailing slash dropped
    plain = plan_remote(HF.hosts["big"])  # source host "laptop"
    assert plain.tmp_root("/srv/tmp/") == f"/srv/tmp/pytest-hosts-{plan.tree_id('laptop', CHECKOUT)}"


def test_home_expands_tilde_root_only():
    tilde = plan_remote(HF.hosts["big"], home="/home/u")
    assert tilde.parent.startswith("/home/u/.pytest-hosts/")
    absolute = plan_remote(HF.hosts["small"], home="/home/u")
    assert absolute.parent.startswith("/srv/ph/")
    # the probe's rewrite keeps everything but the root
    before = plan_remote(HF.hosts["big"], workers=7, use_venv=False)
    after = plan.with_home(before, "/home/u", CHECKOUT, "laptop")
    assert (after.workers, after.python, after.ssh_config) == (7, "python3", CFG)
    assert after.control_dir == before.control_dir
    assert after.tree == f"/home/u/.pytest-hosts/{plan.tree_id('laptop', CHECKOUT)}/demo"


def test_remote_specs_group_workers_per_connection():
    remote = plan_remote(HF.hosts["small"], workers=2)
    specs = plan.remote_specs(remote)
    assert len(specs) == 2
    # a host's own ssh_config replaces the generated one
    assert specs[0] == (f"ssh=-o ControlPath=/run/user/1/pytest-hosts/cm-small-0 u@small"
                        f"//python={remote.python}//chdir={remote.parent}"
                        f"//ssh_config=/home/me/.ssh/small.cfg")
    assert plan.remote_spec(plan_remote(HF.hosts["big"]), 0).endswith(f"//ssh_config={CFG}")
    # sshd allows 10 sessions per connection: 20 workers spread over 3 masters
    specs = plan.remote_specs(plan_remote(HF.hosts["big"], workers=20))
    groups = [spec.split("cm-big-")[1].split(" ")[0] for spec in specs]
    assert groups == ["0"] * 8 + ["1"] * 8 + ["2"] * 4


@pytest.mark.parametrize("args, expected", [
    ([], False),
    (["-k", "x"], False),
    (["-n", "4"], True),
    (["-n4"], True),
    (["-nauto"], True),
    (["--numprocesses", "2"], True),
    (["--numprocesses=2"], True),
    (["--no-exec"], False),          # a long option starting with -n is not -n
    (["tests/", "--", "-n"], False),  # after the separator it is a path
])
def test_cmdline_has_numprocesses(args, expected):
    assert plan.cmdline_has_numprocesses(args) is expected


def decide(**overrides):
    kwargs = dict(hosts_file=HF, project="demo", checkout=CHECKOUT, source_host="l",
                  cmdline_n=False, local_only=None, only=None)
    kwargs.update(overrides)
    return plan.decide(**kwargs)


def test_distributed_by_default():
    mode = decide()
    assert isinstance(mode, plan.Distributed)
    assert mode.local == 4
    assert [(r.host.name, r.workers) for r in mode.remotes] == [("big", 1), ("small", 2)]


def test_cmdline_n_wins_over_everything():
    assert decide(cmdline_n=True) == plan.Untouched()
    assert decide(cmdline_n=True, local_only="--hosts-local", only="big") == plan.Untouched()


def test_no_hosts_file_is_silent():
    assert decide(hosts_file=None) == plan.Untouched()


def test_no_project_entry_warns_and_names_it():
    mode = decide(project="stranger")
    assert isinstance(mode, plan.Untouched)
    assert "[projects.stranger]" in mode.warning
    mode = decide(project=None, checkout=None)
    assert isinstance(mode, plan.Untouched) and "pyproject.toml" in mode.warning


def test_local_only_uses_config_count_and_says_why():
    assert decide(local_only="--hosts-local") == plan.LocalOnly(4, reason="--hosts-local")
    assert decide(local_only="PYTEST_HOSTS=0") == plan.LocalOnly(4, reason="PYTEST_HOSTS=0")
    # a project with no hosts of its own is local with its count, for its own reason
    hf = hosts_file(demo={"local": 2})
    assert decide(hosts_file=hf) == plan.LocalOnly(2, reason="[projects.demo] selects no hosts")
    # the two switches contradict each other
    with pytest.raises(ValueError, match="--hosts-local and --hosts-only=big contradict"):
        decide(local_only="--hosts-local", only="big")


def test_local_zero_distributes_to_hosts_only():
    hf = hosts_file(demo={"local": 0, "hosts": {"big": {}}})
    mode = decide(hosts_file=hf)
    assert isinstance(mode, plan.Distributed)
    assert mode.local == 0 and [r.host.name for r in mode.remotes] == ["big"]
    # a local-only run keeps the 0; the plugin runs it in the controller process
    assert decide(hosts_file=hf, local_only="--hosts-local") == plan.LocalOnly(0, reason="--hosts-local")


def test_only_drops_local_and_other_hosts():
    mode = decide(only="small")
    assert mode.local == 0
    assert [r.host.name for r in mode.remotes] == ["small"]
    with pytest.raises(ValueError, match=r"--hosts-only=nope.*configured: big, small"):
        decide(only="nope")


def test_local_auto_passes_through():
    hf = hosts_file(demo={"hosts": {"big": {}}})
    assert hf.local_workers == 8
    hf = cfg.parse_hosts({"hosts": {"big": {"ssh": "b", "workers": 1}},
                          "projects": {"demo": {"hosts": {"big": {}}}}})
    assert decide(hosts_file=hf).local == "auto"


def test_absolute_args(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    # typed paths (what pytest took as config.args) become absolute; node-id
    # suffixes and options are kept
    typed = ["tests/test_a.py::test_one", "tests"]
    out = plan.absolute_args(["-k", "x", *typed, "-x"], tmp_path, typed, typed=True)
    assert out == ["-k", "x", f"{tmp_path}/tests/test_a.py::test_one", f"{tmp_path}/tests", "-x"]
    # an option's value naming a real directory is not a path arg
    out = plan.absolute_args(["tests", "-k", "lib"], tmp_path, ["tests"], typed=True)
    assert out == [f"{tmp_path}/tests", "-k", "lib"]
    # nor is a value that spells the same word as a positional
    out = plan.absolute_args(["-k", "tests", "tests"], tmp_path, ["tests"], typed=True,
                             takes_value={"-k", "--keyword"})
    assert out == ["-k", "tests", f"{tmp_path}/tests"]
    # nothing typed: the effective paths are appended, absolute
    out = plan.absolute_args(["-s"], tmp_path, ["tests", "tpyc"], typed=False)
    assert out == ["-s", f"{tmp_path}/tests", f"{tmp_path}/tpyc"]
    out = plan.absolute_args([], tmp_path, [str(tmp_path)], typed=False)
    assert out == [str(tmp_path)]
