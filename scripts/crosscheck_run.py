#!/usr/bin/env python3
"""Run one turn of a cross-check reviewer (Codex, or Gemini through the
`agy` CLI) and always exit.

    python3 scripts/crosscheck_run.py codex|gemini start  <workdir> <prompt-file>
    python3 scripts/crosscheck_run.py codex|gemini resume <workdir> <prompt-file>

`start` opens a conversation in a NEW <workdir> and records its session id
there; `resume` sends the next message of that conversation, with its full
context. Launch it with Bash `run_in_background: true`: the harness notifies
when it exits, and it always exits --

  0  the turn completed; the reply is printed and kept in turn-<n>.md
  1  the turn failed, the reviewer exited without completing it, or the
     launch was refused (workdir already used, nothing to resume)
  3  stalled: no event for --stall minutes (default 30)
  4  timed out: the turn ran past --cap minutes (default 60)

Completion is read from the reviewer's own final event, not from the
process exiting: a finished `codex exec` can linger for hours, so a lingering
process is killed once its turn is done. Probes run from <workdir> as
`uv run --project <repo> tpy <probe>.py`; only <workdir>, the build caches
and (for Gemini) agy's own state are writable, so the reviewer cannot edit
the repo or another unit's files by accident.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

CACHE_DIRS = ("~/.cache/uv", "~/.cache/tpyc", "~/.cache/ccache")
AGY_STATE_DIR = "~/.gemini"
# Pinned so the reviewer stays a different model family whatever agy's default is.
AGY_MODEL = "gemini-4-argon-medium"
POLL_SECONDS = 2
LINGER_GRACE_SECONDS = 10


def _writable_roots(workdir: Path) -> list[str]:
    return [str(workdir)] + [os.path.expanduser(d) for d in CACHE_DIRS]


def _codex_command(workdir: Path, session: str | None, message: str, reply: Path) -> list[str]:
    # `resume` takes no --sandbox / -C / --add-dir, so the sandbox is spelled
    # as config overrides on every turn rather than trusted to carry over.
    # /tmp would otherwise be writable, and it holds other units' worktrees.
    sandbox = ["-c", "approval_policy=never", "-c", "sandbox_mode=workspace-write",
               "-c", f"sandbox_workspace_write.writable_roots={json.dumps(_writable_roots(workdir))}",
               "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
               "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true"]
    common = ["--skip-git-repo-check", *sandbox, "--json", "-o", str(reply)]
    if session is None:
        return ["codex", "exec", *common, message]
    return ["codex", "exec", "resume", *common, session, message]


def _agy_command(workdir: Path, session: str | None, message: str, reply: Path) -> list[str]:
    # agy's own --sandbox leaves $HOME writable and its --add-dir paths
    # read-only, so the filesystem is confined with bubblewrap instead.
    bwrap = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--die-with-parent"]
    for d in [*_writable_roots(workdir), os.path.expanduser(AGY_STATE_DIR)]:
        bwrap += ["--bind", d, d]
    agy = ["agy", "-p", message, "--model", AGY_MODEL, "--output-format", "stream-json",
           "--dangerously-skip-permissions"]
    if session is not None:
        agy += ["--conversation", session]
    return [*bwrap, "--chdir", str(workdir), "--", *agy]


def _codex_event(ev: dict, reply: Path) -> tuple[str | None, int | None]:
    # An `error` event is not terminal: Codex reports stream reconnects that
    # way and retries; a real failure ends in `turn.failed` or an exit.
    kind = ev.get("type")
    if kind == "thread.started":
        return ev.get("thread_id"), None
    if kind == "turn.completed":
        return None, 0
    if kind == "turn.failed":
        return None, 1
    return None, None


def _agy_event(ev: dict, reply: Path) -> tuple[str | None, int | None]:
    kind = ev.get("event")
    if kind == "init":
        return ev.get("conversation_id"), None
    if kind == "result":
        result = ev.get("result") or {}
        reply.write_text(result.get("response") or "")
        return None, 0 if result.get("status") == "SUCCESS" else 1
    return None, None


ENGINES = {
    "codex": (_codex_command, _codex_event),
    "gemini": (_agy_command, _agy_event),
}


def _kill(proc: subprocess.Popen) -> None:
    # The whole group, even when its leader is gone: a command the reviewer
    # started can outlive it.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def _on_sigterm(signum, frame) -> None:
    # Turned into an exception so the reviewer is killed on the way out.
    raise SystemExit(4)


def run(engine: str, mode: str, workdir: Path, prompt: Path, stall: float, cap: float) -> int:
    command, parse_event = ENGINES[engine]
    prompt = prompt.resolve()
    if not prompt.is_file():
        print(f"{engine}: no prompt file {prompt}")
        return 1
    sid_file = workdir / "session_id"
    if mode == "start" and workdir.exists() and any(workdir.iterdir()):
        print(f"{engine}: {workdir} is not empty; `start` takes a new directory, "
              f"`resume` continues the conversation in this one")
        return 1
    if mode == "resume" and not sid_file.exists():
        print(f"{engine}: no conversation to resume in {workdir} (run `start` first)")
        return 1
    session = sid_file.read_text().strip() if mode == "resume" else None
    tmp = workdir / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    n = 1
    while (workdir / f"events-{n}.jsonl").exists():
        n += 1
    events = workdir / f"events-{n}.jsonl"
    reply = workdir / f"turn-{n}.md"
    stderr = workdir / f"stderr-{n}.txt"
    message = f"Read {prompt} and follow it."
    signal.signal(signal.SIGTERM, _on_sigterm)
    outcome = None
    proc = None
    try:
        with open(events, "wb") as out, open(stderr, "wb") as err:
            proc = subprocess.Popen(command(workdir, session, message, reply),
                                    cwd=workdir, env={**os.environ, "TMPDIR": str(tmp)},
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    start_new_session=True)
        started = last_event = time.monotonic()
        offset = 0
        while outcome is None:
            time.sleep(POLL_SECONDS)
            now = time.monotonic()
            exited = proc.poll() is not None
            with open(events, "rb") as f:
                f.seek(offset)
                chunk = f.read()
            # A partial last line waits for the next poll, unless the
            # reviewer has exited and nothing more will come.
            complete = chunk if exited else chunk[:chunk.rfind(b"\n") + 1]
            offset += len(complete)
            for line in complete.splitlines():
                last_event = now
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                new_session, done = parse_event(ev, reply)
                if new_session and mode == "start":
                    sid_file.write_text(new_session + "\n")
                if done is not None:
                    outcome = done
            if outcome is not None:
                break
            if exited:
                outcome = 1
            elif now - last_event > stall * 60:
                outcome = 3
            elif now - started > cap * 60:
                outcome = 4
        if outcome == 0:
            try:
                proc.wait(timeout=LINGER_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if proc is not None:
            _kill(proc)

    status = {0: "done", 1: "failed", 3: f"stalled (no event for {stall:g} min)",
              4: f"timed out (cap {cap:g} min)"}[outcome]
    sid = sid_file.read_text().strip() if sid_file.exists() else "none"
    print(f"{engine}: {status}; turn {n}, session {sid}")
    if outcome == 0 and reply.exists():
        print(reply.read_text(errors="replace"))
    else:
        print(f"see {events} and {stderr}")
        tail = stderr.read_text(errors="replace").strip().splitlines()[-5:]
        if tail:
            print("\n".join(tail))
    return outcome


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("engine", choices=tuple(ENGINES))
    ap.add_argument("mode", choices=("start", "resume"))
    ap.add_argument("workdir", type=Path)
    ap.add_argument("prompt", type=Path)
    ap.add_argument("--stall", type=float, default=30, help="minutes without an event")
    ap.add_argument("--cap", type=float, default=60, help="minutes for the whole turn")
    args = ap.parse_args()
    return run(args.engine, args.mode, args.workdir.resolve(), args.prompt, args.stall, args.cap)


if __name__ == "__main__":
    sys.exit(main())
