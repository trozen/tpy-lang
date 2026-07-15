# Channel Design

## Status

`Channel` is the **first enforcement site for the Send marker layer**
(`docs/SEND_SYNC_DESIGN.md` Phase 4). It is an intra-process, async
message queue running on the single-threaded executor
(`docs/ASYNC_DESIGN.md`).

| Aspect | Decision |
|--------|----------|
| API shape | Rust-style split: `channel[T: Send](cap)` returns `(Sender[T], Receiver[T])` |
| First cut (v1) | **SPSC** -- single producer, single consumer; minimal surface (see "v1 scope") |
| Shared state | `Rc`-backed (single-threaded; non-atomic). **Not** `Arc` -- see "Cross-thread channels" |
| Backing buffer | fixed-capacity FIFO ring on `UninitHeapStorage[T]` |
| Send enforcement | the `T: Send` bound on the `channel[...]` **factory** is the user-facing gate; checked by Phase-2 bound machinery -- **no new compiler code** |
| Blocking | `await tx.send(v)` suspends when full; `await rx.recv()` suspends when empty (single-waiter park, mirroring `Future`) |
| Shutdown | explicit `tx.close()` is the primary mechanism; `recv` raises `ChannelClosed` once closed and drained. Destructor close is a best-effort backstop, not relied upon |

### v1 scope (deliberately minimal)

Two independent reviews (self + Codex) converged on shipping the smallest
artifact that *proves the enforcement* on a *real* primitive, deferring
everything whose ownership/timing semantics aren't yet nailed down:

**In v1:** `channel[T: Send](cap)` factory; `Sender.send` (async),
`Sender.close`; `Receiver.recv` (async, raises `ChannelClosed` on
closed+drained). `@nocopy`, non-clonable handles. Single-waiter park.

**Deferred (with the reason):**

- `try_send` -- cannot be ownership-correct with an `Own[T]` param and a
  `bool` return (the value can't be handed back on the full path; see
  "Why no try_send"). Needs `-> Own[T] | None` or a result type; revisit.
- `try_recv` / `recv() -> T | None` -- `None`-as-closed is ambiguous when
  `T` is itself nullable, because TPy unions flatten
  (`(X | None) | None == X | None`). v1 signals closure by raising, not by
  a sentinel. A non-flattening receive result (`Recv[T] = Item(T) | Closed`)
  is the ergonomic future form.
- MPSC (multi-producer): the `_senders` counter and `Sender.clone()`. The
  split API makes this purely additive (see "SPSC -> MPSC").
- Drop-driven multi-sender shutdown: relying on destructor timing for
  close is fragile across async frames / temporaries / panic paths and is
  nondeterministic under CPython. v1 uses explicit `close()`.
- MPMC, capacity-0 (rendezvous), and the Arc-backed cross-thread channel
  (Phase 6).

## Why this is the first enforcement site

The Send/Sync marker layer (Phases 1-3) is unobservable until something
*uses* it. `Channel[T]` is the smallest real consumer: a value handed to
`send` is conceptually transferred to whoever calls `recv`, so the type
that flows through must be `Send`. On the single-threaded executor the
transfer never actually crosses a thread, so the runtime is a plain
buffer -- but the type check fires unconditionally, making programs
**forward-compatible** with the future multi-threaded executor (Phase 5)
without a later migration of their payload types.

Phase 2 already validates the `T: Send` bound at every generic-function
call (`tpyc/sema/calls.py:validate_type_param_bounds`, exercised by
`tests/cases/send_sync/error_bound_not_send`). So **Phase 4 adds no
compiler code** -- it is a TPy-native library type plus tests, exactly as
`docs/SEND_SYNC_DESIGN.md` predicted ("No additional enforcement code;
just the new API types").

### The Send bound is policy, not runtime necessity (accepted tradeoff)

On a single-threaded executor, requiring `T: Send` is stricter than
thread-safety requires: passing a non-Send payload (e.g. `Rc[Widget]`)
between two coroutines on the same thread is in fact safe. `channel[T: Send]`
forbids it anyway. This is a **deliberate choice of forward-compatibility
over single-thread permissiveness** -- the entire reason Phase 4 picks the
channel as the first enforcement site (`docs/SEND_SYNC_DESIGN.md` Problem
Statement: avoid the Swift `Sendable` retrofit). The cost: same-thread code
that wants to shuttle non-Send values through a queue can't use this type.

A future `local_channel[T]` (no `Send` bound, single-thread-only, never
forward-compatible with the MT executor) is a reasonable **complement** --
not a replacement -- and would coexist with `channel[T: Send]` the way Rust
ships several channel flavors. Out of scope for Phase 4.

## Surface API

```python
from tpy.channel import channel, Sender, Receiver, ChannelClosed

class Counter(Send):
    count: Int32

class SharedCache:
    data: Ptr[Buffer]            # raw pointer -> not Send

def main() -> None:
    tx, rx = channel[Counter](4)        # ok
    bad_tx, bad_rx = channel[SharedCache](4)
    #   error: Type argument 'SharedCache' does not satisfy bound 'Send'
    #          ... why-not chain (field 'data: Ptr[Buffer]' is not Send)
```

Async producer/consumer with explicit shutdown:

```python
async def producer(tx: Own[Sender[Int32]]) -> None:
    for i in range(5):
        await tx.send(i)             # suspends if the buffer is full
    tx.close()                       # signal end-of-stream

async def consumer(rx: Own[Receiver[Int32]]) -> None:
    while True:
        try:
            print(await rx.recv())   # suspends if empty
        except ChannelClosed:
            break                    # closed and drained

async def main_co() -> None:
    tx, rx = channel[Int32](2)
    p = create_task(producer(tx))
    c = create_task(consumer(rx))
    await p
    await c
```

### Methods (v1)

`Sender[T: Send]` (`@nocopy`, non-clonable):

| Method | Signature | Behavior |
|--------|-----------|----------|
| `send` | `async send(self, value: Own[T]) -> None` | suspends while full; raises `ChannelClosed` if the receiver is gone |
| `close` | `close(self) -> None` | marks the channel closed; wakes a parked receiver so its next `recv` drains then raises |

`Receiver[T: Send]` (`@nocopy`):

| Method | Signature | Behavior |
|--------|-----------|----------|
| `recv` | `async recv(self) -> T` | suspends while empty; raises `ChannelClosed` once the buffer is empty **and** the channel is closed (or all senders gone) |

`channel[T: Send](capacity: Int32) -> tuple[Own[Sender[T]], Own[Receiver[T]]]`
-- factory. `capacity >= 1`; `capacity < 1` raises at runtime (no
compile-time const-capacity check). The tuple-of-`Own[nocopy]` return and
`tx, rx = channel[...]()` unpacking work via the owned-tuple-unpack move-out
feature (landed on master): each `Own` handle is moved out of the consumed
factory-result tuple into its own movable local.

**`T` must be written explicitly** (`channel[Int32](4)`): `capacity` gives
no inference source for `T`, so a bare `channel(4)` is a "cannot infer"
error. The explicit `channel[Int32](...)` subscript is a tpyc-only spelling --
a generic *function* is not subscriptable in CPython (`TypeError`), unlike a
generic *type* like `list[int]` -- which is why these runtime cases are
`no_cpython` (see also the `spawn[R, T]` note in `docs/THREADING_DESIGN.md`).

### Why no `try_send` in v1

`try_send(value: Own[T]) -> bool` is not ownership-correct: an `Own[T]`
param transfers ownership into the callee, so on a full buffer the value
cannot be returned to the caller via a `bool` -- it would simply be
destroyed. "Check fullness before taking the slot" does not change the API
boundary: the caller has already moved the value in. A correct
non-blocking send needs `-> Own[T] | None` (value back on failure) or a
result type. Deferred until that shape is decided. The async `send` has no
such problem: it holds `value` in its frame slot across the suspension and
pushes once space frees.

## Enforcement: where the `T: Send` check fires

The **user-facing gate is the `channel[T: Send](cap)` factory** -- a
generic function whose bound is validated at the call site
(`validate_type_param_bounds`). This is the path users hit
(`channel[SharedCache](4)`), and the most-exercised bound path. The failure
renders the Phase-3 why-not-send chain (`tpyc/sema/send_chain.py`)
automatically, since `validate_type_param_bounds` appends it
(`_send_sync_bound_detail` in `calls.py`).

The `Sender` / `Receiver` / `_ChanState` records also carry the `T: Send`
bound for consistency, but v1 does **not** claim users are blocked from
spelling a non-Send instantiation through every path: a *bare annotation*
that does not construct (`rx: Receiver[SharedCache]`) is **not**
bound-checked (`tpyc/sema/type_ops.py` `validate_record_type_args` ignores
`type_param_bounds`). This is a **pre-existing gap affecting every bounded
generic record**, not Channel-specific. Since users obtain handles only via
the factory, the factory gate is the effective enforcement. Tracked in
`BUGS.md`; closing it (validating record type-arg bounds at annotation
sites) would harden this and every other bounded record.

## Runtime backing

No new C++. The ring buffer is TPy code over `UninitHeapStorage[T]`
(`lib/tpy/tpy/mem.py`) -- the same uninitialized, `Own[T]`-aware,
nocopy-safe storage `Future[T]` uses, but with `capacity` slots instead of
one. Suspension reuses the executor's `Waker` / `Poll` surface
(`lib/tpy/tpy/coro`), exactly as `Future` / `Event` / `SleepFuture` do.

### Internal shape (sketch)

```python
@nocopy
class _ChanState[T: Send]:
    _buf: UninitHeapStorage[T]
    _cap: Int32
    _head: Int32          # index of the oldest buffered element
    _count: Int32         # number of buffered elements (0.._cap)
    _closed: bool         # set by Sender.close() (or last-sender teardown)

    # SPSC: at most one parked waiter per side.
    _send_waker: Waker    # a producer parked because the buffer is full
    _has_send_waiter: bool
    _recv_waker: Waker    # the consumer parked because the buffer is empty
    _has_recv_waiter: bool

    # Required: UninitHeapStorage is *uninitialized* storage -- it does NOT
    # drop live slots itself, so teardown must drain the buffered elements
    # (mirrors Future.__del__'s take0()). Without this they leak.
    def __del__(self) -> None:
        i = 0
        while i < self._count:
            self._buf.take((self._head + i) % self._cap)   # drop element
            i += 1
```

`Sender` / `Receiver` each hold an `Rc[_ChanState[T]]` (shared lifetime of
the state). `Rc` keeps `_ChanState` alive until *both* handles drop;
`_ChanState.__del__` then drains any remaining buffered elements.

**Destructor backstop.** `Sender.__del__` sets `_closed` and wakes a parked
receiver -- so a *forgotten* `close()` still eventually unblocks the
consumer (Rust-style "drop also closes"). This is a **safety net, not the
contract**: `close()` is the tested, deterministic mechanism (see "Why
explicit close()"). Relying on destructor *timing* is unsound here --
empirically, a handle moved into a coroutine frame is destroyed when the
executor drops the completed `Task`, not when the producer coroutine
returns, so a consumer parked on the close-wake could see it late or
deadlock. Moved-from destructor suppression is verified (a moved `Sender`'s
husk does not re-run `__del__`), so the backstop is safe to keep.

### Blocking via single-waiter park (SPSC)

`send` and `recv` suspend through small internal awaitables that mirror
`SleepFuture`: a `__poll__` that checks the buffer and parks the side's
single waker when it can't proceed. (`send` / `recv` are `async def`
methods that `await` one of these; the poll logic lives on the awaitable,
not the method.)

- `_RecvReady.__poll__`: if `_count > 0`, pop from `_head`, clear
  `_has_recv_waiter`, wake a parked sender, return `Ready(value)`; else if
  `_closed`, return -- recv then raises `ChannelClosed`; else park
  `_recv_waker`, set `_has_recv_waiter`, return `Pending`.
- `_SendReady.__poll__`: if `_closed`, recv-side gone -> `send` raises
  `ChannelClosed`; else if `_count < _cap`, push at `(_head + _count) % _cap`,
  clear `_has_send_waiter`, wake a parked receiver, return `Ready`; else
  park `_send_waker`, set `_has_send_waiter`, return `Pending`.

Each side clears its waiter flag (and lets the stale `Waker` go) on the
poll that consumes it, mirroring `Future`'s `_has_waiter = False`.

A second concurrent waiter on the same side is unreachable in SPSC: the
handles are `@nocopy` and single-owner, so only one coroutine can hold the
`Sender` and one the `Receiver`. The code keeps a defensive assert there,
but it is not a testable path (no valid program can construct the
violation) -- MPSC is what turns the sender slot into a queue.

## Cross-thread channels (Phase 6, Arc-backed) -- forward pointer

This channel is **`Rc`-backed and therefore single-threaded**, correct for
the Phase-4 executor and matching Rust's split (`Rc` in-thread, `Arc`
cross-thread). Because `Rc` is non-Send, `Sender[T]` / `Receiver[T]` are
themselves non-Send -- invisible in Phase 4. When Phase 5's multi-threaded
executor lands and a migrating `Task` captures a `Sender`, the Send/Sync
layer will correctly reject it; that diagnostic is the marker layer doing
its job, not a gap. The cross-thread channel (Phase 6, once `Arc[T]`
exists) is an `Arc`-backed variant with the same `Sender` / `Receiver`
surface, likely a distinct coexisting type. The `T: Send` payload gate
shipped here is unchanged; only the internal handle and the handles' own
Send-ness differ. To be precise about what is forward-compatible: the
**payload type** `T` is gated now so it stays valid under the MT executor;
the **handles** are intentionally local (non-Send) until Phase 6 -- this is
not a proto cross-thread channel.

**Phase-5 regression test (future):** once task migration exists, capturing
a `Sender[T]` into a migrating task must fail *even when `T` itself is
Send*, because the `Rc[_ChanState[T]]` chain is non-Send. That failure is
the proof the split is working as intended.

## SPSC -> MPSC upgrade path

The split API makes MPSC purely additive, with no surface change:

- Today: `Sender` is non-clonable; the sender side has one waker slot.
- MPSC: add a `_senders` count, turn `_send_waker` / `_has_send_waiter`
  into a `list[Waker]` park-queue woken in FIFO order, and allow
  `Sender.clone()` (bumping the count; last clone dropped -> closed). The
  receiver side never changes (the "SC").

Existing SPSC programs keep compiling unchanged; only `Sender.clone()`
becomes newly available.

## Blocking cross-thread MPSC channel (`tplib.channel`) -- DRAFTED, BLOCKED

A separate, *blocking* channel over real OS threads (`tpy.thread.spawn`),
distinct from the async SPSC channel above. This is the "Go-style channel"
form: `thread.spawn` is the goroutine, `channel[T: Send](cap)` returns a
`(Sender[T], Receiver[T])` pair, and `send`/`recv` block the OS thread. TPy has
no M:N scheduler, so each producer is an OS thread; select and unbuffered
(rendezvous) are out of scope; v1 is multi-producer / **single**-consumer.

**Design (implemented, correct modulo the compiler gaps below):**
- Prerequisite **shipped**: `tpy.sync.Condvar` (blocking condition variable over
  `std::condition_variable`; see `docs/LANGUAGE_FEATURES.md` and the
  `condvar_ping_pong` test).
- State: `Arc[_ChanState[T: Send]]` where `_ChanState` = `Mutex[_Buf[T]]`
  (fixed-cap ring + `head`/`count`/`closed`/`senders`) + a `_not_full` and a
  `_not_empty` `Condvar`. Handles are `Arc`-backed, hence `Send` iff `T` is, so
  they cross threads -- unlike the `Rc`-backed async channel.
- `Sender`: `send` (blocks while full; standard `while full and not closed:
  not_full.wait(g)`), `clone()` (bumps a `_senders` count),
  `close()`; `Receiver`: `recv() -> Own[T]` raising `ChannelClosed` when empty
  and closed, plus a generator `__iter__` (`for v in rx:`). Lives in `tplib`
  (not `tpy.thread`) because it composes `tplib.arc.Arc`: the low-level `tpy.*`
  layer must not depend on the higher `tplib.*` layer.
- Close semantics (agreed): drop a `Sender` = "this producer is done"; the last
  sender's drop auto-closes; any `close()` force-closes channel-wide.
- `no_cpython`: `tplib` is a symlinked shared source (no separate cpy stub), and
  the `channel[T](cap)` factory needs an explicit type argument (capacity gives
  no inference for `T`) which is not valid Python -- same as the async channel.

**Blocked on one compiler gap (`BUGS.md`), paused pending the fix:**
1. `for v in rx:` over the generator `__iter__` fails because the receiver is
   inferred `const` (the const-receiver-blocks-lowering family).

The `with`-in-`__del__` rethrow gap (previously blocker 1) is fixed: `with`/
`try` cleanup in a destructor compiles and runs, so `Sender`/`Receiver.__del__`
can take the lock to close + notify (the normal path does not throw). An
exception that *does* escape a destructor now fail-fasts (report to stderr +
abort), not report-and-continue -- see the "Exceptions escaping `__del__`"
note in `docs/LANGUAGE_FEATURES.md`. `Condvar` (the other prerequisite) shipped
independently; resume the channel once the remaining gap is fixed.

## CPython parity

No CPython stub. The channel runs on TPy's single-threaded asyncio
executor, and there is no `lib/cpy/asyncio` -- so, like every other
asyncio-*runtime* case (`asyncio_event`, `asyncio_gather_*`), the
channel's runtime tests carry `no_cpython.txt`. The enforcement test is a
compile-error case that never reaches the cpy phase, so it needs nothing.
(An earlier draft proposed an `asyncio.Queue`-backed stub; that was
dropped to match the established asyncio-runtime no_cpython convention --
a stub would also need `lib/cpy/asyncio`, which doesn't exist.)

## Test plan (new `tests/cases/channel/`)

| Case | Kind | Covers |
|------|------|--------|
| `error_channel_not_send` | error | `channel[SharedCache](4)` -> bound failure + why-not chain |
| `channel_async` | happy | producer/consumer via `create_task` + split handles; blocking on full (small `cap`) and on empty; explicit `close()` -> `recv` raises `ChannelClosed`. Reference-type payload **mutated and observed** after the `send`/`recv` boundary (or a `@nocopy` payload) to force the value-vs-reference distinction per CLAUDE.md |
| `panic_channel_capacity` | panic | `channel[Int32](0)` -> runtime error |

`channel_async` and `panic_channel_capacity` carry `no_cpython.txt` (see
CPython parity). The `@nocopy Counter` payload on `channel_async` satisfies
the reference-type happy-test rule -- a silent copy at the `send` / `recv`
boundary would be a compile error, so the move-through-channel is forced.

## Docs to update on landing

- `docs/SEND_SYNC_DESIGN.md` -- Phase 4 status `Planned` -> `Done` (note v1 scope).
- `docs/FEATURE_ROADMAP.md` -- G2 (Channels) `Not started` -> in-progress.
- `docs/LANGUAGE_FEATURES.md` -- new `Channel` entry.
- `BUGS.md` -- the bare-annotation bound-check gap.

## Cross-references

- `docs/SEND_SYNC_DESIGN.md` -- the marker layer; Phase 4 enforcement.
- `docs/ASYNC_DESIGN.md` -- single-threaded executor, `Waker` / `Poll`.
- `lib/tpy/asyncio/__init__.py` -- `Future` / `Event` / `SleepFuture`, the
  awaitable patterns this mirrors.
- `lib/tpy/tplib/rc.py` -- `Rc[T]` shared ownership.
- `lib/tpy/tpy/mem.py` -- `UninitHeapStorage[T]` buffer backing.
