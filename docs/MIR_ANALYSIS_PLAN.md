# Analysis-only MIR: implementation plan

## Decision and boundary

On 2026-09-17 the user approved bringing analysis-only MIR forward before the
coupled callable contract, following `CALLABLE_CONTRACT_FEASIBILITY.md`.
That approves the sequence, not the proposed admission restrictions or every
implementation detail below. **M1's implementation scope was then approved and
is implemented in `tpyc/mir/`.** It is an internal API used by tests; normal
compilation, source acceptance and generated C++ are unchanged.

The first increment builds and verifies a real control-flow graph from a small
THIR subset. It does not check callable lifetimes yet. Later increments must
supply all six provisions in `CALLABLE_PROVENANCE_REQUIREMENTS.md` before that
consumer can become authoritative. Shared mutation stays legal; the eventual
conflict rule concerns invalidation of borrowed storage, not exclusive access.

## M1: internal scalar CFG foundation

This existing program illustrates the first supported body:

```python
def choose(flag: bool, x: int32) -> int32:
    result = x
    while flag:
        if x > 0:
            result = 1
            break
        flag = False
    return result
```

`choose(True, 3)`, `choose(True, -2)` and `choose(False, 7)` return `1`, `-2`
and `7`. Generated C++ remains the existing local variable, `while`, `if`,
assignment and return. MIR records entry, loop condition, conditional branches,
back edge and exit; `break` reaches the loop exit, not its normal `else` path.

**Invariant:** every successful lowering describes the complete admitted body
with typed binding/temporary identities, explicit evaluation order and valid
control-flow edges; a body outside that subset produces no usable MIR graph.

### Input and integration

Use the exact THIR returned by `Compiler.generate_code_and_thir()` in integration
tests (`tpyc/compiler.py`). This returns sources and their THIR context from one
emission. Do not re-lower a second approximation of the same function.
The builder itself takes a `THIRFunction` and an explicit body identity/kind;
it does not take an analyzer or codegen context.

There is no complete standalone THIR-module pass today. Ordinary functions
lower in `codegen_cpp/generator.py`; resumables lower during frame emission.
`collect_thir()` deliberately drives codegen for that reason. M1 introduces no
normal compilation hook or `--dump-mir` option. Its internal dump and integration
tests exercise the future analysis boundary without adding default compiler
cost or making partial analysis a language restriction.

New pattern: general MIR blocks and places do not exist. Reuse THIR's immutable
typed-node, explicit validator and deterministic dump conventions, not a parallel
parser, type inference pass or C++ emitter. The existing resumable CFG models
suspension/frame mechanics; it is an integration input for a later increment,
not a general semantic CFG to clone.

### Identity and operations

- A body identity includes its module and declaration identity; a short function
  name alone is insufficient. Within that body, allocate deterministic binding,
  temporary and block IDs. Names and source locations are display metadata.
  IDs are stable within the artifact and reproducible for identical input, not
  promised stable across source edits. Python object addresses are not MIR IDs.
- Parameter/local slots are mutable. Reassignment writes the same slot;
  expression temporaries have separate IDs. The admitted subset has one binding
  per source name, so a lowering-local name map can resolve reads to slot IDs.
  A read that cannot resolve locally is unsupported, not an implicit global.
- Store `TpyType` and the carried THIR form. In M1 admitted values have VALUE
  form. Do not infer ownership from VALUE, STORAGE, last-use flags or a C++ type
  string; there are no alias, ownership-transfer or loan verdicts in this slice.
- Operations are typed literal/read/comparison/not evaluation and scalar slot
  writes. A read produces a captured value, not a deferred slot read; a walrus
  yields its assigned value. Eager operand order is admitted only when
  unobservable, as specified below. Branching expressions
  write a common result temporary on each reaching arm; this is mutable-place
  IR, not SSA. Terminators are branch, goto and return.
- Preserve locations on operations and terminators for later explanations.
  Blocks and instructions contain no analyzer, parse-node or emitter references.

### Exact admitted subset

Only ordinary monomorphic synchronous free functions with `bool` and `int32`
parameters, local slots and results; a bare void return is also allowed.
Local declarations form an unconditional entry prefix. Their initializers may
contain admitted branching expressions. All later writes target those slots.

Expressions: literals and plain local/parameter reads; in-range integer literal
coercion to `int32`; builtin comparisons on the admitted scalars; boolean `not`;
boolean `and`/`or`; conditional expressions; and a plain walrus assigning an
already declared scalar slot. Support both boolean `THIRBinOp` short-circuit
nodes and same-boolean `THIRValueSelect` nodes. Reject representation-changing
flags, non-boolean selects, checked casts and user conversions from this slice.

Statements: scalar declarations and assignment, expression statements, return,
`if`/`else`, `while`/`else`, `break`, `continue` and no-op. Loop back edges
re-evaluate the condition. A `break` bypasses that loop's `else`; normal condition
failure enters it. `continue` targets the innermost loop's condition.

Not covered: arithmetic, including fixed-width operations that can trap;
dedicated chained-comparison nodes; branch/loop-created or hoisted declarations;
narrowing; calls (including otherwise scalar calls); fields, indexing, globals
and captures; any ownership/form conversion. Some THIR hoists carry only
`(name, cpp_type)`, so deriving a typed slot from them would parse C++ spelling.
The admitted operations cannot raise or require destructor cleanup. M1 makes
no claim about exceptional or suspension paths in other bodies.

The existing evaluation-order policy is still open (`TODO.md`,
`BUGS.md#subexpression-right-to-left-eval`). M1 must not model Python's preferred
order as a fact about C++ that does not enforce it. For an eager comparison with
a walrus anywhere in one operand, require the other operand to be a literal
(possibly its admitted literal coercion); otherwise return `MIRNotCovered`.
Thus `(n := 1) > 0` is covered, but `x < (x := 0)` and
`(x := 1) == (x := 2)` are not. Pure operands can be normalized left-to-right
because their order is unobservable. Lazy boolean/conditional edges retain
their guaranteed sequencing. This bounded syntactic gate avoids designing a
general effect analysis or settling the open language policy in M1.

### Coverage and verification

Lowering returns either a complete `MIRFunction` or `MIRNotCovered` with a source
location when available, node kind and reason. An unsupported node anywhere,
including an unreachable branch, makes the entire body not covered. There is
no partial-success graph, opaque no-op instruction or broad exception catch.
This is internal analysis coverage, not a compiler diagnostic or rejection of
the source. A future consumer must not interpret `MIRNotCovered` as no effects,
no loans, an empty summary or a passing safety check.

Invalid IR is a separate programmer error. The verifier checks unique IDs,
declared and type-compatible operands/destinations, valid block targets, one
terminator per block, boolean branch conditions, return type agreement, and
definite assignment on reachable paths (including merged expression temporaries
and loop back edges). Unsupported input is not a verifier failure.

### Files and tests

Package: `tpyc/mir/{nodes,lower,validate,dump}.py` plus `__init__.py`
and focused unit/integration tests in that directory. Parser, sema, typesys,
runtime, stdlib and C++ emission need no behavior changes. If inspection during
implementation finds that even this subset requires new semantic THIR metadata,
revisit the boundary rather than reading C++ strings.

1. Compile source fixtures owned by the MIR unit tests through the normal
   emission path and test graph structure from the collected THIR: sequential
   writes, nested branches, early returns, both loop exits, nested break/continue
   targets, and short-circuit/conditional expressions. Dumps pin stable IDs and
   source correspondence, not generated C++ details.
2. Test lazy evaluation observably with an existing-local walrus:
   `n = 0; selected = flag and ((n := 1) > 0); return n if selected else 2`.
   Its CFG must place the write only on the right-operand edge. Cover `or` and
   conditional arms too. No calls are needed to expose evaluation order.
   Cover a walrus in a repeatedly evaluated loop condition. Pin the eager
   competing-operand examples above as `MIRNotCovered`, without asserting their
   toolchain-dependent native output.
3. Negative coverage pins: put unsupported calls, arithmetic, globals, hoists,
   shapes and body kinds inside otherwise supported functions. Assert the
   specific located reason and absence of a graph. These are analysis tests,
   not `error_` cases for valid source.
4. Malformed-IR tests exercise dangling IDs, wrong types, uninitialized merge
   results and malformed edges. Test distinct bodies with identical short names
   and repeated lowering for deterministic, non-colliding identities.
5. Keep compiler unit tests independent of `tests/cases/`. Ordinary cases run
   through the standard compile/exec/CPython harness, which does not exercise
   MIR in M1. Existing expected output must remain byte-identical; unexpected
   churn requires investigation. Future corpus-wide MIR validation belongs in
   a general harness integration, not case-specific compiler tests.
6. Run targeted tests during implementation, then the full forced-exec suite
   once via `rpytest`. Finish defect review, readiness and a single squashed M1
   commit on a branch. Do not merge master or push.

Design probe at `4976142820`: both CPython and native TPy produced `1 -2 7`
for `choose` and `1 2` for the lazy-write example. The real THIR dump contains
`THIRWhile`, `THIRIf`, assignment, short-circuit binop, walrus, literal coercion
and conditional return as expected. This validates the input seam and baseline,
not the unimplemented MIR builder.

The implementation's unit tests compile their own source fixtures and lower
the exact THIR used for emission. They check CFG paths with a bounded test
interpreter, inspect the lazy-write edge, and pin deterministic IDs/dumps.
These assertions test MIR semantics directly; they do not execute generated
C++ or compare MIR execution against CPython. Normal case-harness runs remain
regression checks for the existing compiler, not MIR coverage.
Separate negative tests cover unsupported input and malformed/undefined MIR,
including branch intersections and loop back edges. `MIRBodyKind` is mandatory
input: a `THIRFunction` alone cannot distinguish a free function from all its
sibling body kinds, so the caller must supply the declaration classification.

## Scope matrix and remaining increments

The following factored matrix covers the Cartesian product: a cell is M1 only
when all three axes say M1 and the operation is in the explicit subset above.
Otherwise it is a filed gap assigned to the earliest applicable later stage;
all prerequisite stages still apply. No omitted cell implies support.

| Axis | M1 covered by the tests above | Filed later scope |
|---|---|---|
| Position | ordinary free-function body | M2: methods, constructors, module statements, closures; M3: generator, async, comprehension, context-manager body, try/finally, error-return body, match arm |
| Shape | bool, int32; void return | M2: other scalars, tuple/singleton, Optional, union, str/bytes, reference types, Own, readonly, Ptr/Span, Box/Rc; M4: generic instantiations and callback forms |
| Slot | parameter, entry-declared local, return, expression temporary | M2: field, container element, global, capture, backing storage; M3: branch/loop-created bindings and frame-held slots |

These are work packages, not approved implementation designs or a claim that
each is one commit. Split them at coherent reviewed boundaries after M1.

| Stage | Deliverable | Exit gate |
|---|---|---|
| M1 | Scalar CFG, typed local/temporary IDs, verifier and internal dump | Structural and evaluation-order tests pass; codegen/acceptance unchanged; explicit unsupported coverage |
| M2 | Semantic THIR metadata at existing decision sites; extended places and explicit storage operations | Qualified globals/callees/fields, structured captures and backing-storage identity survive lowering without parsing C++; alias/copy/move/rebind are carried facts, not inferred from form |
| M3 | Full required CFG/regions and liveness plus holder/loan propagation | Exceptional cleanup, destruction, suspension/resume, joins and every alias/aggregate/closure holder preserve dependency extents; emitted evaluation order is modeled soundly, not assumed from Python; unsupported coverage cannot authorize a proof |
| M4 | Finalized provenance/effect summaries and per-instantiation form obligations | Pending/unknown/known-empty remain distinct; named and imported forwarding, recursion, environment/global effects and selecting-call diagnostics compose; ownership summaries and native assignment traits resolved explicitly |
| M5 | Callable contract/admission consumer and authority transition | Re-run compatibility gate, safe/unsafe corpus and all six provenance requirements; approve language diagnostics and switch authority once, avoiding competing checkers |

Summary fixed-point orchestration stays in sema/workspace analysis as designed;
MIR consumes finalized summaries. M4 must address the existing import-component
propagation gap before claiming complete cross-module effects. M3/M4 can develop
in parallel at defined interfaces, but neither alone is enough for M5. A public
dump and optional comparison hook can land when body coverage warrants them.
MIR-backed C++ emission, SSA, exact-index disjointness and move optimization are
not prerequisites for M5. Existing checkers remain authoritative until then.

## Pitfalls and risks

| Pitfall | M1 check / later obligation |
|---|---|
| silent-copy-vs-alias; copy-warning-at-wrong-site | No reference boundaries or new copy verdicts in M1; M2/M3 tests must mutate shared values across each boundary |
| tuple-equals-scalar | Tuple cells are explicitly M2, including singleton/mixed tuples; no scalar fallback for aggregates |
| same-construct-every-position | Factored matrix above and whole-body MIRNotCovered tests; no claim that free-function coverage covers sibling positions |
| conditional-operand-evaluates-in-place | Walrus effects on guarded CFG edges, once only; loop conditions re-evaluate |
| generic-equals-monomorphic-twin | Open generics are not covered; M4 compares instantiations against concrete twins |
| view-not-copy; hidden-allocation | No emitter changes; byte-identical existing C++; M2 carries view/ownership distinctions explicitly |
| const-source-const-loop-var | Iterators and reference loop slots deferred to M2/M3; no fabricated mutable scalar substitute |
| generated-cpp-readability | C++ emission untouched; internal MIR dump is deterministic and readable |
| no-cpp-in-diagnostics; no-internal-names-in-diagnostics | No source diagnostics added; internal dump may name IR kinds, future diagnostics use source names/locations |
| reject-valid-python-only-as-documented-divergence; no-warning-on-valid-code | Coverage failure changes neither source acceptance nor warnings |

Construction and structural validation should be linear in nodes/edges; definite
assignment uses a finite worklist over slot sets. It is not path enumeration.
Do not repeatedly recompile a whole fixture for individual block assertions.

Confidence for the M1 boundary: high after inspection and the native/CPython
probe. Broader analysis confidence remains limited by semantic metadata gaps,
ownership trust policy, cleanup/frame integration and summary completeness;
none is solved by introducing a CFG. No adjacent compiler fix is included.
The existing evaluation-order defect and policy decision remain tracked under
`BUGS.md#subexpression-right-to-left-eval` and TODO.md; broader MIR coverage must
either follow the chosen emission policy or conservatively model its possible
orders before using such graphs for safety proofs.
