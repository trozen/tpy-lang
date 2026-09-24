"""The controller-to-worker switches. xdist rebuilds a worker's config from
the typed command-line args alone, so the env-var form of --update-snapshots
must become a typed flag on the controller (conftest.resolve_update_flag)."""

from pathlib import Path

import pytest

import conftest


class FakeOption:
    def __init__(self, update_snapshots: bool) -> None:
        self.update_snapshots = update_snapshots


class FakeConfig:
    def __init__(self, args: tuple[str, ...], typed: bool) -> None:
        self.option = FakeOption(typed)
        self.invocation_params = pytest.Config.InvocationParams(
            args=args, plugins=None, dir=Path("/x"))

    def getoption(self, name: str) -> bool:
        assert name == "--update-snapshots"
        return self.option.update_snapshots


def test_env_var_becomes_typed_flag_on_the_controller() -> None:
    config = FakeConfig(("-k", "hello"), typed=False)
    assert conftest.resolve_update_flag(config, is_master=True, env_set=True) is True
    assert config.option.update_snapshots is True
    assert config.invocation_params.args == ("-k", "hello", "--update-snapshots")


def test_typed_flag_is_not_appended_twice() -> None:
    config = FakeConfig(("--update-snapshots",), typed=True)
    assert conftest.resolve_update_flag(config, is_master=True, env_set=True) is True
    assert config.invocation_params.args == ("--update-snapshots",)


def test_worker_answers_from_flag_or_inherited_env() -> None:
    # a worker never rewrites the args it was started with, whatever it answers
    for env_set, typed in ((True, False), (False, True), (True, True)):
        args = ("--update-snapshots",) if typed else ()
        config = FakeConfig(args, typed=typed)
        assert conftest.resolve_update_flag(config, is_master=False, env_set=env_set) is True
        assert config.invocation_params.args == args
        assert config.option.update_snapshots is typed
    config = FakeConfig((), typed=False)
    assert conftest.resolve_update_flag(config, is_master=False, env_set=False) is False
    assert config.invocation_params.args == ()


def test_controller_without_either_stays_off() -> None:
    config = FakeConfig(("-k", "x"), typed=False)
    assert conftest.resolve_update_flag(config, is_master=True, env_set=False) is False
    assert config.invocation_params.args == ("-k", "x")
