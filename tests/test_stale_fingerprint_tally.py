# Unit tests for the stale-fingerprint signals that back the bold-yellow
# end-of-run banner. Stale committed fingerprints only warn (the session stays
# green) and their signals scatter -- the session-level cpy-stub line scrolls
# off the top at session start, the per-case warnings hide in the warnings
# summary -- so the banner collects both. The banner is emitted by the
# standalone _emit_stale_fingerprint_banner helper, driven here against a fake
# reporter in isolation (no real terminal_summary, so no exec/thir tally lines
# from shared session state can perturb the assertions).

import pytest

import conftest
from conftest import (
    record_stale_fingerprint,
    stale_fingerprint_cases,
    _emit_stale_fingerprint_banner,
    pytest_testnodedown,
)


class _FakeNode:
    def __init__(self, workeroutput):
        self.workeroutput = workeroutput


class _FakeReporter:
    def __init__(self):
        self.seps = []   # (title, markup)
        self.lines = []  # (text, markup)

    def write_sep(self, sep, title, **markup):
        self.seps.append((title, markup))

    def write_line(self, line, **markup):
        self.lines.append((line, markup))


@pytest.fixture(autouse=True)
def _isolate_stale_state():
    # The banner reads module-global tallies that the REAL terminal_summary also
    # reads at session end. Snapshot + restore them so a test's mutations can't
    # leak a bogus banner into the actual run (or, under xdist, into the
    # controller via workeroutput).
    fp, agg = list(conftest._stale_fp), list(conftest._stale_fp_agg)
    sess = dict(conftest._session_stale)
    try:
        yield
    finally:
        conftest._stale_fp[:] = fp
        conftest._stale_fp_agg[:] = agg
        conftest._session_stale.clear()
        conftest._session_stale.update(sess)


def _clear():
    conftest._stale_fp.clear()
    conftest._stale_fp_agg.clear()
    conftest._session_stale["cpy_stubs"] = False


def _banner():
    r = _FakeReporter()
    _emit_stale_fingerprint_banner(r)
    return r


# ----- accumulator ----------------------------------------------------------

def test_records_dedups_and_sorts():
    _clear()
    record_stale_fingerprint("zeta")
    record_stale_fingerprint("alpha")
    record_stale_fingerprint("zeta")  # duplicate collapses
    assert stale_fingerprint_cases() == ["alpha", "zeta"]


def test_empty_when_nothing_stale():
    _clear()
    assert stale_fingerprint_cases() == []


def test_folds_worker_output_across_workers():
    _clear()
    record_stale_fingerprint("local_case")  # this process's own tally
    pytest_testnodedown(_FakeNode({"stale_fp": ["worker_a", "local_case"]}), None)
    pytest_testnodedown(_FakeNode({"stale_fp": ["worker_b"]}), None)
    assert stale_fingerprint_cases() == ["local_case", "worker_a", "worker_b"]


def test_fold_tolerates_missing_key():
    _clear()
    pytest_testnodedown(_FakeNode({}), None)  # a worker that shipped no stale_fp
    assert stale_fingerprint_cases() == []


# ----- banner ---------------------------------------------------------------

def test_no_banner_when_clean():
    _clear()
    r = _banner()
    assert r.seps == [] and r.lines == []


def test_session_stale_only():
    _clear()
    conftest._session_stale["cpy_stubs"] = True
    r = _banner()
    assert ("STALE FINGERPRINTS", {"yellow": True, "bold": True}) in r.seps
    texts = [t for t, _ in r.lines]
    assert any(t.endswith("tests/update_snapshots.py") for t in texts)  # whole-suite
    assert not any("-k " in t for t in texts)                           # no per-case
    assert all(m.get("yellow") for _, m in r.lines)                     # every line yellow


def test_per_case_only():
    _clear()
    record_stale_fingerprint("beta")
    record_stale_fingerprint("alpha")
    r = _banner()
    assert ("STALE FINGERPRINTS", {"yellow": True, "bold": True}) in r.seps
    texts = [t for t, _ in r.lines]
    assert not any(t.endswith("tests/update_snapshots.py") for t in texts)  # no whole-suite
    # per-case commands, sorted, one per case
    assert [t for t in texts if "-k " in t] == [
        "tpy|   uv run python tests/update_snapshots.py -k alpha",
        "tpy|   uv run python tests/update_snapshots.py -k beta",
    ]
    assert all(m.get("yellow") for _, m in r.lines)


def test_combined_lists_session_and_per_case():
    _clear()
    conftest._session_stale["cpy_stubs"] = True
    record_stale_fingerprint("some_case")
    r = _banner()
    assert any("STALE" in title for title, _ in r.seps)
    texts = [t for t, _ in r.lines]
    assert any(t.endswith("tests/update_snapshots.py") for t in texts)  # whole-suite
    assert any(t.endswith("-k some_case") for t in texts)               # per-case
