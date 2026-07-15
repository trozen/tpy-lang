#!/usr/bin/env python3
"""Nightly CI orchestrator.

Runs the config matrix from configs.json: one fresh container per config,
sequentially. Shares the compile-reuse caches (stdlib .o / PCH / ccache /
uv) across configs and nights via a named docker volume, but always passes
--force-exec so every case actually builds+links+runs every night (the
result-skip cache must never make the nightly incremental).

Stdlib-only; runs on the host under the system python3 (no uv needed).
Invoked by cron via cron-nightly.sh, which handles flock + git pull.

Environment (usually set in ~/.config/tpy-nightly/env, sourced by the
cron wrapper):
  TPY_NIGHTLY_EMAIL_TO       recipient; unset = no email (logged)
  TPY_NIGHTLY_EMAIL_FROM     sender (default: EMAIL_TO)
  TPY_NIGHTLY_GREEN_EMAIL    always|weekly|never (default: always)
  TPY_NIGHTLY_LOGS           log root (default: ~/tpy-nightly/logs)
  TPY_NIGHTLY_CONFIG_TIMEOUT per-config timeout in seconds (default: 10800)
  TPY_NIGHTLY_PULL_FAILED    set by cron-nightly.sh when git pull failed;
                             surfaced in the email subject/body
"""

import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent.parent
IMAGE_PREFIX = "tpy-nightly"
CACHE_VOLUME = "tpy-nightly-cache"
LOG_KEEP_NIGHTS = 14
BASE_REFRESH_DAYS = 7
# A hung image build (apt/network stall) must not block the night: with the
# wrapper's flock, one stuck run would silently eat every following night.
BUILD_TIMEOUT_S = 30 * 60
# Small but real slice for pipeline validation: builds every image and
# exercises comp+exec+cpy, a third-party dep (re -> pcre2), and an async
# case. Every name is explicit -- future_basic was originally selected by
# substring accident ("futu*re_basic*") and is kept deliberately.
SMOKE_FILTER = "hello or re_basic or future_basic"
MAX_FAILING_TESTS_IN_EMAIL = 50


def log(msg: str) -> None:
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[nightly {stamp}] {msg}", flush=True)


@dataclass
class ConfigResult:
    name: str
    # pass | test-failures | error | timeout | unavailable
    status: str = "error"
    detail: str = ""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    log_path: str = ""
    failing_tests: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # "unavailable" (a config whose `requires` toolchain isn't installed
        # yet, e.g. osxcross) is expected-until-installed: visible in every
        # report, but neither red nor a failed exit.
        return self.status in ("pass", "unavailable")


def run_logged(cmd: list[str], log_file: Path, timeout: float | None = None) -> int:
    """Run cmd appending combined output to log_file; return the exit code."""
    with open(log_file, "a") as f:
        f.write(f"\n$ {shlex.join(cmd)}\n")
        f.flush()
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                              timeout=timeout)
    return proc.returncode


def build_image(cfg: dict, refresh: bool, log_file: Path) -> str:
    tag = f"{IMAGE_PREFIX}:{cfg['name']}"
    cmd = ["docker", "build", "-t", tag,
           "--build-arg", f"BASE_IMAGE={cfg['base_image']}",
           "--build-arg", f"TOOLCHAIN_PKGS={cfg.get('toolchain_pkgs', '')}",
           "--build-arg", f"EXTRA_PKGS={cfg.get('extra_pkgs', '')}"]
    if refresh:
        # Weekly: re-pull the base image AND drop the layer cache so apt
        # drift is caught without paying a full rebuild every night.
        cmd += ["--pull", "--no-cache"]
    cmd.append(str(SCRIPT_DIR))
    rc = run_logged(cmd, log_file, timeout=BUILD_TIMEOUT_S)
    if rc != 0:
        raise RuntimeError(f"docker build failed for {tag} (see {log_file})")
    return tag


def container_script(cfg: dict, smoke: bool) -> str:
    """The per-config script run inside the fresh container.

    /repo is a host-made `git archive HEAD` export (tracked files only --
    no .git, no untracked junk, and no foreign-uid ownership for git to
    object to), mounted read-only; copy it to a writable tree, sync the
    venv against the distro Python, and run the full suite for this
    config's toolchain.
    """
    extras = "".join(f" --extra {e}" for e in cfg.get("uv_extras", []))
    pytest_args = [f"--cxx={cfg['cxx']}", "--force-exec",
                   f"--junitxml=/out/junit-{cfg['name']}.xml"]
    pytest_args += cfg.get("pytest_args", [])
    if smoke:
        pytest_args += ["-k", SMOKE_FILTER]
    return "\n".join([
        "set -euo pipefail",
        "cp -r /repo /work",
        "cd /work",
        f"uv sync --quiet{extras}",
        f"uv run pytest {shlex.join(pytest_args)}",
    ])


def export_source(dest: Path) -> None:
    """Export the checkout's HEAD (tracked files only) into dest.

    Done host-side, as the checkout's owner, so the container never runs
    git against a foreign-uid repo (no safe.directory dance) and untracked
    junk in the working tree can never leak into a run.
    """
    subprocess.run(
        ["bash", "-o", "pipefail", "-c",
         f"git -C {shlex.quote(str(REPO_DIR))} archive HEAD"
         f" | tar -x -C {shlex.quote(str(dest))}"],
        check=True)


def run_config(cfg: dict, src_dir: Path, out_dir: Path, timeout: float,
               smoke: bool, refresh: bool) -> ConfigResult:
    res = ConfigResult(name=cfg["name"])
    # A config whose host-side prerequisites aren't installed yet (e.g. the
    # osxcross toolchain) self-disables instead of failing: it stays visible
    # in every report and activates automatically once the paths exist.
    missing = [p for p in cfg.get("requires", [])
               if not Path(p).expanduser().exists()]
    if missing:
        res.status = "unavailable"
        res.detail = f"missing {', '.join(missing)}"
        return res
    log_file = out_dir / f"{cfg['name']}.log"
    res.log_path = str(log_file)
    container = f"{IMAGE_PREFIX}-{cfg['name']}"
    start = time.monotonic()
    try:
        tag = build_image(cfg, refresh, log_file)
        # Self-heal an orphan from a crashed/rebooted previous run: it would
        # otherwise hold this fixed name and fail tonight's docker run.
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        cmd = ["docker", "run", "--rm", "--name", container,
               "-v", f"{src_dir}:/repo:ro",
               "-v", f"{out_dir}:/out",
               "-v", f"{CACHE_VOLUME}:/cache",
               "-e", "TPYC_SHARED_CACHE_DIR=/cache/tpyc",
               "-e", "CCACHE_DIR=/cache/ccache",
               "-e", "UV_CACHE_DIR=/cache/uv"]
        for mount in cfg.get("mounts", []):
            # "~/host/path:/container/path[:opts]" with ~ expanded host-side.
            host, _, rest = mount.partition(":")
            cmd += ["-v", f"{Path(host).expanduser()}:{rest}"]
        for env in cfg.get("env", []):
            # "KEY=VALUE" passed straight through (e.g. LD_LIBRARY_PATH for a
            # mounted toolchain whose libs sit outside the container's default
            # search path).
            cmd += ["-e", env]
        cmd += [tag, "bash", "-c", container_script(cfg, smoke)]
        rc = run_logged(cmd, log_file, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "rm", "-f", container],
                       capture_output=True)
        res.status = "timeout"
        # exc.timeout distinguishes a build timeout from the config timeout.
        res.detail = f"killed after {int(exc.timeout)}s"
        res.duration_s = time.monotonic() - start
        return res
    except RuntimeError as exc:
        res.detail = str(exc)
        res.duration_s = time.monotonic() - start
        return res
    res.duration_s = time.monotonic() - start

    junit = out_dir / f"junit-{cfg['name']}.xml"
    if junit.exists():
        try:
            parse_junit(junit, res)
        except ET.ParseError as exc:
            # Truncated/corrupt XML (pytest killed mid-write) must not lose
            # the whole night's report.
            res.status = "error"
            res.detail = f"junit parse failed: {exc}"
            return res
        if res.failed or res.errors:
            res.status = "test-failures"
        elif rc == 0:
            res.status = "pass"
        else:
            res.status = "error"
            res.detail = f"pytest exit {rc} with no recorded test failures"
    else:
        res.status = "error"
        res.detail = f"no junit xml produced (exit {rc}); infra/build failure"
    return res


def parse_junit(path: Path, res: ConfigResult) -> None:
    root = ET.parse(path).getroot()
    suites = root.iter("testsuite")
    for suite in suites:
        res.failed += int(suite.get("failures", 0))
        res.errors += int(suite.get("errors", 0))
        res.skipped += int(suite.get("skipped", 0))
        res.passed += (int(suite.get("tests", 0))
                       - int(suite.get("failures", 0))
                       - int(suite.get("errors", 0))
                       - int(suite.get("skipped", 0)))
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            res.failing_tests.append(
                f"{case.get('classname', '')}::{case.get('name', '')}")


def format_report(results: list[ConfigResult], rev: str, smoke: bool,
                  pull_failed: bool) -> tuple[str, str]:
    """Return (subject, body)."""
    bad = [r for r in results if not r.ok]
    verdict = "OK" if not bad else f"FAIL {len(bad)}/{len(results)}"
    smoke_tag = " [smoke]" if smoke else ""
    pull_tag = " [git pull FAILED]" if pull_failed else ""
    date = datetime.date.today().isoformat()
    subject = f"[tpy-nightly]{smoke_tag} {verdict} ({date}, {rev}){pull_tag}"

    lines = []
    if pull_failed:
        lines += ["WARNING: git pull failed -- this run used a STALE checkout.",
                  ""]
    lines.append(f"{'config':<22} {'status':<14} {'pass':>6} {'fail':>6} "
                 f"{'error':>6} {'skip':>6} {'mins':>6}")
    for r in results:
        lines.append(f"{r.name:<22} {r.status:<14} {r.passed:>6} {r.failed:>6} "
                     f"{r.errors:>6} {r.skipped:>6} {r.duration_s / 60:>6.1f}")
    for r in results:
        if r.status == "unavailable":
            lines.append(f"  {r.name}: {r.detail} -- activates once installed")
    for r in bad:
        lines += ["", f"--- {r.name}: {r.status}"
                      + (f" ({r.detail})" if r.detail else "")]
        for t in r.failing_tests[:MAX_FAILING_TESTS_IN_EMAIL]:
            lines.append(f"  {t}")
        if len(r.failing_tests) > MAX_FAILING_TESTS_IN_EMAIL:
            lines.append(f"  ... and "
                         f"{len(r.failing_tests) - MAX_FAILING_TESTS_IN_EMAIL} more")
        lines.append(f"  full log: {r.log_path}")
    if not bad:
        lines += ["", "All configs green."]
    return subject, "\n".join(lines) + "\n"


def send_email(subject: str, body: str) -> bool:
    """Send via msmtp; a failure is logged, never fatal (the run's report
    still lands in report.txt + cron.log). Returns True on success."""
    to = os.environ.get("TPY_NIGHTLY_EMAIL_TO")
    if not to:
        log("TPY_NIGHTLY_EMAIL_TO not set; skipping email")
        return False
    sender = os.environ.get("TPY_NIGHTLY_EMAIL_FROM", to)
    msg = (f"From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\n"
           f"\r\n{body}")
    try:
        # A hung SMTP session must not wedge the run while it holds the
        # flock -- that would silently eat every following night.
        subprocess.run(["msmtp", "-t"], input=msg.encode(), check=True,
                       timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError) as exc:
        log(f"email FAILED: {exc}")
        return False
    log(f"email sent to {to}: {subject}")
    return True


def should_send_green(log_root: Path) -> bool:
    """Green-email cadence knob; a green that never arrives detects a dead
    cron. Read-only -- the caller records a sent green via the marker, so a
    failed send does not suppress the next week's green."""
    mode = os.environ.get("TPY_NIGHTLY_GREEN_EMAIL", "always")
    if mode == "never":
        return False
    if mode == "always":
        return True
    marker = log_root / ".last-green-email"
    return not (marker.exists()
                and time.time() - marker.stat().st_mtime < 6.5 * 86400)


def base_refresh_due(log_root: Path) -> bool:
    """Weekly image rebuild (--pull --no-cache) to catch apt/base drift.
    Read-only -- the caller records a completed refresh, so a night that
    dies mid-run keeps the refresh due."""
    marker = log_root / ".last-base-refresh"
    return not (marker.exists()
                and time.time() - marker.stat().st_mtime < BASE_REFRESH_DAYS * 86400)


def prune_logs(log_root: Path) -> None:
    runs = sorted(d for d in log_root.iterdir()
                  if d.is_dir() and not d.name.startswith("."))
    for old in runs[:-LOG_KEEP_NIGHTS]:
        subprocess.run(["rm", "-rf", str(old)], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--smoke", action="store_true",
                    help=f"run only '-k {SMOKE_FILTER}' per config -- validates "
                         "the whole pipeline (images, volume, email) in minutes")
    ap.add_argument("--configs", default="",
                    help="comma-separated config names to run (default: all)")
    ap.add_argument("--no-email", action="store_true",
                    help="print the report to stdout instead of emailing")
    ap.add_argument("--refresh-images", action="store_true",
                    help="force the weekly base-image refresh now")
    args = ap.parse_args()

    with open(SCRIPT_DIR / "configs.json") as f:
        configs = json.load(f)["configs"]
    if args.configs:
        wanted = set(args.configs.split(","))
        unknown = wanted - {c["name"] for c in configs}
        if unknown:
            ap.error(f"unknown config(s): {sorted(unknown)} "
                     f"(known: {[c['name'] for c in configs]})")
        configs = [c for c in configs if c["name"] in wanted]

    log_root = Path(os.environ.get("TPY_NIGHTLY_LOGS",
                                   Path.home() / "tpy-nightly" / "logs"))
    log_root.mkdir(parents=True, exist_ok=True)
    out_dir = log_root / datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    out_dir.mkdir(exist_ok=True)
    timeout = float(os.environ.get("TPY_NIGHTLY_CONFIG_TIMEOUT", 3 * 3600))
    if args.refresh_images:
        refresh = True
    else:
        # Weekly refresh only on full-matrix runs: a --configs subset would
        # otherwise eat the refresh for images it never builds.
        refresh = not args.configs and base_refresh_due(log_root)
    pull_failed = bool(os.environ.get("TPY_NIGHTLY_PULL_FAILED"))

    rev = subprocess.run(["git", "-C", str(REPO_DIR), "rev-parse",
                          "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip() or "?"
    log(f"rev {rev}; {len(configs)} config(s); logs in {out_dir}"
        + ("; smoke" if args.smoke else "")
        + ("; base-image refresh" if refresh else ""))

    results = []
    with tempfile.TemporaryDirectory(prefix="tpy-nightly-src.") as td:
        src_dir = Path(td) / "src"
        src_dir.mkdir()
        export_source(src_dir)
        for cfg in configs:
            log(f"config {cfg['name']} ...")
            try:
                res = run_config(cfg, src_dir, out_dir, timeout, args.smoke,
                                 refresh)
            except Exception as exc:
                # Last resort: one config's unexpected failure (missing
                # docker, OSError, ...) must not lose the other configs'
                # results, the report, or the email.
                res = ConfigResult(name=cfg["name"], status="error",
                                   detail=repr(exc))
            log(f"config {cfg['name']}: {res.status} "
                f"({res.passed}p/{res.failed}f/{res.errors}e, "
                f"{res.duration_s / 60:.1f} min)")
            results.append(res)

    if refresh:
        # Record the refresh only after the builds actually ran (a --pull
        # --no-cache build happened per config, pass or fail); a night that
        # dies mid-run keeps the refresh due. Forced --refresh-images resets
        # the weekly clock the same way.
        (log_root / ".last-base-refresh").touch()

    subject, body = format_report(results, rev, args.smoke, pull_failed)
    (out_dir / "report.txt").write_text(f"{subject}\n\n{body}")
    all_green = all(r.ok for r in results) and not pull_failed
    if args.no_email:
        print(f"\n{subject}\n\n{body}")
    elif not all_green or should_send_green(log_root):
        # Record a green only when it actually went out, so a failed send
        # can't suppress weekly-mode greens for the following week.
        if send_email(subject, body) and all_green:
            (log_root / ".last-green-email").touch()
    prune_logs(log_root)
    return 0 if all_green else 1


if __name__ == "__main__":
    sys.exit(main())
