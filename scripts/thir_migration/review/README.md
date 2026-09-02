# THIR cutover review instruments (2026-09-01)

Artifacts and scripts behind `docs/THIR_CUTOVER_REVIEW.md`. Every figure in
that report was measured at tree `c8c9268ac`; re-run rather than reuse.

Scripts (run from the repo root with `uv run python`):

- `inventory_sites.py OUT.json` -- every `ThirUnsupported(...)` construction
  site: file, line, enclosing function, reason literal.
- `probe_fallback.py FILE.py [--default-int X]` -- one program through both
  codegen paths; prints the AST and THIR outcomes, the fallback dict and a
  verdict (`ROUTES` / `BREAKS_AT_CUTOVER` / `BOTH_REFUSE` /
  `THIR_RAISES_PLAIN`). Needs both emitters, so it dies with the AST path.
- `classify_tests.py [OUT.json]` -- static claim classification of every
  THIR unit test (routes / lowers / identity / reject / render / node /
  fallback / other) plus duplicated embedded programs. Approximate: per
  test, and a reject pin written through the witnessed helpers reads as a
  routing claim.
- `probe_site.py FILE.py` -- `probe_fallback` plus the `file:line` of every
  `ThirUnsupported` construction, for sites that share a bare tag.
- `probe_programs.py OUTDIR JOBS` -- runs every distinct program embedded
  in the THIR unit tests through `probe_fallback`'s logic in parallel.
  Population caveat: only whole-program string constants compile; strings
  composed at runtime from fixtures are reported as `FRONTEND_REFUSES`.

Both probe tools need both emitters and die with the AST path at the
deletion commit; `classify_tests.py` and `shapes_unit.py` survive.

**The post-cutover fix queue** is the BREAKS rows of `bins_*.json` (each
with its reproducer under `probes/<slice>/`) plus the `BREAKS_AT_CUTOVER`
entries of `program_verdicts.json`.

Data:

- `sites.json` -- the 655-site inventory.
- `bins_<slice>.json` (nine slices, 655 sites) -- per-site bins (BREAKS /
  DEAD / REFUSAL / INTERNAL / UNSURE) with evidence and, for BREAKS and
  REFUSAL, the probe program under `probes/<slice>/` (probes for DEAD and
  UNSURE sites were not kept). `probes/mine/` are the report author's
  re-verification probes.
- `reach.json` / `reach_census.log` -- `thir_reject_reach.py --population both`.
- `asym_run.log` -- the asymmetry probe at three widths (control fails at
  Int64/BigInt for one front-end-refused case; valid at Int32).
- `shapes_unit.py`, `shapes_unit.json`, `shapes_corpus.json` -- body-shape
  coverage of the routing unit programs against a corpus `THIR_SHAPES_JSON`
  dump (a local `--no-exec --thir-codegen` run).
- `p2_*.json` -- the seven phase 2 layer reviews' findings.
- `test_claims.json`, `program_verdicts.json` -- the phase 3 instruments'
  outputs.
- `BRIEF_*.md` -- the briefs the review agents worked from.
