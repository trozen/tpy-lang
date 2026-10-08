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

## Cache eviction

The stdlib object and PCH caches are keyed on the compiler source and the
runtime headers, so every edit adds an entry beside the old ones (about 80MB
for a stdlib set). Each session marks the entries it uses (a `.used` file),
and at configure time one process per machine, at most once an hour, removes
the entries unused for `$TPYC_CACHE_MAX_AGE` (`"<n>d"`, default `3d`, `0`
turns it off) and the exec-result markers unused for 30 days or that age,
whichever is longer (a hit refreshes a marker). Remote workers sweep their
own host's cache the same way. The REPL's PCH lives in the same `pch/`
directory (`repl-<key>`). One `tpy| cache sweep:` line reports what went.
An entry no session has marked yet counts as used when its loader file was
last read (atime) or the entry itself last changed, and is kept for at least
14 days, so a run with an older harness keeps its entries even where atime
does not move. Only names the cache writes (sha256 keys, `repl-<key>`, exec
markers) are touched, whatever else the root holds; an entry whose build
lock is held is being built and stays. The sweep is `tpyc.toolchain.sweep_shared_cache`, unit-tested in
`tpyc/test_cache_sweep.py`; `--clean` still wipes everything.

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
