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

**Snippets**: write to a file under `/tmp/agents/` (any filename or subdirectory) and run from there. Do NOT use heredocs (`<<EOF`) -- they trigger permission prompts for multi-line commands.

**Committing -- branch-aware.** A *temporary working branch* is any branch that is **not** `master`/`main` **and** has **no** remote-tracking (upstream) branch -- a throwaway branch for developing a feature before it's squash-merged into `master`. Detect it mechanically: branch from `git branch --show-current`; upstream from `git rev-parse --abbrev-ref --symbolic-full-name @{u}` (this command fails when there is no upstream). Make this determination yourself -- do **not** ask the user whether the branch qualifies.

- **On a temporary working branch:** auto-commit your *own* completed work at each meaningful checkpoint -- a finished plan step, a self-contained edit set, a green run for a change you made, or fixes applied after a review pass (manual or automatic). A checkpoint is a coherent unit you'd want to point back to; err toward fewer substantive commits, not a stream of micro-commits after every edit. Just commit -- do **not** ask permission each time; this rule *is* the standing permission. (You still *pause and flag* -- without halting to ask about routine commits -- if a commit would sweep in something genuinely off: a secret, a stray binary, or changes you didn't make.) Stage only the files you created or modified for that work -- **never** `git add -A` / `git add .` (the tree carries untracked scratch and binary files that must not land in a commit). Concise messages are fine; the branch is squash-merged into `master`, so working history is throwaway.
- **Everywhere else** (`master`/`main`, or any branch that *does* track a remote): never `git commit` or `git add` unless the user explicitly asks.

In all cases: never `--amend`, rebase, force-push, or commit changes you didn't make; never push without an explicit request. An explicit user instruction ("don't commit yet", "hold off") always overrides the auto-commit default.

**Never prune worktrees.** Development uses multiple git worktrees (sibling checkouts sharing one `.git`), but an agent sandbox often can't see the sibling paths -- so they *look* missing. Never run `git worktree prune`, `git gc --prune`, or `git worktree remove` on a path that only appears absent: from the sandbox, prune deletes the other worktrees' admin entries and orphans their checkouts (including unmerged work). More generally, never do destructive git cleanup based on path-existence evidence -- verify against reality first. A machine-local safeguard backs this up (`gc.worktreePruneExpire never`, so a bare prune and auto-gc won't expire worktree entries), but an explicit `--expire` overrides it, so this rule stands regardless.

**Commit message format.** Subject line under ~72 chars; **hard-wrap the body at ~72 columns** (don't emit one long unwrapped line per paragraph -- when using `git commit`, pass a wrapped `-F <file>` rather than long `-m` strings). No `Co-Authored-By` or other LLM/tool-generated references.

**Spirit over letter.** These commit rules exist to (a) spare the user the manual-commit chore and (b) keep junk out of `master` -- not to be performed as a ritual. If following them literally would waste time or nag about obviously-legitimate files, optimize for that intent instead.

**Never make design decisions autonomously.** If during implementation you discover the plan needs to change (new concept, behavior split, workaround for an unforeseen constraint), **stop and consult the user** before proceeding. If you encounter a hard problem or are unsure how to proceed, ask first.

**Add tests** when adding new features or making changes that affect generated code. Cover happy path, errors/warnings, edge cases, and regression guards.

**Report pre-existing bugs** discovered during implementation. Do not silently ignore bugs in adjacent code just because your change didn't cause them. Check `BUGS.md` first -- if the issue is already tracked, reference the entry; otherwise add a new entry there (not in `TODO.md`).

**Bug-fix discipline.** The compiler is tightly coupled; narrow patches at the symptom site routinely leave the underlying invariant violation in place and produce new symptoms in adjacent features (e.g. a fix for `Optional` that doesn't consider `Union`, a fix for functions that doesn't consider methods/constructors, a fix for generators that doesn't consider async/context-managers). For any bug-fix work, invoke `/tpy-fix-bug` to walk through the analysis procedure before proposing code changes. The skill starts with an impact assessment (trivial / localized / architectural) and gates the depth of analysis accordingly. The user must see and approve the analysis before any code change beyond trivial fixes. When time pressure pushes you to skip analysis, push back once -- the cost of one extra round is much lower than the cost of a wrong fix that surfaces later as new bugs.

**Feature discipline.** Same coupling cuts the other way for new work: features that don't consider sibling concepts and existing patterns produce duplication that's expensive to unwind (e.g. a new narrowing rule that handles `Optional` but not `Union`, a new ownership form that fits methods but not generators, a new emit path that duplicates an existing codegen helper). For any new-feature or feature-extension work, invoke `/tpy-add-feature` to walk through the design procedure before writing code. The skill starts with a scope assessment (trivial / localized / architectural) and gates the depth of design accordingly. The user must see and approve the design before any implementation beyond trivial changes. When time pressure pushes you to skip the design phase, push back once -- a wrong design is much harder to unwind than a wrong fix.

**Autonomous backlog handling.** `/tpy-next` selects and fully handles one bounded backlog item end-to-end (analysis, implementation, review, merge-ready squash branch). Invoking it is a standing approval ONLY for work passing its eligibility gate (trivial/localized, High confidence, CPython-parity clean, bounded snapshot churn); design decisions, new warnings/escape hatches, and architectural work still stop and present per the rules above. The merge into master always remains the user's.

**Branch/merge workflow skills.** `/tpy-merge-master` merges `master` into the current working branch and verifies the result (semantic-conflict check over the files/functionality both sides touched, not just a textual merge). `/tpy-review` runs the multi-agent defect review; `/tpy-ready` is the merge-readiness + retrospective gate; `/prep-merge` squashes the branch into a clean commit ready for master. Typical order on a finished branch: `/tpy-merge-master` (pull in master) -> `/tpy-review` -> `/tpy-ready` -> `/prep-merge`.

### Common commands

```bash
uv run tpy                              # Interactive REPL
uv run tpy examples/hello.py            # Run a program
uv run tpy --dump-code examples/hello.py  # Print generated C++ to stdout
uv run tpy examples/hello.py -vv        # -v = commands+timing, -vv adds generated C++
uv run tpyc examples/hello.py -o out/   # Compile to C++ only
uv sync                                 # Install for development
```

## Testing

Each folder under `tests/cases/` becomes one parametrized item of `test_case` with three phases: **comp** (compile + diagnostics + snapshot check + annotation validation), **exec** (build + run C++), and **cpy** (run with CPython, compare to `output.txt`).

**Exec skip is local, not committed.** The exec phase (C++ build + run) skips only when this exact build already ran green on this machine's toolchain, tracked by a gitignored content-addressed marker cache under `~/.cache/tpyc/exec-results/`. The key covers everything determining the binary and its output (toolchain, runtime headers, every module's generated C++ including the stdlib, hand-written C++ companions, link flags, stdin fixtures), so a compiler edit that leaves the emitted C++ identical reuses the cache. `--force-exec` overrides; switching `--cxx` or touching the runtime re-keys everything. The cache is shared across worktrees.

The cpy phase (CPython run) still auto-skips via **committed** fingerprints -- CPython output is toolchain-independent, so a committed key is portable:

- **Session-level** (`tests/.session_fingerprints.json`, single file): `cpy_stubs` -- hash of `lib/cpy/tpy/**`, affects every CPython run. (The legacy `runtime` / `libtpy` keys still recorded here no longer gate any phase -- exec uses the local cache now.)
- **Per-case** (`tests/cases/<case>/expected/.fingerprints`, optional): hash of the case's `main.py` (`main`). Cases without a CPython phase end up with no file.

Parallel execution (`-n auto`) is configured in `pyproject.toml` via `addopts`. Worker count auto-caps to the cgroup v2 CPU quota. The exec phase also reuses a persistent content-addressed cache of compiled stdlib object files (see `tpyc/` code for the cache-key derivation).

**CPython interop ext-exec harness.** A second test module, `tests/test_interop_exec.py`, covers the runtime half of CPython extension authoring (`docs/CPYTHON_INTEROP.md`). Cases live under `tests/interop/<case>/src/` (a `# tpy: ext_module` source named after the module -- the filename is the import name -- + `driver.py` + optional `ext_checks.py`; `expected/` stays at the case root like `tests/cases`) and run under plain `uv run pytest`. Per case: snapshot the module + glue C++ + front-end diagnostics (`diag.txt`, with `# tpyc:` annotation validation) into `expected/` (always); build the `.so` via `tpyc -b` and import it under CPython against `driver.py`, gated by the same shared `exec-results/` marker cache as exec; run the THIR overlay (re-emit through THIR, byte-diff against the AST oracle, ratchet unmarked cases -- `no_thir.txt` sits at the interop case root and these cases are tallied separately from the migration dial); assert cpy-parity (the same driver over the TPy source via `lib/cpy` stubs); run `ext_checks.py` if present. `test_facade_selfcheck` compiles the ABI mirror against real `Python.h` once (skipped when absent). Under `--build-only` or a cross toolchain only the snapshot half runs. `update_snapshots.py` regenerates these `expected/` files too. See `tests/interop/README.md`.

### Test commands

```bash
uv run pytest                              # All tests (exec skips per the local cache; cpy skips per committed fps; THIR byte-diffs EVERY user case by default, ratchets the unmarked ones)
uv run pytest --force-exec                 # Force exec + cpy unconditionally (ignore the local exec cache)
uv run pytest --no-exec                    # Skip the exec phase entirely (comp + cpy only); fast codegen/diagnostics iteration
uv run pytest --no-thir --no-exec          # Disable THIR entirely: emit + byte-diff via the AST path only (no overlay, no ratchet) -- pure-AST mode for fast AST-codegen iteration (conflicts with --thir-codegen/-classify/-check-flip)
uv run pytest --thir-codegen --no-exec     # Ignore no_thir.txt entirely (no ratchet) + print the whole-corpus faces/shapes coverage metrics (the byte-diff itself already spans every case; conflicts with --update-snapshots)
uv run pytest --thir-classify --no-exec    # (Re)write no_thir.txt markers (add where a user module has THIR fallback, remove where clean) -- bootstrap/maintain the per-case migration state
uv run pytest --thir-check-flip --no-exec  # List marked cases now clean enough to un-mark (delete no_thir.txt) -- the porting-progress query
uv run pytest --thir-stdlib --no-exec      # Route lib/tpy + stdlib through THIR too and byte-diff against the SAME RUN's AST output (stdlib has no committed snapshot; cutover gates A5/D4). Writes nothing; conflicts with --update-snapshots / --no-thir; roughly doubles codegen per case
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

Harness-emitted status lines (cache builds, toolchain/ccache status, the active-options summary, warnings) are prefixed with `tpy|` so they stand out from pytest's own output. The terminal summary adds a `tpy| exec:` tally of how many cases built+ran vs skipped via the local cache (or that exec was disabled via `--no-exec`, or built without running under `--build-only`/a cross toolchain). When a shared input (toolchain, runtime headers, or the full stdlib output) changed since this checkout's last run -- invalidating every case's marker -- a `tpy| exec:` line at session start names the cause, so a whole-suite re-verify isn't a surprise. THIR runs by default over every user case; the summary prints the routed-body count, the migrated-case dial, and any byte-diff divergences (labelled with the enclosing function). An unmarked case that falls back FAILS the comp phase (the ratchet). When a committed cpy fingerprint is stale (the session-level `cpy_stubs` hash, surfaced once at session start, or a per-case `main` hash, otherwise buried in the warnings summary), the summary also ends with a bold-yellow `STALE FINGERPRINTS` banner naming the refresh command(s) -- the session stays green, but the stale fingerprint can't be missed.

### Agent testing workflow

**Running tests**: prefer `rpytest <args>` over `uv run pytest <args>` (a drop-in for `uv run pytest` only) -- it offloads the build+run to a remote host to free this machine's CPU. It *always* offloads: if the build host is unreachable it hard-errors (never silently runs locally), so for a deliberate local run use `uv run pytest` directly. If `rpytest` isn't on `PATH` at all, use `uv run pytest`.

**CPU awareness**: never start a new test run while a previous one is still running. Either wait, or kill it (`pkill -f pytest`). Concurrent test suites saturate all cores and slow everything down for all agents and the user.

**During development**, run targeted subsets with `-k pattern` (the new tests you're adding, or categories likely affected by your changes). **Final verification**: run the full suite once, after all changes are done, before reporting complete. Prefer `rpytest`; a deliberate local `uv run pytest` is equally valid, and is the fallback when `rpytest` is unavailable. Use `--force-exec` to re-run exec for every case regardless of the execution cache.

### Snapshot policy

**If a change would modify expected output for *existing* tests** (not new tests you're adding), consult the user before running `update_snapshots.py`. Explain what generated code will change and confirm the change is desired.

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
├── no_thir.txt            # (optional) Exempt this case from the THIR ratchet (not yet migrated; still byte-diffed -- see "THIR migration")
├── options.json           # (optional) Per-case compiler options
└── expected/
    ├── diag.txt           # Compiler diagnostics
    ├── include/main.hpp   # Generated header (if compiles)
    ├── src/main.cpp       # Generated source (if compiles)
    ├── output.txt         # Runtime output (or panic.txt)
    └── .fingerprints      # (optional) per-case `main.py` hash gating the cpy-phase skip
```

`options.json` is layered: the conftest walks up from the case directory toward `tests/cases/`, merging every options.json it finds (deeper file overrides; `dsl_opts` merges per-key). One file at the group level (e.g. `tests/cases/pascal/options.json`) covers every case underneath; per-case files only need the keys that differ. Supported keys:

- `default_int` -- `"Int32" | "Int64" | "BigInt"`. Per-case scope.
- `plugin` -- path (relative to repo root) to a frontend-plugin `.py` file. Usually set at the group level (e.g. `frontends/pascal/pascal_frontend.py`).
- `dsl_opts` -- dict of string options passed to the plugin's constructor (forwarded as if via `--dsl-opt name.key=value` on the CLI). Library paths come from the plugin's `library_paths()` hook -- no `-L` plumbing needed in options.json.

### Adding a new test case

1. Create the folder under `tests/cases/<group>/` using the naming convention above.
2. Write `src/main.py` with a short comment (1-2 lines) at the very top explaining what the test covers -- test names alone are often not enough context. Prefer putting test logic inside functions (`def main(): ...` + `main()`) rather than top-level statements. Top-level codegen differs from function codegen (globals use pointer slots, different variable model), so use top-level statements only when specifically testing global variable behavior.
3. Add `# tpyc:` annotations on lines testing specific compiler behavior. Multiple per line supported (e.g. `# tpyc: warning(/a/) warning(/b/)`):
   - `# tpyc: ok` -- line should compile without error or warning
   - `# tpyc: error(/regex/)` -- line should produce an error matching the regex
   - `# tpyc: warning(/regex/)` -- line should produce a warning matching the regex
   - `# tpyc: type(TypeName)` -- assert inferred type (e.g. `s = "hello"  # tpyc: type(StrView)`). Supports regex with `/pattern/` syntax. Comp-phase only, not validated in update mode.
   - `# tpyc: non_null(var)` -- assert `var` is proven non-null at this ptr dereference (skips `deref_check`). Comp-phase only.
   - `# tpyc: nullable(var)` -- assert `var` is NOT proven non-null at this ptr dereference (uses `deref_check`). Comp-phase only.
   - `# tpyc: is_send(yes|no)` / `is_sync(yes|no)` -- assert the declared variable's Send/Sync trait (on a declaration or for-loop line). Comp-phase only.
   - `# tpyc: frame_send(yes|no)` / `frame_sync(yes|no)` -- assert an async/generator function's FrameType traits (on the `def` line). Comp-phase only.
4. Run `uv run python tests/update_snapshots.py {name}` to generate expected outputs.
5. Run `uv run pytest -k {name}` to verify.

**CPython compatibility**: avoid adding `no_cpython.txt` unless absolutely necessary. Most tests can be made CPython-compatible by adding `__init__` methods (CPython doesn't create instance attributes from type annotations alone) and using `lib/cpy/` stubs. The `lib/cpy/tpy/` package provides CPython implementations of TPy types (`Own`, `nocopy`, `UninitHeapStorage`, etc.). Only skip CPython when the test truly depends on C++-only behavior (e.g. `@native` interop).

**Reference-type happy tests must force the value-vs-reference distinction.** A *passing* test that moves a reference type (class / `list` / `dict` / `set` / recursive-union wrapper) across a boundary -- `yield`, `return`, local binding (`x = accessor()`), container insert, param passing -- must either **mutate the shared object after the boundary and observe the change**, or use a **`@nocopy` type** (so a silent copy becomes a compile error). Read-only output is *parity-blind*: the cpy phase only byte-compares `output.txt`, so a test that just reads can match CPython exactly while TPy silently *copied* where CPython would *alias* -- the divergence (and any aliasing/UAF bug behind it) stays invisible. If copy semantics are genuinely intended at that boundary, say so in the top-of-file comment so the next reader knows it wasn't an oversight. (This mirrors the cpython-parity reviewer's test-adequacy check -- but the author should not rely on review to catch it.)

**Tests must emit host-independent output.** `output.txt` is committed once and byte-compared everywhere, so anything OS-, filesystem- or CPython-version-specific passes only on the host it was snapshotted on. Three known offenders: absolute temp paths (`/tmp` is a symlink on macOS -- compute `os.path.realpath("/tmp")` at runtime and print comparisons, not paths); OS-divergent errno (the same op raises different `OSError` subclasses per OS -- catch both in one `except` and print one stable token); and CPython-version-divergent output (exception message text, `strftime` padding, `fromisoformat` leniency -- print a type or stable token instead). To keep a version-pinned assertion, move it to a `no_cpython.txt` case.

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
| `1`, `"hi"`, `{1, 2}`, `-1` | `Int32(1)`, `StrView("hi")`, `Int32(-1)` |
| `n = len(xs)`, `i < len(xs)` | `n = Int32(len(xs))`, `i < Int32(len(xs))` |
| `self.count = 0`, `self.count += 1` | `self.count = UInt32(0)`, `self.count += UInt32(1)` |
| `poll_pending()` (return type infers `T`) | `poll_pending[None]()`, `poll_pending[T]()` |
| `def f() -> Poll[T]: return poll_pending()` | `... return poll_pending[T]()` |

TPy infers integer-typed-field assigns and augmented-assigns, comparisons that widen BigInt to a sibling IntN, and generic-function type params from return-type context. The explicit form is appropriate when the inferred type would be wrong (e.g. `n: BigInt = len(xs)` when you actually want BigInt for arithmetic that would overflow Int32) or when there's no return-type context at all.

**Comments explain WHY, not WHAT -- and carry no dead history.** Write a comment only when the *why* is non-obvious: a hidden constraint, an invariant, a workaround for a specific bug. The code already says what it does, so don't narrate it. Keep comments short -- one line is usually enough; avoid multi-paragraph blocks. Do **not** embed transient or external narrative that rots as the code moves: refactoring-phase markers ("phase 3 of the X migration"), references to other bugs or to design docs that may not exist, or "added for X" / "used by Y" / "handles issue #N". That context belongs in the commit message or PR, not the source. Applies everywhere, not just `tpyc/`.

## Terminology

TurboPython distinguishes **value types** (primitives, `bool`, `Char`, `str`, `bytes`, views like `Span[T]`, tuples, user types implementing `ValueType` -- value semantics; `is_value_type()` is True) from **reference types** (classes/records, `list`, `dict`, `set`, `bytearray` -- not copied at function boundaries, stored inline in fields and containers; borrow returns render `T&`). Always use "reference types" for the latter, never "object types". `str` and `bytes` are value types although they own buffers: they are immutable in Python, so copy-vs-alias is unobservable -- their mutable siblings (`String` internals aside, `bytearray`) are the reference types. Param shapes are a separate axis: reference types pass by C++ reference (`T&`/`const T&`), while the value-typed `str`/`bytes` pass as views (`std::string_view` / `std::span<const uint8_t>`) -- borrowed, not copied, despite value semantics elsewhere. When classifying a type, check `is_value_type()` / its TypeDef in the compiler rather than reasoning from this list. `Own[T]` means ownership transfer (move), not heap allocation; on a value type it is a no-op spelling (resolves to plain `T`).

## Architecture

The compiler follows a multi-stage pipeline:

```
TurboPython Source (.py) -> Parser -> Semantic Analyzer -> Code Generator -> C++ (.hpp/.cpp) -> C++ Compiler -> Binary
```

### Compilation pipeline

**Multi-module orchestration** (`compiler.py`): The compiler recursively discovers modules by following imports from the entry point, topologically sorts them (Kahn's algorithm), and processes each module through sema and codegen in dependency order. Implicit stdlib modules (`typing`, `tpy`, `builtins`) are always compiled first. Each module's exports (functions, records, protocols, enums, variables) are registered into a shared registry before analyzing downstream modules.

**Parser** (`parse/parser.py`): Single-pass walk over Python's `ast` module output. Scans `# tpy:` directives, converts Python AST to TurboPython AST (`TpyModule`), collects imports, and resolves type annotations against local definitions. No cross-module resolution -- that's deferred to sema.

**Semantic analysis** (`sema/analyzer.py:analyze()`): Two-phase design within each module:
- *Phase 1 -- Registration then analysis*: Types must be registered before they can be referenced. Pass order: imports -> records & enums -> protocols -> inheritance validation -> value-type validation -> type aliases -> functions -> globals -> top-level statements -> record method bodies -> function bodies. Each body-analysis pass runs pre-scan for variable hoisting, then full type-checking.
- *Phase 2 -- Call-graph fixpoint*: After all bodies are analyzed, mutation facts are propagated transitively through the intra-module call graph (`mutation_propagation.py`), `is_readonly` is inferred for methods, and deferred borrow checks are resolved.

**Code generation** (`codegen_cpp/generator.py`): Single pass producing `.hpp` and `.cpp` with careful emit ordering to satisfy C++ forward-declaration constraints: concepts before records, forward decls before full definitions, templates inline in headers, non-template functions in `.cpp`.

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
- `codegen_cpp/` -- C++ code generation (generator, expressions, statements, functions, records, protocols, builtins, types, match, gen_generators, string_dispatch, ...)
- `interop/` -- the CPython `@export` boundary gathered in one package: shared shape/classifier predicates (`export_shape`), the sema and whole-program validators (`sema_validators`, `module_validators`), and the extension glue emitter (`extension`). The phase call sites in sema/compiler/codegen stay thin hooks.
- `thir/` -- Typed High-level IR: the codegen path for migrated user-module bodies, byte-identical to the AST path; byte-diffed on every case by default (see the THIR migration section). The first step of the THIR/MIR migration (`docs/IR_DESIGN.md`)
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
| `StrView` | `std::string_view` |
| `Char` | `char` |
| `Span[T]` / `Span[readonly[T]]` | `std::span<T>` / `std::span<const T>` |
| `SpanIter[T]` | `tpy::SpanIter<T>` |
| `Array[T, N]` | `std::array<T, N>` |
| `Ptr[T]` / `Ptr[readonly[T]]` | `T*` / `const T*` |
| `Own[T]` | `T` by value (for returns/params); ownership transfer, not heap allocation |
| `Box[T]` | Pure-TPy heap-allocated owning container (`tplib/box.py`); `@nocopy`, explicit `.clone()` to duplicate. Construct via `Box(value)` where `value: Own[T]`. |
| `Rc[T]` | Pure-TPy non-atomic shared-ownership wrapper (`tplib/rc.py`); `@nocopy`, explicit `.clone()` to share. Construct via `Rc.new(value)`. |
| `Weak[T]` | Non-owning companion to `Rc[T]`; shares the cell but doesn't keep payload alive. `@nocopy`. Mint via `rc.downgrade()`; recover a strong handle (or `None`) via `weak.upgrade()`. Imported as `from tplib.rc import Weak` (module-scoped so a future `tplib.arc.Weak` can coexist). |
| `Arc[T]` / `Weak[T]` (arc) | Atomic-refcount sibling of `Rc`/`Weak` (`tplib/arc.py`); same shape, `std::atomic<uint32_t>` counters so handles cross threads. `@nocopy`; `Send + Sync` iff `T` is (conditional-override, matches Rust). Construct via `Arc.new(value)`; share via `arc.clone()`. Import as `from tplib.arc import Arc, Weak` (not re-exported from `tplib` -- keeps the atomic runtime out of non-threaded consumers; `Weak` coexists with `tplib.rc.Weak`). |
| `Atomic[T]` (`T: AnyFixedInt`) | `std::atomic<T>` wrapper (`tpy.atomic`); `@nocopy` but movable, `Send + Sync`. Full `std::atomic<integral>` op surface; each op's `MemoryOrder` (a `@native("std::memory_order")` enum) defaults to `SEQ_CST`. CAS returns `(succeeded, observed)`. In-place operators (`+=` etc.) + `str`/`repr`; binary operators / implicit `int()` omitted (would make the racy `a = a + 1` look valid). |
| `Mutex[T]` / `RwLock[T]` | Blocking locks over `std::mutex` / `std::shared_mutex` (`tpy.sync`); `@nocopy`, the interior-mutability primitives. both are `Send + Sync` iff `T: Send` (conditional override). For `RwLock` this is looser than Rust's `T: Send + Sync`, and correct for TPy: a not-`Sync` `T` is either a container (not-`Sync` from shared-mutability, which the readonly read guard removes) or an interior-mutable type built with the unsafe `interior` escape hatch (user owns `Send`/`Sync`) -- Rust needs `T: Sync` only because its `Cell` is *safe* interior mutability. `lock()` / `read()` / `write()` are `@readonly` (lock through a shared handle) and return a `@nocopy` `Deref[T]` guard used as a context manager: `with m.lock() as g: g.append(x)` (deref forwards to the payload); the guard acquires in `__enter__`, releases in `__exit__`. Canonical shared-mutable form is `Arc[Mutex[T]]`. Import `from tpy.sync import Mutex, RwLock`. |
| `Condvar` | Blocking condition variable over `std::condition_variable` (`tpy.sync`); `@nocopy`, `Send + Sync` (internally synchronized, like `Atomic`). `@readonly` `wait(guard)` / `notify_one()` / `notify_all()`: `wait` takes the live `Mutex` guard (`with m.lock() as g: cv.wait(g)`), releases the lock it holds, blocks, then reacquires -- re-check the predicate in a loop (spurious wakeups). The guard is taken via a monomorphized structural hook (static call, no `_RawMutex` in the surface). Pairs with `Mutex` for blocking cross-thread producer/consumer handoff. Import `from tpy.sync import Condvar`. |
| `BytesView` | `std::span<const uint8_t>` |
| `A \| B` (value types) | `std::variant<A, B>` |
| `A \| B` (non-value, params/returns/locals) | `std::variant<A*, B*>` (pointer variant) |
| `A \| B` (non-value, fields/containers) | `std::variant<A, B>` (value variant) |
| `basic_slice` / `slice` | `tpy::BasicSlice` / `tpy::Slice` |

## Supported Language Features

See `docs/LANGUAGE_FEATURES.md` for comprehensive documentation of all language features (working, planned, and open for design) and the full type mapping table.

## LSP Target (Future, blocked on IR migration)

An LSP for TPy is a possible future direction, **not a project goal**. Blocked on the THIR/MIR migration (`docs/IR_DESIGN.md`). Treat the invariants below as **tie-breakers**, not overrides of compiler-centric reasoning:

- Diagnostics carry structured source locations (file + span + severity), not only formatted strings.
- Parser output (`TpyModule` / TPy AST nodes) is the downstream boundary; downstream phases should not depend on CPython `ast` specifics.
- Avoid hard-coded "single entry point" assumptions where a workspace (multi-entry) model works just as well.
- Keep type representations introspectable and printable (hover, completions, signature help need to render types back to the user).

## Compiler Front-end Performance

The tpyc front-end is fast enough for realistic dev workflow at this stage; the C++ back-end dominates total build time. **Do not proactively optimize the front-end** (no incremental caching, self-hosting, LLVM backend, or hot-path tuning) until real workloads demand it. Any cache layer or micro-optimization will bitrot as data structures churn, and the planned THIR/MIR migration (`docs/IR_DESIGN.md`) is the proper foundation for future incremental/caching work -- THIR is explicitly designed as the natural cache boundary.

**Until the IR migration lands**, prefer cheap hygiene that does not fight the migration direction:
- **Per-compilation state belongs on the `Compiler` instance, not module-level globals** -- add a field to `Compiler._init_shared`, read via `get_current_compiler()`. (`_dynamic_attached_qnames` in `type_def_registry.py` is the one known holdout.)
- **Materialize new sema->codegen facts on AST nodes, not side tables** keyed by `id(node)` -- side tables become THIR node fields, so every one is future lowering work.
- **Tag a distinction where it is DECIDED, not where it is consumed.** "Consumer inspects shape and re-derives the fact" is exactly what THIR removes.
- **BorrowTracker extensions must think in `Place`/`LoanInfo`, not string keys.** If the natural expression is a string key, the feature is not designed yet.
- **Per-type C++ knowledge belongs on `TypeDef`,** not in codegen `if`-chains.
- **Keep the Phase-1 / Phase-2 split clean** and Phase-1 dependent on signatures only, not other modules' bodies.

**Watch for algorithmic cliffs.** The worst front-end regressions come from accidentally quadratic scaling in size-proportional places -- overload resolution, protocol conformance checking, mutation propagation. Invisible on small codebases, brutal on large ones. Changes touching these areas should consider worst-case size scaling even when benchmarks on the existing test corpus look fine.

## THIR migration (ACTIVE -- delete this section when complete)

Migrating C++ codegen from the AST path to THIR (`tpyc/thir/`). End state: THIR is the single sema->codegen boundary and the AST codegen is DELETED. A permanent hybrid is not an acceptable outcome -- it doubles the maintenance surface forever.

Fallback is per-BODY and all-or-nothing: a body THIR cannot lower emits through the AST path instead. So the AST body emitter stays complete until one atomic cutover at the end; do not delete AST arms piecemeal, and never mix AST and THIR within one body.

**Metric: cases cleared.** `tpy| thir cases: N/M migrated` is the dial. Do NOT set fallback-UNIT targets -- units reward cheap small tags over the constructs that actually dominate the work. Shape % is asymptotic by design; ignore it.

**The loop.** `probe_corpus.py` builds the per-case blocker map; `probe_sites.py "<tag>"` histograms where in `tpyc/thir/lower/` that tag's cases actually reject. Pick the biggest site, grind every case that rejects there to zero, flip the newly-clean cases (`--thir-check-flip`, delete their `no_thir.txt`), verify, repeat. `/tpy-thir-wave` is this loop as an executable procedure with the probe scripts.

**Anti-patterns:**
- Attack the biggest tags. Clearing the corpus is a SET COVER over the blocker constructs, and a handful of the largest cover a quarter of it -- the small tags are a long 1:1 tail that no selection strategy shortens. But open a big tag as a multi-cell TRACK against a single raise SITE, never as one cell: it is a stack of independent render rows. (Which tags are currently largest is a measurement, not a rule -- it lives in TODO.md.)
- Never reject a family by sampling its cases -- raise sites interleave, so any small sample reads as unrelated shapes. Run `probe_sites.py`.
- Reject tags are LOSSY: they name the first reject, not the blocker. Read the blocking case's source + its `expected/*.cpp` oracle before committing to a target.
- Measure once, then grind. A full-corpus run is ~15 min; two per session is the budget.

**The contract:**
- **AST is the oracle.** A plain `uv run pytest` emits every case through the AST path (that C++ feeds exec and is byte-diffed against the snapshot) AND regenerates every case through THIR, byte-diffing that against the same snapshot. THIR is off under `--update-snapshots` and `--no-thir`.
- **The marker is a promise.** `no_thir.txt` exempts a case from the RATCHET (never from the byte-diff); an UNMARKED case that falls back any user body FAILS the comp phase. Byte-diff catches divergence, the ratchet catches fallback -- neither catches the other, because a fallback emits byte-identical AST.
- **Scoped to USER modules.** `lib/tpy` + stdlib stay on the AST path, so a case migrates on its own code, not its imports'.
- **If the AST makes a byte-identical mirror awkward, fix the AST FIRST**, as its own snapshot-churning change. Mirroring an AST accident plants a permanent wart; silently "fixing" a broken oracle IS the divergence. AST and THIR changes never share a change-set.
- **No admission preflights.** Lowering either generates or raises `ThirUnsupported` with a detailed reason, caught per body at the four entry points. New support goes in the lowering arm.
- **Every new arm gets three units:** a routing pin (face witnessed), a byte-identity pin, and a boundary pin for the adjacent shape that must keep rejecting. Adversarial `dualgen.py` around the boundary is the only detector for an admitted-but-unwitnessed shape.

**Docs:** `docs/IR_DESIGN.md` (design), `docs/THIR_COMPLETION_LEDGER.md` (what landed, per-wave history, lessons), `docs/THIR_EMIT_INVENTORY.md` (per-construct porting reference).

## Concept pointers

Features this doc references or assumes, with one-line explanations and deeper-dive pointers:

- **`@native` interop** -- decorator system for declaring C++ functions/types/globals visible to TPy (used by stdlib bindings and user interop). Tests using it require hand-written C++ companion files (`src/native_types.{hpp,cpp}`) and often need `no_cpython.txt`. See `docs/NATIVE_INTEROP.md`; examples in `tests/cases/native/`.
- **Compile-time macros** (`# tpy: macro_module`) -- modules that run under CPython at compile time to generate/transform AST. Four kinds: class macros (`@class_macro` modifying the decorated class, e.g. `@dataclass`, JSON `@model`), call-site macros (`@call_macro` rewriting one expression, e.g. `asdict()` / `astuple()`), builder-trace macros (`@builder_macro` walking a builder pattern at compile time, e.g. `argparse.ArgumentParser`), and function macros (`@function_macro` rewriting a whole free-function or record-method body, e.g. local-variable type deduction). Public API in `tpyc/macro_api.py`, loader in `tpyc/macro_loader.py`, builder-trace expander in `tpyc/sema/builder_trace.py`, function-macro phase in `tpyc/sema/function_macros.py`. See `docs/MACRO_DESIGN.md`; examples in `tests/cases/macros/` and `tests/cases/argparse/`.
- **`@error_return`** -- zero-cost error handling via `std::expected`. Part of a two-tier exception model: ordinary `try`/`except` for throw/catch, `@error_return` for hot paths that want explicit propagation. See `docs/ERROR_RETURN_DESIGN.md` + `docs/EXCEPTION_DESIGN.md`; examples in `tests/cases/error_return/` and `tests/cases/exceptions/`.
- **Readonly system** -- `readonly[T]` type modifier, `@readonly` method decorator (marks methods that don't mutate `self`), and `@auto_readonly` for methods whose return const-ness tracks the receiver (cloned during sema method expansion into a mutable + const pair). See `docs/READONLY_DESIGN.md`; examples in `tests/cases/readonly/`.
- **Protocols** -- structural protocols (duck-typed at compile time, monomorphized per concrete type) vs `@dynamic` protocols (runtime polymorphism via `Adapter`/`RefAdapter` wrappers). See `docs/PROTOCOL_DESIGN.md` + `docs/DYNAMIC_PROTOCOL_DESIGN.md`; examples in `tests/cases/protocols/`.
- **Flow-sensitive analysis** -- `sema/flow_facts.py` snapshots per-branch state (assignment, termination, narrowing, consumed vars) as immutable values to merge at join points; `sema/narrowing.py` handles Optional/None narrowing; `sema/value_range.py` tracks integer `[lo, hi]` ranges used for bounds-check and div-zero elision.
- **Borrow form / storage form** -- non-value types have two C++ shapes: *storage form* at fields / container elements / `Own[T]` slots / returns (self-contained value, e.g. `std::optional<T>`, `std::variant<A, B>`), and *borrow form* at function params / locals / iterator yields (indirect reference, e.g. `T*`, `std::variant<A*, B*>`, `T&`). Codegen emits per-element conversion helpers at boundaries (`tpy::optional_to_ptr` / `ptr_to_optional`, `to_ptr_variant`, `tuple_to_storage` / `tuple_to_pointer`). The duality is the source of a recurring bug class; see `docs/LANGUAGE_FEATURES.md` "Borrow Form vs Storage Form" for the canonical introduction, and `docs/IR_DESIGN.md` Open Questions item 9 for the planned IR-level resolution.
- **async/await** -- `async def`/`await` lower to a state-machine struct with `poll(waker) -> Poll[T]` via a "resumable-frame" abstraction in codegen (not C++20 coroutines). `await` works in arbitrary control flow (if/while/for/try/except/with/finally) via a localized CFG in `tpyc/codegen_cpp/resumable_cfg.py`. The same resumable frame is shape-neutral: non-simple generators (`yield`) share it (the simple-generator lambda peephole stays separate), so `yield` and `await` lowering are unified. Cancellation is exception-based (`CancelledError` thrown at next suspension). The asyncio runtime (`run`/`sleep`/`create_task`/`Task`/`Future`/`Event`) is mostly TPy code in `lib/tpy/asyncio/` on a small C++ executor bridge. See `docs/ASYNC_DESIGN.md` for the design and `docs/ASYNC_PROGRESS.md` for milestone history; examples in `tests/cases/async/`.

## Key Documentation Files

| File | Purpose | Policy |
|------|---------|--------|
| `docs/LANGUAGE_FEATURES.md` | Comprehensive language feature documentation + type mapping | **Keep up-to-date** with any development |
| `docs/STDLIB_ROADMAP.md` | Python stdlib coverage tracker (per-module items, status, blockers) | **Keep up-to-date** when adding/changing stdlib modules |
| `docs/ARCHITECTURE.md` | Compiler architecture: nominal/structural types, TypeDef registry, sema layout, perf tradeoffs | Update when type-system or sema structure changes |
| `CLAUDE.md` | Commands, architecture overview, agent rules | Update when adding major features |
| `README.md` | Quick start, build flags | Update when CLI changes |
| `TODO.md` | Current priorities | Check before starting non-trivial work |
| `RELEASE_PLAN.md` | Milestone slice of TODO.md: queued features for the next release. Bugs are NOT listed -- every `IMM`/`HIGH` entry in BUGS.md blocks the release, so the tags are the must-fix set | Short bullets only, each linking into TODO.md via a quoted greppable phrase; delete a release's section when it ships |
| `BUGS.md` | Known compiler defects (incorrect output, crashes, miscompiles, rejection of valid code, missing safety diagnostics). Has a `## Compiler bugs` section and a `## Safety / borrow checker` section -- file borrow-checker / view-lifetime gaps in the latter | Add new bug entries here, not in TODO.md |

**Before committing**: If code adds new features or changes behavior, update `docs/LANGUAGE_FEATURES.md` in the same commit to reflect the current state (Working/Planned/Open status).
