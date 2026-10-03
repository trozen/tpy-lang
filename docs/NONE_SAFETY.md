# None Safety

This document describes TurboPython's `T | None` safety behavior, current limitations, and planned work.

## Status

| Item | Status | Notes |
|---|---|---|
| Parse `assert` statements | Done | Supports `assert cond` and `assert cond, "literal"` |
| `assert`-driven Optional narrowing | Done | `assert x is not None` narrows `x` after assertion |
| Warning + runtime check for Optional field/method/subscript access | Done | Applies when Optional may be `None` |
| Elide runtime checks when non-None is proven | Done | Via `if x is not None` and `assert x is not None` |
| `is None` / `is not None` on value optionals | Done | Uses `std::optional<T>::has_value()` in generated code |
| Optional-aware identity checks after flow narrowing | Done | Avoids invalid `== nullptr` for value optionals |
| Optional in value-consuming operators without proof (`x + 1`) | Done | Warns and inserts runtime null checks |
| Truthiness narrowing on Optional names (`if x`, `assert x`) | Done | Narrows on true path; value-optionals emit warning |
| Truthiness narrowing in composed conditions (`not/and/or`) | Done | Name-based facts compose through boolean operators |
| Truthiness narrowing for field/subscript expressions | Done | Supports stable expression identities (field/chained field, builtin-first simple subscripts) |
| Generalized flow facts beyond name-based narrowing | Partial | Added expression identities; still not full arbitrary-expression fact tracking |
| Expression-identity narrowing (Phase 3a) | Done | Tracks stable identities for `obj.field` / chained fields / builtin-first `obj[i]` (simple indexes) |
| Loop-focused truthiness/flow stress semantics (Phase 3b) | Done | Conservative loop-entry policy applied; dedicated loop stress tests added (`while`/`continue`/`break`/nested/short-circuit) |
| Invalidation across mutation/alias boundaries | Partial | Implemented for rooted writes, readonly-aware call boundaries, and unknown/non-readonly call invalidation |
| Readonly metadata coverage across call paths | Partial | Covered for normal calls, module calls, static methods, protocol calls, `super()` methods, and constructors; effect qualifiers on protocols/constructors remain conservative |
| Readonly policy for side effects (`print`) | Done | `print` is explicitly non-readonly due to observable I/O side effects |
| Readonly policy for fresh-object construction + `Own` return | Partial | Policy documented (allowed when not mutating pre-existing observable state); enforcement details still being refined |
| Protocol readonly effect contracts (`Sized.__len__`, `Sequence.__getitem__`) | Todo | Requires protocol-level readonly qualifiers and conformance/inference support |
| Concurrency-aware narrowing safety model | Todo | Only trust facts under proven thread-safety/stability guarantees |
| Effect contracts for user-defined reads (`readonly`/`may_mutate`) | Partial | `@readonly` contract implemented with conservative enforcement; broader effect lattice pending |
| Conservative effect inference for missing annotations | Todo | Infer readonly where provable; default unknown to unsafe |
| Temporary single-evaluation auto-rewrite for unstable reads | Deferred (intentional) | Keep explicit/user-authored for now; compiler should warn instead |
| REPL-specific None-safety regression suite | Postponed | Defer to a future generic REPL regression effort (not None-safety-specific) |
| Mixed Optional/non-Optional equality & ordering semantics | Partial | `==`/`!=` are None-safe (no warning, no panic); `<`/`>`/`<=`/`>=` still warn + runtime check |
| Custom truthiness semantics policy | Todo | Future user-defined/overridden truthiness behavior not specified |
| Rich assert messages (non-literal expressions) | Todo | Currently string-literal-only |

## Test Coverage & Blockers

| Area | Coverage | Blocking condition |
|---|---|---|
| Assert narrowing (`assert x is not None`) | Covered | None |
| Compound assert narrowing (`assert x is not None and ...`) | Covered | None |
| Reassignment invalidates narrowing | Covered | None |
| Guard-clause narrowing (`if x is None: return`) | Covered | None |
| Else-branch narrowing | Covered | None |
| Non-None assignment proves non-None | Covered | None |
| Truthiness assert narrowing (`assert x`) | Covered | Value-optionals warn; non-value optionals narrow quietly |
| Truthiness composed conditions (`not/and/or`) | Covered | Name-based only |
| Branch merge intersection | Covered | None |
| Mixed-variable narrowing isolation | Covered | None |
| Negation narrowing (`not (x is None)`) | Covered | None |
| Function-boundary isolation | Covered | None |
| Operator-phase runtime checks (`+`, comparisons, unary on Optionals) | Covered | Runtime panic tests for binop/comparison/unary optionals |
| Proven optional comparison unwrapping (`(*x) > 0` vs `x > 0`) | Covered | Dedicated post-assert comparison test |
| Loop-specific narrowing stress (`while` + reassignment/continue/break) | Covered | Dedicated loop suite: `loop_while_expr_reproof_ok`, `loop_continue_if_merge_narrowing`, `warn_loop_break_does_not_prove_after_loop`, `warn_loop_subscript_fact_stale_after_write`, `warn_loop_field_fact_stale_after_rebind`, `loop_nested_branch_merge`, `loop_short_circuit_reproof`, `loop_foreach_optional_body_narrowing`, `warn_loop_call_invalidation_after_condition`, `loop_body_renarrow_without_header_fact`, `loop_nested_outer_fact_preserved`, `warn_loop_nested_inner_fact_not_leaked` |
| Field/subscript truthiness narrowing (`if obj.field`, `if items[i]`) | Covered | Stable identity subset implemented; tests added for field/subscript narrowing |
| Mutation/alias invalidation for expression identities | Partial | Root writes and unknown-call invalidation covered; effect-aware/method-specific invalidation pending |
| Concurrency-aware narrowing guards | Not covered | Needs thread-safety/stability contracts and tests |
| User-defined collection read contracts | Partial | `@readonly` exists; broader effect metadata and inference still pending |
| REPL-specific regression suite | Postponed | Defer to future cross-cutting REPL smoke/regression work |
| Rich assert message forms | Not covered | Non-literal assert message support not implemented |

## Goals

- Keep code idiomatic and migration-friendly.
- Prevent undefined behavior in generated C++.
- Let users opt into explicit proofs (`if x is not None`, `assert x is not None`) to remove runtime checks.

## Current Behavior

### Non-value optionals (`Point | None`, `list[T] | None`, ...)

- Unproven field/method/subscript access compiles with a warning and inserts a runtime null check.
- Proven non-None access (via guard or assert) does not insert that check.

Example:

```python
def f(p: Point | None) -> int32:
    return p.mag()   # warning + runtime null check
```

```python
def g(p: Point | None) -> int32:
    if p is not None:
        return p.mag()  # no warning, no extra null check
    return -1
```

### Value optionals (`int32 | None`, `Bool | None`, `float | None`)

- `is None` / `is not None` checks work.
- `== None` / `!= None` on optionals is rejected; use `is None` / `is not None`.
- Truthiness checks (`if x`, `assert x`, `while x`) narrow on true-path but emit warning.
  - Reason: truthiness excludes both `None` and falsy non-None values (`0`, `0.0`, `False`, `""`, etc.).
- Unproven value-consuming operator use compiles with a warning and inserts runtime checks.
- Proven non-None operator use (guard/assert) emits unchecked unwraps with no extra check.

### Narrowing sources

- `if x is not None:`
- `if x is None: ... else:`
- `if x:` / `while x:` / `assert x` (name-based; value-optionals warn)
- short-circuit condition flow (`and` / `or`)
- `assert x is not None`

## Assert

Supported:

- `assert cond`
- `assert cond, "message"`

Behavior:

- At runtime: panics if condition is false.
- In sema: contributes narrowing facts after the assertion.

Current limitation:

- assert message must be a string literal.

## Diagnostics

Unproven optional access emits:

- warning about potential None access
- note that generated code adds runtime null check
- guidance to use guard/assert to prove non-None

## Known Limitations

- Narrowing applies to local variable names and dotted field paths
  (`obj.field`, `obj.a.b` -- see the field-narrowing tests). Field-path
  facts are invalidated aggressively: any method call on the receiver,
  any write to the path or a prefix of it, or passing the object by
  mutable reference kills the fact under every spelling of the object
  (see "Exception-Path and Call-Site Soundness"). Subscript
  access (`items[i]`) gets only limited fact tracking; binding to a local
  first remains the reliable pattern:
  ```python
  val = obj.field
  if val is not None:
      val.method()  # narrowed, no warning
  ```
- Effect contract support is partial: `@readonly` exists, but full effect lattice/inference is not implemented.
- Ordering operators (`<`, `>`, `<=`, `>=`) across mixed Optional/non-Optional values are still conservative (warning + runtime check), matching Python 3 which raises TypeError for `None < 5`.
- `==`/`!=` with Optional value-type operands are None-safe: no warning, no runtime panic. `None == 5` evaluates to `False`, `None != 5` to `True` (delegated to C++ `std::optional` comparison).

## Design Decisions (Locked)

- Safety rule: narrow aggressively, invalidate more aggressively.
- Field-path narrowing exists but is killed by any operation that could
  mutate the object behind the path (method calls, mutable passes, writes,
  mutations through any alias); when in doubt, bind to a local.
- Keep behavior explicit; emit warnings and keep runtime checks when proof is missing.

## Implemented Flow-Fact Semantics

### Loop Stress Semantics

- Implemented:
  - conservative loop-entry fact application
  - `while` condition re-proves name Optional facts for loop body entry
  - body kill-set applied at loop entry: a pre-loop fact (narrowing, ptr
    non-null, value range) is dropped when the body may write the name --
    the back-edge can re-enter the body after the fact was invalidated, so
    single-pass analysis must not assume it (`prescan.collect_fact_kills`
    + `InitTracker.apply_fact_kills`)
  - targeted loop stress tests covering reassignment, `continue`, `break`, nested merges, and short-circuit conditions
- Rule:
  - treat loop body facts as iteration-local unless re-proven by current iteration condition.
  - treat pre-loop facts as body-entry facts only for names the body cannot write.
  - if safety proof is not present at use site, keep warning + runtime null checks.

### Exception-Path and Call-Site Soundness

The same kill-set discipline applies at every other control-flow meet
where a fact may have died on some path:

- `except` handlers: analyzed from pre-try state MINUS the try body's
  kill-set (an exception can be thrown at any point in the try body).
- `finally` bodies: analyzed under all-paths entry facts (try/else/handler
  kill-sets applied); the normal-path facts are restored for code after
  the statement, minus whatever the finally body itself killed.
- Call sites: once a nonlocal-writing closure has been defined, every
  subsequent call kills facts for its nonlocal targets (any call may
  invoke the closure).
- Aliases: a field store, a mutating call and a call argument consult the
  pre-scan's order-free may-hold relation (`prescan.InPlaceWrites`), which
  records which names and field paths each name may hold through any
  binding form -- assignment and rebind, ternary and `and`/`or` arms,
  walrus, unpack, for / with targets, `match` captures, a nested def's
  own bindings (they join the outer body's relation), field chains
  (`t = a.inner`). Two kills do not: the loop / handler sweep for a
  rebound name kills only facts spelled through that name, and a call
  inside a condition does not kill the facts that condition derives
  (`BUGS.md#condition-call-keeps-field-fact`).
  A mutating call or a call argument kills the facts beneath the object
  under every spelling it may have. A field store kills what lies beneath
  the stored slot under every spelling; a store of a value that may be
  None also kills the slot's own fact under every spelling (`t = a if c
  else b; t.v = None` leaves `a.v` unproven), while a value that cannot
  be None keeps it (`t.v = 5` keeps `a.v`). At loop entry the loop
  body's stores are not typed yet, so a store inside the loop counts as
  possibly None there (the handler and finally sweeps see the analysed
  stores). A store never creates a fact. A bare name holding the slot shares it (`u = h.payload;
  h.payload = None` drops `u`'s narrowing), but a local that copies a
  value-typed field is not an alias of it: after `x = a.v` with `v: int
  | None`, `a.v = None` leaves `x`'s narrowing (likewise a `str`, a
  `Ptr` or a value-type record; what lies beneath a copied pointer is
  still its pointee's).
  The relation covers aliases of INLINE storage: a name holds exact
  places (a root and a field path). Three guards bound the work per
  name: a place deeper than 8 fields, more than 16 places under one root,
  more than 64 places in all; past one the relation treats that root as
  possibly aliased by the name anywhere beneath it (a conservative
  answer: such facts are killed, not kept).
  A projection inside a binding cycle is not followed (`node =
  node.next`, or `a = b.next; b = a.next`), so a walk relates the walk
  variable to its start only, and anything reached through a
  pointer-like field is tracked by its spelling: a store or a mutating
  call through one pointer path does not kill a fact read through
  another. The view rule (whether a `str` / `bytes` local can stay a
  view) uses the same exact sharing.
  Not modelled (each filed): pointer structures -- a ring, a multi-hop
  chain back to its start, a self-looping field, a doubly linked list, a
  parent pointer, a second parameter or an `Rc`-shared node reachable
  through pointers, a pointer into an inline field, a binding cycle mixing
  an inline and a pointer projection
  (`BUGS.md#pointer-structure-aliases-unmodelled`), and a local linked
  into one and mutated through the owner
  (`BUGS.md#mutating-call-walk-keeps-linked-local-fact`); parameters
  passed the same object, a pointer-typed root, and a store inside a
  nested def through an alias bound in the outer body
  (`BUGS.md#may-hold-relation-unmodelled-shapes`); a call result, a
  `@property` result, a `with ... as` target, a subscript or an
  iteration variable as an origin (a name bound from one holds only
  itself: `BUGS.md#borrowed-origin-not-related-to-source`,
  `BUGS.md#subscript-element-not-keyed`); a closure mutating a capture
  (`BUGS.md#closure-capture-mutation-invisible-to-kills`); a property
  setter, a destructor or a user `__iadd__` run by a store
  (`BUGS.md#property-setter-sibling-write-invisible-to-loop-kill-set`,
  `BUGS.md#field-store-destructor-skips-fact-kill`,
  `BUGS.md#iadd-target-keeps-facts-beneath`); a `Ptr` argument skipped
  by the call kill (`BUGS.md#ptr-or-tuple-argument-skips-fact-kill`); a
  mutating call inside a narrowing condition
  (`BUGS.md#condition-call-keeps-field-fact`); a call that replaces a
  narrowed `Box` payload through its receiver
  (`BUGS.md#deref-view-survives-receiver-call`).

## Planned (Detailed)

### User-Defined Collections and Effects (Future)

- Current implemented subset:
  - `@readonly` annotation is available on functions and methods.
  - Compiler enforces conservative contract checks:
    - rejects field/subscript writes in readonly bodies
    - rejects writes to globals in readonly bodies
    - rejects calls to unknown/non-readonly functions in readonly bodies
  - Call metadata is propagated through major dispatch forms (including protocol methods, `super()` methods, and constructors) so readonly checks have consistent visibility.
  - `print` is intentionally non-readonly because it has observable I/O side effects.
  - Readonly is about not mutating pre-existing observable state; constructing fresh local objects and returning them (for example via `Own[T]`) is intended to be allowed.
- Remaining work:
  - protocol-level effect contracts for read APIs (e.g., `Sized.__len__`, `Sequence.__getitem__` marked readonly)
  - protocol conformance rule: readonly-required protocol methods must be implemented by readonly (explicit or inferred) methods
  - richer effect metadata (`may_mutate`, qualifiers)
  - conservative effect inference for unannotated functions
  - decorator form `@tpy.readonly` (currently `@readonly` only)
  - thread-safety/stability qualifiers for concurrent reads
- Long-term: add conservative automatic inference for missing annotations.
  - if a function is not annotated, compiler may deduce and mark it readonly when proof succeeds
  - explicit annotation remains the enforceable contract surface
  - infer readonly only when provable
  - default unknown to unsafe
  - keep annotation-based enforcement for explicit intent

### Custom Truthiness Semantics (Future)

- Current truthiness policy assumes built-in/value semantics.
- For future user-defined or overridden truthiness, behavior must be explicitly specified before enabling narrowing based on those semantics.
