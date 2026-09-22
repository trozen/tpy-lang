# Named argument temporaries: dependency investigation

Status: investigated after M3.26/M3.27 on base `b528fbf2c0`.
Implementation scope is not approved. This records a W1 dependency in
[the M3 completion plan](MIR_M3_COMPLETION_PLAN.md), not another completed
increment. Tuple completeness remains separate.

## Concrete boundary

For a hook-free `Cell` with one `int32` field and a scalar-only constructor:

```python
def read(cell: Cell) -> int32:
    return cell.value

def eager(value: int32) -> int32:
    answer = read(Cell(value))
    return answer

def lazy(value: int32, flag: bool) -> int32:
    return read(Cell(value)) if flag else 0
```

Both callers compile today. A compiler probe confirmed these relevant forms:

```cpp
Cell __tmp_1 = Cell(value);
int32_t answer = read(__tmp_1);
return answer;
```

```cpp
std::optional<Cell> __tmp_2;
return flag ? (__tmp_2.emplace(Cell(value)), read(*__tmp_2)) : 0;
```

THIR represents the arguments as `THIRArgTemp(THIRCtorCall(...))`.
`--dump-mir` covers `read` but reports both callers as not covered. Ordinary
calls are unsupported before MIR reaches their argument storage.

These are block-lived locals, unlike the full-expression temporaries in
[M3.22/M3.23](MIR_M3_EXPRESSION_TEMPORARIES_PLAN.md). The optional wrapper
exists on both paths; its payload is initialized only on the selected path.

## Evidence and sibling survey

- `tpyc/thir/nodes.py`: `THIRArgTemp` carries initialization, argument form,
  move/address wrapping and audited movability, but no semantic placement
  fact. Its generated number belongs to emission, not semantic identity.
- `tpyc/thir/emit.py` and `tpyc/codegen_cpp/context.py`: argument rendering
  registers pending declarations; statement emission determines their block.
  An if-condition temp can live through both arms. A temp-bearing while
  condition is emitted inside a restructured loop with per-iteration lifetime.
  An elif can acquire a synthetic else block. `banks_in_region` controls
  optional backing with deferred initialization; comprehension flushes can
  relocate anonymous declarations into a nested iteration scope.
- `tpyc/thir/validate.py`: temps require audited call-argument contexts, with
  a separate generator-factory receiver seam. A fabricated temp under an
  arbitrary field read is not a valid production witness.
- `tpyc/mir/definitions.py`: verified constructors have bool/int32 parameters
  and no body effects. Reference-bearing constructor arguments do not provide
  a shortcut around call support. Scalar Own-argument temps are a sibling,
  not a witness for named record backing.
- [M2.11/M2.12](MIR_CALL_CAPTURE_PLAN.md) preserves qualified callee identity
  and signatures, explicitly without proving effects, exits or provenance.

Existing tracked hazards include `BUGS.md#const-ref-arg-temporary-stored`,
`BUGS.md#native-stub-declares-no-param-retention`,
`BUGS.md#cond-walrus-before-hoisted-temp`,
`BUGS.md#cond-operand-unspelled-arg-temp-rejects`, and
`BUGS.md#resumable-delegate-temp-source-dangles`. This investigation does not
fix them or assume every possible source position already lowers.

## Recommended order

Do not add isolated temp machinery and describe it as source coverage.
First design the M3/M4 call interface, as allowed by the existing
[analysis plan](MIR_ANALYSIS_PLAN.md). This is architectural work requiring
approval before implementation.

The [proposed first call-summary batch](MIR_CALL_SUMMARY_INTERFACE_PLAN.md)
specifies the local evidence, workspace orchestration and bounded consumer.

1. Define how MIR receives finalized callee effects, returned/retained
   dependencies and exit behavior. Reuse semantic identities and existing
   summary authorities. Pending, unknown and known-empty stay distinct;
   readonly parameters or signatures alone do not prove harmless calls.
   Do not create a second ad hoc purity scan.
2. Verify a useful first consumer: direct ordinary calls with supported scalar
   results, no escaping argument aliases, supported exits and hook-free
   construction. The summary producer must actually prove this slice.
3. Integrate eager and deferred named storage using positive THIR placement
   and initialization facts shared with emission. Reuse MIR regions,
   engagement, activation and retained-dependency machinery. Do not infer
   scope from C++ strings or give every argument temp expression lifetime.

These are dependency stages, not promised commits or new milestone numbers.
The interface design must establish a useful batch before choosing commits.
M3 need not be declared complete before M4 interface work begins. Source
acceptance, C++ emission and checker authority should remain unchanged.

## Scope and verification obligations

This investigation admits no new cells. The next design must cover each axis:

| Axis | First candidate | Separate obligations |
| --- | --- | --- |
| Caller | Ordinary function/method/constructor body and supported CFG | Module, closure, match: W2/W5; comprehension: W2; cleanup/context manager/error-return: W3; generator/async: W4 |
| Callee | Direct resolved ordinary function | Methods, reference-argument constructors, callbacks, protocols, native and generic instantiations need identities and summaries in M2/M4 |
| Shape | Hook-free scalar-field record temp and bool/int32 result | Tuple/Optional/union payloads, owning transfers, aggregates, str/bytes views, Ptr/Span, Box/Rc and containers: W1/M2/M4/W5; retain readonly access |
| Use | Eager local and lazy optional-backed call argument | Returned aliases, fields, container inserts, globals and captures require escape provenance; repeated activation must retain dependencies |

Tests must distinguish wrapper existence from engagement, skipped construction,
block end from expression end, early exits and fresh loop activations.
Unknown/incomplete summaries leave bodies uncovered. Internal retention tests
need scalar, tuple and wrapper holders; they do not admit source escapes.

The pitfalls checks require mutation or `@nocopy` at admitted reference
boundaries, singleton/mixed tuple controls, and position/generic siblings.
Probe evaluation order, including reordered keyword arguments and receiver
effects, rather than assuming parameter order or temp numbering proves it.
Use observable selected-branch work to test laziness. Hook-free scalar-result
calls can match CPython despite longer C++ lifetime; finalizers, escapes and
exceptional cleanup need separate proofs. Independent parity assessment agrees
with this bounded contract; runtime parity was not executed in this investigation.

No view copies, allocations, runtime-template or loop-const changes are
proposed. No new source diagnostics or warnings are proposed; missing MIR
coverage is not a language rejection. Existing snapshots should remain
byte-identical. Reuse `short_circuit_arg_temp`, `cond_operand_arg_temp_families`
and `chain_compare_arg_temp` where adequate; add a condensed source case only
for missing observable coverage, alongside internal lifetime tests. Update
`LANGUAGE_FEATURES.md` when coverage lands.
