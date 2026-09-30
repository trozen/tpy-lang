# MIR coverage measurement

Measures how far the MIR pipeline gets over real programs, per function body,
the way `tpyc --dump-mir` sees them: compile in-process (front-end + THIR
collection, no C++ build), lower every emitted body, run the analyses, and ask
the storage adapter for a certificate.

## The four-count

Each callable with a source body (bodyless bindings and overload stubs are
excluded) gets:

| count | meaning |
|-------|---------|
| lowered | `lower_function` / `lower_constructor` (or the call workspace) returned a `MIRFunction` |
| complete | every analysis `--dump-mir` runs (dependencies, scope ends/conflicts, payload ends/conflicts, storage, call effects, retention) returned without `MIRNotCovered` |
| conflict | an inspection reported a conflict (scope end, payload end, replacement, stale alias) or the certificate's verdict is CONFLICT |
| certified | `storage_adapter.certify_thir_storage` returned CERTIFIED and the certificate binds the request |

`certify_thir_storage` with `requires_proof == False` (the body has no
materialized storage and no borrowed-expression obligations) is its own
bucket, **no-proof**, and never counts as certified. `requires_proof is None`
(facts not published) is `no_facts`.

The report breaks the counts down overall, by position (free function, method,
constructor, dunder, property, generator, async, module init, ...), and over
**loan-active** bodies by loan kind. It also ranks the first blocker of each
body: the lowering reason category, else the first analysis gap, else the
storage gap.

## Running

```bash
uv run python scripts/mir_coverage/run.py                       # tests sample + stdlib (+ examples if found)
uv run python scripts/mir_coverage/run.py --corpus tests -j 3 --out /tmp/agents/mir-coverage/now
uv run python scripts/mir_coverage/run.py --corpus examples --examples ~/src/tpy-examples
uv run python scripts/mir_coverage/run.py --cases imports/import_basic records/dataclass_astuple
uv run python scripts/mir_coverage/run.py --out NEW --compare OLD   # blocker transitions per body
uv run python scripts/mir_coverage/run.py --out DIR --report-only   # re-analyze an existing run
```

- `--corpus`: `tests` (the sampled cases), `stdlib` (every library body,
  compiled through `tests/cases/harness/stdlib_render`), `examples` (the
  tpy-examples repo, from `--examples PATH` or `$TPY_EXAMPLES`), `all`.
- `-j`: parallel programs, default 3; above 4 needs `--allow-more-jobs`.
  Each program runs in its own forked child (POSIX only), so a crash or a
  `--timeout` costs one program, not the run.
- `--out` (default `$TMPDIR/agents/mir-coverage/latest`) receives
  `programs.jsonl` (raw per-program records), `bodies.csv` (one row per
  body), `summary.json` (the headline counts), `report.txt`.
- `--compare PREV_DIR` diffs first blockers body by body against
  `PREV_DIR/bodies.csv`. A previous CSV without the four-count columns is
  compared at the lowering stage only.
- `--resume` keeps the per-program records already in `--out/parts` and
  measures only the missing programs, for an interrupted run; without it
  those records are discarded first.
- `--no-diag-scan` skips the report's scan of every case's
  `expected/diag.txt` for lifetime-diagnostic families (it runs only for
  the `tests` / `all` corpora without `--cases`).

## The fixed denominator

The tests sample is a rule, not a file: a case is sampled when the hash of
its `tests/cases` path falls under `--fraction` (default 0.4), and a group
smaller than `--min-per-group` (default 8) is taken whole. The population is
the compiling cases (no `error_` / `panic_`; `harness/stdlib_render` is the
stdlib corpus). Keying on the name keeps membership stable as the corpus
grows: a new case joins with the same probability and moves nothing else, so
reruns compare on the same bodies, and `--compare` reports the cases that
appeared or vanished. The per-group totals weight the "group-weighted to the
full corpus" line. Changing the fraction resets comparability, so do it on
its own.

## Caveats

- **First blocker only.** Lowering stops at the first unsupported construct,
  so a body's reason hides whatever comes after it; unlocking one reason can
  expose another in the same body.
- **Loan activity comes from sema hooks** (`hooks.py` wraps
  `BorrowTracker.add_borrow`, view-variable allocation, param-derived and
  loop-variable provenance, `mark_param_returned`, loop-frame and with-exit
  holds, lambda ref captures) plus a lifetime diagnostic located in the body.
  A loan sema never registers is invisible, so "loan-inactive" means "no loan
  sema tracked", not "no loan".
- **Trial-scope events count.** Sema analyzes some expressions tentatively
  and discards the result; hooks fired inside such a trial still mark the body
  loan-active.
- Body kinds follow `--dump-mir` rather than correcting it. The workspace
  pass (`mir_workspace.analyze_call_workspace`) lowers every scheduled
  callable as a free function and `--dump-mir` reuses that result, so an
  instance method it scheduled is reported as "body kind and receiver
  mismatch". Every other callable with an owner -- dunders, properties and
  staticmethods included -- is lowered as a METHOD by `tpyc/mir/collect.py`,
  so a staticmethod (no receiver) reports the same mismatch and dunders and
  properties are measured as ordinary methods. The `position` column still
  names each kind.
- Blocker TYPE names come from a spy on the lowering's `require` and are a
  best-effort label, not part of the count.
