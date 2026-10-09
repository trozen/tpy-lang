"""The --cxx / --no-ccache leg of conftest.pytest_configure runs on every
process, worker included: a remote worker cannot use a compiler path the
controller resolved, so each resolves the name itself and nothing is
exported through the environment."""

import os
from pathlib import Path

import pytest

import conftest


class FakeToolchain:
    def __init__(self, name: str, ccache: bool) -> None:
        self.compiler = [f"/opt/{name}/bin/c++"]
        self.std = "c++23"
        self.extra_flags = []
        self.ccache = ccache
        self.warn_flags = []


class FakeConfig:
    def __init__(self, **options) -> None:
        self.options = {"--update-snapshots": False, "--dep-mode": [], "--no-ccache": False,
                        "--cxx": "auto", "--clean": False, **options}
        self.option = type("Opt", (), {"update_snapshots": False})()
        self.invocation_params = pytest.Config.InvocationParams(args=(), plugins=None, dir=Path("/x"))

    def getoption(self, name: str):
        return self.options[name]


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    monkeypatch.delenv("CXX", raising=False)
    resolved = []

    def from_env(cxx):
        resolved.append(cxx)
        return FakeToolchain(cxx, ccache=True)
    monkeypatch.setattr(conftest.CppCompilerConfig, "from_env", staticmethod(from_env))
    monkeypatch.setattr(conftest, "strict_warn_flags", lambda compiler: ["-Wall"])
    # the real sweep would act on this machine's shared cache
    monkeypatch.setattr(conftest, "_sweep_shared_cache", lambda: None)
    # pytest_configure mutates these module globals; the real ones must
    # survive for the rest of this worker's session
    monkeypatch.setattr(conftest, "DEP_MODES", {})
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", conftest.UPDATE_EXPECTED)
    for attr in ("compiler", "std", "extra_flags", "ccache", "warn_flags"):
        monkeypatch.setattr(conftest.CPP_CONFIG, attr, getattr(conftest.CPP_CONFIG, attr))
    return resolved


def test_worker_resolves_cxx_by_name(worker):
    conftest.pytest_configure(FakeConfig(**{"--cxx": "clang"}))
    assert worker == ["clang"]
    assert conftest.CPP_CONFIG.compiler == ["/opt/clang/bin/c++"]
    assert conftest.CPP_CONFIG.ccache is True
    assert "CXX" not in os.environ  # nothing travels through the environment


def test_no_ccache_wins_over_the_toolchain_default(worker):
    conftest.pytest_configure(FakeConfig(**{"--cxx": "clang", "--no-ccache": True}))
    assert conftest.CPP_CONFIG.ccache is False


def test_auto_leaves_the_toolchain_alone(worker):
    before = list(conftest.CPP_CONFIG.compiler)
    conftest.pytest_configure(FakeConfig())
    assert worker == []
    assert conftest.CPP_CONFIG.compiler == before
