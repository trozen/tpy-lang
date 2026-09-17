# Callable Provenance: Requirements on the Analysis-Only MIR

This document owns rules 9-23, 25, 28, 29 and 33-36 of the shared callable-rule
numbering; `docs/CALLABLE_CONTRACT_DESIGN.md` owns 1-8, 24, 26, 27, 30-32 and 37
and the admission layer A1-A12. A rule number never moves between the two.

## Purpose

`docs/CALLABLE_CONTRACT_DESIGN.md` decides what a callable's result MEANS -- its
form, its spelling, its legal bindings, its C++ render -- and makes that half
sound with a conservative admission layer: a borrow-returning binding is admitted
only when every root is proved to be an admissible argument, closures with
borrowed environments may not be stored or returned, unknown effects conflict with
any live loan, and global-rooted, temporary-rooted and borrow-bearing-aggregate
results reject. Those restrictions are not the design's intent. They are proposed
conservative checks whose feasibility without a place model, control-flow graph
or new loan propagation must be established by the compatibility gate.

Some restricted programs compile today and are safe under the intended semantics;
others expose actual dangling references. The contract document's "Compatibility
gate" requires these groups to be measured separately before choosing the
contract-first rollout. The 2026-09-17 decision brings analysis-only MIR forward;
`MIR_ANALYSIS_PLAN.md` records the proposed implementation increments. These
requirements remain the intended behavior. A temporary lack of proof must not
become a permanent language restriction by default.

This document states what has to exist before each restriction lifts. It is a
REQUIREMENTS document, not an implementation plan: it carries no file or line
references, no writer lists and no integration choreography, because those decay
while semantic acceptance criteria do not. Its rule numbers are the same numbering
as the contract document's, inherited from section VII of
`docs/CLOSURES_CALLABLE_DESIGN.md` and kept as stable identifiers. Every rule here
is marked *deferred-to-MIR*: it is a property the analysis-only MIR must deliver,
stated so that a later implementation can be checked against it, together with the
programs it must admit and the programs it must reject. The first consumer of the
analysis-only MIR described in `docs/IR_DESIGN.md` is this document.

## What the analysis-only MIR must provide

Optimization passes and MIR-backed C++ emission are NOT prerequisites; the
analysis-only stage is enough. It needs six things.

1. **Stable place identities.** Locals, temporaries, captures, qualified globals,
   fields, dereferences, and summarized container structure and elements, each
   with an identity that survives being copied into another holder. Rule 14 needs
   a TEMPORARY to have an identity; rule 33 needs two fields of one object to be
   different places.
2. **Explicit operations.** Alias, borrow, copy, move, rebind, closure
   construction, call, return, and escape into a field or a container, each an
   operation in the IR rather than a shape a consumer re-derives from an
   expression tree.
3. **A CFG that preserves evaluation order.** Branches, short-circuit expressions,
   loops with their back edges, and exceptional cleanup paths. A lifetime-sensitive
   shape the CFG cannot represent must reject, not fall through.
4. **Liveness plus loan propagation.** Loans follow EVERY holder, including copied
   closures and aggregates, and do not end merely because the local they were
   created from becomes dead.
5. **Summary and effect application.** Rules 15-19 and 33-37 applied at call
   sites, including named calls that forward a callback to another callee.
6. **Per-instantiation obligations.** Form and effect checks discharged once per
   instantiation of a generic body. A CFG does not itself provide the deferred
   machinery rules 17, 26, 27 and 36 need.

No SSA, no exact-index disjointness, no move optimization and no restructuring of
the CFG back into readable C++ is required for any of this.

**What it subsumes of today's checker.** The `BorrowTracker` loan table and the
borrow-specific flow-fact snapshots; alias and root propagation with its
storage-generation replay; the AST lifetime and last-use analysis; the element,
iterator, view and pointer invalidation checks; and the return and escape lifetime
checks. Later, also the move/copy and local-representation decisions.

**What it does not subsume.** Overload resolution, type inference, capture
discovery, and -- by the existing IR plan, which keeps mutation propagation in
sema -- the interprocedural summary FIXPOINT itself. MIR consumes finalized, sound
summaries; it does not have to compute them. Two cautions: a CFG with conservative joins improves precision
but does not by itself resolve every correlated-path false positive, and the
invalidation policy must be TPy's, not Rust's (see "Mutable aliasing" below).

## Requirements

**(c) Closures are aggregates with loans.**

9. **A closure is an aggregate whose borrowed captures carry ordinary place
   loans** (*deferred-to-MIR*) -- by-value captures that themselves contain
   borrows (a `Span`, a `Ptr`, a view) included -- and those loans must follow the
   closure through bindings, arguments, copies, moves, aliases (`g = f`), ordinary
   named-call forwarding, and the three ESCAPE channels: a return, a container
   insert and a field store. A loan that stops at the first copy of the closure is
   not a loan.
10. **A closure capturing `self` by reference is a VIEW over the receiver**
    (*deferred-to-MIR*): the callback carries a loan on the receiver; a temporary
    receiver takes rule 29's reject, escaping past the owner rejects, and
    invalidating receiver storage while the callback is live is an ERROR (not
    the warning the name-rebind tracker emits today). Ordinary field mutation
    remains legal. A loan on the whole receiver and one into replaceable
    substorage have different invalidation conditions. Recognizing a callable as a
    view is not the hard part: the predicate MIR must be able to answer is "does
    the receiver outlive this sink", at a field store and at a container insert,
    not only at a return. Two positions are decided and must both hold. A
    `self`-capturing callback stored in a child object BUILT BY THE CONSTRUCTOR
    (`self.button = Button(lambda: self.bump())`) is LEGAL -- an object outlives
    its own constructor, so `self` is not a temporary there and the loan is on
    `self` -- and so is a `self`-capturing callback returned by a method whose
    receiver outlives it. A callback stored in a sink that outlives the receiver
    is an error whose message names the sink and the remedy: keep the receiver
    alive as long as the sink, or hold it as `Rc[...]` and capture the handle.
11. **A closure call checks its effects against live storage dependencies**
    (*deferred-to-MIR*): diagnose a call that may invalidate storage a result or
    another callback still borrows. A later call through the same closure is not
    inherently a conflict, and ordinary mutation through aliases remains legal.
    Apply the check to every callable reaching the call by rule 36.
12. **Non-escape is a property of the callable VALUE, checked at EVERY binding**
    (*split: the conservative marker is contract-half A2; the precise analysis is
    deferred-to-MIR*): forwarding an `Fn` into a storing slot (a `Callable`
    parameter, field, container element or return) is an error at the binding, and
    so is a lambda that CAPTURES an `Fn` parameter and is itself stored. The
    contract half over-rejects an `Fn` value bound to an OPEN type parameter,
    because whether that parameter's slot stores is a body fact of the callee at
    that instantiation; MIR plus a per-instantiation channel is what admits the
    safe forward.
13. **Capture mode is unchanged and nothing is synthesized** (*deferred-to-MIR as
    a constraint on any later fix*) -- `Fn` captures by reference, `Callable` by
    value -- because an adapter that captures by reference is exactly what escapes
    into a `std::function`.

**(d) Provenance is symbolic and compositional.**

14. **A summary is an expression, not a pair of indices** (*deferred-to-MIR*): the
    representation must preserve the result's form, the argument places it roots
    in, its environment dependencies and TEMPORARY IDENTITY, so that in `f(g(b))`
    the fresh result of `g` stays the temporary `f` borrows from instead of
    flattening to an empty loan set. This is the requirement that needs item 1's
    place model: an index pair cannot name a temporary, a field or an element.
15. **Summaries compose by remapping, not re-derivation** (*deferred-to-MIR*):
    `outer(f, x): return inner(f, x)` substitutes the caller's places into
    `inner`'s summary, and one representation serves named defs, lambdas, callable
    aliases and tuple elements. A summary crossing a generic call is rewritten in
    two steps -- a PLACE REMAP (a place naming a callee parameter or capture
    becomes the caller expression bound to it; one naming the callee's own local
    becomes unnameable and rejects under rule 16) and a FORM RESOLUTION (a form
    naming a type parameter resolves against that instantiation's binding) -- so a
    chain of forwarding helpers composes without any hop re-deriving. A GLOBAL root
    is the third remap arm: it is remap-INVARIANT, stays a rule-28 loan, and must
    not fall into the unnameable-root reject, which would be a false reject on
    exactly the shape rule 28 licenses and is not spellable in the caller's
    namespace across a module boundary. A SIZE CAP bounds the remap: a summary over
    a fixed node count degrades to OPAQUE rather than growing, so k forwarding
    helpers stay O(k), never O(k^2). The cap has its OWN diagnostic, never
    recursion's -- crossing it is a WARNING that says the result is forwarded
    through more hops than TPy summarizes and suggests declaring the result
    `Own[U]` -- so a non-recursive chain is never told that it calls itself.
16. **The Rust rule for a lambda / nested `def`** (*deferred-to-MIR*): form and
    roots are the UNION over every return path with each root mapped precisely (a
    parameter to the corresponding argument, a capture to the captured place, a
    call through the lambda's own callable parameter to that parameter's summary),
    and an unnameable root rejects, naming the lambda. Mixed borrow/fresh return
    paths in one body stay an error under this design's one-form restriction
    (the contract half already rejects them); a borrow-or-own representation
    would need a separate design.
17. **Recursion is OPAQUE, never assume-empty** (*deferred-to-MIR*): known-empty,
    known-summary and pending/unknown stay three distinct states, OPAQUE includes
    possible callable-ENVIRONMENT dependencies and not argument ones alone, and a
    dependent borrow with no complete conservative bound is rejected PER
    INSTANTIATION, at the instantiations whose form resolves to BORROW -- so a
    recursive combinator instantiated at a scalar is not rejected with a borrow
    diagnostic for a borrow it cannot have.
18. **A same-module forward call needs a PENDING state** (*deferred-to-MIR*),
    since module topological order does not order intra-module callers above
    callees. The existing pending-fact discipline is the precedent: a fact that is
    not yet computed must be distinguishable from a fact computed to be empty.

**(e) Argument loans and capture loans ADD.**

19. **Argument loans are the FALLBACK for an OPAQUE callee** (*split: the
    conservative blanket loan is contract-half A3; the precise roots are
    deferred-to-MIR*): where no summary exists, a call whose form is BORROW (or
    NEUTRAL bound to a borrow callee) loans EVERY borrow-bearing argument to the
    result, plus the receiver for a directly named `__call__` object; where rules
    15 and 16 DO give a summary, its precise roots are the loan set, so a directly
    bound `lambda x, y: x` loans only `x`. FRESH loans nothing, and the receiver
    clause never reaches an erased `Callable`, whose `__call__` receiver inside a
    `std::function` is not a loanable root (rule 23). Environment loans (rule 9)
    are a SEPARATE set that ADDS to whichever applies and rule 11 reads the union,
    so a closure over `xs` invoked with only scalar arguments still loans `xs`.
    The "result borrows only argument 0" reject fires only at an OPAQUE slot,
    where the blanket loan is all there is.
20. **Capture roots are their own field** (*deferred-to-MIR*): the existing
    parameter-index borrow set keeps its meaning and its tri-state (unanalyzed or
    pending / analyzed and none / non-empty), the summary is a second, separately
    tri-state fact, and CAPTURE roots live in a SEPARATE field rather than as a
    sentinel index. Where a summary names a parameter, the index space is the
    PARAMETER index, never a codegen-side template-parameter counter.
21. **Summaries are body-finalize facts** (*deferred-to-MIR*), never
    registration-time signature derivations (which can stamp only signature facts,
    plus rule 37's annotation), and they propagate by rule 15's substitution
    clause rather than by the verbatim copy that is correct for an index set and
    wrong for a summary.
22. **One authority, computed once and read** (*split: the contract half already
    stamps the result DESCRIPTOR once in sema and copies it onto the IR call node,
    which codegen alone reads; the PROVENANCE summary is deferred-to-MIR*). The
    standing requirement is that no consumer re-derives a fact from a shape: a
    distinction is tagged where it is DECIDED, and codegen reads the IR copy, not
    a parse node.
23. **The erased `Callable`'s contract** (*split: the argument-only conversion
    check is contract-half A1; admitting a non-argument root is deferred-to-MIR*):
    a borrow-returning `Callable` may root its result only in places whose
    lifetime the compiler can prove outlives every call through the stored value.
    Argument roots qualify by construction. A CAPTURE root needs capture-storage
    lifetimes (D3) and is a located error at the CONVERSION until then, naming the
    captured place. A GLOBAL root needs rule 28's loan and rule 35's global-write
    fact; the original form of this rule, which admitted a global root outright,
    is SUPERSEDED by admission rule A5, so until rules 28 and 35 both exist the
    contract half rejects it rather than treating program lifetime as immortality. A `Callable` reached through a field
    or a container element is carried by the same rule: whichever element runs was
    checked at ITS binding, so the loan set is known without resolving which callee
    it is.
25. **Derived property, stated so the next amendment knows it** (*deferred-to-MIR*):
    reassigning an erased callable never invalidates an outstanding result, and
    that holds entirely because rule 23's check is unskippable -- rule 7's
    every-binding scope and rule 16's fail-closed are what carry it.

**(h) Roots.**

28. **A root in a module global is a LOAN on that global** (*deferred-to-MIR*),
    not a certificate of immortality -- program lifetime belongs to the STORAGE,
    not to the value, so an element borrowed out of a global list is invalidated by
    a later append -- and the loan names the global for the mutation-while-borrowed
    rule, for `Fn` and `Callable` alike. It is rule 35's global-write fact, not
    this loan alone, that makes an opaque call inside a helper count as that
    invalidation (`BUGS.md#readonly-method-global-write-under-live-borrow`,
    `BUGS.md#noarg-helper-global-write-under-live-borrow`).
29. **A temporary root rejects, with ONE fallback** (*split: the reject is
    contract-half A6; the precise liveness is deferred-to-MIR*): a receiver or
    argument root that is a temporary at a call whose result outlives the full
    expression is an error rather than a loan, since a loan on a dead temporary is
    not a mutation and the warning channel would never fire -- EXCEPT where the
    contract is FORM-NEUTRAL and ANY candidate root is a temporary, which falls
    back to the FRESH row with the existing copy warning. ANY, not every: which
    argument a `max` hands back is a RUNTIME fact, so a mixed call over one live
    local and one temporary has no static split and must not reject.

**(k) Mutation effects.**

33. **Write-effects are a PLACE SET per closure** (*deferred-to-MIR*): the places
    a closure's body writes, at place granularity, not at the granularity of a
    name and not at the granularity of a parameter index. Name granularity cannot
    express a write THROUGH an alias (`ys = xs; ys.append(...)`), and a
    parameter-index channel records nothing for a capture or a global. The
    granularity is load-bearing: two closures over `self.items` and `self.spare`
    are ONE conflict at name granularity and NONE at place granularity, leaving
    rule 34 nothing to evaluate on. Effects must distinguish ordinary writes
    from operations that may invalidate borrowed storage; a write alone does
    not establish a lifetime conflict.
34. **Capture loans carry a KIND** (*deferred-to-MIR*) -- shared or mutable, read
    off rule 33's place set, so "shared" is derived from ABSENCE from the write set
    -- but this is not Rust's exclusive-mutable-borrow rule. Shared and mutable
    aliases may coexist. A conflict requires an effect that can invalidate
    storage on which a live loan depends; permission weakening in rule 5 is a
    separate contract check. Conflicting effects and loans on one place are
    diagnosed at the call that passes both: `'apply' is passed two callbacks that
    both use 'xs', and 'grow' writes it while 'peek' holds a reference into it;
    pass 'copy(xs)' to one of them, or call them in separate statements.`
35. **Global writes are a per-def PLACE SET, resolved per instantiation**
    (*deferred-to-MIR*): the same place set as rule 33, restricted to global roots,
    propagated transitively over an UNCONDITIONAL per-call callee list -- not over
    an edge set pruned by a parameter-flow predicate, which has nothing to do with
    globals and which drops exactly the two shapes that matter (a `@readonly`
    method that appends to a global, and a no-argument global-writing helper; both
    are live use-after-frees on master today). A BOOLEAN is not sufficient, for
    three reasons: a write to one global would invalidate a loan into an unrelated
    one; the existing mutation-while-borrowed diagnostic has two slots a boolean
    cannot fill; and with an unknown callee taken conservatively TRUE, every body
    that calls a callable parameter becomes a global writer, so a pure callee and a
    mutating one are flagged identically. The fact must therefore resolve PER
    INSTANTIATION at a call through a callable parameter, and an unknown callee
    stays conservatively "writes something".
36. **A call through ANY callable value is a potential mutation of the UNION of
    the write-effects of every callable reaching it** (*deferred-to-MIR*) --
    captures by rule 33, globals by rule 35, arguments by the existing mutated-
    parameter facts. Four properties are required.
    - **Tri-state.** Effects are known, OPAQUE or pending; a union with an OPAQUE
      member is OPAQUE.
    - **Interprocedural capture effects.** The write-effect set of a callable
      passed as an ARGUMENT is remapped into the caller exactly as a result
      summary is (rule 15's place remap), and the call that passes it is a
      potential mutation of those places. Without this, `helper(callback)` -- the
      `Fn`-parameter idiom these rules are written around -- gets no effect at all,
      and a closure writing through `self` is a silent use-after-free.
    - **Any live loan, not only callable-dependent ones.** An OPAQUE effect under
      ANY live loan whose place the union could contain is a reject, per
      instantiation. Scoping it to callable-dependent loans leaves an ordinary
      element or field loan -- the shape of both filed use-after-free entries --
      with no verdict.
    - **A bounded reaching set that closes over local copies.** The callable-valued
      locals whose assignments reach this call within the enclosing body, crossed
      with their capture sets, with transitivity through local-to-local copies
      (`f = grow; g = f; h = g; h(9)` must resolve to `grow`). A set over a fixed
      cap, or one fed by a parameter, a field or a container element, degrades to
      OPAQUE rather than widening the search: there is no interprocedural
      reaching-callables dataflow, which is the size-proportional cliff this rule
      would otherwise be.
    The diagnostic: `calling 'grow' here may modify 'xs', which the reference 'q'
    (from 'get(0)' on line 20) points into; use 'q' before this call, or take
    'copy(q)'.`

## Mutable aliasing

TPy permits multiple mutable references to one object, matching Python; it does
not enforce "aliasing XOR mutability". `docs/IR_DESIGN.md`, under "Interaction
with Ownership Model", states what the borrow checker does focus on instead:
iterator invalidation, element-reference invalidation, pointer invalidation, and
use-after-move for `@nocopy` types. Rules 34 and 36 are INVALIDATION rules in that
list's sense, not exclusivity rules. Two closures that both hold a shared
(reading) loan on one list coexist; the conflict is between a WRITE that can
invalidate storage and a live reference INTO that storage. A requirement that
reads as "two mutable borrows of one place conflict" would reject ordinary,
correct TPy and must not be written.

## Advisory versus enforcement

`docs/IR_DESIGN.md` makes the default MIR loan checker ADVISORY -- warnings, with
a safe opt-in mode escalating a selected subset to hard errors. The callable
subset is an exception, and the exception is what makes the contract half's
fail-closed claim true: every admission failure listed in the contract document,
and every invalidation these rules diagnose for a callable-dependent borrow, is a
hard ERROR in the default mode. A warn-and-continue verdict on these shapes is a
silent use-after-free, which is the class this design exists to close. Warnings
remain right for the copy-vs-alias hedges the language already warns about (the
own-copy channel, the escaping-capture snapshot); they are not right for a loan
this analysis proves is invalidated.

## Programs each mechanism must admit or reject

Each program below is drawn from the adversarial corpus that produced these rules.
The shared prelude, omitted from every listing, is
`class Node: v: int32` with the obvious `__init__`, and the imports. "Contract
half" names the row in `docs/CALLABLE_CONTRACT_DESIGN.md` under which the shape is
refused today, where it is refused.

### Environment loans (rules 9, 11, 34)

**E1 -- MUST REJECT.** Two closures over one list, one reading through the result,
one growing it. Silent use-after-free today. Contract half: A1 (capture root).

```python
def run(get: Fn[[int32], Node], grow: Fn[[int32], None]) -> int32:
    q = get(0)
    grow(9)
    return q.v

def main() -> None:
    xs = [Node(1)]
    print(run(lambda i: xs[i], lambda k: xs.append(Node(k))))
```

**E2 -- MUST ADMIT.** Two READING closures over one list: two shared loans on one
place coexist (rule 34's matrix). Compiles and matches CPython today.

```python
def apply2(a: Fn[[], int32], b: Fn[[], int32]) -> int32:
    return a() + b()

def main() -> None:
    xs = [Node(3), Node(4)]
    print(apply2(lambda: xs[0].v, lambda: xs[1].v))
```

**E3 -- MUST ADMIT (loan follows the inner closure).** A closure whose capture is
itself a closure over `xs`: the loan on `xs` must reach the outer callable.
Contract half: A1.

```python
def call2(f: Fn[[int32], Node]) -> int32:
    return f(0).v

def main() -> None:
    xs = [Node(1)]
    inner = lambda i: xs[i]
    outer = lambda i: inner(i)
    print(call2(outer))
```

**E4 -- MUST REJECT.** The write reaches the captured list through an ALIAS, which
name-level effects cannot see (rule 33's granularity argument).

```python
def main() -> None:
    xs = [Node(1)]
    def grow(k: int32) -> None:
        ys = xs
        ys.append(Node(k))
    print(pair(lambda i: xs[i], grow))   # pair() is E1's run()
```

### `self` as a view (rule 10)

**S1 -- MUST REJECT.** A `self`-capturing callback out of a TEMPORARY receiver:
reads freed storage today, printing `0` where CPython prints `7`. Contract half:
A2 and A6.

```python
class Holder:
    def make(self) -> Callable[[], int32]:
        return lambda: self.v

def get() -> Callable[[], int32]:
    h = Holder(7)
    return h.make()
```

**S2 -- MUST REJECT.** The self-view is stored in a sink that outlives the
receiver. Contract half: A2.

```python
def main() -> None:
    sink = Sink()              # outlives h
    if True:
        h = Holder(7)
        sink.cb = h.make()     # h dies at scope end
    print(sink.cb())
```

**S3 -- MUST ADMIT.** The widget idiom: the parent owns the child it hands a
callback to, and an object outlives its own constructor. Contract half: A12
rejects it, and this is the single most important admission rule 10 buys.

```python
class Panel:
    def __init__(self):
        self.count = 0
        self.button = Button(lambda: self.bump())

    def bump(self) -> None:
        self.count += 1
```

**S4 -- MUST REJECT.** The observer in the direction where the OBSERVED outlives
the listener: the bus keeps a callback borrowing a dead `Logger`.

```python
def main() -> None:
    bus = Bus()
    scope_inner(bus)           # attaches a self-capturing lambda, then returns
    bus.emit(Event(3))

def scope_inner(bus: Bus) -> None:
    lg = Logger()
    bus.listeners.append(lambda e: lg.handle(e))
```

### Non-escape of `Fn` values (rules 12, 13)

**N1 -- MUST REJECT.** A returned closure CAPTURES an `Fn` parameter, so the `Fn`'s
by-reference captures are copied into a `std::function` and outlive them. Compiles
today. Contract half: the `Fn`-into-a-storing-slot reject row.

```python
def make(f: Fn[[int32], int32]) -> Callable[[], int32]:
    return lambda: f(0)
```

**N2 -- MUST REJECT.** The same hazard through a channel the syntactic rule does
not name: the `Fn` parameter is captured by a closure appended to a caller's list.

```python
def hold(g: Fn[[int32], int32], out: list[Callable[[], int32]]) -> None:
    out.append(lambda: g(0))
```

### Symbolic summaries and composition (rules 14-16, 18, 21)

**C1 -- MUST REJECT (the clear, not the call).** The loan on `xs` must survive TWO
generic hops, so that clearing the list while the result is live is diagnosed.

```python
def inner[T, U](f: Fn[[T], U], x: T) -> U: return f(x)
def outer[T, U](f: Fn[[T], U], x: T) -> U: return inner(f, x)

def main() -> None:
    xs = [Node(1), Node(2)]
    q = outer(same, xs[0])
    xs.clear()
    print(q.v)
```

**C2 -- MUST REJECT or degrade loudly.** Three hops. Either the summary composes
(and the `clear` is diagnosed) or the size cap fires with its OWN warning, never
recursion's message.

```python
def h3[T, U](f: Fn[[T], U], x: T) -> U: return f(x)
def h2[T, U](f: Fn[[T], U], x: T) -> U: return h3(f, x)
def h1[T, U](f: Fn[[T], U], x: T) -> U: return h2(f, x)
```

**C3 -- MUST ADMIT, both legs, with the borrow leg ALIASING.** One generic helper
with a borrow-returning callee and a fresh-returning one. `got.v = 8` must be
visible in `t.left.v`.

```python
def apply[T, U](f: Fn[[T], U], x: T) -> U:
    return f(x)

def main() -> None:
    t = Tree(3)
    got = apply(lambda n: n.left, t)
    got.v = 8
    print(got.v, t.left.v)
    made = apply(lambda i: Leaf(i), 5)
```

### Recursion (rule 17)

**R1 -- MUST REJECT at a BORROW instantiation, MUST ADMIT at a scalar one.** A
recursive combinator passing its own callable parameter to itself.

```python
def rec[T, U](f: Fn[[T], U], x: T, n: int32) -> U:
    if n <= 0:
        return f(x)
    return rec(f, x, n - 1)

def main() -> None:
    xs = [Node(1), Node(2)]
    q = rec(same, xs[0], 2)     # U = Node: OPAQUE, reject
    xs.clear()
```

### Argument loans (rules 19, 20)

**A1 -- MUST ADMIT with a PRECISE loan set.** A directly bound `lambda x, y: x`
loans only `x`, so a short-lived second argument is not a reason to reject.
Contract half: the blanket loan makes this the "borrows only argument 0" reject.

```python
def pick(f: Fn[[Node, Node], Node], a: Node, b: Node) -> Node:
    return f(a, b)

def main() -> None:
    keep = Node(1)
    q = pick(lambda x, y: x, keep, Node(2))
    print(q.v)
```

**A2 -- MUST REJECT.** A capture-rooted result under an `Fn` slot, with the capture
mutated while the result is live: the environment loan and the result loan are two
facts and both are needed.

```python
def ro(f: Fn[[Node], Node], a: Node) -> Node:
    return f(a)

def main() -> None:
    xs = [Node(10)]
    r = ro(lambda n: xs[0], Node(1))
    xs.append(Node(20))
    r.v += 1
```

### The erased `Callable` (rules 22-23, 25)

**K1 -- MUST ADMIT once capture lifetimes exist (D3); MUST REJECT until then.** The
memoizing accessor whose result roots in a captured table. Contract half: the
stored-`Callable` capture-root reject row.

```python
def main() -> None:
    cache: dict[int32, Leaf] = {}
    cache[1] = Leaf(10)
    get2: Callable[[Tree], Leaf] = lambda n: cache[n.key]
    got = get2(Tree(1))
    got.v = 99
    print(got.v, cache[1].v)     # must print 99 99
```

**K2 -- MUST REJECT until globals are tracked (rules 28, 35).** A stored `Callable`
whose result roots in a global that its own body grows on the next call. Contract
half: A5.

```python
pool: list[Node] = []

def first(n: Node) -> Node:
    pool.append(Node(n.v))
    return pool[0]

def main() -> None:
    cb: Callable[[Node], Node] = first
    q = cb(Node(1)); cb(Node(1)); cb(Node(1))
    print(q.v)
```

### Globals (rules 28, 35)

**G1 -- MUST REJECT.** A `@readonly` METHOD appending to a module global while a
borrow into it is live. Silent use-after-free today
(`BUGS.md#readonly-method-global-write-under-live-borrow`).

```python
pool: list[Node] = [Node(1)]

class Lg:
    @readonly
    def log(self, k: int32) -> None:
        pool.append(Node(k))

def main() -> None:
    q = pool[0]; Lg().log(9); print(q.v)
```

**G2 -- MUST REJECT.** The same hole through the other gate: a NO-ARGUMENT helper
(`BUGS.md#noarg-helper-global-write-under-live-borrow`).

```python
pool: list[Node] = [Node(1)]

def grow() -> None:
    pool.append(Node(9))

def main() -> None:
    q = pool[0]; grow(); print(q.v)
```

**G3 -- MUST distinguish the two instantiations.** One body, two callees: the pure
one must compile clean, the mutating one must be diagnosed. A boolean global-write
fact flags both identically (rule 35).

```python
def use(f: Fn[[int32], None]) -> int32:
    q = pool[0]
    f(9)
    return q.v

def main() -> None:
    print(use(pure))
    print(use(grow))
```

### Temporaries (rule 29)

**T1 -- MUST REJECT.** A `__call__` object that is a temporary at the call, whose
result borrows the receiver.

```python
def pick(f: Fn[[Node], Node], a: Node) -> Node:
    return f(a)

def main() -> None:
    q = pick(Sel(), Node(1))     # Sel().__call__ returns self.held
    print(q.v)
```

**T2 -- MUST ADMIT with the warned copy.** A MIXED call, one live local and one
temporary: which argument wins is a runtime fact, so the fallback fires on ANY
temporary root rather than rejecting. Compiles and matches CPython today.

```python
def main() -> None:
    a = Node(1)
    best = max(a, Node(5), key=lambda n: n.v)
    print(best.v)
```

### Effects reaching a call (rule 36)

**F1 -- MUST REJECT.** The effect must cross an ordinary named call: `apply(push)`
is the `helper(callback)` idiom, and `push` writes through `self`. Silent
use-after-free today.

```python
class Store:
    def run(self) -> None:
        q = self.items[0]
        def push(k: int32) -> None:
            self.items.append(Node(k))
        apply(push)              # apply(f) calls f(9)
        print(q.v)
```

**F2 -- MUST REJECT.** The effect reaches the call through a FIELD-held `Callable`,
under an ORDINARY element loan on a global list. Silent use-after-free today.

```python
def main() -> None:
    bus = Bus(grow)              # grow appends to the global pool
    q = pool[0]
    def fire(k: int32) -> None:
        bus.cb(k)
    apply(fire)
    print(q.v)
```

**F3 -- MUST REJECT.** The callable reaches the call through a chain of local
copies: the reaching set must close over `f = grow; g = f; h = g`.

```python
def main() -> None:
    f: Callable[[int32], None] = grow
    g = f
    h = g
    q = pool[0]
    h(9)
    print(q.v)
```

**F4 -- MUST REJECT.** The callable reaches the call through a PROJECTION
(`fs[0]`), not a name.

```python
def main() -> None:
    fs: list[Callable[[int32], None]] = [grow]
    q = pool[0]
    fs[0](9)
    print(q.v)
```

**F5 -- MUST ADMIT.** A harmless scalar callback runs while an ordinary element
reference is live. This shape emits C++ today. Contract half: A4 rejects it
because the callback's effects are unknown, even though its result borrows
nothing. This is a safe false positive, not a lifetime violation.

```python
def use(f: Fn[[], int32], xs: list[Node]) -> int32:
    q = xs[0]
    value = f()
    return q.v + value

def main() -> None:
    xs = [Node(7)]
    print(use(lambda: 3, xs))
```

The effect facts at this instantiation must establish that the callback cannot
invalidate `xs`. The presence of a loan alone is not a reason to reject it.

## Cases to add when the contract half lands

If the compatibility gate approves the contract-first rollout, commit the corpus
as located-reject cases as soon as the contract half rejects
them, each with its intended MIR acceptance recorded in the case header, and
promote them individually as the proof arrives. A safe program pinned as an
`error_` case records an interim limitation, not a permanent rejection requirement.
Keep it distinct from a lifetime violation that must remain rejected. Every
suggested diagnostic remedy also needs a compiling acceptance case under the same
admission rules. Reference-type HAPPY cases must
mutate through the boundary and observe the change: a read-only happy case is
parity-blind and will match CPython even if TPy silently copied.

| Case | Lands as | Intended MIR verdict |
|------|----------|----------------------|
| E1, E4 | `error_` (A1 / A4) | reject, permanently -- the diagnostic text is what changes |
| E2 | happy, exec + cpy | admit; two shared loans coexist (rule 34) |
| E3 | `error_` (A1) | ADMIT under rules 9 and 16 |
| S1, S2, S4 | `error_` (A2 / A6) | reject, permanently (rule 10) |
| S3 | `error_` (A12) | ADMIT under rule 10's constructor clause |
| N1, N2 | `error_` (rule 12's row) | reject, permanently |
| C1, C2 | `error_` (A3) | reject the invalidating statement, with rule 15's precise roots or the size-cap warning |
| C3 | happy, exec + cpy, mutating through `got` | admit both legs; the borrow leg must alias |
| R1 | `error_` (A3 / A4) | reject at the borrow instantiation only (rule 17) |
| A1 | `error_` ("borrows only argument 0") | ADMIT with a precise loan set (rule 19) |
| A2 | `error_` (A1) | reject, with rule 9's environment loan naming `xs` |
| K1 | `error_` (stored-`Callable` capture root) | ADMIT under D3 |
| K2, G1, G2 | `error_` (A5 / A4) | reject the invalidating call (rules 28, 35) |
| G3 | `error_` (A4) at both call sites | admit `use(pure)`, reject `use(grow)` (rule 35, per instantiation) |
| T1 | `error_` (A6) | reject, permanently (rule 29) |
| T2 | happy, exec + cpy | admit, warned copy (rule 29's fallback) |
| F1, F2, F3, F4 | `error_` (A4) | reject, with rule 36's precise place set and reaching relation |
| F5 | `error_` (A4), safe false positive | ADMIT when the callback's effects are known not to invalidate the element loan |

## Residue

Three known gaps that none of the rules above closes.

- **The "borrows only argument 0" spelling gap.** A multi-argument borrow contract
  says the result may refer to EITHER argument. A callee that borrows only the
  first has no way to say so, so a caller passing a short-lived value in the second
  position is rejected even though the program is correct (program A1 above).
  Rule 19's precise roots fix the INFERRED case; the DECLARED case -- an `Fn`
  contract in a signature -- needs a per-argument loan marker in the spelling,
  which is a language-surface change and is not designed. Until then the reject row
  and a `docs/LANGUAGE_FEATURES.md` note are the whole answer.
- **Native results borrowing C++ static storage.** A `@native` accessor over a
  registry singleton (`ns::get_current()`) has no parameter to name and no TPy
  module global to loan, so `@native_borrow(returns=(...))` cannot express it and
  `-> Own[R]` changes the semantics to the copy that silently happens today.
  Singleton and registry accessors are a standard native binding shape, so this
  needs either a `returns=()` form with a stated admissibility rule or a genuine
  static-storage root; neither is designed.
- **Loop-variable capture.** A closure capturing a loop variable snapshots it per
  iteration, so a list of such closures prints `0 1 2` where CPython prints
  `2 2 2` (late binding). Rule 13 keeps the by-value snapshot, so nothing here
  changes it, and the existing parity row covers only "a mutable local reassigned
  later". It is a separate divergence, pre-existing and unrelated to provenance.

## Review history

The rules in both documents are written as target state. The design passed
through three rounds of adversarial review (Rust borrow-checker, C++
implementer, Python-programmer, compiler-architecture and breaker
perspectives) and an implementer dry run before the split; every finding
became a rule, an explicit reject row, a named deferral, or a BUGS.md entry.
The per-finding disposition table is recorded in the commit that introduced
the two documents.
