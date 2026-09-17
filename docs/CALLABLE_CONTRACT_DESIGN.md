# Callable Contract

This document owns rules 1-8, 24, 26, 27, 30-32 and 37 of the shared callable-rule
numbering and the admission layer A1-A12; `docs/CALLABLE_PROVENANCE_REQUIREMENTS.md`
owns 9-23, 25, 28, 29 and 33-36. A rule number never moves between the two.

## Purpose

This document describes the result-contract half of the callable result design:
what a callable's RESULT means, how it is spelled, which bindings are legal, and what the compiler
emits for each. A callable's result carries a FORM -- does the call hand back a
borrow of storage the caller can name, or a fresh value the caller owns? -- and
that fact is read off the spelled return type by exactly the rule a `def` return
already obeys. The contract is decidable from types and declarations. Shipping it
before MIR also requires a conservative admission check; whether that check can
remain syntactically bounded, without substantial new flow analysis, is a gate
on that sequencing (see "Compatibility gate"). The approved sequence now brings
analysis-only MIR first; A1-A12 remain a proposed interim policy, not rules to
ship ahead of analysis. Without a result contract, a call
through a callable value reads as an rvalue at every owning slot, so an `Fn`
bound to a borrow-returning function
copies where CPython aliases, silently
(`BUGS.md#callable-value-borrow-return-copies-unwarned`).

What it deliberately does NOT decide is PROVENANCE: which of the caller's places
a particular borrow roots in, how those roots survive aliases, helpers and
captures, and which later operation invalidates them. Answering that needs stable
place identities, a CFG, liveness and loan propagation -- the analysis-only MIR.
Until that exists this half stands on a conservative ADMISSION LAYER (section
"The admission layer") that admits a borrow-returning binding only when every
root is proved to be an admissible argument of that call, and rejects everything
else with a located error. These changes reject some programs accepted today:
some currently miscompile or permit dangling references, while others are safe
programs rejected conservatively until provenance and effect analysis can
distinguish them. Migration must measure both groups. The companion document
`docs/CALLABLE_PROVENANCE_REQUIREMENTS.md` states, as requirements on the
analysis-only MIR, what has to be true before each restriction lifts; its rule
numbers and this document's are one numbering, inherited from section VII of
`docs/CLOSURES_CALLABLE_DESIGN.md` and kept as stable identifiers.

**File and line references.** This is the ONE document in the pair that carries
them: it describes work to be done against the tree as it stands, so the
citations are part of the instruction. The requirements document carries none by
design -- line-number choreography rots, semantic acceptance criteria do not.

## Language rules and implementation limits

Callable return annotations follow the existing `def` convention. This is a
language rule, including its ownership distinction beyond ordinary Python typing.
Restrictions on capture-rooted results, borrowed environments, global roots and
calls with unknown effects are temporary implementation limitations. Programs
rejected by these restrictions may be valid Python and safe under TPy's intended
semantics. A missing proof is not evidence that a program dangles.

The reject tables classify each row by the reason for rejection:

| Kind | Meaning | Examples |
|------|---------|----------|
| Contract | A declared TPy contract must be respected | Borrow/fresh mismatch; `Fn` non-escape; copying a borrow into inline container storage |
| Lifetime | Storage does not outlive its use | Escaping temporary root; storage invalidation before a borrow's last use |
| Analysis | Required provenance or effects cannot yet be proved; the program may be safe or unsafe | Borrowed environments, unknown callback effects, unsupported helper composition |
| Representation | Supporting the shape needs a separate representation or language decision | Mixed borrow/fresh results; reference-typed optional callable results |

The current one-form rule still rejects mixed borrow/fresh return paths. This is
a chosen restriction of this design, not a claim that such results are inherently
unsafe; a borrow-or-own representation would need a separate design. Likewise,
an analysis restriction is not a permanent language rule merely because this
implementation rejects it.

## The `def` rule, applied to every callable spelling

One rule reads the result form off a spelled return type, and it is the rule a
`def` return already obeys:

- a bare reference type `R` is a **BORROW** (`R&` in C++);
- `Own[R]` is **FRESH** (a value the caller owns);
- a type parameter `U` is **FORM-NEUTRAL** -- resolved per instantiation exactly
  as a generic def's `-> U` is.

`readonly[R]` and `Ptr[R]` are BORROW with the permission dimension of rule 2; a
contract's return goes through the same `make_ref` (`tpyc/typesys.py:2308`) a
def's return does. "Follows the callee" survives only at a type-parameter
return: no monomorphic spelling follows what it is bound to, and erasure into
`Callable` does not change the form.

Shared declarations for the table: `class Node: v: int32`, plus
`def same(c: Node) -> Node: return c` and
`def mk(c: Node) -> Own[Node]: return Node(c.v)`.

| # | Spelling | Form | Verdict |
|---|----------|------|---------|
| 1 | `def same(c: Node) -> Node: return c` | BORROW | ok -- the result aliases `c` |
| 2 | `def mk(c: Node) -> Own[Node]: return Node(c.v)` | FRESH | ok -- the caller owns the result |
| 3 | `def apply[T, U](f: Fn[[T], U], x: T) -> U: return f(x)` | FORM-NEUTRAL | ok -- form per instantiation, `apply(same, n)` and `apply(mk, n)` both compile |
| 4 | `def bor(f: Fn[[Node], Node], a: Node) -> Node: return f(a)` + `bor(same, a)` | BORROW | ok -- `Node&` render, result loans `a` |
| 5 | `bor(mk, a)` | BORROW contract, FRESH callee | **error**: `'mk' returns Own[Node], but the slot's Fn[[Node], Node] contract returns a borrow; declare Fn[[Node], Own[Node]]` |
| 6 | `def own(f: Fn[[Node], Own[Node]], a: Node) -> Own[Node]: return f(a)` + `own(mk, a)` | FRESH | ok -- nothing is loaned |
| 7 | `own(same, a)` | FRESH contract, BORROW callee | **error**: `'same' hands back a reference to its argument, but 'Fn[[Node], Own[Node]]' promises a fresh value; declare 'Fn[[Node], Node]' to pass the reference through, or wrap it as 'lambda n: copy(same(n))' -- copy() duplicates, so mutations through the result stop reaching the argument.` |
| 8 | `g: Callable[[int32], Node] = lambda i: Node(i)` | BORROW contract, FRESH body | **error**: `Callable[[int32], Node] says the callback hands back a borrow, but 'lambda i: Node(i)' builds a new Node on every call. The program is otherwise correct: spell Callable[[int32], Own[Node]].` |

Rows 5 and 7 are the two mismatch directions, and rule 5 scopes them to a callee
with its OWN declared form -- a named `def`, a function reference, an annotated
slot. A bare LAMBDA declares no form, so rule 32 gives it the contract's return
as its annotation instead of either error. This is contextual typing, just as
`b: Box[int32] = Box(1)` supplies missing constructor type arguments. A lambda
at `Callable[[Node], Own[Node]]` is checked like `def f(n: Node) -> Own[Node]`:
returning `n` takes the existing warned-copy path. A named function already has
a contract and cannot have its body re-inferred at the destination.

Row 8 is the def rule cashed at a lambda, and the one place the lambda path
CHANGES: a lambda body today runs `check_view_return_dangle`
(`tpyc/sema/compatibility.py:3009`), which skips the non-value-reference rule for
value-return contexts (rationale at `:3011-3016`) and is shared with the yield
path (`tpyc/sema/statements.py:3968`). The lambda arms
(`tpyc/sema/expressions.py:4196`, `:4212`) switch to `check_dangling_reference`
(`:3027`), lambda-only, and the callable-framed text replaces the def factory's
`Cannot return local or temporary as reference` (`:3134`, `:3165`).

In this half the switch has NO capture exception: a capture root at an `Fn` slot
is an environment loan the compiler cannot yet model, so it rejects under A1
rather than compiling. The exception returns with rule 9.

## Invariant

> Each fact has one authority. The position-aware type classifier determines
> result form, permissions and possible contained borrows. Body analysis
> determines actual environment loans, result roots and mutation effects.
> THIR carries the resolved decisions; code generation consumes them.

Collapsing any two is where the design fails: a scalar-returning callback with a
borrowed `self` capture can have an environment lifetime dependency without a
borrowed result, and a call that invalidates captured storage can invalidate a
result whose root is otherwise still live. Types alone cannot determine either
fact. This half supplies the type descriptor and only the conservative body
facts justified by its admission layer. Precise loans, roots and effects are
requirements on MIR; unresolved obligations reject subject to the compatibility
gate below. Ordinary mutation and overlapping mutable aliases remain legal
unless they invalidate storage a live loan depends on.

## Rules

One sentence each, grouped, each carrying its status. Every rule holds at every
position -- free function, method, constructor, module-level statement,
comprehension, closure, context-manager body, `try`/`finally`, `@error_return`
body, `match` arm.

**(a) Form: one classifier.**

The descriptor below is the target contract, not the representation reader
extracted in checkpoint 2. That checkpoint preserves existing decisions;
permission and contained-borrow analysis are designed in checkpoint 3 and
implemented with the coupled contract in checkpoint 4.

1. **Form spelling, the def rule, uniformly** (*implemented-now*): in
   `Fn[[A...], R]`, `Callable[[A...], R]` and `def ... -> R` alike the form is
   read off `R` as above, and erasure into `Callable` does not change it.
2. **A result descriptor is three facts** (*implemented-now for the type half;
   the provenance half is restricted-until-MIR, requirements rule 2*) -- outer TRANSFER FORM (borrow /
   fresh / neutral), ACCESS PERMISSION (mutable / readonly / symbolic) and
   CONTAINED BORROWS (whether the payload can carry loans at all: tuple elements,
   views, pointers, records, closures) -- so an owned record holding a `Span` is
   FRESH and still borrowing. A type says WHERE borrows may be contained; it
   cannot say WHICH caller's place this value borrows, so in this half a payload
   that can contain borrows is refused at a callable result (A7) rather than
   carried.
3. **One position-aware classifier** (*implemented-now*) computes the descriptor
   and drives both the check and the render, with `call_returns_cpp_ref`
   (`tpyc/value_category.py:127`) and `async_return_form` (`:55`, hand-kept in
   lockstep with it) as its CONSUMERS rather than siblings -- pointer-vs-reference
   is a render distinction, not a form. The descriptor and its reader live in
   `tpyc/typesys.py`, not `value_category`, which imports FROM typesys
   (`tpyc/value_category.py:18`) and cannot be imported back: parked there, the
   enum would force `CallableType._std_function_sig` (`tpyc/typesys.py:4529`,
   rule 24's render) to decide form a second time.
4. **Formless payloads are the ones with no storage** (*implemented-now*)
   (scalars, `bool`, `char`, tuples of those): `str`, `bytes` and the views keep
   the def-return convention at their position rather than being reclassified
   here, and a tuple is not formless by value-typedness
   (`TupleType.is_value_type()` is unconditionally True, `tpyc/typesys.py:3804`)
   but takes its form per element.

**(b) Conversion legality, keyed on the source, at every binding.**

5. **No adaptation, for a callee with a DECLARED form** (*implemented-now,
   mandatory in sema*): a named `def`, a function reference or an annotated slot
   binds only if its descriptor satisfies the contract's -- FRESH to FRESH,
   BORROW to BORROW, either to NEUTRAL, permission WEAKENING only (a mutable
   borrow satisfies a readonly contract, not the reverse) -- and a mismatch is a
   located error with the two remedies of rows 5 and 7, never a wrapper, an
   adapter or a materialized copy. A bare LAMBDA declares no form and is not
   judged here (rule 32). Where the payload is `@nocopy` or has a `__del__` the
   `copy()` remedy is WITHHELD and the message names the real fix instead --
   return a scalar field, or hold the payload as `Rc[...]` and hand back a
   `.clone()`, which keeps the result shared rather than duplicated.
6. **The error is keyed on the SOURCE and fires AFTER overload selection**
   (*implemented-now*) -- a lambda takes the lambda stamp, a function reference
   takes `function_ref_info` (`tpyc/parse/nodes.py:232`, the channel
   `tpyc/sema/compatibility.py:468` already reads), an annotated local or field
   its declared slot type, a `__call__` object the overload the conformance loop
   matched -- and for a form mismatch on a user `def` it never degrades into
   "'mk' is not a variable". Builtin and bound-method references (`key=len`,
   `key=str.lower`, `obj.method`) keep their current, non-conforming messages:
   out of scope here.
7. **Every binding is checked** (*implemented-now*) -- argument coercion,
   assignment, reassignment, field initializer, container element, and an
   `Optional`-peeled `Callable[...] | None` slot whose peel must carry the RETURN
   type and not only the argument shape -- with an implicit erasure checked at
   each binding and joined conservatively, a join that cannot name one form
   rejecting.
8. **The check cannot live where the qualifiers are stripped** (*implemented-now*):
   SIX sites erase the form and must agree rather than be replaced --
   `_check_compat`'s callable arm (`tpyc/sema/compatibility.py:620-628`),
   `build_concrete_callable` (`tpyc/sema/expressions.py:4291`, `:4300`, which
   strips `Own` but PRESERVES `Ref` by design, `:4299-4303`, so it erases the
   FRESH half only), `_concrete_fn_type` (`:4278`),
   `_match_function_to_hint_data` accepting `Own[Node]` against a bare `Node`
   hint (`:4364-4374`), `_classify_strict_match`'s `_strip`
   (`tpyc/sema/overloads.py:235`, `:252-269`) and `_structural_match` (`:102`),
   whose callable arm (`:118-121`) strips `Own`/`Ref` off both sides of the
   callable return and whose readonly arm (`:113-114`) erases the permission
   dimension rule 5 weakens along. The check joins at the ARGUMENT-COERCE SITE
   with the source in hand;
   `_callable_signature_satisfies` (`compatibility.py:2472`) is no chokepoint,
   reached only from the `__call__` branch.

**(f) The erased `Callable`.**

24. **The erased borrow renders PER FORM** (*implemented-now, mandatory in sema*)
    -- `std::function<B&(A)>` for a bare `R`, `std::function<const B&(A)>` for
    `readonly[R]`, `std::function<B*(A)>` for `Ptr[R]` -- replacing the bare
    `return_type.to_cpp()` of `_std_function_sig` (`tpyc/typesys.py:4529`);
    `ReadonlyType.to_cpp()` hands back the WRAPPED type (`tpyc/typesys.py:1749`),
    so one flat `B&` spelling renders `Node&` for a `readonly[Node]` contract and
    rejects the very `const Node&` callee it is for. Two payloads fall outside
    those rows. A **`str` / `bytes` / view payload keeps rule 4's def-return
    convention, the OWNED spelling**: `def f(n: Node) -> str` and
    `-> readonly[str]` both render `std::string f(const Node&)` (measured), so
    `Callable[[Node], readonly[str]]` erases to
    `std::function<std::string(Node&)>` and not to the view row -- which also
    keeps out the trap that row hides, since
    `std::function<std::string_view(Node&)>` silently ACCEPTS an
    owned-`std::string` callee and dangles (measured). A **`Ptr[R] | None`
    contract gets its own row,** `std::function<std::optional<Node*>(A)>`, since
    `std::function<Node*(Node&)>` REJECTS an `optional<Node*>` callee (measured);
    it is distinct from the reference-typed `Optional` result, which stays a
    reject. **Sema's rule-5 check is the ONLY backstop:** the `const B&` row
    ADMITS a fresh lambda on `clang++-18`, `clang++-19` and `g++-12` (measured
    below), rejecting only on `g++-13`/`14` and `clang++-20` -- and clang 19 is
    the project minimum.

**(g) Neutral results.**

26. **A neutral result binds to a LOCAL through a slot WRAPPER TYPE with `.get()`**
    (*implemented-now for the representation; the lifetime of what the slot holds
    is restricted-until-MIR, requirements rule 26*) -- identity for the fresh row, deref for the borrow row
    -- so the borrow-vs-payload distinction lives in the TYPE and ONE emitted body
    serves every instantiation; `to_result_slot` keys on the EXPRESSION's
    lvalue-reference-ness (the same key the slot type uses, so a `Ptr[R]` payload
    prvalue is not mistaken for a borrow). Four spellings are fixed. (i) The
    enclosing signature is **`decltype(auto)`**, not `__R0`, a body-local alias
    declared AFTER the signature; the only alternative is a trailing return
    re-spelling the call, and both deduce `Node&` / `Node` correctly (measured).
    (ii) `.get()` carries a **`const&` overload on BOTH rows** (`T& get() const&`
    on the borrow row, `const T& get() const&` on the fresh row) -- the borrow row
    stays mutable through it by construction, and without them a slot read through
    a `const Frame&` or a non-mutable `operator()` is a hard unlocated error.
    (iii) A local REBOUND from two callables of differing FORM is a located sema
    error rather than the slot's `static_assert`, resolved PER INSTANTIATION as
    rule 27 is: `q = f(a); q = g(a)` over two neutral parameters is legal at every
    same-form instantiation and rejects only at a mixed one, and a neutral result
    from TWO callables in one expression (`return f(a) if c else g(a)`) takes the
    same rule -- same form on both arms, the deduced type is that form, otherwise
    a located reject naming both calls. (iv) A rebind also needs the PAYLOAD
    move-assignable: `is_move_assignable_v` is false for a payload with a const
    member or no move-assign (measured) and `q = to_result_slot(...)` then fails
    unlocated, so that is a located sema reject whose remedy is a second slot
    local. In a resumable frame the slot lives INSIDE a `frame_slot`
    (`runtime/cpp/include/tpy/frame_slot.hpp:68`), never bare: the fresh row is
    default-constructible only if its payload is. A CONCRETE borrow contract does
    NOT use the slot: `Fn[[Node], Node]` binds `Node& q = f(a);` bare (decision
    13).
27. **A neutral result stored into an OWNING slot rejects PER INSTANTIATION**
    (*implemented-now*) -- container insert, `Own[...]` parameter, `Own[...]`
    return, field -- at the instantiations whose form resolves to BORROW, never on
    a body whose result type cannot be instantiated at a reference type at all, so
    an all-scalar instantiation (`K = int32`) and a body every caller instantiates
    with a FRESH callee both stay legal; the error is located at the CALL SITE
    that chose the borrow callee and cross-references the storing line, and D1 is
    what admits it.

**(i) Overload and `@dispatch`.**

30. **An exact form match outranks a form-neutral one** (*implemented-now*), so a
    pair differing only in result form stays resolvable in sema -- the form must
    enter `_classify_strict_match` (`tpyc/sema/overloads.py:235`) AND the
    `__call__` conformance loop (`tpyc/sema/compatibility.py:996`, which returns
    on FIRST match, comment at `:992`), never selecting on signature and erroring
    on form afterwards while a later overload would have conformed.
31. **The `requires` clause is spelled PER FORM with a borrow-category check
    beside the payload check** (*implemented-now, mandatory in sema*) --
    `requires std::is_lvalue_reference_v<decltype(...)>` for a borrow row,
    `requires !std::is_reference_v<decltype(...)>` for BOTH the fresh row of a
    `@dispatch` pair (without which the pair is ambiguous in C++) and the `Ptr`
    row (without which a `Node*&` callee gives a `Node**` slot and an unlocated
    deref error), and with the PAYLOAD clause -- not the category one -- carrying
    the `std::optional<Node>&` rejection. The clause backstops **FORM ONLY**: C++
    has no provenance backstop, and three dangling callees (a reference into a
    BY-VALUE parameter, a function-local laundered through a member accessor, a
    `Node&` out of a ternary with a non-argument arm) satisfy every row, measured,
    with no warning from any toolchain. Provenance is sema's -- which is why a
    `@native` / `@cpp_template` borrow-returning callee carrying no rule-37
    annotation is OPAQUE at a borrow contract and rejected.

**(j) Stub returns.**

32. **Stub returns take the same rule, and a stub's USES of a callable result bind
    it too** (*implemented-now*): `min`/`max`'s own `-> T`
    (`lib/tpy/tpy/_builtins/_funcs.py:125`, `:174`) is FORM-NEUTRAL, so
    `best = max(a, b, key=...)` binds through rule 26's slot instead of copying
    the winner into a value local as it does today. And because the C++ key
    helpers STORE the key result -- `builtin_sorted_key`'s
    `decorated.emplace_back(key(items[i]), i)` over `using K = std::decay_t<...>`
    (`runtime/cpp/include/tpy/builtins.hpp:304`, `:300`) and `min_key` / `max_key`'s
    `auto ka = key(a), kb = key(b)` (`:320`, `:335`) -- that is rule 27's shape
    performed in C++, so those contracts SPELL the copy: `key: Fn[[T], Own[K]]`.
    **A LAMBDA at such a contract is not a mismatch:** having no declared form, it
    takes the contract's return as its RETURN ANNOTATION and runs the existing
    warned own-copy path -- what the named twin
    `def key(n) -> Own[Child]: return n.child` does today (measured: `warning:
    copies Child into owned storage; use copy() to make this explicit`, and the
    program runs). So `key=lambda it: it.child` keeps compiling, warned, and
    `key=lambda n: n.name` over a `str` key costs nothing at all, a `str` return
    being an owned `std::string` already (rule 4, measured). Only a `@nocopy` or
    `__del__` key payload has no copy to warn about, and there the error names the
    real fix -- key on a scalar field, `key=lambda n: n.tag.w`.

**(l) Stub provenance.**

37. **`@native_borrow(returns=(...))` states what a bodyless stub's result
    borrows** (*implemented-now*). The argument is a tuple of PARAMETER NAMES,
    with `"self"` admitted for a method receiver:
    `@native_borrow(returns=("a", "b"))` on
    `def max[T, K](a: T, b: T, key: Fn[[T], Own[K]]) -> T: ...` says the result is
    a borrow rooted in either argument, read as a union. It stamps
    `return_borrows_from` exactly as a def body does, so every consumer reads one
    field whether the callee has a body or not. It is READ AT REGISTRATION, beside
    the existing native-method receiver stamp: a bodyless native METHOD whose
    `signature_may_return_borrow` holds already derives `frozenset({-1})` from its
    signature (`tpyc/sema/registration.py:1568-1581`; the generator stamp at
    `:1565-1567` is a different inference), and that is true for an open
    type-param return, i.e. precisely `max`-shaped stubs. Five points settle how
    the two interact and what the annotation may say.
    - **Precedence.** The annotation SUPPRESSES the signature inference for that
      stub. An omitted `"self"` is therefore a positive denial of a receiver
      borrow, not an oversight the inference fills in.
    - **Schema.** `Parser._schema_from_stub._map_type`
      (`tpyc/parse/parser.py:1369-1392`) maps only `bool`, `str` and `type` today,
      and one unmapped parameter kills the whole schema, so the annotation's cost
      includes a new TUPLE-OF-PARAMETER-NAMES argument kind in `_map_type` and
      `_validate_decorator_args`. `native_preserves_refs`
      (`lib/tpy/tpy/_bootstrap/_extern.py:31`, qname at `tpyc/qnames.py:157`,
      parse field at `tpyc/parse/nodes.py:1454`, copied into `FunctionInfo` at
      `tpyc/sema/registration.py:1534`) is precedent for the plumbing but is a
      ZERO-parameter bare decorator, so it is no precedent for the schema.
    - **Parameter validation.** A named parameter must have borrowable storage.
      The predicate already exists: `generator_borrow_param_indices`
      (`tpyc/sema/registration.py:3234-3239`) admits an index only for a
      non-value, `str`, borrowing-view or varargs parameter and excludes
      `is_owned_in_coro_frame`. `returns=(...)` is validated against it at
      registration, and naming a scalar parameter is an error naming that
      parameter.
    - **The stub's own render.** A bodyless native stub whose declared return is a
      bare reference type renders FRESH at a direct call today: `@native("ns::get")
      def get() -> Node` over a C++ `node_t&` emits `::ns::node_t q = ::ns::get();`
      -- a copy -- while the identical TPy `def same(c: Leaf) -> Leaf` in the same
      file emits `Leaf& r = ...` (measured). The annotation therefore changes the
      stub's RENDER as well as its provenance: an annotated borrow-returning stub
      binds as a borrow, like a def. A stub that is NOT annotated keeps today's
      render and is rejected at a borrow contract, so no existing stub's emission
      changes silently.
    - **What it cannot say.** A result borrowing C++ STATIC storage
      (`ns::get_current()` over a registry singleton) has no parameter to name and
      no TPy module global to loan, so there is no admissible root and no slice-1
      spelling: such a stub must declare `-> Own[R]`, which is what silently
      happens today, and the reject row says so outright. A stub also cannot
      DECLARE a C++ rvalue-reference result, there being no TPy spelling for
      `T&&`: that makes "the declared return is what it is" a stub-author
      obligation, not a compiler verdict.
    `min`, `max` and `sorted` are its first users, and it is what makes rule 32's
    `-> T` decidable.

## The admission layer

Rules 5, 24 and 31 make a contract's FORM sound. They say nothing about where a
borrow points, and a form check alone would admit a dangling result with every
toolchain silent (rule 31, measured). Until the analysis-only MIR can answer
provenance, the following twelve rules are the proposed safety obligations for
the contract half. Their implementability and compatibility cost must pass the
compatibility gate before the coupled branch proceeds.
They are conservative by construction: each one refuses a shape rather than
guessing, and each names the requirements-document rule that admits it later.

A1. **Argument-root admissibility.** A borrow-returning binding is admitted only
    when EVERY root of the result is proved to be an admissible argument of that
    call. A root that is unknown, capture-rooted, function-local or otherwise not
    nameable as an argument rejects. Checking the return type proves nothing about
    roots, so this is a separate proof at the binding, not a consequence of rule 5.
    (Requirements rules 7, 16, 23, 25.)

A2. **No stored or returned closure with a borrowed environment.** A closure whose
    environment contains a borrow -- a by-reference `self` capture, a captured
    `Fn` parameter, or a by-value capture holding a view, a `Span` or a `Ptr` --
    may not be stored in a field or container, returned, or bound to any slot that
    outlives the enclosing statement. Non-escape is carried as a conservative
    per-binding MARKER on the callable value, propagated through assignment,
    aliasing (`g = f`) and named-call forwarding; where the escape behaviour of a
    sink cannot be decided, it rejects. (Requirements rules 9, 10, 12, 13.)

A3. **Argument loans, conservatively preserved.** A call whose result form is
    BORROW loans EVERY potentially borrowed argument, and those loans are
    preserved through locals, aliases and named forwarding using the EXISTING
    parameter-index summaries (`return_borrows_from`), which conservatively cover
    the simple `apply(f, x)` shape. A composition those summaries cannot express
    rejects rather than silently losing the dependency. (Requirements rules 15,
    19, 21.)

A4. **Unknown effects conflict with any live loan.** A call whose effects are not
    known -- a call through a callable value, a call to an opaque or recursive
    callee, a named helper that invokes a callback -- conflicts with ANY live
    loan, including ordinary element, view and pointer loans, not only
    callable-dependent ones. The conflict is an error at that call. (Requirements
    rules 11, 33, 34, 35, 36.)

A5. **No global-root exception.** A callable result rooted in a module global
    rejects. Program lifetime belongs to the STORAGE, not to the value, and
    nothing in this half tracks global identity or invalidation, so the "the root
    is a global, therefore it is safe" shortcut is not available. (Requirements
    rules 28, 35.)

A6. **Escaping temporary roots reject.** A receiver or argument root that is a
    temporary at a call whose result outlives the full expression is an error. The
    FRESH fallback survives only where a FORM-NEUTRAL contract meets the existing
    warned copy path and ANY candidate root is a temporary -- the `max(a, Node(5),
    key=...)` shape -- and it carries that warning. (Requirements rule 29.)

A7. **Borrow-bearing aggregate results reject.** A callable result whose payload
    can contain borrows -- a pointer-repr tuple, a record with a borrowing field,
    a view inside an owned aggregate -- rejects. `Own[...]` on the outer value
    does not erase an interior dependency, and this half has no way to carry one.
    (Requirements rule 2's contained-borrows half.)

A8. **Conflicts are checked DURING the invocation.** A callback may invalidate an
    argument borrow while the helper that received it is still using that borrow,
    so the conflict check runs at the call that passes the callable, not only
    after its result is bound.

A9. **Extents are conservative and lexical.** A loan is live from its creation to
    the end of the enclosing lexical region unless it is provably consumed
    earlier. "Not currently recorded" is never read as "dead".

A10. **Hard errors, not warnings.** Every admission failure in the supported
    callable subset is an ERROR. The existing checker sometimes warns and
    continues; a fail-closed claim cannot rest on an advisory diagnostic.

A11. **Rules 5, 24 and 31 are mandatory sema checks.** C++ constraints cannot
    validate provenance, and the supported erasure toolchains do not reliably
    reject a dangling fresh-to-reference conversion (rule 24, measured). Sema is
    the only backstop, so none of the three may be skipped on any path.

A12. **The widget-in-constructor idiom is RESTRICTED.**
     `self.button = Button(lambda: self.bump())` inside `__init__` rejects under
     A2. The intent is legal -- an object outlives its own constructor -- but the
     lifetime proof needs receiver liveness at the sink, which is MIR's.
     (Requirements rule 10.)

### Compatibility cost of unknown effects

A4 also rejects safe code unrelated to reference-returning callbacks. With
`Node` a mutable reference type holding `v: int32`:

```python
def use(f: Fn[[], int32], xs: list[Node]) -> int32:
    q = xs[0]
    value = f()
    return q.v + value

def main() -> None:
    xs = [Node(7)]
    print(use(lambda: 3, xs))
```

This shape emits C++ today, with `q` a reference into `xs`. The contract-only
implementation rejects the call to `f` because it cannot establish the callback's
effects, although this caller supplies a harmless constant-returning lambda.
That rejection is not required by the callable return rule; it is the cost of
the temporary effect policy. A4's scope includes scalar callbacks and ordinary
element, view and pointer loans, not only callable-dependent results.

## Rejected in this half

These changes reject some programs accepted today, both unsafe programs and safe
false positives. "Kind" uses the distinction above; "Admitted by" names the
requirements-document rule (or later slice) that lifts the restriction. A lifetime
diagnostic must name a demonstrated violation. An analysis diagnostic must say
what cannot be proved, without claiming that the destination outlives the owner.

Every suggested remedy must have a compiling acceptance case under the proposed
admission rules before its message ships. Check interactions between restrictions:
extending a receiver's lifetime does not bypass A2; moving a use earlier does not
necessarily end A9's lexical loan; `copy()` is unavailable for some payloads and
does not remove interior borrows. Where no supported remedy is demonstrated, say
that the shape is not supported yet instead of prescribing an unverified rewrite.

| Shape | Kind | Message sketch | Admitted by |
|-------|------|----------------|-------------|
| An `Fn` parameter forwarded into a storing slot, or captured by a stored lambda | Contract | `'f' is an Fn parameter: it may hold references to its caller's locals, so it cannot be stored. Change this parameter to 'Callable[[int32], int32]' so the callback is copied into the slot.` | permanent (requirements rule 12) |
| An `Fn` value bound to an OPEN type parameter | Analysis | `'f' is an Fn parameter, and 'relay' takes its callback as an open type parameter 'C', so whether 'relay' stores it cannot be decided here; change 'f' to 'Callable[[int32], int32]' so it is safe to store, or give 'relay' a concrete Fn parameter.` | D1's per-instantiation channel |
| A callable-dependent loan live across a suspension, `yield` of a neutral result included | Analysis | `'q' is a reference produced by calling 'f', and it is still in use after this suspension; call 'f' again after resuming, or take 'copy(q)' before the suspension.` | D4 |
| Mixed borrow / fresh return paths in one lambda or nested `def` | Representation | `this callback returns 't.left' (a reference into 't') on one branch and a new Leaf on the other; a callback has one result form. Return 'copy(t.left)' on both branches -- note both then hand back duplicates -- or return an existing Leaf on both.` | one-form restriction; a mixed-form representation is not designed |
| A stored `Callable` whose result borrows its CAPTURES | Analysis | `this callback's result borrows 'cache', which the Callable captured; TPy cannot yet track results borrowing a stored callback's captures. Pass the table as an argument, or return Own[Leaf] -- note Own hands back a copy, so mutations through the result stop being visible in 'cache' -- or hold the entries as 'Rc[Leaf]' and return 'cache[k].clone()', which keeps the result shared with the table.` | D3 |
| Unresolved recursive provenance | Analysis | `'rec' calls itself through 'f', so what its result refers to cannot be determined here; declare the result 'Own[U]' so every call hands back a fresh value.` | delayed validation with a conservative provenance bound |
| An escaping temporary root (a temporary receiver whose method returns a `self`-capturing callback included) | Lifetime | `'Sel()' is a temporary and the callback borrows it, but the callback is used after this statement. Binding the receiver to a local removes this temporary-lifetime violation, but returning a callback with a borrowed environment is still unsupported (A2).` | permanent |
| A borrow-bearing AGGREGATE result (pointer-repr `tuple`, a record with a borrowing field) | Analysis | `this callback returns tuple[Node, int32] whose first element is a reference into its argument; tuples of references returned through a callable are not supported yet. Return 'tuple[Own[Node], int32]' (the Node is copied), or return only the scalar fields you compare.` | requirements rule 2 |
| A `@native` / `@cpp_template` borrow-returning callee with no `@native_borrow` | Analysis | `'native_get' is a @native binding with no TPy body, so what its reference points into cannot be determined; annotate it '@native_borrow(returns=("src",))' to say which parameter the result borrows, or declare it '-> Own[Node]'.` | -- (rule 37 ships the annotation) |
| A `@native` result borrowing C++ static storage (no parameter to name) | Representation | `'get_current' hands back a reference into storage that no argument names, so TPy cannot say what it points into; declare it '-> Own[Node]' -- the caller then owns a copy, which is what the current binding already does.` | a spelling for static-storage roots (requirements residue) |
| A FORM-NEUTRAL result in an owning slot (rule 27) | Contract | `'collect(lambda t: t.left, ...)' passes a callback that hands back a reference, and 'collect' stores its result in a list (main.py:21); TPy has no container of references. Pass 'lambda t: copy(t.left)', or declare the parameter 'Fn[[T], Own[K]]' -- both make the stored element a duplicate.` | D1 |
| A zero-argument borrow contract with no admissible root | Analysis | `'get_current' is bound to 'Callable[[], Node]', which returns a reference, but it takes no argument to take one from; declare 'Callable[[], Own[Node]]' so every call hands back a fresh value.` | requirements rules 23, 28 (a capture or global root becomes admissible) |
| A multi-argument borrow contract where the callee borrows only SOME arguments | Analysis | `'f' is declared 'Fn[[Node, Node], Node]', so its result may refer to either argument; 'scratch' is a local here, and the result outlives it. There is no spelling yet for "the result borrows only argument 0" -- pass a value that outlives the result in both positions.` | a per-argument loan marker |
| A reference-typed `Optional` / union callable result | Representation | `'Callable[[Node], Node \| None]' hands back either a reference or nothing, and an optional reference is not supported yet; declare 'Callable[[Node], Own[Node] \| None]' -- the result is then a copy -- or return a sentinel Node.` | `BUGS.md#fn-contract-reference-optional-result` |

The admission layer adds six rows:

| Shape (admission rule) | Kind | Message sketch | Admitted by |
|-------|------|----------------|-------------|
| A capture-rooted result at a NON-storing `Fn` slot (A1) | Analysis | `'lambda i: xs[i]' hands back a reference into 'xs', which it captured rather than took as an argument; TPy cannot yet prove how long that reference stays valid. Pass 'xs' as a parameter of the callback, or return 'Own[Node]' -- note Own hands back a copy.` | requirements rules 9, 16, 19 |
| A stored or returned closure with a borrowed environment, `self` included (A2) | Analysis | `storing this callback in 'bus.listeners' is not supported yet because it captures 'self' by reference; TPy cannot verify the Logger's lifetime at this destination.` | requirements rules 9, 10, 13 |
| A borrow forwarded through a composition the parameter-index summaries cannot express (A3) | Analysis | `'h1' forwards its callback's result through 'h2', and TPy cannot yet follow what that result refers to across both hops; call the callback directly here, or declare the result 'Own[U]' so every call hands back a fresh value.` | requirements rules 15, 21 |
| An unknown effect under a live loan (A4) | Analysis | `TPy cannot determine the effects of calling 'grow' here while the reference 'q' (from 'get(0)' on line 20) is live; calls with unknown effects under a live reference are not supported yet.` | requirements rules 33-36 |
| A callable result rooted in a module global (A5) | Analysis | `'first' hands back a reference into the module-level 'pool', and a later call may reallocate it; TPy does not yet track what invalidates a global. Return 'Own[Node]' so the caller owns a copy.` | requirements rules 28, 35 |
| A `self`-capturing callback stored in a child built by the constructor (A12) | Analysis | `storing this callback in 'self.button' is not supported yet because it captures 'self' by reference; TPy cannot yet prove that the Panel outlives the stored callback.` | requirements rule 10 |

The zero-argument row is a PER-BINDING check, not a shape reject: with A5 in force
its only admissible root in this half is a directly named `__call__` receiver, so
`Fn[[], B]` and `Callable[[], B]` stay legal where that is the root and the
message names the binding that had none. Migration therefore does not tell an
`Fn[[], B]` site to respell. A C++ `T&&` result is not in these tables at all: it
is a stub-author obligation (rule 37).

## Data each phase carries

| Phase | Fact | Where |
|-------|------|-------|
| typesys / value_category | the result descriptor (transfer form + permission + contained-borrows) and its reader in `typesys`, so `value_category` and `CallableType._std_function_sig` both consume ONE decider | `tpyc/typesys.py:4529` is the render consumer and `tpyc/value_category.py:18` is why it cannot live the other way round; `call_returns_cpp_ref` (`value_category.py:127`) and `async_return_form` (`:55`) become consumers |
| sema (stub) | `return_borrows_from` stamped from `@native_borrow(returns=(...))` at registration, suppressing the signature inference | `tpyc/sema/registration.py:1534`, `:1568-1581` |
| sema (call site) | the resolved descriptor on a PARSE-NODE field of the call, the `await_result_is_borrow` carrier (`tpyc/parse/nodes.py:731`, stamped at `tpyc/sema/expressions.py:2412`) -- its sema readers run before THIR exists: `returns_borrow` (`tpyc/value_category.py:398`) and the callable arms of `is_rvalue_source` | stamped at `tpyc/sema/calls.py:6009` |
| THIR | the descriptor COPIED onto `THIRCall` (`tpyc/thir/nodes.py:570`, `@dataclass(frozen=True)` at `:569`, hence hashable -- a frozen dataclass of tuples, never a set or a dict) by BOTH arms: the free-call arm for a bare-name callable value (`tpyc/thir/lower/checks.py:3825-3836`, called at `tpyc/thir/lower/expressions.py:8749`) and the computed-callee arm (`:7684`) | codegen reads ONLY this copy, unlike `gen_async.py`'s direct parse-node read of `await_result_is_borrow` |
| runtime / codegen | `result_slot_t` / `to_result_slot` (two rows, each with a `const&` `get()`) and `result_neutral`, inside a `frame_slot` in a resumable frame (`runtime/cpp/include/tpy/frame_slot.hpp:68`); the per-form `requires` render and the erased `std::function` spelling | `runtime/cpp/include/tpy/type_traits.hpp:214-220`; `tpyc/codegen_cpp/functions.py:334`, `:357`; `tpyc/typesys.py:4529` |

**The slot trait is new, and is not `val_or_ptr_t`,** which keys on
`is_value_type<T>` (`runtime/cpp/include/tpy/type_traits.hpp:215`) and is
ill-formed for a borrow callee (`Node&` gives `Node&*`) and wrong for a fresh one
(`Node` gives `Node*` over a prvalue). It is a WRAPPER TYPE, not a bare alias: a
bare `T*` slot cannot be told apart from a `Ptr[R]` payload slot, which is
verbatim the objection this design raises against `tuple_elem_ref` (`:290`). The
rvalue-reference row is declared-not-defined so a native `T&&` result is a hard
error rather than a silent assign-through into storage this call does not own:

```cpp
namespace detail {
    template<typename T> struct result_slot {          // the FRESH row
        static constexpr bool is_borrow = false;
        T v;
        T&        get() &      { return v; }
        const T&  get() const& { return v; }
        T&&       get() &&     { return static_cast<T&&>(v); }
    };
    template<typename T> struct result_slot<T&> {      // the BORROW row
        static constexpr bool is_borrow = true;
        T* v;
        T& get() &      { return *v; }
        T& get() const& { return *v; }
        T& get() &&     { return *v; }
    };
    template<typename T> struct result_slot<T&&>;      // no row: rejected in sema
}
template<typename T> using result_slot_t = detail::result_slot<T>;

template<typename Dest, typename T>
Dest to_result_slot(T&& x) {
    if constexpr (std::is_lvalue_reference_v<T>) { return Dest{&x}; }
    else {
        static_assert(!Dest::is_borrow, "borrow slot cannot take a temporary");
        return Dest{std::forward<T>(x)};
    }
}
```

The `const&` rows let a slot be read through a `const Frame&` or a non-mutable
`operator()`, the borrow row staying MUTABLE through them (`*v` is `T&` whatever
the slot's constness). `to_result_slot` keys on the EXPRESSION's
lvalue-reference-ness -- the key `result_slot` itself uses -- not on the slot being
a pointer: a `Ptr[R]` payload arrives as a prvalue `Node*` and must pass through
rather than be addressed, and its `static_assert` is the one `to_val_or_ptr`
already carries (`:317`). For `def use[T](f: Fn[[T], T], a: T, b: T) -> T` with
`q = f(a); q.v += 1; q = f(b); return q`:

```cpp
template<typename __F0, typename T>
decltype(auto) use(__F0& f, T& a, T& b) {           // NOT `__R0`: a body-local
    using __R0 = decltype(f(a));                    // alias cannot spell a signature
    ::tpy::result_slot_t<__R0> q =
        ::tpy::to_result_slot<::tpy::result_slot_t<__R0>>(f(a));
    q.get().v = ::tpy::add_check<int32_t>(q.get().v, 1);
    q = ::tpy::to_result_slot<::tpy::result_slot_t<__R0>>(f(b));
    return static_cast<__R0>(std::move(q).get());   // rule 26's return of a slot
}
```

ONE body serves every instantiation, `.get()` being identity-then-member on the
fresh row and deref-then-member on the borrow row, so there is no `if constexpr`
over a shape and no second template. The slot is keyed on `decltype` of the
EMITTED CALL, not on `std::invoke_result_t<__F0&, ...>` re-spelled from the
contract, which probes its arguments as rvalues and would differ for a callee with
ref-qualified parameter overloads. And the RETURN is `q.get()`, not `q`: returning
the slot deduces `result_slot_t<Node&>`, making the caller's slot a `Ptr`-payload
slot with no deref and losing the borrow marker one frame up. The render this
replaces is a value slot that copies the borrow AND turns the rebind into an
assign-through (`BUGS.md#callable-value-borrow-return-copies-unwarned`).

The `requires` rows, per form, with `__a0` a `Node&`:

```cpp
// Fn[[Node], Node]           requires std::is_lvalue_reference_v<decltype(__fn(__a0))>;
//                            { __fn(__a0) } -> std::convertible_to<Node&>;
// Fn[[Node], readonly[Node]] ... the same category check, -> convertible_to<const Node&>;
// Fn[[Node], Ptr[Node]]      requires !std::is_reference_v<decltype(__fn(__a0))>;
//                            { __fn(__a0) } -> std::convertible_to<Node*>;   // no trailing &
// Fn[[Node], Own[Node]]      requires !std::is_reference_v<decltype(__fn(__a0))>;
//                            { __fn(__a0) } -> std::convertible_to<Node>;

template<typename R, typename U>            // the neutral contract: a bare
concept result_neutral =                    // convertible_to<U> is false for a
    std::convertible_to<R, U> ||            // @nocopy U at a borrow callee
    std::convertible_to<R, U&> || std::convertible_to<R, const U&>;
```

Every block above compiles with **`g++-14` 14.2.0 and `clang++-19` 19.1.1**, both
`-std=c++23 -Wall -Wextra`, each negative rejected on both: all four slot
instantiations from ONE `decltype(auto)` template (the borrow rebind mutates the
caller's `a`, the fresh one does not), the `const&` `get()` rows, the
`is_move_assignable_v` failure that is rule 26's rebind reject, the temporary
`static_assert`, the absent `T&&` row, every `requires` row against its wrong
callee (fresh-at-borrow, borrow-at-fresh, readonly-at-mutable, a by-value callee
at a const-reference contract which `convertible_to` alone would admit, a `Ptr`
callee at the borrow row, a `Node*&` callee at the `Ptr` row), and
`std::function<Node&(Node&)>` taking a borrow lambda while rejecting a fresh one.
Rule 24's two extra payload rows measure on the same pair:
`std::function<const std::string&(...)>` REJECTS a `string_view` callee while
`std::function<std::string_view(...)>` accepts an owned-`std::string` callee (the
dangle that spelling would hide), and `std::function<Node*(Node&)>` rejects an
`optional<Node*>` callee. The `const Node&` erasure is the ONE claim that does not
hold portably -- admitted on `clang++-19` (and `clang++-18`, `g++-12`), rejected
only on `g++-13`, `g++-14` and `clang++-20` -- which is why rule 24 makes sema the
sole backstop. Names stay `::tpy::`-qualified and the compound requirement calls
through a variable, so neither path is subject to ADL.

## Decisions

The fourteen points the implementation dry run left open, each settled here with
an answer that needs no place model.

1. **Classifier signature.** `classify_result(ret_type, *, position, fi=None) ->
   ResultDescriptor`; `call_returns_cpp_ref` degrades to a one-liner over it and
   `async_return_form` to a second. This is the target semantic API; checkpoint 2
   uses `classify_result_representation` without inventing the missing facts.
2. **Where its halves live.** The WHOLE descriptor lives in `typesys`;
   `value_category` keeps only the analyzer-dependent predicates. `_std_function_sig`
   needs the permission and contained-borrows dimensions, so a reduced second
   reader is exactly the drift rule 3 exists to prevent.
3. **`AsyncReturnForm`.** Deleted when the full descriptor lands. Checkpoint 2
   retains it as a compatibility adapter over the shared representation reader.
4. **`Place`'s degenerate form.** Not applicable: this half introduces no `Place`.
   Loans keep today's string storage keys.
5. **One loan table or two.** Neither is re-keyed and no second table is added.
   The loan table stays as it is until MIR replaces it.
6. **`InvokeResult.form`.** Not applicable: no symbolic summary in this half. Where
   a form is carried it is carried as the full descriptor, never as one of its
   three facts re-derived at each read.
7. **`substitute_method_type_params`.** Unchanged: `return_borrows_from` keeps its
   verbatim copy, which is right for an index set. The remap belongs to the
   summary, which is a requirement on MIR.
8. **The argument-coerce site.** `_typecheck_and_coerce_arg` in `tpyc/sema/calls.py`,
   with the four non-argument bindings (assignment, field initializer, container
   element, return) calling the same checker. A hook inside `check_type_compatible`
   is rejected: the SOURCE expression would have to be threaded to every caller,
   and rule 6 is keyed on the source.
9. **How the six stripping sites agree.** `_classify_strict_match` and the
   `__call__` conformance loop gain FORM-AWARE equality, because rule 30 requires
   the form to SELECT; the other four keep stripping, and the post-selection check
   re-reads the unstripped types from the source.
10. **`@native_borrow`'s schema.** `_map_type` gains a tuple-of-parameter-names
    kind. A varargs or comma-joined respelling is rejected: the names are a list,
    and the schema is the place that validates them.
11. **`writes_globals` and the topo sort.** Not applicable: the global-write fact
    is a requirement on MIR, so nothing is folded into the existing in-degree
    computation now.
12. **Clearing the new callee list.** Not applicable, for the same reason.
13. **The slot at a CONCRETE borrow contract.** A concrete borrow contract renders
    a BARE reference (`Node& q = f(a);`). The slot is reserved for a FORM-NEUTRAL
    result, where the borrow-vs-payload distinction is not statically known.
14. **The `Fn`-provenance marker.** A per-binding conservative fact on the local
    (A2), propagated through assignment and aliasing. NOT a type-level marker on
    `CallableType`: that leaks into type equality and into all six stripping sites.

## What works now, what rejects until MIR

| Idiom | Verdict in this half |
|-------|----------------------|
| Argument accessor: `lambda n: n.child` at a borrow contract | **works**, with conservative argument loans (A3) |
| Factory: `lambda i: Node(i)` at `Own[Node]` | **works** |
| Scalar and `str` keys: `key=lambda s: s.name` | **works**, and costs nothing (rule 32) |
| Reference-typed key at an `Own[K]` contract | **works**, with the existing copy warning; a `@nocopy` / `__del__` payload errors with the real fix (rules 5, 32) |
| Generic `apply(f, x): return f(x)` | **works** for proved argument-rooted borrows and for fresh results; argument dependencies forwarded conservatively |
| Stored event handler returning `None` or a scalar | **works when its stored environment owns its dependencies**; a scalar result alone is not sufficient (A2) |
| Capture accessor through a helper: `get(lambda i: xs[i], 0)` | **rejects** (A1), including indirectly capture-rooted results |
| `peek` and `grow` over one list with a `peek` result live across `grow` | **rejects** (A4) |
| Two closures that merely MENTION the same list, both reading | not inherently forbidden; admitted where A1 and A4 are satisfied |
| Escaping `self`-capturing callback | **rejects** (A2), even with a scalar result |
| Constructor stores `lambda: self.bump()` in a child | **rejects** (A12), pending a lifetime proof |
| A callable result rooted in a module global | **rejects** (A5) |

## CPython parity

The callback idioms here are valid Python, so each rejection is a divergence
requiring a located diagnostic and a note in `docs/LANGUAGE_FEATURES.md`. State
whether it follows from a language contract or a temporary limitation, and give
a verified remedy where one exists. Documenting a safe false positive does not
itself justify shipping it; that decision belongs to the compatibility gate.
No divergence this half CREATES is silent, and rows
(a), (b) and (h) each replace a silent copy performed today; the one silent
divergence it leaves untouched is declared as row (j) rather than omitted.

| Divergence | Diagnostic | Escape hatch | LANGUAGE_FEATURES note |
|------------|-----------|--------------|------------------------|
| (a) bare reference contract with a fresh body: `g: Callable[[int32], Node] = lambda i: Node(i)` | row 8's callable-framed error | spell `Own[Node]` in the contract | that a callable's return reads exactly as a def's, with the factory spelling |
| (b) fresh contract with a borrow callee: `own(same, a)` | rule 5's located error at the ARGUMENT | `lambda n: copy(same(n))`, or declare `Fn[[Node], Node]`; for a `@nocopy` / `__del__` payload the `copy()` half is withheld and `Rc[...]` + `.clone()` named instead | the two mismatch directions and their remedies |
| (c) capture-rooted borrow out of a stored `Callable` | the A1 / stored-`Callable` error at the conversion, naming the captured place | pass the state as an argument (`Fn` is parameter-position only, so it is NOT a remedy at a field or local); hold the entries as `Rc[...]` and hand back a `.clone()`, which preserves the aliasing; or return `Own[...]`, which the diagnostic flags as changing it | the argument-only contract, cross-referenced to section III of the closures design |
| (d) a form-neutral result into an owning slot, at a BORROW instantiation | rule 27's reject at the call site that chose the borrow callee | `copy()` at the slot, or `Own[...]` in the contract; both copy, and the diagnostic says so | the copy contract of a generic body, and that TPy has no container of borrows |
| (e) mixed borrow / fresh lambda return paths | the mixed-path error naming both RETURNED EXPRESSIONS (not lines -- a conditional lambda is one line) | `copy(n)` on both paths, or return an existing object on both | the union rule for closure results |
| (f) a stored `Callable` capturing a mutable local SNAPSHOTS it; CPython aliases | the escaping-closure capture warning (`BUGS.md#escaping-capture-mutation-snapshot`) | hold the state in a class or an `Rc`, or pass it as a callback argument | that stored callbacks do not alias their captures |
| (h) `max(a, b, key=...)` / `min` hand back the winner ITSELF | none needed: rules 32 + 37 REMOVE the divergence rather than declaring it (TPy prints `5 99` today where CPython prints `99 99`) | -- | that the winner aliases, matching CPython; and that when ANY argument is a temporary, A6's fallback copies it, warned |
| (i) a `Callable` returning a `self`-capturing closure out of a TEMPORARY receiver | A6's temporary-root reject | no lifetime-only workaround in this half: a durable receiver removes A6, but A2 still rejects the returned borrowed environment | that a `self`-capturing callback is a view over the receiver, and lives as long as one |
| (j) `functools.reduce` with an IN-PLACE accumulating callback: the caller's seed is never updated (TPy prints `3 0`, CPython `3 3`) | **none -- silent, and this half does not change it** | accumulate by returning a fresh value instead of mutating the seed, until D2 | that `reduce`'s seed is copied in, so an in-place callback's writes are lost; the entry is `BUGS.md#reduce-accumulator-copied` |
| (k) a capture-rooted callback at a NON-storing `Fn` slot, legal Python and legal under the full design | A1's reject | pass the captured container as a callback argument, or return `Own[...]` | that capture-rooted callback results wait on the provenance analysis |
| (l) a harmless scalar callback invoked under an ordinary element loan | A4's unknown-effects reject | none established for the general case; a rewrite must actually end the loan under A9 | temporary effect-analysis limitation, including the safe example under A4 |
| (m) a stored callback borrowing a receiver that does outlive it, including a child callback in a constructor | A2 / A12's unsupported-lifetime-proof message | keeping the receiver alive alone does not bypass A2; alternative capture forms need a verified acceptance case | temporary borrowed-environment restriction; intended acceptance under requirements rule 10 |

Evaluation order, exception behaviour, scoping, numerics and identity are
untouched.

## Stdlib consequences

- `min` / `max` (`lib/tpy/tpy/_builtins/_funcs.py:125`, `:174`) and `sorted`
  (`:525`) keep `K` neutral and respell the key as `key: Fn[[T], Own[K]]`, because
  the C++ helpers store it (rule 32). Their OWN `-> T` is classified by rule 32
  instead of exempted, and rule 37's annotation is what makes a bodyless
  `@cpp_template` stub's `-> T` decidable:

  ```python
  @dispatch
  @pure
  @readonly
  @native_borrow(returns=("a", "b"))
  @cpp_template("::tpy::max_key({0}, {1}, {2})")
  def max[T, K: Comparable](a: T, b: T, key: Fn[[T], Own[K]]) -> T: ...
  ```

  The three-argument siblings take `returns=("a", "b", "c")`; `sorted`'s
  `-> Own[list[T]]` is FRESH and carries no annotation. Today the winner is copied
  silently (`5 99` against CPython's `99 99`, measured on this tree), which rule 32
  closes by binding through the slot and correcting `tpy::max_key` / `min_key` from
  `const T&` to `T&` (`runtime/cpp/include/tpy/builtins.hpp:319`, `:334`).
- `map` (`:620`) keeps `Fn[[T...], U]` with `U` neutral; the call-site inference
  that instantiates `map_iter`'s payload at the borrow form is what rule 26
  preserves. `filter` (`:648`) and `takewhile` / `dropwhile` / `filterfalse`
  (`lib/tpy/itertools.py:56`, `:63`, `:73`) return `bool`, formless by rule 4.
- `functools.reduce` is UNTOUCHED (`lib/tpy/functools.py:19`, `:27`) and
  `BUGS.md#reduce-accumulator-copied` stays open -- the seed's fate is a
  parameter-position question, not a result-form one, D2's pair is its designed
  resolution, and parity row (j) states that the shape stays silently divergent.

## Migration

The two scans below measure signature and result-form changes only. They do NOT
measure the new rejections from A1-A12, particularly A4's reach into scalar
callbacks under ordinary loans. Their counts cannot establish the compatibility
cost of the whole proposal.

The first scan, over `Fn[...]` / `Callable[...]` SPELLINGS in
`tests/cases`, `lib`, `examples` and `tests/interop` bucketed by return shape: six
bare reference-typed callable returns in four files.

- `tests/cases/generics/generic_own_slot_copy_ack/src/main.py` -- `Fn[[int32],
  Cell]` at `:89` and `:98`, bound to `lambda v: Cell(v)` at `:107` and `:109`: row
  8 errors, both respelled `Fn[[int32], Own[Cell]]`. The four neutral / concrete
  bodies (`:86`, `:91`, `:95`, `:99`) keep their `# tpyc: ok` -- rule 27 fires per
  instantiation and every caller in the file passes a FRESH callee.
- `tests/cases/imports/error_mutual_symmetric_inline_templates/src/a.py:12`,
  `.../src/b.py:11` and
  `tests/cases/imports/error_mutual_template_function_by_value/src/a.py:7` -- the
  zero-argument contract `Fn[[], B]`. NO respelling: that row is a per-binding check
  on the ROOT, and all three stop at the cyclic-import error first.
- `tests/cases/calls/error_func_ref_generic_bound/src/main.py:12` --
  `Fn[[Blob, Blob], Blob]` bound to `def max_val[T: Comparable](a: T, b: T) -> T`, a
  borrow callee at a borrow contract; no change.

The second scan is the one a spelling census misses: **`key=` LAMBDAS**, which rule
32 touches without any `Fn[...]` appearing in user code. Over the same four trees
(source files only) there are **22 `key=lambda` sites in 8 files** (re-measure at
implementation: a later count found 23, one site having drifted in). Twenty key on
a scalar, a `str` or a tuple element and are untouched -- a `str` key already
renders an owned `std::string` at a def return (measured). The remaining **two**
(`tests/cases/builtins/sorted_key_user_type/src/main.py:23`, `:29`, over a
record-typed `Comparable` field) keep compiling under rule 32 and gain the existing
`copies Score into owned storage` warning -- the verdict the named twin already
gets. No `key=` site becomes an error from the result-contract change alone;
admission-layer effects remain to be measured. Result-form churn also includes
the `requires` clause at every `Fn` site (**90 snapshot files** carry `requires(__F`
on this tree) and the
slot render of every body that binds a NEUTRAL callable result to a local; **261**
`Fn[` / `Callable[` source spellings exist in total.

## Compatibility gate

The checkpoint 3 investigation is recorded in
[`CALLABLE_CONTRACT_FEASIBILITY.md`](CALLABLE_CONTRACT_FEASIBILITY.md).
Its focused baseline and code audit led to the approved 2026-09-17 decision to
bring shared analysis-only MIR forward. See `MIR_ANALYSIS_PLAN.md` for the
implemented scalar foundation and later increments. The gate has not passed and the coupled implementation
remains gated; this decision does not approve A1-A12 as language restrictions.

Before committing to the coupled implementation branch, establish the admission
layer's feasibility and its effect on programs accepted today. Contract-first is
a proposed sequence, not an exemption from measuring safe false positives.

1. For each admission rule, identify the existing facts that establish it, the
   missing facts and propagation, and the bounded check proposed in their place.
   Include the per-instantiation form facts needed by rules 26 and 27, distinguishing
   infrastructure needed now from D1's later behavior. Estimate this work separately;
   it is absent from the 49-day subtotal below.
2. Measure accepted programs newly rejected by A1-A12 against the existing corpus
   and focused examples. Include scalar callbacks, ordinary element/view/pointer
   loans, named forwarding, borrowed environments and safe constructor callbacks;
   a census of reference-return signatures is insufficient. Distinguish frontend
   acceptance, successful C++ builds and runtime/parity behavior in the baseline.
3. Classify each new rejection as a contract/representation restriction, a
   demonstrated lifetime violation, or a missing proof. Separate safe false
   positives from unsafe programs; record unresolved cases as unclassified rather
   than assuming they are unsafe. Count these separately from pre-existing rejects.
4. Pin representative safe false positives with their intended eventual acceptance,
   alongside unsafe cases that must remain rejected. When the interim restrictions
   land, any `error_` case for a safe program must state which missing proof will
   promote it to a happy case. Verify each diagnostic remedy with a compiling case
   under the same admission rules, including any change to aliasing or copying.
5. Present the measured compatibility cost and get an explicit decision on whether
   it is acceptable for an interim release. A located error is better than a
   miscompile, but that does not justify rejecting an unrelated safe program.

**Exit condition.** If enforcing the admission layer requires substantial new
flow analysis, or rejects common safe callback idioms, reconsider sequencing and
bring analysis-only MIR forward. Do not silently weaken the safety obligations or
turn a temporary analysis limitation into a permanent language rule to preserve
the contract-first schedule. The independent prerequisites below can proceed
without deciding that schedule.

## Implementation plan

### Execution checkpoints

This is the agreed implementation sequence. The numbered work items below are
the detailed inventory; they do not bypass these checkpoints or authorize new
language restrictions discovered during implementation.

| Checkpoint | Work and acceptance condition | Status |
|---|---|---|
| 1. Callable prerequisites | P0.1 located lambda diagnostics; P0.2 contextual lambda parameters at container bindings; P0.3 invocation through a dict field. Separate changes with focused regressions; preserve acceptance and generated code for P0.1. | Complete; all three landed |
| 2. Shared type decisions | Unify const-inference readers and extract existing result-representation decisions. Preserve current behavior, including native and erased callable differences, with byte-identical generated-code snapshots. | Both extractions implemented and verified |
| 3. Feasibility and generic forms | Design the full descriptor's permission and contained-borrow analysis and validate the per-instantiation form channel required by rules 26/27. Measure admission against ordinary callback programs, including safe false rejections. Decide contract-first versus analysis-only MIR first at the compatibility gate. | Initial survey/model/baseline complete; analysis-first approved 2026-09-17; compatibility gate not passed |
| 3a. Shared analysis foundation | Build analysis-only MIR before the coupled contract; increments and coverage gates in `MIR_ANALYSIS_PLAN.md`. | M1 scalar CFG implemented as an internal API; provenance/effects and admission remain gated |
| 4. Coupled contract implementation | Full semantic descriptor, admission, conversion checks, lambda result stamps, runtime slots, THIR, erasure, native annotations and stubs land together after checkpoint 3 passes. | Gated |
| 5. Precise provenance | Deliver the callable requirements on the shared analysis substrate before approving admission; further precision can follow as proofs become available. | Foundation moved forward to 3a; consumer remains gated |

Checkpoint 1 starts with **P0.1 only**. Its invariant is that a lambda rejection
identifies the actual rejecting lambda and blocking construct, preserving both
through enclosing calls and statements. Reuse the existing THIR diagnostic
machinery and routing predicates; do not add a second admission policy.

For each implementation unit, reproduce the relevant failure, trace sibling
paths, add focused tests and run the affected subset. Consult before updating
existing snapshots, explaining the concrete change. Run the full suite once at
the end of a completed compiler change, not for documentation-only checkpoints.
Commit each coherent completed checkpoint on the working branch and update this
table so the next session can resume from evidence rather than conversation.

**P0.1 complete (2026-09-16).** The implementation preserves routing, capture and
body failure details at the lambda's location, including through enclosing
expression diagnostics. Focused tests cover free functions, methods,
constructors, module scope, generators and async bodies; argument-gate failures,
frame/non-copyable/narrowed captures, and enclosing print/tuple/condition/f-string
paths have separate assertions. Review added coverage for suspending conditions,
await operands and both constructor tuple initializer shapes; these wrappers
preserve the same located rejection. Container-call elements in literals and
comprehensions cover the intersection with the latest master changes. All 28
focused lambda checks passed in the final full remote run after merging master
`afd815b028`: **7,988 passed, 23 skipped**, with all **4,134 executable cases**
built and run under `--force-exec`. Six existing diagnostic snapshots changed in
total; generated C++ snapshots did not change. At that checkpoint P0.2 had not
started. The
`return <void call>` fixture restriction is already tracked as
`BUGS.md#async-void-return-drops-call`.

The contract tests must distinguish borrowing from copying through mutation
visibility (or `@nocopy`), exercise rebinding separately from initialization, and
pair concrete and generic forms. Include named-function/lambda twins and the
same contract at parameters, returns, locals, fields and container elements.
Keep harmless scalar callbacks under a live loan in the compatibility corpus.

**P0.2 implemented (2026-09-16).** A consuming `Own[Callable[...]]` slot retains the
callable signature during contextual analysis, following the named-function
path. An owning tuple also passes its element hints to nested lambdas. Container
element writes and comprehension results reuse the existing value-form callable
lowering; optional slots preserve whole optional values and accept `None`.

The condensed `callable_container_binding` case covers free functions, methods,
constructors, module scope, generators, async bodies, comprehensions, closures,
context managers, `try`/`finally`, `@error_return` and `match`. It covers list,
array and dictionary slots, scalar and tuple elements, optional callbacks,
named-function twins, and mutation through reference arguments. Inference-only
tests cover callable parameter shapes: scalar, tuple, optional, union,
str/bytes, Own, readonly, Ptr/Span and Box/Rc, plus an open generic parameter.
Those checks establish context propagation without claiming new lowering for
every callback signature. Union-of-callable ambiguity, open-parameter lambda
lowering and `Own[Callable]` return lowering remain outside this prerequisite;
the last is tracked by `BUGS.md#own-callable-return-rejected`.

Validation: all 14 inference checks and three new snippet cases passed. The
full remote `--force-exec` run passed **8,005 tests, 23 skipped**, with all
**4,135 executable cases** built and run. No existing snapshots changed.

**P0.3 implementation.** An indexed field call preserves its subscript until
semantic analysis distinguishes a value field from a generic method. The
receiver is visited once, including walrus expressions and consuming calls.
Known record/property and protocol-bound fields use ordinary field/index
analysis, then the existing callable argument and computed-call lowering paths.
Index errors propagate; indexing a plain callable can no longer silently call
the unindexed value. Ordinary method specialization keeps its existing path.

The condensed `callable_field_index` case covers named and chained receivers,
dictionary/list/nested-list fields, generic records, readonly methods, scalar
and reference arguments, once-only property getters with shared results, void
callbacks, and key-before-argument evaluation
with inline arguments. Temporary arguments reuse direct-call materialization;
their ordering remains subject to `BUGS.md#subexpression-right-to-left-eval`
and the postponed evaluation-order policy.
Named, indexed and direct-field callable invocations share argument admission
and temporary materialization. Four lowering checks keep their existing
lambda-body restriction identical where argument temporaries cannot flush
(`BUGS.md#lambda-body-reference-argument-temp`).
Mutation after invocation proves reference arguments remain shared; paired
bounds annotations check argument-fact invalidation through indexed callbacks,
direct callable fields and ordinary methods. Methods reuse the free-call
argument invalidator, closing a stale-length-proof gap. Positions include free
functions, constructors, methods, globals, generators, async, comprehensions,
closures, context managers, `try`/`finally`, `@error_return` and `match`.
Seventeen semantic checks cover invalid indices/signatures, single receiver
analysis and protocol fields. Call-rooted receiver checks are semantic only:
the separate shared lowering gap is `BUGS.md#call-rooted-container-field-index`.
The pre-existing generic-method argument constness gap is tracked separately
as `BUGS.md#generic-method-callable-param-forced-const`; the same failure occurs
with named and direct-field callbacks, while the free-function twin works.

Validation: all 21 unit checks and both new snippet cases passed. The full
remote `--force-exec` run passed **8,028 tests, 23 skipped**, with all **4,136
executable cases** built and run. No existing snapshots changed.

**Const-inference reader extraction.** The shared reader returns recorded source
indices (`-1` for the receiver, nonnegative indices for parameters). Missing
borrow facts contribute no recorded source to these two consumers; the helper
does not replace readers that distinguish unknown provenance from no borrow.
Unknown mutation facts still remain unknown. The inherently-const-view
exception remains local to receiver inference and examines the current declared
return type without peeling new wrappers. Return-root subtraction retains its
current overlap with mutation facts; it does not claim to separate actual
mutation from borrow exposure.

The scope is metadata reading at ordinary and resumable methods. Free functions
keep their existing signature path; constructors, consuming methods, mutable
auto-readonly clones and property registry reachability keep their current
exclusions. Other statement positions and binding slots produce the same
FunctionInfo facts and require no new handling. Consumer tests cover unknown,
empty, receiver and parameter roots; reference, view, optional, tuple, union,
Own, readonly, generic and async-wrapper return shapes. Existing scalar/span
and `@nocopy` tuple-return cases check aliasing through mutation, while the
readonly, async, iterator, property and dereference corpus pins signatures.
No new acceptance rule, diagnostic, allocation or copying policy is introduced.

Validation: **26 consumer checks** and **14 existing cases** passed in the
focused run. The full remote `--force-exec` run passed **8,054 tests, 23 skipped**,
with all **4,136 executable cases** rebuilt and run. All existing diagnostic
and generated-code snapshots remained byte-identical.

**Result-representation extraction.** `classify_result_representation` in
`typesys` reads the existing synchronous-call, async-payload and erased-callable
decisions at an explicit `ResultPosition`. Its enum records representation
choices only; it is not a reduced semantic `ResultDescriptor`. In particular,
STORAGE does not mean FRESH or borrow-free. No permission or contained-borrow
field is filled with a placeholder. The complete descriptor remains required
before contract checking and rendering adopt the target rules.

Synchronous calls retain constructor and free-native exclusions and strip only
`Ref`; async payloads retain their marker/Ref/readonly normalization order and
generic trait path. Erasure retains the raw declared type's `to_cpp()` spelling,
including explicit references and tuple elements. Async renderers still read
their original types for pointer spelling and constness. Existing discrepancies
for void, wrappers, unions, native functions and erased reference results are
preserved, not presented as newly approved language rules.

The scope matrix for this extraction is:

| Axis | Coverage or exclusion |
|---|---|
| Scalar, reference, tuple (single and mixed), Optional, Union, recursive wrapper, generic, Own, readonly, markers, str/bytes, Ptr/Span, protocols | Consumer decision matrix pins all three positions without normalizing away their differences. |
| Box/Rc and other library nominals | Their existing TypeDef/value classification is delegated unchanged; the full pointer/library corpus pins their rendered uses. No new traversal into fields is introduced. |
| Free functions, methods, constructors and native calls | Metadata cases pin exclusions; existing field-return and generic function/method cases mutate returned aliases. |
| Async, including `try`/`finally` and generic/value twins | Existing async borrow, nocopy, optional and suspended-finally cases pin payload emission and shared mutations. |
| Globals, generator bodies, comprehensions, closures, context managers, `@error_return`, `match` | Calls use the same reader regardless of enclosing body; existing callable-field cases cover invocation in each. Generator yield and error-return slot classifiers are separate policies and are not extracted here. |
| Local, parameter, return, field, container element and global slots | The decision follows the callee/result type, not the destination slot. Existing alias, tuple-return and callable-container cases cover consumption; no slot/coercion logic changes. |

The pitfalls checks preserve existing acceptance, diagnostics, evaluation order,
copy/view/allocation choices and constness through byte-identical snapshots.
Mutation-sensitive execution cases and nocopy payloads distinguish aliases from
copies; decision checks pin generic and monomorphic shapes without claiming
their existing differences are fixed. No diagnostic strings or formatting rules
change. The existing callable-result copying and async-union defects remain
tracked in `BUGS.md`; fixing them requires the later contract/consumer work.

Validation: **49 consumer checks** and **15 existing cases** passed in the
focused run (14 executable cases rebuilt and run). The full remote
`--force-exec` run passed **8,103 tests, 23 skipped**, with all **4,136 executable
cases** rebuilt and run. Existing sources and expected snapshots were unchanged.

### Detailed work inventory

The execution checkpoints above determine the landing order. Items 1-5 are
independently mergeable, each with its own acceptance test; everything from
item 6 on is coupled and lands as one branch only after the compatibility gate
above is satisfied.

**Independent prerequisites and refactors.**

1. **P0.1 -- `expr.lambda` span + blocking element.** The shared lambda routing
   predicate supplies the cause; `note_detail` carries its source location.
   Lambda lowering adds capture/body causes, and `ThirUnsupported.with_context`
   preserves the located rejection through enclosing expressions. Tests cover
   parameter/return tuple shapes, captures and body failures, with multi-line
   locations and supported named-function/lambda twins. Pure diagnostic
   improvement, no design dependency.
2. **The const-inference reader unification.** Codegen's subtraction of return
   roots from `mutated_params` and sema's receiver auto-const gate share
   `recorded_return_borrow_sources` in `typesys`. The audit found related readers
   with different policies: codegen subtracts every recorded root; sema blocks
   auto-const for a receiver root except at an inherently const view return.
   Preserve both policies, including their treatment of missing facts. Pure
   refactor, zero snapshot churn.
3. **P0.2 -- a lambda at a container-element binding gets its parameter types.**
   Recover the callable shape through `Own` in contextual analysis and preserve
   per-element tuple hints. Admit callable element replacement through the
   shared THIR container-write path, including optional slots. Container
   methods and subscript writes then use the same known callable signature.
4. **P0.3 -- a `Callable` in a dict field is callable at all.**
   Preserve the indexed-callee alternative and resolve fields before callable
   invocation. Reuse `analyze_callable_value_call` and computed-call lowering,
   including argument coercion and opaque-call mutation invalidation, while
   preserving generic-method resolution and single receiver analysis.
5. **The existing representation decisions.** `ResultPosition`,
   `ResultRepresentation` and `classify_result_representation` live in
   `tpyc/typesys.py` beside the Ref helpers. `async_return_form` and
   `call_returns_cpp_ref` in `value_category` are thin consumers;
   `CallableType._std_function_sig` reads the same decider. Nothing is persisted
   or inferred beyond existing type/declaration facts. Acceptance test:
   byte-identical snapshots. The full `ResultForm` / `ResultDescriptor`, including
   permission and contained-borrow analysis, is designed at checkpoint 3 and
   implemented with the coupled branch after that gate passes.
6. **`@native_borrow` plumbing WITHOUT annotating a stub.** The decorator stub
   (`lib/tpy/tpy/_bootstrap/_extern.py:31`), the qname (`tpyc/qnames.py:157`), the
   parse field (`tpyc/parse/nodes.py:1454`), the decorator branch in BOTH parser
   loops (the free loop has no `native_preserves_refs` branch today), the
   tuple-of-names schema kind, and the `FunctionInfo` stamp at
   `tpyc/sema/registration.py:1534` / `:3477-3505`. Inert until a stub carries it.

**The coupled branch.**

7. **Runtime traits.** `result_slot_t` / `to_result_slot` / `result_neutral` in
   `runtime/cpp/include/tpy/type_traits.hpp` after `val_or_ptr` (`:214-220`) and the
   tuple slot (`:282-322`); the `static_assert` mirrors `:317`. Land with step 11,
   which is its first consumer.
8. **The lambda / nested-`def` result stamp and the dangle switch.** Extend
   `returns_borrow_rooted_at_self` (`tpyc/parse/nodes.py:2102`) with the union over
   return paths and the mixed-path reject; switch
   `tpyc/sema/expressions.py:4196` / `:4212` from `check_view_return_dangle`
   (`tpyc/sema/compatibility.py:3009`) to `check_dangling_reference` (`:3027`), with
   callable-framed text replacing `:3134` / `:3165`; the yield caller
   (`tpyc/sema/statements.py:3968`) is untouched.
9. **The admission layer (A1-A12).** The argument-root proof, the non-escape marker,
   the conservative argument loans over `return_borrows_from`, the
   unknown-effect-under-live-loan check at the invocation, and the global,
   temporary and aggregate rejects.
10. **Conversion legality.** One checker keyed on the source (`function_ref_info`,
    `tpyc/sema/compatibility.py:468`), invoked after overload selection at
    `_typecheck_and_coerce_arg` and at each non-argument binding. The six stripping
    sites of rule 8 stay and agree; form enters `_classify_strict_match`
    (`tpyc/sema/overloads.py:235`) and the `__call__` conformance loop
    (`compatibility.py:996`).
11. **Codegen / THIR.** The per-form `requires` at `_gen_fn_template_parts`
    (`tpyc/codegen_cpp/functions.py:334`, `ret_cpp` at `:357`; today's render for an
    `Fn[[Node], Node]` contract is `{ __fn(__a0) } -> std::convertible_to<Node>;`
    with no category check); the erased render at `_std_function_sig`
    (`tpyc/typesys.py:4529`); the descriptor on `THIRCall` (`tpyc/thir/nodes.py:570`)
    stamped at both lowering arms (`tpyc/thir/lower/expressions.py:7684` and
    `:8749` via `tpyc/thir/lower/checks.py:3825-3836`, where `fi` is non-None because
    `analyze_callable_value_call` sets a synthetic `is_callable_value` FI at
    `tpyc/sema/calls.py:6039-6045`); the bare reference bind at a concrete borrow
    contract and the slot local, its `.get()` reads and the `decltype(auto)` return
    at a neutral one, in `tpyc/thir/emit.py`.
12. **The reject rows.** One `error_` case each, messages verbatim from the tables
    above.
13. **Stubs.** `lib/tpy/tpy/_builtins/_funcs.py:125` (`min`), `:174` (`max`), `:525`
    (`sorted`): key respelled `Fn[[T], Own[K]]`, `@native_borrow` added;
    `runtime/cpp/include/tpy/builtins.hpp:319`, `:334` `const T&` -> `T&`.
14. **Tests and docs.** A condensed happy case per position, the migration
    respellings, and the `docs/LANGUAGE_FEATURES.md` result-form section with the
    divergence table, the "result borrows only argument 0" spelling gap and the
    loop-filled-registry shape.

**Estimate (incomplete subtotal, not a release estimate).** The contract-half
rows of the implementation dry run's table sum to
**49 engineer-days** (P0.1 1, P0.2 2, P0.3 4, classifier 4, runtime traits 1.5,
reader unification 3, `@native_borrow` 3, conversion legality 7, codegen/THIR 8,
rejects 6, stubs 2.5, tests+docs 7). That number EXCLUDES the four dataflow steps
of the original 79 -- places and summaries, environment loans, effects, and the
lambda stamp's provenance half, 30 days between them -- which move to the
requirements document; the admission layer (step 9) is a conservative subset of
them whose cost is not in that table and is estimated when its shape is settled.

**The riskiest step is codegen/THIR.** Rule 24 makes sema the only backstop: the
`const B&` erasure row ADMITS a fresh lambda on clang 19, the project minimum, so
any hole left by step 10 becomes a silent miscompile no toolchain reports --
compounded by the `decltype(auto)` signature change and 90 snapshot files of
`requires` churn that can mask a real divergence in review.

## Rollout preconditions

This half does not roll out until the compatibility gate passes and three
sema/THIR gaps close: rules 7 and 23 and
the Migration measurement all presume them, and the blast radius is otherwise
measured against a corpus that cannot spell the shapes.

1. The `expr.lambda` THIR reject must carry the lambda's SPAN and name its blocking
   element -- otherwise the commonest Python callback,
   `sorted(xs, key=lambda s: s.name)` over a `str` key, dies with a message that
   says nothing about why. Its NAMED twin already compiles, so the gate is only
   `expr.lambda`.
2. A lambda at a CONTAINER-ELEMENT binding must get its parameter types.
   `bus.listeners.append(lambda e: print(e.code))` uses the list's callable
   element type as context, including through the consuming `Own` parameter.
   Rule 7 names that binding as one it checks; signature inference alone does
   not promise lowering for every lambda body or capture shape.
3. A `Callable` held in a DICT FIELD must be callable at all.
   `self.commands[name](arg)` follows ordinary field lookup and indexing before
   invoking the stored callable. This prerequisite is documented in
   `docs/LANGUAGE_FEATURES.md`; it does not introduce a new callable contract.

## Later slices

- **D1 -- more permissive owning-slot copy obligations.** The per-instantiation
  FORM infrastructure is required by rules 26/27 in checkpoint 3, before the
  contract ships; only the later relaxation to a copy-warning obligation is
  deferred here. The initial infrastructure needs three
  pieces the own-copy channel lacks: a second openness predicate beside
  `_still_open` / `type_has_type_param` (`tpyc/sema/own_copy.py:218`, `:158`), a form
  composition rule in the forwards loop (`:246-249`) parallel to `substitute_types`,
  and form-aware keys at edge, root, forward and discharge deduplication sites.
  Up to 2^k form assignments remain possible for k independent callback positions;
  folding form into the existing tuple key does not remove that bound. Explore
  reachable states rather than enumerating the product. Keep selecting-call-site
  origins separate from canonical states so repeated instantiations retain their
  own diagnostics. Also required is a root-vs-forward split that does
  not file a concrete-types / open-form edge as a root (`tpyc/sema/context.py:1542`).
  The call site CAN supply the form (`record_own_copy_instantiation` runs at the call
  node with the arguments in hand, `tpyc/sema/calls.py:5290`;
  `defer_own_copy_verdict` at `tpyc/sema/context.py:1458` is the existing channel).
  The feasibility report adds an 8-13 engineer-day estimate for this channel's
  integration/diagnostics, excluding slots, descriptor analysis, provenance and
  native move-assignment traits; it is not part of the incomplete 49-day subtotal.
  D1's later use of that infrastructure lifts
  rule 27 and the open-type-parameter over-reject, and closes the callable leg of
  `BUGS.md#generic-own-slot-borrow-call-unwarned`.
- **D2 -- the consuming fold, with a Python-compatible `reduce` designed
  separately.** The consuming fold is `Fn[[Own[U], T], Own[U]]` with
  `initial: Own[U]` and `-> Own[U]` -- the accumulator moves in and back out, the
  only shape that works for a `@nocopy` accumulator, and it needs `Own[U]` at a
  callback PARAMETER position. Python's `reduce` is a different function (the
  accumulator BORROWS the seed, may OWN later results, and empty input returns the
  seed itself) needing a borrow-or-own state model, so the two are separate APIs and
  neither ships here.
- **D3 (stored-`Callable` capture lifetimes) and D4 (a neutral result at a `yield`,
  and callable loans across a frame)** are provenance work; they are stated in
  `docs/CALLABLE_PROVENANCE_REQUIREMENTS.md`.

Unchanged and still out of scope: method, constructor and staticmethod references
at a callable slot (current errors stand); form-carrying `U`, rejected below.

## Alternatives considered and rejected

- **Per-call-site copy adapters:** the adapter captures by reference and escapes into a `std::function`.
- **Bare `Fn` follows the callee, bare erased `Callable` is fresh:** one spelling with two readings.
- **Bare is `Own`:** rejects `lambda x: x`, `lambda p: p.child` and every accessor callback.
- **Form-carrying `U`** (binding `U := Ref[X]`): makes `list[U]` a container of borrows.
- **Two constrained templates per body:** k callable parameters give 2^k bodies; the slot gives one.
- **A bare-alias slot (`T` / `T*`):** cannot tell a borrow slot from a `Ptr[R]` payload slot, so the deref has to sniff a shape (rule 26's wrapper puts the distinction in the type).
- **Program-lifetime storage as a safe root:** confuses the storage's duration with the value's (A5).
- **A lambda at a fresh contract as a rule-5 mismatch:** turns every reference-typed `key=` lambda into an unspellable error; a lambda has no declared form to mismatch (rule 32).
- **`convertible_to` alone, or the `std::function` conversion, as the C++ backstop:** the first tests conversion and not provenance (rule 31); the second is toolchain-dependent and wrong on the project's minimum clang (rule 24).
- **Building the provenance analysis against the AST to ship it with this half:** its alias propagation, branch snapshots and lifetime bookkeeping would be rebuilt on MIR, and the IR plan already names that "writing MIR badly, twice".
