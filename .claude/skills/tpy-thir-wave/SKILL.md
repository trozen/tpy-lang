---
name: tpy-thir-wave
description: Grind one THIR raise site to zero -- histogram where a blocker tag actually rejects, widen the lowering arm for the biggest site, flip the newly-clean cases, verify. Invoke when the user wants THIR migration pushed forward.
---

# /tpy-thir-wave

Grind ONE raise site to zero. The doctrine (metric, contract, anti-patterns)
is CLAUDE.md's "THIR migration" section -- read it, do not re-derive it.

## Argument

A blocker tag (`body:expr.call`) or a specific site (`expressions.py:6982`).
No argument: pick the biggest tag from the last `probe_sites.py` run.

## Standing directive

Keep grinding cases at the chosen site until none rejects there, then stop.
Do not pause between cells to ask. Do not set a unit target. Stop early only
for a design fork (a new concept or behavior split) -- present it, don't
improvise.

## Scripts (`scripts/`, run from the repo root)

| Script | Job |
|---|---|
| `probe_corpus.py` | per-case blocker map -> `blockers.json`. ~5 min. Once per session. |
| `probe_sites.py "<tag>"` | **where** that tag's sole-blocker cases actually reject, ranked. ~4 min. This picks the work. |
| `probe_one.py <case>...` | per-case fallback reasons + snapshot byte-diff. The per-edit smoke: a flip candidate prints CLEAN and IDENTICAL. |
| `probe_loc.py <case>...` | every `ThirUnsupported` raised, with reason + source line. Decodes a lossy tag. |
| `dualgen.py <file.py>` | THIR-vs-AST diff over a scratch program. The only check for an admitted shape with no corpus witness. |

## Loop

1. **Pick the site.** `probe_sites.py "<tag>"`; take the top row. Open two or
   three of its cases AND their `expected/*.cpp` oracle before writing code.
2. **Read the AST arm you are mirroring**, and grep the THIR tests for pins on
   that boundary (`stays_ast`, `still_defers`, `ineligible`, and corpus cases
   the widened gate could newly capture). A pin encodes a known render split
   -- if its stated reason is what your change removes, convert it; if not,
   respect it. **Re-run every pin whose reason your widening touches BEFORE
   the corpus run** -- three de-routings in one wave (a delegation
   over-capturing a routed shape, two stale fence pins) were each caught a
   full corpus/suite run later than this check would have.
3. **Widen the arm.** Register any new face in `tpyc/thir/faces.py`.
4. **Smoke:** `probe_one.py` every case at the site, then `dualgen.py` on
   adversarial inputs around the new boundary. Byte-identity via FALLBACK is
   not routing -- check the fallback line. **Disable-and-dualgen every NEW
   admission leg before the cell commit**: temporarily disable the leg and
   re-probe the drilled case -- if it still routes byte-identically, the leg
   is dead code; drop it (a dead for-head leg once survived to the next
   review round because only the drilled case was probed).
5. **Chain-walk.** Clearing one blocker promotes the next; keep going on the
   same cases until CLEAN or the chain leaves this site.
6. **Pin:** routing + byte-identity + boundary units for every new arm.
   Every dualgen boundary probe becomes a COMMITTED pin before the cell
   commit -- probe-only boundary evidence has slipped through three review
   rounds in a row; the probe file is the pin's draft, convert it.
7. **Commit the cell.** Auto-commit on the working branch. Before the
   commit: `grep -rn DBG tpyc/` must be empty (two committed spy prints
   each cost a review-round catch).

Repeat 3-7 until the site is empty.

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
