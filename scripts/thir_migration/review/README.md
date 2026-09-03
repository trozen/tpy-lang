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
- `BRIEF_*.md` -- the briefs the review agents worked from.
