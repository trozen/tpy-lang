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
included), the commands that compile the stdlib object set every case
links, hand-written C++ companions, link flags and stdin fixtures. So a
compiler edit that leaves the emitted C++ and those commands identical
reuses the cache, while switching `--cxx` or touching the runtime re-keys
every case. `--force-exec` ignores the cache; `--clean` wipes it together
with the shared PCH and stdlib object caches. The key derivation (`compute_exec_fingerprint`) is unit-tested
in `tests/test_exec_fingerprint.py`.

One exception: system-mode third-party libraries (`--dep-mode`) enter the
key only through their link flags, so a system-library upgrade does not
re-key and can skip stale-green; run `--force-exec` after such an upgrade
(the nightly always does).

The ext-exec phase of `tests/test_interop_exec.py` is gated by the same cache.

## Program runs

The exec and cpy phases run the program in a fresh empty cwd
(`_run_program`) and kill it after `RUN_TIMEOUT_S` (300s), which fails the
case with the tail of its output; a hung program would otherwise hold its
worker, and the session, indefinitely. A case must not depend on a pipe's
capacity: a user past `fs.pipe-user-pages-soft` gets one-page pipes (a
build host running a few sessions' workers and compile servers is), so a
case that writes more than 4 KB into a pipe before reading it uses a file
instead.

## Cache eviction

The stdlib object and PCH caches are keyed on the compiler source and the
runtime headers, so every edit adds an entry beside the old ones (about 80MB
for a stdlib set). Each session marks the entries it uses (a `.used` file),
and at configure time one process per machine, at most once an hour, removes
the entries unused for `$TPYC_CACHE_MAX_AGE` (`"<n>d"`, default `3d`, `0`
turns it off) and the exec-result markers unused for 30 days or that age,
whichever is longer (a hit refreshes a marker). Remote workers sweep their
own host's cache the same way. The PCH cache is shared with `tpy` and the
REPL: one builder, `tpyc.toolchain.shared_pch`, keys an entry on the runtime
headers' content and the PCH build command (opt flags included), so the
harness, a CLI release build, a CLI `--debug` build and the REPL each use
their own entry under `pch/`, and both `tpy` (after a build) and the REPL (at
startup) sweep as well (`docs/BUILD_PIPELINE.md` "Precompiled Headers").
Leftover `repl-<key>` entries from older REPLs age out like the rest. One
`tpy| cache sweep:` line reports what went.
An entry no session has marked yet counts as used when its loader file was
last read (atime) or the entry itself last changed, and is kept for at least
14 days, so a run with an older harness keeps its entries even where atime
does not move. Only names the cache writes (sha256 keys, `repl-<key>`, exec
markers) are touched, whatever else the root holds; an entry whose build
lock is held is being built and stays, and one a lock-free reader marks used
while the sweep renames it away is renamed back (not airtight: a reader that
accepts it between the two renames, or one whose entry another builder
recreated meanwhile, loses the PCH for that one build). In a PCH entry that stays,
builder temp files (`<name>.<pid>.tmp`) older than the max age go: a build
killed over a ready entry leaves them, and no builder takes its lock again. The sweep is `tpyc.toolchain.sweep_shared_cache`, unit-tested in
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

## Forked compiles

A program's compile spends most of its front-end time analyzing the stdlib
modules every program imports. On Linux each xdist worker therefore starts,
on first use, one compile server per template key (`tests/fork_compile.py`):
a fresh interpreter that analyzes the template's layers once
(`Compiler.prepare`, see `docs/ARCHITECTURE.md`) and then forks once per
request. The child finishes the program (`Compiler.finish`) and runs the
request's function, `fn(compiler, run, *args)`, the same function the
in-process path runs, and returns its result as plain data over a pipe.
Two clients use it:

- `test_case`: `conftest.compile_case` -- diagnostics, codegen into the
  case's `__tpyc__`, MIR and the annotation checks that read compiler
  objects (`type()`, `mir()`, `mir_summary()`, the MIR line facts).
- The sweeps (`test_ref_sink_sweep.py`, `test_arg_family_sweep.py`,
  `test_property_position_sweep.py`, `test_tuple_element_matrix.py`): their
  scripts compile through `program_compile.compile_program` in
  `scripts/thir_migration/review/`, and the session fixture
  `sweep_compiles` sends it to the servers. The function is
  `program_compile.program_facts` -- diagnostics, the THIR survey's dumps and
  rejects per entry-module body, or the emitted C++ -- and each sweep reads
  its verdicts off those facts in the worker. A standalone script run
  installs no runner and compiles in-process.

- Template keys: `base` prepares the implicit stdlib; `asyncio` adds a layer
  rooted at `asyncio`. A `base` child whose program reaches asyncio answers
  "reroute" before analyzing anything and the case goes to `asyncio`.
- Library C++ is generated once per server: after `prepare`, a throwaway
  child finishes an empty program and generates every template module
  (`conftest.emit_library`, with `TEST_CODEGEN_OPTIONS`); the server keeps
  each module's sources (`Compiler.generate_module_sources`) and the THIR
  face witnesses its generation added. A case's `compile_case` writes those
  sources through the compiler's own write step
  (`Compiler.write_module_sources`, at the case's layout paths) and adds
  those witnesses instead of generating the template modules, so
  its result equals the in-process one. This rests on a module's C++ not
  depending on the rest of the program (`docs/ARCHITECTURE.md`). It
  generates every module as before when the case's codegen options differ,
  when a program module renamed a template module's header path (a
  workspace-wide include-path collision), or when the program registered a
  return-exception name the template did not (that set is keyed by bare
  name and library bodies read it); a module the case's
  `snapshot_lib_modules` names is always generated, so its snapshot pins
  the case's own generation. A server whose library generation reported a
  diagnostic stores nothing (a case writing the stored files would not
  report it), so its cases generate every module. The servers' ready reply
  lists the modules, or why there are none -- a failed generation or that
  diagnostic (`_Server.library`). Library modules outside the templates are
  still generated per case.
- What the function raised (`pytest.fail` included) is raised again in the
  worker, with the child's traceback as a note. A child killed by a signal
  fails its test with the signal and the child's stderr. A reroute or a
  refusal raised by `run()` wins over whatever the function made of it.
- The program compiles in the worker process instead (the plain path) with
  `--no-fork-compile`, off Linux, for a compiler configuration that is not
  the templates' (a non-default `default_int`, other library directories),
  for a case's `plugin` / `dsl_opts`, when the
  child refuses the prepared layers
  (`LayerReuseRefused`: a module name the prepared layers resolved, hit or
  miss, or a macro probe miss resolves differently from the case directory, e.g.
  a program-local `typing.py`, or the program changes a prepared module's
  C++ namespace), and for the rest of
  the worker's session once a server fails to start or dies -- the summary
  then carries one `tpy| WARNING: forked compiles stopped` line. Refused
  cases are counted in a `tpy| forked compiles: N case(s) compiled
  in-process` line.
- The nightly runs every row with `--no-fork-compile`, so the path users
  run (`Compiler.compile()` in one process) is what it tests.
- Everything else (direct `compile_with_diagnostics` callers,
  subprocess-based tests) compiles in-process.
- Debug a compile with `--no-fork-compile`: a forked child's stdin is
  `/dev/null` and its output is shown only when it dies, so `breakpoint()`,
  `--pdb` or a debug print inside the compiler goes nowhere.
