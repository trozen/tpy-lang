# M3.11: constant boolean CFG edges

Status: approved 2026-09-21; implemented.
This continues [M3.9/M3.10](MIR_M3_DECLARATIONS_PLAN.md) on
`mir-m3-constant-cfg`, with one final squashed commit for the user's merge.

## Contract

```python
def example() -> int32:
    while True:
        value = 1
        break
    return value
```

This already compiles and returns 1. Its emitted C++ predeclares `value`,
assigns it inside `while (true)`, breaks, and returns it. M3.10 reports this
body as MIR-not-covered because structural definite assignment includes an
impossible zero-trip edge. M3.11 removes that edge during MIR construction.
Source acceptance, diagnostics, emitted C++ and checker authority do not change.

Invariant: a structural edge is removed only when an already evaluated,
single-definition boolean expression temporary proves its condition; all
reachable evaluations and their storage identities remain in place.

## Construction and finalization

1. Keep the complete THIR coverage check before building MIR, including dead
   arms. Unsupported syntax or missing metadata cannot disappear into a
   successful analysis because its containing arm is unreachable.
2. Remember exact boolean literals and `not` chains only at the builder's
   single-definition expression-result helper. Do not derive facts from
   mutable locals, parameters, globals, comparisons, presence tests or
   multiple-definition select destinations. Integer 0/1 is not a boolean fact.
3. Use one branch helper for statement `if`, `while`, and expression selection
   (`and`, `or`, ternaries and their existing THIR forms). A known condition
   produces a direct jump; all other conditions retain both successors.
   Evaluation occurs before the helper: `(flag := True)` still writes `flag`.
4. Extend existing reachable-block finalization to retain only referenced
   slots, plus parameters, globals, receiver initialization and transitive
   alias-source dependencies. Include record-write owner metadata. Preserve
   original IDs; gaps are valid. Retain reached region entries and their
   existing ancestry, without moving storage or fabricating scopes.
5. Keep strict validation and downstream consumers on this same structural
   graph. Missing definite assignment still yields MIR-not-covered through
   the dedicated proof-failure exception; malformed MIR still raises.

The precedent is the builder's existing reachability cleanup and shared
selection lowering. Presence analysis already understands boolean constants,
but runs after structural definite assignment; moving it before validation
would introduce an unnecessary dependency. No general optimization pass or
second feasible-edge graph is introduced.

## Factored scope

A cell is covered only when every applicable axis and existing operation gate
admits it. Excluded cells remain in the M2-M5 backlog in `TODO.md`.

| Axis | Covered | Deferred or unaffected |
|---|---|---|
| Position | Existing synchronous monomorphic free functions, methods, constructor tails; if/elif/else, while/else, lazy expressions | Module bodies, generic bodies, generators, async, comprehensions, closures, context managers, try/finally, error-return, match, for-loops retain their existing gates |
| Shape | Exact bool/not conditions; existing scalar, record, tuple, Optional, union, readonly and Own operations within reached arms | No new forms for str/bytes, Ptr/Span, Box/Rc, containers, owned aggregates or default-constructed wrapper hoists |
| Slot | Existing locals, parameters, scalar returns, field reads/writes, private backing and scalar global facts | No new parameter/return forms, field replacement, container elements, borrowed escapes or frame slots |
| Proof | Literal/not expression temporaries, including walrus RHS temporaries | Comparisons, arbitrary truthiness, mutable-variable propagation, select-result propagation and logical identities |

## Tests and pitfalls

- Promote the existing constant-loop fixtures in `test_hoisted_declarations`
  from uncovered to covered, execute their MIR for free/method/constructor
  positions and retain direct strict-validator negative tests.
- `test_constant_cfg` pins literal/not edges, break versus loop-else,
  nontermination and continue, lazy reached/skipped writes, walrus guard
  writes, dynamic guards and comparisons, and dead storage/region cleanup.
  Assert MIR topology and analysis results, not merely runtime output.
- Keep whole-body unsupported-dead-THIR tests and the existing scope,
  presence, retention and dependency suites. Check sparse IDs and unused
  signature parameters survive finalization.
- Conditional operands remain guarded; pruning removes unreachable code,
  never reachable instructions with effects. Select destinations are not
  single-definition temporaries.
- Alias-versus-copy and tuple-versus-scalar: retain existing operations and
  metadata; the reached record/tuple witness mutates then reads through the
  alias. Readonly access and storage duration remain producer facts.
- Position symmetry: all admitted callable forms share the builder. Generic
  and resumable positions retain their explicit coverage boundaries.
- Views, allocations, runtime template kinds, const loop variables and C++
  readability are unaffected: no emitter/runtime change or new iteration form.
- Diagnostics remain unchanged, including copy warnings; a MIR coverage gap
  is not a rejection of valid source Python or a new language lifetime rule.

The independent design-time CPython parity assessment found no divergence
under these restrictions. No new snippet test is planned: this step changes
analysis only, and the source/THIR-to-MIR unit fixtures directly exercise its
contract. Existing end-to-end snapshots guard unchanged emission.

## Completion

Update the MIR status in `LANGUAGE_FEATURES.md`, `IR_DESIGN.md`,
`MIR_ANALYSIS_PLAN.md`, the declaration plan and `TODO.md`. Run targeted MIR
tests during development, the specialist review and readiness gates, and one
full forced suite after all changes. Existing snapshot changes require
separate approval. Leave one squashed commit on a branch, without merging or
pushing master.
