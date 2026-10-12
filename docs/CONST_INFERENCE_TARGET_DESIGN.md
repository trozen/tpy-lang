# Const inference: target design

Status: TARGET, not scheduled. Recorded 2026-10-08 after the shallow-readonly branch, with a
second-opinion review. It states where const/readonly inference is meant to end up so the
interim sema rules (READONLY_DESIGN "A handle's referent") have a destination.

Decided 2026-10-11 (see "Order and authority"): inferred const keeps ONE implementation at
every step -- today sema's Phase-2 inference -- and it is solved before any
representation-dependent finalization. There is no per-component switch and no comparison
run of a second inference.

## Goal and the claim we can make

Users never write `@readonly` / `readonly[T]` to get correct or efficient C++; the compiler
infers C++ `const` for methods, parameters and locals. Literal exactness ("const exactly when
no write can happen") is undecidable for unrestricted programs. The achievable claim:

- **Sound, annotation-free inference for analyzed bodies**: the least solution within a
  specified abstraction of places, projections and aliases (no write is ever hidden; const is
  dropped only where the abstraction cannot prove it).
- **Contract-relative at opaque boundaries**: `@native` stubs, `@dynamic` protocol methods
  (their virtual slot fixes the qualifier), function values / `Fn` parameters. An unannotated
  opaque contract cannot prove readonly.

## Three facts, not one

Today one name-based verdict (`infer_method_const`, `param_const`) stands for three different
things that must be solved separately:

- **Effects**: which places a body writes, including through handles (`Ptr`, `Span`,
  iterators, `Rc` payloads), derived places, returned / stored aliases, escapes and callbacks.
- **Required access**: what the body's contract hands out or needs, independent of writes.
      def get(self) -> A:
          return self.a      # writes nothing, yet must NOT be const: it hands out A&
  Conversely a write through a `Ptr[A]` field is legal inside a `const` method.
- **Emitted const**: the C++ qualifier, derived from the two above plus ABI constraints
  (`@dynamic` virtual slots, declared contracts).

## Architecture

Not "sema decides" and not "resolve at render". The target is a shared place / effect
analysis on an **access-symbolic MIR core**, followed by **access (capability / provenance)
constraint solving**, obligation checking, and only then finalized THIR / C++ lowering:

1. Sema checks types and ENFORCES DECLARED readonly (`@readonly`, `readonly[T]`,
   implicit-readonly dunders / `@pure` / frozen records, `auto_readonly` markers) -- user
   contracts. It stops inferring const for emission.
2. Bodies lower to an access-symbolic core: places and calls carry symbolic access variables
   instead of a finalized const. Clone pairs (`@auto_readonly`, component-marked twins) stay a
   symbolic choice so the resolved callee and the C++ overload cannot diverge.
3. Per-function summaries record writes, returned / stored aliases, escapes, callback effects
   and required capabilities. They are solved as a monotone least fixpoint over the call
   graph, scheduled over the workspace dependency graph (not import order), with generic
   effect schemes or per-instantiation solving (late instantiations invalidate dependents).
4. Solved access finalizes parameter / local / tuple / union const spellings, varargs,
   conversions and temporary binding TOGETHER (const already decides whether some conversions
   are admitted, not only their spelling).
5. A call on a readonly receiver to a method without declared readonly is accepted by sema as
   an OBLIGATION carrying the call span, receiver / argument path and an effect witness; the
   solver discharges it or reports there with a "the callee writes ..." note. Missing analysis
   coverage is never treated as proof; no unresolved obligation reaches C++.

Foundations already exist: MIR places / projections, call summaries and writing-call effects
(MIR_CALL_SUMMARY_INTERFACE_PLAN.md, MIR_CALL_EFFECTS_PLAN.md). Effect coverage can be made
complete before every lifetime proof is; give the analyses separate completeness states.

## Consumers of today's verdict that must move

- Readonly-receiver acceptance (explicit calls reject during sema; implicit ones already defer)
  -> obligations (point 5).
- Protocol conformance and `@dynamic` overrides read `is_readonly` -> deferred conformance
  obligations; virtual-slot qualifiers stay ABI constraints.
- Borrow conflicts, view demotion, copy diagnostics, match-subject warnings -> after solving,
  including representation decisions, not just diagnostics.
- Const spellings and conversion admission in THIR -> finalized from solved access.
- `@auto_readonly` clone / result access selection -> symbolic until solved.
- MIR receiver passing / validation currently imports the finalized verdict
  (`effective_params` / `receiver_param`) -> separate declared permissions from inferred
  requirements first.
- Narrowing: note that call handling today invalidates field facts unconditionally; it does
  NOT preserve them using inferred const (older docs say otherwise). Effect-aware narrowing is
  a precision gain of this design; narrowing needed to resolve types / callees needs deferred
  constraints or reanalysis.

## Hard parts (where precision is bounded by the abstraction)

Containers of pointers (alias provenance through stores / loads and indices; current container
summaries merge all elements), closures / callbacks (captured-environment effects, callback
target polymorphism), recursion with growing paths (widening), generics instantiated later,
cross-module scheduling, `@dynamic` dispatch, and compile time (context sensitivity, alias
sets and specialization dominate cost).

## Order and authority (decided 2026-10-11)

**Order: solve before representation-dependent finalization.** Finalized THIR cannot carry a
symbolic const: about twenty THIR sites decide what LOWERS from const, not only how it is
spelled (the binding planner's hoist kind, inline prvalue bind vs hoisted argument temp vs
refusal in the argument admission tables, mutated-slot temp hoists, union const wrappers, the
`@auto_readonly` clone and its result access). Lowering twice (once with a guessed const, then
with the solved one) is not an answer by itself either: the first pass must already represent
programs whose acceptance depends on the solution, its effects must conservatively cover the
final program's, and changed temporaries, overload picks or clone choices must not change
aliasing -- none of which holds without a defined monotone domain. The solver therefore runs
on facts available BEFORE finalized THIR (sema's body facts today, an access-symbolic IR
upstream of THIR later); THIR keeps receiving a finished verdict.

**Authority: one implementation at every step.** Sema's Phase-2 inference
(`mutation_propagation`, `infer_method_const`, `param_const`) is the only implementation of
inferred const. MIR computes no const meanwhile and keeps reading the finished verdict
(`effective_params` / `receiver_param`); its write set for a const place is therefore not
independent evidence. The route to the target extracts that inference into one shared
effect / access solver whose consumers are signatures, obligations, THIR and MIR, and later
replaces its body-fact PRODUCER (sema -> MIR or the access-symbolic IR), retiring the replaced
producer in the same step. A per-component switch (MIR's verdict where a component is fully
covered, sema's elsewhere) was rejected: it keeps two inference implementations live.

Replacing the producer requires MIR lowering that records writes instead of trusting sema's
const, effect-only completeness separate from lifetime completeness, an SCC fixpoint for
recursion (today recursion makes a summary OPAQUE), and effect coverage of every body kind.
Coverage is a PRECISION requirement, not a soundness one: a solver may treat an unanalyzed
body as unknown, but switching that way early rejects programs that compile today, which is a
user decision.

An obligation discharges against required ACCESS and the emitted ABI, not against the absence
of a write:

    def read(xs: list[int32]) -> int32:      # const List& xs: accepts readonly storage
        return len(xs)
    def expose(xs: list[int32]) -> list[int32]:   # List& xs: exports mutable access
        return xs

Neither writes, yet only `read` discharges an obligation from a readonly context; a rejection
witness may be "returns mutable access" or "the virtual slot requires a mutable receiver" as
well as a write. Witnesses need the call edges that propagation clears today.

## Migration

Incremental, one implementation at every step, no global flag day:
1. Separate the three facts (contracts / effects / ABI) in today's data structures, moving
   real consumers in the same step (declared readonly vs inferred const first).
2. Calls from a readonly context into callees without declared readonly become obligations,
   discharged after workspace finalization by the existing pending readonly-receiver checks
   (`CallChecks.resolve_pending_readonly_receiver_checks`), with a witness.
3. Extract the inference into the shared solver; move its consumers onto it.
4. Replace the body-fact producer once its prerequisites hold (below), retiring the old one.
5. Retire the interim sema rules: the call-site handle demotion, the `for`-loop `__iter__`
   exemption and its climb gaps, `binds_owned_value`'s pointer-form rule, the conservative
   Rc / Weak / Arc / Task demotion.

Prerequisites: MIR effect coverage of every body (closures, generators / async, comprehensions
are excluded today), projection paths through handle / `Deref` steps and container elements
with provenance, cross-module summaries, and a THIR that no longer decides const.
