# THIR cutover review instruments (2026-09-01)

Artifacts and scripts behind `docs/THIR_CUTOVER_REVIEW.md`. Every figure in
that report was measured at tree `c8c9268ac`; re-run rather than reuse.

**This directory is the post-cutover fix queue.** The AST body emitters are
deleted, so every site binned `BREAKS` below names a shape that now fails to
compile with a `ThirRejectError`. The queue is the `BREAKS` rows of
`bins_*.json` (each with its reproducer under `probes/<slice>/`) plus the
`BREAKS_AT_CUTOVER` entries of `program_verdicts.json`. A row closes when a
lowering arm in `tpyc/thir/lower/` handles the shape and carries its three
units (routing pin, render pin, boundary pin).

Scripts (run from the repo root with `uv run python`):

- `inventory_sites.py OUT.json` -- every `ThirUnsupported(...)` construction
  site: file, line, enclosing function, reason literal.
- `classify_tests.py [OUT.json]` -- static claim classification of every
  THIR unit test (routes / lowers / identity / reject / render / node /
  fallback / other) plus duplicated embedded programs. Approximate: per
  test, and a reject pin written through the witnessed helpers reads as a
  routing claim.
- `shapes_unit.py` -- body-shape coverage of the routing unit programs
  (`tpyc/thir/shape.py` survives the deletion, so this still runs).
- `reprobe.py OUT_DIR` -- **the queue's re-measurement.** Runs every
  BREAKS probe through the front end on the current tree and classifies it
  (REJECTS with the live tag / COMPILES / FRONTEND_ERROR / CRASH), joined
  with its bin row into `OUT_DIR/results.json`. Run it before choosing a
  batch; the bins are a one-time binning and rows close as arms land. Its
  output is not committed (40 seconds to regenerate); the figures below are
  the 2026-09-03 run.
- **The render oracle.** The deleted AST body emitter still exists at
  commit `e5e9274af` (an ancestor of master: the flip commit, where THIR
  authors by default and the AST is the fallback). `git worktree add
  <path> e5e9274af`, then `uv run tpy --dump-code file.py` THERE renders a
  rejecting body through the old emitter. A new lowering arm falls in one
  of four classes against it: (1) byte mirror -- the default, diff the
  comment-stripped `.cpp`/`.hpp` of every new case against the oracle
  before review; (2) the oracle render is ill-formed or dangling -- keep a
  located reject and file the shape in BUGS.md, never mirror it; (3) no
  oracle -- the AST crashed on the shape, so the exec run, CPython parity
  and the unit pins are the only check, and a sink matrix (local / return
  / field / method-arg / container element) is mandatory for a value-form
  row; (4) a valid oracle render deliberately changed -- say so in the
  commit and the case. Name the shared tables and predicates a row must
  reuse in the implementer brief (the op->dunder table, the runtime-view
  predicate, the registry's conversion spellings): parallel implementers
  otherwise grow private copies.
- `probe_fallback.py`, `probe_site.py`, `probe_programs.py` -- **RETIRED.**
  Each emitted one program through both codegen paths and compared the
  outcomes, so all three stopped working when the AST body emitters were
  deleted. Kept unrun as the record of how the verdicts below were
  produced.

Data:

- `sites.json` -- the 655-site inventory.
- `bins_<slice>.json` (nine slices, 655 sites) -- per-site bins (BREAKS /
  DEAD / REFUSAL / INTERNAL / UNSURE) with evidence and, for BREAKS and
  REFUSAL, the probe program under `probes/<slice>/` (probes for DEAD and
  UNSURE sites were not kept). `probes/mine/` are the report author's
  re-verification probes.
- `reach.json` / `reach_census.log` -- the reject-reachability census over
  both populations, from a sweep tool deleted with the migration scripts.
- `asym_run.log` -- the asymmetry probe at three widths (control fails at
  Int64/BigInt for one front-end-refused case; valid at Int32).
- `shapes_unit.json`, `shapes_corpus.json` -- body-shape coverage of the
  routing unit programs against a whole-corpus shape dump taken before the
  cutover.
- `p2_*.json` -- the seven phase 2 layer reviews' findings.
- `test_claims.json`, `program_verdicts.json` -- the phase 3 instruments'
  outputs.
- Re-probe figures go stale within a batch -- run `reprobe.py` rather than
  reading a number here. For the record: at master `da1a92ee15` (before
  batch 1) 388 of the 402 BREAKS rows rejected over 260 distinct live tags
  (252 of the 299 tag x site groups size one); batch 1 closed ten shapes,
  batch 2 (2026-09-04) 57 rows, batch 3 (2026-09-04) the nine failing NAMED
  programs (`examples/**` and `../tpy-examples`), eight of which compile now.
  Selection rule since batch 3: a failing named program outranks any tag
  count; probe them first.
- `BRIEF_*.md` -- the briefs the review agents worked from.
