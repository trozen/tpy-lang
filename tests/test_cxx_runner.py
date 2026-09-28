# The harness runs C++ compiles from the checkout root with CCACHE_BASEDIR set
# to it, so ccache hashes checkout-relative paths and worktrees share entries.

import subprocess

import conftest


def test_compiles_run_from_the_checkout_root_with_its_ccache_basedir(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(conftest.subprocess, "run", fake_run)
    monkeypatch.setenv("CCACHE_SLOPPINESS", "pch_defines")
    conftest._run_cxx(["ccache", "g++", "-c", "x.cpp"])
    assert seen["cwd"] == conftest.PROJECT_ROOT
    assert seen["env"]["CCACHE_BASEDIR"] == str(conftest.PROJECT_ROOT)
    # the rest of the environment (PCH sloppiness, PATH) still reaches ccache
    assert seen["env"]["CCACHE_SLOPPINESS"] == "pch_defines"
