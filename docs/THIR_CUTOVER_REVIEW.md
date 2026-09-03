# THIR cutover review (2026-09-01)

A review of `tpyc/thir/` ahead of deleting the AST body emitters, run in
four phases against tree `c8c9268ac` (branch `tmp`). Every figure names the
tree and the key it was measured on; re-measure before reusing one.

- Phase 0 -- go/no-go: can the AST body emitters be deleted now?
- Phase 1 -- the data model (`nodes.py`), judged against `IR_DESIGN.md` and
  ordinary IR hygiene.
- Phase 2 -- lowering and emit: findings plus a target post-cutover shape.
- Phase 3 -- the unit tests: what each claims, what survives the cutover, a
  conversion plan.

Scope: REPORT ONLY. Nothing in the compiler changed for this review; the
only code that landed is the classifier scripts under
`scripts/thir_migration/review/`.

## Summary

- **Flip, delete, fix as we go (decided 2026-09-02).** Everything
  the corpus and the library reach is clean (3,767/3,767 cases, zero
  fallback, all audits at zero). Outside that set, one valid program in
  four still falls back today, measured three ways: 39 of 162 in the
  adversarial sweep, 478 of 1,901 whole programs embedded in the unit
  tests, and 402 of the 655 reject sites confirmed live by a probe. After
  deletion each of those is a hard compile error on working code,
  including everyday shapes such as `if f():` on an int call,
  `print(end='')`, `(a if c else b)[0]` and `case x as y`. Two lowering
  crashes on valid code (`(t := "ab").upper()`) show the internal-error
  class the gates cannot see. Order: fix those two crashes, flip and
  prove the snapshot diff empty, add a strict-mode diagnostic for
  rejects, run the adversarial generator once, delete, then fix rejects
  as they surface from the per-site reproducers.
- **The data model is complete but is the AST emitter transcribed.**
  101 of 537 node fields are pre-rendered C++ text, 114 are boolean
  flags in exclusive clusters, six mode discriminators are strings, the
  design's headline facts (`is_last_use`, `is_movable`, the layout sets)
  are declared and never written, and async/generator bodies are an
  overlay keyed by AST node identity. None of it blocks the cutover; all
  of it is a typing-and-naming pass plus a representation pass afterwards.
- **Lowering and emit want three layers where there are two and a
  half.** Two functions of 7,563 and 5,162 lines hold the dispatch;
  lowering renders callee spellings, casts and whole statements to text;
  the lowering context carries about thirty C++ slot-shape sets the design
  assigned to a later representation pass; semantic classifiers live in
  the printer package. The plan is seven zero-churn steps verified by the
  committed snapshots, then three output-changing ones, the last of which
  is the MIR seed.
- **The unit tests are mostly claims that die with the emitter.** Of
  6,741 tests, the byte-identity and routing claims lose their subject;
  the 197 reject pins and the 478 breaking programs are the inventory
  worth keeping; 869 of the 1,484 body shapes the routing programs
  exercise are absent from the corpus, so consolidating them into cases
  buys real coverage, not one-to-one.
- **Decisions for the user:** whether `faces.py` stays as a coverage
  instrument or is stripped; whether module-cumulative temp numbering is
  kept; whether the docstring rewrite lands in the deletion commit.

## Phase 0 -- go/no-go

### Verdict

**Flip, then delete, then fix as we go -- decided 2026-09-02.** Make
THIR the author of record (Gate D4's commit 1), prove the corpus is
unchanged, then delete the AST body emitters (commit 2) and treat every
remaining reject as an ordinary bug. Two months of the migration have
blocked meaningful compiler work; the doubled surface a staged window
would keep alive is a cost, not insurance.

What the evidence below establishes, and what the decision accepts:

- **The corpus and the library are not at risk.** Everything the 3,767
  cases and `lib/tpy` reach is byte-identical through THIR and every
  audit reads zero. Deletion cannot introduce silent wrong code there.
- **Outside that set, about one valid shape in four falls back today**,
  measured three independent ways, and each becomes a compile error at
  deletion. The tail is flat (hundreds of sites, no head), so it cannot
  be ground down first at any sane cost; after deletion each gap arrives
  with a stack trace naming its site, and the per-site bins in this
  report give a reproducer for 402 of them. The everyday shapes listed
  under "What the sites are" are the first fix queue.
- **Two lowering crashes on valid code** show a class the gates cannot
  see; they are fixed before the flip.

The review's first draft recommended a staged window (flip with a
fallback diagnostic, delete later). That was priced for an external
audience hitting novel shapes as refusals; for this project's single
author the trade goes the other way, and the ledger's own acceptance
record (section H of the Gate D4 inventory) made the same argument.
The evidence sections stand unchanged.

**Order of work, each its own small change on one branch:**

1. Fix the two known lowering crashes (`checks.py:7701`, reached through
   `_method_nonname_receiver_ok`), via `/tpy-fix-bug`. Independent of
   the flip. DONE 2026-09-02.
2. Commit 1 per D4: flip `thir_codegen` to the default, regenerate the
   snapshots, prove `git diff tests/cases` is EMPTY. Lifting
   `_thir_flag_conflict` in `tests/conftest.py` is the first step.
   DONE 2026-09-02.
3. Make a body-boundary reject a diagnostic, behind `--thir-strict`: a
   `CodeGenError` carrying the source line and the reject tag instead of
   the fallback. Default off until commit 2; pin the wording with unit
   tests and an `error_*` case now. Running the corpus with the flag on
   lists exactly the bodies that would break. DONE 2026-09-02, as
   `ThirRejectError` (a `CodeGenError` subclass, so a harness comparing
   both paths' verdicts does not read it as a disagreement) behind
   `--thir-strict`, with the wording, location and fold positions pinned
   in `tpyc/thir/test_thir_strict.py`. The `error_*` case is deferred to
   the deletion unit: while the fallback still exists the case would
   need the option, and the diagnostic it pins changes wording when
   `--thir-strict` goes away.
4. Run `dualgen.py` once at all three integer widths on the tree about
   to be deleted; it needs both authors and has no successor.
   DONE 2026-09-02.
5. Commit 2: delete the four AST body emitters and the migration
   scaffolding. **DONE 2026-09-03.** What went: the four emitters, both
   audits (`move_audit.py`, `binding_audit.py`), the two committed gates
   (`codegen_cpp/test_cutover_gate.py`, `tests/test_thir_stdlib_gate.py`),
   the `conftest.py` oracle pass / ratchet / dial / marker machinery and
   the seven `--thir-*` options, the error-path diagnostic-author gate,
   the interop overlay, the reject TALLY, and the migration scripts and
   two nightly rows. What stayed, against this step's original wording:
   `reject.py` is `fallback.py` trimmed to the reject module (it keeps
   `ThirUnsupported`, the reject-reason journal and the reject error
   builder -- only the tally dies); `shape.py` stays as a module with its
   per-run recording removed; `faces.py` stays with its zero-witness
   report printing unconditionally; and `scripts/thir_migration/review/`
   stays in place as the fix queue. `--thir-strict` and
   `CodeGenOptions.thir_codegen`/`thir_strict` are gone -- strict is the
   only behaviour. No `expected/` file was touched. The
   detector-successor designs (M1, M2, B1) were not built; they were
   insurance for a staged window. The ledger's closing entry records the
   diffstat and the retired detectors.
6. **The live queue.** Fix rejects as they surface, from the
   everyday-shape list below and `program_verdicts.json`; each ends as
   supported (with a corpus case), a deliberate diagnostic, or a filed
   defect. The per-site bins in `scripts/thir_migration/review/` are the
   working list: `bins_*.json` (402 BREAKS rows with a reproducer under
   `probes/<slice>/`) plus the `BREAKS_AT_CUTOVER` entries of
   `program_verdicts.json`. Phase 2's zero-churn restructuring can
   proceed in parallel, since the committed snapshots stay the oracle.

### Evidence

**The corpus and the library are clean.** Full suite at `c8c9268ac`
through rpytest: 13,439 passed, 23 skipped; 3,767 of 3,767 cases routed
with zero fallback; 13,262 bodies through THIR; interop 34 of 34; move
verdicts 0 divergences over 1,012 joined nodes; binding facts 0 gaps
over 11,958 joined bodies. The stdlib's committed render
(`tests/cases/harness/stdlib_render`, 1.7 MB) exists, so the "closing
window" item from the detector-successor design is closed. Nothing the
corpus reaches changes at the flip.

**Outside the corpus, one in four valid shapes falls back.** Three
populations, three keys, one ratio:

| population | key | falls back | ratio |
|---|---|---|---|
| adversarial sweep, 2026-09-01 (ledger, TODO.md) | hand-written probe programs at Int32 | 39 of 162 | 24% |
| every whole program embedded in the THIR unit tests (this review, `probe_programs.py`) | distinct programs the front end accepts | 478 of 1,901 | 25% |
| every reject site, read and probed (this review, 9 slices) | `raise ThirUnsupported` sites | 402 of 655 confirmed live | 61% of sites |

The site figure is a different key (a site is live if SOME program
reaches it) and is not comparable to the program ratios; it says how
much of the reject surface is real rather than defensive. The 478
breaking programs spread over 279 distinct reject tags; the 39 over 32.
There is no head to attack: the tail is flat, which is why grinding it
before the flip was rejected by the ledger and is not proposed here.

**What the sites are.** All 655 sites were read and probed, one agent
per slice, under one rubric (`scripts/thir_migration/review/
BRIEF_reject_sites.md`):

| bin | sites | meaning |
|---|---|---|
| BREAKS | 402 | a program the front end accepts and the AST emits, confirmed by a probe whose fallback was traced to this raise line |
| DEAD | 141 | unreachable, with the sema refusal / parser shape / masking gate cited |
| UNSURE | 108 | no spelling found in budget; most sit in arms whose sibling rung is confirmed BREAKS, or behind an earlier gate |
| REFUSAL | 4 | the AST refuses the same shape at codegen; only the diagnostic changes |
| INTERNAL | 0 | no site in the read bins is caught and retried by a sibling arm. Two handlers swallow rather than retry (`constants.py:102` returns None for a constant position, `simple_gen.py:265` re-tags), and the census found one site caught internally (`expressions.py:12736`) |

Per file: `statements.py` 184 / 44 / 44 / 1 (BREAKS / DEAD / UNSURE /
REFUSAL), `expressions.py` 134 / 39 / 48 / 0, `match.py` 42 / 44 / 0 /
1, `resumable.py` 15 / 5 / 6, `functions.py` 13 / 4 / 6 / 2,
`comprehensions.py` 10 / 3 / 1, the rest under 3 each. Every BREAKS
carries `confidence: high`; 27 sampled BREAKS probes (three per slice)
were re-run for this report and all reproduced. Raw bins with the
evidence per site: `scripts/thir_migration/review/bins_*.json`; the
cited probe programs under `probes/<slice>/` in the same directory.

Shapes a user writes without thinking, all confirmed to fall back today:

- `if f():` on an int-returning call; `if n + 1:`; `if 1:`; `if c:` on a
  Char or Float local (`expressions.py`, 14 truthiness sites)
- `print(end='')`, `print(sep=', ')`, `print(flush=True)`
  (`statements.py:14491-14530`)
- `h.host = v` through a user `__setattr__` with a non-literal value
  (`statements.py:762`)
- `with` inside an `if` / `for` / `while` whose body binds a name read
  after the block (`statements.py:15136`)
- `del a.x, b.y` and `del d[k], e[j]` multi-target
  (`statements.py:13404`, `13349`)
- `match f():` on a call subject; `case x as y`; `case 1 | _:`
  (`match.py:1139`, `1427`, `1440`)
- `(a if c else b)[0]`; `a or b` on bytes / `Span` / `bytearray`
  (`expressions.py:6571`, `2838`)
- `xs[0] = 5` on `list[Int32 | None]`; `xs[1] = None` on
  `list[Box | None]` (`statements.py:10518`, `10491`)
- `n + 1` as a bare statement (`statements.py:14810`)
- a nested `def` taking a `list[Int32]` or Optional parameter
  (`statements.py:4776-4864`)
- module-scope non-value globals in several spellings
  (`statements.py:4330-4486`)
- `if isinstance(x, Proto):` inside any async or generator body
  (`statements.py:6900`: the resumable lowering never passes the
  concept renderer)
- the free-call admission gate at `expressions.py:12307` absorbs about
  twenty unrelated argument shapes and will dominate any post-cutover
  error report; its message is a tag, not a diagnostic

**Re-probed 2026-09-03 at master `da1a92ee15`, the post-deletion tree**
(`scripts/thir_migration/review/reprobe.py`; its output is not committed,
the script re-runs in under a minute): 388 of the 402 BREAKS rows still rejected, 12
compiled, none crashed. Of the everyday shapes above, the truthiness family
(all five spellings), the plain `print` keyword forms (`end=` / `sep=` /
`flush=` with literal values; `**kw` and a computed `sep=` still reject),
`with` inside an `if`, multi-target `del` on subscripts, the free-call
ternary argument and the plain module-scope `list[str]` global compiled at
that tree; the bins behind the `del` / `with` / global / `print` lines still
reject at their own narrower spellings. The first step-6 batch (same day)
then closed the three `match` rows (on the scalar, chain, str-switch and poly
tiers; `case x as y` still rejects on the record / union / optional-record
tiers, `BUGS.md#match-as-capture-composite-tiers`), `(a if c else b)[0]`, `a or b` on
bytes / `Span` / `bytearray`, both Optional-element setitem rows, the bare
`n + 1` statement, nested `def` with container params, `del a.x, b.y` and
the non-literal `__setattr__` value. The queue is flat: 260 distinct live
tags over the 388 rows, so batches are chosen by user-facing frequency,
not by count; re-run the script before choosing one.

Sites whose comments call them unreachable but are live: `match.py:1873`,
`2429`, `1994`; `statements.py:4146`; the "defensive" rvalue-plus-hoist
leg of the record match tier. Many DEAD sites are dead only because an
earlier gate masks them (44 of `match.py`'s 44; the `temps_ok` flush
discipline in the arg ladders, which is near-uniform but not uniform:
the ctor ladder's optional-pointer scalar row lacks it and that is why
`expressions.py:14473` is live), so a widened gate upstream revives
them. Several BREAKS gate shapes the AST renders as ill-formed C++
(`expressions.py:5059`, `5937`, `5981`, `6723`, `7467`;
`statements.py:3256`, `5651`): there "breaks" means a bad-C++ error
becomes a THIR error, the harmless direction. One AST-side crash was
found in passing: `copy(xs)` on a literal-seeded list raises
`RuntimeError: PendingListType should be resolved before codegen` at
`codegen_cpp/expressions.py:561`; THIR rejects it cleanly.

**The internal-error class is real and has witnesses.** Lowering can
reject by raising something other than `ThirUnsupported`, which no gate
sees and which the deletion turns into a compiler crash on valid code:

- `(t := "ab").upper()` -- a walrus as a method receiver, on a plain
  `str` -- raises `AttributeError: 'TpyNamedExpr' object has no
  attribute 'obj'` from `checks.py:7701`, reached through
  `_method_nonname_receiver_ok` (`checks.py:7927`), which forwards every
  non-Name, non-Call receiver to the field-receiver check. A ternary
  receiver on a `@dynamic` protocol takes the same path. Re-run for this
  report on the one-liner; the AST emits it. Filed and fixed 2026-09-02.
- `test_thir_wave_freecall_ret.py::test_bugs_repro_validator_escapes_fallback`
  pins a `THIRValidationError` on code the AST emits, the same class.
- One routed byte DIVERGENCE: `isinstance(v, Int32) and v is None` on
  `Int32 | str | None` -- the AST emits ill-formed
  `holds_alternative<monostate>(std::get<int32_t>(v))`, THIR emits valid
  code, and the fence at `expressions.py:4016` misses it. Benign
  direction; the fence is dead.

Across roughly 900 probe runs by the nine agents these were the only
plain raises, so the class is small but it is not empty, and nothing
in the test suite would have found the first one.

**The asymmetry probe** (`asym/check_asym.py`, three widths, 2,016
runs): 0 programs the AST refuses and THIR emits, 21 fallbacks, of which
7 distinct shapes, all already in TODO.md's sweep record. The control
passed at Int32 and FAILED at Int64 and BigInt for one of its five
cases (`error_async_match_dyn_await` is refused by the FRONT END at
those widths, so the codegen-refusal control cannot be asked there).
Read the zero as validated at Int32 only.

### The detectors that die: recommendation per detector

- **`move_audit.py` -- accept the loss, build M2 first.** The join
  compares the same formula over the same `all_last_uses`; its only
  independent input is the movable set, so it detects movable-set drift
  and nothing else, and ~2/3 of the move surface never passes through it
  (detector-successor design, 2026-08-30). Its class is latent (a wrong
  verdict at a site whose render ignores it). The cheap THIR-internal
  successor M2 -- forbid a move across a materializing conversion,
  80-150 lines -- targets the one wrong-code move the join missed and
  can be accepted against the audit while it still exists. Build M2
  before commit 2; defer M1 (the move chokepoint, 250-350 lines) to Phase
  2's Z3/Z7 where the move fields get typed anyway.
- **`binding_audit.py` -- accept the loss.** Its subject (AST binding
  sets as a subset of THIR mirrors) has no meaning with one author, and
  Phase 2's O2 collapses the mirrored sets into one storage-class fact,
  which is the real successor. The scoped B1 variant from the design
  (decl-node vs set agreement for `pointers` / `ptr_variant_locals` /
  `optional_locals`) is worth 3-4 days only if O2 is far off.
- **The error-path gate -- accept the loss.** `diag.txt` already carries
  every codegen diagnostic a case reaches, at the same granularity. What
  dies is coverage of shapes no case carries, which it never covered.
- **`dualgen.py` -- run it once more immediately before commit 2**, all
  three integer widths, on a tree that has not moved much since the
  2026-09-01 sweep. It is the only instrument that has found the
  out-of-corpus divergence class and has no successor. **DONE 2026-09-02**
  (three populations, ~5,300 programs, tree `1be2cf003`; the tables are in
  the ledger). The tool needed two authors and was deleted with them.
- **`faces.py` -- needs the user's decision** (Phase 2, F7). **Decided
  2026-09-02: it stays**, with its zero-witness report printing
  unconditionally.

(Written for the staged-window draft; with the decision above only the
`dualgen` run survived as a precondition, and it is discharged. Kept
because the per-detector reasoning is what a later reader will ask for.)

### Census: the program corpora cannot see the reject surface

A reject-reachability sweep over both populations (corpus + stdlib, tree
`c8c9268ac`; the sweep tool was deleted with the migration scripts). NAME THE KEY: TODO.md and the ledger's Gate D4 section H
quote `--population all` at 2026-09-01 as 283 reached / 372 never; that
figure adds the `units` population, the THIR unit tests, which reach a
site ON PURPOSE (a boundary pin exists to hit it). The table below is
the two PROGRAM corpora alone, which is the population the point below
is about:

| bucket | sites |
|---|---|
| static `raise ThirUnsupported` sites | 655 |
| reached by any compiled program | 2 |
| of which fallback-causing | 1 (`expressions.py:7704`, from one `error_*` case) |
| of which caught internally | 1 (`expressions.py:12736`) |
| never reached | 653 |

Read that against the 402 sites confirmed LIVE by probing (all 655 read):
the census reaches 0.3% of the sites, hand probes reach 61%.
The two populations are answering different questions. The corpus and
the library are ratcheted to zero fallback, so by construction they can
reach a reject site only by failing that ratchet; the census measures how
clean the corpus is, not how large the reject surface is. This is the
ledger's own observation ("the healthier the migration gets, the blinder
the program corpora become"), now with the number attached: 653 of 655.
Do not use the census's unreached bucket as evidence of dead code; use
the per-site bins, and after the flip, the fallback diagnostic on real
programs.

## Phase 1 -- the data model

`tpyc/thir/nodes.py` at `c8c9268ac`: 3,441 lines, 123 frozen dataclasses
(60 expression nodes, 46 statement nodes, 17 auxiliary), 537 own fields
excluding the four base-class fields. Six nodes carry `__post_init__`
exclusivity asserts; `validate.py` (621 lines) holds a second, walk-based
rule set.

### What the design asked for, and what is there

`IR_DESIGN.md` ("THIR Design", "Form as a First-Class THIR Fact") asks for
an immutable, self-contained IR: every sema fact on the node, no side
tables, form as a carried positional fact set by one classifier, and
representation normalization deferred to MIR so the byte-diff net stays
intact during coexistence. Measured against that:

- **Self-contained: mostly, with one structural exception.** Expression
  and statement nodes carry resolved types and facts and never reference
  the analyzer. But `THIRResumableBody` is not an IR of the body: it is
  seven `Mapping[int, ...]` tables keyed by `id()` of AST nodes the
  state-machine skeleton still walks (`nodes.py:3363-3397`), and
  `THIRResumableReturn.ast_stmt: object` carries a raw AST node
  (`nodes.py:1949`). `THIRMatchArmEntry.body_key` is another `id()` hook.
  For async and non-simple generator bodies THIR is a leaf overlay on an
  AST-driven printer, by the Gate D3 decision. That decision is fine for
  the cutover; it is the single biggest obstacle to a MIR, because a CFG
  IR cannot be keyed by the identity of a syntax tree it no longer owns.
- **Form as a carried fact: done, and then bypassed at the leaves.**
  `Form` sits on `THIRExpr` and `THIRFormConvert` is the one conversion
  node, as designed. But several leaves also carry a sink-decided render:
  `THIRBytesLiteral.form` is chosen per sink (`nodes.py:126-139`),
  `THIRLiteral.int_cpp` / `none_cpp` are pre-spelled per slot
  (`nodes.py:95-105`), `THIROptViewArg` / `THIROwnOptRebuild` are
  arg-boundary conversions modelled as leaf nodes rather than converts
  (`nodes.py:426-457`). The design's "conversion inserted at each CONSUMER
  site" rule is honoured for the Optional/union/tuple families and
  side-stepped for literals and views.
- **No side tables: true for the IR, false for the seams.** `id(` appears
  17 times in `nodes.py` (all in the resumable maps' docs) and 41 times in
  `lower/resumable.py`; `lower/statements.py` has 37. The lowering keeps
  id-keyed journals (`hoist_decls`, prescan facts, rebind slots) that are
  exactly the side tables the design wanted materialized.

### The five hygiene findings

1. **101 of 537 fields are pre-rendered C++ text.** Counted by name
   (`cpp`, `*_cpp`, `cpp_*`). They range from a spelled type (defensible:
   type rendering is the printer's job either way) to whole statements
   composed in lowering: `THIRDynNarrowAlias.cast_rhs_cpp`,
   `THIRMatchArmEntry.poly_ref_decl` ("the fully-rendered
   `Sub& __case_i = *__mpoly_i;` alias line"), `THIRGenExpr.binding_cpp`
   (a pre-rendered `{counter} {var} = __i++;`), `THIRNestedDef.capture_cpp`,
   and the `(prefix, suffix)` string pairs that `THIRMatchArmEntry`
   composes around a runtime subject spelling (`field_conds`, `opt_conds`,
   `poly_cast`, `poly_or_conds`). A node holding half a C++ statement with
   a hole in it is a render plan, not an IR fact; the emitter cannot
   re-decide anything about it and a MIR could not consume it.
2. **114 boolean flags, many in mutually exclusive clusters.**
   `THIRFieldAccess` carries five flags and four asserts
   (`nodes.py:1346-1370`); `THIRIsNone` four mode flags; `THIRMembership`
   three (`free_function` / `ranges_contains` / `iter_loop`) that select
   one of four renders; `THIRMatch` eight; `THIRName` four including two
   never written. The file already contains the right pattern four
   times -- `TupleSourceBind`, `WithTargetArm`, `PtrSlotKind`,
   `PrintForm` -- and `TupleSourceBind`'s own docstring calls itself "one
   typed discriminator replacing the accreted per-form booleans". The
   remaining clusters are the same fix not yet applied.
3. **Stringly-typed discriminators beside real enums.**
   `THIRComprehension.kind` / `.loop`, `THIRForRange.step_kind`,
   `THIRTry.tier`, `THIRMatchBinding.mode`, `THIRTupleUnpack.binds`
   (validated against a `_BIND_TOKENS` frozenset at construction), and
   `THIRMatch.strategy` with fourteen string values. Each is an enum
   written as a string.
4. **Declared-and-never-written fields.** `THIRName.is_last_use` and
   `is_movable` (the design table's headline facts; one write site in
   `lower/`, none read), `THIRFunctionLayout`'s three sets ("the fields
   exist so the emitter reads layout off THIR rather than the analyzer as
   coverage grows" -- it never did), `THIRConstructor.record_name` /
   `params` ("not read by the tail-only emitter"). The move decision is
   made in lowering and baked into `THIRMove` / `move: bool` fields
   instead, so the design's "last use + movable on the name" model exists
   only as dead declarations.
5. **Sibling node families that are one construct with a mode.** Three
   narrowing aliases (`THIRNarrowAlias` / `THIRDynNarrowAlias` /
   `THIRAnyNarrowAlias`), four isinstance nodes, three membership nodes,
   three tuple-literal nodes, three error-return nodes, and the
   declaration family `THIRVarDecl` / `THIRPtrLocalDecl` /
   `THIRFrameSlotWrite` / `THIRAssign` / `THIRPtrLocalRebind`, whose
   `PtrSlotKind` enum has 21 members each documenting a distinct C++
   slot recipe. These are not wrong; they are the AST emitter's arm
   structure transcribed into the type hierarchy, and they are why the
   emit dispatch is an isinstance ladder.

### Documentation state

- The module docstring is stale: it says the node set "covers the
  non-form value-type subset" and that form-carrying nodes are
  "deliberately absent" (`nodes.py:1-13`), while `Form` is on the base
  class and dozens of borrow/storage nodes exist.
- 299 of the file's 3,441 lines name an AST emitter symbol or say "mirrors
  the AST". Across all non-test THIR files the count is 3,619 lines
  (grep for `_gen_*` / `gen_*` / `_emit_*` / `ExpressionGenerator` /
  "the AST" / "mirror"; a coarse key, over-counting emit.py's own
  `_emit_*` names). Whatever the exact figure, the node docstrings
  specify themselves by pointing at code the cutover deletes. The
  policy question is in Phase 2.

### Verdict on the data model

As a codegen IR for the AST-deletion commit it is sufficient: it is
complete over the corpus and the library, validated, dumpable, and frozen.
As the permanent sema->codegen boundary it is a transcription of the AST
emitter's decision tree into dataclasses, with the decisions themselves
often already rendered to text. The fix is not a rewrite: it is (a) a
naming and typing pass (enums for the string modes and flag clusters,
delete the dead fields, rewrite the docstrings as invariants), zero churn;
then (b) a representation pass that moves pre-rendered strings back to
typed facts the emitter renders, mostly zero churn if done one node at a
time against the committed snapshots; then (c) the design's own deferred
item, collapsing `PtrSlotKind` and the `cpp_local_representation`
metadata into a late representation lowering -- the piece the design
assigned to MIR, and the first output-changing step.

## Phase 2 -- lowering and emit

Seven parallel layer reviews under one rubric (monoliths, duplicate
classifiers, decisions in the wrong layer, byte-identity warts, reject
population, docstring debt, string modes and flag zoos, the MIR boundary),
each anchored to `file:line`; every load-bearing claim below was re-checked
against the tree. Raw findings: `scripts/thir_migration/review/p2_*.json`.

Sizes at `c8c9268ac`: `lower/` is 71,259 lines in 14 files; `emit.py`
5,069; the AST body emitters the cutover deletes are 15,759.

### Findings

**F1. Two functions hold the dispatch, and the extraction is already half
done.** `_lower_stmt_dispatch` (`lower/statements.py:7287-14849`, 7,563
lines) is 22 top-level `isinstance` arms. The small kinds (`with`, `try`,
`raise`, `match`) delegate to a named function in one line each; the four
big kinds are inlined: `TpyVarDecl` 2,756 lines (7470-10226), `TpyReturn`
1,768 (11140-12908), `TpyAssign` 688, `TpyForEach` 548. `_lower_expr_impl`
(`lower/expressions.py:4836-9998`, 5,162 lines) is 18 arms; `TpyCall`,
`TpyMethodCall` and `TpySubscript` run about a thousand lines each.
`_lower_call_arg` (12962-14681) is 37 predicate arms with 65 return points:
the ADMISSION ladders were folded into `checks.py` tables on 2026-08-26
(TODO.md "THE ARG-LADDER FOLD"), but the LOWERING cascade behind them was
not, and TODO.md records the unexecuted half (consume the gate's verdict
token instead of re-deriving the row). On the emit side `_emit_expr` (436
lines) and `_emit_stmt` (949) are `isinstance` ladders, `dump.py` carries
an independent 109-arm ladder over the same hierarchy, and `_emit_match`
(`emit.py:2956-2984`) is a 12-comparison ladder on the string
`THIRMatch.strategy` mirrored by a second ladder in `lower/match.py`.

**F2. Lowering renders C++ text that the printer should own.** The two
largest call classifiers, `_marker_call_kind` (`checks.py:8124`, 263 lines)
and `_free_callee_kind` (`checks.py:3543`, 256 lines), are parallel ladders
that call the codegen naming helpers (`module_qualified_callee_cpp`,
`static_method_callee_cpp`, `escape_cpp_name`, ...) and store the result on
`THIRCall.callee_cpp` / `cpp_template` / `template_args_cpp`.
`_coerce_wrap` (`predicates.py:312`) hand-builds `static_cast<T>({0})`-style
templates per coercion name, a second string encoding of what
`coercions.py` already tabulates, and `_coerce_disposition`
(`predicates.py:368`) returns a bare string mode. `THIRDynNarrowAlias.
cast_rhs_cpp` and the `hoist_decls: tuple[tuple[str, str], ...]` pairs on
`THIRIf` / `THIRTry` / `THIRWith` / `THIRForRange` / `THIRForEach` /
`THIRMatch` carry rendered type text where a typed fact would do. The Phase
1 count (101 pre-rendered fields) is the node-side view of this.

**F3. The lowering context is mostly C++ representation state.**
`_LowerCtx` (`lower/context.py:671-1395`) initializes 50 per-name sets and
dicts. By the reviewer's read roughly thirty are slot-shape facts
(`pointers`, `ptr_variant_locals`, `rebind_slot_locals`,
`storage_tuple_locals`, `frame_slots`, `coro_handle_slots`, the const
twins, ...) and the rest are semantic (`movable_locals`,
`ref_alias_locals`, the narrowing scope, `forbidden_reads` / `_writes`).
`IR_DESIGN.md`'s Form section names exactly this population ("~12
predicates + 22 side-sets") as MIR's late-representation fold. The
`binding_union` / `_binding_capture` plumbing in the same class exists only
for `binding_audit.py` and dies with it.

**F4. Semantic classifiers live in the printer package.** Lowering's
imports from `codegen_cpp` are mostly naming and type-rendering primitives
(`context`, `types`, `type_resolution`), which is the right direction. Four
are not: `forms.classify_local_binding` (the borrow-vs-storage verdict, read
at `checks.py:1647`), `forms.reads_storage_form_optional` /
`is_plain_nonvalue` / `is_ptr_variant_union`, `protocols.classify_dyn_own_arg`
/ `dyn_forward_ok` (`checks.py:8892-8970`), and `protocols.narrow_cast_rhs`.
These decide THIR's own Form and forwarding facts and were homed in
`codegen_cpp` so both emitters could share one implementation.
`emit_prims.py` documents itself as a temporary home pending a move into
`thir/`; `forms.py` and `protocols.py` carry no such note, so a checklist
scoped to `emit_prims` misses them. `_borrow_local_binding`
(`checks.py`, 351 lines) fetches the `classify_local_binding` verdict and
then re-derives three more cases for its fallthrough, which is a classifier
to complete, not a ladder to tabulate.

**F5. Byte-identity warts with no C++ reason.** The `CtxTempSink` /
`CtxCounter` adapters (`emit.py:309-412`) exist so temp and label numbers
stay module-cumulative across AST and THIR bodies interleaved in one file;
`CtxTempSink.create` calls straight into the AST context's `temps`
(`emit.py:321`). `paren_wrap=True` is baked at lowering in
`_slot_literal_retype` (`expressions.py:16263`) to reproduce an AST
parenthesization. Counter-draw ordering ("the AST draws one loop index PER
bound", `nodes.py:1112`), `burns_match_counter`, `no_source_comment` and
`trivia_loc` reproduce emission accidents. By contrast `THIRArgTemp`
(`nodes.py:733`) is the pattern to keep: it carries the verdict and defers
numbering to emit.

**F6. The reject population is migration debt, not language semantics.**
Every layer reviewer characterised its `ThirUnsupported` sites the same
way: gates that say "no witness yet, the AST arm is not mirrored"
(`statements.py:15111`, the `res.*` / `sig.*` / `sgen.*` families in
`resumable.py`, `container_lit.elem.*` and `method.ptr_template.*` in
`checks.py`), with a small minority that encode a real restriction
(`decl.self_rebind`; the `forbidden_writes` match-scope invariant at
`statements.py:7292`; F1-record-only receivers by design). Phase 0
quantifies this per site.

**F7. Migration-era modules and instruments.** `fallback.py` and
`shape.py` lose their subject at cutover (nothing falls back, nothing is
"routed vs total"). `faces.py` has 34 `_witness(...)` calls woven through
`emit.py` and its only reader is the `--thir-codegen` summary in
`conftest.py`, which the cutover removes; keep-as-coverage-instrument or
strip is a decision, recorded below. `validate.py` and `dump.py` survive
with docstring cleanup. The `analyzer.if_branch_decls` side table
(`sema/context.py`) is read at 23 sites in `lower/` by id(); that is a
pre-existing sema pattern THIR widened rather than materialized.

**F8. The resumable overlay is two layers of id() maps.** Besides the ten
`Mapping[int, ...]` tables on `THIRResumableBody` / `THIRSimpleGenBody`,
the outer seam caches whole lowered bodies on `CodeGenContext.
thir_functions` / `thir_resumables` / `thir_simple_gens` /
`thir_constructors` keyed by `id(func)` (`codegen_cpp/context.py:1341-1365`),
consumed at `generator.py:539-585`, `gen_async.py:2142`, `records.py:920`.
And `codegen_cpp/resumable_cfg.py` is already a basic-block CFG (`Fall` /
`Branch` / `ReturnT` / `RaiseT` / `MatchDispatch` terminators over
`BB.stmts`), owned by the printer, AST-typed, and scoped to async and
generator bodies only.

**F9. Docstring debt.** 3,619 lines across the non-test THIR files name an
AST emitter symbol or say "mirrors the AST" (coarse key, see Phase 1);
per file: `expressions.py` 582, `statements.py` 532, `checks.py` 489,
`predicates.py` 433, `emit.py` 727 (inflated by its own `_emit_*` names),
`match.py` 135, `functions.py` 126, `resumable.py` 105. The statements
reviewer found most already sit next to an invariant sentence, so the
rewrite is largely a trim.

### The target shape

Three layers where today there are two and a half:

1. **Lowering produces a semantic THIR**: resolved types, `Form`, move and
   copy verdicts, narrowing aliases, ownership at boundaries, the
   control-flow tree. No C++ text, no slot numbering, no strategy choice.
2. **A representation pass** decides per name and per site how a value is
   stored: pointer local, rebind slot, frame slot, storage optional,
   hoisted predecl, temp materialization, match dispatch strategy. This is
   the pass `IR_DESIGN.md` deferred to MIR; it consumes `_LowerCtx`'s
   thirty representation sets, `PtrSlotKind`'s 21 recipes and
   `cpp_local_representation`, and emits one storage-class fact per name.
   It is where a MIR would slot in, and `resumable_cfg.py`'s block graph
   is the seed of its CFG.
3. **The printer** renders C++: names, qualification, templates, casts,
   comment trivia, counters. Everything now spelled into `*_cpp` fields
   moves here.

Dispatch in every layer is a table keyed on node type (lowering and emit
share one registry with `dump.py`), and shape-uniform families use
`field_write.py`'s `(classify, lower)` plan pattern: the var-decl and
return arms, `_lower_call_arg`, `_container_lit_elem_ok`, the two callee
ladders. Families whose arms differ in control-flow shape (the fourteen
match strategies, the comprehension routes) get per-strategy functions
behind an enum, not a table.

### The plan, in order

Prerequisite: the cutover itself, as two commits per Gate D4 (flip
authorship, prove `git diff tests/cases` empty; then delete). Everything
below assumes the AST emitter is gone and the committed snapshots are the
oracle.

Zero-churn steps, each verifiable by the corpus byte-diff alone:

- **Z1 Delete the scaffolding.** `fallback.py`, `shape.py`, the
  binding-audit plumbing in `context.py`, the `AST_ONLY_DIAGNOSTICS`
  machinery, the reject-reason `note()` journal. Keep `faces.py` only if
  the decision below says so. Size S.
- **Z2 Docstring policy.** Rewrite every AST-symbol reference as the
  invariant it was pointing at; delete "mirrors X" where the invariant is
  already stated. Mechanical, ~3.6k lines to touch, best landed in the
  deletion commit so dangling names never exist. Size M.
- **Z3 Types for modes.** Enums for `THIRMatch.strategy`,
  `THIRComprehension.kind` / `loop`, `THIRForRange.step_kind`,
  `THIRTry.tier`, `THIRMatchBinding.mode`, `THIRTupleUnpack.binds`,
  `_coerce_disposition`, `_ForEachRoute.route`; discriminators for the
  flag clusters on `THIRFieldAccess`, `THIRMethodCall`, `THIRIsNone`,
  `THIRMembership`, `THIRName`; delete the never-written fields
  (`is_last_use`, `is_movable`, `THIRFunctionLayout`'s sets,
  `THIRConstructor.record_name` / `params`). Size M.
- **Z4 Dispatch tables.** Split `_lower_stmt_dispatch` and
  `_lower_expr_impl` into per-kind functions behind `type -> handler`
  tables; the same for `_emit_expr` / `_emit_stmt` with `dump.py` sharing
  the registry. Pure extraction; the small statement arms show the shape.
  Size M (mostly moving text).
- **Z5 Plan-table folds.** `TpyVarDecl` and `TpyReturn` arms,
  `_lower_call_arg` (consuming the admission token), `_container_lit_elem_ok`,
  `_marker_call_kind` + `_free_callee_kind` merged. Accept that a few
  plans carry scope mutations. Size L.
- **Z6 Re-home the semantic classifiers.** Move `forms.py`'s and
  `protocols.py`'s classifiers and `emit_prims.py` into `thir/`; complete
  `_borrow_local_binding` as one classifier. Size S-M.
- **Z7 Un-render one node at a time.** Replace each `*_cpp` field with the
  typed fact and render it in the printer, byte-diffing after each node.
  Start with the whole-statement strings (`cast_rhs_cpp`, `poly_ref_decl`,
  `binding_cpp`, `capture_cpp`), then `callee_cpp` / `cpp_template`, then
  the `hoist_decls` pairs, then `THIRCoerce.wrap`. Size L in total, S per
  node.

Output-changing steps, each a separate change with a one-sentence
description of its diff class, verified by exec and CPython parity:

- **O1 Drop the emission accidents.** `paren_wrap`, the per-bound loop
  index draw, `burns_match_counter`, and (if wanted) per-function instead
  of module-cumulative temp and label numbering, which also removes the
  `Ctx*` adapters. Wide but uniform churn. Size S-M each.
- **O2 The representation pass.** Collapse the thirty `_LowerCtx`
  representation sets, `PtrSlotKind` and `cpp_local_representation` into
  one per-name storage-class fact computed once, and move the choice out
  of lowering. Do it per family (optional locals, union locals, tuple
  locals, frame locals), each with its own churn. This is the design's
  own deferred item and the first place emitted slot shapes may improve.
  Size XL.
- **O3 A THIR-owned CFG for resumable bodies.** Generalize
  `resumable_cfg.py`'s block graph into a structure THIR owns, so leaf
  content becomes block statements instead of id()-keyed maps and the
  outer `thir_*` caches disappear. This is the MIR seed; it should not be
  started before O2 has shown what the representation facts are. Size XL.

Decisions this plan needs from the user: whether `faces.py` stays as a
permanent coverage instrument (then it needs a reader that runs) or is
stripped in Z1; whether module-cumulative counter numbering is worth
keeping (it is only an AST habit, but dropping it churns most snapshots at
once); and whether the docstring rewrite lands in the deletion commit or as
its own pass immediately after.

## Phase 3 -- the unit tests

`tpyc/thir/test_*.py` at `c8c9268ac`: 289 files, 6,741 test functions,
127,357 lines. 217 files are `test_thir_wave_*` (2,165 tests, one file per
grind row) and 72 are feature-named (4,576 tests). Two instruments, both
under `scripts/thir_migration/review/`:

- `classify_tests.py` reads every test statically and records which CLAIM
  its assertions make (routing, lowering, byte-identity, reject, render
  substring, node structure, fallback tally), plus the duplicated embedded
  programs.
- `probe_programs.py` runs every distinct embedded program through both
  codegen paths and records whether it routes, falls back, or is refused.

### What the tests claim (static, per test, approximate)

| headline claim | tests | what it means after cutover |
|---|---|---|
| routes (`_assert_routes_byte_identical`, `_lower_ctx_witnessed`) | 2,076 | "lowers without raising" -- still meaningful, but every corpus case proves the same |
| lowers (`_lower_ctx` + node lookups) | 2,423 | same, weaker form |
| render or node structure only | 712 | snapshot tests in miniature, without a build |
| identity only (`_assert_byte_identical`) | 186 (+165 with a render assert) | compares THIR to a deleted emitter: vacuous |
| reject pins (`_assert_rejects_at`, `_raised_in_lowering`) | 197 | the witnessed inventory of refused shapes |
| fallback-tally asserts | 272 | lose their subject with `fallback.py` |
| structural / meta (arg-table registry, faces, validator, dump, node invariants) | 710 | stay as unit tests |

The classifier is per test and approximate: a reject pin written as
"lower the module witnessed, then assert the fallback dict" reads as a
routing claim (57 single-program tests named `*_keeps_rejecting` /
`*_stays_ast` land there). Use the table for proportions, not for a
per-file decision.

Duplication: 6,877 embedded programs, 4,855 distinct, so 2,022 are exact
copies; 109 programs recur across files.

### What the programs do (dynamic, per program, exact)

Of the 4,855 distinct embedded programs, 1,901 are whole programs the
front end accepts at `Int32` with the stdlib alone. The other 2,954 are
string fragments composed at runtime (2,187 fail to parse on their own),
fixtures that need a test-local library directory (518), or deliberate
front-end error pins (249). The probe covers the 1,901 -- name the
population when quoting the figures.

| verdict | programs | note |
|---|---|---|
| ROUTES | 1,420 | unchanged by the cutover |
| BREAKS_AT_CUTOVER | 478 | AST emits today, THIR falls back: a hard error after deletion |
| BOTH_REFUSE | 2 | codegen refusals on both paths |
| THIR_RAISES_PLAIN | 1 | `THIRValidationError` on code the AST emits -- a pinned bug repro (`test_thir_wave_freecall_ret.py::test_bugs_repro_validator_escapes_fallback`), and the exact post-cutover ICE class |

One in four whole programs in the unit tests falls back today, the same
ratio the 2026-09-01 adversarial sweep found (39 of 162). The 478 spread
over 279 distinct reject tags (438 body, 48 constructor, 24 resumable, 10
top-level); the largest tag, `decl.slot_type`, carries 35. There is no
head to attack from this side either. The files with the most breaking
programs are `test_thir_containers.py` (26), `test_thir_wave_asdict.py`
(25), `test_fallback.py` (22, by design), `test_thir_tuples.py` (16),
`test_thir_resumable.py` (15).

### The plan

Triage by DYNAMIC verdict, not by file, and only after the cutover has
made the oracle question moot:

1. **Reject pins and breaking programs are the inventory.** Every one of
   the 478 breaking programs plus the 197 explicit reject pins is a shape
   the cutover turns into a compile error. Each ends as one of: supported
   (then it belongs in a corpus case with exec and cpy), a deliberate
   diagnostic (then a diagnostic case with `# tpyc: error(...)`), or a
   filed defect. This list is the unit-test side of Phase 0's fix list
   and should be worked from `program_verdicts.json`, not rediscovered.
2. **Routing programs with render claims consolidate into cases.** The
   1,420 routing programs carry no semantic evidence today (nothing ever
   built their C++). Dedupe by shape and fold the distinct shapes into a
   small number of ordinary cases, many shapes per case, so they gain
   exec and CPython parity. MEASURED (`shapes_unit.py`, which collects the
   programs itself, against a `THIR_SHAPES_JSON` dump of a comp-only corpus run at `c8c9268ac`;
   key = `thir/shape.py`'s body signature, which is callable kind +
   construct vocabulary + param/return type families):

   | | shapes |
   |---|---|
   | corpus, distinct body shapes | 5,177 |
   | unit routing programs, distinct body shapes | 1,484 |
   | of which already in the corpus | 615 |
   | of which NOT in the corpus | 869 |
   | routing programs whose every body shape is corpus-covered | 534 of 1,420 |

   By construct vocabulary alone (dropping the type families) 476 of
   1,029 unit shapes are still absent from the corpus. So the
   expectation that the corpus already covers most of what the unit
   tests exercise is wrong: the consolidation has real coverage value,
   and the 869 uncovered shapes are the ones worth a case. That is
   still far fewer cases than programs, since one case can carry many
   shapes.
3. **Routing-only and identity-only tests are deleted** once their shape
   is in a case. `_assert_byte_identical` and
   `_assert_routes_byte_identical` are removed from `testutil.py` with
   the emitter; the surviving helper is "lower this module and hand back
   the THIR" for structural tests.
4. **Fallback-tally tests go with `fallback.py`**; faces tests follow the
   `faces.py` decision.
5. **The 710 structural tests stay** (arg-table registry invariants, node
   `__post_init__` contracts, validator rules, dump format, the
   `hidden_call` reflection guards) and get the same enum/typing pass as
   the nodes they test.
6. **Fragments need a per-test pass.** The 2,954 unprobeable strings are
   composed at runtime from class-level prefixes; the conversion tooling
   has to evaluate the test's fixture, not scan the file. That is the
   expensive part of the conversion and the reason not to do it file by
   file: run the classifier at pytest-collection time (a fixture that
   records the final source each helper receives) and it becomes exact.

Do not convert one-to-one. The corpus has 3,767 cases and a full run is
about five minutes on the remote host; 1,900 more cases would double it
for shapes the corpus mostly already has.
