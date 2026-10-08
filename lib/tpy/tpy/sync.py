# tpy: include("<tpy/sync.hpp>")
# tpy: link("pthread")
"""Blocking synchronization primitives -- `Mutex[T]` and `RwLock[T]`.

The interior-mutability primitives: a `Mutex[T]` / `RwLock[T]` is `@nocopy`,
holds an inner `T`, and hands out scoped access to it through a guard used as
a context manager. `lock()` / `read()` / `write()` are `@readonly` (they acquire
through a *shared* handle -- you do not need exclusive ownership to lock), so
the canonical `Arc[Mutex[T]]` works: `arc.get()` yields a readonly `Mutex`, and
`.lock()` still acquires and hands out a mutable borrow of the payload.

    data = Arc.new(Mutex.new([0]))
    worker = data.clone()               # share into a spawned thread
    with data.lock() as g:              # auto-derefs through Arc; acquires here
        g.append(1)                     # mutate the payload; released at block exit

`Mutex[T]` and `RwLock[T]` are both `Send + Sync` iff `T` is `Send`. This
matches `Mutex`'s Rust rule; for `RwLock` it is *looser* than Rust's
`unsafe impl<T: Send + Sync>`, and sound on the safe surface: a safe not-`Sync`
`T` is always a container whose not-`Sync`-ness is shared-mutability that the
readonly read guard removes (TPy has no safe interior mutability, unlike Rust's
`Cell`, so the guard suffices for every safe payload: a safe `Send` `T` holds no
writable `Ptr`, and the `@unsafe_send` types that do -- `Arc`, `Mutex`,
`RwLock` -- expose their payload only through readonly-following accessors). It
is NOT sound in general: a `Send`-but-not-`Sync` `@unsafe_send` payload that
mutates through a pointer from a readonly method (a user `Cell`-analog) gets a
`Sync` the author never asserted, and concurrent readonly reads race -- a latent
hole, low priority. The guarantees also assume no caller touches the `_`-prefixed
implementation fields (`_payload` reached around the lock). See
docs/SEND_SYNC_DESIGN.md (RwLock Sync bound) and BUGS.md.
"""
from __future__ import annotations
from typing import Protocol, Self
from tpy import (Own, Ptr, uint32, Deref, nocopy, readonly, auto_readonly,
                 take_ptr, unsafe_send, unsafe_sync)
from tpy.mem import UninitStorage
from tpy.unsafe import unsafe_take, unsafe_release, unsafe_store
from tpy.extern import native


@native("tpy::MovableMutex")
@nocopy
class _RawMutex:
    def __init__(self) -> None: ...
    def lock(self) -> None: ...
    def unlock(self) -> None: ...


@native("tpy::MovableSharedMutex")
@nocopy
class _RawSharedMutex:
    def __init__(self) -> None: ...
    def lock(self) -> None: ...
    def unlock(self) -> None: ...
    def lock_shared(self) -> None: ...
    def unlock_shared(self) -> None: ...


@native("tpy::MovableConditionVariable")
@nocopy
class _RawCondvar:
    def __init__(self) -> None: ...
    def wait(self, m: Ptr[_RawMutex]) -> None: ...
    def notify_one(self) -> None: ...
    def notify_all(self) -> None: ...


# The heap cell holding the lock next to its payload. Reached only through the
# `Ptr` a Mutex/guard carries; readonly does not reach through a `Ptr`, so
# locking or writing the payload through it leaves a shared (readonly) Mutex
# handle readonly -- the same shape as Rc/Arc's `_cell`.
@nocopy
class _MutexCell[T]:
    _mu: _RawMutex
    storage: UninitStorage[T]

    def __init__(self) -> None:
        self._mu = _RawMutex()
        self.storage = UninitStorage[T]()

    def __del__(self) -> None:
        self.storage.reset()

    def lock(self) -> None:
        self._mu.lock()

    def unlock(self) -> None:
        self._mu.unlock()


@nocopy
class _RwLockCell[T]:
    _mu: _RawSharedMutex
    storage: UninitStorage[T]

    def __init__(self) -> None:
        self._mu = _RawSharedMutex()
        self.storage = UninitStorage[T]()

    def __del__(self) -> None:
        self.storage.reset()

    def lock(self) -> None:
        self._mu.lock()

    def unlock(self) -> None:
        self._mu.unlock()

    def lock_shared(self) -> None:
        self._mu.lock_shared()

    def unlock_shared(self) -> None:
        self._mu.unlock_shared()


@nocopy
@unsafe_send(if_params_send=True)
@unsafe_sync(if_params_send=True)
class Mutex[T]:
    # lock() (a @readonly method reached through a shared Arc handle) mutates
    # the lock and hands out a mutable payload borrow through these pointers:
    # readonly protects the handle, not what it points at. `_payload` aliases
    # into the cell's storage; the mutable borrow the guard yields is exactly
    # what the lock's runtime exclusion makes sound -- touching `_payload`
    # directly bypasses the lock.
    _cell: Ptr[_MutexCell[T]]
    _payload: Ptr[T]

    def __init__(self, value: Own[T]) -> None:
        cell = unsafe_take(_MutexCell[T]())
        cell.storage.construct(value)
        self._cell = cell
        self._payload = cell.storage.ptr()

    def __del__(self) -> None:
        unsafe_release(self._cell)

    @staticmethod
    def new(value: Own[T]) -> Own[Mutex[T]]:
        return Mutex[T](value)

    @readonly
    def lock(self) -> Own[MutexGuard[T]]:
        return MutexGuard[T](self._cell, self._payload)


# Guards enforce their lock at runtime: payload access is only valid while the
# lock is held, i.e. inside the guard's `with` block. `_locked` gates every
# access so misuse -- a guard bound without `with` (never acquired), or read
# after the block (the `with` as-var outlives the block) -- aborts loudly
# instead of racing the shared payload unsynchronized. This is a runtime
# backstop, not a static guarantee; the static tie awaits the region model
# (BUGS.md). It does not cover a guard outliving a dropped bare Mutex (hole 3).
def _require_locked(locked: bool) -> None:
    if not locked:
        raise RuntimeError(
            "lock guard used without holding the lock -- access the payload "
            "only inside the guard's `with` block")


@nocopy
class MutexGuard[T](Deref[T]):
    _cell: Ptr[_MutexCell[T]]
    _payload: Ptr[T]
    _locked: bool
    # A pointer to the cell's raw lock, for Condvar.wait's `_CondvarLock` hook,
    # so `_raw_mutex` can be @readonly -- callable through wait's readonly
    # param. Points into the cell (no allocation); the cell outlives the guard.
    _raw_mu: Ptr[_RawMutex]

    def __init__(self, cell: Ptr[_MutexCell[T]], payload: Ptr[T]) -> None:
        self._cell = cell
        self._payload = payload
        self._locked = False
        self._raw_mu = take_ptr(cell._mu)

    # Acquire on enter (not at lock()): a guard that is never entered never
    # blocks, so a stray `g = m.lock()` outside a `with` holds no lock -- and
    # `_locked` stays False, so any payload access on it aborts.
    def __enter__(self) -> Self:
        self._cell.lock()
        self._locked = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._locked = False
        self._cell.unlock()

    def __deref__(self) -> T:
        _require_locked(self._locked)
        return self._payload

    # Access-polymorphic like __deref__: a readonly guard hands out a
    # readonly payload.
    @auto_readonly
    def get(self) -> auto_readonly[T]:
        _require_locked(self._locked)
        return self._payload

    # `Own[T]`, not `T`: this writes into the lock's owned storage, so a borrowed
    # reference payload would be copied where CPython aliases the passed object.
    # Moving in consumes the source, so the two models are indistinguishable.
    # It writes only through `_payload`, so readonly inference would make it
    # callable on a readonly guard, which hands out a readonly payload.
    @readonly(False)
    def set(self, value: Own[T]) -> None:
        _require_locked(self._locked)
        unsafe_store(self._payload, uint32(0), value)

    # Internal hook for `Condvar.wait` (`_CondvarLock` structural conformance):
    # hands out the raw lock this guard holds so users never touch `_RawMutex`.
    # @readonly (`_raw_mu` is a `Ptr`, which readonly does not reach through) so
    # it stays callable through wait's readonly param. Only the exclusive Mutex guard conforms;
    # RwLock's shared_mutex guards don't (a condvar pairs with an exclusive lock).
    @readonly
    def _raw_mutex(self) -> Ptr[_RawMutex]:
        # Gated like every other accessor: handing out the raw lock from a guard
        # that never acquired it would let Condvar.wait unlock a std::mutex this
        # thread doesn't hold (UB), instead of a loud RuntimeError.
        _require_locked(self._locked)
        return self._raw_mu


@nocopy
@unsafe_send(if_params_send=True)
# Sync iff T: Send -- same as Mutex. Looser than Rust's RwLock<T> (needs
# T: Send + Sync) and sound on the safe surface: a safe not-Sync T is always a
# container whose shared-mutability the readonly read guard removes, and TPy has
# no safe interior mutability (unlike Rust's Cell). NOT sound in general: a
# Send-but-not-Sync `@unsafe_send` payload that writes through a pointer from a
# readonly method (a user Cell-analog) gets a Sync the author never asserted --
# a latent hole, low priority. See docs/SEND_SYNC_DESIGN.md (RwLock Sync bound)
# and BUGS.md.
@unsafe_sync(if_params_send=True)
class RwLock[T]:
    _cell: Ptr[_RwLockCell[T]]
    _payload: Ptr[T]

    def __init__(self, value: Own[T]) -> None:
        cell = unsafe_take(_RwLockCell[T]())
        cell.storage.construct(value)
        self._cell = cell
        self._payload = cell.storage.ptr()

    def __del__(self) -> None:
        unsafe_release(self._cell)

    @staticmethod
    def new(value: Own[T]) -> Own[RwLock[T]]:
        return RwLock[T](value)

    @readonly
    def read(self) -> Own[ReadGuard[T]]:
        return ReadGuard[T](self._cell, self._payload)

    @readonly
    def write(self) -> Own[WriteGuard[T]]:
        return WriteGuard[T](self._cell, self._payload)


@nocopy
class ReadGuard[T](Deref[readonly[T]]):
    _cell: Ptr[_RwLockCell[T]]
    _payload: Ptr[readonly[T]]
    _locked: bool

    def __init__(self, cell: Ptr[_RwLockCell[T]], payload: Ptr[readonly[T]]) -> None:
        self._cell = cell
        self._payload = payload
        self._locked = False

    def __enter__(self) -> Self:
        self._cell.lock_shared()
        self._locked = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._locked = False
        self._cell.unlock_shared()

    def __deref__(self) -> readonly[T]:
        _require_locked(self._locked)
        return self._payload

    @readonly
    def get(self) -> readonly[T]:
        _require_locked(self._locked)
        return self._payload


@nocopy
class WriteGuard[T](Deref[T]):
    _cell: Ptr[_RwLockCell[T]]
    _payload: Ptr[T]
    _locked: bool

    def __init__(self, cell: Ptr[_RwLockCell[T]], payload: Ptr[T]) -> None:
        self._cell = cell
        self._payload = payload
        self._locked = False

    def __enter__(self) -> Self:
        self._cell.lock()
        self._locked = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._locked = False
        self._cell.unlock()

    def __deref__(self) -> T:
        _require_locked(self._locked)
        return self._payload

    # Access-polymorphic like __deref__: a readonly guard hands out a
    # readonly payload.
    @auto_readonly
    def get(self) -> auto_readonly[T]:
        _require_locked(self._locked)
        return self._payload

    # `Own[T]`, not `T`: this writes into the lock's owned storage, so a borrowed
    # reference payload would be copied where CPython aliases the passed object.
    # Moving in consumes the source, so the two models are indistinguishable.
    # It writes only through `_payload`, so readonly inference would make it
    # callable on a readonly guard, which hands out a readonly payload.
    @readonly(False)
    def set(self, value: Own[T]) -> None:
        _require_locked(self._locked)
        unsafe_store(self._payload, uint32(0), value)


class _CondvarLock(Protocol):
    # A held exclusive lock a Condvar can wait on. MutexGuard conforms; this
    # keeps `_RawMutex` out of Condvar.wait's signature -- callers pass the guard
    # and wait extracts the raw lock through the hook. Plain (non-@dynamic)
    # Protocol -> monomorphized per guard type, so the call is static, no vtable.
    @readonly
    def _raw_mutex(self) -> Ptr[_RawMutex]: ...


@nocopy
@unsafe_send
@unsafe_sync
class Condvar:
    # wait()/notify() mutate the std::condition_variable `_cv` points at
    # through a shared (readonly) handle -- readonly does not reach through
    # the pointer -- sound because the primitive is internally synchronized,
    # exactly how a @readonly Mutex.lock() mutates its std::mutex. Send+Sync
    # unconditionally (the C++ primitive is thread-safe), matching Atomic.
    # TODO: the Ptr (and its heap alloc via unsafe_take) is here only to keep
    # the primitive mutable behind a readonly handle. Unlike Mutex/Rc/Arc,
    # nothing holds a pointer INTO the Condvar, so once
    # unsafe_interior_mutable accepts an inline `mutable` member (TODO.md),
    # this becomes an alloc-free `unsafe_interior_mutable[_RawCondvar]`.
    _cv: Ptr[_RawCondvar]

    def __init__(self) -> None:
        self._cv = unsafe_take(_RawCondvar())

    def __del__(self) -> None:
        unsafe_release(self._cv)

    # Atomically release the lock the caller holds (pass the live guard), block
    # until notified, then reacquire before returning. Takes the guard via the
    # `_CondvarLock` structural hook -- static (monomorphized), and `_RawMutex`
    # never appears in the signature. Spurious wakeups possible -> loop.
    @readonly
    def wait(self, lock: _CondvarLock) -> None:
        self._cv.wait(lock._raw_mutex())

    @readonly
    def notify_one(self) -> None:
        self._cv.notify_one()

    @readonly
    def notify_all(self) -> None:
        self._cv.notify_all()
