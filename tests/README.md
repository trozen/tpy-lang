# Test harness notes

Overview, commands and case layout live in `CLAUDE.md` ("Testing"); this file
holds the harness internals. The CPython extension harness is documented in
`tests/interop/README.md`.

## Exec-result cache

The exec phase (C++ build + run) skips only when this exact build already ran
green on this machine's toolchain. That is tracked by a gitignored,
content-addressed marker cache under `~/.cache/tpyc/exec-results/`, shared
across worktrees.

The key covers everything that determines the binary and its output: the
toolchain, the runtime headers, every module's generated C++ (the stdlib
included), hand-written C++ companions, link flags and stdin fixtures. So a
compiler edit that leaves the emitted C++ identical reuses the cache, while
switching `--cxx` or touching the runtime re-keys every case. `--force-exec`
ignores the cache; `--clean` wipes it together with the shared PCH and stdlib
object caches. The key derivation (`compute_exec_fingerprint`) is unit-tested
in `tests/test_exec_fingerprint.py`.

One exception: system-mode third-party libraries (`--dep-mode`) enter the
key only through their link flags, so a system-library upgrade does not
re-key and can skip stale-green; run `--force-exec` after such an upgrade
(the nightly always does).

The ext-exec phase of `tests/test_interop_exec.py` is gated by the same cache.

## Harness output

Harness-emitted status lines (cache builds, toolchain/ccache status, the
active-options summary, warnings) are prefixed with `tpy|` so they stand out
from pytest's own output.

- The terminal summary adds a `tpy| exec:` tally of how many cases built+ran
  vs skipped via the exec-result cache (or that exec was disabled via
  `--no-exec`, or built without running under `--build-only` / a cross
  toolchain).
- When a shared input (toolchain, runtime headers, or the full stdlib output)
  changed since this checkout's last run -- invalidating every case's marker --
  a `tpy| exec:` line at session start names the cause, so a whole-suite
  re-verify is not a surprise.
- When a committed cpy fingerprint is stale (the session-level `cpy_stubs`
  hash, surfaced once at session start, or a per-case `main` hash, otherwise
  buried in the warnings summary), the summary includes a bold-yellow
  `STALE FINGERPRINTS` banner naming the refresh command(s). The session stays
  green, but the stale fingerprint cannot be missed.
