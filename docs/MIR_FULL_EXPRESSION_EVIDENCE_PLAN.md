# Full-expression storage correspondence

Status: implemented, reviewed and verified after the coverage refresh at
`d4827a6abb`. This is a localized extension of the internal evidence adapter,
with no change to source admission, diagnostics or generated C++.

## Observable contract

For a hook-free scalar-field record, these already compile:

```python
def read(value: int32, flag: bool) -> int32:
    result = Cell(value).value if flag else Cell(7).value
    return result
```

The C++ remains a direct expression:

```cpp
int32_t result = flag ? Cell(value).value : Cell(7).value;
return result;
```

Only the selected constructor runs. Its object expires at the expression
boundary; the scalar result remains usable. MIR already models those exact
construction and end events. The storage adapter now connects the recorded
THIR backing to that MIR object and requests the existing lifetime proof.

The invariant is: every demanded full-expression THIR backing maps to the
exact storage root allocated for that constructor by the ordinary MIR builder,
and certification checks that root's existing lifetime without extending it.

## Existing mechanism and bounded changes

Follow the named-argument backing path in `mir/lower.py`: the builder publishes
an identity-keyed source-to-place map; `mir/storage_adapter.py` checks complete
correspondence and submits the demanded roots to `mir/storage_evidence.py`.

1. Generalize the map's key annotation from `THIRArgTemp` to `THIRExpr`.
   In the existing constructor-temporary builder arm, record the newly
   allocated storage place. Do not reconstruct roots by name or lower twice.
2. Let represented `FULL_EXPRESSION` backing reach the adapter's existing
   evidence path. Preserve uncovered facts, missing/pruned correspondences,
   exact request identity and the distinct operation-proof contract.
3. Reuse the existing record construction, full-expression regions, scope-end
   checks, definitions and escape analysis. No planner, solver, source
   lowering, emitter, runtime or summary-contract extension is planned.

`_Coverage.temporary` and `MIRDefinitions` continue to require complete,
hook-free scalar-field constructor facts. Select-slot backing stays uncovered.
The work neither proves custom cleanup nor promotes storage to an outer scope.

## Scope and sibling checks

| Position or sink | Intended coverage or retained boundary |
|---|---|
| Free function, ordinary method, constructor tail | Existing covered scalar reads, assignments, returns and discards |
| Lazy bool/ternary operands | Existing selected-arm construction and shared expression end |
| While condition | Existing repeated activation, including the final false evaluation |
| Ordinary loop body | Existing expression regions only; range/native loop heads do not expand |
| Local/parameter-derived borrowed operations beside temporaries | Both evidence demands compose in the existing operation proof |
| Field assignment | Existing scalar writes only; reference-field escape channels stay outside coverage |
| Constructor member/base initializer lists | No new coverage; retain existing route checks |
| Module/global, closure, comprehension, match | Existing body/escape gaps remain (M3 W2/W5) |
| Generator/async | Existing frame/resumption gaps remain (M3 W4) |
| With, try/finally, error-return | Existing cleanup/exceptional-flow gaps remain (M3 W3) |
| Generic/overloaded callable | Existing definition/route restrictions remain |

The producer is a plain record with supported bool/int32 fields; readonly
access does not change its duration. Owning aggregate, nested record,
container, str/bytes, Ptr/Span and Box/Rc producers do not gain coverage.
Tuple, Optional and union holders are lifetime-analysis siblings: existing
unsafe internal witnesses must still report retained references when the
temporary ends. This does not extend tuple-language support or allow a source
borrow of a constructor temporary where lowering currently rejects it.

## Validation and delivery

Use existing internal fixtures in `test_expression_temporary_lower.py`,
`test_expression_temporaries.py` and `test_storage_adapter.py`; compiler tests
must not reach into `tests/cases`. The existing source case
`control_flow/record_expression_temporaries` supplies emitted C++ and runtime
parity coverage. Add a source section only if an observable boundary is missing.

- Certify counterparts of all four audited bodies: `free`, `condition`,
  `Runner.read`, `Runner.__init__`. Assert exact backing/root correspondence,
  expression-region duration and request identity, not only a verdict.
- Exercise scalar declaration/assignment/return, discard, lazy operands,
  multiple temporaries at one expression end and loop reactivation.
- Mix named argument backing, full-expression backing and borrowed operations
  in one covered body; all demanded roots must participate in the proof.
- Keep missing/pruned backing, malformed or stale facts, missing definitions,
  unsupported constructor effects/types, order-sensitive operands and select
  backing uncovered or invalid as their existing contracts require.
- Feed existing unsafe retained-reference witnesses to the shared evidence
  API for record, tuple, Optional and union holders. Never run dangling C++.
- Re-run the same audit sample and check the exact verdict transitions,
  retaining every body and proof requirement in the comparison.

Expected existing snapshot churn: zero generated C++, diagnostics or output.
The change follows the Pitfalls constraints on silent copies, hidden
allocations, view/ownership distinctions, readonly access and conditional
evaluation by preserving all emitted storage. Position and holder symmetry
are pinned above; unsupported generic twins stay explicitly outside scope.
No runtime templates or numeric behavior change. Internal coverage reasons
remain internal, so no diagnostic text, warning or Python rejection is added.

Delivered as one reviewed implementation commit. Targeted proof tests pass;
the final `rpytest --force-exec` run passed 11,057 tests with 23 skips and
4,245 C++ cases built and run, with no execution-cache skips. Existing
snapshots required no updates. Specialist review and the independent
readiness retrospective have no outstanding findings.
The repeated 37-case audit confirms all four isolated bodies now certify:
18 of the unchanged 142 recorded-proof user bodies certify, 124 remain
uncovered and none reports a conflict. No compile, workspace or adapter
exceptions occurred. This does not establish production enforcement or M3 completion.
Confidence: high; the measured gap is correspondence for storage already
represented by MIR, rather than a missing lifetime model.
