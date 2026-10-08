"""The shared cache sweep: which entries go, the hourly gate, the locks."""

import fcntl
import os
import time
from pathlib import Path

import pytest

from tpyc import toolchain

DAY = 86400
NOW = time.time()
MD, GCH = "metadata.json", "tpy_pch.hpp.gch"


def key(c: str) -> str:
    """A cache key: a sha256 hex digest, as the producers write."""
    return c * 64


UNUSED, MARKED, BUILDING, OLD_READ, OLD_UNMARKED = (key(c) for c in "abcde")
REPL = "repl-" + "1" * 12


def _age(path: Path, days: float, *, atime_days: float | None = None) -> None:
    t = NOW - days * DAY
    a = t if atime_days is None else NOW - atime_days * DAY
    os.utime(path, (a, t), follow_symlinks=False)


def _entry(root: Path, rel: str, read_file: str, *, built: float, used: float | None = None,
           read: float | None = None) -> Path:
    """An entry built `built` days ago; marked used `used` days ago and its
    loader file read `read` days ago, when given."""
    entry = root / rel
    entry.mkdir(parents=True)
    (entry / read_file).write_text("x" * 5000)
    _age(entry / read_file, built, atime_days=read)
    if used is not None:
        (entry / toolchain.CACHE_USED_MARK).touch()
        _age(entry / toolchain.CACHE_USED_MARK, used)
    _age(entry, built)  # last: adding files above moved the dir's mtime
    return entry


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv(toolchain.CACHE_MAX_AGE_ENV, raising=False)
    root = tmp_path / "cache"
    unused = _entry(root, f"stdlib-objs/{UNUSED}", MD, built=10, used=5)
    # a link inside an entry goes with the entry; its target stays
    (tmp_path / "linked-target").mkdir()
    (tmp_path / "linked-target" / "keep").touch()
    (unused / "link").symlink_to(tmp_path / "linked-target")
    _age(unused, 10)
    _entry(root, f"stdlib-objs/{MARKED}", MD, built=10, used=1)
    _entry(root, f"stdlib-objs/{BUILDING}", MD, built=0)
    # unmarked (an older harness): read 1 day ago stays; 20 days unused goes
    _entry(root, f"stdlib-objs/{OLD_READ}", MD, built=10, read=1)
    _entry(root, f"stdlib-objs/{OLD_UNMARKED}", MD, built=20)
    _entry(root, f"pch/{UNUSED}", GCH, built=9, used=9)
    _entry(root, f"pch/{MARKED}", GCH, built=9, used=0)
    _entry(root, f"pch/{REPL}", GCH, built=9, used=9)
    # not the cache's: other names, a trash name it never writes, a link
    _entry(root, "pch/project", GCH, built=40)
    _entry(root, "stdlib-objs/.trash-important", MD, built=40)
    (root / "stdlib-objs" / f".trash-{key('9')}-12").mkdir()  # an interrupted sweep's
    outside = _entry(tmp_path, "outside", MD, built=10)
    (root / "stdlib-objs" / key("8")).symlink_to(outside)
    (root / "exec-results").mkdir()
    for name, days in ((key("0"), 40), (key("2"), 10), (f"{key('3')}.77.tmp", 40), ("notes", 40)):
        (root / "exec-results" / name).write_text("case\n")
        _age(root / "exec-results" / name, days)
    (root / "exec-results" / "documents").mkdir()
    _age(root / "exec-results" / "documents", 40)
    return root


def _left(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.glob("*/*")}


def test_sweep_removes_only_unused_cache_entries_and_old_markers(root, tmp_path):
    before = _left(root)
    sweep = toolchain.sweep_shared_cache(root, now=NOW)
    assert (sweep.removed, sweep.markers, sweep.failed) == (4, 2, [])
    assert sweep.freed > 0
    gone = {f"stdlib-objs/{UNUSED}", f"stdlib-objs/{OLD_UNMARKED}", f"pch/{UNUSED}", f"pch/{REPL}",
            f"stdlib-objs/.trash-{key('9')}-12", f"exec-results/{key('0')}",
            f"exec-results/{key('3')}.77.tmp"}
    assert before - _left(root) == gone
    assert _left(root) - before == set()  # no trash left behind
    assert (tmp_path / "outside" / MD).exists()  # a linked entry is never followed
    assert (tmp_path / "linked-target" / "keep").exists()


def test_an_entry_being_built_stays(root):
    with open(root / "stdlib-objs" / UNUSED / ".lock", "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        _age(root / "stdlib-objs" / UNUSED, 10)  # creating .lock moved the dir's mtime
        sweep = toolchain.sweep_shared_cache(root, now=NOW)
    assert UNUSED in sweep.failed
    assert (root / "stdlib-objs" / UNUSED / MD).exists()


def test_unmarked_entries_get_the_longer_age(root, monkeypatch):
    monkeypatch.setenv(toolchain.CACHE_MAX_AGE_ENV, "1d")
    toolchain.sweep_shared_cache(root, now=NOW)
    # 10 days unmarked is inside the 14-day minimum; marked 5 days ago is not
    assert (root / "stdlib-objs" / OLD_READ).exists()
    assert not (root / "stdlib-objs" / UNUSED).exists()


def test_sweep_runs_once_an_hour(root):
    now = time.time()  # the stamp is touched in real time
    assert toolchain.sweep_shared_cache(root, now=now) is not None
    assert toolchain.sweep_shared_cache(root, now=now + 60) is None
    assert toolchain.sweep_shared_cache(root, now=now + 60, force=True) is not None
    assert toolchain.sweep_shared_cache(root, now=now + 2 * 3600) is not None


def test_sweep_yields_to_a_running_sweep(root):
    with open(root / ".sweep.lock", "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        assert toolchain.sweep_shared_cache(root, now=NOW) is None
    assert (root / "stdlib-objs" / UNUSED).exists()


def test_a_linked_sweep_lock_keeps_its_target(root, tmp_path):
    (tmp_path / "important.txt").write_text("keep me")
    (root / ".sweep.lock").symlink_to(tmp_path / "important.txt")
    toolchain.sweep_shared_cache(root, now=NOW)
    assert (tmp_path / "important.txt").read_text() == "keep me"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the read-only directory")
def test_an_entry_that_cannot_go_stays_and_is_reported(root):
    (root / "stdlib-objs").chmod(0o555)
    try:
        sweep = toolchain.sweep_shared_cache(root, now=NOW)
    finally:
        (root / "stdlib-objs").chmod(0o755)
    assert set(sweep.failed) == {UNUSED, OLD_UNMARKED, f".trash-{key('9')}-12"}
    assert sweep.removed == 2  # the two pch entries
    assert (root / "stdlib-objs" / UNUSED / MD).exists()


def test_max_age_from_the_environment(root, monkeypatch):
    assert toolchain.cache_max_age() == 3 * DAY
    monkeypatch.setenv(toolchain.CACHE_MAX_AGE_ENV, "7d")
    sweep = toolchain.sweep_shared_cache(root, now=NOW)
    # marked 9 days ago: pch UNUSED and REPL; unmarked 20 days: OLD_UNMARKED
    assert (sweep.removed, sweep.markers) == (3, 2)
    assert (sweep.entry_age, sweep.marker_age) == (7 * DAY, 30 * DAY)
    # an age past 30 days holds the markers too: at 41 days a 35-day marker stays
    for c, days in (("4", 35), ("5", 45)):
        (root / "exec-results" / key(c)).write_text("case\n")
        _age(root / "exec-results" / key(c), days)
    monkeypatch.setenv(toolchain.CACHE_MAX_AGE_ENV, "41d")
    sweep = toolchain.sweep_shared_cache(root, now=NOW, force=True)
    assert (sweep.markers, sweep.marker_age) == (1, 41 * DAY)
    assert (root / "exec-results" / key("4")).exists()
    monkeypatch.setenv(toolchain.CACHE_MAX_AGE_ENV, "0")
    assert toolchain.sweep_shared_cache(root, now=NOW, force=True) is None
    for bad in ("3h", "d", "-1d", "three"):
        monkeypatch.setenv(toolchain.CACHE_MAX_AGE_ENV, bad)
        with pytest.raises(ValueError, match="TPYC_CACHE_MAX_AGE must be"):
            toolchain.cache_max_age()


def test_a_missing_root_is_not_swept(tmp_path, monkeypatch):
    monkeypatch.delenv(toolchain.CACHE_MAX_AGE_ENV, raising=False)
    assert toolchain.sweep_shared_cache(tmp_path / "absent") is None


def test_mark_is_best_effort(tmp_path):
    toolchain.mark_cache_entry_used(tmp_path / "absent")  # no entry, no error
    assert not (tmp_path / "absent").exists()
    toolchain.mark_cache_entry_used(tmp_path)
    assert (tmp_path / toolchain.CACHE_USED_MARK).exists()
