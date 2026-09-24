# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TurboPython (TPy) is a toolchain that translates Python to C++.
It ships two CLIs that share the same argument grammar, differing only in their default action:

- **`tpy`** -- user-facing runner. Bare `tpy` drops into a REPL; `tpy foo.py` runs the program.
- **`tpyc`** -- compiler front-end. `tpyc foo.py` emits `.hpp`/`.cpp` in `__tpyc__/` without running.

**Goals:**

1. **Performance** -- Low-latency compiled output with opt-in constraints for hot paths (e.g. `@noalloc`).
2. **Idiomatic Python** -- Standard Python should work out of the box, with minimal restrictions (e.g. type annotations on functions).
3. **Tooling-friendly** -- Source files are valid Python, so existing IDEs, linters, type checkers, and LLMs work without special plugins.

## Working in this repo

**Always** use `uv run` to invoke Python/tpy (never bare `python` or `tpy`). Use Read/Grep/Glob tools instead of `cat`/`head`/`tail`/`grep`/`rg`/`find`.

Example programs live in the separate `tpy-examples` repo (https://github.com/trozen/tpy-examples), not in this tree.

**Snippets**: write to a file under `/tmp/agents/` (any filename or subdirectory) and run from there. Do NOT use heredocs (`<<EOF`) -- they trigger permission prompts for multi-line commands.

**Committing -- branch-aware.** A *temporary working branch* is any branch that is **not** `master`/`main` **and** has **no** remote-tracking (upstream) branch -- a throwaway branch for developing a feature before it's squash-merged into `master`. Detect it mechanically: branch from `git branch --show-current`; upstream from `git rev-parse --abbrev-ref --symbolic-full-name @{u}` (this command fails when there is no upstream). Make this determination yourself -- do **not** ask the user whether the branch qualifies.

- **On a temporary working branch:** auto-commit your *own* completed work at each meaningful checkpoint -- a finished plan step, a self-contained edit set, a green run for a change you made, or fixes applied after a review pass (manual or automatic). A checkpoint is a coherent unit you'd want to point back to; err toward fewer substantive commits, not a stream of micro-commits after every edit. Just commit -- do **not** ask permission each time; this rule *is* the standing permission. (You still *pause and flag* -- without halting to ask about routine commits -- if a commit would sweep in something genuinely off: a secret, a stray binary, or changes you didn't make.) Stage only the files you created or modified for that work -- **never** `git add -A` / `git add .` (the tree carries untracked scratch and binary files that must not land in a commit). Concise messages are fine; the branch is squash-merged into `master`, so working history is throwaway.
- **Everywhere else** (`master`/`main`, or any branch that *does* track a remote): never `git commit` or `git add` unless the user explicitly asks.

In all cases: never `--amend`, rebase, force-push, or commit changes you didn't make; never push without an explicit request. An explicit user instruction ("don't commit yet", "hold off") always overrides the auto-commit default. Only `master` is durable: stale branches get bulk-deleted, so finished work left behind on an unmerged side branch is lost work. Keep sequential work stacked on one branch, and flag anything unmerged that still needs to land.

**Never prune worktrees.** Development uses multiple git worktrees (sibling checkouts sharing one `.git`), but an agent sandbox often can't see the sibling paths -- so they *look* missing. Never run `git worktree prune`, `git gc --prune`, or `git worktree remove` on a path that only appears absent: from the sandbox, prune deletes the other worktrees' admin entries and orphans their checkouts (including unmerged work). More generally, never do destructive git cleanup based on path-existence evidence -- verify against reality first. A machine-local safeguard backs this up (`gc.worktreePruneExpire never`, so a bare prune and auto-gc won't expire worktree entries), but an explicit `--expire` overrides it, so this rule stands regardless.

**Commit message format.** Subject line under ~72 chars; **hard-wrap the body at ~72 columns** (don't emit one long unwrapped line per paragraph -- when using `git commit`, pass a wrapped `-F <file>` rather than long `-m` strings). No `Co-Authored-By` or other LLM/tool-generated references.

**Spirit over letter.** These commit rules exist to (a) spare the user the manual-commit chore and (b) keep junk out of `master` -- not to be performed as a ritual. If following them literally would waste time or nag about obviously-legitimate files, optimize for that intent instead.

**Add tests** when adding new features or making changes that affect generated code. Cover happy path, errors/warnings, edge cases, and regression guards.

**Report pre-existing bugs** discovered during implementation. Do not silently ignore bugs in adjacent code just because your change didn't cause them. Check `BUGS.md` first -- if the issue is already tracked, reference the entry. Otherwise fix it or file it per "A branch holds its goal" below. A `BUGS.md` entry (not `TODO.md`) is for genuinely out-of-scope work -- filing is not a way to defer something you could just fix.

**Bug-fix discipline.** The compiler is tightly coupled; narrow patches at the symptom site routinely leave the underlying invariant violation in place and produce new symptoms in adjacent features (e.g. a fix for `Optional` that doesn't consider `Union`, a fix for functions that doesn't consider methods/constructors, a fix for generators that doesn't consider async/context-managers). For any bug-fix work, invoke `/tpy-fix-bug` to walk through the analysis procedure before proposing code changes. The skill starts with an impact assessment (trivial / localized / architectural) and gates the depth of analysis accordingly. The user must see and approve the analysis before any code change beyond trivial fixes. When time pressure pushes you to skip analysis, push back once -- the cost of one extra round is much lower than the cost of a wrong fix that surfaces later as new bugs.

**Feature discipline.** Same coupling cuts the other way for new work: features that don't consider sibling concepts and existing patterns produce duplication that's expensive to unwind (e.g. a new narrowing rule that handles `Optional` but not `Union`, a new ownership form that fits methods but not generators, a new emit path that duplicates an existing codegen helper). For any new-feature or feature-extension work, invoke `/tpy-add-feature` to walk through the design procedure before writing code. The skill starts with a scope assessment (trivial / localized / architectural) and gates the depth of design accordingly. The user must see and approve the design before any implementation beyond trivial changes. When time pressure pushes you to skip the design phase, push back once -- a wrong design is much harder to unwind than a wrong fix.

**Pitfalls.** `docs/PITFALLS.md` lists the language and generated-code rules that keep being broken past a green suite and a clean review (silent copies, tuple-vs-scalar drift, hidden allocations, C++ or internal names in diagnostics, rejections of valid Python). `/tpy-fix-bug` and `/tpy-add-feature` walk it at the scope step and fill a position-by-shape scope matrix; each reviewer definition names the entries it owns and runs their **Check** line rather than reading for them.

**Branch/merge workflow skills.** `/tpy-merge-master` merges `master` into the current working branch and verifies the result (semantic-conflict check over the files/functionality both sides touched, not just a textual merge). `/tpy-review` runs the multi-agent defect review; `/tpy-ready` is the merge-readiness + retrospective gate; `/prep-merge` squashes the branch into a clean commit ready for master. Typical order on a finished branch: `/tpy-merge-master` (pull in master) -> `/tpy-review` -> `/tpy-ready` -> `/prep-merge`.

### Working with the user

The user reviews three things: whether the generated C++ is right, whether the language behavior makes sense, and the compiler's general architecture -- its core data structures, generic implementation over special cases, apparent code smells. Shape everything presented for that.

- **Lead with code.** Analyses, designs, branch summaries and decisions show what works (Python snippets with their behavior) and the generated C++ (before/after) before any compiler internals; the mechanism comes last, in a few bullets that name the core data structures touched and say whether the change goes through an existing generic path or adds a special case (and why).
- **Decisions stand alone.** A decision the user must make goes in its own message, one at a time: the Python shape, the C++ each option yields, labelled options with consequences, recommendation first -- never buried in a status report.
- **Verdict first.** "Is it ready / reviewed / merged?" is answered in the first word; notes go underneath.
- **Instructions start work; questions get answers.** A question or remark gets an answer and a recommendation; code changes only on a go, and answering never pauses approved work. An instruction is carried out as given -- every step, on the named branch -- or you say first why not. New tooling (scripts, wrappers, guard tests) and system changes are proposals, never side effects of other work.
- **Decide what the user would decide.** Inside approved work, act on your recommendation and name the choice. Set aside -- finish the rest, then present each on its own -- anything that changes which programs compile or what they do, adds, removes or moves a diagnostic, adds a copy or allocation to the generated C++, adds a special case, or changes the approach. Neutral or improving C++ churn is not a decision (see the snapshot policy).
- **A branch holds its goal.** Include small work that completes it -- a defect it introduced, the planned fix at a sibling position, a few lines in code it already touches -- and name it in the summary. File, or offer as its own branch, what the user would want to review on its own. Once the user is reviewing the branch, additions other than fixes to what it introduced default to followups. If a premise proves false or the branch outgrows its goal, stop and re-plan with the user.

### Orchestration (default for non-trivial units)

This governs how an agreed unit is executed, not when to start one. A unit starts only after the user has approved its analysis or design (`/tpy-fix-bug`, `/tpy-add-feature`) and said go; a question, a "what's next?" or a discussion is not a go -- answer it and wait. Once started, unless the user says otherwise:

- You orchestrate; implementation, exploration and review go to your own harness's subagents (in Claude Code: on the Opus model).
- Subagent worktrees: create them yourself (`git worktree add <path> -b <branch> <sha>`, then `uv sync`) and put "verify `git rev-parse HEAD` is <sha>, else stop" in the brief -- in Claude Code, `isolation: worktree` roots at the main checkout's HEAD, not at your branch. Integrate their work into ONE working branch.
- Every brief says: tests through plain `uv run pytest`, never with `-n` (see "Agent testing workflow").
- Every batch goes through `/tpy-review` with its recommendations applied. At the end: `/tpy-merge-master` if master moved, then `/tpy-ready`. `/prep-merge` only when the user asks.
- Set decisions aside per "Decide what the user would decide"; everything else runs to the end.
- **Second opinion from the other harness**, read-only: from Claude Code `codex exec -c approval_policy=never --sandbox read-only "<prompt>"`, from Codex `claude -p --permission-mode plan "<prompt>"`. Suggest it in one line, and run it on the user's yes, before presenting an architectural plan with competing designs or Medium/Low confidence, when review rounds stop converging, or before a language-rule change.

### Common commands

```bash
uv run tpy                              # Interactive REPL
uv run tpy hello.py                     # Run a program
uv run tpy --dump-code hello.py         # Print generated C++ to stdout
uv run tpyc --dump-thir hello.py        # Print the lowered THIR for each body
uv run tpy hello.py -vv                 # -v = commands+timing, -vv adds generated C++
uv run tpyc hello.py -o out/            # Compile to C++ only
uv sync                                 # Install for development
```

## Testing

Each folder under `tests/cases/` becomes one parametrized item of `test_case` with three phases: **comp** (compile + diagnostics + snapshot check + annotation validation), **exec** (build + run C++), and **cpy** (run with CPython, compare to `output.txt`).

**Exec skip is local, not committed.** The exec phase skips only when this exact build already ran green on this machine, via a gitignored content-addressed marker cache (`~/.cache/tpyc/exec-results/`; key details in `tests/README.md` "Exec-result cache"). `--force-exec` overrides. The cpy phase pipes the same `src/input.txt` stdin fixture, so a case that reads stdin still gets a CPython parity run.

The cpy phase (CPython run) still auto-skips via **committed** fingerprints -- CPython output is toolchain-independent, so a committed key is portable:

- **Session-level** (`tests/.session_fingerprints.json`, single file): `cpy_stubs` -- hash of `lib/cpy/tpy/**`, affects every CPython run. (The legacy `runtime` / `libtpy` keys still recorded here no longer gate any phase -- exec uses the local cache now.)
- **Per-case** (`tests/cases/<case>/expected/.fingerprints`, optional): hash of the case's `main.py` (`main`). Cases without a CPython phase end up with no file.

Parallel execution (`-n auto`) is configured in `pyproject.toml` via `addopts`. Worker count auto-caps to the cgroup v2 CPU quota. The exec phase also reuses a persistent content-addressed cache of compiled stdlib object files (see `tpyc/` code for the cache-key derivation).

**CPython interop ext-exec harness.** `tests/test_interop_exec.py` (part of the normal suite) builds each `tests/interop/<case>/` extension `.so`, drives it from CPython and checks parity against the TPy source; `update_snapshots.py` regenerates its `expected/` too. Layout and phases: `tests/interop/README.md`.

### Test commands

```bash
uv run pytest                              # All tests (exec skips per the local cache; cpy skips per committed fps)
uv run pytest --force-exec                 # Force exec + cpy unconditionally (ignore the local exec cache)
uv run pytest --no-exec                    # Skip the exec phase entirely (comp + cpy only); fast codegen/diagnostics iteration
uv run pytest tests/test_stdlib_render_coverage.py  # ~6s, no toolchain: the stdlib render case imports every lib/tpy module, no two module names collide on one snapshot path, and every arg-table family is reached by some stdlib body
uv run pytest --clean                      # Wipe shared PCH + stdlib .o + exec-results caches (implies --force-exec)
uv run pytest --no-ccache                  # Bypass ccache for this run (does not wipe it)
uv run pytest --cxx clang                  # Build the exec phase with a specific toolchain (mirrors `tpyc --cxx`)
uv run pytest --cxx list                   # List available C++ toolchains and exit
uv run pytest --update-snapshots           # Regenerate expected files (implies --force-exec)
uv run pytest --update-snapshots -k hello  # Regenerate for a specific case
uv run pytest tpyc/                        # Unit tests only (no C++ toolchain)
uv run pytest -k hello                     # Pattern-matched cases
uv run pytest -k "bool_type or bool_conversion"

uv run python tests/update_snapshots.py          # Thin wrapper around --update-snapshots
uv run python tests/update_snapshots.py -k hello # Same, for a specific case
```

`--update-snapshots` (or the equivalent `UPDATE_EXPECTED=1` env var) implies `--force-exec` so output.txt, panic.txt, generated code, and `.fingerprints` are all regenerated in one pass (and the local exec cache is repopulated as cases pass).

Linked per-case test binaries are deleted after a passing exec phase (they are never reused -- exec either skips via the local cache, rebuilds, or builds-without-running under `--build-only`; this keeps `tests/cases/` from accumulating gigabytes of dead executables). A failing exec keeps its binary for debugging; set `TPY_KEEP_TEST_BINARIES=1` to keep all of them.

Harness status lines are prefixed `tpy|` (exec tally, cache-invalidation cause, `STALE FINGERPRINTS` banner); see `tests/README.md` "Harness output".

### Agent testing workflow

**Running tests**: plain `uv run pytest <args>`. The `pytest-hosts` plugin (`tools/pytest-hosts/README.md`) spreads the session over a few local workers and the remote build hosts named in the developer's `~/.config/pytest-hosts/hosts.toml`; `uv run pytest-hosts` shows the resolved setup and `pytest-hosts status` the hosts. Rules:

- **Run tests in the foreground** with the tool timeout raised (up to 600000 ms); a full or `--force-exec` run fits. Background only a run that exceeds that, then wait for its completion notification -- never poll it.
- **Never type `-n`.** A typed `-n` means a local-only run with that many workers -- `-n auto` saturates this machine.
- `--hosts-local` keeps a quick, small run off the hosts. `--cxx` must resolve on every worker, so a toolchain a host lacks needs `--hosts-local`.
- Snapshot updates (`UPDATE_EXPECTED=1 uv run pytest -k <name>`) pull the regenerated files back, `tests/interop` included. Afterwards check the summary for `pull-back FAILED` (the session still exits green) and `git status` for expected files the regeneration removed: deletions on a host are not pulled back.
- A host takes a limited number of concurrent sessions; a further run queues (`busy, queued...`) until a slot frees.

**CPU awareness**: never start a new test run while a previous one is still running. Wait for its completion notification (never poll with `pgrep`/`sleep` loops), or stop a run *you* started through its harness task. Never pattern-kill (`pkill -f pytest` or similar): other sessions' runs are not yours to stop, and the nightly's `docker run` client is on this host too. Concurrent test suites saturate all cores and slow everything down for all agents and the user.

**During development**, run targeted subsets with `-k pattern` (the new tests you're adding, or categories likely affected by your changes). **Final verification**: run the full suite once, after all changes are done, before reporting complete. Use `--force-exec` to re-run exec for every case regardless of the execution cache.

### Snapshot policy

**Churn in existing tests' expected output is approved with the plan.** The `/tpy-fix-bug` / `/tpy-add-feature` analysis lists which existing snapshots will change (count, kind, one generated-C++ example); approving the plan approves regenerating them. Churn beyond the plan that is neutral or an improvement: continue and list it in the branch summary. A regression or a program-behavior change: stop and ask. Churn is never a reason to choose a narrower fix or to keep two code paths.

**Changing what an existing test pins** -- its source, `# tpyc:` annotations, `output.txt`, `error_`/`panic_` status -- is a decision ("Decide what the user would decide"), taken before the edit.

**When adding new test cases**, run `update_snapshots.py -k {name}` so all expected files (`diag.txt`, generated `.hpp`/`.cpp`, `output.txt`, `.fingerprints`) are generated together.

### Test layout

Two kinds: **unit tests** in `tpyc/` (no C++ toolchain needed) and **snippet tests** in `tests/cases/<group>/` with snapshot-based expected output. Cases are named `{name}/` (normal), `error_{name}/` (compilation error), or `panic_{name}/` (runtime panic). Use Glob/`ls tests/cases/` to discover groups.

Per-case structure:

```
tests/cases/<group>/<case>/
├── src/
│   ├── main.py
│   ├── native_types.hpp   # (optional) C++ type defs for native interop tests
│   └── native_types.cpp   # (optional) C++ stubs for native interop tests
├── no_cpython.txt         # (optional) Skip CPython compatibility test
├── options.json           # (optional) Per-case compiler options
└── expected/
    ├── diag.txt           # Compiler diagnostics
    ├── include/main.hpp   # Generated header (if compiles)
    ├── src/main.cpp       # Generated source (if compiles)
    ├── output.txt         # Runtime output (or panic.txt)
    └── .fingerprints      # (optional) per-case `main.py` hash gating the cpy-phase skip
```

`diag.txt` holds every diagnostic the compile produced, warnings first and in
source order, then the error that stopped it -- so an `error_` case records the
warnings sema had already emitted, and a `# tpyc: warning(...)` annotation can
be pinned alongside a later error. (The `tpyc` CLI still drops those warnings on
its fatal-error paths; see BUGS.md.)

**The stdlib's own snapshot** is an ordinary case, `tests/cases/harness/stdlib_render`: it imports every non-macro module under `lib/tpy` and its `options.json` sets `snapshot_lib_modules` to `["*"]`, so the whole library's generated C++ lands in that case's `expected/` tree and is byte-compared (and built and run) like any other snapshot. `tests/test_stdlib_render_coverage.py` asserts the import list stays equal to the module set (the glob only covers what the case compiles) and that no two module names collide on one snapshot path. It also holds the arg-table family-reach gate -- every registered family is dispatched to by some stdlib body.

`options.json` is layered: the conftest walks up from the case directory toward `tests/cases/`, merging every options.json it finds (deeper file overrides; `dsl_opts` merges per-key). One file at the group level (e.g. `tests/cases/pascal/options.json`) covers every case underneath; per-case files only need the keys that differ. Supported keys:

- `default_int` -- `"int32" | "int64" | "BigInt"`. Per-case scope.
- `plugin` -- path (relative to repo root) to a frontend-plugin `.py` file. Usually set at the group level (e.g. `frontends/pascal/pascal_frontend.py`).
- `dsl_opts` -- dict of string options passed to the plugin's constructor (forwarded as if via `--dsl-opt name.key=value` on the CLI). Library paths come from the plugin's `library_paths()` hook -- no `-L` plumbing needed in options.json.
- `snapshot_lib_modules` -- list of glob patterns over library module names (e.g. `["tplib.array_list"]`, `["os.*"]`, `["*"]`) whose generated C++ this case ALSO snapshots into its own `expected/` tree, on top of its `src/` modules. For a stdlib module whose emission at this case's instantiations and `default_int` is worth pinning. Always explicit -- adding an import never starts snapshotting a module -- and an entry matching NO compiled library module fails the case, so a rename cannot quietly narrow coverage. A plain list, so a per-case file REPLACES a group-level one (only `dsl_opts` merges).

### Adding a new test case

1. Create the folder under `tests/cases/<group>/` using the naming convention above.
2. Write `src/main.py` with a short comment (1-2 lines) at the very top explaining what the test covers -- test names alone are often not enough context. **Comment the lines that are the actual subject.** A case is usually mostly scaffolding -- imports, type defs, helper classes, fixture data -- around the few lines whose behavior is under test; put a one-sentence comment on those so a reader isn't left inferring which line the case exists for. Prefer putting test logic inside functions (`def main(): ...` + `main()`) rather than top-level statements. Top-level codegen differs from function codegen (globals use pointer slots, different variable model), so use top-level statements only when specifically testing global variable behavior.
3. Add `# tpyc:` annotations on lines testing specific compiler behavior. Multiple per line supported (e.g. `# tpyc: warning(/a/) warning(/b/)`):
   - `# tpyc: ok` -- line should compile without error or warning. Never in an `error_` case: compilation stops at the first error, so an `ok` leg there asserts nothing and the harness rejects it. Every `ok` / `error` / `warning` annotation is validated in update mode too, against the diagnostics just snapshotted.
   - `# tpyc: error(/regex/)` -- line should produce an error matching the regex
   - `# tpyc: warning(/regex/)` -- line should produce a warning matching the regex
   - `# tpyc: type(TypeName)` -- assert inferred type (e.g. `s = "hello"  # tpyc: type(StrView)`). Supports regex with `/pattern/` syntax. Comp-phase only, not validated in update mode.
   - `# tpyc: non_null(var)` -- assert `var` is proven non-null at this ptr dereference (skips `deref_check`). Comp-phase only.
   - `# tpyc: nullable(var)` -- assert `var` is NOT proven non-null at this ptr dereference (uses `deref_check`). Comp-phase only.
   - `# tpyc: is_send(yes|no)` / `is_sync(yes|no)` -- assert the declared variable's Send/Sync trait (on a declaration or for-loop line). Comp-phase only.
   - `# tpyc: frame_send(yes|no)` / `frame_sync(yes|no)` -- assert an async/generator function's FrameType traits (on the `def` line). Comp-phase only.
   - `# tpyc: bounds_safe(var)` / `bounds_checked(var)` -- assert the subscript on `var` is proven in range (no bounds check emitted) / not proven (check emitted). Comp-phase only.
   - `# tpyc: div_safe(var)` / `div_checked(var)` -- same for the divisor `var` being proven non-zero. Comp-phase only.
   - `# tpyc: cast_safe(TypeName)` / `cast_checked(TypeName)` -- same for the cast to `TypeName` on that line being proven in range (no range check emitted) / not proven. Comp-phase only.
4. Run `uv run python tests/update_snapshots.py {name}` to generate expected outputs.
5. Run `uv run pytest -k {name}` to verify.

**Condensed cases.** The corpus is over 5000 cases and each one is a cost. One case per fix or feature, with one section per covered position (the `docs/PITFALLS.md` list: free function, method, constructor, module-level statement, generator, async, comprehension, closure, context-manager body, `try`/`finally`, `@error_return` body, `match` arm) under a one-line comment naming the position and the `# tpyc:` annotation on the subject line; output lines prefixed with the section name so `output.txt` names the cell that diverged. `error_` cases take the one most representative position, since the compiler stops at the first error; the other positions are covered by the happy-path sections. A section that would need `no_cpython.txt`, a `@native` companion or a runtime panic goes in its own case, since either removes the cpy or exec phase for every section beside it. A divergence found in review becomes a section in the existing case, never a new case.

**CPython compatibility**: avoid adding `no_cpython.txt` unless absolutely necessary. Never add it to an `error_` or `panic_` case: the cpy phase only runs when a committed `output.txt` exists and skips panic cases outright, so the marker is inert there. Most tests can be made CPython-compatible by adding `__init__` methods (CPython doesn't create instance attributes from type annotations alone) and using `lib/cpy/` stubs. The `lib/cpy/tpy/` package provides CPython implementations of TPy types (`Own`, `nocopy`, `UninitHeapStorage`, etc.). Only skip CPython when the test truly depends on C++-only behavior (e.g. `@native` interop).

**Reference-type happy tests must force the value-vs-reference distinction.** A *passing* test that moves a reference type (class / `list` / `dict` / `set` / recursive-union wrapper) across a boundary -- `yield`, `return`, local binding (`x = accessor()`), container insert, param passing -- must either **mutate the shared object after the boundary and observe the change**, or use a **`@nocopy` type** (so a silent copy becomes a compile error). Read-only output is *parity-blind*: the cpy phase only byte-compares `output.txt`, so a test that just reads can match CPython exactly while TPy silently *copied* where CPython would *alias* -- the divergence (and any aliasing/UAF bug behind it) stays invisible. If copy semantics are genuinely intended at that boundary, say so in the top-of-file comment so the next reader knows it wasn't an oversight. (This mirrors the cpython-parity reviewer's test-adequacy check -- but the author should not rely on review to catch it.)

**Tests must emit host-independent output.** `output.txt` is committed once and byte-compared everywhere, so anything OS-, filesystem- or CPython-version-specific passes only on the host it was snapshotted on. Three known offenders: absolute paths (every program run, exec and cpy alike, gets a fresh empty cwd -- write fixture files by relative name, which also keeps two cases from ever sharing a file, and print comparisons against `os.getcwd()` rather than paths: `/tmp` is a symlink on macOS and the scratch dir differs per run); OS-divergent errno (the same op raises different `OSError` subclasses per OS -- catch both in one `except` and print one stable token); and CPython-version-divergent output (exception message text, `strftime` padding, `fromisoformat` leniency -- print a type or stable token instead). To keep a version-pinned assertion, move it to a `no_cpython.txt` case.

These divergences stay hidden once the exec/cpy phases have been cached on a host; `--force-exec` on the target OS (or CPython version) re-runs them to surface a regression.

## Code Style

For `tpyc/` compiler modules:
- Imports at top of file only (avoid internal imports unless unavoidable)
- Use type annotations
- **ASCII only** in source code and comments -- no Unicode arrows (`→`), em dashes (`—`), or other non-ASCII characters. Use `->` and `--` instead.

**Don't write types the compiler can infer.** Applies everywhere -- test snippets (`tests/cases/*/src/main.py`), stdlib (`lib/tpy/`), examples. Only spell out the type when inference would actually fail or be ambiguous, or when the test is specifically exercising the constructor/annotation syntax.

Concretely, prefer the left form over the right when both compile:

| Prefer | Avoid (when context determines the type) |
|--------|------------------------------------------|
| `1`, `"hi"`, `{1, 2}`, `-1` | `int32(1)`, `StrView("hi")`, `int32(-1)` |
| `n = len(xs)`, `i < len(xs)` | `n = int32(len(xs))`, `i < int32(len(xs))` |
| `self.count = 0`, `self.count += 1` | `self.count = uint32(0)`, `self.count += uint32(1)` |
| `poll_pending()` (return type infers `T`) | `poll_pending[None]()`, `poll_pending[T]()` |
| `def f() -> Poll[T]: return poll_pending()` | `... return poll_pending[T]()` |

TPy infers integer-typed-field assigns and augmented-assigns, comparisons that widen BigInt to a sibling IntN, and generic-function type params from return-type context. The explicit form is appropriate when the inferred type would be wrong (e.g. `n: BigInt = len(xs)` when you actually want BigInt for arithmetic that would overflow int32) or when there's no return-type context at all.

**Comments explain WHY, not WHAT -- and carry no dead history.** Write a comment only when the *why* is non-obvious: a hidden constraint, an invariant, a workaround for a specific bug. The code already says what it does, so don't narrate it. Keep comments short -- one line is usually enough; avoid multi-paragraph blocks. Do **not** embed transient or external narrative that rots as the code moves: refactoring-phase markers ("phase 3 of the X migration"), references to design docs that may not exist, or "added for X" / "used by Y" / "handles issue #N". That context belongs in the commit message or PR, not the source. **One exception:** a filed defect may be cited by its `BUGS.md` slug, spelled `BUGS.md#<slug>` -- slugs are immutable and `tpyc/test_bugs_slugs.py` fails on a reference whose entry is gone, so unlike a bug number it cannot rot silently. Still state the fact the comment is about; the slug is a pointer, not a substitute for saying what is true. Applies everywhere, not just `tpyc/`.

## Terminology

TurboPython distinguishes **value types** (primitives, `bool`, `char`, `str`, `bytes`, views like `Span[T]`, tuples, user types implementing `ValueType` -- value semantics; `is_value_type()` is True) from **reference types** (classes/records, `list`, `dict`, `set`, `Array[T, N]`, `bytearray` -- not copied at function boundaries, stored inline in fields and containers; borrow returns render `T&`). Always use "reference types" for the latter, never "object types". `str` and `bytes` are value types although they own buffers: they are immutable in Python, so copy-vs-alias is unobservable -- their mutable siblings (`String` internals aside, `bytearray`) are the reference types. Param shapes are a separate axis: reference types pass by C++ reference (`T&`/`const T&`), while the value-typed `str`/`bytes` pass as views (`std::string_view` / `::tpy::BytesView`) -- borrowed, not copied, despite value semantics elsewhere. When classifying a type, check `is_value_type()` / its TypeDef in the compiler rather than reasoning from this list. `Own[T]` means ownership transfer (move), not heap allocation; on a value type it transfers nothing -- at a return it resolves to plain `T`, at a parameter it selects the owned form (`std::string` by value for `str`) over the view, so spell it there only when the callee must own the buffer (whether that spelling should exist at all, against the generic `Own[T]` instantiated at `str`, is an open design entry in TODO.md).

## Architecture

The compiler follows a multi-stage pipeline:

```
TurboPython Source (.py) -> Parser -> Semantic Analyzer -> Code Generator -> C++ (.hpp/.cpp) -> C++ Compiler -> Binary
```

### Compilation pipeline

**Multi-module orchestration** (`compiler.py`): The compiler recursively discovers modules by following imports from the entry point, topologically sorts them (Kahn's algorithm), and processes each module through sema and codegen in dependency order. Implicit stdlib modules (`typing`, `tpy`, `builtins`) are always compiled first. Each module's exports (functions, records, protocols, enums, variables) are registered into a shared registry before analyzing downstream modules.

**Parser** (`parse/parser.py`): Single-pass walk over Python's `ast` module output. Scans `# tpy:` directives, converts Python AST to TurboPython AST (`TpyModule`), collects imports, and resolves type annotations against local definitions. No cross-module resolution -- that's deferred to sema.

**Semantic analysis** (`sema/analyzer.py:analyze()`): Two-phase design within each module:
- *Phase 1 -- Registration then analysis*: Types must be registered before they can be referenced. Pass order: imports -> records & enums -> protocols -> inheritance validation -> value-type validation -> type aliases -> functions -> globals -> top-level statements -> record method bodies -> function bodies. Each body-analysis pass runs pre-scan for variable hoisting, then full type-checking. One construct adds a function DURING body analysis: a generator expression is a generator function sema builds at the expression, analyzes on the spot under a function state of its own, and appends to `module.functions` (the parser pre-computes `TpyModule.has_header_genexpr` because the `_inl.hpp` verdict is asked before that).
- *Phase 2 -- Call-graph fixpoint*: After all bodies are analyzed, mutation facts are propagated transitively through the intra-module call graph (`mutation_propagation.py`), `is_readonly` is inferred for methods, and deferred borrow checks are resolved.

**Code generation** (`codegen_cpp/generator.py`): Single pass producing `.hpp` and `.cpp` with careful emit ordering to satisfy C++ forward-declaration constraints: concepts before records, forward decls before full definitions, templates inline in headers, non-template functions in `.cpp`.

**THIR and the codegen boundary.** `tpyc/thir/` (Typed High-level IR) is the single sema->codegen boundary for every BODY: `thir/lower/` builds THIR from the analyzed AST and `thir/emit.py` renders the C++. `codegen_cpp/` is the printer/skeleton layer around it -- module driver, headers, signatures, record/protocol/enum drivers, the ctor member-init driver, the resumable frame at its leaf seam, and type rendering. There is no second body emitter and nothing to fall back to: a body THIR cannot lower is a **compile error** (`ThirRejectError`, naming the blocking construct and its line). The fix for one is a lowering arm in `tpyc/thir/lower/`, never a special case in the printer, and every new arm gets a snapshot case (exec + cpy) that pins its render and an `error_*` case for the everyday adjacent shape that must keep rejecting. An arm is never pinned by a unit test; the `tpyc/thir/test_*.py` files pin registries, the validator's invariants over hand-built THIR, the reject diagnostic and the `--dump-thir` renderer. Known unlowered shapes are queued in `scripts/thir_migration/review/` (`bins_*.json` bins each reject site, with a reproducer under `probes/`; `program_verdicts.json` holds the per-program verdicts). The RULE for the lowering and the emitter: nothing about builtin methods, runtime symbols or container kinds is hardcoded -- admission keys on the value form (`ValueForm`) and per-TypeDef facts, renders read the stub's `@native` annotation and the resolved dunder -- with exactly two exceptions that require compiler support: literal construction and pending-literal element-type resolution. Existing violations are tracked debt (TODO.md, the `[thir] RULE` entry), not accepted residue. A fix that adds a lowering row keyed on a method name or a callee kind is rejected at analysis; a runtime signature that refuses the form the language model prescribes (a `str` argument is a view) is fixed in the runtime. `--dump-thir` prints a module's lowered IR. Design: `docs/IR_DESIGN.md`.

**C++ build** (`compiler.py` / `cli.py`): After codegen, the CLI generates a CMake sources file or invokes the C++ compiler directly. Object files are compiled in parallel (`-j`), then linked. Optional ccache integration.

**Whole-run build cache** (`build_cache.py`): after a successful binary-producing build the CLI writes a manifest next to the binary (every input file, module-resolution outcomes, toolchain identity, options key); an unchanged rerun skips the whole pipeline, replays recorded warnings, and `os.execv`s the binary (~100ms). To keep that warm path fast, `tpyc/__init__.py` and `tpyc/modules/__init__.py` re-export lazily (PEP 562) and the C++ toolchain block lives in light `toolchain.py` -- don't add heavy imports to those or to `cli.py`'s module level. `--rebuild` bypasses the cache.

### Source layout

The compiler lives in `tpyc/`. Modules are grouped by phase -- browse `tpyc/` to see the full layout. Key top-level entries:

- `cli.py` -- CLI entry point
- `build_cache.py` -- whole-run up-to-date check (manifest + warm-path exec); light imports only
- `toolchain.py` -- C++ toolchain discovery/config (CppCompilerConfig, warn flags, PCH); light imports only
- `parse/` -- parser package (`parser.py`, `nodes.py`, `imports.py`)
- `typesys.py`, `type_resolver.py` -- type system and resolver for parser-emitted `TypeRefNode`s
- `sema/` -- multi-pass semantic analysis (analyzer, statements, expressions, calls, methods, protocols, narrowing, mutation_propagation, value_range, flow_facts, match, ...)
- `codegen_cpp/` -- the C++ printer/skeleton layer (generator, functions, records, protocols, types, gen_generators, gen_async, string_dispatch, emit_prims, ...). Bodies come from `thir/`, not from here
- `interop/` -- the CPython `@export` boundary gathered in one package: shared shape/classifier predicates (`export_shape`), the sema and whole-program validators (`sema_validators`, `module_validators`), and the extension glue emitter (`extension`). The phase call sites in sema/compiler/codegen stay thin hooks.
- `thir/` -- Typed High-level IR: `lower/` builds it from the analyzed AST, `emit.py` renders it. The sole author of every emitted body (see "THIR and the codegen boundary"); the landed half of the IR direction in `docs/IR_DESIGN.md`
- `modules/` -- resolution helpers and constant tables. Note: builtin types, functions, and protocols are *defined* in `.py` stubs under `lib/tpy/`, not here. Generic type factories and per-qname behavior live on `tpyc.type_def_registry.TypeDef`; see `docs/ARCHITECTURE.md` for the nominal/structural split.
- `compiler.py` -- multi-module orchestration
- `macro_api.py`, `macro_loader.py` -- compile-time macro module support
- `repl.py`, `repl_backends.py` -- REPL

### Runtime (`runtime/cpp/include/tpy/`)

The C++ runtime is a modular header library; `tpy.hpp` is the umbrella include. Headers are grouped by concern (types, containers, printing, iterators, protocols, builtins, generators, bytes, files, etc.). Each header has a top-comment documenting its role -- browse the directory to see the full set.

Generated code requires **C++23** (for `std::ranges` concepts and `std::expected`). With libstdc++ that means **clang >= 19**: clang 18 defines `__cpp_concepts` as 201907, which keeps libstdc++'s `<expected>` disabled (clang 19 bumped it to 202002; GCC and zig's bundled libc++ are unaffected). Generated code also uses the **GCC statement expression extension** (`({ ...; value; })`) wherever codegen needs expression-level locals: `@error_return` unwrapping, list/dict/set/array comprehensions, chained comparisons with complex intermediates, and the `x in (a, b, c)` membership form with a complex LHS. This extension is supported by GCC, Clang, and all LLVM-based compilers (Intel ICX, ARM armclang, IBM Open XL). It is **not** supported by MSVC.

### Libraries (`lib/`)

**Compiler search order** (first match wins):
1. Entry point directory (user modules)
2. `-L` paths (user-specified, in order)
3. `lib/tpy/` (tplib, stdlib modules, tpy protocols)

The CPY phase of `test_case` uses `PYTHONPATH=lib/cpy/:src_dir`.

Conventions:
- `lib/tpy/tpy/` is the TPy package. `tpy`, `typing`, `builtins` are **implicitly compiled** into every program.
- `lib/tpy/tplib/` -- TPy-native user types (`Box`, `ArrayList`, `FixStr`, JSON).
- `lib/tpy/_bindings/` -- raw `@native` binding layers used by stdlib modules (e.g. `pcre2.py` for `re`, `posix_socket.py` for `socket`). **Not for direct user import.** Put future raw-libc bindings here; keep `tplib/` reserved for TPy-native user types.
- `lib/cpy/tpy/` -- CPython stubs for TPy types (seen only by CPython, not tpyc). Tests depending on compiler-specific behavior use `no_cpython.txt`.

### Vendored third-party C/C++ libraries (`runtime/cpp/third_party/`)

Stdlib modules binding C/C++ libraries (e.g. `re` -> PCRE2) vendor upstream source under `runtime/cpp/third_party/<lib>/` with a sidecar `<lib>.vendor.json` (version/URL/SHA256) and a `scripts/vendor_<lib>.py` reproducer.

Each lib has a hand-written facade header at `runtime/cpp/include/tpy/stdlib/<lib>_h.hpp` that mirrors only the symbols/types we use and **does not** include the upstream C header -- this keeps upstream macros out of TPy-generated TUs (avoids preprocessor collisions with TPy module-level constants of the same name). The vendored .c files include the real upstream header during their separate compilation; the linker resolves our `extern "C"` declarations to those symbols.

User opt-in per stdlib module: `tpyc --<lib>=bundled|system|auto|none`. Default is bundled. Stdlib modules declare the dependency via `# tpy: link("pcre2", managed=True)`; plain `link("foo")` remains raw `-lfoo`. See `runtime/cpp/third_party/README.md` for the bump procedure.

## TPy custom types (quick reference)

Full TurboPython -> C++ type mapping lives in `docs/LANGUAGE_FEATURES.md`. Types worth remembering at the CLAUDE.md level (these are TPy-specific, not Python stdlib analogs):

| Type | C++ |
|------|-----|
| `int` | `tpy::BigInt` (arbitrary precision) |
| `String` | `::tpy::String` (a `std::string` subclass; parameters `const ::tpy::String&`) -- the mutable, owning sibling of `str`. Its own C++ type so a trait keyed on the C++ type can tell it from `str` |
| `bytes` / `bytearray` | `::tpy::Bytes` / `::tpy::ByteArray` (both `std::vector<uint8_t>` subclasses; a `bytes` parameter is `::tpy::BytesView`, a `bytearray` one a reference). Distinct from each other and from `list[uint8]`, which keeps the plain `std::vector<uint8_t>` |
| `StrView` | `std::string_view` |
| `char` | `char` |
| `Span[T]` / `Span[readonly[T]]` | `std::span<T>` / `std::span<const T>` |
| `SpanIter[T]` | `tpy::SpanIter<T>` |
| `Array[T, N]` | `std::array<T, N>` |
| `Ptr[T]` / `Ptr[readonly[T]]` | `T*` / `const T*` |
| `Own[T]` | `T` by value (for returns/params); ownership transfer, not heap allocation |
| `Box[T]` | Pure-TPy heap-allocated owning container (`tplib/box.py`); `@nocopy`, explicit `.clone()` to duplicate. Construct via `Box(value)` where `value: Own[T]`. |
| `Rc[T]` / `Weak[T]` | Pure-TPy non-atomic shared ownership + weak companion (`tplib/rc.py`); `@nocopy`, `Rc.new(v)` / `.clone()` / `.downgrade()` / `.upgrade()` |
| `Arc[T]` / `Weak[T]` (arc) | Atomic-refcount `Rc`/`Weak` that crosses threads (`tplib/arc.py`); see `docs/LANGUAGE_FEATURES.md` "TurboPython -> C++ Type Mapping" |
| `Atomic[T]` (`T: AnyFixedInt`) | `std::atomic<T>` wrapper (`tpy.atomic`); see `docs/LANGUAGE_FEATURES.md` "TurboPython -> C++ Type Mapping" |
| `Mutex[T]` / `RwLock[T]` | `std::mutex` / `std::shared_mutex` locks with context-manager guards (`tpy.sync`); canonical shared form `Arc[Mutex[T]]`; see `docs/LANGUAGE_FEATURES.md` "TurboPython -> C++ Type Mapping" |
| `Condvar` | `std::condition_variable` taking a live `Mutex` guard (`tpy.sync`); re-check the predicate in a loop (spurious wakeups); see `docs/LANGUAGE_FEATURES.md` "TurboPython -> C++ Type Mapping" |
| `BytesView` | `::tpy::BytesView` (a `std::span<const uint8_t>` subclass, so the bytes family has the `==` / `<=>` / `std::hash` the standard gives `std::string_view` but not a span) |
| `A \| B` (value types) | `::tpy::Union<A, B>` -- a `std::variant` that owns Python's comparison rule (by value across alternatives), so a union compare and every container over one render the bare C++ operator |
| `A \| B` (non-value, params/returns/locals) | `::tpy::Union<A*, B*>`, or `::tpy::Union<const A*, const B*>` at a parameter the body does not mutate through -- the BORROW form. ONE type at every position: the ALTERNATIVES say which form it is, and a pointer alternative makes the comparison go through the POINTEE (its `__eq__`, or identity when it defines none, which only a borrow position can answer) |
| `A \| B` (non-value, fields/containers) | `::tpy::Union<A, B>` (storage form; same type as a value union's, so one type owns `==`) |
| `basic_slice` / `slice` | `tpy::BasicSlice` / `tpy::Slice` |

## Supported Language Features

See `docs/LANGUAGE_FEATURES.md` for comprehensive documentation of all language features (working, planned, and open for design) and the full type mapping table.

## LSP Target (Future, blocked on IR migration)

An LSP for TPy is a possible future direction, **not a project goal**. Blocked on the IR migration (`docs/IR_DESIGN.md`) -- THIR has landed, MIR has not. Treat the invariants below as **tie-breakers**, not overrides of compiler-centric reasoning:

- Diagnostics carry structured source locations (file + span + severity), not only formatted strings.
- Parser output (`TpyModule` / TPy AST nodes) is the downstream boundary; downstream phases should not depend on CPython `ast` specifics.
- Avoid hard-coded "single entry point" assumptions where a workspace (multi-entry) model works just as well.
- Keep type representations introspectable and printable (hover, completions, signature help need to render types back to the user).

## Compiler Front-end Performance

The tpyc front-end is fast enough for realistic dev workflow at this stage; the C++ back-end dominates total build time. **Do not proactively optimize the front-end** (no incremental caching, self-hosting, LLVM backend, or hot-path tuning) until real workloads demand it. Any cache layer or micro-optimization will bitrot as data structures churn, and the IR migration (`docs/IR_DESIGN.md`) is the proper foundation for future incremental/caching work -- THIR is explicitly designed as the natural cache boundary.

**Until the IR migration lands**, prefer cheap hygiene that does not fight the migration direction:
- **Per-compilation state belongs on the `Compiler` instance, not module-level globals** -- add a field to `Compiler._init_shared`, read via `get_current_compiler()`. (`_dynamic_attached_qnames` in `type_def_registry.py` is the one known holdout.)
- **A new sema->codegen fact belongs on a THIR node or the lowering context** -- not on a parse node, and never in a raw side table keyed by `id(node)` -- use `tpyc/identity_map.py` where a table keyed on an object is right. THIR is the boundary codegen reads; a fact parked anywhere else has to be re-found at emit time.
- **Tag a distinction where it is DECIDED, not where it is consumed.** "Consumer inspects shape and re-derives the fact" is exactly what THIR removes.
- **BorrowTracker extensions must think in `Place`/`LoanInfo`, not string keys.** If the natural expression is a string key, the feature is not designed yet.
- **Per-type C++ knowledge belongs on `TypeDef`,** not in codegen `if`-chains.
- **Keep the Phase-1 / Phase-2 split clean** and Phase-1 dependent on signatures only, not other modules' bodies.

**Watch for algorithmic cliffs.** The worst front-end regressions come from accidentally quadratic scaling in size-proportional places -- overload resolution, protocol conformance checking, mutation propagation. Invisible on small codebases, brutal on large ones. Changes touching these areas should consider worst-case size scaling even when benchmarks on the existing test corpus look fine.

## Concept pointers

Features this doc references or assumes, with one-line explanations and deeper-dive pointers:

- **`@native` interop** -- decorator system for declaring C++ functions/types/globals visible to TPy (used by stdlib bindings and user interop). Tests using it require hand-written C++ companion files (`src/native_types.{hpp,cpp}`) and often need `no_cpython.txt`. See `docs/NATIVE_INTEROP.md`; examples in `tests/cases/native/`.
- **Compile-time macros** (`# tpy: macro_module`) -- modules that run under CPython at compile time to generate/transform AST. Four kinds: class macros (`@class_macro` modifying the decorated class, e.g. `@dataclass`, JSON `@model`), call-site macros (`@call_macro` rewriting one expression, e.g. `asdict()` / `astuple()`), builder-trace macros (`@builder_macro` walking a builder pattern at compile time, e.g. `argparse.ArgumentParser`), and function macros (`@function_macro` rewriting a whole free-function or record-method body, e.g. local-variable type deduction). Public API in `tpyc/macro_api.py`, loader in `tpyc/macro_loader.py`, builder-trace expander in `tpyc/sema/builder_trace.py`, function-macro phase in `tpyc/sema/function_macros.py`. See `docs/MACRO_DESIGN.md`; examples in `tests/cases/macros/` and `tests/cases/argparse/`.
- **Overloading** -- two decorators for two forms: `typing.overload` is CPython's shape (bodyless stubs plus one trailing implementation, specialized per stub), `tpy.dispatch` is TPy's (every same-named variant is its own implementation: a body, `@native` or `@cpp_template`; the whole builtin stub surface is written this way). Same overload set and call resolution either way; the decorators never mix under one name. See `docs/OVERLOAD_DESIGN.md`; examples in `tests/cases/calls/` (`overload_*`, `dispatch_forms`).
- **`@error_return`** -- zero-cost error handling via `std::expected`. Part of a two-tier exception model: ordinary `try`/`except` for throw/catch, `@error_return` for hot paths that want explicit propagation. See `docs/ERROR_RETURN_DESIGN.md` + `docs/EXCEPTION_DESIGN.md`; examples in `tests/cases/error_return/` and `tests/cases/exceptions/`.
- **Readonly system** -- `readonly[T]` type modifier, `@readonly` method decorator (marks methods that don't mutate `self`), and `@auto_readonly` for methods whose return const-ness tracks the receiver (cloned during sema method expansion into a mutable + const pair). See `docs/READONLY_DESIGN.md`; examples in `tests/cases/readonly/`.
- **Protocols** -- structural protocols (duck-typed at compile time, monomorphized per concrete type) vs `@dynamic` protocols (runtime polymorphism via `Adapter`/`RefAdapter` wrappers). See `docs/PROTOCOL_DESIGN.md` + `docs/DYNAMIC_PROTOCOL_DESIGN.md`; examples in `tests/cases/protocols/`.
- **Flow-sensitive analysis** -- `sema/flow_facts.py` snapshots per-branch state (assignment, termination, narrowing, consumed vars) as immutable values to merge at join points; `sema/narrowing.py` handles Optional/None narrowing; `sema/value_range.py` tracks integer `[lo, hi]` ranges used for bounds-check and div-zero elision.
- **Borrow form / storage form** -- non-value types have two C++ shapes: *storage form* at fields / container elements / `Own[T]` slots / returns (self-contained value, e.g. `std::optional<T>`, `::tpy::Union<A, B>`), and *borrow form* at function params / locals / iterator yields (indirect reference, e.g. `T*`, `::tpy::Union<A*, B*>`, `T&`). Codegen emits per-element conversion helpers at boundaries (`tpy::optional_to_ptr` / `ptr_to_optional`, `to_ptr_variant`, `tuple_to_storage` / `tuple_to_pointer`). The duality is the source of a recurring bug class; see `docs/LANGUAGE_FEATURES.md` "Borrow Form vs Storage Form" for the canonical introduction, and `docs/IR_DESIGN.md` Open Questions item 9 for the planned IR-level resolution.
- **async/await** -- `async def`/`await` lower to a state-machine struct with `poll(waker) -> Poll[T]` via a "resumable-frame" abstraction in codegen (not C++20 coroutines). `await` works in arbitrary control flow (if/while/for/try/except/with/finally) via a localized CFG in `tpyc/codegen_cpp/resumable_cfg.py`. The same resumable frame is shape-neutral: every generator (`yield`) shares it -- it is the single generator emitter, single-yield generators included -- so `yield` and `await` lowering are unified. Cancellation is exception-based (`CancelledError` thrown at next suspension). The asyncio runtime (`run`/`sleep`/`create_task`/`Task`/`Future`/`Event`) is mostly TPy code in `lib/tpy/asyncio/` on a small C++ executor bridge. See `docs/ASYNC_DESIGN.md` for the design and `docs/ASYNC_PROGRESS.md` for milestone history; examples in `tests/cases/async/`.

## Key Documentation Files

| File | Purpose | Policy |
|------|---------|--------|
| `docs/LANGUAGE_FEATURES.md` | Comprehensive language feature documentation + type mapping | **Keep up-to-date** with any development |
| `docs/STDLIB_ROADMAP.md` | Python stdlib coverage tracker (per-module items, status, blockers) | **Keep up-to-date** when adding/changing stdlib modules |
| `docs/ARCHITECTURE.md` | Compiler architecture: nominal/structural types, TypeDef registry, sema layout, perf tradeoffs | Update when type-system or sema structure changes |
| `docs/PITFALLS.md` | Language and generated-code rules that keep passing review, each with a real example and a check | Add an entry when a class of defect is caught by hand a second time |
| `CLAUDE.md` | Commands, architecture overview, agent rules | Update when adding major features |
| `README.md` | Quick start, build flags | Update when CLI changes |
| `TODO.md` | Current priorities | Check before starting non-trivial work |
| `RELEASE_PLAN.md` | Milestone slice of TODO.md: queued features for the next release. Bugs are NOT listed -- every `IMM`/`HIGH` entry in BUGS.md blocks the release, so the tags are the must-fix set | Short bullets only, each linking into TODO.md via a quoted greppable phrase; delete a release's section when it ships |
| `docs/TUPLE_COMPLETION_PLAN.md` | Ordered, release-split work units for tuple-element-equals-scalar, with the measured element-form x position matrix | Tick units with their merge commit; re-run and replace the matrix, never append |
| `BUGS.md` | Known compiler defects (incorrect output, crashes, miscompiles, rejection of valid code, missing safety diagnostics). Has a `## Compiler bugs` section and a `## Safety / borrow checker` section -- file borrow-checker / view-lifetime gaps in the latter | Add new bug entries here, not in TODO.md |

**Before committing**: If code adds new features or changes behavior, update `docs/LANGUAGE_FEATURES.md` in the same commit to reflect the current state (Working/Planned/Open status).
