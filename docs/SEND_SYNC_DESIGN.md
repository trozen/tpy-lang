# Send / Sync Design

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | `is_send` / `is_sync` traits on all built-in types (runtime + sema), auto-derivation for user records (fields + base class), `Send` / `Sync` marker `Protocol`s in `tpy` | Done |
| **Phase 2** | Marker-layer gap closing (closures, coroutine/generator frames, union forms), tightened rules, `Send[T]` / `Sync[T]` marker wrappers covering `Callable[...]` and `@dynamic` protocols, explicit opt-in / opt-out syntax, `# tpyc:` annotations + test group locking auto-derivation | Done (per-record C++ trait mirror deferred -- see Implementation Notes) |
| **Phase 3** | Diagnostic surface (`tpy.assert_send[T]`, `--explain-send`, inline chain at enforcement sites) | Done |
| **Phase 4** | First enforcement site: `Channel[T]` (intra-process, `T: Send`) on top of the single-threaded executor | Done (async `tpy.channel` is SPSC; its multi-producer form is deferred. The separate *blocking cross-thread* MPSC channel `tplib.channel` shipped on top of Phase 6 -- see the Phase 6 row and `docs/CHANNEL_DESIGN.md`) |
| **Phase 4.5** | Second enforcement site: real OS threads -- `tpy.thread.spawn` (Runnable-struct form) Send-checks the task (`Send[Own[T]]`) and result (`R: Send`) at the call site | Done (V1; closure-based `spawn` deferred -- see `docs/THREADING_DESIGN.md`) |
| **Phase 5** | Multi-threaded *async* executor; `Task[T]` requires `Send` frame for migration; closure-based `thread.spawn(fn)` requiring `Send` closure | Planned (v3+) |
| **Phase 6** | `Arc[T]` (atomic shared ownership) + the conditional Send/Sync override it needs -- Done; synchronization primitives (`Mutex[T]`, `RwLock[T]`, `Send + Sync` iff `T: Send`, in `tpy.sync`) -- Done (see `docs/THREADING_DESIGN.md` V3); `Condvar` -- Done; blocking cross-thread MPSC channel (`tplib.channel`, `Arc[Mutex[ring]]` + two `Condvar`s, `channel[T: Send]`) -- Done (see `docs/CHANNEL_DESIGN.md`); `Sync`-required borrow sites -- Planned (v3+) |

Phases 2 and 3 are independent of any concurrency runtime and should land first. Phase 4 forces the open questions in Phase 2/3 to be answered against a concrete enforcement site; the rest is gated on the multi-threaded executor decision in `docs/ASYNC_DESIGN.md`.

Phase 2 landed on the `send-sync-phase2` branch (steps A-F: tightenings + test annotations, wrappers + bounds, FrameType, decorator kit, `Send[Pet]`, runtime span traits). Naming note: the doc's pre-implementation text says `Coroutine[T]` for the async call-result type; the implemented surface type is `Cancellable[T]` (`sema/registration.py` wraps every async def's return type in it).

## Problem Statement

TurboPython targets low-latency workloads where pipeline stages typically run on dedicated cores and communicate through queues. The compiler must be able to reject programs that transfer or share state across threads in ways that race, *without* requiring annotations on the 95% of types that are trivially thread-safe (value types) or trivially not (raw pointers, non-owning views).

The Rust experience is that these markers are very expensive to retrofit -- Swift's `Sendable` rollout took years of warnings precisely because the type-system shape had to change after concurrency primitives had already shipped. The TPy decision is to design and ship the type-level layer *before* any concurrency primitive lands, then plug enforcement sites in as concurrency arrives. Phase 1 already shipped the trait skeleton; this document specifies the rest.

The design also constrains future answers to existing open questions in other areas (async multi-thread executor, dynamic protocol adapters, ownership transfer) so they remain consistent with the marker model.

## Conceptual Model

Send and Sync are TPy adaptations of the Rust markers, with the differences spelled out explicitly.

- **Send** -- *transferring ownership of a value of this type from one thread to another is data-race-free*. In TPy this is the question asked at every move boundary that crosses a thread (channel send, `thread.spawn` closure capture, Task migration). It is **not** asked at function calls or returns within a thread.

- **Sync** -- *holding multiple `Ptr[readonly[T]]` to the same `T` from different threads is data-race-free*. Equivalent to Rust's `Sync` defined as "`&T` is `Send`." Only readonly aliasing matters at the thread boundary; mutable cross-thread aliasing is forbidden by **absence** (no Send-tagged `Ptr[T]` exists for non-Sync `T`), not by an external rule.

**Within a thread, TPy allows aliased mutation** (`docs/SAFETY_MODEL.md` "Aliased Mutation (No Restriction)"). Send/Sync is the *only* alias-control mechanism in the language, and it activates *exclusively* at thread boundaries (channel sends, spawn, task migration). Two `Ptr[T]` handles to the same object inside a single thread are routine; the markers don't compose with a within-thread rule because no such rule exists.

The model is **conservative on by default**: `is_value_type(T)` implies both Send and Sync, because value types are copied at every boundary. Everything else has to either be reachable through structural rules (auto-derivation) or explicitly opted in.

### Differences from Rust

- **No interior mutability primitive.** TPy has no `Cell`/`RefCell`/`UnsafeCell`, so we never need the "T is Send but not Sync because of interior mutability" branch as a built-in. User types that want shared-mutable semantics must reach for `Mutex[T]` (Phase 6) which encodes the Sync uplift explicitly.

- **`readonly[T]` is a borrow-side attribute, not a deep freeze.** A `readonly[list[int]]` parameter promises *this handle* won't mutate, but the underlying list may still be mutated through another (non-readonly) handle elsewhere. This bounds what `readonly[T]` can lift to Sync (see Open Question 1 below).

- **`Ptr[T]` is always non-Send, non-Sync** -- raw pointer, no ownership guarantee. `Ptr[readonly[T]]` is Sync iff `T` is Sync (multiple read-only views are fine if the underlying type's shared state is fine).

- **`Own[T]` is Send iff `T` is Send** -- ownership transfer is the canonical Send operation.

- **No `unsafe` block** -- escape hatches are decorator-based (`@unsafe_send` -- Open Question 4) and visible at the type definition site, not at the use site.

## Type-Level Rules

The table consolidates what's in `runtime/cpp/include/tpy/type_traits.hpp`, `tpyc/type_def_registry.py`, and `tpyc/typesys.py`.

| Type form | Send | Sync | Notes |
|-----------|------|------|-------|
| Owning value types (`int`, `int32`, `bool`, `float`, `char`, ...) | Yes | Yes | Copied at every boundary; no aliasing |
| Dual-form value types (`str`, `bytes`) | Yes | Yes | Immutable. TPy storage form is owned (`std::string`, `::tpy::Bytes`); param form is a borrow view (`std::string_view`, `::tpy::BytesView`). Send-ness applies to the storage form -- transferring `str` ownership moves the underlying buffer. Frame slots that store the borrow form follow the OQ3 storage-form rules. |
| `bytearray` | Yes | No | Mutable buffer (`::tpy::ByteArray`) -- same Sync rule as `list[T]`. A reference type, so neither trait defaults on; the registry carries an explicit `is_send=True`, `is_sync=False` and the runtime spells the Send row beside the type (the `std::vector` partial specialization does not match a derived class). |
| Pure non-owning views (`tpy.StrView`, `tpy.BytesView`) | No | Yes | Pure view types; always borrow originating-thread storage. Sema explicitly overrides `is_send=False, is_sync=True` on their TypeDefs in `type_def_registry.py`. |
| `Ptr[T]` | No | No | Raw mutable pointer, no ownership guarantee |
| `Ptr[readonly[T]]` | No | Yes (if `T` Sync) | Read-only shared access |
| `Own[T]` | If `T` Send | No (single-owner; tightened from current `wrapped.is_sync()` -- see Implementation Notes) | Move-only single-owner; sharing readonly aliases of an `Own[T]` slot doesn't make semantic sense |
| `list[T]` | If `T` Send | No | Mutable container |
| `dict[K, V]` | If `K`, `V` Send | No | Mutable container |
| `set[T]` | If `T` Send | No | Mutable container |
| `Array[T, N]` | If `T` Send | If `T` Sync | Fixed-size, inline |
| `Span[T]` | No | No | Non-owning mutable view |
| `Span[readonly[T]]` | No | If `T` Sync | Non-owning read-only view |
| `SpanIter[T]` | No | No | Has mutable index state |
| `tuple[T1, ..., Tn]` | If all `Ti` Send | If all `Ti` Sync | Composite |
| `T1 \| T2` (UnionType) | If both Send | If both Sync | AND over members |
| `readonly[T]` | Same as `T` | If `T` Sync (OQ1 decision; was Send→Sync lift) | OQ1 |
| `Callable[[...], R]` (`std::function`) | No | No | Use `Send[Callable[...]]` for the Send-tagged form (OQ2) |
| `Send[T]` (marker wrapper) | Always Yes (sema-side assertion on the value being wrapped; for erased `T` like `Callable`/`@dynamic` protocols, the assertion fires at the construction site that creates the erased value) | Same as `T` | OQ2; mirrors `readonly[T]` shape; no runtime representation change |
| `Sync[T]` (marker wrapper) | Same as `T` | Always Yes (asserted at construction) | Parallels `Send[T]` |
| `Send[Pet]` (where `Pet` is `@dynamic`) | Yes | Same as `Pet` | OQ5; sema-only wrapper -- Send check fires at the construction site that creates the Adapter; C++ representation is the same `tpy::Adapter<Pet, T>` / `tpy::RefAdapter<Pet, T>` as bare `Pet`. Storage form gates on Box[P] |
| `Fn[[...], R]` (template) | n/a | n/a | Lowers to a template parameter; Send/Sync checked at the call site against the inferred concrete callable |
| `Coroutine[T]` (async frame) | If its FrameType is Send | No (frames have mutable state) | OQ3; wraps a FrameType internally |
| `Iterator[T]` from generator function | If its FrameType is Send | No | OQ3; wraps a FrameType internally |
| Lambda / closure (concrete) | If its FrameType is Send | If its FrameType is Sync | OQ3; FrameType is anonymous, surfaces via `Fn[F: Send]` bound or `Send[Callable[...]]` wrapper |
| `Task[T]` | Send iff its frame is Send (Phase 5) | No | Frame is on heap behind type erasure; Send-ness encoded into `Task` type (Phase 5) |
| `FrameType` (internal) | AND over slot types | AND over slot types | Captured-state structural form covering coroutines, generators, lambdas, closures (OQ3) |
| `Rc[T]` | No | No | Non-atomic refcount; single-threaded by construction |
| `Arc[T]` (Phase 6) | If `T` Send + Sync | If `T` Send + Sync | Matches Rust |
| User record | All fields Send AND base Send (or no base) | All fields Sync AND base Sync (or no base) | Auto-derived; can be overridden via opt-in / opt-out (Open Question 4) |
| Generic record at instantiation | Type-param-substituted field rule | Same | A generic record's Send-ness is computed per concrete instantiation -- field types are walked with `TypeParamRef`s resolved |

## Auto-Derivation Algorithm

Currently in `tpyc/sema/registration.py`. Walking the same algorithm into design space, with the additions Phase 2 introduces:

```
is_send(record) =
    is_send(base_class)  if base exists, else True
    AND for every field f: is_send(f.type) OR is_type_param(f.type)
    AND not has_unsafe_not_send annotation
    AND (no explicit Send claim required for auto-derive)

is_sync(record) =
    is_sync(base_class)  if base exists, else True
    AND for every field f: is_sync(f.type) OR is_type_param(f.type)
    AND not has_unsafe_not_sync annotation
```

Type parameters in field types (`x: T` inside `class Box[T]`) are treated as conformant during the record's structural check; the actual answer is computed per instantiation, when `NominalType.is_send()` / `is_sync()` walks the record's fields and parents under concrete `type_args` and re-applies the per-field predicate. The walker runs on every query for generic records (non-generic records use the cached registration-time bool), and a re-entrancy guard handles self-referential generics like `class Tree[T]: children: list[Tree[T]]` via greatest-fixed-point semantics.

**Phase 2 addition:** if the user writes `class Foo(Send): ...` (or `class Foo(Send, Sync): ...`), the compiler asserts the structural derivation also yields true and emits an error if not. This is the explicit-opt-in form -- "I claim Foo is Send; please check it." It does *not* unsafely override the structural answer.

**Phase 2 addition:** `@unsafe_send` / `@unsafe_sync` decorators force the structural answer to true regardless of fields (opt-in *unsafe*); `@nosend` / `@nosync` decorators force the structural answer to false regardless of fields (opt-out). All four are visible at the definition site, greppable, and reviewable. See OQ4 for the full kit and mutual-exclusion rules.

## Design Decisions

All six design questions are decided. Summary:

| # | Question | Decision |
|---|----------|----------|
| OQ1 | `readonly[T].is_sync()` lift rule | Tighten to `wrapped.is_sync()` (drop the Send->Sync lift) |
| OQ2 | Erased-Callable Send-ness | Introduce `Send[T]` marker wrapper (mirrors `readonly[T]`); `T: Send` bound for generic params |
| OQ3 | Coroutine / generator / lambda / closure frames | Unified internal `FrameType` -- structural slot walk; generator-codegen migration is independent |
| OQ4 | Opt-in / opt-out syntax | Full kit: `class Foo(Send): ...` opt-in + `@unsafe_send` / `@unsafe_sync` + `@nosend` / `@nosync`; mutually exclusive per target (record markers conflict on the record; function decorators conflict on that function's FrameType) |
| OQ5 | `@dynamic` protocol Send-ness | `Send[Pet]` -- valid wherever bare `Pet` is valid (params/locals). No compiler dependency on Box; storage form composes with `Box[Send[Pet]]` (Box[P] has shipped) |
| OQ6 | Diagnostic surface | Inline chain at enforcement + `tpy.assert_send[T]()` / `assert_sync[T]()` + `tpyc --explain-send T` (all share one chain-walker) |

Each is expanded below with semantics and the alternatives that were rejected.

### OQ1. `readonly[T].is_sync()` -- tighten the current rule? **[DECIDED: tighten]**

**Phase 1 rule** (`ReadonlyType.is_sync` in `tpyc/typesys.py`): `wrapped.is_send() or wrapped.is_sync()`. Reasoning was "Send means no aliasing; freezing via readonly removes the mutation, so it's safe to share." **Tightened in Phase 2 step A.**

**Problem:** `readonly` is a borrow-side restriction, not a deep freeze. A `readonly[list[int]]` parameter is reachable through other non-readonly aliases. From the perspective of "is it safe for two threads to hold this *handle*", yes -- but only if the underlying object is in fact Sync. Today `list[int]` is not Sync, so the lift is unsound.

**Decision:** `readonly[T]` is Sync iff `T` is Sync. The "freeze" property does not extend across the readonly boundary because the object may be aliased elsewhere. Phase 2 tightens `ReadonlyType.is_sync()` to `wrapped.is_sync()` and adds a test pinning `readonly[list[int32]]` as **not** Sync.

**Alternative considered (rejected):** keep the lift, but require `readonly[T]` only be Sync-uplifted in contexts where the compiler can prove no mutable alias exists (escape analysis). Too expensive to deliver now; defer.

### OQ2. `Callable[[...], R]` -- erased closures **[DECIDED: Send[T] marker wrapper]**

**Phase 1 state:** `CallableType` (`tpyc/typesys.py`) had `is_value_type() = True` and no `is_send` / `is_sync` override, so the default rule returned Send=True / Sync=True for every `Callable[...]`. This was **wrong** -- erased callables can capture non-Send state (the C++ `std::function` carries the captured closure, which may borrow originating-thread memory). Phase 2 step A explicitly overrides `CallableType.is_send() = is_sync() = False` for bare `Callable[...]`; opting into Send is via the `Send[Callable[...]]` wrapper. This also addresses the "callback field on a sendable record" pattern, where there is no `Fn[...]` workaround (Fn is template-only, not valid in field position).

**Decision:** Introduce a marker-wrapper type `Send[T]` that asserts Send-ness at every construction site. Mirrors `readonly[T]`'s shape -- standard Python subscript syntax, no new operators. **Sema-only wrapper -- no new C++ templates emitted.** The C++ representation of `Send[T]` is identical to bare `T`; the Send-ness is enforced at construction-site / boundary points by sema (channel send, spawn, etc.). Same machinery solves OQ5 for `@dynamic` protocols: `Send[Pet]` is the type-system tag that requires the concrete `T` to be Send when the Adapter is constructed.

```python
ch: Channel[Send[Callable[[int], None]]]
ch.send(lambda n: print(captured_int, n))   # checked at lambda -> Send[Callable] conversion

class CompletionHandler:
    on_done: Send[Callable[[int], None]]    # field; Handler can be Send because Send[...] is Send
```

**Semantics:**

- For **erased types** (`Callable[...]` and `@dynamic` protocols): `Send[T]` is a sema-only assertion on the concrete impl. The Send check resolves to FrameType.is_send (OQ3) when the concrete impl is a lambda or closure -- structural slot walk. **No new C++ templates required** -- the C++ representation of `Send[Callable[...]]` is `std::function<...>` (same as bare `Callable[...]`); the C++ representation of `Send[Pet]` is `tpy::Adapter<Pet, T>` / `tpy::RefAdapter<Pet, T>` (same as bare `Pet`). Sema enforces Send-ness at boundary points (construction site, channel send, spawn) before the value is erased. The existing `tpy::Send<T>` / `tpy::Sync<T>` C++ concepts (Phase 1, `type_traits.hpp`) remain available for hand-written C++ to constrain on. Note: `Adapter[P]` is **not** a TPy-level surface -- the user writes `Send[Callable[...]]` and `Send[P]` directly; `Adapter` is a C++ codegen detail (see OQ5).
- For **non-erased types**: `Send[T]` is a static assertion -- "this slot only accepts conformers that are Send." `Send[int32]` is just `int32` (assertion holds trivially); `Send[Ptr[Buf]]` is a compile error.
- Stackable: `Send[Sync[T]]` is well-defined and canonicalized.
- Reuses the existing `Send` Protocol name -- consistent with Rust (`Send` is both the trait and the marker in `dyn Trait + Send`) and Python typing (`Callable` is both a base class and a subscriptable type). The two readings agree: both answer "is this Send?" from different sides.

**Bound vs wrapper in generic-param position.** Two forms appear, with different meanings:

```python
# Bare marker bound: "T must be Send" (single constraint)
class Channel[T: Send]: ...
def spawn[F: Send](fn: F) -> Thread: ...

# Wrapper bound: "F must implement Pet AND be Send"
def spawn_pet[F: Send[Pet]](pet: F) -> Thread: ...
```

`T: Send` (the bound) and `Send[T]` (the wrapper) are not the same thing:

- `T: Send` is a single marker constraint applied at the type-parameter declaration site. Rust's `T: Send` exactly.
- `Send[Pet]` is the wrapper type asserting "implements Pet AND is Send." When used as a bound (`[F: Send[Pet]]`), it is a conjunction of two constraints, no different from what Rust's `[F: Pet + Send]` expresses. Conjunction is encoded by the wrapper because Python typing has no `&` operator.

Outside of generic-param position the bound form doesn't apply -- only the wrapper. Mirrors Rust's `T: Send` (bound only) vs `dyn Trait + Send` (wrapper-position only).

**Alternatives considered (rejected):**

- *(A)* Callable always non-Send; force record-functor workaround for callback fields. **Rejected**: limits users -- callback-field-on-record is a common pattern (completion handlers, event subscribers, callbacks) and `Fn[...]` is not valid in field position. The record-functor workaround is verbose and viral.
- *(B)* Parallel `SendCallable[[...], R]` type. **Rejected**: solves OQ2 only, doesn't generalize to `@dynamic` adapters (OQ5) or future erased types. Naming doesn't scale (`SendSyncCallable`?).
- *(C)* `T1 & Send` intersection operator (Rust-shaped). **Rejected**: introduces a new type-level operator that diverges from Python typing grammar. See [[language-design-versatile-pythonic]] memory.

See [[language-design-versatile-pythonic]] for the design preference that drove this choice.

#### `Send[T]` / `Sync[T]` canonicalization with other wrappers

`Sync[T]` mirrors `Send[T]` with the same dual semantics (sema-side assertion on non-erased `T`; sema-side assertion at the erased-value construction site for `Callable`/`@dynamic` `T`), the same canonicalization rules, and the same auto-derive integration. Everything below applies to both wrappers.

**Canonical wrapper order at resolve time** (mirrors `readonly[T]`'s existing normalization, e.g. `readonly[T | None]` -> `readonly[T] | None` -- which also lives in the type resolver, not the parser; implemented by `make_send_marker` / `make_sync_marker` in `typesys.py` called from the resolver's wrapper arms):

| Outer (parsed) | Canonical (after resolve-time rewrite) | Note |
|-----------------|-------------------------------------|------|
| `Send[T \| None]` | `Send[T] \| None` | Send distributes over union; doesn't apply to None |
| `Send[A \| B]` | `Send[A] \| Send[B]` | Send distributes over union |
| `readonly[Send[T]]` | `Send[readonly[T]]` | Send floats outermost |
| `Send[readonly[T]]` | `Send[readonly[T]]` | canonical |
| `Send[Own[T]]` | `Send[Own[T]]` | canonical; Own is the move-form of T, Send asks about the move |
| `Own[Send[T]]` | `Send[Own[T]]` | Send floats outermost |
| `Send[Send[T]]` | `Send[T]` | idempotent |
| `Send[Sync[T]]` | `Send[Sync[T]]` | canonical; both wrappers stack outermost-first (Send before Sync alphabetically) |
| `Sync[Send[T]]` | `Send[Sync[T]]` | normalize to alphabetical when both present |

**Position rules:**

- `Send[T]` / `Sync[T]` are valid in every position a non-wrapped type is valid: params, locals, returns, fields, type-args, generic bounds.
- For non-erased `T` the wrapper is a **static assertion** (`Send[int32]` == `int32`; `Send[Ptr[Buf]]` is a compile error).
- For erased `T` (`Callable`, `@dynamic` protocol like `Pet`) the wrapper is a **sema-side Send/Sync assertion** on the concrete impl that gets erased -- see OQ2 / OQ5. No runtime representation change; the C++ type is the same as bare `T`.
- Canonicalization happens once at resolve time; sema and codegen see canonical forms only. The type-printer emits canonical forms back to the user. Generic substitution re-canonicalizes (a `Send[T]` wrapper erases when the substituted concrete type is statically Send).

**Why canonical-at-resolve:** matches the `readonly[T | None]` precedent already shipped; gives one canonical representation per type so structural equality and the typesys.is_send/is_sync rules stay simple. Avoids "are these equivalent" comparisons at sema time.

### OQ3. Captured-state Send/Sync -- coroutines, generators, lambdas, closures **[DECIDED: unified FrameType]**

**Current state:** `async def f`, generator functions, lambdas, and nested closures each lower to a struct (or C++ lambda) holding captured state plus a method. None of these structs are first-class `TpyType`s in sema; `Coroutine[T]` / `Iterator[T]` / `Callable[...]` are surface types that wrap them but don't expose slot types.

**Why it matters:** every Send/Sync question about these constructs reduces to "AND over captured-slot Send/Sync." Without a typesys handle on the slot list, the compiler can only answer "always non-Send" (over-conservative) or "always Send" (unsound), and diagnostics cannot point at the offending capture.

**Decision: unified `FrameType` covering all four surfaces.** All four compile to "struct with captured slots + some method"; the typesys answer is structurally identical:

| Surface | Captured state | Synthesized method | User-facing wrapper |
|---------|----------------|--------------------|--------------------|
| `async def f(...)` | params + locals-across-await + sub-await storage | `poll(waker) -> Poll[T]` | `Coroutine[T]` |
| `def g(...): yield ...` | params + locals-across-yield | `__next__() -> T` | `Iterator[T]` |
| `lambda x: x + cap` | free vars from enclosing scope | `__call__(...)` | `Callable[...]` / `Fn[...]` |
| Nested `def inner(): ... cap` | free vars from enclosing scope | `__call__(...)` | `Callable[...]` / `Fn[...]` |

One typesys form, one `is_send` / `is_sync` rule:

```python
class FrameType(TpyType):
    slots: tuple[(name, slot_type), ...]   # slot_type is the **storage form** of the C++ struct field
    kind: FrameKind  # COROUTINE | GENERATOR | CLOSURE  (drives diagnostic phrasing)

    def is_send(self) -> bool:
        return all(t.is_send() for _, t in self.slots)
    def is_sync(self) -> bool:
        return all(t.is_sync() for _, t in self.slots)
```

This collapses OQ2 and OQ3 into the same machinery: OQ2's `Send[T]` wrapper and `T: Send` bound *ask* the question; FrameType *answers* it for any anonymous captured-state value. The user-facing types (`Coroutine[T]`, `Iterator[T]`, `Callable[...]`) stay unchanged; FrameType is the internal "concrete impl" the wrappers query.

**Storage-form (not param-type) semantics.** FrameType slots reflect the literal C++ struct field types, not the user-written param annotations. The slot shape varies by what's being stored:

| Source | Slot shape | File |
|--------|-----------|------|
| Non-value params (`list[int]`, `Pet`, user records) | `T&` reference | `gen_async.py:266`, `:657`; `gen_generators.py:666`, `:695` |
| `Own[T]` params (move-in, owned storage) | `T` by value | -- |
| `str` params (`std::string_view` -- non-owning view) | `std::string_view` | `gen_async.py:280`, `gen_generators.py:678` |
| `String` params (`std::string` -- owned string, from `lib/tpy/tpy/_core/_types.py:1511`) | `String` by value | -- |
| Hoisted non-value locals across await/yield | `tpy::frame_slot<T>` | `gen_async.py:664`, `gen_generators.py:702` |
| Borrowed async awaitables | raw pointer | `gen_async.py:679` |
| Value-type params (`int32`, `bool`, `float`, `char`, ...) | `T` by value | -- |

The Send rule walks whatever slot shape is actually emitted:

- Reference slots (`T&`, raw pointer) are non-Send -- alias originating-thread memory.
- `std::string_view` slots are non-Send (borrow originating-thread storage) but Sync (read-only view) -- matches the StrView row in the rules table.
- `tpy::frame_slot<T>` slots reduce to T's Send-ness (owned storage).
- Value-type slots and `T`-by-value slots are Send iff `T` is Send.

Consequence: most async/generator/closure functions today take borrow-form params and produce non-Send frames. To make a coroutine sendable, the user takes ownership of every borrowed param: `async def fetch(host: String, buf: Own[list[int]])` -- `String` is the owned-string type (`std::string`), distinct from `str` (`std::string_view`); `Own[list[int]]` moves the list in. Matches Rust's `move ||` closure pattern. The cost only fires at Phase 5 enforcement sites; single-thread async (v1, v1.5) is unaffected.

**Escape hatch: `@unsafe_send` on the definition.** OQ4's `@unsafe_send` decorator (already decided for user records) generalizes to `def` / `async def` / generator function / nested `def` definitions. Forces the synthesized FrameType to Send regardless of slot types. Same audit-boundary semantics as the record form -- visible at the definition site, greppable, the author takes responsibility for soundness.

**Lambdas can't take decorators** (Python syntax limitation -- `@unsafe_send lambda ...` is not valid syntax). A user who needs the escape hatch on a lambda must lift it to a named `def`. This is fine in practice: the audit-boundary semantics of `@unsafe_send` already imply the user is doing something non-trivial; promoting from `lambda` to `def` is a small additional cost that also gives the call site a referenceable name for the audit comment.

```python
@unsafe_send
async def fetch(host: str, buf: list[int]) -> int32:
    # Author asserts: this frame is safe to migrate. E.g., the originating
    # thread blocks until completion, or `buf` lives in shared/atomic storage.
    ...
```

Mirrors Rust's `unsafe impl Send for MyFuture {}`. Legitimate cases:
1. Fork-join patterns where the originating thread parks until the migrated frame completes.
2. Borrowed data lives in shared/atomic storage (`Arc[T]` -- Phase 6).
3. Static/eternal data (currently no `Eternal[T]` marker; `@unsafe_send` is the hatch until one exists).

**Sequencing -- does the generator-onto-resumable-frame migration need to land first?**

No. Generator codegen migration onto the async path's resumable-frame abstraction is already a planned refactor (see `docs/ASYNC_DESIGN.md` and TODO.md), but it's **codegen-internal**. FrameType is a typesys abstraction; it only needs the slot list, which sema already tracks for both paths (the set of locals that persist across yield/await). Phase 2 can ship FrameType wired into both `gen_generators.py` and `gen_async.py` independently.

**Soft action during Phase 2:** factor slot extraction into a shared `extract_frame_slots(fn)` helper called from both codegens. Becomes a single point of truth that the eventual generator migration consolidates onto mechanically rather than re-deriving.

**As implemented (Phase 2 step C):** the classifier lives in `tpyc/sema/frame_traits.py` as a sema-side conservative mirror of `gen_async._classify_params` + the hoisted-local decision tree, rather than a literal extraction from codegen (extracting would have churned async/generator emission; true unification is deferred to the generator-onto-resumable-frame migration). Frame materials (hoisted locals, loop-var names, awaited sub-frame FunctionInfos, nested-def captures) are stamped on `FunctionInfo` at body-analysis end and resolve lazily -- awaited callees may be analyzed later; a greatest-fixed-point cycle guard handles mutually-awaiting coroutines. Lambdas carry their classified frame on the AST node (`TpyLambda.frame_type`). The invariant: any slot or await operand sema cannot classify makes the frame non-Send and non-Sync; relaxations are individual, tested decisions. Per-value consultation happens at Send/Sync conversion sites via the source expression (lambda node / `function_ref_info`), not via a `concrete_frame` field on surface types -- this drives the per-value frame chain shipped in Phase 3 for lambda / function-ref values; `Send[Iterator[T]]` / `Send[Cancellable[T]]` as user annotations (per-value consultation on generator / coroutine *call* results) wait for the Phase-5 enforcement surface that consumes them.

**Alternatives considered (rejected):**

- *(B)* Tag at `Coroutine[T]` level with an opaque boolean. **Rejected**: diagnostics become "frame is not Send" with no slot reference -- exactly the opposite of what the marker layer is for. Also requires codegen-fills-sema (backwards dependency).
- *(C)* Defer all coroutine/generator Send computation to Phase 5. **Rejected**: concentrates breakage when Phase 5 lands; users can't pin Send-ness with `assert_send[Coroutine[T]]()` (OQ6) during Phase 2; `Channel[Coroutine[T]]` can't be typed in Phase 4.
- *(D)* Separate `CoroFrameType` / `GenFrameType` / `ClosureType` typesys forms. **Rejected**: the structural rule is identical across kinds; one form keeps the typesys traversal smaller and the diagnostic path unified.

### OQ4. Opt-in / opt-out syntax **[DECIDED: full kit -- opt-in Protocol + @unsafe_* + @nosend/@nosync]**

Three orthogonal forms, all visible at the definition site, mutually exclusive per target (see "Mutual exclusion is per target" below):

**Opt-in: `class Foo(Send): ...` / `class Foo(Send, Sync): ...`**

Author asserts conformance; compiler verifies the structural rule (all fields Send/Sync + base Send/Sync) and errors if it fails.

```python
class Trade(Send):
    symbol: str
    qty: int32
    price: float
# OK -- all fields Send, claim verified

class Order(Send):
    symbol: str
    handler: Ptr[Buf]
# error: Order(Send) declared Send but field 'handler: Ptr[Buf]' is not Send
```

Locks the type into its Send/Sync contract -- silently adding a non-Send field later produces an error rather than quietly flipping the auto-derived answer.

**Opt-in unsafe: `@unsafe_send` / `@unsafe_sync`**

Force the answer to true regardless of fields. The only place soundness depends on the author rather than the compiler -- the decorator marks the audit boundary (mirrors Rust's `unsafe impl Send`). Applies to **records** (forces structural rule on fields to true) and also to **`def` / `async def` / generator function / nested `def` definitions** (forces the synthesized FrameType to Send -- see OQ3 escape hatch). Does **not** apply to `lambda` (Python syntax doesn't allow decorators on lambdas); users must lift to `def` to use the escape hatch.

```python
@unsafe_send
class NativeHandle:
    raw: Ptr[NativeState]   # type system can't see that the C library
                            # guarantees the handle is thread-safe to transfer
```

**Opt-out: `@nosend` / `@nosync`**

Force the answer to false regardless of fields. Applies to **records** (forces structural rule on fields to false) and to **`def` / `async def` / generator function / nested `def` definitions** (forces the synthesized FrameType to non-Send / non-Sync). For types or functions whose structural answer is wrong because of hidden invariants (thread-local cache references, arena allocations bound to the constructing thread, etc.). Does **not** apply to `lambda` (Python syntax doesn't allow decorators); users must lift to `def`.

```python
@nosend
class ArenaBuffer:
    data: list[int32]    # structurally Send, but the arena it lives in isn't
    capacity: int32
```

**Mutual exclusion is per target.** The Send/Sync answer is a property of a specific *type definition*: a record's `Send`-ness is its own property; an async/generator/lambda definition's FrameType is its own property. The opt-in / unsafe / opt-out forms conflict only when applied to the *same* target:

- *Same record:* `class Foo(Send)` + `@unsafe_send` is redundant (rejected); `class Foo(Send)` + `@nosend` is contradictory (rejected); `@unsafe_send` + `@nosend` is contradictory (rejected).
- *Same function:* `@unsafe_send` + `@nosend` on one `def` / `async def` / generator function definition is contradictory (rejected). Lambdas cannot be decorated.
- *Different targets compose freely:* `class Foo(Send)` containing a method `@unsafe_send async def m(self, buf: list[int])` is **fine** -- the class marker applies to the record type; the function decorator applies to the synthesized FrameType for `m`'s state machine. Two different targets, no conflict.

Sema enforces the per-target mutual exclusion in the registration pass.

**Alternatives considered (rejected):**

- *(auto-derive only)*. **Rejected**: real type systems hit cases where the structural rule and author intent disagree (foreign handles, hidden invariants). Without escape hatches, authors misshape types to satisfy the structural rule or live with wrong answers. Swift had this for a while and added escape hatches in response.
- *(opt-in Protocol only, no `@unsafe_*` or `@nosend`)*. **Rejected**: covers the "I assert" use case but leaves both override directions unsupported. Foreign-handle types and hidden-invariant types both need escape hatches.
- *(per-field annotations -- `x: Send[Ptr[Buf]]` lying about a field)*. **Rejected**: conformance is at the type level, not the field level. Per-field would explode the audit surface.

### OQ5. `@dynamic` protocol surface for Send-tagged values **[DECIDED: `Send[Pet]` in Phase 2; no Box dependency]**

**Current state:** `@dynamic class Pet` creates an existential erased form. The user writes `def greet(p: Pet)` and codegen lowers to `tpy::RefAdapter<Pet, T>` (lvalues) or owning `tpy::Adapter<Pet, T>` (rvalues). `Adapter[P]` is **not** a TPy-level type -- it's a C++ codegen detail. Dynamic-protocol values living in fields or containers go through `Box[P]` (shipped; items #13/#14/#15 in `docs/DYNAMIC_PROTOCOL_DESIGN.md` now Done).

**Decision:** `Send[Pet]` is the surface, mirroring how bare `Pet` works. The wrapper applies uniformly:

- **As a bound:** `def spawn[F: Send[Pet]](f: F)` -- F implements Pet AND is Send.
- **As an erased borrow/local-owned form:** `def greet(p: Send[Pet])` -- codegen emits the same `tpy::Adapter<Pet, T>` / `tpy::RefAdapter<Pet, T>` as bare `Pet`. Sema checks at the call site that the concrete argument's type is Send before allowing the Adapter construction. No new C++ templates needed.
- **As a storage form in fields/containers:** waits for `Box[P]` to land, exactly like bare `Pet` does today. Once `Box[P]` ships, `Box[Send[Pet]]` works automatically; no Send-specific Box machinery needed.

**No compiler dependency on Box.** Phase 2 ships Send/Sync support without touching Box code. The composition `Box[Send[Pet]]` works (now that Box[P] has shipped) because:
- `Box[T]` is library code in `lib/tpy/tplib/box.py`, implemented via the general `Deref[T]` protocol -- the compiler does not hardcode `Box`.
- `Send[T]` is a type-system wrapper; it does not know about Box.
- Their composition follows general rules.

This mirrors Rust: `Send` is a marker the compiler knows about; `Box<T>` lives in `alloc`; they compose because of general rules.

**Mapping to Rust:** `Send[Pet]` parameter ~ `&dyn Pet + Send`. `Box[Send[Pet]]` (post-Box[P]) ~ `Box<dyn Pet + Send>`. Bare `Pet` ~ `&dyn Pet` (no Send requirement).

**Two-level model: pointee vs handle (Phase 2 decision).** `Send[Pet]` asserts a property of the *pointee*: the concrete type erased behind the adapter is Send. It does **not** assert that the adapter *handle* itself is transferable. The handle's own Send-ness is a property of its form: `tpy::RefAdapter<Pet, T>` borrows the originating object (never Send as a handle, exactly like `&dyn Pet + Send` in Rust -- the reference itself is only Send if the pointee is Sync); an owning `tpy::Adapter<Pet, T>` or `Box[Send[Pet]]` owns its payload (handle Send iff the concrete `T` is Send -- which the wrapper already guarantees). Phase 2 enforces only the pointee level, at every conversion/construction site that erases a concrete value into a `Send[Pet]` slot. Phase 5 thread-boundary sites must check **both** levels: the pointee assertion (already carried by the wrapper) AND that the handle form is owning (owning Adapter / `Box[Send[Pet]]`, not RefAdapter). A `Send[Pet]` *parameter* therefore does not by itself license cross-thread transfer -- it licenses constructing owned Send-tagged storage from it.

**Alternatives considered (rejected):**

- *Promote `Adapter[P]` to a TPy-level type (e.g. `Send[Adapter[Pet]]`)*. **Rejected**: leaks a codegen detail into the surface; users would have to learn `Adapter[P]` as a TPy type that has no precedent (bare `Pet` already means erased today).
- *`Box[Send[Pet]]` as the only surface*. **Rejected**: would gate Phase 2 on Box[P]; awkward bound syntax (`[F: (Send, Pet)]` requires intersection-like grammar).
- *`Adapter[P, Send]` extra type-arg slot*. **Rejected**: diverges from `Send[T]` wrapper pattern; non-generalizing.
- *Ban `@dynamic` in Send contexts*. **Rejected**: limits users.

### OQ6. Diagnostic surface **[DECIDED: ship inline chain + assert_send/sync + --explain-send]**

When a Send/Sync check fails, the user needs to know **why**. The structural rule has a path -- "Foo is not Send because field `bar: Ptr[Buf]` is not Send" -- and Phase 3 surfaces it three ways:

**Shared machinery:** a recursive `why_not_send(t: TpyType) -> SendChain | None` walker that returns a structured path:

```
list[Order]
+-- Order (record)
    +-- field 'handler: Ptr[Buf]' is not Send (raw pointer)
```

`why_not_sync` is the analog. All three surfaces below are thin renderers over the chain output -- the cost is the walker, not the renderers.

**1. Inline chain at enforcement sites.** Every Send/Sync check failure renders the chain in the error message automatically. Applies to Phase 4 `Channel[T]` construction/send, Phase 5 `thread.spawn` closure check, and any user-side `assert_send[T]()` call.

**2. `tpy.assert_send[T]()` / `tpy.assert_sync[T]()` compile-time assertions.** Zero-cost; usable in library code to pin Send/Sync contracts. Failure prints the chain. Lets library authors validate forward-compatibility before any enforcement site exists -- adding a non-Send field to a published library type breaks the library's own build immediately rather than silently breaking downstream consumers later.

**3. `tpyc --explain-send T` / `--explain-sync T` CLI flag.** Prints the structural derivation tree for any nominal type. Power-user tool for exploring unfamiliar code; cheap once the walker exists.

**Alternative considered (rejected):** terse "X is not Send" errors with no chain. Turns Send/Sync diagnostics into a grep exercise -- exactly the friction the marker layer is supposed to eliminate.

## Enforcement Sites (Phase 4+)

The marker layer is unobservable until something *uses* it. The planned sites, in order:

1. **`Channel[T]`** (Phase 4) -- `send(value: T)` requires `T: Send`. On the single-threaded executor this is a no-op semantically (queues just buffer), but the type check fires unconditionally so user programs are forward-compatible with the multi-threaded executor. Backed by an SPSC or MPSC ring buffer; choice deferred to the channel design doc.

2. **`thread.spawn(closure)`** (Phase 5) -- requires the closure type to be Send (captured locals + the function pointer). Mirrors `std::thread::spawn`. Implies Phase 5 executor, Phase 2 closure rule (OQ2), and Phase 2 coroutine-frame rule (OQ3) if `spawn` accepts coroutines.

3. **`Task[T]` migration** (Phase 5) -- a `Task[T]` registered on a multi-threaded executor must have a Send frame. The single-threaded executor (v1) has no migration site and accepts non-Send frames. The multi-thread executor adds the constraint at `create_task` / `spawn_blocking`.

4. **`Arc[T]`** (Phase 6) -- requires `T: Send + Sync`. The standard atomic shared-ownership requirement.

5. **`Mutex[T]` / `RwLock[T]`** (Phase 6, shipped) -- the Sync uplift primitives. Both are Sync iff `T` is Send. For `RwLock` this is looser than Rust's `T: Send + Sync`, and correct for TPy: the read guard hands out `readonly[T]`, and a not-Sync `T` is either a container (not-Sync from shared-mutability, removed by the readonly guard) or an interior-mutable type from the unsafe `unsafe_interior_mutable` hatch (user owns Send/Sync). Rust needs `T: Sync` only because its `Cell` is *safe* interior mutability. Both enable shared-mutable across threads safely. (Sound only on the *safe* surface: it also grants Sync to a `Send`-but-not-`Sync` interior-mutable payload -- a user `Cell`-analog via the hatch -- a latent hole that opens on the first such type. The precise fix is RwLock-local -- `Sync iff T: Send AND (freezable(T) OR T: Sync)` -- not the OQ1 `readonly[container]: Sync` refinement; see the RwLock Sync bound follow-up below.)

## Interaction with Other Features

**Ownership / borrow** (`docs/OWNERSHIP_DESIGN.md`, `docs/SAFETY_MODEL.md`): TPy deliberately allows aliased mutation within a thread (matches Python semantics). Send/Sync is the **only** alias-control mechanism in the language, and it activates exclusively at thread boundaries (channel sends, spawn, Task migration). Within a thread, two `Ptr[T]` handles to the same object are routine and don't trigger any check. Ownership transfer (`Own[T]` moves) is the canonical Send operation; borrows (`Ptr[T]`, references) are by construction non-Send because they alias originating-thread memory -- see also OQ3 / FrameType.

**Readonly** (`docs/READONLY_DESIGN.md`): proposed tightening of `readonly[T].is_sync()` in OQ1 makes readonly purely a borrow-side restriction. `readonly[T]` does not by itself uplift Send to Sync.

**Async** (`docs/ASYNC_DESIGN.md`): v1 / v1.5 are single-threaded; the marker layer is dormant. Phase 5 multi-thread executor reads coroutine-frame Send-ness from OQ3. The split between single-threaded `Task` (no Send constraint) and multi-threaded `Task` (Send required) is a v3+ design decision tracked there.

**Rc / Arc** (`tplib/rc.py`, `tplib/arc.py`): `Rc[T]` is the canonical non-Send shared-ownership type. `Arc[T]` (Phase 6, shipped) is the Send/Sync counterpart -- Send + Sync iff `T` is both, via the conditional override. The two-type split mirrors Rust and avoids paying the atomic cost when threads aren't involved.

**`@dynamic` protocols** (`docs/DYNAMIC_PROTOCOL_DESIGN.md`): see OQ5.

**Generic record monomorphization**: Send/Sync answers are per concrete instantiation, computed at instantiation time. `list[int32]` is Send; `list[Ptr[Buf]]` is not. Auto-derive handles this through TypeParamRef forward-conformance during the record-level check, then field walks with substituted args at use sites -- `NominalType.is_send()` / `is_sync()` walks `record.fields` and `record.parents` under `type_params -> type_args` substitution on every query for generic records (a re-entrancy guard handles self-referential generics like `class Tree[T]: children: list[Tree[T]]` via greatest-fixed-point semantics).

## Roadmap (detail)

### Phase 1 -- markers + auto-derive (DONE)

- C++ `is_send<T>` / `is_sync<T>` traits in `runtime/cpp/include/tpy/type_traits.hpp` with specializations for primitives, pointers, `string_view`, vector, array, `ordered_map`, `ordered_set`, `SpanIter`.
- C++ `Send` / `Sync` concepts.
- Sema `TpyType.is_send()` / `is_sync()` defaults + overrides on `TypeRef`, `RecordType`, `OwnType`, `PointerType`, `ReadonlyType`, `UnionType`, tuple form.
- TypeDef registry per-builtin rules.
- `sema/registration.py` auto-derive for user records.
- `Send` / `Sync` Protocol markers in `lib/tpy`.

### Phase 2 -- marker-layer correctness + opt-in/opt-out + tests (DONE)

All items below landed (steps A-F), with two scope notes: the per-record
C++ trait mirror was deferred (Implementation Notes), and `bytearray`
gained an explicit `is_sync=False` override (step A review finding).

1. OQ1: tighten `ReadonlyType.is_sync()` to `wrapped.is_sync()`.
2. Tighten `OwnType.is_sync()` to always-False (table-vs-code mismatch identified in validation review; aligns code with the rules table).
3. OQ2: `CallableType.is_send() = is_sync() = False` for bare `Callable[...]`; introduce `Send[T]` / `Sync[T]` marker wrapper types with dual erased/assertion semantics; canonical wrapper order at resolve time (Send/Sync outermost, distributing over union/optional); add `T: Send` / `T: Sync` bound support in PEP 695 generic param syntax.
4. OQ3: introduce `FrameType` covering coroutines, generators, lambdas, closures uniformly. **Storage-form slot semantics** (frame slots reflect literal C++ struct field types, not user param annotations). Shipped as the sema-side classifier in `sema/frame_traits.py` -- a conservative mirror of codegen's shapes rather than a helper extracted from both codegens; see the OQ3 "As implemented" note. Extend `@unsafe_send` to function definitions as the escape hatch.
5. OQ4: `Send` / `Sync` Protocol opt-in (with structural verification), `@unsafe_send` / `@unsafe_sync` opt-in unsafe, `@nosend` / `@nosync` opt-out. All four decorator-form attachments (`@unsafe_*` and `@nosend`/`@nosync`) apply to records AND to `def` / `async def` / generator function / nested `def` definitions (forces FrameType to the asserted answer). Lambdas can't be decorated -- users lift to `def` to use any of these.
6. OQ5: `Send[Pet]` for `@dynamic` protocols -- valid wherever bare `Pet` is valid today (params/locals). **Sema-only**: codegen emits the existing `tpy::Adapter` / `tpy::RefAdapter` templates; sema enforces the Send constraint at the construction site that creates the Adapter. No new C++ templates needed. **No compiler dependency on Box.**
7. Add `# tpyc: is_send(yes/no)` / `is_sync(yes/no)` test annotations.
8. New test group `tests/cases/send_sync/` covering: every built-in type form, user records with mixed-Send fields, opt-in success/failure, opt-out, generic record monomorphization, union/tuple/optional shapes, coroutine/generator/lambda/closure Send-ness with mixed-Send captures, `Send[Callable[...]]` and `Send[Pet]` construction, canonical wrapper ordering.
9. Update `docs/LANGUAGE_FEATURES.md` Send/Sync section to match Phase 2 rules.
10. **Runtime trait mirror for `std::span`.** Done: `type_traits.hpp` now specializes `is_send` (false) and `is_sync` (false for `std::span<T>`, element-Sync for `std::span<const T>`), matching the sema rules table. The user-record specializations remain deferred with the per-record mirror (Implementation Notes).

  Additionally: confirm the value-type-default Send/Sync answer for `builtins.str` and `builtins.bytes` still agrees with the rules table. These are immutable dual-form value types with no explicit Send/Sync override, defaulting to Send=Yes/Sync=Yes via the value-type rule -- the intended answer for their storage form. `builtins.bytearray` is the mutable member of the family: a reference type, so it carries explicit `is_send=True` / `is_sync=False` registry rows and a matching Send row in the runtime beside `tpy::ByteArray` (the `std::vector` partial specialization does not match a derived class). The borrow-form behavior is handled by FrameType storage-form semantics (OQ3), not by changing the underlying type's Send/Sync answer.

**Effort:** S-M. Phase 2 ships type-system parsing/sema for two new wrapper types (`Send[T]`, `Sync[T]`), a new typesys form (`FrameType`), Send/Sync auto-derive integration for opt-in/opt-out decorators, and the storage-form slot walk. **No new C++ templates** -- the Send/Sync wrappers are sema-only; the C++ representation of `Send[T]` / `Sync[T]` is the same as bare `T`. Sema fires Send/Sync checks wherever the user writes `Send[T]` / `Sync[T]` as an expected type or `T: Send` / `T: Sync` as a generic bound (so Phase 2 tests for `Send[Callable[...]]` / `Send[Pet]` construction work immediately, against the user-explicit annotations the tests use). Concurrency-API enforcement sites (`Channel[T]` with `T: Send` bound; `thread.spawn`; Task migration) arrive in Phase 4+ -- but those are new APIs whose parameter types reuse Phase 2's bound-checking machinery; no additional enforcement code. Codegen does need a small change for the user-record `tpy::is_send<UserT>` / `tpy::is_sync<UserT>` specialization emission (one-line emit per record).

### Phase 3 -- diagnostics (DONE)

1. OQ6: implement the shared `why_not_send(t) / why_not_sync(t)` chain-walker over typesys.
2. Wire the chain into Send/Sync error messages everywhere they fire (preparation for Phase 4 enforcement, even though there are no firing sites yet in Phase 3).
3. `tpy.assert_send[T]()` / `tpy.assert_sync[T]()` compile-time assertions, renderer over the chain-walker.
4. `tpyc --explain-send T` / `--explain-sync T` CLI flag, renderer over the chain-walker.

**Effort:** S. The walker is the work; the three surfaces are thin renderers. OQ5 (`@dynamic` adapter Send-encoding) shipped in Phase 2 alongside `Send[Callable[...]]`, so no additional adapter work in Phase 3.

**As implemented:**

- Walker lives in `tpyc/sema/send_chain.py`. It is **oracle-driven**: it never re-derives the boolean, calling `t.is_send()` / `t.is_sync()` as the single source of truth and only attributing an already-False answer to the sub-components carrying it (record fields/bases under use-site `type_args`, container type-args, union members, tuple elements, wrapper payloads, frame slots), bottoming out in a per-form leaf reason. Adding a new Send/Sync-bearing `TpyType` needs one arm in `_attribute` + `_leaf_reason`, not a second copy of the rule.
- `assert_send` / `assert_sync` are `@builtin_function` declarations (`lib/tpy/tpy/_core/_functions.py`) checked in `sema/calls.py` (`_analyze_send_sync_assertion`) and elided in codegen (`TpyCall.compile_time_assert`, skipped in `codegen_cpp/statements.py`). CPython stubs in `lib/cpy/tpy/__init__.py` make them subscriptable no-ops.
- Inline chains are wired at the three Phase-2 firing sites: the `Send[T]` / `Sync[T]` conversion check (`sema/compatibility.py`), the `T: Send` / `T: Sync` bound check (`sema/calls.py:validate_type_param_bounds`), and the `class Foo(Send)` opt-in check (`sema/registration.py`). The resolve-time `Send[Ptr]` marker error keeps its existing single-line message.
- `tpyc --explain-send TYPE` / `--explain-sync TYPE` (`tpyc/explain.py`) resolves `TYPE` through the entry module's real `TypeResolver` (so user records, imports, and generic forms resolve as in an annotation), then renders the chain.
- **Per-value frame chain at the conversion site.** When a `Send[T]` / `Sync[T]` conversion rejects a lambda or function-reference value, the chain is rooted at the static type but its children are the value's concrete `FrameType` slots, naming the offending capture (e.g. `captured 's' is not Send`) instead of the generic "erased callable may capture non-Send state" leaf. The boolean accept/reject already consulted the value's frame (`compatibility._value_frame_traits`); this routes the same frame into the chain (`_value_frame` + `send_chain.why_not_frame`, own-slot only). The static-type chain remains the fallback for value kinds with no frame fact (e.g. a bare `@dynamic` protocol) and for the residual case where the cause is an awaited sub-frame rather than an own slot. The other three chain sites (bound check, opt-in, `--explain`, `assert_send`) are type-only -- no value, no frame -- so they keep the static-type chain.
- **Deferred follow-up:** the `Send[Iterator[T]]` / `Send[Cancellable[T]]` user-annotation surface (per-value frame check on generator / coroutine *call* results, vs lambda / function-ref values today) is **gated on Phase 5** -- its only enforcement consumer is the multi-threaded executor's `Task` migration site, which does not exist yet. Tracked in `TODO.md`.

### Phase 4 -- first enforcement: Channel[T] (DONE)

Design: `docs/CHANNEL_DESIGN.md`. Shipped:

1. `channel[T: Send](cap) -> (Sender[T], Receiver[T])` -- Rust-style split,
   SPSC, `Rc`-backed FIFO ring on `UninitHeapStorage`; blocking `send`/`recv`
   via the single-waiter park model; explicit `close()`.
2. The `T: Send` bound on the `channel` factory is the enforcement -- it rides
   the Phase-2 bound machinery and the Phase-3 why-not-send chain. **No
   channel-specific enforcement code.**
3. The channel itself is TPy library code (`lib/tpy/tpy/channel.py`); no new
   C++ runtime. Three general compiler changes were needed along the way:
   the owned-tuple-unpack move-out feature (unblocks `tx, rx = channel(cap)`);
   suppressing the C++ concept constraint for `Send`/`Sync` marker bounds (they
   are sema-only; the per-record `is_send` C++ trait stays deferred); and
   making a `Send`/`Sync`-bounded type param satisfy that marker bound when
   forwarded to a nested generic (so the internal records can carry `T: Send`).
4. Tests in `tests/cases/channel/`: `error_channel_not_send` (bound failure +
   chain), `channel_async` (producer/consumer, blocking, close), and
   `panic_channel_capacity`.

**Deferred** (tracked in `docs/CHANNEL_DESIGN.md` / `BUGS.md`): MPSC
(`Sender.clone()` + waker queue), `try_send`/`try_recv`, capacity-0 rendezvous,
and the Arc-backed cross-thread channel (Phase 6).

**Effort:** was L; landed as library code + tests + three general compiler fixes.

### Phase 5 -- multi-threaded executor

Gated on `docs/ASYNC_DESIGN.md` v3+ decision. Adds:

1. Multi-threaded `asyncio` executor (separate from v1 single-thread executor).
2. `thread.spawn(fn)` requiring Send closure (enforcement site; `Send[Callable[...]]` machinery shipped in Phase 2).
3. `Task[T]` migration constraint: requires Send frame (enforcement site; `FrameType.is_send` shipped in Phase 2).
4. Enforcement of `Send[Pet]` at any spawn boundary that takes erased `@dynamic` protocol values (the `Send[Pet]` type itself shipped in Phase 2 alongside `Send[Callable[...]]` per OQ5). Storage-form `Box[Send[Pet]]` enforcement at channel/field boundaries gates on `Box[P]` landing first.
5. `thread_local` storage primitive in TPy.

**Effort:** XL.

### Phase 6 -- Arc, Mutex, RwLock

1. `Arc[T]` requires `T: Send + Sync`. Atomic counterpart to `Rc[T]`. **Done**
   (`tplib/arc.py`; see `docs/THREADING_DESIGN.md` V2). Delivered the
   **conditional Send/Sync override** the marker layer previously lacked:
   `@unsafe_send(if_params_send=..., if_params_sync=...)` / `@unsafe_sync(...)` on a generic record
   grant the trait iff every type param satisfies the listed markers, else fall
   back to the structural answer -- the analog of Rust's
   `unsafe impl<T: Send + Sync>`, and the machinery Mutex reuses below. The
   why-not chain attributes a failure to the offending type param and the marker
   it lacks. (The old kit only had the *unconditional* `@unsafe_send` / `@nosend`.)
2. `Mutex[T]` -- Sync iff `T: Send` (a conditional override: `@unsafe_sync(if_params_send=True)`).
   Exposes a mutable-`T` guard. **Shipped** (`tpy.sync`).
3. `RwLock[T]` -- multi-reader shape, exposing a `readonly[T]` read guard and a
   mutable-`T` write guard. Sync iff `T: Send` -- same as `Mutex`, looser than
   Rust's `T: Send + Sync` and correct for TPy (interior mutability is unsafe,
   so the readonly read guard suffices for every safe payload; see the RwLock
   Sync bound follow-up below). **Shipped** (`tpy.sync`).

   **RwLock Sync bound -- latent soundness hole (low priority).** The `Sync iff
   T: Send` bound treats all not-Sync `T` alike, but there are two kinds: a
   *container* (not-Sync purely from shared-mutability, which the `readonly`
   read guard removes) and an *interior-mutable* type (not-Sync because it
   mutates through `readonly` -- TPy's only such types come from the unsafe
   `unsafe_interior_mutable` hatch). (Other not-Sync safe forms -- e.g. a
   `Send[Callable[...]]`-wrapped closure, structurally not-Sync -- reduce to the
   *container* case for this argument: an ordinary lambda's by-value captures
   are not `mutable` and pointer/reference captures are excluded from `Send`, so
   concurrent readonly calls can't race, and any capture that *could* is behind
   the same unsafe hatch.)

   The current bound is sound for the whole *safe* surface (safe not-Sync types
   are all container-shaped, frozen by the read guard). It is **not** sound in
   general: it also grants Sync to a `Send`-but-not-`Sync` interior-mutable
   payload -- a user `Cell`-analog built with the hatch -- where two readers
   mutate through their own `readonly[T]` handles and race. This is a latent
   hole, not merely imprecision: the `Cell` author spends one honest
   `@unsafe_send` (a cell *is* safe to move) and correctly withholds
   `@unsafe_sync`; RwLock then fabricates the `Sync` the author never asserted,
   and the race surfaces in a *different*, fully-safe-looking `Arc[RwLock[Cell]]`.
   It is unreachable today only because no such type exists yet (no stdlib type
   is simultaneously `Send`, not-`Sync`, and hatch-based -- `Rc`/`Weak` are
   not-`Send`; `Atomic`/`Mutex`/`Arc` are correctly `Sync`); it opens on the
   first user `Cell`.

   The fully-precise bound is **RwLock-local** -- not the broad OQ1
   `readonly[container]: Sync` refinement:

       RwLock[T]: Sync  iff  T: Send  AND  (freezable(T) OR T: Sync)

   where `freezable(T)` = no mutation is reachable through `readonly[T]` -- `T`
   transitively reaches no `unsafe_interior_mutable` field AND no `@native`
   interior-mutable leaf (e.g. `Atomic`, whose `@readonly` ops mutate a
   `std::atomic` through a shared handle with no hatch field). Note `freezable`
   is defined on the mutation route, not the hatch alone: a `@native`
   `readonly`-mutating leaf is *not* freezable even without a hatch field. Such
   leaves that are genuinely `Sync` (`Atomic`) are still admitted via the
   `OR T: Sync` disjunct; the freezable disjunct exists to admit the safe
   container surface. `freezable` waives Rust's second gate exactly where the
   read guard makes concurrent reads sound: `list`/`dict`/`set`/plain classes stay Sync (so
   `Arc[RwLock[list]]` keeps working), while a `Cell`-analog (not freezable, not
   `Sync`) becomes not-Sync until the user adds `@unsafe_sync`. The `OR T: Sync`
   disjunct is load-bearing: a naive `Sync iff T: Send AND no-interior-mutability`
   would wrongly reject `RwLock[Atomic[...]]`, `RwLock[Mutex[T]]`, nested locks,
   and any `RwLock[Config]` where `Config` merely holds an `Atomic` counter --
   all legitimately Sync. Low priority (possibly never): closing it only rejects
   a deliberately-unsafe construct that does not exist yet, and it needs the
   conditional-override to carry the compound predicate.
4. Channels gain MPMC variant if useful.

**Effort:** L (Arc + conditional-override, Mutex, and RwLock all shipped; the
precise RwLock Sync bound (item 3 above) and MPMC channels remain).

## Implementation Notes

- The trait names `is_send` / `is_sync` are stable across sema, runtime, and the user-facing `Send` / `Sync` Protocols. Don't rename.
- The auto-derive answer is sema-side. The codegen-emitted `tpy::is_send<UserT>` / `tpy::is_sync<UserT>` per-record mirror (so hand-written C++ can SFINAE / concept-constrain on user records) is **deferred out of Phase 2**: nothing consumes it yet, and emitting it would regenerate every record-bearing snapshot. Revisit when the first hand-written C++ consumer appears; emit only for explicitly-marked records if possible.
- **Two enforcement axes -- when does a Send/Sync diagnostic fire?**
  - *User-explicit Send/Sync sites* (Phase 2): wherever the user writes `Send[T]` / `Sync[T]` as an expected type (param annotation, field type, return type, type-arg, generic bound), sema fires the check at every conversion / construction / assignment. So `cb: Send[Callable[[], None]] = my_lambda` checks `my_lambda` is Send-conforming at the assignment; `def greet(p: Send[Pet])` checks at the call site; `def spawn[F: Send[Pet]]` checks at the generic-bound resolution. This machinery is fully in Phase 2.
  - *Concurrency-API sites* (Phase 4+): `Channel[T]`, `thread.spawn(fn)`, `Task[T]` migration. These are new types/APIs that *use* the Phase 2 machinery -- their parameters are annotated `Send[T]` or have `T: Send` bounds, so the check is just Phase 2's bound-checking machinery firing at the new API surface. No additional enforcement code; just the new API types.
- Phase 2 introduced **no new C++ templates**. `Send[T]` and `Sync[T]` have the same C++ representation as bare `T`; the check is sema-side (the per-record trait mirror is deferred, see above).
- **`FrameType` typesys home:** lives in `tpyc/typesys.py` alongside `CallableType` / `OwnType` / `ReadonlyType`; classification logic in `tpyc/sema/frame_traits.py`. Attached via `frame_type` (+ raw frame materials) on `FunctionInfo` for async/generator/nested-def and via `TpyLambda.frame_type` on the lambda AST node. Send/Sync conversion checks consult the concrete value's frame through the source expression (see the OQ3 "As implemented" note); surface types carry no `concrete_frame` field.
- **`Sync[T]` parallels `Send[T]`** with identical canonicalization rules and identical erased/assertion duality. Where this doc names `Send[T]` for brevity (e.g., `Send[Callable[...]]`, `Send[Pet]`), the same shape exists for `Sync[T]`.
- **Own[T] tightening:** `OwnType.is_sync()` (`tpyc/typesys.py`) changed from `wrapped.is_sync()` to always-False in Phase 2 step A. Aligns with the rules table; Own represents single-owner move semantics for which Sync-shareability is a category error.
- **Generic-record substitution:** `NominalType.is_send()` / `is_sync()` walks `record.fields` and `record.parents` under `type_params -> type_args` substitution on every query for generic records, with a re-entrancy guard for self-referential generics (greatest-fixed-point semantics). Non-generic records use the cached registration-time bool. Sema's registration predicate (`tpyc/sema/registration.py`) accepts a field whose type contains a TypeParamRef as conservatively-OK; the use-site walker is the source of truth.

## Cross-references

- `docs/SAFETY_MODEL.md` -- ownership / borrow invariants Send/Sync builds on.
- `docs/READONLY_DESIGN.md` -- readonly's semantics, prerequisite for OQ1.
- `docs/OWNERSHIP_DESIGN.md` -- Own/move/borrow shape; Send is the cross-thread version of move.
- `docs/ASYNC_DESIGN.md` -- multi-thread executor gating, coroutine frame model.
- `docs/DYNAMIC_PROTOCOL_DESIGN.md` -- adapter machinery, OQ5.
- `docs/FEATURE_ROADMAP.md` E1 (Send/Sync), G2 (Channels), IX (Concurrency) -- top-level roadmap entries; this doc is the detailed sibling.
- `docs/LANGUAGE_FEATURES.md` "Thread Safety Markers (Send/Sync)" -- user-facing summary; gets updated to match Phase 2 rules.
