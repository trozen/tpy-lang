"""`pytest-hosts status`: what it says about who holds each slot."""

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from pytest_hosts import cli, plan, session
from pytest_hosts import config as cfg


def test_describe_locks_names_each_holder():
    out = "\n".join([
        "slot 0 busy 1000 400 laptop /home/me/src/my demo",
        "slot 1 free",
        "slot 2 busy",  # a holder from an older client records no owner
        "now 1012",
        "tree", "venv",
    ])
    free, gated, lines = cli.describe_locks(out)
    assert (free, gated) == (1, False)
    assert lines == [
        "slot 0: laptop /home/me/src/my demo, 10 min, last heartbeat 12s ago",
        "slot 1: free",
        "slot 2: busy (no owner recorded)",
    ]


def test_describe_locks_names_an_exclusive_session():
    out = "slot 0 busy 990 990 box /src/a\nslot 1 free\nexclusive-held 995 900 box /src/a\nnow 1000\n"
    free, gated, lines = cli.describe_locks(out)
    assert (free, gated) == (0, True)
    assert lines[-1] == "exclusive (holds the host or waits for it): box /src/a, 100s, last heartbeat 5s ago"


@pytest.mark.parametrize("seconds,shown", [(5, "5s"), (119, "119s"), (600, "10 min"),
                                           (7260, "2h01"), (-3, "0s")])
def test_format_duration(seconds, shown):
    assert cli.format_duration(seconds) == shown


@pytest.mark.skipif(not shutil.which("flock"), reason="needs util-linux flock")
def test_status_command_reports_a_live_holder(tmp_path):
    host = cfg.HostConfig(name="h", ssh="h", workers=1, slots=2, lock_dir=str(tmp_path / "locks"))
    remote = plan.plan_remote(host, Path("/x"), "l", ssh_config="/c", control_dir="/d")
    holder = subprocess.Popen(["bash", "-c", session.lock_command(host, 1)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
    try:
        assert holder.stdout.readline() == b"LOCKED\n"
        holder.stdin.write(session.owner_line("laptop", Path("/src/my demo")))
        holder.stdin.write(b"\n")
        owner = tmp_path / "locks" / "1.owner"
        deadline = time.monotonic() + 5
        while not owner.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        out = subprocess.run(["bash", "-c", cli.status_command(remote)], capture_output=True,
                             text=True, timeout=10).stdout
        free, gated, lines = cli.describe_locks(out)
        assert (free, gated) == (1, False)
        assert lines[0] == "slot 0: free"
        assert lines[1].startswith("slot 1: laptop /src/my demo, ")
        assert lines[1].endswith(" ago")
    finally:
        holder.stdin.close()
        holder.wait(timeout=5)


def test_describe_locks_does_not_name_a_stale_owner():
    # a holder killed without cleanup leaves its record; the next holder, from
    # an older client or unable to replace it, must not be shown as that one
    out = f"slot 0 busy 1000 400 laptop /src/a\nnow {1000 + session.HOLD_SECONDS + 60}\n"
    free, gated, lines = cli.describe_locks(out)
    assert lines == ["slot 0: busy (stale owner record, last heartbeat 3 min ago)"]


def _take(host, slot, owner_text):
    holder = subprocess.Popen(["bash", "-c", session.lock_command(host, slot)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
    assert holder.stdout.readline() == b"LOCKED\n"
    holder.stdin.write(owner_text.encode() + b"\n")
    holder.stdin.write(b"\n")
    return holder


def _wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.05)
    return predicate()


@pytest.mark.skipif(not shutil.which("flock"), reason="needs util-linux flock")
def test_status_keeps_one_record_per_slot_whatever_the_checkout_says(tmp_path):
    host = cfg.HostConfig(name="h", ssh="h", workers=1, slots=1, lock_dir=str(tmp_path / "locks"))
    remote = plan.plan_remote(host, Path("/x"), "l", ssh_config="/c", control_dir="/d")
    holder = _take(host, 0, "laptop /src/odd\\nslot 7 free tree venv")
    try:
        assert _wait_for((tmp_path / "locks" / "0.owner").exists)
        out = subprocess.run(["sh", "-c", cli.status_command(remote)], capture_output=True,
                             text=True, timeout=10).stdout
        free, gated, lines = cli.describe_locks(out)
        assert free == 0 and len(lines) == 1
        assert lines[0].startswith("slot 0: laptop /src/odd\\nslot 7 free tree venv, ")
        assert "no-tree" in out.splitlines() and "tree" not in out.splitlines()
    finally:
        holder.stdin.close()
        holder.wait(timeout=5)


@pytest.mark.skipif(not shutil.which("flock"), reason="needs util-linux flock")
def test_owner_record_never_writes_through_what_sits_at_its_path(tmp_path):
    locks = tmp_path / "locks"
    locks.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n")
    (locks / "0.owner").symlink_to(victim)
    os.mkfifo(locks / "1.owner")
    host = cfg.HostConfig(name="h", ssh="h", workers=1, slots=2, lock_dir=str(locks))
    holders = [_take(host, 0, "laptop /src/a"), _take(host, 1, "laptop /src/b")]
    try:
        for n, holder in enumerate(holders):
            owner = locks / f"{n}.owner"
            assert _wait_for(lambda: owner.is_file() and not owner.is_symlink())
            assert owner.read_text().split(" ", 1)[1] == f"laptop /src/{'ab'[n]}\n"
        assert victim.read_text() == "keep me\n"
    finally:
        for holder in holders:
            holder.stdin.close()
            holder.wait(timeout=5)


@pytest.mark.skipif(not shutil.which("flock"), reason="needs util-linux flock")
def test_a_terminated_holder_removes_its_owner_record(tmp_path):
    host = cfg.HostConfig(name="h", ssh="h", workers=1, lock_dir=str(tmp_path / "locks"))
    holder = _take(host, 0, "laptop /src/a")
    owner = tmp_path / "locks" / "0.owner"
    try:
        assert _wait_for(owner.exists)
        # holder.pid is flock (the outer shell exec'd it); the holder is its bash child
        children = subprocess.run(["pgrep", "-P", str(holder.pid)], capture_output=True,
                                  text=True).stdout.split()
        assert len(children) == 1
        os.kill(int(children[0]), signal.SIGTERM)
        holder.wait(timeout=5)
        assert not owner.exists()
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.skipif(not shutil.which("flock"), reason="needs util-linux flock")
def test_owner_record_replaces_a_symlink_to_a_directory(tmp_path):
    locks = tmp_path / "locks"
    locks.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (locks / "0.owner").symlink_to(target)
    host = cfg.HostConfig(name="h", ssh="h", workers=1, lock_dir=str(locks))
    holder = _take(host, 0, "laptop /src/a")
    owner = locks / "0.owner"
    try:
        assert _wait_for(lambda: owner.is_file() and not owner.is_symlink())
        assert list(target.iterdir()) == []
    finally:
        holder.stdin.close()
        holder.wait(timeout=5)


@pytest.mark.skipif(not shutil.which("flock"), reason="needs util-linux flock")
def test_a_record_it_could_not_install_is_neither_refreshed_nor_removed(tmp_path):
    # what another user's leftover record looks like to this holder: a path
    # it cannot replace (here a non-empty directory)
    locks = tmp_path / "locks"
    (locks / "0.owner").mkdir(parents=True)
    (locks / "0.owner" / "keep").write_text("")
    os.utime(locks / "0.owner", (1000, 1000))
    host = cfg.HostConfig(name="h", ssh="h", workers=1, lock_dir=str(locks))
    remote = plan.plan_remote(host, Path("/x"), "l", ssh_config="/c", control_dir="/d")
    holder = _take(host, 0, "laptop /src/a")
    try:
        for _ in range(3):
            holder.stdin.write(b"\n")
            time.sleep(0.1)
        assert (locks / "0.owner").stat().st_mtime == 1000
        assert not list(locks.glob("0.owner.*"))  # no temp file left behind
        out = subprocess.run(["sh", "-c", cli.status_command(remote)], capture_output=True,
                             text=True, timeout=10).stdout
        assert cli.describe_locks(out)[2] == ["slot 0: busy (no owner recorded)"]
    finally:
        holder.stdin.close()
        holder.wait(timeout=5)
    assert (locks / "0.owner" / "keep").exists()
