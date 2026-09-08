"""The `error_` case annotation gate, pinned directly.

`test_case` runs it on every error case, but a case that passes proves only
the accepting side; the refusing side (a vacuous `# tpyc: ok`, a missing
`error` leg) has no case that can carry it, since such a case fails.
"""
from pathlib import Path

import pytest

from conftest import error_case_annotation_problems


def _case(tmp_path: Path, source: str) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(source)
    return src


def test_error_leg_alone_passes(tmp_path):
    src = _case(tmp_path, "x = 1  # tpyc: error(/nope/)\n")
    assert error_case_annotation_problems(src) == []


def test_ok_leg_is_refused_and_named(tmp_path):
    src = _case(tmp_path,
                "a = 1  # tpyc: ok\n"
                "b = 2  # tpyc: error(/nope/)\n"
                "c = 3  # tpyc: ok\n")
    problems = error_case_annotation_problems(src)
    assert len(problems) == 1
    assert "main.py:1" in problems[0] and "main.py:3" in problems[0]


def test_warning_leg_is_not_vacuous(tmp_path):
    src = _case(tmp_path,
                "a = 1  # tpyc: warning(/w/)\n"
                "b = 2  # tpyc: error(/nope/)\n")
    assert error_case_annotation_problems(src) == []


def test_missing_error_leg_is_refused(tmp_path):
    src = _case(tmp_path, "a = 1  # tpyc: warning(/w/)\n")
    problems = error_case_annotation_problems(src)
    assert len(problems) == 1
    assert "at least one" in problems[0]


def test_gate_walks_nested_source_packages(tmp_path):
    src = _case(tmp_path, "a = 1  # tpyc: error(/nope/)\n")
    pkg = src / "pkg"
    pkg.mkdir()
    (pkg / "helper.py").write_text("h = 1  # tpyc: ok\n")
    problems = error_case_annotation_problems(src)
    assert len(problems) == 1 and "helper.py:1" in problems[0]


class _Request:
    """The slice of pytest's `request` the comp phase reads, with the
    finalizers captured instead of registered on THIS test."""

    def __init__(self, request):
        self.config = request.config
        self.node = request.node
        self.finalizers = []

    def addfinalizer(self, fn):
        self.finalizers.append(fn)


def test_update_mode_validates_after_the_snapshots_land(tmp_path, monkeypatch,
                                                         request):
    """An update run writes diag.txt, finishes, and THEN fails on a wrong
    annotation.

    The one update run that skipped validation regenerated nineteen
    cases around wrong annotations and left the next plain run to find
    them; failing mid-phase instead would leave the wiped tree behind.
    Drives the real comp phase on a synthetic error case, so a
    reintroduced update-mode exemption fails here. The interop harness
    carries the same call site by hand; it has no toolchain-free probe.
    """
    import conftest
    import test_case as harness

    case_dir = tmp_path / "error_probe"
    src = case_dir / "src"
    src.mkdir(parents=True)
    main = src / "main.py"
    main.write_text("from tpy import Int32\n\n"
                    "too_big: Int32 = 3000000000  # tpyc: error(/no such message/)\n")
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(harness, "UPDATE_EXPECTED", True)
    # The options.json walk stops at the cases root; a temp case needs one.
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path)
    req = _Request(request)
    harness.test_case(case_dir, main, req)
    diag = case_dir / "expected" / "diag.txt"
    assert diag.exists() and "outside Int32 range" in diag.read_text()
    assert len(req.finalizers) == 1
    with pytest.raises(pytest.fail.Exception) as info:
        req.finalizers[0]()
    assert "main.py:3" in str(info.value)


def test_error_case_gate_runs_before_the_update_wipe(tmp_path, monkeypatch,
                                                      request):
    import conftest
    import test_case as harness

    case_dir = tmp_path / "error_probe"
    src = case_dir / "src"
    src.mkdir(parents=True)
    main = src / "main.py"
    main.write_text("from tpy import Int32\n\n"
                    "n: Int32 = 1  # tpyc: ok\n"
                    "too_big: Int32 = 3000000000  # tpyc: error(/Int32 range/)\n")
    diag = case_dir / "expected" / "diag.txt"
    diag.parent.mkdir()
    diag.write_text("stale\n")
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(harness, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path)
    with pytest.raises(pytest.fail.Exception) as info:
        harness.test_case(case_dir, main, _Request(request))
    assert "main.py:3" in str(info.value)
    # The refused case keeps its snapshot: the gate fired before the wipe.
    assert diag.read_text() == "stale\n"


def test_error_case_that_compiles_fails_at_once_in_update_mode(tmp_path,
                                                                monkeypatch,
                                                                request):
    """An error case whose rejection stopped firing must not run on into
    the code snapshots and exec: it fails right after diag.txt, in update
    mode as in a plain run."""
    import conftest
    import test_case as harness

    case_dir = tmp_path / "error_probe"
    src = case_dir / "src"
    src.mkdir(parents=True)
    main = src / "main.py"
    main.write_text("from tpy import Int32\n\n"
                    "n: Int32 = 1  # tpyc: error(/never fires/)\n")
    monkeypatch.setattr(conftest, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(harness, "UPDATE_EXPECTED", True)
    monkeypatch.setattr(conftest, "CASES_DIR", tmp_path)
    with pytest.raises(pytest.fail.Exception) as info:
        harness.test_case(case_dir, main, _Request(request))
    assert "compiled successfully" in str(info.value)
    assert (case_dir / "expected" / "diag.txt").exists()
    assert not (case_dir / "expected" / "src").exists()
