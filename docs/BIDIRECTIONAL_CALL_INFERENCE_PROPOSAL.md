# Bidirectional Call Inference Proposal (V1)

## Problem

TurboPython call resolution is mostly argument-directed today:

1. infer/check argument types
2. resolve overload/generic substitutions from arguments
3. apply coercions
4. derive return type

This works for most APIs, but it is weak for return-type-driven patterns such as:

- `unsafe_cast(p)` where the target pointer type is expected from context
- generic functions where expected result type should help disambiguate candidates

Current behavior relies on targeted special handling (`unsafe_cast`) and ad hoc hint threading.

## Goal

Add a constrained, explicit bidirectional inference model so expected type context can participate in call resolution, while keeping diagnostics deterministic and easy to debug.

## Non-goals (V1)

- No global whole-expression constraint solving (Swift-style expression solver)
- No silent tie-breaking between multiple viable candidates
- No changes to runtime semantics
- No broad rewrite of parser/AST

## Design Principles

1. Context can refine, never obscure:
   - Use expected type context only to narrow candidates already viable from arguments.
2. Ambiguity must remain explicit:
   - If multiple candidates remain, emit an error and require explicit type arguments or annotations.
3. Keep existing success behavior stable:
   - Existing unambiguous calls should keep resolving the same way.
4. Prefer local reasoning:
   - Each call is resolved with a bounded local algorithm and clear diagnostics.

## Scope V1

Enable context-aware call resolution for:

1. Function calls:
   - user functions
   - builtin module functions
2. Method calls:
   - builtin methods
   - user methods
3. Generic and non-generic overload resolution

Context sources in V1:

1. Assignment/annotation context:
   - `x: T = f(...)`
2. Argument context (nested call):
   - `g(f(...))` where `g` parameter type is known
3. Return context:
   - `return f(...)` where function return type is known

Explicitly out of scope in V1:

- cross-statement propagation
- branch-wide constraint merging beyond current sema flow

## Adjacent Usability Extensions

These issues are closely related to bidirectional inference ergonomics and should
be tracked in the same proposal, even if some land in V2.

### A. General Contextual Call Rule (Functions, Methods, Constructors)

Contextual typing should apply to all call expressions, not only constructors.

Target patterns:

- `x: T = f(...)` where expected result type constrains `f`
- `g(f(...))` where `g` argument type constrains `f(...)`
- `obj.m(f(...))` where method argument type constrains nested call

Core rule:

1. Any call expression may consume expected type context when available.
2. Context is used to narrow/refine viable candidates, not to silently pick
   among ambiguous candidates.
3. If still ambiguous, require explicit type arguments or annotation.

### B. Contextual Constructor Instantiation

Target patterns:

- `l: list[int] = list()`
- `p: Ptr[int] = Ptr()`
- `l = list()` where `l` already has known type `list[int]`
- `p = Ptr()` where `p` already has known type `Ptr[int]`

Constructors are a special case of the general contextual-call rule above.

Proposed constructor rule:

1. When constructor type args are omitted, use expected type context if it is
   known and unambiguous.
2. If context is absent or ambiguous, require explicit type args.

Notes:

- This is the same inference direction as call-context inference, but for
  constructors/type calls.
- Supports both declaration-time and reassignment contexts (subject to
  declaration rules below).

### C. Declaration/Initialization Policy Interaction

Current friction:

- `l: list[int]; l = list()` fails because annotated variables must be
  initialized at declaration.

Policy options:

1. Keep current rule (must initialize annotated vars immediately).
2. Relax rule for selected types/contexts.
3. Relax rule generally with definite-init analysis requirements.

Recommendation:

- Treat this as a separate policy decision from resolver mechanics.
- Resolver support for contextual constructors should not depend on a specific
  choice here.

### D. Pointer Nullability Semantics

Current inconsistency:

- `Ptr()` constructs `nullptr`.
- `p: Ptr[T]; p = None` is rejected (non-Optional assignment).
- `p is None` is rejected because `is None` is currently limited to Optional.

Open model choices:

1. Optional-only null model:
   - raw `Ptr[T]` is non-None in type system; use `Optional[Ptr[T]]` for
     nullability checks.
2. Nullable-pointer model:
   - allow `Ptr[T] is None` checks directly and define narrowing behavior.
3. Hybrid model:
   - keep Optional as primary but permit selected pointer/None checks for
     ergonomics.

Recommendation:

- Decide model before broad changes; this impacts sema rules, narrowing, and
  diagnostics beyond call resolution.

## Algorithm (V1)

Given call `C(args)` and optional expected type `E`:

1. Build candidate set from name/arity.
2. Perform current argument-based viability check:
   - strict pass, then coercion pass (existing model)
3. If `E` exists, apply return compatibility filter:
   - keep candidates where `candidate_return` is compatible with `E`
   - compatibility uses existing coercion/compat rules where safe
4. If one candidate remains:
   - resolve generics
   - coerce arguments
   - finalize return type
5. If none remain:
   - report mismatch with explicit mention of expected type influence
6. If multiple remain:
   - emit ambiguity error with candidate signatures
   - suggest explicit type args or annotation

Important:
- Expected type is a filter, not a scoring bonus.
- No hidden heuristic tie-breakers in V1.

## Generic Inference Interaction

For generic candidates:

1. infer from argument types first (current behavior)
2. if inference leaves unresolved params, allow expected return type to infer remaining params when mapping is direct and unambiguous
3. if conflicting inference occurs, report conflict explicitly

Example intent:

- `def id[T](x: T) -> T`
- `y: Int32 = id(1)` -> infer `T = Int32` using args plus expected type if needed

## Unsafe Cast in V1

`unsafe_cast` can remain special initially, but V1 should reduce special-case surface:

1. Keep explicit form:
   - `unsafe_cast[T](p)`
2. Keep context form:
   - `q: Ptr[U] = unsafe_cast(p)`
3. Ensure nested argument context remains valid:
   - `sink(unsafe_cast(p))` when `sink` parameter type is known

Longer term, `unsafe_cast` can move closer to regular call machinery once return-type constraints are first-class in resolver flow.

## Diagnostics

Required diagnostic behavior:

1. Ambiguous with context:
   - mention expected type and candidate list
2. No candidate after context filter:
   - show candidates that matched args but failed expected return compatibility
3. Generic inference conflict:
   - identify type parameter and conflicting inferred types

Error text should always include:

- call signature attempted
- expected type (if used)
- action suggestion: explicit type args or annotation

## Rollout Plan

1. Phase 1:
   - Introduce resolver API that accepts optional expected type for calls/methods.
   - Keep behavior identical when expected type is absent.
2. Phase 2:
   - Apply expected-type filtering for a narrow set of call paths.
   - Add regression tests for `unsafe_cast`, nested calls, and generic ambiguity.
3. Phase 3:
   - Expand to all function/method call paths.
   - Remove redundant special-case logic where safe.

## Risks and Mitigations

1. Risk: behavior changes in existing overload-heavy code
   - Mitigation: context filter only, no heuristic tie-break in V1
2. Risk: worse error messages
   - Mitigation: structured diagnostics with candidate lists and context reason
3. Risk: implementation complexity growth
   - Mitigation: phased rollout with strict scope and tests at each phase

## Test Strategy

Add/keep tests across groups:

1. `calls/`:
   - nested call context propagation
   - method argument context propagation
2. `generics/`:
   - generic inference with expected return context
   - ambiguity requiring explicit type args
3. `pointers/`:
   - `unsafe_cast` explicit and context forms
4. `imports/`:
   - module and aliased function call paths with context

## Open Decisions

1. Should return compatibility allow all coercions, or only safe widening?
2. Should expected return type participate before or after generic substitution attempt?
3. Should constructors participate in V1 or V2?
4. Should we expose a flag for strict mode while rolling out?
5. Which declaration/initialization policy should govern typed-but-later-assigned vars?
6. What pointer nullability model should TPy adopt for `Ptr[T]` and `None` checks?
