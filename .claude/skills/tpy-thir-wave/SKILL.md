---
name: tpy-thir-wave
description: Run one autonomous THIR migration wave -- pick sole-blocker families from a fresh per-case map, widen lowering arms with byte-diff verification, harvest and flip cases toward a flip target, batch-review, and deliver a merge-ready branch. Invoke when the user wants THIR migration pushed forward by N cases without steering each cell.
---

# /tpy-thir-wave

Run one THIR migration wave end-to-end: baseline -> grind cells -> harvest
flips -> verify -> review -> wrap. The wave's operating doctrine (metric
philosophy, anti-patterns, the ratchet/overlay model) lives in CLAUDE.md's
"THIR migration" section -- read it first and do NOT re-derive it; this
skill is the executable procedure layered on top.

## Arguments

Optional flip target. Defaults to `40-50`.

- `/tpy-thir-wave` -- push through 40-50 case flips
- `/tpy-thir-wave 20` -- a smaller wave (e.g. limited time)

## The goal (built into this skill)

Invoking this skill IS the standing directive to keep working until one of
these holds -- do not pause to ask "should I continue?" between cells:

- **Target met**: the flip target is reached (dial delta vs the wave's
  start), the suite is green with exec, and the review batch has run with
  findings applied.
- **Blocked**: every remaining verified candidate family is
  design-territory, architectural, or on the avoid-list -- report the
  frontier state and stop.
- **Design fork**: a cell turns out to need a new concept, a behavior
  split, or an unforeseen-constraint workaround -- per CLAUDE.md, STOP and
  present it; do not improvise. Switching to a different frontier family
  instead is always allowed and preferred when confidence is low.

Everything lands on ONE working branch (see memory: unmerged cell work
stacks; never branch a new cell off master mid-wave). For hook-level
enforcement the user can wrap the invocation as
`/goal /tpy-thir-wave <target>` -- the skill behaves identically either
way.

## Bundled probe scripts

All in `scripts/` next to this file; run from the repo root; all write
scratch under `/tmp/agents/thir-wave/`:

| Script | Job |
|---|---|
| `probe_corpus.py` | Full-corpus per-case blocker map (`blockers.json`) -- the sole-blocker ranking that drives cell selection. CPU-heavy; run once per baseline and re-run only when the map is meaningfully stale (>~30 flips old). |
| `probe_one.py` | Single-case probe: fallback reasons + snapshot byte-diff in one shot. The per-edit smoke -- a flip candidate must print both CLEAN and IDENTICAL. |
| `probe_loc.py` | Reject spy: every ThirUnsupported raised, with reason + source line. Use to decode lossy tags and find the NEXT shadowed blocker. |
| `dualgen.py` | THIR-vs-AST dual generation + diff over a scratch program. The honest smoke for admitted-but-unwitnessed shapes -- use it on adversarial inputs around every new arm's boundary. |

## Steps

### 0. Preflight

- Clean tree; current branch is master-equivalent or a fresh wave branch.
  Create `thir-<theme>-wave` off master if starting fresh.
- CPU check: `pgrep -f pytest` -- never start a run while one is running.
- Record the starting dial (the `tpy| thir cases:` line from the last
  green run, or from the baseline run below).

### 1. Baseline

Run the metrics pass LOCALLY (the JSON dumps write on the run host, so not
rpytest), in the background:

```bash
THIR_ARM_RESIDUAL_JSON=/tmp/agents/thir-wave/residual.json \
THIR_FALLBACK_JSON=/tmp/agents/thir-wave/fallback.json \
uv run pytest --thir-codegen --no-exec -q
```

While it runs, launch `probe_corpus.py` ONLY IF no fresh `blockers.json`
exists -- otherwise wait; the two runs must not overlap. Confirm the
baseline is green and note the zero-witness faces line (new faces from the
previous wave should have corpus witnesses by now; flag any that do not).

### 2. Grind loop (repeat per cell until the target)

1. **Select** the most UNIFORM sole-blocker family by CASE count from
   `blockers.json` -- not the biggest bucket (`expr.call` /
   `expr.method_call` / `decl.slot_type` are fragmenting elephants;
   CLAUDE.md anti-pattern (a)).
2. **Verify before building** -- the step this wave-model exists to
   enforce:
   - Open the actual blocking case source AND its `expected/*.cpp` oracle
     hunk (tags are lossy; CLAUDE.md anti-pattern (e)).
   - `probe_loc.py` the witnesses to get the true reason + line.
   - **Read the AST-side dispatch the new arm will mirror** (the emit
     function in `tpyc/codegen_cpp/` -- e.g. a peephole split, a target
     threading, a temp hoist) and **grep the THIR test files for existing
     pins on that boundary** (`still_defers`, `stays_ast`). An existing
     pin encodes a known render split; widening past it without
     understanding WHY it exists is this model's proven failure mode.
   - If the family fragments into 3+ distinct render shapes, drop it
     silently and take the next candidate.
3. **Implement** the arm; register any new face in `tpyc/thir/faces.py`.
4. **Smoke**: `probe_one.py` on every witness (CLEAN + IDENTICAL), then
   `dualgen.py` on adversarial scratch inputs around the boundary
   (slot-threaded vs target-less positions, Own/Optional wrappers,
   negation, non-literal sources). A shape that only stays identical via
   FALLBACK has not been routed -- check the fallback line.
5. **Chain-walk to zero**: first-reject shadowing means fixing one blocker
   reveals the next; keep drilling the same witnesses until CLEAN. A case
   whose chain crosses into another lane (generics, resumables, the
   avoid-list) is parked, not forced.
6. **Pin**: every new arm gets three units in the wave's test file --
   a routing pin (`_fn(...) is not None` + witness via
   `_lower_ctx_witnessed`), a byte-identity pin
   (`_assert_byte_identical`), and a BOUNDARY pin (the adjacent shape
   that must keep falling back).
7. **Commit the cell** (branch policy: auto-commit on the wave branch,
   concise message with the arms + verified witnesses).

### 3. Harvest (after each 2-4 cell batch, not per cell)

- `uv run pytest --thir-check-flip --no-exec -q > /tmp/agents/thir-wave/checkflip.txt 2>&1`
  -- redirect the FULL output to a file; never pipe through `tail` (a
  truncated candidate list caused a missed flip and a false
  "provably complete" claim in the 2026-07-22 wave).
- Remove `no_thir.txt` for every listed candidate, then ratchet-verify
  with a targeted `uv run pytest --no-exec -k <names>` -- all must report
  migrated/zero-fallback.
- Commit the flips with the dial delta in the subject.
- Expect bonus flips beyond the probed targets every time; that is the
  point of harvesting.

### 4. Verify

- Full THIR units: `uv run pytest tpyc/thir/ -q` (background; ~10 min).
- Full suite WITH exec via `rpytest -q` (fall back to local
  `uv run pytest` only if rpytest is unavailable). The official dial line
  in this run's summary is the number the wave reports.
- Background-run etiquette: long runs go through run_in_background; wait
  for the completion notification -- never poll with sleep/watcher loops.

### 5. Review batch (autonomous within the wave)

- Run `/tpy-review`; apply routine findings (Warnings + cheap
  Suggestions) without prompting, as ONE review-round commit; file
  deferred items in BUGS.md/TODO.md. Stop only for design forks.
- File pre-existing bugs the review surfaces (CLAUDE.md: report, don't
  ignore) -- check BUGS.md for an existing entry first.

### 6. Wrap

- Refresh TODO.md's relevant frontier entry (landed residue, newly parked
  rungs with their verified blockers) and add a wave entry to
  `docs/THIR_COMPLETION_LEDGER.md` -- in-branch, not deferred.
- Run ONE final `--thir-check-flip` if any lowering changed after the last
  harvest (the flip-completeness rule -- the ratchet proves no wrong flip,
  NOT that none was missed).
- Update session memory: dial, branch/commits, landed arms, parked
  residuals, lessons.
- Report the wave: dial delta, cells, flips, review outcome. Then stop --
  `/tpy-merge-master` -> `/tpy-ready` -> `/prep-merge` are separate
  user-sequenced gates, and the merge into master is ALWAYS the user's.

## Hard rules (each one paid for in a past wave)

- Mirror the AST dispatch EXACTLY when widening a route; the corpus
  byte-diff catches witnessed divergence, but only pins catch the rest.
- A gate change must never silently convert "reject" into "route" for the
  un-flagged slice of a position-gated arm -- re-check the default path
  after adding an opt-in flag.
- Opt-in escapes (`allow_unrouted_name`, `field_prechecked`-style
  bypasses) are position-scoped: audit what safety checks the bypass
  skips, and boundary-pin the general path still rejecting.
- Never `update_snapshots.py` during a wave -- snapshots stay AST-authored;
  a snapshot diff means investigate, not re-baseline.
- One test run at a time; kill nothing you did not start.
