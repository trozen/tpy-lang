# Intermediate Representations -- Design

## Status

| Feature | Status |
|---------|--------|
| THIR node definitions (`tpyc/thir/nodes.py`) | Covers the whole body surface the corpus and stdlib exercise |
| AST + sema -> THIR lowering (`tpyc/thir/lower/`) | Same -- a shape it does not cover is a compile error; the queue is `scripts/thir_migration/review/bins_*.json` |
| `--dump-thir` debug output | Done |
| THIR-backed codegen context | The ONLY author, for every module (`tpyc/thir/emit.py`) |
| Codegen migration from analyzer/AST to THIR | **DONE (2026-09-03).** The AST body emitters (`codegen_cpp/{expressions,statements,match,builtins}.py`) are deleted; `codegen_cpp` is the printer/skeleton layer |
| THIR form fact (Open Q 9/11/12) | **Rungs F1-F3 landed as tabulated below; unions/generics/views route in practice, so the F4-F6 rows are stale as a status view -- read them as scope, not as remaining work. F-final (RefType removal + AST form-codegen retirement) has NOT happened: `RefType` is still live in `typesys.py`.** The per-increment history has been distilled into "Migration findings (distilled)" under the Rollout Plan; the dated blow-by-blow log was dropped |
| MIR node definitions (`tpyc/mir/nodes.py`) | Scalar CFG, borrowed/owned record storage, alias operations, nested field places and selected tuple/Optional/union payloads, validation and internal dump implemented |
| Semantic callable metadata | M2.11 carries selected resolved free-function identities/signatures on THIR calls and definitions; M2.12 carries complete bounded closure capture inventories and body-local occurrence identities. General MIR calls and closure execution remain uncovered; see [call/capture plan](MIR_CALL_CAPTURE_PLAN.md) |
| THIR -> MIR lowering (`tpyc/mir/lower.py`) | M1 and M2.1-M2.10 bounded coverage in free functions, ordinary instance methods and fully initialized scalar constructors, including qualified scalar globals; explicit whole-body MIRNotCovered, no compilation hook. Exact shapes and exclusions: [MIR analysis plan](MIR_ANALYSIS_PLAN.md). B1 admits every loan-inert primitive leaf (fixed-width ints, float, float32, bool, char) and enum values, certified primitive operations and scalar `print`; B2 (first half) admits BigInt, str, String and bytes as owned leaves -- owned storage at rest, readonly borrows at a parameter -- with stub-call contracts from `@pure` / `transient=True`, raising and cyclic summaries and global reads: MIR lowers 11.5% of sampled test bodies and 3.3% of loan-active user bodies (before B1: 7.1% and 1.1%; before B2: 9.5% and 2.1%); widening follows the [breadth-first order](MIR_ANALYSIS_PLAN.md#breadth-first-order) |
| `--dump-mir` debug output | Implemented for the bounded MIR subset; uncovered/unavailable bodies are reported explicitly |
| MIR liveness pass | M3.1 backward may-liveness implemented for validated bounded MIR; debug output only, no move decisions |
| MIR dependency pass | M3.2 per-payload referents and live dependencies implemented with explicit backing duration; debug output only, no lifetime-safety verdict |
| MIR backing writes and scalar payload ends | M3.3-M3.6 merged: bounded write/end inventories and internal possible retained-reference conflicts; no source diagnostics or safety authority |
| MIR emitted storage regions | M3.7 normal storage-end inventory and M3.8 internal retained-reference inspection implemented; cleanup effects and lifetime-safety authority remain uncovered ([region plan](MIR_M3_REGIONS_PLAN.md)) |
| MIR late declarations and ordinary hoists | M3.9 late direct declarations and M3.10 bounded ordinary hoists implemented ([declaration plan](MIR_M3_DECLARATIONS_PLAN.md)) |
| MIR constant boolean edges | M3.11 folds literal/not boolean branches during construction and removes unreachable blocks, slots and regions before strict validation ([constant CFG plan](MIR_M3_CONSTANT_CFG_PLAN.md)); no mutable-variable propagation |
| Physical wrapper initialization | M3.12 models actual defaults independently of source assignment; M3.13 admits scalar Optional/union if/while hoists through typed THIR facts ([wrapper-storage plan](MIR_M3_WRAPPER_STORAGE_PLAN.md)) |
| Optional-backed record hoists | M3.14 separates empty wrappers, engaged records and source assignment; M3.15 connects bounded plain-record if/while THIR facts ([record-storage plan](MIR_M3_RECORD_STORAGE_PLAN.md)) |
| Inline-record tuple backing | M3.16 models flat owned/mixed payload identities and normal lifetimes; M3.17 connects bounded local constructor literals ([tuple-storage plan](MIR_M3_TUPLE_STORAGE_PLAN.md)) |
| Immutable whole-tuple aliases | M3.18 normalizes fixed body-local aliases and chains to existing constructor-tuple backing ([alias plan](MIR_M3_TUPLE_ALIAS_PLAN.md)); no copying, reseating or new storage |
| Ordinary iteration | M3.19 models native iterator identities and dependencies; M3.20 connects unit-step int32 range CFG; M3.21 connects fixed borrowed native scalar/record sources, readonly and retained aliases ([iteration plan](MIR_M3_ORDINARY_FOR_PLAN.md)); protocol iteration and other shapes remain open |
| Remaining M3 scope | [Completion checklist](MIR_M3_COMPLETION_PLAN.md): storage lifecycle, additional CFG positions, cleanup, suspension and complete holder propagation; each open item is scheduled under a breadth-first B-step |
| MIR move/copy lowering and move optimization | Explicit bounded record copy/move operations implemented; move optimization not started |
| MIR advisory loan checker (default mode) | Not started; step B6 of the [breadth-first order](MIR_ANALYSIS_PLAN.md#breadth-first-order), which also decides per-family vs single authority transition |
| MIR safe opt-in enforcement mode | Not started |
| MIR-backed codegen | Not started |
| Retirement of old sema/codegen ownership logic | Not started |

**Active MIR sequence:** analysis-only MIR precedes the coupled callable
contract (approved 2026-09-17) and grows breadth-first (approved 2026-09-29):
B1 loan classification of representations, B2 owned leaves (BigInt, str,
String, bytes; landed) then views as places, B3 containers and iterators, then
cleanup, B4 generator/async frames, with B5 call summaries alongside and B6
the advisory checker and authority transition
([breadth-first order](MIR_ANALYSIS_PLAN.md#breadth-first-order)). Lifetime and
loan defects are routed to MIR, not patched in sema (`BUGS.md` entries tagged
`deferred: MIR`). The landed M1-M4.6 increments stay recorded in
`MIR_ANALYSIS_PLAN.md`. The historical phase lists below are not an
instruction to implement move optimization or switch emission before the
callable analysis consumer.

A throwaway Phase-1 spike (2026-06) validated the THIR boundary -- byte-identical
codegen from THIR with no analyzer reference, on an arithmetic slice; see Rollout
Plan -> "Phase-1 spike validation".

**Completion tracking (historical).** The THIR migration is finished, so the
documents that tracked it are now records rather than roadmaps:
`THIR_COMPLETION_LEDGER.md` (what each rung deleted, per-wave history, the
lessons), `THIR_EMIT_INVENTORY.md` (the finite emit surface it ported) and
`THIR_CUTOVER_REVIEW.md` (the 2026-09-02 decision -- flip, delete, fix as we go
-- and the post-cutover health review of the IR). This doc remains the design,
plus the distilled migration findings under the Rollout Plan. What is still
open is MIR. The shape meter (`tpyc/thir/shape.py`) went with the unit
tests it measured; its percentage was asymptotic by construction and steered
nothing.

## Motivation

### The Problem

TPy's compiler currently uses a single representation: the AST from `parse/nodes.py`,
mutated in-place by sema with 50-70 optional annotation fields (`resolved_function_info`,
`inferred_type_args`, `bounds_safe`, `ptr_non_null`, etc.). Codegen reads the annotated
AST plus sema side tables (`expr_types`, `var_types`, fact dicts) via a direct reference
to the `SemanticAnalyzer`.

This works, but creates three concrete problems:

1. **Tight coupling.** Codegen cannot run without a live sema instance. Sema state is
   spread across AST node fields, `SemanticContext` dicts keyed by `id()`, and
   `BorrowTracker` string maps. There is no self-contained "this is what sema produced"
   artifact.

2. **Hard to debug.** There is no way to dump the fully-typed, fully-resolved program
   state between sema and codegen. Debugging requires mentally reconstructing what sema
   wrote into each node and side table.

3. **Borrow checking precision.** The current borrow checker operates on the AST with
   `freeze()`/`restore_from_frozen()` at branch points via `FlowFacts`, but merges
   borrow states conservatively at join points (union of borrows from both branches).
   This means a borrow active in either branch is assumed active in both -- so a move
   on one path can conflict with a borrow on a mutually exclusive path. The tracker
   also uses string-based storage keys with limited field-path support (`"self.items"`),
   and cannot split borrows by independent fields.
   A CFG-based IR is the standard solution for path-sensitive safety analysis.

4. **Agent boundary.** When LLM agents work on the compiler, the lack of clean phase
   boundaries makes it easy to accidentally couple new code to sema internals. A well-
   defined IR contract between phases prevents this class of errors.

### Why Two IRs

One IR is not enough because sema and borrow checking have different needs:

- **Type checking and overload resolution** work naturally on trees. Expressions have
  types, calls resolve to specific functions, generics are instantiated. A tree-shaped IR
  is the right fit.

- **Borrow checking, liveness, and move optimization** need path-sensitive analysis:
  "is this variable live on *every* path reaching this point?" This requires a CFG where
  each basic block has explicit predecessors and successors, and dataflow facts propagate
  along edges.

Trying to do both on the same representation forces either a tree that carries CFG
information (awkward) or a CFG that carries type-checking state (wasteful). Two IRs
let each phase use the right structure.

### What THIR/MIR unblocks (running ledger)

A growing list of concrete defects and duplication whose *clean* fix is gated on the
IR migration -- maintained so the migration's priority can be judged against accumulated
cost rather than asserted. Add entries here as they surface; cite the BUGS.md / TODO.md
source. Some entries are closeable pre-IR only as a *rejection* (loud diagnostic), not a
*fix*; those are the strongest signal, because the feature genuinely cannot be expressed
in the current model.

- **Borrow-form vs storage-form (Open Questions item 9 -- the largest cluster).** Tuple
  (and Optional/Union) C++ form is reconstructed per-site in codegen instead of being a
  type fact, so every new boundary shape needs another consumer-side dispatch patch:
  - *Tuple local with a durable reference member silently copies it at yield/return* --
    was **[MED, silent CPython divergence]** (TPy `5` vs CPython `99`). **FIXED pre-IR**
    by the tuple borrow-pointer unification (`unify-tuple-borrow-pointer-form`,
    `docs/TUPLE_BORROW_UNIFICATION_PLAN.md`): the bound local is a pointer-form tuple
    (`std::tuple<int, Box*>`), constructible and rebindable where a reference field is
    not, and it aliases correctly across suspensions -- disproving this item's earlier
    "THIR-gated" claim for the durable-share case. The cost was the consumer-side
    dispatch inventory now listed under Open Questions item 9, plus three adversarial
    audit waves closing provenance escapes -- the per-shape fact-propagation burden item
    11 is about.
  - Nested tuple where outer/inner forms disagree -- **[MED]** (BUGS.md).
  - Rvalue tuple-of-records into a ref/pointer-form slot -- **[MED/LOW]** (BUGS.md).
  - Generic `V | None` instantiated with `V = Ptr[T]` (double-pointer) (BUGS.md).
  - Bare-Optional yield missing the storage->pointer bridge (BUGS.md).
  - Union `match` capture: value-variant storage-form binding vs pointer-variant
    borrow subject (also an undesigned-aliasing-form design question) (BUGS.md).
  - View-family `*args` elements (str/bytes) reconstructed per-site as storage form
    (`varargs<std::string>` / `varargs<std::vector<uint8_t>>`) instead of the borrow
    form the scalar param already uses (`string_view` / `span<const uint8_t>`), so every
    individual arg is copied into the owned pack (TODO.md). Intentional + memory-safe
    today; the zero-copy borrow form is all-paths-or-nothing (one element type, so every
    consumer must agree) and rides on this item's general element-as-borrow +
    materialize-at-owned-sink rule rather than being bespoke work. The inventory the MIR
    rule must cover, from a per-shape probe: (1) pack construction -- individual args
    zero-copy, `*container` star-unpack needs a `vector<view>` materialization (the list's
    `std::string`s aren't contiguous views), varargs->varargs forwarding already fine;
    (2) every owned sink -- return, var-init, container insert, dict key, tuple element,
    comprehension element, `list(parts)` -- each currently re-decides whether to wrap;
    (3) the per-site element-type derivations that already *disagree* today (statement-`for`
    binds `string_view`, comprehension binds `const std::string&`, slice-result local binds
    `varargs<std::string>`) -- unifying these is the bulk of the consumer-side dispatch;
    (4) the lifetime half -- generator/coro frame capture must OWN a copy (captured views
    dangle past the call statement for non-literal args; async `*args` is unsupported today,
    so only the resumable-struct frame applies). Read-only uses
    (len, print, concat, element-into-str-param, statement-`for` iteration) are already
    correct. Probed + Codex-co-validated all-paths-or-nothing 2026-06.
  Several smaller cases in this class *were* closed pre-IR by extending consumer-side
  predicates -- but each one touched another dispatch site, which is exactly the cost the
  IR fact removes. The same fact also dissolves the sema `Ref[T]` wrapper, which today
  co-exists with codegen's positional re-derivation as a second borrow-form oracle
  (Open Questions item 12).
- **Path-insensitive borrow checking (Motivation problem 3).** The AST borrow checker
  merges borrow states conservatively at join points (union over branches), so a move on
  one path conflicts with a borrow on a mutually-exclusive path -- false positives a
  CFG-based MIR resolves.
- **Extent-scoped loans for match-arm bindings (BUGS.md [HIGH]).** A non-scalar `match`
  arm binding is an `auto&` borrow into the subject's storage; mutating the subject root
  within the arm (a method that reassigns it, or an alias) dangles it -- silent UB,
  verified. It cannot be fixed soundly today: the whole-function mutation facts the
  deferred-check resolver reads are extent-blind, so they cannot express "the subject root
  was mutated *while this arm's binding was live*", and copying the binding is off the
  table (str/BigInt perf, view dangle, reference-type CPython-aliasing divergence). The
  scalar half is closed by copying free-copy scalars; the non-scalar half wants a MIR
  `Place(subject-root)` + `LoanInfo(arm extent)` loan that rejects root mutation while the
  loan lives -- a concrete motivator for the place/loan model, not just a precision win.
- **Hand-copied sema/codegen predicate mirrors.** Predicates duplicated across phases and
  kept in lockstep only by discipline: `directly_implements_dynamic` (sema mirror of
  codegen, now 4 call sites -- BUGS.md), the default-ctor predicate and the param-const
  verdict (TODO.md). A shared-IR contract removes the duplication class.
- **Eager per-shape local binding decisions (Open Questions item 11).** Non-value and
  pointer-repr-tuple locals pick their C++ shape eagerly at the binding site across ~7
  parallel mechanisms (ref binds, pointer-locals + rvalue slots, optional-locals,
  frame_slot fields, borrow-/storage-form tuple sets), each with its own
  init-deferral/rebind/alias rules -- the source of the optional brace-init corruption
  class, the tuple owning/alias rebind rejection (BUGS.md), and the per-shape
  provenance-fact propagation that three adversarial audit waves patched escape-by-escape.
  MIR's place/loan model with late representation selection + a mem2reg-style fold
  replaces all of it.
- **Generic str/bytes ABI perf split (Open Questions item 8).** **[perf, not
  correctness]** generic-`T`-over-`str` materializes `std::string` at each call site.
  Documented; low priority.
- **Move/relocation safety of inline storage (MIR move/copy lowering).**
  `UninitArrayStorage`'s move ctor `memcpy`d its element array unconditionally,
  silently corrupting a non-trivially-relocatable element: an SSO `std::string`'s
  data pointer aliases its own inline buffer, so a byte-copy leaves the moved-to
  string pointing into the moved-from (soon-dead) storage. It surfaced as a
  `stack-use-after-return` for `async def -> str` (the result flows through a
  moved `Poll<std::string>`), ASan/hardened-allocator-only and benign on glibc --
  the worst kind of latent miscompile. The first fix (a per-storage liveness
  bitset + element-wise move) was rejected: liveness is the OWNER's state (a
  size, a head/count, a flag), so duplicating it in the storage is redundant and
  over-general (a ring buffer's liveness is not even a prefix). The shipped
  design instead keeps the storage dumb -- `memcpy` move for trivially-copyable
  `T`, the move **deleted** for non-trivial `T` (silent corruption becomes a
  compile error) -- and introduces `tpy::UninitStorage<T>` for single-optional-value
  owners (Poll/Rc-payload/channel-send), whose one liveness bit IS the owner's
  (no duplication). The per-type "trivially relocatable?" decision (here a
  conservative `is_trivially_copyable` proxy) and the storage-vs-slot choice are
  exactly what MIR's move/copy lowering should own and verify centrally, rather
  than each hand-written container re-deriving it and one (the old move ctor)
  getting it wrong.
- **Joint generic inference: a pending-typed arg co-resolved by a sibling argument.**
  **[ergonomics, not correctness]** An untyped empty-container local (`heap = []` ->
  `PendingList[???]`) passed to a generic free function alongside an argument that fixes
  the type parameter is not resolved: for `heappush(heap, Entry(copy(src[i])))` against
  `heappush[X](heap: list[X], item: Own[X])`, `X = Entry[T]` is inferable from `item`, but
  `match_type_with_inference` is directional (param <- one arg at a time) with no shared
  unification variable tying `heap`'s pending element to `X`, so the local stays
  `PendingList[???]` and the call is rejected. Forward-from-usage deduction already covers
  the method-call shape (`xs.append(5)`) and the concrete expected-type shape (`f(x)` where
  `f` wants `Container[int32]`) -- see `BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md` Phase 3a --
  but the joint case (co-resolve a pending arg with a type param determined by a *sibling*
  arg, then write the result back onto the local) is the HM-style constraint-solving step
  that doc defers to "Phase 3+". Natural on MIR's unification-variable model; awkward to
  bolt onto the directional AST matcher. Workaround: annotate the local
  (`heap: list[Entry[T]] = []`). Surfaced reviewing the owned-storage-form inference fix.
- **Callable result provenance -- the analysis-only MIR's FIRST CONSUMER.** The
  callable-result design splits along exactly this line: what a callable's result
  MEANS is decidable from types and is proposed to ship against sema
  (`docs/CALLABLE_CONTRACT_DESIGN.md`),
  while WHICH of the caller's places a returned borrow roots in, and what later
  invalidates it, is not. The second half is written as requirements on the
  analysis-only MIR in `docs/CALLABLE_PROVENANCE_REQUIREMENTS.md`, and until they
  are met the contract half refuses those shapes with located errors rather than
  accepting them unsoundly -- capture-rooted callback results, stored closures with
  borrowed environments (`self` included), global-rooted borrows, helper
  compositions the parameter-index summaries cannot express, and any call whose
  effects are unknown while a loan is live. These restrictions also reject safe
  programs accepted today. The contract document's "Compatibility gate" measures
  that cost before the coupled implementation; if the admission layer requires
  substantial new flow analysis or rejects common safe callback idioms, bring
  analysis-only MIR forward. The focused measurements and code audit in
  `docs/CALLABLE_CONTRACT_FEASIBILITY.md` led to the approved 2026-09-17 decision
  to do so. `docs/MIR_ANALYSIS_PLAN.md` records the first increment; no admission
  rule has been implemented.
  Six provisions are load-bearing for it:
  (1) stable place identities covering locals, temporaries, captures, qualified
  globals, fields, derefs and summarized container elements; (2) explicit
  operations -- alias, borrow, copy, move, rebind, closure construction, call,
  return, escape into a field or container; (3) a CFG preserving evaluation order,
  including short-circuits, back edges and exceptional cleanup, rejecting the
  lifetime-sensitive shapes it cannot represent; (4) liveness plus loan propagation
  that follows every holder, copied closures and aggregates included; (5)
  summary/effect application at call sites, including named calls that forward a
  callback; (6) form and effect obligations discharged per instantiation. No SSA,
  no exact-index disjointness and no MIR-backed emission are needed for any of it.
  Two constraints the consumer imposes on the eventual checker: its callable-subset
  verdicts are hard ERRORS even in the default advisory mode (a warn-and-continue
  verdict there is a silent use-after-free), and its conflict rule is invalidation,
  not exclusivity -- two shared loans on one place must coexist (see "Interaction
  with Ownership Model" below).
- **Simple-generator peephole eager-body divergence.** RESOLVED 2026-09-12 without MIR:
  the single-yield lambda peephole was deleted outright, so every generator lowers on the
  resumable frame and no prologue or post-yield code runs eagerly. What this bullet used to
  park behind the reroute stays filed on its own: the alias clobber (BUGS.md
  `resumable-alias-identity`) and the owning whole-tuple-slot `yield t` rejects.
- **Loop-bound generators that borrow storage the loop writes.** The everyday
  `for row in rows: xs = [...]; g = gen(xs); for v in g: yield v` keeps the previous
  pass's `g` open, in CPython, until the next pass binds `g` again -- after `xs` is
  already a new list, while the old `g` still reads the old one. TPy writes the one
  storage both passes share, so a generator or async body REJECTS the shape whenever
  the loop may write what the frame borrows (`sema/loop_frames.py`), and a plain
  function keeps `g` in the pass's C++ block, closing a pass early
  (BUGS.md#plain-loop-generator-closes-at-pass-end). A plain function does the same
  for a generator first bound in any other block -- an `if` arm, a `with` / `try`
  body, a `match` arm -- closing it at the block's end
  (BUGS.md#plain-block-generator-closes-at-block-end); read after the block it may
  borrow only storage that outlives the body
  (BUGS.md#generator-block-bind-borrows-local-rejects), because declaring it in front
  of the block together with the locals it borrows (tried 2026-09-25 on
  `frame-borrow-order`) leaves every later write to those locals unchecked against
  the open frame -- `if k: g = gen(xs); first(g)` then `xs.append(...)` in a loop is
  a heap-use-after-free when the frame closes -- and one declared there over a
  parameter stays open while a `with` exit writes that parameter
  (BUGS.md#plain-with-exit-writes-generator-storage). The fix is to close the old
  frame just before the write (design R, studied 2026-09-25 on `frame-borrow-order`),
  which is not a legal placement without MIR: the close must come after the
  statement's own RHS runs (`xs = [next(g)]` consumes `g`) and before the store, on
  normal and exceptional edges alike, and at implicit points (a `with` whose
  `__exit__` replaces `h.xs` under a `g` created inside the body); a whole-name root
  may hide an internal iterator (a live generator iterating a list across an
  `append` is a use-after-free with only a warning today); only element-shaped or iterator roots may close early -- a container
  or whole-`self` root the `finally` reads through must stay open, as CPython shares
  that object, and whole-`self` rules must not reject the everyday
  `for t in self.tokens(): self.cur = Cell(t)`; an early close is observable when the
  `finally` feeds code before CPython's close point; and effect-free operands do not
  prove the cleanup commutes with the store. It needs MIR W3 (exit and cleanup
  effects and their order), W4 (frame dependencies across suspension and close) and
  W5 (element / returned / escaped provenance, place-granular call effects)
  (`docs/MIR_M3_COMPLETION_PLAN.md`); its gate is the two ASan matrices of that
  study (`/tmp/agents/fbo-int/m3`, 301 cells; `m4`, 288 cells).

---

## Prior Art

| Compiler | IRs | Notes |
|----------|-----|-------|
| Rust (rustc) | HIR -> MIR -> LLVM IR | MIR is where borrow checking, move analysis, and optimizations happen. Pre-monomorphization. |
| Swift (SIL) | AST -> raw SIL -> canonical SIL -> LLVM IR | SIL carries ownership and lifetime information. Two forms (raw/canonical) separate verification from optimization. |
| Go | AST -> SSA | Single IR, SSA-based. No borrow checking needed (GC). |
| C++ (Clang) | AST -> LLVM IR | No intermediate -- AST is heavily annotated, similar to TPy's current state. |

TPy's situation is closest to early Rust before MIR was introduced (2016). Rust had the
same problem: borrow checking on the tree-shaped HIR was imprecise and generated false
positives. MIR solved this.

---

## THIR Design

### Goal

Produce an **immutable, self-contained** representation of a fully-analyzed module that
codegen (and later MIR lowering) can consume without referencing the `SemanticAnalyzer`.

### What Changes

| Today | After THIR |
|-------|------------|
| AST nodes have 50-70 optional annotation fields, mostly `None` after parsing | THIR nodes have required fields -- all types resolved, all overloads bound |
| `expr_types[id(node)]` side table | `THIRExpr.result_type: TpyType` on the node |
| `var_types[id(node)]` side table | `THIRVarDecl.resolved_type: TpyType` on the node |
| `ptr_deref_facts[(line, key)]` dict | `THIRDeref.non_null: bool` on the node |
| `subscript_bounds_facts` dict | `THIRSubscript.bounds_safe: bool` on the node |
| `all_last_uses: set[int]` + `movable_locals: set[str]` | `THIRName.is_last_use: bool` + `THIRName.is_movable: bool` on the node |
| `resolved_function_info` optional field | `THIRCall.target: ResolvedFunction` required field |
| Per-function analyzer dicts (`function_scan_results`, `function_hoisted_vars`, `function_movable_locals`, `function_move_through_vars`, `function_global_decls`) | `THIRFunction.layout` and `THIRFunction.declared_globals` |
| Module options from sema/context (`default_int_type`, `default_int_for_literal`) | `THIRModule` required fields |
| View/literal registries (`str_vars`, `bytes_vars`, `list_literals`, `dict_literals`, `set_literals`) | explicit `view_info` / `literal_info` on the relevant THIR nodes |
| Codegen holds `self.ctx.analyzer` reference | Codegen receives `THIRModule`, no analyzer reference |

### THIR Node Hierarchy

The THIR mirrors the AST structure but with all analysis results materialized:

```
THIRModule
  functions: list[THIRFunction]
  records: list[THIRRecord]
  protocols: list[THIRProtocol]
  enums: list[THIREnum]
  globals: list[THIRGlobal]
  top_level: list[THIRStmt]
  default_int_type: TpyType
  default_int_for_literal: TpyType
  type_registry: TypeRegistry          # shared, immutable after sema

THIRFunction
  name: str
  params: list[THIRParam]
  return_type: TpyType
  body: list[THIRStmt]
  layout: THIRFunctionLayout
  declared_globals: frozenset[str]
  mutated_params: frozenset[str]       # from Phase 2 propagation
  is_readonly: bool
  return_borrows_from: frozenset[int]  # param indices
  is_generic: bool
  type_params: list[TypeParam]
  overload_group: str | None
  generator: THIRGeneratorInfo | None

THIRFunctionLayout
  hoisted_locals: frozenset[str]
  movable_locals: frozenset[str]
  move_through_locals: frozenset[str]
  pointer_locals: frozenset[str]
  ref_locals: frozenset[str]
  reassigned_locals: frozenset[str]    # affects C++ declaration style / slot handling

THIRGeneratorInfo
  yield_type: TpyType
  states: list[THIRGeneratorState]
  frame_fields: list[THIRSyntheticField]
  strategy: GeneratorStrategy          # current codegen strategy, if any

GeneratorStrategy
  = current backend-defined enum matching generator lowering variants

THIRGeneratorState
  state_id: int
  resume_label: str

THIRSyntheticField
  name: str
  type: TpyType

THIRParam
  name: str
  type: TpyType                        # fully resolved (Own[T], readonly[T], etc.);
                                       # no Ref[T] -- dissolved into form facts
                                       # (Open Questions item 12)
  default: THIRExpr | None
  is_mutated: bool                     # from mutation analysis
```

#### Expressions

```
THIRExpr (base)
  result_type: TpyType                 # always present
  loc: SourceLocation | None

THIRName
  name: str
  result_type: TpyType
  is_last_use: bool                    # from liveness analysis
  is_movable: bool                     # in movable_locals

THIRCall
  target: ResolvedFunction             # fully resolved -- function, overload index, etc.
  args: list[THIRExpr]
  type_args: tuple[TpyType, ...]       # instantiated generics (empty if non-generic)
  result_type: TpyType

THIRMethodCall
  receiver: THIRExpr
  method: ResolvedFunction
  args: list[THIRExpr]
  type_args: tuple[TpyType, ...]
  deref_depth: int                     # Ptr auto-deref count
  result_type: TpyType

THIRFieldAccess
  receiver: THIRExpr
  field: str
  deref_depth: int
  non_null: bool                       # proven non-null at this deref
  result_type: TpyType

THIRSubscript
  container: THIRExpr
  index: THIRExpr
  bounds_safe: bool                    # proven in-bounds
  result_type: TpyType

THIRBinOp
  left: THIRExpr
  op: BinOpKind
  right: THIRExpr
  resolved: ResolvedBinop | None       # operator overload, if any
  divisor_non_zero: bool
  result_type: TpyType

THIRNamedExpr                            # walrus operator (:=)
  name: str
  value: THIRExpr
  result_type: TpyType

THIRCoerce
  expr: THIRExpr
  from_type: TpyType
  to_type: TpyType
  kind: CoercionKind                   # widening, own-strip, optional-wrap,
                                       # runtime-bigint, etc.

THIRLiteral
  value: int | float | str | bool | bytes | None
  result_type: TpyType

THIRListLiteral
  elements: list[THIRExpr]
  element_type: TpyType                # resolved element type
  literal_info: THIRLiteralInfo | None
  result_type: TpyType

THIRDictLiteral
  items: list[(THIRExpr, THIRExpr)]
  key_type: TpyType
  value_type: TpyType
  literal_info: THIRLiteralInfo | None
  result_type: TpyType

THIRSetLiteral
  elements: list[THIRExpr]
  element_type: TpyType
  literal_info: THIRLiteralInfo | None
  result_type: TpyType

THIRTupleLiteral
  elements: list[THIRExpr]
  result_type: TpyType

THIRTupleUnpack
  targets: list[THIRExpr]
  value: THIRExpr
  result_type: TpyType

THIRComprehension
  kind: AggregateKind
  element: THIRExpr
  clauses: list[THIRComprehensionClause]
  result_type: TpyType

THIRGeneratorExpr
  element: THIRExpr
  clauses: list[THIRComprehensionClause]
  result_type: TpyType

THIRComprehensionClause
  = For(target: THIRExpr, iterable: THIRExpr)
  | If(condition: THIRExpr)

THIRLiteralInfo
  needs_stable_storage: bool

THIRViewInfo
  source_kind: str                     # str / bytes / span / ptr / field / element
  source_expr: THIRExpr

# ... (array, f-string, lambda, etc.)
```

#### Statements

```
THIRVarDecl
  name: str
  resolved_type: TpyType              # always resolved
  init: THIRExpr | None
  is_hoisted: bool                     # escapes inner scope
  is_pointer_local: bool               # T* slot (non-value type local)
  view_info: THIRViewInfo | None
  narrowing_facts: dict[str, TpyType]  # from isinstance/assert on this decl

THIRAssign
  target: THIRExpr                     # name, field, subscript
  value: THIRExpr
  view_info: THIRViewInfo | None

THIRAugAssign
  target: THIRExpr                     # name, field, subscript
  op: BinOpKind                        # Add, Sub, etc.
  value: THIRExpr
  resolved_inplace: ResolvedFunction | None  # __iadd__ etc. overload

THIRDelItem
  calls: tuple[THIRExpr, ...]          # one __delitem__ / __delattr__ call per target, in source order (del d[k], o.x)

THIRForEach
  var: str
  elem_type: TpyType
  iterable: THIRExpr
  body: list[THIRStmt]
  orelse: list[THIRStmt]               # for/else body (runs if no break)
  is_consuming: bool                   # consuming iteration selected
  is_native: bool                      # NativeIterable range-for

THIRWhile
  condition: THIRExpr
  body: list[THIRStmt]
  orelse: list[THIRStmt]               # while/else body
  narrowing_facts: dict[str, TpyType]  # condition narrowing in body

THIRIf
  condition: THIRExpr
  then_body: list[THIRStmt]
  else_body: list[THIRStmt]
  then_narrowing: dict[str, TpyType]   # type narrowing in then-branch
  else_narrowing: dict[str, TpyType]

THIRAssert
  condition: THIRExpr
  message: THIRExpr | None
  narrowing_facts: dict[str, TpyType]  # narrowing after assert passes

THIRMatch
  subject: THIRExpr
  arms: list[THIRMatchArm]

THIRMatchArm
  pattern: THIRPattern
  guard: THIRExpr | None
  body: list[THIRStmt]
  narrowing_facts: dict[str, TpyType]  # type facts for this arm

THIRReturn
  value: THIRExpr | None
  is_dangling: bool                    # if True, sema already reported error

THIRYield
  value: THIRExpr | None
  state_id: int                        # generator state machine ID

THIRTryExcept                          # @error_return(E) zero-cost error handling
  kind: TryKind                        # ErrorReturn or Throw
  error_local: str | None
  body: list[THIRStmt]
  handlers: list[THIRExceptHandler]

TryKind
  = ErrorReturn
  | Throw

THIRWith
  context: THIRExpr
  var: str | None
  body: list[THIRStmt]
```

### Lowering Pass: AST + Sema -> THIR

A new pass (`tpyc/thir/lower.py`) walks the annotated AST and sema side tables,
producing THIR nodes:

```python
def lower_module(ast: TpyModule, analyzer: SemanticAnalyzer) -> THIRModule:
    """Convert annotated AST + sema state into a self-contained THIR."""
    ...
```

This is where all `id()`-keyed lookups, optional field reads, and side table accesses
are resolved into concrete THIR fields. After lowering, the analyzer can be discarded.

Two requirements are important here:

1. **Implicit coercions must be materialized.** Every sema-selected conversion becomes
   an explicit `THIRCoerce` at the exact site where it applies: call arguments,
   assignments, returns, operator operands, literal elements, default arguments,
   `Own` stripping, optional wrapping, enum-from-value, `str -> StrView`,
   `bytes -> BytesView`, and the other coercion families currently scattered across
   sema. THIR lowering must not rely on codegen or MIR lowering to rediscover them.

2. **THIR must be codegen-complete.** If current codegen needs per-function layout
   facts, view provenance, literal lowering metadata, module integer defaults,
   generator frame shape, or declared globals, THIR must carry an explicit equivalent.
   The shape may improve over today's analyzer dicts, but the analyzer dependency must
   end after THIR lowering.

### Debugging: `--dump-thir`

A human-readable text format for inspecting the THIR:

```
fn main() -> Void:
  %items: list[int32] = list_literal([1, 2, 3])    # elem_type=int32
  %total: int32 = int_literal(0)
  for %x: int32 in %items [consuming=false, native=false]:
    %total = binop(%total, Add, %x)                 # resolved=int32.__add__
  call print(%total)                                 # target=builtins.print
```

This makes the resolved types, overloads, and optimization facts visible at a glance.

The dump renders what CODEGEN lowered, not a standalone re-lowering: it runs codegen
and reads its per-module THIR caches, then discards the generated C++. That is what
lets it show resumable (async / generator) bodies -- which only lower at frame
emission, since their CFG needs live codegen state -- and constructors, neither of
which the sync per-body entry reaches. Bodies that did NOT lower are named rather
than omitted, since "what did not route" is usually the question. The dump is a
SURVEY of the bodies that lower ahead of emission: every function, method and
constructor body is attempted and reports its own reject reason, so one run lists
every unsupported FUNCTION, METHOD and CONSTRUCTOR body of a file (and a verdict
matrix can hold one cell per function instead of one per program). It does NOT
list every unsupported body: the units that lower DURING emission -- a generator
/ async frame, the module-init body, a class constant, a `Final` global -- are
attempted only when no pre-emission body rejected. Once one has, the deferred
reject ends the pass before emission starts and every one of those units reads
`<not attempted: an earlier reject ended emission>`; a reject inside one of them
ends the pass in the same way, so what follows it reads `not attempted` too.
An ordinary compile is unchanged: it reports the first reject. Every concrete
`THIRExpr` / `THIRStmt` subclass must have a render arm: the dispatch raises on an
unregistered node instead of degrading to a `<ClassName>` placeholder, and
`test_dump.py` fails the moment a new node class lands without one.
Because it runs codegen, the dump costs a full codegen pass and a
`CodeGenError` can now surface from it -- the old standalone lowering never
invoked codegen at all. Callables codegen never attempts (native bindings,
`@cpp_template`, `...` stubs, overload stubs) are labelled
`<not a body-migration candidate>` rather than as fallbacks, so the frontier
is not over-reported.

### Form as a First-Class THIR Fact (resolved 2026-06)

Resolves Open Questions 9 (tuple/form fact), 11 (uniform local model -- the THIR
half), and 12 (RefType fate). The ground-truth surface this must subsume is
`docs/THIR_FORM_INVENTORY.md`; that document is the binding checklist (completion
= every item closed + the AST form-codegen retired). A spike validated the node
mechanism against the live `convert()` chokepoint (24/24 across optional/union/
tuple x both directions x const x move) and reproduced the minimal field-read
slice (`const Inner* x = ::tpy::optional_to_ptr(b.inner)`) byte-for-byte.

#### Framing constraint: byte-identical, for validatability (not churn)

The form slice must emit C++ byte-identical to the AST path (verified by forcing
`--thir-codegen` on and diffing -- zero snapshot diffs, the same gate increments
1-5 pass). The reason is not snapshot-churn cost; it is that a zero-diff is the
*only* way a human can confirm thousands of cases still compile correctly. If a
form change rewrote thousands of snapshots, no reviewer could validate them. So
byte-identity is the migration's correctness proof during AST/THIR coexistence.
Representation *normalization* (collapsing the `LocalCppForm` zoo) is real and
desirable, but it belongs to MIR's late-representation fold (Open Q 11's MIR
half), where it is the explicit goal -- not smuggled into THIR where it would
forfeit the zero-diff net.

#### Two facts, not one: value-form vs local-representation

A var-decl like `x = b.field` braids two orthogonal facts that the design keeps
separate:

1. **Value form** (Open Q 9) -- the borrow-vs-storage axis of a *value*:
   `BORROW` (`T*`, `T&`, `variant<A*,B*>`, `tuple<...,T*>`, `optional<T>` read as
   `T*`) vs `STORAGE` (`T`, `optional<T>`, `variant<A,B>`, `tuple<...,optional<T>>`)
   vs `VALUE` (value types -- the two forms coincide). This is the `CppForm` enum
   lifted from a codegen-local notion to a carried IR fact. It is SEMANTIC: it
   survives into MIR (it is about ownership/aliasing).

2. **Local representation** (Open Q 11) -- the C++ *slot shape within a form*:
   `T&` alias vs `T*`+rebind-slot vs `optional<T>` deferred-init vs `frame_slot<T>`
   vs pointer-element tuple. This is the existing `LocalCppForm` (9 variants) +
   the 22 side-sets. It is COMPATIBILITY metadata -- carried only to reproduce
   today's eager C++ byte-identically; MIR's fold subsumes ONLY this level.

These co-arrive (the first conversion-bearing slice needs both), so THIR carries
both from increment 1. Only the MIR fold defers.

#### The form tag lives on the expression, not the type

The same `TpyType` (`Inner | None`) renders as `Inner*` (borrow) or
`std::optional<Inner>` (storage) depending purely on POSITION -- so form is a
positional fact, not intrinsic to the type. Putting a form tag on the type would
force two non-canonical type instances per tuple/optional; putting it on the expr
keeps types canonical and matches today's `FormValue.form` (the producing emitter
records the form it actually emitted). So:

```
THIRExpr (base)
  result_type: TpyType
  form: Form               # NEW: BORROW | STORAGE | VALUE  (default VALUE)
  loc: SourceLocation | None
```

`Form.VALUE` default leaves the existing value-scalar slice untouched (every
current node is VALUE). `result_type` still selects the conversion FAMILY; `form`
says which side of the axis. Form is SET by lowering through a single classifier
(one writer -> no divergence); the coerce boundary ASSERTS rather than silently
passing a value-form expr through, so a `VALUE` tag can never mask a *missed*
conversion. This positional placement is also what resolves the consumer-dictated
exhibits (Open Q 9's `key=` lambda, async/await union, match capture): the
conversion is inserted at each CONSUMER site, so one definition with one param
form is bridged independently by each consumer -- no definition-site guess.

`THIRVarDecl` additionally carries the local-representation fact:

```
THIRVarDecl
  ...
  form: Form                       # coarse, SEMANTIC -- drives the insertion rule
                                   #   (a local is NOT uniformly BORROW: storage-
                                   #   optional / storage-tuple loop/unpack vars
                                   #   are STORAGE form)
  cpp_local_representation: ...    # the LocalCppForm analog, carried VERBATIM.
                                   #   COMPATIBILITY metadata: explicitly
                                   #   non-semantic, FORBIDDEN for any other THIR
                                   #   node to depend on; MIR's fold subsumes only
                                   #   this. Do not redesign it here (that is
                                   #   divergence risk + MIR's job).
```

#### THIRFormConvert -- the explicit conversion node

```
THIRFormConvert(THIRExpr)
  value: THIRExpr          # inner; value.form is the source form
  # result_type + form (inherited) = destination type + destination form
  is_const: bool           # const-qualified borrow -> const helper overload
  move: bool               # last-use into owned sink -> _move helper variant
```

Invariant: `THIRFormConvert` preserves `result_type` and changes only `form` --
this is what distinguishes it from `THIRCoerce` (which changes the TYPE). No
`kind` field: the spike proved the runtime helper is a pure function of
(family(result_type), value.form -> form, is_const, move) -- Optional ->
`optional_to_ptr` / `ptr_to_optional[_move]`; Union -> `to_[const_]ptr_variant` /
`to_value_variant<...>`; Tuple -> `tuple_to_pointer<...>` / `tuple_to_storage[_move]
<...>`. (Open caveat below: whether those four inputs suffice for ALL ~150 direct
sites is what the F1 spike must confirm; if a site needs more, it goes on the node
then, not pre-emptively.)

#### How lowering obtains the form -- pure classifier + emit counter

The form/representation decisions are made today DURING the codegen walk (the
binding-site writers in `_gen_var_decl_code` et al.), but they split cleanly:

- **Form classification is pure-derivable.** Codegen already re-runs the same
  `scan_reassigned_vars` prescan sema runs, so the walk-order input (reassigned /
  rvalue-reassigned / alias) is available BEFORE the emit walk. The pointer-vs-
  optional-vs-tuple-vs-alias choice is a pure function of (resolved type +
  prescan). It is extracted into a shared classifier helper that BOTH the legacy
  codegen path and THIR lowering call -- identical by construction, the same trick
  `resolve_stmt_binding_type` already uses. No new mutable pass; the "pre-pass" is
  the prescan that already exists + pure helpers. (Per-type spellings live on
  `TypeDef`; binding-level classification in a shared `forms` helper, per CLAUDE.md.)
- **Only slot numbering is genuine walk-order state** (`rebind_slots`, `__slot_N`)
  -- reproduced by a per-function emit counter, the established `iter_counter`
  pattern from increment 2.

So the legacy path is touched only by a verified extract-method refactor (the
decision logic is unchanged -> zero diff), and lowering and codegen cannot diverge
because they call one classifier.

#### Insertion rule (dissolves the ~150 direct sites)

Every slot has a form (field / container / `Own` -> STORAGE; param / return /
yield / borrow-local -> BORROW; value type -> VALUE; storage-form locals ->
STORAGE). Callers do not pass raw `dst_form` / `is_const` / `move`; a boundary
API carries the decision:

```python
def required_form(slot) -> Form | None:        # None == no form axis (value type)
    ...
def coerce_form(expr, slot):                    # the single insertion door
    f = required_form(slot)
    if f is None or expr.form == f:
        return expr                             # asserts no axis applies for value
    return THIRFormConvert(value=expr, result_type=expr.result_type,
                           form=f, is_const=slot.is_const, move=slot.move)
```

Today's ~12 predicates + 22 side-sets + per-site re-derivations collapse into two
carried facts read here: the source's `form` and the slot's form. This is the bulk
of the win and the bulk of the risk -- the side-sets encode subtle const / rebind /
suspension / null-state facts the tags must preserve to stay byte-identical.

#### RefType (Open Q 12) -- narrowed dissolution

A borrow-form non-value value is exactly what `Ref[T]` marks today; with `form` on
the expr it is redundant (`to_cpp_stored() -> val_or_ref<T>` is the generic-slot
storage form, `is_ref_param()` rvalue-temp binding is a BORROW param slot, lambda
`-> T&` is a BORROW return). Scope of THIS resolution: THIR introduces NO new
`RefType` use, and `RefType` stays frozen. FULL removal of `RefType` from the type
system is a LATER gate (rung F5/F-final), after the generic-slot (`val_or_ref_t`)
and lambda-return cases are proven -- not blessed up front.

#### Admission gates key on the value form (2026-09-05)

The same axis governs which types a lowering arm ADMITS, not only how a value is
represented: the direction is that a gate asks whether the type's value form is
`ValueForm.BORROW_REF` rather than whether it is one of the builtin container
names. `record_like` (a NOMINAL type the lowering binds and spells as one
object: a user record of any value form, a RECORD-category builtin, or a builtin
container -- the reference form carrying a `cpp_formatter`) is the admission
predicate where the site's whole decision is "record or container"; the
migration-era type-arg spelling fence is not part of it, because every render
spells a type through the resolver (`lc.render_type`) rather than the bare
`to_cpp()` the fence guarded; `_f1_record` stays where the render needs a
record fact (fields, member-init, parents, protocol conformance, the `Deref`
target, a record's `operator<<`, the upcast, arrow-ness via
`_record_class_binding`); where a record route and a container route once ran
side by side the routes are one path -- the borrow-local rungs, the owned decl,
the move-through and hoist flavors, the rvalue-call argument temp, the
Optional-ptr borrow binding -- and the one container-specific conjunct a merged
route may carry is the element family a STORAGE slot spells
(`_storage_family_ok`: `record_like` plus `_storage_call_ret` for a container),
because the element reads that follow render off the spelled slot type. The
five selectors still standing are each owned by a named unit (TODO.md, "THE
NEXT ORDER" item 1). Per-TypeDef facts carry the
residue a family list used to stand in for (`cpp_formatter`,
`param_cpp_formatter` / `param_mut_cpp_formatter`, `subscript_borrows`), and a
resolved stub method's `FunctionInfo` carries the rest. The element family is the
worked example: membership is "the receiver's `__getitem__` resolves to the builtin
`::tpy::__getitem__`" (`_native_getitem_index_param`), which admits the value-form
`Span` and `varargs` and excludes `set`, the bytes family and a user `__getitem__`
for stated render reasons rather than by omission. Two decisions stay
container-specific because they have no type to key on: literal construction
(`[..]`, `{..}`, comprehensions -- there is no callee) and pending-literal
resolution. Gates still keyed on a container NAME outside those two remain:
`scripts/thir_migration/review/container_gates.py` counts about 90 family
enumerations. The ordered ladders that carried a container arm below the record
one (return, field write, if-expr, the method-argument sinks, and the method
gate's return half) were merged to one arm on 2026-09-05, once each recorded
failure had been classified as an admission problem with zero render diffs. The
residue classes identified so far -- the receiver membership list, the method
gate's receiver/overload half, and the ordinary admission gates that were not
reached -- are filed as their own TODO entries; the rest of that count has not
been audited.

#### The read arms own the form tag (2026-09-30)

A sink reads a source's form off the lowered node; it does not re-derive it
from the declared type. That holds today for the read arms a field write
consumes, made honest in one unit:

- the general field-read arm (`_member_read_form`) tags every member with a
  form axis -- a record, an Optional, a union, a storage tuple -- `STORAGE`,
  because a member IS the field's own storage, never the `T*` / pointer
  variant a borrow of the same type is; `_lower_field_source`, the second
  field-read arm the borrow-local bindings and tuple lifts call, has always
  tagged `STORAGE`, so the two writers agree -- folding them is open work;
- the container-subscript arm tags an Optional, union or storage-tuple
  element `STORAGE`; a record element keeps `BORROW`, the `T&` the dunder
  yields;
- one wrapper-level stamp in `_lower_expr` tags a call `STORAGE` when the
  callee's declared return is `Own[T]` or `Own[T] | None` (the expression
  type sema stamps has the `Own` stripped) and `BORROW` when the result is a
  pointer-repr tuple (`std::tuple<..., T*>`). The arms still write the
  str/bytes view-family half of a call's form themselves; the wrapper adds
  its axis only where they left `VALUE`, an ordering contract that one
  `_call_result_form` read by both would remove (TODO).

The name read carries `indirect` beside `deref` (`raw_pointer` = the bare
`T*`), since `BORROW` spells both `T&` and `T*`. That is representation on an
expression node, the same exception `deref` already is to "representation
stays on `THIRVarDecl`". The consuming method's receiver carries the same
`is_last_use` / `is_movable` stamp a name does.

The field-write sink (`thir/lower/field_write.py`) decides its render from
`form`, `_node_moves` and `result_type`; what it still asks before lowering
-- the whole-binding read, the pointer-slot lift, the global-slot use, the
view shim's param type -- is the residue the TODO entry's step (e) names.

The PLACEMENT half of the slot contract -- whether a statement exists to
hoist a declaration before -- is a fact about the whole expression tree
under a sink, and `_ExprUse` is deliberately not propagated into
subexpressions, so the contract only DECLARES it (`SlotPlacement` on the
use) and the lowering context carries it as a scope
(`_LowerCtx.placement_scope`): the member-init value, the base-init
arguments and a lambda body open the no-statement scope, a comprehension
body reopens the statement one (its element temps flush inside its own
loop). The sink's flush GRANT (`allow_temps`) is a different fact the
admission rows keep reading unchanged; the scope answers one
question, "may a declaration be hoisted here", asked where a node whose
render hoists one is created (`_hoisted`, over one node-kind predicate
the validator shares), where a select would emplace a slot, and where an optional iterator temp is a render
choice. A creation with no statement raises a reject marked `no_flush`;
the ctor driver reads that mark as "demote to the body" and any other
reject as a reject, so one lowering settles the verdict. A creator that
bypasses the factory fails the validator loudly rather than demoting --
closing that class needs the hoist decided on the node kind, not at the
creation site, and is open. The
invariant -- a member-init or base-init cell holds no statement temp
outside an inner flush region -- is the validator's, not a scan the
lowering re-runs.

Not yet honest, and named so: a record borrow-returning call stays `VALUE`
(MIR's `_borrowed_expression` asserts a call is tagged `VALUE`, and a
`@property` getter's storage-reference result is lifted through a
`THIRFormConvert` that would become a no-op); a `THIRCtorCall` rvalue stays
`VALUE` beside the `STORAGE` an `Own[record]` call gets; an all-`Own` tuple
result has no pointer-repr element and reads as a value; an owned local's
name read stays `BORROW`. Each is a consumer-visible convention until its
unit moves it.

### Form rollout ladder (F1 -> F-final)

The form work is a sub-stream of the THIR migration, sequenced one family/
representation-subset at a time, each rung gated by zero snapshot diffs, each
closing named `THIR_FORM_INVENTORY.md` items. The eligibility gate keeps every
intermediate state correct (anything unsupported falls back to the proven AST
path), so "partially migrated" is never "broken." Completion is the
defined end state: the gate excludes nothing form-related and the AST form-codegen
is deleted (F-final). Buggy exhibits (BUGS.md union match-capture, `key=` lambda,
async/await union) are migrated FAITHFULLY (byte-identical, bug preserved -- THIR
makes the conversion visible); fixing them is a separate churn-accepting follow-on
that the migration enables. Migration-complete != bugs-fixed.

**The table below is a SCOPE map, not a status board.** The per-rung "landed"
tags stopped being maintained after F3, while the corpus went on to route
unions, generic slots and view locals in practice -- so an untagged F4/F5/F6 row
means "nobody re-tagged it", not "not started". The one row that is still
genuinely open is **F-final**: `RefType` is live in `typesys.py` and the AST
form codegen has not been retired. For what is actually left, read the fallback
tally, not this table.

| Rung | Scope | Closes (inventory) |
|------|-------|--------------------|
| **F1** *(landed 2026-06)* | single-assignment non-value **record** locals + Optional[record] storage->borrow read (`T&` alias, lvalue `optional_to_ptr`, is_const propagation, record borrow params) + scalar field reads; excludes reassigned/rebound/rvalue-slot, container/cross-module/native records, and calls passing a non-value arg (auto-move). Container locals fold in with F3 | most of section 1 non-value-local + section 4 read |
| **F2** *(landed 2026-06)* | reassigned/rebound locals + Optional borrow<->storage write/return. **Landed:** reseatable `T*` pointer-locals -- **lvalue** reseat (`&(...)`, no slot, F2a) and the **rvalue** `__slot_N` rebind machinery (F2d) -- `->` reads; the optional-field/return **write** from a borrow source, copy (`ptr_to_optional`, F2b) and **move** (`ptr_to_optional_move`, F2e); the storage-Optional **return** + **`None`** write/return (`std::nullopt`, F2c). **Deferred -- separate frontiers, not form cells (AST path):** ctor-MIL (M3 constructor frontier; M1 landed 2026-06 so F2 routes corpus method bodies, and M3a landed 2026-06 for pure-MIL scalar inits -- the `ptr_to_optional` MIL cell is M3b) and the call-arg / call+`copy()`-write sources (non-value call args + auto-move). | section 3b slot machinery + section 4 write/lift (the local + free-function sites) |
| **F3** *(landed, increments 22-28)* | Tuple form (per-element pointer/optional mask). **Landed:** storage->borrow read (`tuple_to_pointer`: borrow-form tuple return + storage-tuple `auto&&` alias locals) + borrow->storage write (`tuple_to_storage`, tuple-field write off a borrow tuple param) for pointer-repr tuples of scalar / F1-record / `Optional[F1-record]` elements; and the tuple-subscript family (statement-shape axis, incr 25-28): value / record / `Optional[record]`-element `t[N].field` reads (`std::get<N>`, `->`/`.` per element form, `deref_check` for unproven Optional) + record-element `t[N].field = /+= <scalar>` writes. **Deferred (AST path):** storage-Name alias sources, reassignable BORROW_TUPLE / OPTIONAL_BORROW_TUPLE, the `tuple_to_storage_move` `Own[tuple]` move arm, loop-var / unpack sources (statement-shape axis), Optional-field/tuple-field writes through a subscript, tuple-literal MIL construction | section 1 tuple, section 4 tuple sites |
| **F4** | Union form (`to_ptr_variant` / `to_value_variant`, value/ptr-variant split, the 3 consumer-dictated exhibits) | section 1 union |
| **F5** | Generic-slot form (`val_or_ref_t` / `val_or_ptr_t` over TypeParamRef) | section 1 generic, section 3c RefType (begin) |
| **F6** | str/bytes view split (Open Q 11 scope extension) | section 3d |
| **F-final** | `RefType` removal + AST form-codegen retirement | section 3c, end state |

**Callable-kind axis (M1-M3), orthogonal to the form ladder.** The form rungs run
over a callable slice that was free-functions-only; the method/constructor frontier
widens *which callables* route, independent of *which type families* do. **M1
(landed 2026-06): plain instance methods** -- `self` modeled as an F1-record
pointer-receiver (`THIRSelf` -> `this`, arrow reads), readonly methods admitted
with a const `self`, value-scalar params only. This is what first put F1/F2 under
the whole-corpus byte-diff over *real* corpus code (every prior form rung routed
zero cases -- their shapes live in methods). **M2 (landed, increment 12): record
params on methods** -- a method-level const-param verdict read from the owning
record's `const_borrow_params`. **M3 constructors / member-init-list: M3a (landed,
increment 14)** -- the whole-ctor `THIRConstructor` node (MIL split from body) + a
MIL-tail emitter, for the pure-MIL scalar slice (flat record, every init hoists,
empty body). **M3b-copy (landed, increment 15)** -- record / `Optional[record]`
MIL fields, copy arm: the `ptr_to_optional` cell (now closed) + `None` + non-own
record-param copy, reusing F2b/F2c in MIL position. **M3b-move (landed, increment
16)** -- own-param `std::move` sources (THIR's first `std::move` emit). **M3b-rvalue
(landed, increment 17)** -- record *value* sources (`_is_record_value_source`:
param name / ctor-call rvalue / param field-read, constructing the field directly)
+ own-optional params + the `copy()`-on-Optional source; `self.<record field>` reads
stay deferred. **M3c-trivia (landed, increment 18)** -- docstring / `pass` non-init
bodies (`THIRNoOpStmt`, no code; the body-brace shape is the only output difference),
the first ctor-body statement shape. **M3c-demotion (landed, increment 19)** -- the
hoist/demotion split: non-hoistable / post-chain-break field inits demote into the body
(lowered via the shared `_lower_stmts`/`_lower_stmt` path); only the `chain_broken`
cascade needed explicit reproduction, the rest subsumed by node-local admission.
**M3d-1 (landed, increment 20)** -- a single F1 base: `super().__init__` -> a structured
`THIRBaseInit` prepended to the MIL. **M3d-2 (landed, increment 21)** -- multi-base
(parent-order-sorted base inits + the `BaseN.__init__` form) + inherited-field writes
(body branch + `expr_reads_self_field`). The ctor frontier (M3a-M3d) is complete; the
remaining ctor cells are cross-axis-blocked (F3+ field forms, the record body-write rung,
native/generic-record frontiers).

**F1 is the pre-commit gate** (Codex review condition + the spike's real test): an
end-to-end byte-identical lowering of one real non-value function through THIR-
with-form, proving the form facts are DECIDED correctly at lowering across every
optional case in the corpus -- not a hardcoded node. **Explicit success criterion:
if `is_const`/`move` turn out to need walk-state the prescan does not carry, the
"pure classifier" assumption does not fully hold and the node shapes are revisited
BEFORE committing F1.** That is the one risk that could push back up the design.

*Gate MET (2026-06, in-tree implementation): F1 lowers real non-value functions
through THIR-with-form, byte-identical to the AST path across the full corpus with
`--thir-codegen` forced. The criterion held -- `is_const` is a pure sema read
(`FunctionInfo.const_borrow_params` for the param receiver + `ReadonlyType`), never
walk-accumulated `const_indirect_locals` (an F1 receiver is a param, and the const
F1-local case is tracked in a per-function `const_locals` set seeded in source
order); `move` does not enter the read slice. No node-shape revisit was needed.*

*Gate run 2026-06 (throwaway spike, PASSED): over 162 compiled sources,
`_is_const_union_source` (the optional-read const decision) returned True only via
`const_ref_params` (a sema/Phase-2 fact) -- never via the walk-accumulated
`const_indirect_locals`. So `is_const` for the read slice is a pure sema read; the
form classification (`OptionalType` + `uses_pointer_repr()` +
`is_storage_form_optional_source`, i.e. `isinstance(.., TpyFieldAccess)`) is a pure
function of type + expr shape; and `move` does not enter the storage->borrow read
slice (it is an F2 borrow->storage concern, and is seeded from sema's
`function_movable_locals` regardless). The transitive `local->local` const path was
not exercised and, if it arises, is forward-source-order reproducible
(decl-before-use). The pure-classifier assumption holds -- F1 is cleared to
implement.*

## Rollout Plan

### Migration Strategy

The migration should be incremental. The compiler currently has three concerns tangled
together:

- sema as the source of truth for typed program facts
- borrow/move analysis spread across sema and codegen
- codegen reading directly from analyzer internals

Those should be separated in phases. The key sequencing principle:

- **switch codegen to THIR before switching codegen to MIR**

THIR is structurally close to the current codegen input, so it is the right first
boundary. MIR should first become the analysis source of truth, and only later the
emission source of truth.

#### Migration-shape exploration + decision (2026-07)

**HISTORICAL.** Everything from here to the end of "Migration findings
(distilled)" describes the THIR body-codegen migration, which completed on
2026-09-03 when the AST body emitters were deleted. The mechanisms it names --
whole-body fallback, `no_thir.txt` markers, the case dial, the ratchet, the
AST oracle pass and the `--thir-*` pytest options -- are all gone. Read it for
the reasoning and the lessons, not for how the compiler behaves today.

A spike (branch `spike-thir-invert-gate`) explored two ways to escape the
original "gate-first, whole-body routing, delete at ~100%" shape, whose payoff
(AST deletion) is all-or-nothing at the very end:

- **Lever A -- try-lower-then-fall-back.** Replace the ~358KB predictive
  former eligibility gate (`checks.py` + `predicates.py`) with: attempt to lower
  any body, raise `ThirUnsupported` at the point a construct can't be carried,
  catch at the body boundary, fall back to the AST path. An audit confirmed THIR
  lowering is side-effect-free w.r.t. codegen state (all working state on a
  per-call `_LowerCtx`; analyzer/registry access read-only; no AST-node tagging),
  so abandoning a partial attempt leaves no residue. Self-contained arms (`del`)
  invert byte-identically. **Caveat found:** `_lower_expr` arms *assume*
  eligibility -- dropping the gate without hardening them produces silent
  miscompiles (not clean rejects), so inline asserts/rejects are required as the
  gate is removed.

- **Lever B -- mixed-mode per-statement AST-escape.** A `THIRAstEscapeStmt` wraps
  a statement THIR can't lower and emits it by re-entering the AST emitter, so a
  body routes *partly* through THIR with islands of AST. Validated
  byte-identical over the corpus; the mid-body scope bridge (an escaped statement
  reading a THIR-declared local) is tractable for plain-value locals via a small
  ctx-registering `ScopeSink`, and mechanical (mirror `cpp_local_representation`)
  for non-plain. **DEFERRED / reverted from the working branch**, for two reasons:
  (1) escape-*first* as a sequencing strategy costs weeks of scaffolding for the
  same porting work, and (2) escaped constructs count as "routed," which corrupts
  the honest per-case "this case's user code is fully THIR" metric. It remains a
  validated option to revisit if whole-body fallback proves too coarse.

**Decision: per-case incremental migration, whole-body fallback.** THIR is
scoped to *user* modules (lib/tpy + stdlib stay AST for EMISSION -- a stable leaf
behind the user-code boundary, so a case migrates on its own code, not its
imports). A body either fully routes THIR or falls back whole to AST (Lever A).
Per-case `no_thir.txt` markers (managed by `--thir-classify`, un-mark candidates
surfaced by `--thir-check-flip`) track migration; a case is "clean" iff every user
body routes with zero fallback. The predictive gate is dropped in favour of Lever A +
inline asserts. The real-build flip to THIR-primary (the enforced sema->codegen
boundary) is deferred until porting velocity earns it; stdlib THIR is needed only
for the eventual full AST deletion, not for the near-term separation/debuggability
win.

*Where that left the tree (2026-08).* The marker scheme has run its course: zero
`no_thir.txt` remain and the dial necessarily reads N/N, so neither the markers nor
`--thir-check-flip` can select work any more -- they now only guard against
regression. The stdlib is still AST-scoped for real emission, but it is measured
and byte-diffed through THIR by two always-on gates (the per-case wide oracle and
`tests/test_stdlib_render_coverage.py`), and its fallback reached ZERO on 2026-08-30,
so no routing distance to the AST deletion remains -- what is left is the
cutover itself. The tail that closed it was recorded as decision-bound rather
than admission-bound (filed defects, design forks, chained sites); every one of
those statuses had been read off a reject tag instead of ablated, and each was
pessimistic.

##### Ratchet + tiered measurement (2026-07 refinement)

The default-on byte-diff proves the emitted C++ is *correct*, but NOT that THIR
was *used*: a whole-body fallback emits byte-identical AST, so a migrated case can
silently regress THIR->AST with a green diff. The marker is therefore a
**contract, not just a routing switch**: an *unmarked* case that falls back any
user body FAILS the comp phase (the ratchet). Byte-diff catches divergence; the
ratchet catches fallback. A new case using an un-migrated construct fails until
migrated or marked (`--thir-classify`).

Measurement is separated from emission and tiered so the always-on cost is ~zero:

- **Tier 0 -- ratchet (free, always).** Unmarked cases already *emit* via THIR, so
  asserting zero fallback adds no pass. This is the correctness win.
- **Tier 1 -- full-corpus dial (free, always).** `tpy| thir cases: N/M migrated`
  spans the whole corpus, and marked cases are counted not-migrated *from the
  marker* (marked <=> has fallback). The overlay does now yield a per-case
  fallback count for them, but the dial ignores it on purpose: the marker is the
  contract, and a marked-but-clean case is benign porting progress, not a
  regression to fail on. `--thir-check-flip` turns that drift into un-mark
  candidates. **SATURATED since 2026-08** (zero markers, so the dial is N/N by construction): the dial
  still reports, but it can no longer select work -- only regress.
- **Tier 2 -- exact sweep (whole corpus).** THIR emit + byte-compare runs for
  EVERY case on a plain run -- the marker gates the ratchet, not the overlay,
  because fallback is per-body and a marked case still routes bodies that must be
  diffed. Only the faces/shapes coverage metrics and the marker-ignoring
  (no-ratchet) mode stay behind `--thir-codegen`.

A cheap "gate-only, no-emit" probe to *re-measure* marked cases every run was
considered and rejected: the `resumable` (async/generator) eligibility is coupled
to emission (it needs the live codegen ctx), so a gate-only probe would undercount
async fallback and mint false-clean cases. The overlay measures them by real emit
instead, which is exact and (measured) under a second over the corpus. Lever A
catches lowering rejects at the sync-function, constructor, and resumable
boundaries, so converted gate families can defer their decision to lowering
without crashing the ratchet.

With those boundaries in place the whole-body (`_body_eligible`), per-statement
(`_stmt_eligible`), and expression (`_expr_eligible`) predictive traversals were
removed outright. Each statement and expression kind now checks admission beside
its lowering arm; recursive lowering discovers the first unsupported child and the
body boundary discards the partial attempt. Statement policy and the
`match`/`with`/`try` structure gates have since been removed: statement rejection
happens in the lowering arm, while `match` and `for` classifiers return a strategy
that lowering consumes. Expression consumer-shape helpers (slot, call, and method)
remain as local classifiers consumed by lowering; condition and binary-operator
lowering check admission inline and raise `ThirUnsupported` directly. None of these
helpers provides a separate whole-expression preflight.

Here, **predictive admission traversal** means walking descendants only to
answer "can this lower?" and then walking the same descendants again to build
THIR. It does not include:

- semantic prescans needed to establish Python function scope (reassignment,
  shadowing, and whole-function local bindings);
- strategy classifiers whose result is consumed by lowering (match dispatch,
  comprehension/for-loop shape, and constructor MIL-vs-body placement);
- walks that construct emitted metadata (generator captures and termination
  flags); or
- fallback diagnostic classification after rejection is already decided.

A strict post-conversion sweep removed the remaining constructor duplicate:
MIL admission no longer repeats the recursive body-local reference check after
the constructor strategy has already selected the initializer for MIL lowering.
Unsupported nested statements and expressions are now discovered by recursive
lowering and reported with `ThirUnsupported`; there is no separate recursive
admission pass.

##### The cutover (2026-09-02/03)

For most of the migration both paths ran every session, because each was the
only check on the other: first the AST authored and THIR rode as an overlay,
then that was reversed. The reversal cost AUTHORSHIP INDEPENDENCE rather than
the byte-diff -- the snapshots stayed committed files, but stopped being
regenerable from an oracle the migration did not own.

The deletion closed it. The four AST body emitters are gone, so a lowering
rejection no longer discards a body's partial THIR in favour of a second
emitter: it is a `ThirRejectError` naming the blocking construct. That also
retired the mechanisms built to police the two-author regime -- the move-verdict
and binding-set audits, the error-path diagnostic-author gate, the reject
tally, the `no_thir.txt` markers and the case dial. What survives as the
correctness oracle is the committed `expected/` tree, compared byte-for-byte on
every run.

#### Phase-1 spike validation (2026-06)

A throwaway probe lowered one arithmetic function (`def add(a, b): c = a + b + 1;
return c`) to immutable THIR nodes carrying the sema facts, then emitted C++ from
THIR with **no `SemanticAnalyzer` reference** -- output byte-identical to the
current AST-driven codegen. Confirmed empirically:

- **The boundary is real and the leaf emit layer is already analyzer-decoupled.**
  `result_type` (`get_expr_type`) and `resolved_binop` lower onto nodes with no
  friction, and the existing C++ leaf helpers (`expand_cpp_template`,
  `get_dunder_cpp_template`, `TpyType.to_cpp`) produced the binop emission
  unchanged, just fed from THIR instead of side tables. Most of codegen is
  already a `fact -> string` function; THIR only changes where the facts come
  from. The non-form expression/statement coverage is mechanical breadth, not
  hard depth -- a few focused weeks, low conceptual risk.
- **Three friction points, all already named above as rollout prerequisites,
  confirmed real:** (1) implicit coercions are NOT materialized -- a literal `1`
  kept `result_type = IntLiteral(1)`, so coercions must become explicit
  `THIRCoerce` nodes at use sites, not just copied types; (2) liveness/movability
  facts live in per-function context, not a top-level analyzer table, so lowering
  must run per-function in scope; (3) local declared-types must be captured
  explicitly onto `THIRVarDecl` (codegen currently re-derives them).
- **The form facts (Open Questions 9/11) are the genuine long pole.** The slice
  is all value types, so borrow/storage form never arose -- the spike does NOT
  de-risk it. Form-as-an-IR-fact should be *designed before* the form-carrying
  nodes are written, not retrofitted. *(Done 2026-06: the form design is now
  resolved -- see "Form as a First-Class THIR Fact" + the F1-F-final ladder. A
  second spike validated the `THIRFormConvert` node against the live `convert()`
  chokepoint; F1 remains the pre-commit gate for the lowering-side detection.)*

Net: the non-form Phase-1 is a reasonable bet once the fact set is frozen (post
0.4.0); the form decision is the gating design work the THIR node shapes depend
on.

#### Compositional-gate principle (for migrating the next construct)

When porting a construct, first classify it:

- **Type-keyed** -- the emit is a pure function of a *resolved type* plus a
  recursive sub-gate (for-each element binding, comprehension loop var, subscript
  value-leaf read, container-param signature). Do **not** write an enumerated
  per-family eligibility whitelist; write ONE compositional predicate -- "the type
  renders identically AND the body / sub-expressions route recursively" (see
  `_for_each_elem_binding_ok`, `_container_param_renders`). The recursion collapses
  the whole family in one gate.
- **Form-sensitive** -- the emit depends on the expression's shape/ownership, not
  just its type (call-args, field-writes, container-literal element STORAGE,
  container mutation / `__setitem__`). These gates **cannot** collapse compositionally
  until the form fact is materialized on the node -- which is the migration's core
  direction (see "Form as a First-Class THIR Fact").

Corollary: clearing a type-keyed gate typically reveals a downstream form-sensitive
blocker in the same bodies, so per-wave shape yield stays small. Measure arms
(the finite emit surface), not shapes.

#### Migration Principles

1. **Preserve behavior first.** Early THIR and MIR work should not intentionally change
   generated code or diagnostics.
2. **Make phase boundaries explicit before changing semantics.** First remove analyzer
   coupling, then move borrow/move logic into MIR, then strengthen enforcement.
3. **Advisory first, safe mode later.** The MIR loan checker must initially preserve the
   current migration-friendly warning behavior. Safe opt-in enforcement is layered on
   after the analysis is stable.
4. **Keep explicit low-level tools.** `Ptr[T]` remains available in both default and safe
   mode; only pointer arithmetic / unchecked pointer fabrication stay in `tpy.unsafe`.
5. **Run old and new analyses in parallel during transition.** MIR diagnostics should be
   compared against existing sema behavior before MIR becomes authoritative.

#### Recommended Rollout

Before any codegen switch, the IR must first be complete enough to replace the current
analyzer coupling:

- **Before THIR-backed codegen**: THIR must cover per-function layout/scan facts,
  module options, explicit coercions, view/literal metadata, generator frame metadata,
  and declared globals.
- **Before MIR-backed codegen**: MIR must preserve narrowing, structured region tags
  for reconstructable control flow, and both return-tier and throw-tier error handling.

The numbered list below is the ORIGINAL rollout plan, kept for the reasoning
rather than as a map of the tree: the paths have moved (`tpyc/thir/lower.py`
became the package `tpyc/thir/lower/`), and `tpyc/mir/` does not exist. Steps
1-3 are long done.

1. **Define THIR nodes** in `tpyc/thir/nodes.py`
2. **Implement `lower_module()`** -- now the `tpyc/thir/lower/` package
3. **Add `--dump-thir`** to CLI
4. **Create a `THIRCodeGenContext`** that reads from THIR instead of analyzer
5. **Migrate codegen modules one at a time** (expressions, statements, functions, records)
6. **Remove analyzer references from codegen**
7. **Define MIR nodes** in `tpyc/mir/nodes.py`
8. **Lower THIR -> MIR** in `tpyc/mir/lower.py`
9. **Add `--dump-mir`**
10. **Implement MIR liveness + move/copy passes**
11. **Implement MIR advisory loan checker**
12. **Run MIR checker in parallel with existing sema borrow/move logic**
13. **Make MIR authoritative for ownership/borrow diagnostics**
14. **Add safe opt-in mode** on top of the same MIR analysis
15. **Switch codegen from THIR to MIR** once MIR carries enough information for readable,
    stable emission
16. **Retire old sema/codegen ownership logic**

The distinct-types-via-inheritance fix for the str/bytes generic param ABI
(Open Questions item 8) is **independent of this rollout** -- it operates on the
runtime type layer and the C++-template-keyed paths and does not require THIR/MIR
to land first. It can be scheduled separately whenever the team is ready.

#### Why THIR-Backed Codegen Comes First

Jumping directly from "analyzer-backed codegen" to "MIR-backed codegen" would mix four
independent risks:

- new IR design bugs
- new lowering bugs
- new borrow/move analysis bugs
- codegen porting bugs

Switching to THIR first isolates the representation migration from the ownership-model
migration. MIR can then mature as an analysis artifact before it becomes the executable
source.

#### Behavior Expectations By Stage

- **THIR stages**: no intentional behavior change; output should stay identical
- **Early MIR stages**: analysis/debug only; codegen still reads THIR
- **MIR advisory stages**: diagnostics may be compared or duplicated, but default
  severity stays warning-level for migration-friendliness
- **Safe mode stages**: selected MIR violations become errors only under explicit opt-in
- **MIR-backed codegen**: ownership and control-flow decisions now come from MIR, not
  sema/codegen heuristics

Run the full test suite at each stage. THIR migration should be behavior-preserving;
later MIR stages may intentionally alter diagnostics or move/copy decisions, but only
when the corresponding phase is made authoritative.

### Migration findings (distilled)

Durable, non-obvious lessons from the incremental THIR body-codegen migration
(the dated per-increment log they were extracted from has been dropped). Grouped
by theme. **HISTORICAL**: these were written while two emitters ran side by
side, so the mechanisms they name (overlay, fallback, ratchet, markers, the AST
oracle) no longer exist. The lessons about what a green diff does and does not
prove still apply to the committed-snapshot oracle that replaced them.

**Byte-diff as the correctness oracle.**

- The whole-corpus byte-diff is the oracle -- every case's overlay is diffed on a
  plain `uv run pytest`, so a `--no-exec` run is the fast form; the gate/lowering
  *mirror discipline* exists to feed it. It repeatedly caught real
  mid-cell divergences (a guarded match mis-promoted to the if-elif-guarded chain;
  a view-resolved promoted str local over-moved; a container-element over-move; a
  BigInt frame-field write rendered position-blind). Trust the diff, not the reasoning.
- The AST oracle pass covers a case's **local** modules, and the RATCHET is scoped
  to them, so a case migrates on its OWN code's portability, not its imports'.
  Routing itself is not scoped: THIR authors `lib/tpy` and the stdlib too. Their
  check is the stdlib oracle (`--no-thir-stdlib` turns it off), which re-emits them
  through the AST and diffs that against the emitted output -- the only oracle
  stdlib emission has.
- **Green byte-diff does not mean a face is covered.** A gate arm or render no corpus
  case reaches is invisible to the diff -- several latent call-arg bugs, and a
  template-keyword miscompile, sat in exactly such witness-free faces. Register each
  new gate/render face in `faces.py` and require a witness (unit or corpus). Zero-witness
  branches hide real bugs.
- **A gate arm can be UNREACHABLE end-to-end even though both halves look right:**
  admission passes but the recursive child lowering re-rejects (a different gate --
  result-use, pending-type resolution) and the body falls back, so the arm is dead,
  not wrong. Wave-6 hit this twice: the Own-slot container copy-temp compared the
  slot against an unresolved `PendingListType` read, and the Own-slot method-call
  rvalue arm admitted a shape whose INNER call then failed the record-result gate
  until the arg lower threaded BORROW_BIND. The unit pin that asserts the arm
  actually routes (not just that the predicate returns True) is what surfaces this.
- **Sema attaches an EMPTY-params synthetic ctor fi for records with no own
  `__init__`** (inherited param-ful inits, `@native` records, TypedDict) -- the real
  param list lives in `ri.init_params` triples (name, type, default), which the AST
  arg loop reads as a fallback. Any THIR arity/param logic keyed on `fi.params`
  silently mis-rejects these; route through `_ctor_effective_params`.
- **The same emit can be per-arg-loop, not per-construct:** the consuming
  `own_iter(std::move(b))` wrap for an `Iterable[Own[T]]` name arg fires on the
  STUB-METHOD arg loop but NOT the free-native loop (`xs.extend(b)` wraps,
  `sum(xs)` binds bare) -- the whole-corpus byte-diff caught the over-wrap
  mid-cell. Mirror the AST's per-loop split, not the slot type alone; where the
  split leans on unrelated gates keeping a face off THIR, leave a COUPLING note
  at the arm.
- **Emitted-wrap ORDER is a fact to mirror, not derive:** the container-element
  render is wrap-for-the-owned-slot THEN move (`std::move(std::string((*a)))`,
  the make_vector escape). THIR had the converts after the move; the divergence
  surfaced only when adjacent routing let a body reach the shape -- codegen-side
  seeding facts (`seed_param_locals` movability) are not in the raw sema sets
  the lowering mirrors, so each such fact needs an explicit front-run.

**Measurement / tally honesty.**

- **F1-ness is phase-dependent**: `_f1_record` (and any spelling-keyed
  predicate reading `to_cpp()` / `native_cpp_names`) can answer differently
  at ANALYSIS time (a bare `activate_compiler` after `compile()`) vs at EMIT
  time (inside `generate_code`, the name maps populated). A gate probed
  outside generation can look like it needs a new emit arm that is in fact
  unreachable -- the record-writes wave built (and then deleted) exactly such
  an arm for the covariant Optional-field write. Probe F1/spelling questions
  inside `generate_code`; where a gate's admission relies on emit-time
  F1-ness, guard the emit side with an explicit reject, not an assumed arm.
- A face asserted through an OR-hedged pin (`assert a or b`) is NOT
  witnessed -- the hedge hides which arm fired. Pin the exact face; shared
  witness names that also fire from other positions (e.g. free-call vs
  method-position `optptr.name`) alias the zero-witness metric, so note the
  unit-pin-only positions explicitly at the gate.
- Corpus fallback/routing tallies are **per-case-inflated**: the stdlib compiles into
  every case, so ONE stdlib body counts ~3,400x. Sequence by *distinct bodies* / the
  shape meter, and name the marquee stdlib bodies -- never by raw body counts.
- The first-reject fallback reason **masks** the true blocker. Every "measured elephant"
  (imported callees, `method.marker`, `overload_set`, `static.generic`, `subscript.elem_family`)
  shrank an order of magnitude once drilled: measure with a sub-classifier before planning
  a frontier, not from the coarse tag.
- Exclude declaration-only stubs and bare-`@native` methods from the routing population
  (`is_stub`) -- the AST never `gen_body`s them, so they register phantom "routed" entries.

**Probe-first; frontiers are often smaller than they look.**

- Several planned "architectural design passes" collapsed to a **one-arm localized change**
  once probed (builtin-`@builtin_type` receivers, str name-receiver methods, `@native`
  records -- the last needed exactly ONE new stamp, the field rename). Probe the real
  divergence before assuming a frontier is architectural.
- The non-F1-record frontier (and resumable frames) are **all-or-nothing per body**: a
  body uses its record/coroutine pervasively, so nothing routes until the *whole* touchpoint
  set is covered. A partial gate widening routes +0 and misleads.

**Form / borrow gotchas.**

- A pointer-repr tuple/str **name** is BORROW form despite `is_value_type()`; a convert
  source must never be mislabeled VALUE.
- `ptr_to_optional` is reserved for **borrow-`T*`** sources; a record *value* source
  constructs the field (or its Optional) directly (`opt(Inner(v))`).
- **Two distinct facts, once conflated:** `lc.sema_movable_locals` is sema's raw
  "this local is owned"; `lc.movable_locals` is the WORKING set the move sites read,
  which a name joins only via `promote_movable` at a decl arm that promotes -- the
  mirror of `StatementGenerator.promote_movable`. Seeding the working set from the raw
  fact moves a value-typed local (a view-promoted `str`, a BigInt) and a ptr-variant
  alias where the AST copies. The promotion rule is per-ARM, not global: the frame and
  owned-tuple arms promote value-typed names, the tier-1 fallthrough does not, so no
  type-keyed filter over the raw set can express it.
- **Separately**, `_container_elem_move_source` carries a SINK rule, not a set
  correction, and the two sinks it serves DISAGREE on a value-typed movable payload:
  a container literal moves it (`make_vector<BigInt>(std::move(n))`), a tuple literal
  renders it bare (`{n, ...}`). One shared helper serves both, so the rule keys on the
  sink the caller represents (`tuple_elem`), never on the payload type -- collapsing it
  either way diverges one side.
- **Own params own their storage** -> they read STORAGE form (a validator sink-rule catch;
  an Own-param BORROW mislabel was the rule's first live catch).
- **Position-blind vs target-typed render:** the async frame-field-write arm and the async
  return-value arm render position-blind (a literal at a wider slot stays bare `42`, never
  `::tpy::BigInt(42)`), unlike the sync target-typed arms. Lower these through `_lower_expr`,
  not the return-coercion path.
- Arg-temps flush only at the simple-statement positions (expr-stmt / var-decl init /
  name assign / return / field write / print arg); nested-call and while/elif-condition
  args have no flush point and stay AST.

**Anti-drift: extract shared facts, don't mirror.**

- When a routing decision or a C++ spelling is shared between the AST path and THIR,
  **extract** it to a shared helper (`module_qualified_callee_cpp`, `imported_free_callee_cpp`,
  `partition_optional_cases`, `substitute_type_params_simple`, `expand_fi_template`) rather
  than reproduce it -- the two paths then *cannot* drift. Mirror only where extraction is
  infeasible; the byte-diff is the anti-drift net for what stays mirrored.
- Compute **one** routing fact, consumed by both the gate and lowering (`_free_callee_kind`,
  `_marker_call_kind`, `_match_union_route`, `_comp_route`). A gate that accepts what lowering
  then rejects (or vice versa) is a latent divergence.
- The `gen_body` THIR interception keys on `id(func)`, so **any** callable in a multi-entry
  overload set must be rejected (each stub emits the shared impl with per-stub dead-branch
  facts). Resumable bodies key `THIRResumableBody` by `id()` of skeleton-held AST nodes for
  the same reason.

**Branch-scoped state: one registry, one pop.**

- Per-name classification sets on `_LowerCtx` are SCOPE state, not accumulators:
  a branch lowers over a per-branch `declared` copy, so its lc-set registrations
  must pop with it. Hand-listed restore sites accreted six parallel mechanisms
  and each eventually missed a set -- the confirmed worst case was
  a value-opt binding registration (today the `value_opt_bindings` map)
  leaking from a branch-local `x: int | None` decl into a
  sibling branch's plain `x`, rendering `(*x)` on an `int32_t` (uncompilable
  C++), masked only because no corpus case reused a name across sibling scopes.
  The fix is structural: `branch_scope()` restores every set in
  `_BRANCH_SCOPED_SETS` (mirroring the AST's `LocalScopeSnap`), a completeness
  unit test forces every new mutable `_LowerCtx` slot to be classified
  branch-scoped or function-scoped, and whole-set restore is symmetric -- it
  undoes in-scope REMOVALS too, so shadowing (the for-each frame/pointer
  shadow) needs no separate mechanism.
- Registration that must OUTLIVE a scope (with-targets, match full-binds, a
  nested def's name) is done by ORDERING -- the caller registers in its own
  frame, outside the inner push/pop -- not by an exemption API. This matches
  the AST exactly: `LocalScopeSnap` pops a with-target at an enclosing branch
  boundary too, so "persists" always means "registered one scope up".
- A per-arm `in_branch` reject standing in for a missing restore is a fragile
  guard: two Criticals and the leak above were exactly ordering-masked sites.
  With restores centralized, such rejects divide into RESTORE-guards (now
  liftable, see the TODO gate-lift worklist) and RENDER-guards (branch position
  genuinely changes emitted C++ -- hoist predecl placement, `loop_depth`
  re-entry) which must stay.

**Counters and numbering.**

- Emit counters that reset per **function** are per-function `_EmitState` ints (`iter_counter`,
  `match_counter`). Module-**cumulative** counters (with/try/try-except) draw from the live
  `ctx` counter through a ctx-backed sink so the numbering stays continuous across the
  module's bodies. Verify a counter's reset scope before choosing -- `match_counter` was assumed
  cumulative and is per-function.

**Mirroring pre-existing AST bugs, deliberately.**

- Byte-identity means **bug-identity** on green paths: the migration mirrors pre-existing AST
  miscompiles byte-for-byte (each filed in BUGS.md) rather than fixing them, and gate-rejects
  the ones that would diverge or are toolchain-caught. The `_gen_while` stale-snapshot bug was
  *honored* (gate-rejected), never reproduced -- and has since been fixed on the AST path (the
  head restructures to `while(true){temps; if(!cond)break;}`), so the gate now just awaits a
  mirror of the corrected emission. Fix-in-place is out of scope for a migration cell.

**Resumable frames (async / generators).**

- The resumable state-machine **skeleton** (`resumable_cfg` + `gen_async`: CFG, frame struct,
  case labels, region replay, suspend/resume) stays **shared** machinery for both paths -- like
  signatures and structural emission -- justified by MIR OQ6 and the dual-maintenance cost of
  a 4.1k-line evolving emitter. Only the user-source **leaves** route through THIR, via a seam
  at the skeleton's ~25 delegation sites; per-body all-or-nothing, and a routed body's missing
  leaf is a hard error. `lower_resumable` is the only seam lowering: the single-yield lambda
  peephole that once took the same leaf-seam shape (`lower_simple_generator` ->
  `THIRSimpleGenBody` -> `SimpleGenLeafEmitter`) was deleted on 2026-09-12. Foreach CALLERS
  over generator factories / user-iterator names route via `THIRForIterProto` (the universal
  `::tpy::__iter__` loop), with generator callees admitted by the call classifiers only in iterable position.
- **End state (post-AST-deletion):** the leaf seam is the byte-diff-safe transition, not the
  destination. Once the AST body emitters are deleted, the resumable skeleton + seams fold
  into the IR: the resumable CFG becomes IR proper, async lowering an IR-to-IR transform, and
  the emitter a printer (the Rust-style shape). The region-transparent leaf seam (Design A of
  the region/loop multi-seam) is the step toward that trajectory, not a permanent boundary.

**Ctor frontier.**

- A constructor's member-init list is emitted by the record driver **outside** `gen_body`, so
  it needs its own `THIRConstructor` node + feed -- it cannot reuse the method `gen_body` hook.
- The ctor-arg record-rvalue temp is **mutation-keyed** (mirroring `_gen_record_ctor_args`): a
  const slot binds the inline prvalue expansion (temp-free at any nesting depth); a mutated ref
  slot hoists a named temp at flush positions only.

**Package structure.**

- The `lower/` package split derived module placement from the AST-level call-graph SCCs; the
  gate and lowering arms for one construct **co-evolve**, so they belong in the same file.
  Match/comprehension modules mutually recurse with `statements.py` via module-object imports
  (attribute access defers to call time, robust to import order).

**Wave orchestration (parallel executors + collector).**

- **Per-branch green does NOT compose.** Independent tracks each verified green can still
  diverge when unioned -- interlocked residue means two tracks together flip cases neither
  flips alone (an `asyncio.run` driver arm + the resumable region/loop seam flipped 53 cases,
  0 of them solo). The composition-divergence risk lives at the MERGE, not the cell: run the
  union byte-diff + `--thir-check-flip` on the COLLECTOR after all merges, and verify every
  merge's tree against `^2` (a transient `index.lock` once produced an empty merge with
  poisoned ancestry that only review caught).
- **Cadence:** commit per cell, but a full-corpus from-scratch run per cell is over-verification
  -- gate cells on TARGETED green (`-k` touched cases + `--thir-check-flip`) and reserve the one
  whole-corpus exec for the collector merges, where the real risk sits.
- **A gate-flip must update the gate's prose.** The ratchet and byte-diff verify emitted code,
  NOT comment truthfulness -- a cell that flips a gate (e.g. admitting CFG-based finally) but
  leaves the old "rejected" comments produces a lie only *review* can catch, never the suite.
- **Dedup discipline at the seam:** extract a VERBATIM triplicate immediately (rule-of-three;
  the byte-diff is blind to drift between the copies, so a future edit to 2-of-3 leaves a latent
  bug -- e.g. the `_lower_hoist_predecls` render loop shared by if/try/with). But do NOT collapse
  two functions that are identical *today* yet belong to DIVERGING DOMAINS (call- vs ctor-arity,
  which plausibly split on kwonly/defaults) -- that is over-coupling, not DRY.
- **Wave 4 (dial 1163->1177): no big SELF-CONTAINED ARCH lever survives honest drilling.**
  Three self-contained ARCH picks (resumable template-frame, Optional-ptr slot-hoist,
  representation/Any) each COLLAPSED on `.py`+oracle drilling: resumable template-frame is
  blocked on the generics foundation; the Optional-ptr slot is ~19 residual bodies (mostly
  already built, NOT the stale ~163 the tags implied); representation/Any is a mirage (fstring
  dissolves into `expr.call` call-lowering residue, `container_literal` is element-storage-form
  work, Any is a small mostly-built box). LESSON: at this frontier stage the residue is
  interlocked and whole-case dial movement comes from COMBINING construct-clearing tracks at the
  collector merge (wave 4: 8 solo flips + 3 combined = 11), not a single ARCH item. Tag-based
  leverage estimates are mirages -- always drill the blocking body + `expected/*.cpp` oracle
  before costing (three collapses this wave). The next genuinely-big lever is the generics
  foundation itself (partially built).
- **Generics foundation (2026-07-16, dial 1241->1273, 32 generics/ flips):** the residue was,
  as the brief conjectured, THREADING -- a `TypeParamRef` (bare, bounded, or a composite's
  leaf) became an admissible leaf in the existing decl / call-arg / method / return / coerce /
  subscript / setitem predicates, spelled through the one shared path (`expand_fi_template` /
  `to_cpp_stored` / `lc.render_type`); template headers and concepts stay AST-emitted. The
  genuinely-new cells were small: bounded-T receivers dispatch through the PROTOCOL checker by
  resolving the bound from `_LowerCtx.tparam_bounds` (sema does NOT stamp bounds on
  expression-type TypeParamRefs -- the mirror of the AST's `current_type_param_bounds`);
  generic METHODS ride the plain member path with a `method_targs_cpp` suffix; a temporary
  into a RAW-T method slot hoists the receiver-substituted named temp; INT-kind `[N: int]`
  params seed as INT TypeParamRef bindings so `N` reads render bare; explicit `f[int](x)`
  type-args were blocked only by the parser's leftover `subscript_callee` fallback (sema
  clears it when actually used). Static-protocol SYNC bodies already routed via the protocol
  machinery. Left to the case-driven
  waves (concrete, not generics): value-record copy decls (`q = p`), readonly-Ptr value decl
  slots, None-element containers, chained method receivers (`box.get().append(4)`).
- **Resumable template frames (2026-07-16, +5 flips): a deferred ARCH item that was never an
  ARCH item.** `res.generic` / `res.generic_record` were filed for two waves as "a separate
  emitter tier" -- a template-frame emitter THIR would have to grow. It needed none: the
  frame struct, its template header, and the value-vs-reference CAPTURE choice
  (`param_val_or_ref_t<T>` ctor param -> `val_or_ref_t<T>` field) are all SKELETON
  (AST-emitted); THIR supplies only LEAVES, and a leaf reads a CAPTURED `T` frame field bare
  whether it instantiated to `T` or `T&`. Both gates were deleted outright and the bare `T`
  joined the capture families. LESSON (the mirror of wave 4's "no big ARCH lever survives
  drilling"): a deferred item's COST estimate rots exactly like a tag-based leverage
  estimate. This one was written when the frames really would have needed the generics
  foundation, and nobody re-drilled it after that foundation landed and made the leaves
  ordinary. Before scheduling a long-deferred rung, spend the 10 minutes to re-probe its
  ACTUAL first rejects (`_thir_fallback` per case) -- the filed cost is a claim about a
  codebase that has since changed underneath it. Corollary: the whole-body fallback boundary
  is what made this cheap -- because skeleton/leaf is a clean seam, a construct that only
  touches skeleton costs THIR nothing.
- **...and the SECOND lesson, from the review that caught the first one's overreach: "the
  form is deferred to instantiation" is a CAPTURE fact, not a type fact.** The first cut put
  the bare `T` into `_res_value_ok`, the shared base of the three near-parallel resumable
  predicates -- so it leaked into `_res_local_ok`, and a bare-`T` LOCAL (`y = x`) routed and
  emitted `y = x` / `y` against what the skeleton actually emits as a `T*` pointer ALIAS
  (`y = &(x)` / `(*y)`). A SILENT divergence that the whole-corpus byte-diff missed
  entirely, because no case declares a bare-`T` local -- green byte-diff is a claim about the
  shapes the corpus REACHES, never about a family. The admission now lives in its own
  `_res_capture_ok` (param / return / yield), leaving `_res_local_ok` on `_res_value_ok`, and
  a unit pins the bare-`T` local's fallback. This is the exact drift TODO.md already predicted
  for this predicate trio ("the classic Optional-but-not-Union shape"): a fact was added to the
  SHARED base without auditing the sibling that legitimately diverges. When a new admission is
  justified by a POSITION's calling convention, it belongs in that position's predicate --
  putting it in the base silently re-justifies it for every other position.
- **The cheap-probe kit (use it before scheduling ANY cell; each probe is seconds-to-minutes,
  a wrong schedule is days).** Three escalating probes, all standalone scripts against the
  live compiler, no corpus run: (1) per-case fallback dump -- `Compiler.from_source(src,
  lib_dirs=[case_src, get_lib_dir()/"tpy"])`, `compile()`, `generate_code_to_strings(entry,
  CodeGenOptions(thir_codegen=True))`, read `compiler._thir_fallback` -- answers "what
  ACTUALLY blocks this case today". (2) per-BODY attribution -- wrap
  `thir.lower.resumable.lower_resumable` (module attribute; gen_async imports it
  function-locally, so the patch takes) to log `(func.name, _thir_reject_reason,
  _thir_reject_detail)` on None returns -- answers "WHICH body, WHICH arm" when the
  aggregate tally is too coarse (the resumable component records raw inner reasons, e.g. a
  bare `expr.method_call`, so the detail slot is what names the arm). (3) the rot test --
  monkeypatch the candidate gate wider in-memory, then generate BOTH paths
  (`thir_codegen=False/True`) and byte-compare -- answers "is this filed cost already
  obsolete" BEFORE any design work. The 2026-07-17 resumable session confirmed the rot rate
  these guard against: the filed driver-cell thesis (marker.module.generic gating ~144
  async cases) had silently dissolved, the res.param_type family list was wrong (containers
  dominated at 70 slots), and static-protocol params -- filed as needing a new capture tier
  -- were probe-verified byte-identical with the gate simply removed.

**Binding facts vs type facts (the mirror ledger).** Codegen classifies a local
through per-name `*_locals` SETS (`pointer_locals`, `ptr_variant_locals`,
`optional_locals`, `storage_form_tuple_locals`, `const_indirect_locals`, ... --
`local_cpp_form` is the ordered ladder over them). A type predicate is not a
substitute: the same declared type can arrive through a binding with a different
C++ form (a ptr-variant union as a container element binds the VALUE variant; a
pointer-repr `Optional` as a loop var binds storage). Two divergences of exactly
this shape were found by accident in wave 16 (union isinstance narrowing, then
both union match tiers), which motivated a full sweep of the remaining sets.

- Rule: a THIR render that codegen keys on set membership must key on the mirror
  set (`_narrow_subject_is_ptr` is the pattern), never on the type verdict. Where
  the mirror is not yet known complete, REJECT rather than guess -- but that fence
  is a stopgap, and it over-rejects: the union-narrowing one cost every
  value-variant element binding until the mirror was audited and it came out.
- The sweep (every producer of the pointer / ptr-variant / optional / storage-tuple
  sets, dual-path probed) found no further live divergence. It did find that several
  unmirrored registrations are unreachable only *emergently* -- the fence belongs to
  a gate that exists for another reason (an Optional binding the None-test arm cannot
  classify, an owned-tuple source the call/subscript arms reject, a union element
  the unpack target classifier never admits). Those were pinned by unit tests
  since converted to cases, and each partial mirror names its unmirrored
  producers where it is declared.
- Not swept to the same depth, and the place to look first if this class resurfaces:
  `const_borrow_form_tuple_locals`, whose const verdict comes from a whole-body
  fixpoint pre-pass (`_compute_borrow_tuple_const`) that THIR has no analog for --
  its name-chain rung is fenced only by the storage-tuple alias arm requiring a
  field source. `movable_locals` carries its own partiality caveats in `_LowerCtx`.
- Consequence for future cells: widening one of those gates is not a local change
  -- it un-fences a binding whose mirror does not exist yet. Seed the mirror in
  `_LowerCtx` in the same cell.

---

## MIR Design

### Goal

A **CFG-based IR** with explicit control flow, typed places, and explicit move/borrow
operations. Enables path-sensitive borrow checking, precise liveness, and composable
optimization passes.

### Design Principles

For the active analysis-only rollout, `MIR_ANALYSIS_PLAN.md` defines the smaller
increments. The sketches below include later emission/optimization work; in
particular, last-use hints do not authorize new Move/Copy decisions during the
analysis-only foundation, and C++ RAII does not remove the analysis obligation
to represent cleanup and storage lifetimes before lifetime checking.

- **Not SSA.** Variables are mutable places, like Rust's MIR. SSA would add phi-node
  complexity without proportional benefit given TPy's ownership model.
- **Pre-monomorphization.** Generic functions remain generic in MIR. C++ templates
  handle instantiation. This keeps the generated C++ readable and interoperable.
- **RAII for drops.** No explicit `Drop` instructions for normal scope exits -- C++
  destructors handle cleanup. `del` statements lower to explicit `StorageDead`.
- **Preserves source structure.** MIR is lowered from THIR but retains enough
  information (variable names, source locations, type annotations) for readable
  C++ emission.

### Core Concepts

#### Basic Blocks

```
BasicBlock
  id: BlockId
  statements: list[MIRStmt]
  terminator: Terminator               # goto, branch, return, panic, switch
```

A function is a list of basic blocks. The first block is the entry point. Control
flow is explicit via terminators:

```
Terminator
  = Goto(target: BlockId)
  | Branch(cond: MIROperand, then_: BlockId, else_: BlockId)
  | Return(value: MIROperand | None)
  | Panic(message: str)
  | Switch(operand: MIROperand, arms: list[(Pattern, BlockId)], default: BlockId)
  | Yield(value: MIROperand, resume: BlockId)     # generator yield point
  | Invoke(call: MIRRvalue, ok: BlockId, err: BlockId, err_local: str | None)
                                               # return-tier @error_return handling
  | Unreachable
```

`Yield` suspends the generator, returning a value to the caller. On resume, execution
continues at the `resume` block. This supports the existing state-machine codegen for
generator functions (`gen_generators.py`).

`Invoke` is used for return-tier `@error_return(E)` calls: if the callee returns an
error via `std::expected`, control flows to `err`; otherwise to `ok`. `err_local`
captures the `__err_opt_N`-style temporary when the surrounding `except` block needs
to read the error payload.

Throw-tier `try`/`except` still needs explicit region metadata in MIR. The lowering
must retain enough structured information to represent nested `try` regions and
exception handlers even after CFG flattening. The exact encoding can be block metadata
or explicit handler tables, but MIR-backed codegen cannot assume "return-tier only".

#### Places

A `Place` identifies a logical storage location. This replaces the current string-based
storage keys in `BorrowTracker`.

Important: places model ownership-relevant storage, not literal C++ object layout. For
example, `list[T]` is backed by `std::vector<T>`, so the element storage is not inline in
the vector object itself. MIR should still model:

- the container object
- the container structure (operations like `append`, `insert`, `del` may replace or shift
  the owned backing storage)
- the element storage region borrowed by `items[i]`, `Span[T]`, iterators, etc.

This lets the borrow checker express "element/view borrow of `items`" without caring
whether the runtime representation is inline storage, heap storage, or a view.

The minimal place set should therefore include both direct places and summarized storage
regions:

```
Place
  = Local(name: str)                         # local variable / local owner slot
  | Global(name: str)                        # module/global storage
  | Capture(name: str)                       # captured outer-scope variable
  | Field(base: Place, field: str)           # record field
  | Index(base: Place, index: MIROperand)    # precise container subscript
  | Struct(base: Place)                      # container structural identity
  | Elements(base: Place)                    # container element storage region
  | Deref(base: Place)                       # pointer dereference

# Examples:
# x           -> Local("x")
# G           -> Global("G")
# x from outer -> Capture("x")
# x.items     -> Field(Local("x"), "items")
# x.items[i]  -> Index(Field(Local("x"), "items"), Local("i"))
# items[*]    -> Elements(Local("items"))
# append(items, v) mutates Struct(Local("items"))
# *ptr        -> Deref(Local("ptr"))
```

Places give the borrow checker precise knowledge of what is accessed. `Field(x, "a")`
and `Field(x, "b")` are distinct -- borrowing one does not conflict with mutating
the other.

`Struct(base)` and `Elements(base)` are intentionally coarser than exact indices. They
match the current TPy safety needs well:

- `items[i]` can borrow from `Elements(items)`
- `Span(items)` / `items[a:b]` borrow from `Elements(items)`
- `append`, `insert`, `del`, slice assignment mutate `Struct(items)` and may invalidate
  loans on `Elements(items)`

This is a good first step even if the compiler later grows exact per-element reasoning.

#### Statements

```
MIRStmt
  = Assign(place: Place, rvalue: MIRRvalue)
  | StorageLive(local: str, type: TpyType, kind: LocalKind)
  | StorageDead(local: str)                   # explicit early destruction (del x)
  | Narrow(local: str, narrowed_type: TpyType, source: NarrowSource)
  | Validate(kind: ValidateKind, place: Place)  # borrow check assertion

LocalKind
  = Value                    # T -- value type, stored directly
  | Pointer                  # T* -- pointer-local (non-value type, stack-allocated slot)
  | Ref                      # T& -- reference to another local (alias)

NarrowSource
  = IsInstance
  | Assert
  | MatchArm
  | NonNull
  | PatternGuard

ValidateKind
  = ActiveLoanConflict
  | UseAfterMove
  | StructuralMutationDuringLoan
  | DanglingBorrowReturn
  | InvalidPtrProvenance
```

`LocalKind` reflects TPy's pointer-variable model (see `OWNERSHIP_DESIGN.md`):
non-value-type locals are `T*` pointing to stack-allocated storage, while value-type
locals are plain `T`. This distinction affects codegen (slot allocation) and borrow
checking (pointer-locals create implicit borrows on their backing storage).

`StorageDead` is emitted for explicit `del x` statements (early variable destruction).
Normal scope exits rely on C++ RAII. Note: `del obj[key]` (container deletion) is a
`Call` to `__delitem__`, not `StorageDead`.

#### Rvalues

```
MIRRvalue
  = Use(operand: MIROperand)                        # plain read
  | Move(operand: MIROperand)                        # move (source dead after)
  | Copy(operand: MIROperand)                        # explicit copy
  | Borrow(place: Place,
           mode: BorrowMode,
           provenance: BorrowKind)                   # create reference
  | Call(target: ResolvedFunction,
         args: list[MIROperand],
         type_args: tuple[TpyType, ...])
  | BinOp(left: MIROperand, op: BinOpKind, right: MIROperand)
  | UnaryOp(op: UnaryOpKind, operand: MIROperand)
  | Literal(value: int | float | str | bool | None)
  | Construct(type: TpyType, fields: list[MIROperand])
  | Aggregate(kind: AggregateKind, elements: list[MIROperand])
  | Coerce(operand: MIROperand, from_type: TpyType, to_type: TpyType)
```

The critical distinction is `Move` vs `Copy` vs `Use`:
- `Use` reads without ownership transfer (value types, references)
- `Move` transfers ownership -- the source place is dead after
- `Copy` creates an independent copy of a non-value type

In the current compiler, this decision is made at codegen time via `_maybe_move()`.
In MIR, it is an explicit instruction decided by the move optimization pass.

Conceptually, `Move` is an ownership-transfer request on a place, not "the variable's
type changed to `Own[T]`". A local binding keeps its base type `T`; MIR decides whether
a particular use site becomes `Use(x)`, `Copy(x)`, or `Move(x)` based on liveness,
uniqueness, and active loans on the underlying place.

#### Borrows vs Loans

A useful distinction:

- **borrow**: the source-language semantic relation ("this value refers to someone
  else's storage")
- **loan**: the MIR borrow checker's active tracked record of that borrow over a place

Example: `y = x` for a non-value type creates a borrow of `x`'s place. The checker then
tracks an active loan on that place while `y` is live. A later `Move(x)` conflicts with
that active loan unless analysis proves `y` is dead.

#### Derived Lifetimes and Provenance

TPy should not expose Rust-style explicit lifetime parameters in ordinary source code.
Instead, lifetimes are derived from MIR loan liveness and carried internally as:

- the place being borrowed
- the CFG region where the loan is live
- the provenance of any derived view / pointer / borrowed return

Conceptually:

```text
LoanInfo
  id: LoanId
  place: Place
  mode: BorrowMode
  kind: BorrowKind
  origin: StmtId | ExprId
  holder: LocalName | TempId | ReturnValue | FieldSink
  live_blocks: set[BlockId]
  provenance: Provenance
```

Where provenance captures where a non-owning value came from:

```text
Provenance
  = FromPlace(place: Place)
  | FromParam(index: int)
  | FromGlobal(name: str)
  | FromCapture(name: str)
  | FromUnknown
  | Join(sources: list[Provenance])
```

Examples:

- `span = items[a:b]` -> provenance from `Elements(Local("items"))`
- `p = ptr(x)` -> provenance from `Local("x")`
- `return self.field` -> provenance from `Field(Local("self"), "field")`
- borrowed value returned from a wrapper -> provenance joined from the source params

This is the internal lifetime model for safe-mode checks. A move, mutation, return, or
escape is legal only if no conflicting live loan reaches that program point and the
provenance proves the source outlives the use.

#### Borrow Kinds and Modes

```
BorrowMode
  = Shared
  | Mutable

BorrowKind
  = Alias                   # whole-container alias (safe through mutations)
  | Field                   # field-level reference
  | Element                 # reference to container element
  | Iterator                # for-loop iterator over container
  | Pointer                 # Ptr[T]
  | View                    # StrView / BytesView / Span-like view
```

These correspond to the existing `BorrowKind` enum in `sema/context.py` (`ALIAS`,
`FIELD`, `ITER`, `ELEMENT`, `PTR`). The key semantic distinction: `Alias` borrows
are safe through container mutations (whole-object reference, not invalidated by
reallocation), while `Element` and `Iterator` borrows are invalidated by structural
mutations (append, insert, del). `Field` borrows are invalidated when the parent
object is reassigned but not by sibling field mutations.

In the current compiler, borrows are side-state in `BorrowTracker`. In MIR they
become explicit `Borrow` instructions, making conflicts visible in the IR. `BorrowMode`
captures whether the use requires shared or mutable access; `BorrowKind` captures where
the borrow came from and what invalidates it.

`Pointer` deserves special treatment: `Ptr[T]` is not "arbitrary raw pointer" in the
language design. It is primarily an explicit nullable reference form. Pointer arithmetic
and unchecked pointer manipulation remain in `tpy.unsafe`; plain `Ptr[T]` operations can
still participate in normal provenance / lifetime analysis.

#### Function Lifetime / Effect Contracts

For ordinary TPy functions, many facts can be inferred and materialized into THIR / MIR:

- `return_borrows_from = {0, ...}`
- `mutated_params = {...}`
- structural invalidation facts for container-like methods
- whether a returned `Ptr[T]` / `Span[T]` / `StrView` is derived from an input place

For native functions implemented in C++, these contracts should usually be explicit,
because the compiler cannot reliably infer them from the definition body. The IR design
therefore needs room for native summaries such as:

- `return_borrows_from`
- `returns_ptr_to`
- `mutates`
- `may_invalidate`
- `readonly`
- `opaque_effects`

These contracts are especially important for core-library functions that construct or
return views (`Span`, `StrView`, `BytesView`), explicit nullable references (`Ptr[T]`),
or iterator/pointer-like adapters.

Absent an explicit contract, native code should be treated conservatively:

- returned provenance may be `FromUnknown`
- mutation / invalidation may be assumed
- advisory mode may warn and reduce optimization
- safe mode may reject lifetime-sensitive uses unless the call is behind an explicit
  escape hatch

### THIR -> MIR Lowering

The lowering pass (`tpyc/mir/lower.py`) converts THIR to MIR:

1. **Control flow desugaring.** `if`/`else` -> `Branch` terminators, `for` -> loop
   blocks with `Goto`/`Branch`, `match` -> `Switch`, `while` -> loop with `Branch`.
   `for/else` and `while/else` desugar to a boolean flag + `Branch` after the loop
   (flag is set on `break`, checked after loop exit). `try`/`except` for
   `@error_return` desugars to `Invoke` terminators for return-tier handling, while
   throw-tier `try` / `except` must preserve enclosing region / handler metadata.
   Chained comparisons (`a < b < c`) desugar to short-circuit `Branch` chains during
   lowering.

2. **Place construction.** Each lvalue expression becomes a `Place`. Field accesses,
   subscripts, and derefs nest naturally.

   Type narrowing must also survive lowering. Branches and match arms that narrow a
   name's type insert explicit `Narrow(local, narrowed_type, source)` statements on the
   dominated path. Later MIR passes and MIR-backed codegen consult these statements to
   build block-local type environments. This avoids losing facts like "in this block,
   `x` is known to be `Foo`" after flattening THIR control flow into basic blocks.

3. **Initial Move/Copy assignment.** The lowering pass inserts `Move` for last-use
   sites (from THIR's `is_last_use` flags) and `Copy` elsewhere. The optimization
   pass may upgrade `Copy` -> `Move` later.

4. **Borrow creation.** Alias assignments (`y = x` for non-value types) become
   borrows of the underlying owner place. Element access and view creation should lower
   to summarized element-storage borrows:

   - `y = x` -> borrow of `Local("x")` (or the owner place behind it)
   - `v = items[i]` -> borrow of `Elements(Local("items"))`
   - `span = items[a:b]` -> view borrow of `Elements(Local("items"))`
   - `p = take_ptr(x)` -> pointer borrow of `Local("x")`

   Exact `Index(base, i)` borrows can be added later for more precision, but the initial
   MIR should support the summarized `Elements(base)` form because it matches the current
   TPy invalidation rules.

5. **StorageLive/StorageDead.** `StorageLive` at variable declaration, `StorageDead`
   at explicit `del` statements.

### MIR Passes

Each pass is an independent function `pass(mir: MIRFunction) -> MIRFunction` or
`pass(mir: MIRFunction) -> list[Diagnostic]`:

#### Pass 1: Liveness Analysis

Standard backward dataflow on the CFG. For each basic block, compute which variables
are live at entry and exit. This replaces `tpyc/liveness.py` with a principled
algorithm that handles branches, loops, and join points correctly.

Result: `LivenessInfo` mapping each statement to the set of live variables after it.

#### Pass 2: Move Optimization

Using liveness info, upgrade `Copy` -> `Move` where the source is dead after:

```
Before:  _tmp = Copy(x)       # x is dead after this point
After:   _tmp = Move(x)       # ownership transferred
```

This replaces the current `_maybe_move()` / `movable_locals` / `all_last_uses`
machinery with a single, clean pass.

#### Pass 3: Borrow Checking

Walk the CFG forward, maintaining per-block borrow state:

```python
BorrowState:
  active_loans: dict[Place, set[LoanInfo]]
  moved_places: set[Place]
```

At each statement:
- `Borrow(place, mode=Mutable, ...)` -- check no conflicting live loans on `place` or overlapping
  parent/child places
- `Borrow(place, mode=Shared, ...)` -- check no live mutable / move-conflicting loans on `place`
- `Move(place)` -- check no active loans that still reach `place`, mark as moved
- `Assign(place, ...)` -- invalidate or conflict with child-place loans as appropriate
- `Assign(Struct(base), ...)` / structural mutation calls -- conflict with loans on
  `Elements(base)` and views derived from them
- Calls with mutated params -- check no conflicting loans on argument places
- `Ptr[T]` creation / use -- treat as explicit nullable-reference loans, not as a fully
  unchecked bypass; pointer arithmetic remains outside this pass in `tpy.unsafe`

For summarized container places, the critical rules are:

- loans on `Elements(base)` represent element refs, spans, iterators, and other views
- mutating `Struct(base)` may invalidate `Elements(base)` loans
- sibling field loans (`Field(x, "a")` vs `Field(x, "b")`) do not conflict unless a
  parent-place operation invalidates both

At branch join points, merge loan states conservatively across reachable predecessors.
The key win over the current AST-based checker is that the analysis is attached to CFG
edges and explicit places rather than string roots and ad hoc freeze/restore snapshots.

Loop headers are merge nodes with pre-loop and back-edge predecessors. Monotone
kill-facts (pointer non-null, parameter provenance, trusted-call-return, type
narrowing) must be meet-merged at the header rather than restored from the pre-loop
snapshot: a fact that the body clears must not re-appear after loop exit. The
current AST-based checker applies a single-pass intersection for all four sets at
loop exit (`tpyc/sema/init_tracker.py::apply_loop_exit_facts`), which is sound for
post-loop uses but remains optimistic for mid-body uses (body analysis starts from
the pre-loop snapshot). In MIR this falls out of standard forward dataflow at the
header and should become a hard correctness requirement for Pass 3, with no
mid-body approximation.

This replaces the current `BorrowTracker` in `sema/context.py` with path-sensitive
analysis. The key improvement: an `if` branch that moves a variable does not conflict
with an `else` branch that borrows it, because they are on different paths.

The same pass can produce different severities depending on enforcement mode:

- **advisory/default**: emit warnings, keep lowering
- **safe opt-in**: elevate selected violations (dangling borrowed return, structural
  mutation while `Elements(base)` is loaned, move with live aliases, invalid `Ptr`
  provenance) to hard errors

The `Validate` statement family exists so MIR lowering and early analysis passes can
materialize the checks that later become diagnostics or hard errors:

- `Validate(ActiveLoanConflict, place)` -- use/mutation conflicts with a live loan
- `Validate(UseAfterMove, place)` -- moved place used again
- `Validate(StructuralMutationDuringLoan, place)` -- structural mutation invalidates
  element/view loans
- `Validate(DanglingBorrowReturn, place)` -- borrowed return escapes owner lifetime
- `Validate(InvalidPtrProvenance, place)` -- `Ptr[T]` escapes or aliases invalidly

#### Pass 4: Value Range Propagation

Forward dataflow tracking integer ranges `[lo, hi]` through the CFG. This replaces
`tpyc/sema/value_range.py` with a CFG-based version that naturally handles loop
induction variables and branch conditions.

Result: at each subscript/deref, whether bounds check / null check can be elided.

#### Pass 5: Dead Code Elimination

Remove statements whose results are never used (no live variables depend on them).
Standard backward pass on the CFG.

### Debugging: `--dump-mir`

`uv run tpyc --dump-mir program.py` collects the THIR bodies used by emission
and prints supported MIR CFGs for user modules. It also reports why a body is
outside MIR coverage, has no body, rejected THIR lowering, or was never
attempted after an earlier rejection. It produces no C++ files or binary and
does not change normal compilation. `tpy --dump-mir program.py`, `-c` and stdin
input are also supported. The option cannot be combined with build/exec or
other dump modes. The following sketch includes future operations outside
the current bounded subset:

```
fn main() -> Void:
  bb0:
    StorageLive(items, list[int32])
    items = Aggregate(List, [Literal(1), Literal(2), Literal(3)])
    StorageLive(total, int32)
    total = Use(Literal(0))
    goto -> bb1

  bb1:                                  // loop header
    _iter_has_next = Call(iter.__next__, [_iter])
    branch(_iter_has_next) -> bb2, bb3

  bb2:                                  // loop body
    x = Use(_iter_current)
    total = Call(int32.__add__, [total, x])
    goto -> bb1

  bb3:                                  // after loop
    Call(print, [Move(total)])
    return
```

### Interaction with Ownership Model

TPy's ownership model is advisory by default. Existing codebases must continue to
compile, so the MIR needs to support two enforcement levels over the same core place /
loan analysis:

- **Default mode (advisory)**: emit warnings, drive move/copy optimization, preserve
  current migration-friendly behavior
- **Safe opt-in mode**: treat a selected subset of ownership / lifetime violations as
  hard errors, with explicit escape hatches still available

TPy is therefore not a globally affine type system (see
`docs/CONSUMING_ITERATION_DESIGN.md`). The MIR should model ownership strongly enough to
support an enforcing mode later, but its default interpretation remains advisory.

**Move/Copy is a place-level decision.** `Move` means "transfer ownership of the
underlying place". In advisory mode, a failed move check may become a warning or may be
lowered back to `Copy` / `Use` depending on the operation. In safe mode, the same check
can be a hard error.

**`Own[T]` requests transfer, it does not make names affine.** When a function parameter
is `Own[T]`, the caller's argument is lowered as a request to `Move(arg_place)`. This is
legal only when the owner place is unique enough at that program point. The local binding
itself does not permanently change type from `Ref[T]` to `Own[T]`; the access mode is
chosen per use site.

**`Ptr[T]` remains available even in safe mode.** The intended meaning of `Ptr[T]` is
"explicit nullable reference", not unrestricted raw pointer. In safe mode:

- plain creation / passing / returning / dereferencing of `Ptr[T]` can remain allowed
- provenance and lifetime of the pointee place are checked
- `Ptr[readonly[T]]` participates as an explicit readonly borrow
- pointer arithmetic, unchecked casts, and arbitrary address fabrication stay in
  `tpy.unsafe` as escape hatches outside the safety guarantee

This preserves migration viability for existing low-level code while still allowing a
stronger safety story for ordinary non-pointer borrows.

**Consuming iteration lowers naturally.** A consuming `for` loop:

```python
for x in items:    # items is last use, consuming __iter__ selected
    process(x)     # x is Own[T], movable
```

Lowers to:

```
_iter = Call(__iter__, [Move(items)])     // consuming overload, items moved
bb_loop_body:
  x = Move(_iter_current)                // element moved out of iterator
  Call(process, [Move(x)])               // x moved into process
```

The THIR's `is_consuming` flag drives the selection of `Move` vs `Use` for the
iterable, and the element variable is naturally movable.

**`del` has two forms.** `del x` (variable destruction) lowers to `StorageDead(x)` in
MIR, enabling early resource release. `del obj[key]` (container element deletion)
lowers to `Call(__delitem__, [obj, key])`. Normal scope-exit destruction is handled by
C++ RAII -- the MIR does not insert drops at scope boundaries.

**`@nocopy` types.** For `@nocopy` types, the move optimization pass can verify that
no `Copy` instructions exist for that type -- any remaining `Copy` is a compile error.
This is cleaner than the current approach of checking during type coercion in sema.

**Borrow checking respects TPy's permissive aliasing.** Unlike Rust, TPy allows
multiple mutable references to the same object (matching Python semantics). The borrow
checker focuses on:
- Iterator invalidation (mutation during iteration)
- Element reference invalidation (structural mutation while element is borrowed)
- Pointer invalidation (reallocation while `Ptr[T]` is outstanding)
- Use-after-move for `@nocopy` types

It does **not** enforce exclusive mutable access (no "aliasing XOR mutability" rule).

### MIR -> C++ Codegen

The codegen backend reads MIR instead of THIR:

| MIR construct | C++ emission |
|---------------|-------------|
| `Move(x)` | `std::move(x)` |
| `Copy(x)` | `x` (C++ copy constructor) |
| `Use(x)` | `x` |
| `Borrow(x, Shared, Alias)` | (variable is `T*` or `T&` -- whole-object reference) |
| `Borrow(x, Shared, Field)` | (variable points to `parent.field`) |
| `Borrow(x, Shared, Element)` | (variable points to `container[i]`) |
| `StorageLive(x, T, Value)` | `T x;` or `T x = ...;` |
| `StorageLive(x, T, Pointer)` | `T __slot_x; auto* x = &__slot_x;` |
| `StorageDead(x)` | `{ /* end scope for x */ }` or explicit destruction |
| `Goto(bb)` | fall-through or `goto` (structured emission avoids goto where possible) |
| `Branch(c, t, f)` | `if (c) { ... } else { ... }` |
| `Switch(...)` | `switch` or `if`/`else if` chain |
| `Invoke(call, ok, err, err_local)` | `auto __res = call; if (!__res) { err_local = __res.error(); goto err; }` |
| `Yield(val, resume)` | state-machine `switch` dispatch |

The codegen reconstructs structured control flow from the CFG where possible (if/else,
while, for) to keep the C++ readable. This is a well-studied problem (structural
analysis / region detection), but it is also one of the biggest migration risks. MIR-
backed codegen should therefore require explicit structured-region tags from lowering:
loop headers/latches/exits, `for/else` and `while/else` regions, `with` guards,
return-tier and throw-tier `try` regions, generator dispatch roots, and short-circuit
comparison regions. "Recover structure from raw CFG alone" is not a realistic
implementation requirement for the first MIR-backed codegen pass.

---

## What Does NOT Change

- **Type checking stays tree-based.** Overload resolution, generic instantiation,
  protocol conformance, type inference -- all remain in sema, operating on the AST.
  These are naturally tree-shaped operations.

- **C++ templates for generics (C++ backend).** No monomorphization in the TPy
  compiler for the C++ backend. Generic functions in MIR carry type parameters, and
  codegen emits `template<typename T>`. An LLVM backend would add a monomorphization
  pass (see Future: LLVM Backend).

- **Parser unchanged.** The parser produces the same AST. THIR lowering is a new
  pass after sema, not a parser change.

- **Test structure unchanged.** Snapshot tests compare generated C++ output. Since
  codegen still produces C++, the test infrastructure works as-is. New snapshot tests
  can be added for THIR and MIR dumps.

- **Mutation propagation stays in sema.** The Phase 2 call-graph fixpoint (transitive
  mutation inference) runs after sema and before THIR lowering. Its results are
  materialized into THIR nodes (`mutated_params`, `is_readonly`). MIR borrow checking
  consumes these facts but does not recompute them.

---

## Phasing and Dependencies

```
Phase 1 (THIR):
  1.1  Define THIR node types                             tpyc/thir/nodes.py
  1.2  Implement THIR lowering pass                       tpyc/thir/lower.py
  1.3  Add --dump-thir CLI flag                           tpyc/cli.py
  1.4  Create THIRCodeGenContext                           tpyc/codegen_cpp/context.py
  1.5  Migrate codegen to read from THIR                  tpyc/codegen_cpp/*.py
  1.6  Remove analyzer reference from codegen             tpyc/codegen_cpp/context.py
  1.7  Add THIR snapshot tests                            tests/

Phase 2 (MIR):
  2.1  Define MIR types (Block, Place, Stmt, Rvalue)      tpyc/mir/nodes.py
  2.2  Implement THIR -> MIR lowering                     tpyc/mir/lower.py
  2.3  Implement liveness pass                            tpyc/mir/liveness.py
  2.4  Implement move optimization pass                   tpyc/mir/move_opt.py
  2.5  Implement borrow checking pass                     tpyc/mir/borrow_check.py
  2.6  Implement value range pass                         tpyc/mir/value_range.py
  2.7  Add --dump-mir CLI flag                            tpyc/cli.py
  2.8  Migrate codegen to read from MIR                   tpyc/codegen_cpp/*.py
  2.9  Remove old liveness.py, BorrowTracker,             tpyc/liveness.py,
       value_range.py                                     tpyc/sema/context.py,
                                                          tpyc/sema/value_range.py
  2.10 Add MIR snapshot tests                             tests/
```

Phase 1 is a prerequisite for Phase 2. Within each phase, steps are sequential except
that snapshot tests (1.7, 2.10) can be added incrementally alongside each step.

---

## Future: LLVM Backend

The MIR design intentionally keeps the door open for an LLVM backend. This section
documents what that would require and how C++ interop is preserved.

### Pipeline

The MIR stays backend-agnostic. The backend choice determines which lowering runs
after the shared analysis passes:

```
                        ┌─> C++ codegen (structured C++ emission)
THIR -> MIR -> passes ──┤
                        └─> LLVM lowering (future)
                              ├─ monomorphization pass
                              ├─ drop insertion pass
                              └─ LLVM IR emission
```

Passes 1-5 (liveness, move optimization, borrow checking, value range, dead code)
are shared. The backends diverge only at the final emission stage.

### What LLVM Requires Beyond C++

| Concern | C++ backend | LLVM backend |
|---------|-------------|-------------|
| **Generics** | C++ templates | Monomorphization pass: stamp out concrete versions of each generic function for every used type combination |
| **Drops** | C++ RAII (implicit) | Explicit drop insertion pass: compute drop points at scope exits, `Move` sites, and early `StorageDead` |
| **STL types** | Direct use (`std::vector`, `std::string`, etc.) | Link against libstdc++/libc++ and call through C-ABI wrappers, or provide a TPy runtime library |
| **Name mangling** | C++ compiler handles it | Emit mangled names following the platform ABI (Itanium/MSVC) |
| **Exceptions** | C++ exceptions / `std::expected` | LLVM `invoke`/`landingpad` for unwinding, or keep `std::expected` via C-ABI calls |

The **monomorphization pass** is the largest addition. It runs on MIR before LLVM
lowering, replacing generic type parameters with concrete types and duplicating
function bodies. This is the same approach Rust takes (monomorphize on MIR, then
lower to LLVM IR). The C++ backend skips this pass entirely.

The **drop insertion pass** walks the CFG and inserts destructor calls at every point
where a variable goes out of scope or is moved. The C++ backend skips this because
C++ RAII handles it implicitly. For LLVM, drops are explicit `Call` instructions to
destructor functions.

The same pass removes **placeholders**. C++ must declare a local, frame field or global
before Python first assigns it, and a C++ declaration constructs; that is why the C++
backend builds such a slot with the type's default constructor, value-initialized
(`P p{};`), which runs a `ValueType`'s zero-argument `__init__` once more than CPython
does (the documented contract asks it to be side-effect free). With explicit lifetimes the
slot is plain storage: definite-initialization analysis proves every read follows a write,
construction happens at the first assignment, and the drop runs unconditionally where
every path initializes the slot and under a one-bit drop flag where only some do (Rust's
drop elaboration; unwinding reads the same flags). The placeholder's default construction
goes away with it: a variable without a value runs no constructor, so the extra
`__init__` run and the side-effect-free contract are dropped, as is the static-initialization
hazard of a module global's placeholder (its constructor runs before the module's other
globals are bound, BUGS.md#valuetype-global-default-ctor-static-init), and a `@native` `ValueType` no longer needs a C++
default constructor.

### C++ Interop Without Generating C++

Interop is an **ABI contract**, not a source-level dependency. LLVM-generated machine
code can interoperate with C++ code because both follow the same platform ABI.

| Direction | Mechanism |
|-----------|-----------|
| **TPy calls C++** | `@native` declarations provide the C++ function signature. The LLVM backend emits a call using the platform's C++ ABI (same calling convention, name mangling). The C++ library is linked at link time. |
| **C++ calls TPy** | The TPy compiler generates a C++ header (`.hpp`) declaring the TPy-compiled functions with proper mangling. C++ code `#include`s the header and links against the TPy-compiled object files. |
| **Shared types** | Types like `std::vector<int32_t>` have a fixed ABI layout. LLVM-generated code can construct/read/write them if it knows the layout. Alternatively, C-ABI wrapper functions handle type construction/access. |

This is proven by prior art:
- **Rust** interops with C++ via `cxx`/`bindgen` without generating C++ source
- **Swift** interops with ObjC/C++ through ABI compatibility
- **Clang itself** compiles C++ to LLVM IR -- so LLVM IR is inherently ABI-compatible
  with C++ compiled by Clang

### Runtime Library Strategy

The current C++ backend relies on the C++ standard library (`std::vector`,
`std::string`, `std::optional`, `tpy::ordered_map`, etc.) plus TPy's runtime headers
in `runtime/cpp/include/tpy/`. For an LLVM backend, two viable strategies:

1. **Link against the C++ runtime.** Compile `runtime/cpp/` with a C++ compiler into
   a static/shared library. LLVM-generated code calls into it via C-ABI wrapper
   functions. This reuses all existing runtime code. The wrappers are thin: `vec_push`
   calls `std::vector::push_back`, `str_len` calls `std::string::size()`, etc.

2. **Native TPy runtime (long-term).** Rewrite performance-critical runtime components
   (vector, string, hash map) in TPy itself or in C with LLVM-friendly layouts. This
   eliminates the C++ stdlib dependency but is a large effort. Practical only if/when
   TPy is self-hosting.

Strategy 1 is the pragmatic starting point. The C-ABI wrappers can be auto-generated
from the existing runtime headers.

### MIR Design Implications

The MIR as currently designed requires **no structural changes** for LLVM support.
The key decisions that keep it backend-agnostic:

- **Not SSA**: LLVM IR is SSA, but LLVM's `mem2reg` pass converts alloca-based code
  to SSA automatically. MIR places lower to allocas, and LLVM optimizes from there.
- **Explicit Move/Copy/Borrow**: these map to LLVM operations regardless of backend.
  `Move` -> load + store + drop source. `Copy` -> load + store (or memcpy).
- **Typed places with `LocalKind`**: `Value` locals -> alloca. `Pointer` locals ->
  alloca holding a pointer. Natural LLVM lowering.
- **Backend-specific passes**: monomorphization and drop insertion are additional
  passes in the LLVM pipeline, not changes to the shared MIR.

### Not a Near-Term Goal

The LLVM backend is a future possibility, not a current priority. The C++ backend
remains the primary target because:
- Readable C++ output is valuable for debugging, auditing, and interop
- C++ templates avoid the complexity of compiler-side monomorphization
- The C++ ecosystem (build systems, sanitizers, profilers) is directly usable
- The runtime library is already written in C++ headers

The IR design simply ensures that this path is not closed off. If/when the LLVM
backend becomes desirable (e.g., for faster compilation, LTO across TPy modules,
or eliminating the C++ compiler dependency), the MIR is ready.

---

## Open Questions

### Implementation Notes

- **Dynamic / opaque values.** Define how `Any`, dynamic `__getattr__` / `__setattr__`,
  namespace-style objects, and opaque native objects lower to coarse summarized places
  and how they degrade analysis precision.
- **Native contract surface.** Decide the exact user-facing annotation/decorator syntax
  for native lifetime/effect summaries such as `return_borrows_from`, `returns_ptr_to`,
  `mutates`, and `may_invalidate`.
- **Safe-mode boundary.** Spell out which operations are inside the safety guarantee and
  which remain explicit escape hatches (`tpy.unsafe`, pointer arithmetic, unchecked
  casts, opaque native code without contracts).
- **Rebind vs mutate.** Make the rule explicit during implementation that rebinding a
  name creates a new owner/place binding, while mutation changes an existing place.
- **Worked examples.** Add a few focused examples once implementation starts,
  especially for borrowed returns, `Ptr[T]`, views, captures, and structural
  invalidation.
- **Structured MIR metadata.** Decide the exact representation of the region tags needed
  for readable MIR-backed C++ emission.
- **Native default conservatism.** Define the default behavior when a native function
  lacks an explicit lifetime/effect contract in advisory mode vs safe mode.

1. **THIR granularity for match/case.** Match arms have complex pattern-matching
   logic. Should THIR preserve the high-level `THIRMatch` with structured arms, or
   desugar patterns into explicit comparisons? Recommendation: keep structured -- the
   `match` codegen already handles this well, and desugaring loses readability.

2. **MIR for top-level statements.** Module-level code (globals, top-level expressions)
   uses a different variable model (pointer slots). Should this go through MIR, or
   should MIR only cover function bodies? Recommendation: MIR for function bodies
   only, at least initially. Top-level code has simpler control flow and less need
   for path-sensitive analysis.

3. **CFG reconstruction for codegen.** Emitting readable C++ from a CFG requires
   reconstructing structured control flow. This is non-trivial: `break`/`continue`
   with labels, `for/else`/`while/else`, `with` statement guards, `try`/`except`
   error-return patterns, and generator state machines all have structured C++
   emission patterns that must be recovered from the CFG. Since all MIR is lowered
   from structured Python, the CFG is always reducible -- but the reconstruction
   still needs careful handling of each pattern. Recommendation: tag MIR blocks with
   their source-level structure during lowering (loop headers, if-then/else, with
   guards) to simplify reconstruction, rather than recovering structure purely from
   the CFG topology.

4. **Incremental adoption. RESOLVED, and the answer held.** Should codegen support
   both THIR and AST input during migration, or is a big-bang switch acceptable?
   Recommendation was dual-mode during migration. That is what was built, but the
   granularity landed finer than this question imagined: the unit is the BODY, not
   the codegen module, and the AST path is the ORACLE rather than merely the old
   path -- every routed body is byte-diffed against it. The cost that was not
   foreseen here is the exit: three cross-path detectors exist only because two
   authors do, and all three die on the cutover commit.

5. **Separate THIR and MIR codegen backends.** During Phase 2, codegen switches from
   THIR to MIR. Should both backends coexist permanently (e.g., THIR backend for fast
   debug builds, MIR backend for optimized builds)? Recommendation: single MIR backend
   once Phase 2 is complete. The MIR pass pipeline can be shortened for debug builds
   (skip optimization passes).

6. **Generator functions in MIR.** Generator functions are currently lowered to state-
   machine structs in codegen (`gen_generators.py`). Should this transformation happen
   during THIR -> MIR lowering (generators become explicit state machines in MIR), or
   should MIR represent generators with `Yield` terminators and defer the state-machine
   transform to MIR -> C++ codegen? Recommendation: `Yield` terminators in MIR --
   this keeps MIR closer to the source semantics and lets the state-machine transform
   remain a codegen concern. But this means MIR passes (liveness, borrow checking) must
   understand that `Yield` suspends and resumes, which complicates dataflow analysis.

7. **String/bytes view borrow tracking.** Sema currently tracks `StrView`/`BytesView`
   borrows separately from `BorrowTracker` (via `str_source_borrows`,
   `bytes_source_borrows`). Should MIR unify these with the general `Borrow`
   instruction, or keep them separate? Recommendation: unify -- a `StrView` borrowing
   from a `str` variable is conceptually the same as any other borrow. The `BorrowKind`
   may need a `View` variant to capture the "invalidated by any mutation of source"
   semantics.

8. **Generic param ABI for TPy types with storage/param split.** Several TPy types
   have a storage/param C++ split: `str` (storage `std::string`, param
   `std::string_view`), `String` (storage `std::string`, param `const std::string&`),
   `bytes` (storage `std::vector<uint8_t>`, param `std::span<const uint8_t>`),
   `bytearray` (storage `std::vector<uint8_t>`, param mutable ref). Non-generic
   codegen handles the split position-aware (param positions emit the param
   formatter, storage positions emit the storage formatter). Generic codegen uses
   the runtime trait `param_val_or_ref_t<T>` keyed on the C++ storage type, which
   cannot distinguish `str` from `String` (both `std::string`) or `bytes` from
   `bytearray` (both `std::vector<uint8_t>`). Net effect today: generic-T-over-str
   pays a `std::string` materialization at every call site (SSO covers short
   literals; long literals heap-allocate once per call). C++ template instantiation
   erases TPy-type identity by the time it sees `T`; no runtime trait keyed on the
   C++ type can recover it.

   No fix is unambiguously best. The honest design landscape:

   | Approach | Idiomatic C++ | `vector<str>` interop | Perf gap closed | Cost |
   |----------|---------------|-----------------------|-----------------|------|
   | **Current state (accept asymmetry)** | yes | preserved | no (small gap) | none |
   | **Distinct C++ types** (`auto_string : public std::string`) | yes | **broken** -- `vector<auto_string>` is not `vector<std::string>` | yes | medium runtime + audit churn |
   | **Codegen monomorphization** (per-call function emission, no template) | yes (output-wise) | preserved | yes | heavy compiler internals (instantiation registry, cross-module emission) |
   | **Descriptor template parameter** (`template<class TDesc>` with `TDesc::storage`, `TDesc::param`) | **no** -- compromises readable C++ output goal | preserved | yes | medium codegen churn but readers must learn descriptor pattern |
   | **Drop the split entirely** | yes | preserved | no (bigger gap, applies to non-generic too) | none |
   | **Trait specialization on shared C++ types** | yes | preserved | yes | unsound -- conflates `str`/`String` and `bytes`/`bytearray`; rejected |
   | **Auto-downgrade `T=str` to `T=StrView` for literals** | no | preserved | yes | unsound -- signature-level safety check can't cover body-side dangling cases; rejected |

   The three idiomatic options each pay a distinct cost. There is no row that wins
   all three of `idiomatic / vector interop / perf gap closed` without paying a
   real cost somewhere.

   **Codegen monomorphization** is the cleanest long-term path *if* the perf gap
   ever becomes worth solving. Compiler emits one C++ function per `(generic, TPy
   type args)` instantiation instead of a single template. Each emitted function
   uses normal C++ types (no descriptors, no traits, no `auto_string` wrapper) --
   `inline std::string echo_str(std::string_view x) { return std::string(x); }`
   reads the way C++ developers expect. Vector interop preserved because storage
   types stay unchanged (`list[str]` still `std::vector<std::string>`). Cost is
   compiler-internal: instantiation registry, cross-module emission rules, header
   placement for inline functions, generic methods/classes/`Fn[..., T]`/protocol
   integration. Estimate: 2-4 weeks of focused work.

   **Distinct C++ types** (auto_string approach) is the lighter-touch idiomatic
   option but pays its cost user-visible: existing user code that does `@native`
   interop with `std::vector<std::string>` against TPy `list[str]` would have to
   migrate to `list[String]` (which stays `std::vector<std::string>`). Mechanical
   migration but real surface change. Estimate: 1-2 weeks.

   **Current state** is the pragmatic answer. The perf gap is small in practice
   (SSO covers the common case of short literals; longer literals through pure
   pass-through generics is a rare pattern); users who hit a real hot path can
   write `def f(s: StrView)` explicitly. Aligns with TPy's "opt-in constraints
   for hot paths" philosophy: the perf gap is the cost of *not* opting in to
   compiler complexity. Recommendation: stay here unless measured workloads
   justify the upgrade.

   **Descriptor template parameter** -- documented for completeness, but the
   non-idiomatic generated C++ output (`f<tpy::str_desc>(...)` instead of
   `f<std::string>(...)`) compromises a stated TPy goal: readable C++ output for
   debugging, auditing, and interop. Demoted to "considered but compromises
   primary goal." Could still serve as a fallback for future TPy types whose
   semantics genuinely cannot be expressed via distinct C++ types, but for the
   str/bytes case the output cost outweighs the benefits.

   **Drop the split** is the simplification answer. Single representation per
   TPy type (`str` always `std::string`, `bytes` always `std::vector<uint8_t>`).
   `StrView`/`BytesView` remain as explicit opt-in for view semantics. Predictable,
   uniform, no special machinery. Pays the materialization cost at every str
   param boundary (SSO covers it for short literals). Worth considering if/when
   the architectural simplification becomes more valuable than the optimization.

   **Scheduling**: no work planned. The current state is the safety floor. If the
   perf gap becomes worth fixing (driven by measured workloads, not preemptive
   optimization), the recommended target is codegen monomorphization. That work
   does *not* require THIR/MIR to land first but probably benefits from being
   done concurrently with the THIR codegen migration to avoid double-churn.

9. **Tuple form as a first-class type fact.** RESOLVED 2026-06 -- see "Form as a
   First-Class THIR Fact" (THIR Design). The exhibit inventory below stands as the
   ground-truth surface the design must subsume; the resolution is: a `form` tag on
   the THIR EXPRESSION (not the type) + an explicit `THIRFormConvert` node, with
   the recommendation in this item's last line adopted (form tag + explicit
   conversion nodes) but PLACED ON THE EXPR for the positional reasons given there.
   (A full ground-truth map of the
   form-dispatch surface this item -- plus items 11/12 -- must subsume is in
   `docs/THIR_FORM_INVENTORY.md`, the bootstrap artifact for the form design.)
   Today `TupleType` is a single sema type
   whose C++ representation depends on context -- borrow form (`tuple<T*,...>`,
   one pointer-element shape for every non-value element since the tuple
   borrow-pointer unification; generic elements via `val_or_ptr_t<T>`) at
   param/return/local/frame boundaries, storage form (`tuple<optional<T>,...>`,
   `tuple<T,...>`) at field/container/`Own[]` boundaries. (See
   `LANGUAGE_FEATURES.md` "Borrow Form vs Storage Form" for the canonical definition
   of these forms; this item is specifically about elevating the distinction to a
   first-class IR fact.) Codegen reconstructs which form is needed at each site and
   inserts conversions (`tuple_to_storage[_move]` with per-element dest-shape
   dispatch, `tuple_to_pointer`, `tuple_value_to_borrow`, `to_storage_elem`,
   `to_pointer_form`, `to_val_or_ptr`). The unification fixed the silent-copy
   bug class (a param-/local-/call-rooted reference-member tuple now ALIASES at
   yield/return, matching CPython; the borrow lives in a pointer-holding frame
   field, which the resumable-frame model CAN express -- the prior assessment
   that the share fix was THIR-gated proved wrong), but it did so by adding
   more consumer-side dispatch: every site that READS a tuple element as a
   value re-derives the form. The consumer-site inventory THIR must subsume
   with structural conversion/access nodes:
   - subscript read (`_gen_subscript` tuple branch: raw pointer vs
     `tuple_elem_ref` for generic slots vs `optional_to_ptr` lift),
   - field access / method call on a subscript (`->` vs `.` via
     `_tuple_subscript_yields_borrow_ptr`),
   - value contexts (`gen_expr_deref` deref of borrow subscripts),
   - unpack binding (`unwrap_ref(tuple_elem_ref(std::get<i>(...)))`),
   - print/repr and hash (runtime `print_element` / `__hash__` `T*` deref
     overloads),
   - comparison (`tpy::tuple_eq` / `tpy::tuple_lt` routing in the binop
     emitter, plus the `in`-needle storage lift),
   - construction slots (`_tuple_literal_slot_info` + address-of /
     `tuple_value_to_borrow` / `to_val_or_ptr<Dest>` value rendering),
   - boundary wraps (`_maybe_wrap_tuple_to_pointer` / `_to_storage`, the
     call-arg bridge, field writes, return/yield conversion, the await-arg
     lift).
   Closed 2026-09, the resumable-frame tuple local: its field was derived from
   the element TYPE while the write was decided per element, so the two
   disagreed whenever an element was not a plain lvalue borrow. A whole-tuple
   loop var now takes one field for both element shapes -- a pointer at the
   source element (`std::tuple<int32_t, A>* t;`) -- and every other tuple
   local's ownership is decided PER ELEMENT by each init and joined across all
   of them (a literal's VALUE-captured element and every element an owning call
   hands over are the frame's; an lvalue element stays a pointer at the
   caller's object), carried as an effective `Own[]`-marked tuple type onto the
   frame layout verdict THIR reads. One derivation, at the site that decides
   it, instead of two independent ones -- the shape the form fact generalizes.
   Remaining open exhibits of the bug class: nested tuples where outer/inner
   forms disagree (BUGS.md nested-tuple entries), rvalue tuple-of-records into
   borrow-form slots (BUGS.md rvalue address-of entry), the rvalue GENERIC
   tuple element gap, and the recursive-union-wrapper durable member (excluded
   from the `T*` form). A particularly sharp exhibit is the `key=` lambda over
   a generic-element tuple (`sorted(pairs, key=...)` /
   `min(a, b, key=...)` where `pairs: list[tuple[T, int32]]`): the lambda's
   param form is reconstructed at its DEFINITION site, but the form it actually
   needs is decided by the CONSUMER -- `builtin_sorted_key` calls `key(items[i])`
   with a STORAGE-form element, while `min_key`/`max_key` are handed the
   BORROW-form function args, so one lambda definition cannot satisfy both
   consumers under the current model. (`min`/`max` additionally need a
   borrow->storage RETURN conversion on the result.) Value-type keys (str/int)
   compile because borrow and storage forms coincide; reference-type keys fail
   the C++ build with no TPy diagnostic. With form as an explicit IR fact the
   lambda param carries its consumer-dictated form and the conversion is a
   visible node, not a definition-site guess. (BUGS.md key-lambda generic-element
   entry.) Another sharp exhibit is the **async/await union return** (B41 Union
   sibling, BUGS.md): aligning an `async def -> A | B` to the sync borrow
   convention requires classifying the await-result union frame-local as
   pointer-variant (`std::variant<A*,B*>`), but that single type-based
   classification reaches a *different* consumer -- a direct storage binding in
   the same coro body (`pet = h.pet`, union field -> local) -- whose async
   binding site emits a plain assignment with no `to_ptr_variant` lift, while
   the sync var-decl for the identical source emits one. So one form
   classification cannot satisfy both the await-result consumer (wants borrow)
   and the direct-binding consumer (whose binding site doesn't convert), and the
   fix splinters into either per-binding-site lifts or await-target-specific
   classification. (The scalar pointer-repr Optional case has no such split
   because its local form is uniformly `T*` -- the value/pointer-variant binding
   duality is specific to unions.) The same union value/pointer-variant split
   recurs at a third site, the **`match` capture of a genuine-union subject**
   (`def m(x: A | B): match x: case q:`): the capture `q` is hoisted in STORAGE
   form (value-variant `std::variant<A,B>`) while the subject is BORROW form
   (pointer-variant `std::variant<A*,B*>`), so the bind is a C++ type error with
   no TPy diagnostic -- and, unlike the await/field case, the *aliasing form a
   union capture should even take* (pointer-variant alias vs value-variant
   copy-out vs per-variant narrowing) is undesigned, so this exhibit is also a
   design question, not only a missing conversion node. (A *narrowed* union
   subject hits a related but distinct mismatch -- it analyzes as a record match
   against pointer-variant storage. BUGS.md union-match-capture entry.) With form
   an explicit IR fact, each binding's
   borrow<->storage conversion is a visible lowering node regardless of whether
   the source is an await payload or a field read, so the async and sync binding
   paths converge instead of diverging by consumer site. Each of these was/is
   handled by touching consumer-side dispatch sites; the IR fact replaces all of
   it with explicit conversion nodes. THIR should make form an explicit type
   fact (either two distinct tuple types, or a form tag on one), so conversion
   sites become visible in the IR rather than reconstructed in codegen.
   Recommendation: form tag on `THIRTupleType` with conversions emitted as explicit
   THIR nodes during lowering -- analogous to how borrows are explicit in MIR.

10. **Covariant return for polymorphic-owner types.** TPy lowers `Own[T]` to
    `std::unique_ptr<T>`, and C++ does not support covariant return on
    `unique_ptr` (only on raw `T*` / `T&` -- a deliberate, repeatedly-reaffirmed
    C++ language restriction, see P0670's rejection). This blocks the natural
    pattern of a method overriding a `@dynamic`-protocol slot with a narrower
    return type (e.g. `Dog.replicate(self) -> Own[Dog]` refining
    `Cloneable.replicate(self) -> Own[Cloneable]`, or
    `BaseException.clone(self) -> Own[BaseException]` refining
    `Throwable.clone(self) -> Own[Throwable]`). An attempt to support it on the
    C++ backend (a sema covariant-return acceptance rule + a
    `tpy::narrowing_cast<>` codegen bridge that emits the vtable slot at the
    parent's wider signature and downcasts at concrete-typed call sites) worked
    but introduced a divergence between the TPy declaration and the emitted C++
    signature, plus a cluster of edge cases (multi-protocol ambiguity, overload
    matching, the narrowing-cast hardening). It was dropped: the cost/benefit
    (mostly a naming preference -- `Box[BaseException]` vs the existing
    `Box[Throwable]` convention) did not justify the machinery, and the existing
    `Box[Throwable]` + virtual-`__raise__` convention (Phase 20) already handles
    polymorphic exception storage and dynamic-type recovery (`raise stored /
    except ConcreteType`). A backend that controls codegen below the C++ language
    layer (LLVM IR, or the MIR -> C++-or-LLVM split here) has no covariant-return
    restriction: raw pointers in vtable slots + a smart-pointer wrap at the call
    boundary (the standard pre-2011 C++ idiom, and what LLVM-targeting languages
    like Rust/Swift do by choice) makes this a clean codegen rule. Revisit when
    the backend question opens up; until then, declare polymorphic-protocol method
    returns at the protocol's own type (`Own[Cloneable]`, `Own[Throwable]`).
    Recommendation: handle at MIR -> backend lowering, not as a C++-backend
    sema/codegen feature.

11. **Uniform local model: every non-value local as slot + alias, with late
    representation folding.** THIR HALF RESOLVED 2026-06 -- see "Form as a
    First-Class THIR Fact". The THIR-era decision: carry the local representation
    VERBATIM (`cpp_local_representation`, the `LocalCppForm` analog) as non-semantic
    compatibility metadata, byte-identical to today's eager choice. The
    late-representation SELECTION + mem2reg FOLD described below stays MIR-era (it
    is the explicit normalization goal there); THIR does not attempt it. The
    remainder of this item is the MIR design (preserved below).

    Today the C++ shape of a non-value (or
    pointer-repr-tuple) local is decided EAGERLY at the binding site, by a
    zoo of per-shape mechanisms: `T&` ref binds and `auto&&` tuple aliases
    (single-assignment borrows), `T*` pointer-locals + hoisted
    `std::optional<T>` rvalue slots (`rebind_slots`, reassigned borrows),
    `std::optional<T>` optional-locals (deferred init, sync), the walrus
    variants of each, `tpy::frame_slot<T>` (resumable frames),
    `std::tuple<..., T*>` borrow-form tuple locals, and storage-form tuple
    locals -- tracked across `LocalCppForm` (now including `BORROW_TUPLE`)
    plus side sets that remain the classifier's backing store. Each
    mechanism re-implements init-deferral, rebinding, and aliasing slightly
    differently (operator= vs emplace vs lift), which is where the
    `optional` brace-init corruption class, the default-construct-before-
    assign waste, and the tuple owning/alias rebind rejection all came
    from. The MIR-native model dissolves this: every local is a PLACE (a
    slot owning storage, or a borrow of another place); binding kinds are
    explicit (own-init, alias, rebind); representation selection (direct
    `T`, `T&`, `T*` + slot, `optional<T>` / `frame_slot<T>`,
    pointer-element tuple) becomes a LATE per-place decision driven by
    facts the place already carries -- rebound? crosses a suspension?
    address escapes? null state needed? -- followed by a mem2reg-style
    FOLD that collapses single-binding straight-line places back to plain
    direct bindings so generated C++ stays readable and the hot paths
    (param borrows, loop vars) pay nothing. Provenance/escape soundness
    also unifies: the per-name fact sets sema accumulates today
    (`owns_fresh`, `owning_storage`, `ephemeral_borrow_vars`,
    `safe_to_return_vars`) become properties of the place's loans,
    compositional through aliases, ternaries, and walrus by construction
    instead of per-shape propagation rules. Sub-question: whether the C++
    backend should emit ONE deferred-storage primitive everywhere
    (`frame_slot<T>` in sync bodies too, with the state-aware-destruction
    TODO removing its alive bool) or keep `optional<T>` for sync --
    uniformity favors the former; decide when the fold pass exists so the
    choice is measurable. Pre-IR stopgaps this item subsumes: the tuple
    rvalue-slot design + owning/alias mix (landed pre-IR -- borrow-form
    slot + flow-correct owning fact + `BORROW_TUPLE`, with the side sets
    still the backing store rather than a unified place model), and the
    eager per-site binding decisions in `_gen_var_decl_code` /
    `_gen_named_expr` / the loop binders that it leaves scattered.
    Recommendation: make places-with-late-representation the MIR
    locals model (the natural reading of `Place`/`LoanInfo` above), and
    treat the C++ emission of each representation as a small backend menu
    the fold pass picks from. Sequencing (agreed): the representation
    model + fold are IR-ONLY -- building places/CFG/liveness against the
    AST would be writing MIR badly, twice. The one piece worth pulling
    forward pre-IR if the migration is not imminent is the sema-side
    provenance consolidation (one BindingProvenance record replacing the
    per-name fact sets; TODO.md entry carries the decision rule). STATUS:
    the storage + flow-plumbing half of that consolidation LANDED pre-IR --
    six name-keyed escape/ownership fact sets are now one `BindingProvenance`
    record per local (`tpyc/sema/context.py`) merged by one lattice-driven
    routine (`flow_facts.merge_binding_provenance`). What remains IR-only is
    the "root place + binding kind + durability" ONE-derivation model: the
    expression-walking derivers were deliberately left untouched (unifying
    them is the AST-side place model this item defers), and
    `ephemeral_borrow_vars` stays separate (loop-region-scoped, no flow
    merge). `BindingProvenance` is the proto-LoanInfo this item migrates.

    Scope extension: str/bytes VIEW locals belong under this umbrella too,
    even though they are value types lowered by a separate mechanism today
    (the `str_vars`/`bytes_vars` view-tracking facts + a binary
    view-XOR-owned-per-variable decision, e.g. `mark_view_reassigned_from_owned`
    promoting the whole local to `std::string`). The place/slot model says
    the variable stays the borrow form (`string_view`) and an owned-source
    assignment lands in a storage slot bound to it, with the fold collapsing
    to plain `std::string` when the slot is the only source (today's
    always-owned behavior). The win over the current binary choice is the
    MIXED case -- a local fed by a literal/param in one branch and an owned
    temporary in another no longer materializes the borrowed branches into
    `std::string`. As with the rest of item 11 this is fold-dependent (the
    fold must collapse the common always-view and always-owned cases or both
    regress to two C++ variables), so it is IR-era, not a pre-IR change. The
    current binary mechanism is sound (extra copy in mixed cases, never a
    dangle), so this is a quality/uniformity gain, not a correctness fix.

12. **Fate of the sema `Ref[T]` wrapper.** RESOLVED 2026-06 (narrowed) -- see
    "Form as a First-Class THIR Fact". `Ref[T]` dissolves into the borrow-form tag;
    at THIR a borrow-form expr IS what `Ref[T]` marked. Scope of the resolution:
    THIR introduces NO new `RefType` use and it stays frozen NOW; FULL removal from
    the type system is a later gate (rung F5/F-final), after the generic-slot
    (`val_or_ref_t`) and lambda-return cases are proven. Detail below.

    `RefType` is the sema-level
    "borrowed, not owned" marker: auto-inserted by `make_ref` on
    function/method params and returns, field/subscript access results, and
    iterator elements; never user-written. Production is centralized and
    disciplined, but consumption is split between two oracles: codegen
    strips the wrapper at function entry (`var_types` is built via
    `unwrap_ref_type`) and re-derives borrow-ness positionally from
    `is_value_type()`, while compatibility treats `Ref[T] ~ T` in both
    directions and inference canonicalizes it away per position
    (`to_owned_storage_form` for owned slots, `to_bare_slot_form` for
    bare-T slots, both in `sema/type_ops.py`). The strip-to-consume ratio
    across the compiler is roughly 8:1. What genuinely rides on the wrapper
    today: copy-into-storage detection (warning when a borrow is silently
    copied into a field/container), generic reference preservation
    (`U=Ref[Point]` -> `val_or_ref<Point>` for iterator combinators and
    `map(identity, ...)`), and lambda trailing return types (`-> T&`).
    Decision: keep `RefType` until THIR, but treat it as FROZEN -- do not
    extend it to new positions (each one adds strip sites and
    inference-leak surface); new borrow-form facts go on AST nodes per the
    migration rules in CLAUDE.md. At THIR lowering, `Ref[T]` dissolves into
    the explicit form fact of items 9 and 11: the borrow-vs-storage form
    tag plus explicit conversion nodes carries everything the wrapper
    encodes, THIR types do not contain `RefType`, and the stripping fabric
    disappears with it.

13. **Cross-module nominal identity as a single carried fact.** Distinct
    records/exceptions sharing a short name across modules are kept distinct
    by their module qname, but that invariant is currently enforced at ~5
    phase-specific sites keyed on the qname rather than from one object every
    consumer routes through: the resolver mints the qname from the import
    tuple, sema (`isinstance` / constructor record resolution) and codegen
    (`get_record_for_type`, `record_qualification`) re-resolve by qname, and
    `make_union` / `coercions` / `type_ops` compare qnames. No single
    chokepoint exists pre-IR -- that object *is* THIR. A related seam: the
    two type-to-C++ paths use different identity rules (`NominalType.to_cpp`
    keys `native_cpp_names` by short name with a qname fallback, while
    codegen's `type_to_cpp` is qname-aware) -- a latent re-collision vector
    (see BUGS.md). THIR should make the qname the single carried identity so
    these scattered checks and the dual `to_cpp`/`type_to_cpp` rule collapse
    into one. Surfaced by `fix-cross-module-type-identity` (audit triage #13).
