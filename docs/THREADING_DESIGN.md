# Threading design (owned-only, Send/Sync-checked)

Status: **V1-V3 BUILT.** `tpy.thread.spawn` / `JoinHandle` (V1), `Arc`/`Weak`
+ `Atomic` (V2), and `Mutex`/`RwLock` (V3) all ship:
`runtime/cpp/include/tpy/threading.hpp` + `lib/tpy/tpy/thread.py`,
`tplib/arc.py` + `tpy/atomic.py`, and `runtime/cpp/include/tpy/sync.hpp` +
`lib/tpy/tpy/sync.py`, tests under `tests/cases/threading/`. The V1 notes
below are retained as the historical contract. The
prerequisite generic-protocol *sibling*-bound fix was merged earlier
(`bf1f47bb41`); building V1 additionally required a second compiler fix --
a `Send[Own[T]]` param was not recognized as movable (the transparent
Send/Sync marker was not peeled in owned-param recognition), so the moved-in
task copied instead of moving (a hard error for `@nocopy` tasks). Fixed and
landed with V1. This document is the contract for the whole track.

Implementation notes (where the build refined the contract):
- `JoinHandle[R]` is the TPy `@nocopy` wrapper as designed (a native
  `_RawJoin[R]` field owning `std::thread`+`std::future`, plus a `_consumed`
  flag). `join`/`detach` set `_consumed` *before* the underlying call so a
  re-raised task exception does not re-trip the drop check on unwind.
- Abort-on-unconsumed-drop: `__del__` calls a native `[[noreturn]]`
  `tpy_panic` helper rather than `raise` -- a TPy `__del__` lowers to a
  `noexcept` C++ destructor, where a `throw` is `-Werror=terminate`. The raw
  handle's own destructor only *detaches* (safe teardown), so it never masks
  that diagnostic. Double-`join`/`detach` still `raise RuntimeError` from the
  normal methods (catchable).
- The internal `_spawn_native` takes a plain `Own[T]` (Send already enforced
  at the user-facing `spawn`'s `Send[Own[T]]` param), so the forward moves.

This track cashes the already-shipped Send/Sync trait investment
(`SEND_SYNC_DESIGN.md` Phases 1-4, of which only `channel[T: Send]` has a
consumer today) into real OS-thread parallelism that is safe by
construction, and backs the "no GIL" claim. The model is deliberately
**Rust's `Send`/`Sync` + move**, expressed in Python syntax -- **not**
CPython's `threading` (see "Why not CPython threading").

The near-term v1 is deliberately the **Runnable-struct** form of `spawn`,
*not* closures. That choice was reached after a design grilling session and
a co-validate review (two independent reviews) surfaced that the closure
path -- a callable generic bound -- is the risky, THIR-coupled part, while
the actual no-GIL payoff needs none of it. Closures become a later
ergonomic layer over the same core. The rejected alternatives and the
review findings are recorded inline so they do not get re-litigated.

## Roadmap / progress

| ID | Increment | Scope | Status | Depends on |
|----|-----------|-------|--------|------------|
| V1 | `tpy.thread.spawn` (Runnable-struct) | `spawn[R: Send, T: ThreadTask[R]](task: Send[Own[T]]) -> Own[JoinHandle[R]]` (`ThreadTask` = structural `run() -> R`; `Send` via the `Send[Own[T]]` wrapper; `R: Send` bound since R crosses the thread boundary; `spawn(task)` fully inferred -- the marker-wrapper + associated-type inference gaps are closed). `JoinHandle`: `join() -> R`/`detach()`, abort-on-unconsumed-drop. | **BUILT** | -- |
| V2 | `Arc[T]` / `Weak[T]` | Atomic sibling of `Rc`, built on a new generic `Atomic[T: AnyFixedInt]` (`tpy.atomic`, wrapping `std::atomic<T>`). `Send + Sync` iff `T` is, via the conditional `@unsafe_send`/`@unsafe_sync` (if_params_*) override. Shared into a task by `arc.clone()` into a struct field. | **built** | V1 |
| V3 | `Mutex[T]` / `RwLock[T]` | Both `Send + Sync` iff `T: Send` (RwLock looser than Rust's `T: Send + Sync` -- correct because TPy interior mutability is unsafe/user-owned). Shared as `Arc[Mutex[T]]`. `@readonly` lock/read/write hand out a `Deref[T]` context-manager guard (interior mutability via `unsafe_interior_mutable[Ptr[cell]]`). Module placement decided: `tpy.sync` (native-primitive layer, parallel to `tpy.atomic`; `Arc` stays pure-TPy in `tplib.arc`). | **BUILT** | V2 |
| D1 | Closure `spawn` (ergonomic layer) | `spawn(lambda: work(data))` desugaring to the V1 core. Needs a **callable generic bound** + owning-capture. Own design pass; the current spelling is shaky (see "Deferred: closures"). Likely **post-THIR**. | **deferred** | V1, (THIR) |
| D2 | De-intrinsic `asyncio.create_task` | Shipped as "bound coroutines": move-only handle locals holding the concrete frame (zero-alloc; erasure only at typed boundaries), consumed by await/create_task/run/move; compile-time coroutine-only arg contract. See the SHIPPED section below. | **SHIPPED** | -- |
| D3 | Movable `Callable` | Re-back `Callable` with C++23 `std::move_only_function`. Independent; breaking (~47 snapshots + real copy sites). | **filed, decoupled** | -- |
| D4 | Scoped threads / `TaskGroup` | Borrow-a-local-into-a-thread; structured concurrency. Region-gated. | **THIR-gated** | THIR/MIR |
| D5 | Multi-threaded async executor | Work-stealing, cross-thread wakers, atomic `Task` state. | **v3+** | V2, V3 |

Near-term order: **V1 -> V2 -> V3.** This is the full payoff of parallel
work + shared state, using only existing type-system machinery plus Arc's
one new atomic primitive. No closures, no callable-bound, no intrinsic, no
THIR dependency. Everything in the D-row is deferred and tracked.

Related docs: `SEND_SYNC_DESIGN.md` (the trait system this consumes),
`CHANNEL_DESIGN.md` (the only other `Send`-bound consumer),
`OWNERSHIP_DESIGN.md` / `MOVE_SEMANTICS_DESIGN.md` (the `Own`-move the struct
form relies on), `PROTOCOL_DESIGN.md` (structural `run()` bound),
`CLOSURES_CALLABLE_DESIGN.md` (the deferred closure layer),
`ASYNC_DESIGN.md` (coroutines / create_task / the `exception_ptr` mechanism),
`IR_DESIGN.md` (OQ9 borrow/storage duality -- gates D1/D4, not V1-V3).

## Goal (the ultimate vision, layered)

1. Run `Send`-checked work on real OS threads, move owned data in, get the
   result / exception back. (V1)
2. Share state across threads via `Arc[Mutex[T]]`. (V2, V3)
3. Ergonomics + structure: closure `spawn` (D1) and structured concurrency /
   scoped threads (D4).

All risk and coupling live in layer 3; layers 1-2 need none of it. V1-V3
deliver layers 1-2. The near-term proof point is a CPU-bound benchmark
showing ~2 threads ~= ~2x throughput over CPython -- the concrete "no GIL"
demonstration.

```python
from tpy.thread import spawn

class Blur(Send):                       # Send auto-derived from fields
    src: Own[list[float]]               # data moved in via existing Own-move
    def run(self) -> list[float]: ...

def main() -> None:
    h = spawn(Blur(src=partition))      # task moved into a new OS thread
    other = blur_local()                # runs concurrently on this thread
    print(len(h.join()) + other)        # join() blocks; returns the Send result
```

Compile-time rejection of the unsafe cases:

```python
class Bad(Send): shared: Rc[list[int]]  # error: Rc is non-Send (non-atomic refcount)
spawn(Bad(...))                         # ... so Bad cannot satisfy Send; rejected at construction
```

## Why not CPython threading

CPython's `threading` safety model *is* the GIL. Dropping the GIL (the point
of TPy threads) makes that API a footgun: `Thread(target=lambda:
shared.append(x))` becomes an unsynchronized race with no diagnostic, and the
API has nowhere to express "this is `Send`" or "this is moved in"; `join()`
discards the result and exceptions are swallowed to an excepthook. TPy
follows the model that composes with no-GIL -- Rust's `Send`/`Sync` + move --
in Python syntax. A faithful `threading` port would be actively misleading;
a thin `threading`-shaped convenience could be sugar over the safe core
later, never the safety boundary.

## V1 -- `tpy.thread.spawn` (Runnable-struct)

### Signature and surface

```python
def spawn[R: Send, T: ThreadTask[R]](task: Send[Own[T]]) -> Own[JoinHandle[R]]: ...
# ThreadTask[R] is a structural Protocol with `def run(self) -> R`.
# R: Send because the result crosses the thread boundary (worker -> joiner).
# Fully inferred at the call site: spawn(Blur(...)) -- see the inference note.
```

- `[R, T: ThreadTask[R]]` -- `T` is bounded by a generic structural protocol
  that names the sibling `R`; the concrete `run() -> R`'s return type gives
  `R`, which types `JoinHandle[R]`. This is why the merged bound-fix is a
  prerequisite: validating `T` against `ThreadTask[R]` requires substituting
  the resolved `R` into the bound first (`validate_type_param_bounds`). A
  plain `T: Send` bound does NOT work -- method resolution goes through the
  declared bound, so `task.run()` on a `Send`-bounded `T` errors ("Send has
  no method run"); the structural `ThreadTask[R]` bound is what makes `run()`
  callable and recovers `R`.
- **`Send` is enforced via the `Send[Own[T]]` param wrapper**, not a
  `T: Send` bound (the protocol bound already occupies `T`, and a marker +
  method bound don't combine cleanly). The wrapper is the existing Phase-2
  `Send[T]` marker; it enforces `Send` at the call site with the full
  `why_not_send` chain naming the offending field. Verified
  (`send_tv2.py`-style probe).
- `task: Own[T]` -- the task is **moved** into the thread via the existing
  `Own`-move; the caller loses it. Data is carried as the struct's moved
  fields (the struct is the reified closure); no capture analysis needed.
- **The task is dropped when `run()` completes, not at `join()`** (Rust's
  drop-at-thread-completion). `spawn_thread` moves the task into a body-scoped
  local of the worker lambda, so its fields' `__del__` run as soon as the
  thread finishes -- a peer blocked on the task's RAII cleanup (e.g. a channel
  `Sender.__del__` that closes + notifies) unblocks without waiting for the
  joiner to call `join()`. This also fixes *which thread* runs those
  destructors: they run on the **worker** (at completion), not on the joiner
  (at `join()`). Sound because the task is `Send` -- its fields already crossed
  onto the worker -- and it matches Rust; the only observable effect is that a
  field destructor's side effects now happen on the spawned thread.
- **Fully inferred at the call site** (`spawn(task)`): `T` is inferred
  through the transparent `Send[]` wrapper and `R` via associated-type
  inference from the conformer's `run()` return type (both gaps closed
  post-V1; a fire-and-forget `run() -> None` task also works -- the
  void-like `R` canonicalizes to `None` and the generated concept accepts
  the void-returning conformer via `tpy::proto_result`). The explicit
  `spawn[R, T](...)` form still works but isn't valid CPython (a generic
  function can't be subscripted at runtime), so the V1-era test cases that
  use it stay `no_cpython`; the inferred-form cases run under CPython
  against the `lib/cpy/tpy/thread.py` stub.

`JoinHandle[R]` (v1, minimal): `join(self) -> R`, `detach(self)`. Drop model
(as built): `@nocopy` + `__del__` + a mutable `_consumed` flag (the `Rc`
pattern) -- `join`/`detach` set the flag and then delegate to the raw handle;
`__del__` on an unconsumed handle calls a native `[[noreturn]]` `tpy_panic`
(abort-on-unconsumed-drop). It is a native panic rather than a `raise`
because a TPy `__del__` lowers to a `noexcept` C++ destructor where `throw`
is `-Werror=terminate` (see the Implementation notes at the top). A second
`join`/`detach` after the flag is set `raise`s `RuntimeError` (catchable, an
ordinary method not a destructor). This does **not** require consuming `self`
by move, so the earlier `join(self)`-by-move spike is moot. `is_finished` /
timeout-join / thread name are possible follow-ups (not yet filed).

### Exception propagation and drop policy

- **`join()` re-raises** the task's exception in the joining thread,
  catchable by ordinary `try/except`. **Mechanism as built: C++23
  `std::packaged_task<R()>` + `std::future<R>`** -- `future::get()` already
  does cross-thread result-*or*-rethrow, which is exactly `join()`'s
  semantics, and it handles result storage for free (no hand-rolled
  `exception_ptr` stash needed). Verified end-to-end
  (`tests/cases/threading/spawn_exception`).
- **Abort-on-unconsumed-drop.** `JoinHandle` is `@nocopy`; `join`/`detach`
  flip the `_consumed` flag (they do NOT move-consume `self`). A handle
  dropped without either is a loud runtime panic (matches raw `std::thread`'s
  join-or-detach-or-terminate contract; upholds "exceptions do not vanish").
  Runtime-enforced in v1; upgrades to a compile error if/when linear types
  land.

### Why struct, not closure, for v1

The closure form needs a callable generic bound that the co-validate review
found shaky (see "Deferred: closures"); the struct form delivers the same
payoff with a single ordinary `Send` bound + a structural protocol + move --
all already implemented, all valid Python. The struct is not throwaway: the
later closure layer (D1) desugars *to* this core. Trade accepted: more
verbose user code (a task struct per job) in exchange for zero shaky
type-system work and no THIR dependency.

### V1 prerequisite spikes (all resolved in the shipped V1)

1. **Runtime hand-off -- RESOLVED.** A module-level `@native` template free
   function is the vehicle (precedent: `tpy::as_span` / `tpy::deref_check` in
   `_core/_functions.py`), and the raw handle maps to a C++ template type
   (precedent: `list` -> `std::vector`). The C++ side
   (`runtime/cpp/include/tpy/threading.hpp`) deduces `R` via
   `decltype(task.run())`; codegen emits no explicit template args for the
   native call, so the explicit-TPy-`R` vs C++-`decltype` interplay is a
   non-issue (`R` deduced from the task, TPy `R` types `JoinHandle[R]`).
2. **`join(self)` by move -- MOOT.** The drop model uses `@nocopy` + `__del__`
   + a `_consumed` flag (above), so no move-consume of `self` is needed.
3. **Cross-thread exception catch -- RESOLVED.** `std::packaged_task` +
   `std::future::get()` gives cross-thread result-or-rethrow for free;
   confirmed end-to-end (exception rethrown in the joining thread, caught by
   TPy `try/except` -- `tests/cases/threading/spawn_exception`).

### V1 tests

Shipped under `tests/cases/threading/` (the explicit-type-args cases are
`no_cpython` -- a generic function isn't subscriptable under CPython; the
inferred-form cases run under CPython via `lib/cpy/tpy/thread.py`):

- `spawn_join` -- move-in fork/join happy path: two `@nocopy` tasks run
  concurrently, each owns a `list` moved in and **mutated on the worker**,
  observed via `join()` (proves the move transferred ownership rather than
  silently copied, per the CLAUDE.md reference-type-test rule); also covers
  multiple handles joined and a task with no meaningful data.
- `spawn_exception` -- `join()` re-raises the task's exception, caught by
  `try/except`. `spawn_detach` -- `detach()` consumes, no drop panic.
- `panic_spawn_double_join` / `panic_spawn_unconsumed_drop` -- the two
  runtime panics (double-consume; dropped without join/detach).
- `error_spawn_task_not_send` (non-`Send` task field, `Rc`) /
  `error_spawn_result_not_send` (non-`Send` `R`) -- compile-time rejection
  with the why-not chain.
- Inferred-form cases (post-V1 inference work): `spawn_inferred` (fully
  inferred `spawn(task)`, move observed via worker-side mutation; runs under
  CPython), `spawn_inferred_none` (fire-and-forget `run() -> None`, explicit
  + inferred forms, `@nocopy` task), `error_spawn_inferred_not_send` (Send
  rejection still fires without explicit type args).
- `spawn_task_field_drop` (`no_cpython`) -- the task drops at `run()`
  completion, not `join()`: a field's `__del__` signals a `Condvar` the main
  thread blocks on *before* it joins, so it can only wake if the drop happened
  at thread completion (a regression deadlocks).

## V2 -- `Arc[T]` / `Weak[T]` (BUILT)

Atomic sibling of `Rc` (`tplib/arc.py`), a near-clone whose only behavioral
delta is atomic refcounts. As shipped:

- **`Atomic[T: AnyFixedInt]`** (`tpy.atomic`, `runtime/cpp/include/tpy/atomic.hpp`)
  -- a generic wrapper over `std::atomic<T>`, not an Arc-private helper. The
  `AnyFixedInt` bound is what let us pick generic-over-integers without C++'s
  silent-mutex-for-big-`T` footgun (Rust enumerated concrete `AtomicU32/64/...`
  only because it couldn't ergonomically bound the generic). `@nocopy` but
  movable via a relaxed-load move ctor (a TPy move is exclusive, so it's
  race-free) -- which keeps `_ArcCell` movable for the `unsafe_take` heap-build
  idiom `Rc` uses. Memory orderings cross the boundary as `std::memory_order`
  itself (a `@native("std::memory_order")` `MemoryOrder` enum), so no
  translation layer. Full `std::atomic<integral>` surface; CAS returns
  `(succeeded, observed)`. The user-facing `Atomic` is a thin TPy wrapper over
  the raw `@native` core, which is what lets each op's `MemoryOrder` default to
  `SEQ_CST` (a plain `order: MemoryOrder = MemoryOrder.SEQ_CST` default) and adds
  in-place operators (`+=` etc., seq_cst RMW) and snapshot `str`/`repr` as plain
  method bodies. Binary operators / implicit `int()` are withheld so the racy
  `a = a + 1` (load/store, not atomic) stays a type error.
- **Conditional Send/Sync** -- `Arc`/`Weak` carry
  `@unsafe_send(if_params_send=True, if_params_sync=True)` / `@unsafe_sync(...)` (V2 rung 2), so
  they are `Send + Sync` exactly when `T` is both, matching Rust's
  `unsafe impl<T: Send + Sync>`. No constructor gate: `Arc[non-Send-Sync]` is a
  legal *non-Send* value (like `Arc<Rc<_>>`), rejected only at the thread
  boundary, where the why-not chain names the offending type parameter and the
  marker it lacks.
- **Ordering recipe** (matches `std::sync::Arc`): clone `fetch_add(1, Relaxed)`;
  drop `fetch_sub(1, Release)` + an `Acquire` fence on the final decrement
  before the payload destructor; `upgrade` a CAS loop with `Acquire`.

Composes with V1 spawn by `arc.clone()` into a `@nocopy` task's field, no
closures needed (`tests/cases/threading/arc_spawn` hammers the refcount from
three threads).

**Known limitation (pre-existing, tracked in `BUGS.md`):** a *self-referential*
record holding an `Arc[Node]` (or `Arc[Node] | None`) field -- the natural
Rust-style shared graph/tree node -- is currently mis-derived as **not Send/not
Sync** (so it can't cross a thread boundary), because a non-generic
self-referential record reads its own not-yet-computed Send answer during
registration. This is a general Send/Sync fixpoint gap (it reproduces with
`list[Node]` too, independent of Arc), not specific to Arc; it fails safe
(rejects valid code, no miscompile). Fix pending via `/tpy-fix-bug`.

## V3 -- `Mutex[T]` / `RwLock[T]` (BUILT)

`std::mutex` / `std::shared_mutex` wrappers in `tpy.sync`
(`runtime/cpp/include/tpy/sync.hpp` + `lib/tpy/tpy/sync.py`), tests under
`tests/cases/threading/mutex_*` and `rwlock_shared`. As shipped:

- **The interior-mutability primitives.** `lock()` / `read()` / `write()` are
  `@readonly` -- you acquire through a *shared* handle, not an exclusive one --
  so the canonical `Arc[Mutex[T]]` works: `arc.get()` yields a readonly
  `Mutex`, and `.lock()` still acquires and hands out a *mutable* borrow of the
  payload. This is the interior mutability SEND_SYNC_DESIGN.md flagged Mutex
  would provide (TPy has no `Cell`/`RefCell`). The mechanism is the same
  `unsafe_interior_mutable[Ptr[cell]]` escape hatch `Rc`/`Arc` use for their refcount: the
  lock and payload live in a heap `_MutexCell` reached through a raw `Ptr`, so
  readonly does not propagate into the pointee.
- **Guard = context manager.** `lock()` returns a `@nocopy` `Deref[T]` guard;
  `with m.lock() as g:` acquires in `__enter__` and releases in `__exit__` (via
  the compiler's try/catch scope guard, so release is guaranteed on every exit
  path, including exceptions and early return). A guard that is never entered
  never blocks, so a stray `g = m.lock()` outside a `with` holds no lock.
  Payload access is the `Deref` chain: `with m.lock() as g: g.append(x)`
  forwards through the guard to the list; value-type payloads use
  `g.get()`/`g.set()`. `RwLock`'s read guard yields `readonly[T]`, its write
  guard mutable `T`.
- **Both `Send + Sync` iff `T: Send`** (`@unsafe_send(if_params_send=True)` /
  `@unsafe_sync(if_params_send=True)` on each). For `Mutex` this is Rust's rule
  (`unsafe impl<T: Send>`) -- exclusive access makes a `Send`-but-not-`Sync` `T`
  shareable. For `RwLock` it is *looser* than Rust's
  `unsafe impl<T: Send + Sync> Sync for RwLock<T>`, and sound on the whole *safe*
  surface: the read guard hands out `readonly[T]` to concurrent readers, and a
  safe not-`Sync` `T` is always a container whose not-`Sync`-ness is
  shared-mutability -- which the readonly guard removes, so concurrent reads are
  safe (this is why `Arc[RwLock[list]]` compiles, mirroring `Arc[Mutex[list]]`).
  Rust needs `T: Sync` because its `Cell` is *safe* interior mutability the
  compiler must defend against; TPy has no safe interior mutability, so the
  readonly read guard suffices for every safe payload. It is **not** sound in
  general: for a `Send`-but-not-`Sync` interior-mutable payload built with the
  unsafe `unsafe_interior_mutable` hatch -- a user `Cell`-analog -- the bound
  fabricates a `Sync` the author never asserted, and concurrent readonly reads
  race. Latent today (no such type exists yet), opens on the first user `Cell`.
  The precise fix is RwLock-local -- `Sync iff T: Send AND (freezable(T) OR
  T: Sync)`, *not* the OQ1 `readonly[container]: Sync` refinement -- and low
  priority; see `docs/SEND_SYNC_DESIGN.md` (RwLock Sync bound) and `BUGS.md`.
  No constructor gate; rejected only at the thread boundary, with the why-not
  chain naming the offending type parameter (`error_mutex_not_send`).
- **Movable lock.** `std::mutex` / `std::shared_mutex` delete their move ctors,
  so the cell holding one inline would be non-movable -- but the cell is
  move-constructed once into heap storage at `Mutex.__init__` while still
  exclusively owned and unlocked. `tpy::MovableMutex` / `MovableSharedMutex`
  add that single move ctor (default-construct a fresh lock), exactly the
  `tpy::MovableAtomic` trick.

**Module placement (decided).** `tpy.sync`, parallel to `tpy.atomic`: the
principle is *native-primitive wrappers (a `# tpy: include` header, not
expressible in pure TPy) live in `tpy.*`; pure-TPy smart pointers live in
`tplib.*`*. So `Mutex`/`RwLock` are `tpy.sync` and `Arc` correctly stays
`tplib.arc` (pure-TPy, built on `tpy.atomic`). The canonical pair spans two
packages, honestly reflecting the layering.

**Known cost (filed perf TODO).** `Arc[Mutex[T]]` is two heap allocations (the
Arc cell + the Mutex cell) vs Rust's one. A single-alloc fusion needs either an
address-of-inline-field primitive or `unsafe_interior_mutable` support on a non-`Ptr`
inline field; neither exists today. Correctness-neutral; deferred.

**Guard-lock enforcement + residual limitations (BUGS.md, `/tpy-review` of V3).**
Payload access is gated on the lock being held: each guard carries a `_locked`
flag (set in `__enter__`, cleared in `__exit__`), and `get`/`set`/`__deref__`
raise `RuntimeError` if it isn't set -- so a guard used *outside* its `with`
block (bound but never entered, or read after the block) aborts loudly instead
of racing the payload unsynchronized (`tests/cases/threading/guard_misuse_rejected`;
the CPython stub mirrors the check). What remains is the *lifetime* half: a guard
outliving a dropped bare (non-`Arc`) `Mutex`/`RwLock` still dangles (the flag
lives on the guard, so it can't catch the cell being freed) -- region/lifetime-
model-gated, the known no-region borrow gap -- it awaits the region/lifetime
model.

`set()` takes `Own[T]`: it moves the value into the lock's storage rather than
copying, so a reference-type payload is consumed at the call, not silently
duplicated where CPython aliases. Passing a still-live lvalue therefore warns
(`copies ... into owned storage; use copy() to make this explicit`) -- the
standard acknowledged-copy diagnostic, silenced by `set(x.copy())` (which
restores parity: both sides then hold a copy). An rvalue or last-use lvalue
moves in with no diagnostic. The canonical way to update a reference payload is
still to mutate it in place through the guard's deref, not to replace it via
`set()`. Value-type `set()` (e.g. `Mutex[int32]`) is unaffected -- value types
don't alias.

## Deferred: closures (D1) -- the ergonomic layer, own design pass

`spawn(lambda: work(data))` desugaring to the V1 struct core. Deferred, not
cancelled -- it is the eventual ergonomic face. It needs a **callable generic
bound**, which two independent reviews found shaky as previously specced:

- The spelling `def spawn[F: Callable[[], R], R: Send](fn: Send[F])` uses a
  **dependent callable bound that recovers `R` from `F`**, with `R`
  referenced before declaration. This is **not ordinary Python generic
  machinery** -- a real type checker will not recover `R` from `F` this way,
  which undercuts the "valid Python / tooling-friendly" (goal #3)
  justification the spelling was chosen on. Treat it as a *new type-system
  feature*, not a small bound extension.
- The bound uses `Callable` (the erased `std::function` type) where the
  intended semantics are `Fn`'s (monomorphized, move-only-capable). Reusing
  `Callable` risks `F` erasing to `std::function`, reintroducing the
  copyable-only problem. A dedicated callable-bound concept is likely needed
  rather than overloading `Callable`.
- Owning-capture is underspecified at the hard part: **mixed captures**
  (fields, globals, `readonly` refs, borrowed params, `self`, container
  elements, aliases) and a **phase-ordering** risk (current lambda analysis
  may fix by-ref capture *before* the `Send` check runs).
- THIR is in flight reshaping exactly the capture/move facts owning-capture
  needs, so building this pre-THIR risks building it twice.

Rejected during design (kept so they are not retried): `Send[Fn[...]]`
(parser-rejected: "constrain it with a generic bound instead");
`fn: Fn[[], R]` + a spawn-specific `Send` check (bespoke, per-use special
case); bare `[F: Send](fn: F)` (cannot recover `R`); intersection bound
`Callable[[], R] + Send` (no such syntax in Python; breaks goal #3); a
`spawn` **compiler intrinsic** (explicitly rejected by the project owner --
"add another intrinsic every time?"). The proper path is a well-designed
callable generic bound, revisited with a de-risk spike (wire
`TypeParamRef.bound` to a callable type; confirm `R` recovery and valid
tooling), likely after THIR.

## SHIPPED: `create_task` de-intrinsic (D2 -- bound coroutines)

Independent of threads; shipped as the "bound coroutines" feature. A
coroutine binds to a move-only local holding the CONCRETE frame inline
(`std::optional<__coro_*>`, internal ConcreteCoroType subtyping
Cancellable[T]) -- binding and `await c` never allocate; erasure to
`unique_ptr<Cancellable<T>>` (the `make_adapter` wrap `create_task`'s
arg coercion always emitted) happens exactly at typed boundaries:
`create_task(c)` / `run(c)` args and `Own[Cancellable[T]]`
params/returns (a concrete binding holds one frame type: mixed and
recursive rebinds rejected, create_task named as the recursion escape). A guaranteed representation rule, not
an optimization. Method-coroutine bindings carry receiver borrow rules:
stable-lvalue receiver at bind, borrow registered until consumption,
warnings on receiver-escaping consumption (create_task / return).
`_require_async_def_call_arg` (which had gone half-dead after the v1.2
library cutover) was replaced by a compile-time *coroutine-only* value check
on `create_task`/`run` args -- CPython-parity: the strict rule was kept for
BOTH (CPython's create_task and run raise TypeError/ValueError for
non-coroutine awaitables), implemented as a static-type check, not a
call-shape check.
`_check_no_bare_async_call` now rejects only genuine drops (bare statement,
field/element store). The must-consume guarantee landed as compile
*warnings* (never-consumed / rebind-over-unconsumed; runtime abort-on-drop
a la JoinHandle is unsound for coroutines -- a cancelled task legitimately
drops an unrun frame) plus hard errors for double-consume and use-after-move
via the existing consumed-vars machinery. Note for D1: this is the
consume-discipline substrate owning-capture would reuse; the
all-paths-consumed flow upgrade is a filed TODO.

## Deferred: D3 (movable `Callable`), D4 (scoped/`TaskGroup`), D5 (executor)

- **D3 movable `Callable`** -- re-back with C++23 `std::move_only_function`.
  Independent of everything else; a breaking change (audit found real copy
  sites: `__getitem__` on a `const list[Callable]` returns the
  `std::function` by value, ctor by-value-param -> member copy, lambda ->
  `std::function` return coercion; ~47 snapshot dirs + ~3 codegen copy->move
  patterns; needs the snapshot-policy heads-up). Worth doing on its own
  merits (kills accidental callback copies, storable move-only closures).
- **D4 scoped threads / `TaskGroup`** -- borrow a local into a thread;
  region-gated, behind THIR (same lifetime feature as the deferred async
  borrow-escape work).
- **D5 multi-threaded executor** -- the current executor assumes single
  thread (`_current_executor` plain global, unguarded queues, RAII-teardown
  UAF caveat); a real second thread would corrupt executor state. v3+.

## Why V1-V3 are buildable pre-THIR

The THIR borrow/storage-form work (`IR_DESIGN.md` OQ9) is about *locals'
binding shapes within function bodies*; V1-V3 are about a `Send`-bounded
`Own[T]` parameter, a structural `run()` protocol, an atomic refcount cell,
and lock wrappers -- none of which materialize a new locals' binding-shape
fact. The IR-coupled pieces (closure owning-capture D1, borrow-into-thread
D4) are exactly the ones deferred.

## Co-validate review findings (recorded, both reviewers)

Two independent reviews (this design's author + a Codex staff-engineer pass)
converged on: the callable-bound closure path is the wrong type
(`Callable` vs `Fn`), likely over-scoped, and the capture/consume model is
underspecified; and increment A/D2 is not on the threading critical path.
Codex additionally flagged the invalid-Python dependent-bound (folded into
"Deferred: closures") and two prerequisites now listed as V1 spikes
(`join(self)`-by-move; cross-thread exception catch). The author additionally
raised the runtime hand-off spike (V1 spike 1) and the `std::future`
simplification (adopted for V1). Codex's recommended remedy -- a `spawn`
compiler intrinsic -- was **overridden**: it is the project-owner-vetoed
approach; the scope reduction is achieved instead by the Runnable-struct v1,
which is intrinsic-free.
