---
name: tpy-thir-wave
description: Grind one THIR raise site to zero -- histogram where a blocker tag actually rejects, widen the lowering arm for the biggest site, flip the newly-clean cases, verify. Invoke when the user wants THIR migration pushed forward.
---

# /tpy-thir-wave

Grind ONE raise site to zero. The doctrine (metric, contract, anti-patterns)
is CLAUDE.md's "THIR migration" section -- read it, do not re-derive it.

## Argument

A blocker tag (`body:expr.call`) or a specific site (`expressions.py:6982`).
No argument: pick the biggest tag from the fallback tally itself -- reject reasons now compose their blocking shape, so the tally names the shape without a probe. Fall back to `probe_sites.py` only for a tag whose reason is still bare.

## Standing directive

Keep grinding cases at the chosen site until none rejects there, then stop.
Do not pause between cells to ask. Do not set a unit target. Stop early only
for a design fork (a new concept or behavior split) -- present it, don't
improvise.

## Scripts (`scripts/`, run from the repo root)

| Script | Job |
|---|---|
| `probe_corpus.py` | per-case blocker map -> `blockers.json`. ~5 min. Once per session. |
| `probe_sites.py "<tag>"` | **where** that tag's sole-blocker cases actually reject, ranked. ~4 min. FALLBACK instrument: it exists because `expr.call` / `expr.method_call` carried no detail, and they now do. Reach for it only when a reason is bare. |
| `sites_multi.py [prefix]` | per-case raise-site SETS over every marked case (soles AND multis) + a greedy set-cover ranking. The paying view when cases have several blockers; `probe_sites.py` only sees sole-blockers. |
| `probe_one.py <case>...` | per-case fallback reasons + snapshot byte-diff. The per-edit smoke: a flip candidate prints CLEAN and IDENTICAL. |
| `probe_loc.py <case>...` | every `ThirUnsupported` raised, with reason + source line. Decodes a lossy tag. |
| `dualgen.py <file.py>` | THIR-vs-AST diff over a scratch program. The only check for an admitted shape with no corpus witness. |

## The instruments lie in four known ways

Each was found mid-session by an agent measuring something it had been told.
Two of them read a REAL flip as a fallback, so a wave that trusts them
discards its own work.

1. **`Compiler._thir_routed_bodies` cannot see resumable frames or simple
   generators.** It sums `thir_functions + thir_constructors` only
   (`compiler.py`), so a generator/async flip leaves the count UNCHANGED.
   Wrap `lower_resumable` / `lower_simple_generator` and report
   `res_routed=N/M` instead. When you patch `lower_simple_generator`, patch it
   at BOTH the submodule and the PACKAGE attribute -- the call site imports
   from the package at call time, so patching only the submodule silently
   reads `0/0`.
2. **The census cannot see resumable frame-gate rejects at all.**
   `resumable.py`'s `_reject` does `note(reason); return None` and never
   constructs a `ThirUnsupported`, which is what `sites_multi.py` hooks. Every
   generator/async case's site set is a LOWER BOUND missing a whole class.
3. **`move_audit` is structurally blind at copy/alias-position arms and at the
   ctor MIL** (`joined=0` / the join count does not move when those bodies
   start routing) -- the AST asks no move question there, so the join
   denominator is empty and it reports green regardless. Never cite it at
   either. (It is not a coverage HOLE at the MIL: a wrong move/copy verdict
   still changes emitted text, which the byte-diff catches.)
4. **A byte-diff harness must be self-checking.** One scout's harness mapped
   `<out>/<case>/main.d/{include,src}/...` onto `expected/main.d/...`, found
   no counterpart, silently skipped, and printed IDENTICAL having compared
   ZERO files -- voiding its whole brief. Strip the `.d` component and print
   an explicit `cmp=N`.

## Picking work

- **Rank by ABLATION, never by census count.** The sole-site label is a
  HYPOTHESIS: lowering aborts at the first `ThirUnsupported`, so a second
  blocker in the SAME STATEMENT is invisible, and some "sites" are one shared
  raise for a whole dispatch ladder. It was wrong nine times in one session.
- **The three largest sites by touched-case count were proven ZERO-flip dead
  ends.** Size does not predict yield; paired small sites do.
- **Cost labels from triage are systematically too pessimistic.** Six of
  eleven cases triaged as "needs a new render arm" turned out SMALL and
  proven to flip alone, all reusing existing nodes. Probe before pronouncing.
- **Look for the already-landed SIBLING row before designing anything.** Nine
  rows in one session were verbatim siblings one sink over. No histogram
  surfaces those; only reading the sibling gate does.
- **Stub the rejecting node to see past it.** Replacing it with a dummy
  answers "is anything hiding behind this?" in one run (output is garbage by
  construction -- it measures remaining work, it is not a flip claim).

## Loop

1. **Pick the site.** Read it off the composed reason first; `probe_sites.py "<tag>"` only if the reason is bare. Take the top row. Open two or
   three of its cases AND their `expected/*.cpp` oracle before writing code.
2. **Read the AST arm you are mirroring**, and grep the THIR tests for pins on
   that boundary. Grep by the construct/type SHAPE (the source pattern, the
   payload type, the callee name), NOT just the boundary-marker tags
   (`stays_ast`, `still_defers`, `ineligible`) -- fences live under names
   like `own_value_local` / `self_subject` / `field_element` that tag-greps
   miss (three late catches in one session). Include corpus cases
   the widened gate could newly capture. A pin encodes a known render split
   -- if its stated reason is what your change removes, convert it; if not,
   respect it. **Re-run every pin whose reason your widening touches BEFORE
   the corpus run** -- three de-routings in one wave (a delegation
   over-capturing a routed shape, two stale fence pins) were each caught a
   full corpus/suite run later than this check would have.
3. **Widen the arm.** Register any new face in `tpyc/thir/faces.py`.
4. **Smoke:** `probe_one.py` every case at the site, then `dualgen.py` on
   adversarial inputs around the new boundary. Byte-identity via FALLBACK is
   not routing -- check the fallback line, with the RIGHT counter for the
   shape (see "The instruments lie"). **Disable-and-dualgen every NEW
   admission leg before the cell commit**: temporarily disable the leg and
   re-probe the drilled case -- if it still routes byte-identically, the leg
   is dead code; drop it (a dead for-head leg once survived to the next
   review round because only the drilled case was probed). **Disable by
   RE-EDITING, never by `git checkout <path>`** -- that has wiped uncommitted
   cell work twice. And before dropping a leg that looks dead, try to build a
   DISCRIMINATING case: one implementer did and found the leg load-bearing,
   saving a real safety property from deletion.
4b. **BLAST-SWEEP 3-4 neighbour cases sharing the mechanism.** This -- not the
   flip proof -- is what validates a gate's KEY. Three rows in one session
   were byte-identical on their OWN flip case with a WRONG key, each exposed
   only by a neighbour (one turned a record alias into a copy; one was placed
   in a predicate shared with the free-call loop, which hoists a temp where
   the ctor passes bare).
5. **Chain-walk.** Clearing one blocker promotes the next; keep going on the
   same cases until CLEAN or the chain leaves this site.
6. **Pin:** routing + byte-identity + boundary units for every new arm.
   Every dualgen boundary probe becomes a COMMITTED pin before the cell
   commit -- probe-only boundary evidence has slipped through three review
   rounds in a row; the probe file is the pin's draft, convert it.
   A routing pin must assert routing MECHANICALLY (`_assert_routes_byte_identical`,
   THIR-node assertions, or an EXACT fallback-dict check). Plain
   `_assert_byte_identical`, render-string assertions and PREFIX-FILTERED
   fallback checks are all satisfied by a whole-body fallback -- one pin
   filtered `resumable:`-prefixed keys and let a `body:` key through, the exact
   shape its own boundary sibling asserted. Pin the WHOLE dict, or the EXACT
   dict when the fixture legitimately carries a deliberate boundary reject.
   **Confirm a boundary shape is CONSTRUCTIBLE before pinning it** -- sema
   rejects more shapes than you expect, and an unfalsifiable pin cannot fail.
6b. **Name an owner for every SHARED file before the lanes start, and say so
   in each brief.** Partitioning lanes BY FILE leaves the append-only shared
   files -- `faces.py` above all -- owned by nobody, and this has now cost
   three waves: `git commit --only <path>` does not protect them (it commits
   the path's working-tree state, peer edits included), so the lane that
   finishes second either commits a peer's half-written line or drops its own.
   Either serialise that one file or hold every registration for
   consolidation. Restating the lesson in a ledger entry is not the fix; the
   fix is the assignment in the brief.
6c. **Carry the written rules INTO the lane brief.** An implementer that never
   read this file cannot follow it. The two that get broken are the
   `git checkout <path>` ban in step 4 and 6b above -- quote them, do not
   assume them.

7. **Commit the cell.** Auto-commit on the working branch, ONE COMMIT PER CELL
   as soon as it verifies -- an agent died mid-batch and per-cell commits are
   what made the work recoverable. Before the commit: `grep -rn DBG tpyc/`
   must be empty (two committed spy prints each cost a review-round catch).

Repeat 3-7 until the site is empty.

## Two rules that outrank a green suite

- **If the AST's render is an ACCIDENT, do not mirror it -- STOP.** One case
  looked like a proven SMALL flip; its oracle carried a frame member that was
  never referenced, shadowed by a same-named local. Mirroring would have
  planted the accident permanently. It turned out to be the benign face of a
  HIGH wrong-code family. Read the oracle, not just the brief.
- **The fence sweep is NOT a substitute for the full suite.** Three times in
  one session a pin needing conversion was found only by the suite -- once a
  compiler test using a cell's exact shape as its "un-migrated" fixture.
  Shape-grepping the obviously-related test files missed all three.

## Finish

- `--thir-check-flip --no-exec`, delete the listed `no_thir.txt`, ratchet-verify
  with `pytest --no-exec -k <names>`, commit the flips.
- Full suite with exec (`rpytest -q`). Re-run `--thir-check-flip` if lowering
  changed after the last harvest -- the ratchet proves no WRONG flip, not that
  none was missed.
- `/tpy-review`, apply routine findings as ONE commit.
- Add a wave entry to `docs/THIR_COMPLETION_LEDGER.md`.
- Report: site, cases cleared, dial delta. The merge is always the user's.

## Hard rules (each paid for)

- Mirror the AST dispatch exactly; the corpus byte-diff catches witnessed
  divergence, only pins catch the rest.
- A gate change must not silently turn "reject" into "route" for the unflagged
  slice of a position-gated arm.
- Never `update_snapshots.py` during a wave -- a snapshot diff means investigate.
- One test run at a time; kill nothing you did not start.
