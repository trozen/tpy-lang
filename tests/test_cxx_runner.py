# How the harness runs C++ compiles and links: from the checkout root with
# CCACHE_BASEDIR set to it (worktrees share ccache entries), and linking with
# the first fast linker that passes a trial link with the configured compiler.

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


def _probe_linking_only(monkeypatch, working: set[str]) -> list[str]:
    tried = []

    def fake_run(cmd, **kwargs):
        ld = next(a for a in cmd if a.startswith("-fuse-ld="))
        tried.append(ld)
        return subprocess.CompletedProcess(cmd, 0 if ld in working else 1, b"", b"")

    monkeypatch.setattr(conftest.subprocess, "run", fake_run)
    return tried


def test_fast_linker_is_the_first_candidate_that_links(monkeypatch):
    # mold installed but rejecting the toolchain's link: lld is not there
    # either, so only a passing trial link may pick a linker.
    tried = _probe_linking_only(monkeypatch, {"-fuse-ld=mold"})
    # __wrapped__: the session's memoized answer must not see the fake
    assert conftest.fast_linker_flags.__wrapped__() == ("-fuse-ld=mold",)
    assert tried == ["-fuse-ld=lld", "-fuse-ld=mold"]


def test_no_fast_linker_keeps_the_toolchain_default(monkeypatch):
    _probe_linking_only(monkeypatch, set())
    assert conftest.fast_linker_flags.__wrapped__() == ()


def test_exec_fingerprint_changes_with_the_linker(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "fast_linker_flags", lambda: ())
    default = conftest.compute_exec_fingerprint(tmp_path, [], [], [])
    monkeypatch.setattr(conftest, "fast_linker_flags", lambda: ("-fuse-ld=lld",))
    assert conftest.compute_exec_fingerprint(tmp_path, [], [], []) != default
