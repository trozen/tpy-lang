# Internal storage-proof coverage audit

This audit follows steps 1-2 of the
[storage-origin design](MIR_STORAGE_ORIGIN_DESIGN.md). It measures the internal
API without changing source admission, diagnostics, generated C++ or production
checking. The implementation baseline is `442e0a3571`.

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

## Measured sample

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

## Known boundaries

Assume a hook-free scalar-field `Cell` and a known borrow-returning function
`observe(cell: Cell) -> readonly[Cell]`.

| Source shape | Current internal boundary |
|---|---|
| `saved = observe(Cell(value)); return saved.value` | Named backing can certify in a fully covered body |
| `saved = observe(owner); return saved.value` | The binding records an obligation, but no-local-backing discharge is absent |
| `alias = saved` | A stable-looking name can preserve a temporary dependency; the sink inventory alone does not prove stability |
| `return owner` | Borrowed returns have no per-operation inventory entry; the MIR core has a separate return-escape check when invoked |
| `saved = owner if flag else Cell(2)` | Select backing is recorded but its placement is not connected to this certificate |
| `return Cell(value).value` | Full-expression backing is recorded but not connected to the adapter's lifetime evidence |
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

## Recommended next implementation

Complete the operation contract for the already-supported borrowed-record
local bindings, reseats and returns in ordinary bodies, still internally:

1. Inventory the supported borrowed sinks independently of whether their
   values syntactically look stable or introduce new temporary backing.
2. Bind each obligation to its exact MIR operation and discharge it using
   whole-body origin, lifetime and escape evidence. Support positive evidence
   for parameter-derived origins without requiring newly materialized
   backing; successful origin resolution alone is not a lifetime proof.
3. Keep missing origins, unrepresented operations and missing facts uncovered;
   preserve exact-body/request identity checks and whole-body coverage.
4. Test direct and call-mediated aliases, holder reseats, joins and returns,
   including safe parameter-only bodies and retained local backing.

The [borrow-operation plan](MIR_BORROW_OBLIGATION_PLAN.md) is the approved
bounded implementation design for this recommendation. The measurements above
remain the pre-extension baseline; they do not measure the new contract.
It closes a contract prerequisite rather than claiming the largest immediate
increase in the sample's certification count; whole-body planning is the
largest measured coverage limitation.
Select-slot/full-expression producer mapping, richer body coverage and the
production output gate are separate subsequent work. None of these steps
authorizes broadly rejecting existing safe programs or changes the lifetime
policy for the separately tracked escape defects.
