"""Unit tests for the nightly-CI orchestrator (ci/nightly/nightly.py):
report logic (parse_junit + format_report), the per-config script builders,
and the backend dispatch / self-disable gate. The real docker/ssh/email
plumbing is validated end-to-end by `nightly.py --smoke`; these cover the
decisions an unattended night makes about what to run and report."""

import importlib.util
import json
import shlex
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_NIGHTLY_PATH = _REPO / "ci" / "nightly" / "nightly.py"
_spec = importlib.util.spec_from_file_location("tpy_nightly", _NIGHTLY_PATH)
nightly = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nightly)

_FALLBACK_PATH = (_REPO / "scripts" / "thir_migration"
                  / "thir_stdlib_fallback.py")
_fb_spec = importlib.util.spec_from_file_location("tpy_thir_stdlib_fallback",
                                                  _FALLBACK_PATH)
fallback_script = importlib.util.module_from_spec(_fb_spec)
_fb_spec.loader.exec_module(fallback_script)


def _configs() -> list[dict]:
    return json.loads(
        (_NIGHTLY_PATH.parent / "configs.json").read_text())["configs"]


_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" errors="1" failures="2" skipped="3" tests="10">
<testcase classname="tests.test_case" name="test_case[cases/stdlib/re_basic]">
  <failure message="boom">trace</failure></testcase>
<testcase classname="tests.test_case" name="test_case[cases/async/future_basic]">
  <failure message="boom">trace</failure></testcase>
<testcase classname="tests.test_case" name="test_case[cases/x/y]">
  <error message="err">trace</error></testcase>
<testcase classname="tests.test_case" name="test_ok"/>
</testsuite>
</testsuites>
"""


def test_parse_junit_counts_and_failing_names(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(_JUNIT)
    res = nightly.ConfigResult(name="cfg")
    nightly.parse_junit(junit, res)
    assert (res.failed, res.errors, res.skipped, res.passed) == (2, 1, 3, 4)
    assert len(res.failing_tests) == 3
    assert any("re_basic" in t for t in res.failing_tests)


def test_format_report_failure_and_tags() -> None:
    ok = nightly.ConfigResult(name="zig", status="pass", passed=5, duration_s=60)
    bad = nightly.ConfigResult(
        name="sysdeps", status="test-failures", passed=1, failed=2,
        duration_s=120, log_path="/logs/sysdeps.log",
        failing_tests=["tests.test_case::test_case[cases/stdlib/re_basic]"])
    subject, body = nightly.format_report([ok, bad], "abc1234",
                                          smoke=False, pull_failed=False)
    assert "FAIL 1/2" in subject and "abc1234" in subject
    assert "re_basic" in body and "/logs/sysdeps.log" in body

    subject2, body2 = nightly.format_report([ok], "abc1234",
                                            smoke=True, pull_failed=True)
    assert "[smoke]" in subject2 and "git pull FAILED" in subject2
    assert "STALE" in body2


def test_format_report_truncates_long_failure_lists() -> None:
    cap = nightly.MAX_FAILING_TESTS_IN_EMAIL
    bad = nightly.ConfigResult(
        name="cfg", status="test-failures", failed=cap + 5,
        failing_tests=[f"tests.test_case::t{i}" for i in range(cap + 5)])
    _, body = nightly.format_report([bad], "abc1234",
                                    smoke=False, pull_failed=False)
    assert "... and 5 more" in body
    assert f"t{cap + 4}" not in body


def test_container_script_toolchain_vs_cpython_axis() -> None:
    """A C++-toolchain row forces exec and skips cpy; a CPython-version row
    runs --no-exec (comp + cpy), never --force-exec (they conflict), and
    carries no --cxx (it needs no C++ toolchain)."""
    tool = nightly.container_script(
        {"name": "2404-gcc13", "cxx": "gcc-13"}, smoke=False)
    assert "--cxx=gcc-13" in tool
    assert "--force-exec" in tool and "--no-cpy" in tool
    assert "--no-exec" not in tool

    ver = nightly.container_script(
        {"name": "cpy-3.13", "cpython": "3.13"}, smoke=False)
    assert "--no-exec" in ver
    assert "--force-exec" not in ver and "--no-cpy" not in ver
    assert "--cxx" not in ver


def test_remote_script_native_full_run() -> None:
    """The ssh/native remote script runs the FULL suite -- --force-exec, no
    --cxx (auto-detect the host clang), no --no-cpy (the host's CPython parity
    is the point) -- with rootless uv on PATH and junit written above the
    synced repo (so rsync --delete never touches it)."""
    s = nightly.remote_script(
        {"name": "macos-native",
         "ssh": {"host": "tpy-nightly-mac", "workdir": "tpy-nightly-work"}},
        smoke=False)
    assert "--force-exec" in s
    assert "--junitxml=../junit-macos-native.xml" in s
    assert "--cxx" not in s and "--no-cpy" not in s
    assert ".local/bin" in s and "uv run pytest" in s
    # The smoke arm adds the -k slice.
    smoked = nightly.remote_script(
        {"name": "macos-native",
         "ssh": {"host": "tpy-nightly-mac", "workdir": "tpy-nightly-work"}},
        smoke=True)
    assert "-k" in smoked and nightly.SMOKE_FILTER in smoked


def test_ssh_unreachable_self_disables(monkeypatch, tmp_path: Path) -> None:
    """Best-effort contract: an unreachable ssh host self-disables to
    'unavailable' (ok, never red) -- like the requires-missing gate, so a
    Mac that's off never turns the nightly red."""
    cfg = {"name": "macos-native", "ssh": {"host": "x", "workdir": "w"}}
    monkeypatch.setattr(nightly, "_ssh_reachable", lambda host: False)
    res = nightly.run_config(cfg, tmp_path, tmp_path, timeout=1,
                             smoke=False, refresh=False)
    assert res.status == "unavailable" and res.ok
    assert "unreachable" in res.detail
    monkeypatch.setattr(nightly, "_ssh_reachable", lambda host: True)
    assert nightly._config_unavailable(cfg) is None


def test_run_config_dispatch_and_row_timeout(monkeypatch,
                                             tmp_path: Path) -> None:
    """run_config routes ssh rows to _run_ssh and others to _run_docker, and
    a per-row `timeout` overrides the session default."""
    calls: dict = {}

    def fake_docker(cfg, src, out, smoke, refresh, log, timeout):
        calls.update(backend="docker", timeout=timeout)
        return 0

    def fake_ssh(cfg, src, out, smoke, log, timeout):
        calls.update(backend="ssh", timeout=timeout)
        return 0

    monkeypatch.setattr(nightly, "_run_docker", fake_docker)
    monkeypatch.setattr(nightly, "_run_ssh", fake_ssh)
    monkeypatch.setattr(nightly, "_ssh_reachable", lambda host: True)

    nightly.run_config({"name": "d", "cxx": "gcc"}, tmp_path, tmp_path,
                       timeout=999, smoke=False, refresh=False)
    assert calls == {"backend": "docker", "timeout": 999}

    calls.clear()
    nightly.run_config({"name": "m", "ssh": {"host": "x", "workdir": "w"},
                        "timeout": 42},
                       tmp_path, tmp_path, timeout=999, smoke=False,
                       refresh=False)
    assert calls == {"backend": "ssh", "timeout": 42}


def test_unavailable_config_short_circuits(tmp_path: Path) -> None:
    """A config whose `requires` path is missing self-disables before any
    docker interaction: status 'unavailable', counted ok (not red), named
    in the report body with its missing path."""
    cfg = {"name": "macos-arm64",
           "requires": [str(tmp_path / "osxcross" / "bin" / "oa64-clang++")]}
    res = nightly.run_config(cfg, tmp_path, tmp_path, timeout=1,
                             smoke=False, refresh=False)
    assert res.status == "unavailable" and res.ok
    assert "oa64-clang++" in res.detail

    ok = nightly.ConfigResult(name="2404-gcc13", status="pass", passed=5)
    subject, body = nightly.format_report([ok, res], "abc1234",
                                          smoke=False, pull_failed=False)
    assert "OK" in subject                      # unavailable is not a FAIL
    assert "unavailable" in body
    assert "activates once installed" in body


def test_container_script_no_exec_row_is_front_end_only() -> None:
    """A `no_exec` row (the THIR stdlib oracle) runs comp only: --no-exec (so
    never --force-exec, which conflicts), --no-cpy, no --cxx -- and its
    pytest_args ride through verbatim."""
    s = nightly.container_script(
        {"name": "thir-stdlib", "no_exec": True,
         "pytest_args": ["--thir-stdlib"]}, smoke=False)
    assert "--no-exec" in s and "--no-cpy" in s and "--thir-stdlib" in s
    assert "--force-exec" not in s and "--cxx" not in s
    # --thir-stdlib errors out when combined with either of these
    # (conftest `_thir_flag_conflict`).
    assert "--update-snapshots" not in s and "--no-thir" not in s


def test_configs_json_rows_keep_their_phase_selection() -> None:
    """Regression guard for the `no_exec` generalization: every committed row
    still gets exactly the flags it got before the key existed -- comp+cpy for
    the CPython axis, forced build+run for everything else, and comp-only for
    rows that opt in explicitly."""
    for cfg in _configs():
        if "ssh" in cfg or cfg.get("script"):
            continue
        s = nightly.container_script(cfg, smoke=False)
        if cfg.get("cpython"):
            expect = {"--no-exec"}
        elif cfg.get("no_exec"):
            expect = {"--no-exec", "--no-cpy"}
        else:
            expect = {"--force-exec", "--no-cpy"}
        got = {f for f in ("--no-exec", "--no-cpy", "--force-exec") if f in s}
        assert got == expect, f"{cfg['name']}: {got} != {expect}"


def test_script_row_runs_its_command_instead_of_the_suite() -> None:
    """A `script` row replaces the pytest invocation entirely (it has no junit)
    while keeping the container setup -- repo copy + `uv sync` -- so the command
    runs against the row's own synced venv."""
    s = nightly.container_script(
        {"name": "ratchet", "script": "uv run python tool.py --max-thing 3"},
        smoke=False)
    assert "uv run python tool.py --max-thing 3" in s
    assert "uv run pytest" not in s
    assert "uv sync" in s and "cp -r /repo /work" in s


def test_script_row_verdict_comes_from_the_exit_code(monkeypatch,
                                                     tmp_path: Path) -> None:
    """The ratchet's teeth: a script row with no junit is red when the command
    exits non-zero and green when it exits 0. Without this the row could run
    every night and never be able to fail."""
    cfg = {"name": "thir-stdlib-fallback", "script": "true"}
    for rc, expect_ok in ((0, True), (1, False)):
        monkeypatch.setattr(
            nightly, "_run_docker",
            lambda c, s, o, sm, r, l, t, _rc=rc: _rc)
        res = nightly.run_config(cfg, tmp_path, tmp_path, timeout=1,
                                 smoke=False, refresh=False)
        assert res.ok is expect_ok, (rc, res.status, res.detail)
    assert res.status == "test-failures" and "exited 1" in res.detail
    # ... and a red row is named in the report, not silently tallied.
    _subject, body = nightly.format_report([res], "abc1234", smoke=False,
                                           pull_failed=False)
    assert "thir-stdlib-fallback" in body and "exited 1" in body


def test_thir_stdlib_fallback_row_is_an_armed_ratchet() -> None:
    """The stdlib FALLBACK number -- the cutover metric the routing work moves
    -- must stay measured by a row that can fail. Guards all three ways it
    could silently stop working: the row disappearing, the script path
    rotting, and `--max-fallback` being dropped (leaving a report that always
    exits 0)."""
    row = next((c for c in _configs()
                if c["name"] == "thir-stdlib-fallback"), None)
    assert row is not None, "the stdlib fallback ratchet row was removed"
    argv = shlex.split(row["script"])
    script = next(a for a in argv if a.endswith(".py"))
    assert (_REPO / script).exists(), f"{script} moved; the row runs nothing"
    assert "--max-fallback" in argv, (
        "the row measures without a threshold -- a report, not a ratchet")
    threshold = int(argv[argv.index("--max-fallback") + 1])
    # The VALUE is pinned in tests/test_thir_stdlib_gate.py, against what this
    # script's own counting key measures on the tree -- it rides that gate's
    # compile rather than paying a ~64s sweep here.
    assert threshold >= 0


def test_fallback_ratchet_exit_code() -> None:
    """Exceeding the threshold fails; matching or beating it passes (so
    progress never needs a config edit), and an unarmed run always passes."""
    assert fallback_script.ratchet_exit_code(171, 171) == 0
    assert fallback_script.ratchet_exit_code(170, 171) == 0
    assert fallback_script.ratchet_exit_code(172, 171) == 1
    assert fallback_script.ratchet_exit_code(9999, None) == 0


def test_fallback_ratchet_fails_a_broken_sweep() -> None:
    """The ratchet's blind spot: a sweep that stops measuring reports FEWER
    fallbacks and so reads as progress. A module floor turns that into red."""
    floor = fallback_script.MIN_MODULES_MEASURED
    assert fallback_script.ratchet_exit_code(0, 171, floor - 1) == 1
    assert fallback_script.ratchet_exit_code(0, 171, floor) == 0
    # A `--modules` subset run passes no count -- it measures a handful by
    # design, and floor-failing it would just teach people to lower the bar.
    assert fallback_script.ratchet_exit_code(0, 171, None) == 0


def test_fallback_script_defaults_out_of_the_repo_root() -> None:
    """Its JSON + entry-program scratch are untracked; defaulting them to the
    repo root put them one careless `git add` from a commit. `__tpyc__/` is
    gitignored at every level."""
    out = fallback_script.DEFAULT_OUT
    assert "__tpyc__" in out.parts
    assert out.parent.parent == fallback_script.REPO
