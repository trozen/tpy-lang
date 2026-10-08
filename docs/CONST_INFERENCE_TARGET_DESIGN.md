# Const inference: target design

Status: TARGET, not scheduled. Recorded 2026-10-08 after the shallow-readonly branch, with a
second-opinion review. It states where const/readonly inference is meant to end up so the
interim sema rules (READONLY_DESIGN "A handle's referent") have a destination.

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

## Migration

Incremental, per dependency component, no global flag day:
1. Separate the three facts (contracts / effects / ABI) in today's data structures.
2. Introduce symbolic access for a component; run the new analysis alongside the old verdict
   and compare.
3. Switch complete components (all bodies covered, trusted boundary contracts) to the solved
   verdict; each switched component has ONE authoritative verdict across signatures, callers,
   conversions and diagnostics.
4. Retire the interim sema rules inside switched components: the call-site handle demotion,
   the `for`-loop `__iter__` exemption and its climb gaps, `binds_owned_value`'s pointer-form
   rule, the conservative Rc / Weak / Arc / Task demotion.

Prerequisites: MIR effect coverage of every body (closures, generators / async, comprehensions
are excluded today), projection paths through handle / `Deref` steps and container elements
with provenance, cross-module summaries, and a THIR that no longer decides const.
