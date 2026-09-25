# Internal storage-proof coverage audit

This audit follows steps 1-2 of the
[storage-origin design](MIR_STORAGE_ORIGIN_DESIGN.md). It measures the internal
API without changing source admission, diagnostics, generated C++ or production
checking. The original implementation baseline is `442e0a3571`; the refreshed
measurement below uses `d4827a6abb` after explicit borrowed-operation proofs
and the subsequent master integrations landed.

## What the results mean

Count emitted user bodies separately from library bodies and from source
programs. For each eligible emitted function or constructor, distinguish:

- Published facts with recorded backing or borrowed-sink obligations. Only
  this population belongs in the Certified / Conflict / Not covered table.
- Published facts with an empty inventory. This means no obligation was
  recorded by this bounded collector, not that all borrows are safe.
- Unpublished facts, unsupported body kinds, or bodies not emitted.

An obligation with no recorded backing is distinct from an empty inventory.
Unknown origins cannot be treated as durable external storage. Count a gap
reason once per body; a body can have multiple reasons, so reason counts do
not add up to the number of uncovered bodies. Keep unexpected validation
errors separate from ordinary coverage results.

The inventory omits some producers and sinks, so even the recorded-body
certification rate is not a percentage of programs or borrowed operations
proved safe. A Conflict count of zero in accepted regression cases would not
establish the absence of lifetime defects. Internally constructed unsafe IR
witnesses remain separate from this source-corpus measurement.

## Original measured sample

At the baseline, select every `tests/cases/**/src/main.py` whose case has an
`expected/output.txt`, excluding any `error_` or `panic_` path component, and
matches either rule:

- Its group is `calls`, `control_flow`, `records` or `tuple`, and its case
  basename contains `argument_storage`, `borrowed_return`,
  `expression_temporar`, `select` or `ternary`.
- Its basename contains `named_argument` or `arg_temp`, in any group.

All 34 matches were measured, without a case-count cutoff. Each compilation
used its original entry path and layered `default_int` option, collected
C++/THIR from every compiled module, and built constructor definitions and
call summaries before requesting evidence. Library bodies were available to
summary resolution but counted separately. No source test program or generated
C++ was executed by this audit; these are already passing regression cases.
All selected cases resolved to `int32`; no frontend-plugin exclusion was
needed.

All 34 cases compiled and emitted successfully. No unexpected compile,
emission, workspace or adapter exception occurred.

| Emitted user-body population | Count |
|---|---:|
| Functions / methods / constructors | 223 / 36 / 59 |
| Module initialization / resumables | 34 / 15 |
| Total body instances | 367 |
| Published facts, recorded proof requirement | 117 |
| Published facts, empty recorded inventory | 235 |
| Unpublished facts (resumables) | 15 |

The ordinary-body adapter was called for 313 bodies. The 34 module-init,
15 resumable and 5 generic-callable bodies were kept outside that adapter
population. All 117 recorded-proof bodies were eligible for an adapter call;
the other 196 calls had empty inventories and did not receive certificates.
The generic-callable exclusions apply to functions and methods. Emitted
generic constructors remained in the constructor population: for example,
`Boxed.__init__` in `generics/generic_arg_temp_instantiated_slot` had an empty
inventory and returned Not covered for missing record layout. It does not
affect the 117-body recorded-proof denominator.

| Verdict among the 117 recorded-proof bodies | Count |
|---|---:|
| Certified | 5 |
| Conflict | 0 |
| Not covered | 112 |

The certified bodies were `Caller.__init__` in `calls/borrowed_argument_storage`
and `update`, `nested`, `Runner.method`, `Runner.__init__` in
`control_flow/for_argument_storage`. These include ordinary functions, a
method and constructor tails; this is not a claim that their surrounding
programs are wholly certified.

Selected gap reasons, counted once per recorded-proof user body:

| Reason | Bodies |
|---|---:|
| Complete temporary plan missing | 97 |
| Argument backing has no temporary plan | 49 |
| Callee lacks a finalized known summary | 22 |
| Borrowed-expression obligation has no modeled backing | 21 |
| Select-slot placement is not planned | 21 |
| Unsupported local type or form | 19 |
| Full-expression backing is not connected | 5 |

The first two rows overlap heavily; neither is an independent count of bugs.
Both adapter gaps and underlying evidence gaps are included. Unsupported
statements, expressions, parameters and record fields also occur. The sample
therefore does not justify applying the current proof as a blanket gate to
existing accepted code.

A disjoint breakdown is useful for prioritization: 97 bodies lack a complete
plan; among the remaining 20, five certify, 12 lack a known callee summary,
two hit unsupported binary operations, and one lacks a resolved ordinary
callee. No improvement to one of these boundaries automatically closes the
others.

There were also 2,311 emitted library-body instances across the compilation
requests, including repetitions of the same library definitions. They are
not unique library coverage. Of 62 with recorded proof requirements, 40
reached the ordinary-body adapter and were Not covered; 22 were outside its
supported routes. They are excluded from the user-body table above.

## Refreshed measurement: explicit operations

On 2026-09-24, rerun the same selection rule at `d4827a6abb`. It selects 37
cases: the original 34 plus `control_flow/select_join_types`,
`control_flow/select_pointer_slots` and `control_flow/select_value_slots`.
All use `int32`; none requires a frontend plugin. All 37 compile and emit;
there are no compile, workspace or adapter exceptions. No generated program
is executed for this measurement.

These are emitted THIR cache entries per compilation request, not every C++
function: nested defs, lambdas and generator expressions can remain embedded
in their enclosing body. Library instances remain separate and can repeat
across requests. Unsupported routes remain visible in the population.

| Emitted user-body population | Count |
|---|---:|
| Functions / methods / constructors | 275 / 40 / 65 |
| Module initialization / resumables | 37 / 17 |
| Total body instances | 434 |
| Published facts, recorded proof requirement | 142 |
| Published facts, empty recorded inventory | 275 |
| Unpublished facts | 17 |
| Adapter calls | 375 |
| Recorded-proof bodies: Certified / Conflict / Not covered | 14 / 0 / 128 |

All 142 recorded-proof user bodies reach the adapter. The excluded routes
are 37 module initializers, 17 resumables and five generic callables. Generic
constructors retain the baseline treatment: they reach the adapter, whose
coverage checks still apply. The other 233 adapter calls have empty recorded
inventories and receive no certificate.

The original 34-case cohort still has 367 body instances. Match by case,
module, category, callable name and duplicate occurrence, and verify the
source locations of changed rows. Its transitions are:

| Original state | Refreshed state | Bodies |
|---|---|---:|
| Certified | Certified | 5 |
| Not covered | Certified | 3 |
| Not covered | Not covered | 108 |
| Not covered | Empty recorded inventory | 1 |
| Empty recorded inventory | Certified | 6 |
| Empty recorded inventory | Empty recorded inventory | 229 |
| Unpublished | Unpublished | 15 |

The three formerly uncovered bodies now certifying are `Cell.via_method` and
`Caller.__init__` in `calls/borrowed_return_effects`, and `Acc.larger_n` in
`control_flow/ternary_self_arm`. Six newly inventoried borrowed-return bodies
also certify: `observe` and `choose` in `calls/borrowed_argument_storage`, and
`identity`, `choose`, `forward`, `observe` in `calls/borrowed_return_effects`.
The emptied row is `give_or` in `control_flow/ternary_mixed_category_alias`:
master changed its owning select lowering, so it no longer records select
backing. This is an inventory change, not a newly proven lifetime. Accordingly,
the old `5/117` and new `14/142` are not comparable certification rates.

| Backing population | Bodies | Certified | Not covered |
|---|---:|---:|---:|
| Named argument backing | 74 | 5 | 69 |
| Select-slot backing | 27 | 0 | 27 |
| Full-expression backing | 5 | 0 | 5 |
| Borrowed operations without recorded backing | 36 | 9 | 27 |

Backing kinds are potentially overlapping categories, not a general partition
of obligations. In this sample they happen to sum to the recorded population.
No conflict was reported; this does not establish safety of uncovered bodies.

### Observed blockers and prioritization

An absent temporary plan is not necessarily incomplete planning:
`prepare_temporaries` also returns `None` for a supported body with no named
argument placements. All nine certificates without recorded backing have no
plan. The old blanket "complete plan missing" count is no longer a meaningful
prioritization metric.

Of the 74 argument-backed bodies, 54 actually lack the required argument
placement plan. A read-only replay of the planner identifies these first
stopping nodes: method calls (13), printing (11), string literals (9), calls
with `cpp_template` metadata (5), container literals (5), for-each loops (3),
chained comparisons (3), and one each of Optional pointer arguments, raises,
calls with template arguments, comprehensions and native calls. These are
different missing capabilities; relaxing planning alone supplies neither
MIR expression coverage nor effects for the called functions.

Twenty-three recorded-proof user bodies encounter an unavailable finalized
callee summary. Following their ordinary-call dependency graph exposes
summary storage/value-shape restrictions for 12 bodies and unsupported
parameter types for ten; other roots include statements, expression shapes,
metadata, record fields and recursion. Counts overlap within a caller. This
does not establish that extending one root would make every caller certify.
The MIR coverage walk stops at its first unsupported operation, so every gap
table describes observed blockers, not an exhaustive list of blockers or a
prediction of the gain from removing one.

Four full-expression bodies already have covered MIR and only lack the
backing-to-proof connection: `free`, `condition`, `Runner.read` and
`Runner.__init__` in `control_flow/record_expression_temporaries`. The fifth,
`main` in `calls/borrowed_return_effects`, also has a callee-summary blocker.
This makes full-expression correspondence a bounded next contract completion;
it does not justify widening the many unrelated planning cases as one patch.

The refreshed library population has 2,820 body instances, including 77 with
recorded proof requirements. Fifty of those reach the adapter and remain
uncovered; 27 have excluded routes. None certifies, and no adapter exception
occurred. These repeated library instances are not unique library coverage.

## Full-expression correspondence follow-up

The [approved adapter extension](MIR_FULL_EXPRESSION_EVIDENCE_PLAN.md) was
measured with the same 37-case sample after connecting constructor storage.
All populations, recorded obligations and route exclusions are unchanged.
All 37 cases compile and emit, with no workspace or adapter exceptions.

| Verdict among the same 142 recorded-proof user bodies | Before | After |
|---|---:|---:|
| Certified | 14 | 18 |
| Conflict | 0 | 0 |
| Not covered | 128 | 124 |

The only changed verdicts are the four targeted bodies in
`control_flow/record_expression_temporaries`: `free`, `condition`,
`Runner.read` and `Runner.__init__`. Each now certifies its actual constructor
storage roots through the existing expression-region lifetime proof. The
fifth full-expression body, `calls/borrowed_return_effects::main`, stays
uncovered because of its independent callee-summary blocker. Argument-backed,
select-backed and operation-only verdicts, and all library verdicts, are
unchanged. These are body-level certificates, not whole-program coverage or
production checking.

## Select-slot correspondence follow-up

The [select-slot extension](MIR_SELECT_STORAGE_PLAN.md) was measured before
and after implementation on the tree incorporating master `98a11d353c`.
The selection rule still finds the same 37 cases. That master revision
expanded `select_join_types`, so this population has 446 emitted user bodies:
286 functions, 40 methods, 65 constructors, 37 module initializers and 18
resumables. Of these, 142 require proof, 286 have an empty recorded inventory
and 18 have unpublished facts. The ordinary adapter receives 386 bodies;
the exclusions remain module initialization, resumables and five generic
callables. Compare exact body identities within this refreshed population,
not the earlier 434-body denominator.

| Verdict among the same 142 recorded-proof user bodies | Before | After |
|---|---:|---:|
| Certified | 18 | 18 |
| Conflict | 0 | 0 |
| Not covered | 124 | 124 |

Every body's inventory state and verdict is unchanged. All 37 cases compile
and emit, with no unexpected compile, workspace or adapter exceptions.
There are 27 select-backed bodies, all still uncovered: 26 lack a complete
temporary plan, and one has storage inside a nested body. Other blockers
overlap these counts, including unsupported local forms in 19 bodies.
Connecting select roots does not remove those independent body boundaries.
The 2,820 library-body instances also retain their verdicts: 77 require proof,
50 reach the adapter and remain uncovered, and 27 use excluded routes.

The new correspondence is established separately by compiler-local witnesses:
safe plain-record ternaries certify, construction occurs only on the selected
arm, and shared mutations remain visible. The known plain branch-local alias
escape is a Conflict with no gaps. Internal retained tuple/Optional/union
holders conflict at the actual lowered select root. These witnesses are not
additional source-corpus certificates; repeated while-head emplacement,
record and/or and source wrapper sinks remain explicit boundaries. The audit
executes no generated program, including the known unsafe escape.

## Current known boundaries

Assume a hook-free scalar-field `Cell` and a known borrow-returning function
`observe(cell: Cell) -> readonly[Cell]`.

| Source shape | Current internal boundary |
|---|---|
| `saved = observe(Cell(value)); return saved.value` | Named backing can certify in a fully covered body |
| `saved = observe(owner); return saved.value` | Parameter-derived operations can certify without new backing when the whole body and callee summary are covered |
| `alias = saved` | Supported typed record bindings retain an explicit operation and all dependency origins; lifetime evidence must cover the whole body |
| `return owner` | Eligible borrowed returns have exact operation correspondence and whole-body escape evidence |
| `saved = owner if flag else Cell(2)` | Bounded hook-free movable scalar-field constructors map to planned select backing and can certify in a fully covered body |
| `return Cell(value).value` | Covered hook-free scalar-field constructor backing maps to its exact expression-region root and can certify |
| Adding `print(saved.value)` to a covered body | Whole-body temporary planning and MIR coverage can become unavailable |

These are different gaps. Complete sink inventory does not establish physical
placement, and complete placement does not establish call effects or escape
coverage. The known select-slot escape remains separately tracked in
[BUGS.md](../BUGS.md#select-slot-alias-escape-unchecked).

## Production entry points

`Compiler.generate_code_and_thir` returns C++ and the exact THIR context from
one emission. It is suitable for measuring emitted bodies. A tolerant
`collect_thir` survey can instead return a partial context; missing bodies must
not silently disappear from its denominator.

Production output is still released through several paths: file generation,
string generation, REPL file publication and extension glue generation.
None calls `certify_thir_storage`. The debug MIR collector also excludes
module initialization, resumables and generic/overloaded bodies, and its CLI
workspace collects user modules only. Production integration must not inherit
that display filter as a completeness assumption.

The design's shared generation request remains necessary before enforcing an
admission gate across these paths. Measuring a function adapter does not
validate output-publication coverage.

## Remaining work

The earlier recommendation, explicit borrowed-operation proofs, landed in
`d4827a6abb`; the [borrow-operation plan](MIR_BORROW_OBLIGATION_PLAN.md) records
that implementation. The subsequent
[full-expression storage correspondence](MIR_FULL_EXPRESSION_EVIDENCE_PLAN.md)
connects the constructor roots already allocated by MIR to recorded THIR
backing and reuses the existing lifetime evidence. No new placement or lifetime
rule was needed, and the four isolated bodies above now certify.

Bounded select-slot mapping now reuses that same proof. Broader planning/call
coverage, repeated select emplacement and production output gating remain
separate work. This sample still cannot justify a blanket
production gate or new rejections of existing safe programs.
