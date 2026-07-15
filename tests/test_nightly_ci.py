"""Unit tests for the nightly-CI orchestrator's report logic
(ci/nightly/nightly.py: parse_junit + format_report). The docker/email
plumbing is validated end-to-end by `nightly.py --smoke`; these cover the
pure functions that decide what an unattended night reports."""

import importlib.util
from pathlib import Path

_NIGHTLY_PATH = (Path(__file__).resolve().parent.parent
                 / "ci" / "nightly" / "nightly.py")
_spec = importlib.util.spec_from_file_location("tpy_nightly", _NIGHTLY_PATH)
nightly = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nightly)

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
    runs --no-exec (comp + cpy) and never --force-exec (they conflict)."""
    tool = nightly.container_script(
        {"name": "2404-gcc13", "cxx": "gcc-13"}, smoke=False)
    assert "--force-exec" in tool and "--no-cpy" in tool
    assert "--no-exec" not in tool

    ver = nightly.container_script(
        {"name": "cpy-3.13", "cxx": "gcc-13", "cpython": "3.13"}, smoke=False)
    assert "--no-exec" in ver
    assert "--force-exec" not in ver and "--no-cpy" not in ver


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
