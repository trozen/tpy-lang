# Brief: classify THIR reject sites for the cutover go/no-go

## Context

TurboPython (repo root: /home/tommy/dev/turbo-python/tpy-m1) has two C++ codegen paths for
function bodies: the legacy AST emitter (`tpyc/codegen_cpp/`) and THIR (`tpyc/thir/`,
lowering in `tpyc/thir/lower/`). Today, when THIR lowering hits a shape it cannot
handle it raises `ThirUnsupported(reason)`; the whole body then silently falls back
to the AST emitter. The project is about to DELETE the AST body emitter. After that,
a `ThirUnsupported` that escapes to the per-body boundary is no longer a fallback:
it is a hard compile error on whatever program hit it.

Your job: for every `raise ThirUnsupported(...)` site in your slice, decide what
happens to it after the cutover. This is a READ-ONLY review. Do not edit any file in
the repository. Do not run pytest. Do not use git to change anything.

## Your inputs

- Your slice: a JSON list of sites `{file, line, func, reason}` (path given in your
  task message). Line numbers are for the CURRENT tree; verify each by reading the
  code around it (use `sed -n` / `grep -n` / the Read tool).
- Probe tool: `uv run python scripts/thir_migration/review/probe_fallback.py FILE.py [--default-int int32|int64|BigInt]`
  It compiles a TPy program through both paths and prints a verdict:
  `ROUTES` (THIR handled every body), `BREAKS_AT_CUTOVER` (AST emits, THIR fell
  back on at least one body; the `fallback:` dict names the reason tag),
  `BOTH_REFUSE` (front end or AST codegen rejected the program), `THIR_RAISES_PLAIN`
  (THIR raised a non-ThirUnsupported exception: an ICE class, report it!).
  Always run from the repo root with `uv run`. Write probe programs under
  `<SCRATCH>/probes/<your-slice-name>/` (create the dir), never in the repo.
- `<SCRATCH>/known_39_shapes.txt`: 39 shapes already KNOWN to fall back (from a
  2026-09-01 adversarial sweep). If a site in your slice is the one behind a known
  shape, say so; do not re-derive those from scratch.
- Reading aids: `docs/IR_DESIGN.md` (design), `tpyc/thir/reject.py` (the
  `ThirUnsupported` class, `note()`, attempt bracketing), `tpyc/thir/testutil.py`
  (`_assert_rejects_at`: unit tests that PIN a reject use it; grep
  `tpyc/thir/test_*.py` for the site's reason literal to find pinned shapes).

## Language notes you need

TPy is a statically typed Python subset compiled to C++. Functions need type
annotations. Program shape for probes: put logic in `def main() -> None:` and call
`main()` at the bottom. Integer literals default to `int32` unless `--default-int`
says otherwise. `from tpy import int32, int64, Own, Ptr, StrView, ...` for the TPy
types. Records are plain classes with annotated fields and an `__init__`. Look at
`tests/cases/<group>/<case>/src/main.py` for idiomatic examples of any construct
(e.g. `tests/cases/match/`, `tests/cases/generators/`, `tests/cases/async/`,
`tests/cases/optional/`, `tests/cases/unions/`). Grep `tests/cases` for a construct
before guessing its spelling.

## The bins (exactly one per site)

- `INTERNAL` -- the raise is caught by an `except ThirUnsupported` handler INSIDE
  `tpyc/thir/` that tries a sibling arm or recomposes the reason and RE-RAISES from
  a different site, and no caller path lets it escape uncaught. Cite the handler
  `file:line`. If the handler calls `note(...)`/`_reject(...)` and returns, that is a
  BODY BOUNDARY, not internal: the body falls back. Then bin by what escapes.
- `DEAD` -- no program the front end accepts can reach the raise. You must cite the
  reason: a sema check that refuses the shape earlier (file:line), a type-system
  invariant, or a logically impossible condition given the enclosing arm's guards.
  "I could not construct a probe" is NOT evidence of DEAD; it is `UNSURE`.
- `REFUSAL` -- the shape is reachable, but the AST codegen path ALSO refuses it
  (probe verdict `BOTH_REFUSE` with the refusal coming from codegen, not sema).
  After cutover only the diagnostic changes. Record the AST error text and note that
  the `ThirUnsupported` reason is a tag, not a user-facing message.
- `BREAKS` -- a program the front end accepts and the AST emits today, that THIR
  rejects at THIS site. After cutover that program stops compiling. Must be CONFIRMED
  by a probe with verdict `BREAKS_AT_CUTOVER`, and you must check that the fallback
  reason printed matches this site's reason literal (or is composed from it: tags
  compose the host statement's shape as a prefix, and `note()` is set-if-empty so an
  EARLIER gate's reason can mask the one you are after; if it does, say so and try a
  probe that avoids the earlier gate). Record the probe file path.
- `UNSURE` -- you could not decide within budget. State exactly what is missing.

Extra flags (optional, any site): `plain_raise: true` if the guarded code path can
raise a NON-ThirUnsupported exception (AssertionError, KeyError, AttributeError,
ValueError ...) on a shape the front end accepts. That is an internal compiler error
class the cutover makes visible, and it is worth more than the bin.

## Method

1. Read the site, its enclosing function, and the guards between the function entry
   and the raise. Work out which source shape reaches it.
2. Check whether the reason literal is pinned by a unit test (grep the test files).
   A pin's fixture is a ready-made probe; run it through the probe tool.
3. Write a minimal probe that targets the site. Run it. Read the verdict AND the
   fallback reason: the reason must match this site, not an earlier gate.
4. If the probe was refused by the front end, figure out WHY (the error text says)
   and try a different spelling before concluding DEAD. Reject tags are lossy and
   sites interleave: never decide a site by sampling a sibling.
5. Sites in one function guarded by parallel `elif` conditions can share one probe
   family; still record each site separately.

Budget: your slice has ~70 sites. Spend effort proportional to difficulty; a site
whose guard obviously mirrors a sema refusal takes a minute, a site deep in an arg
ladder may take several probes. Do not stop early; every site gets a bin, `UNSURE`
being the honest one when you run out.

## Output

Write a JSON file at the path given in your task message: a list with one object
per site, in slice order:

```
{"file": ..., "line": ..., "func": ..., "reason": ...,
 "bin": "INTERNAL|DEAD|REFUSAL|BREAKS|UNSURE",
 "confidence": "high|medium|low",
 "evidence": "<one to three sentences: the guard/handler/sema check cited with file:line, or the probe verdict>",
 "probe": "<probe file path or null>",
 "plain_raise": false,
 "known_shape": "<quote from known_39_shapes.txt or null>"}
```

Then reply with a SHORT report (under 300 words): bin counts for your slice, the
list of BREAKS sites with one line each (line, reason, what the probe program does),
any `plain_raise` findings, and anything that surprised you. Do not paste code into
the report. The JSON file is the deliverable; the report is the summary.
