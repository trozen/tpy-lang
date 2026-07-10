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
`Cell`, so the guard suffices for every safe payload). It is NOT sound in
general: a `Send`-but-not-`Sync` interior-mutable payload from the unsafe
`unsafe_interior_mutable` hatch (a user `Cell`-analog) gets a `Sync` the author
never asserted, and concurrent readonly reads race -- a latent hole, low
priority. See docs/SEND_SYNC_DESIGN.md (RwLock Sync bound) and BUGS.md.
"""
from __future__ import annotations
from typing import Self
from tpy import (Own, Ptr, UInt32, Deref, nocopy, readonly, unsafe_interior_mutable,
                 unsafe_send, unsafe_sync)
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


# The heap cell holding the lock next to its payload. Reached only through the
# raw `Ptr` a Mutex/guard carries, so mutating the lock or payload through it
# never demotes the shared (readonly) Mutex handle -- the interior-mutability
# escape hatch, same shape as Rc/Arc's `_cell`.
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
    # Both fields are `unsafe_interior_mutable` so lock() (a @readonly method reached
    # through a shared Arc handle) can mutate the lock and hand out a mutable
    # payload borrow: readonly does not propagate into an interior field's
    # pointee. `_payload` aliases into the cell's storage; the mutable borrow it
    # yields is exactly what the lock's runtime exclusion makes sound.
    _cell: unsafe_interior_mutable[Ptr[_MutexCell[T]]]
    _payload: unsafe_interior_mutable[Ptr[T]]

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

    def __init__(self, cell: Ptr[_MutexCell[T]], payload: Ptr[T]) -> None:
        self._cell = cell
        self._payload = payload
        self._locked = False

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

    def get(self) -> T:
        _require_locked(self._locked)
        return self._payload

    def set(self, value: T) -> None:
        _require_locked(self._locked)
        unsafe_store(self._payload, UInt32(0), value)


@nocopy
@unsafe_send(if_params_send=True)
# Sync iff T: Send -- same as Mutex. Looser than Rust's RwLock<T> (needs
# T: Send + Sync) and sound on the safe surface: a safe not-Sync T is always a
# container whose shared-mutability the readonly read guard removes, and TPy has
# no safe interior mutability (unlike Rust's Cell). NOT sound in general: a
# Send-but-not-Sync interior-mutable payload from the unsafe
# `unsafe_interior_mutable` hatch (a user Cell-analog) gets a Sync the author
# never asserted -- a latent hole, low priority. See docs/SEND_SYNC_DESIGN.md
# (RwLock Sync bound) and BUGS.md.
@unsafe_sync(if_params_send=True)
class RwLock[T]:
    _cell: unsafe_interior_mutable[Ptr[_RwLockCell[T]]]
    _payload: unsafe_interior_mutable[Ptr[T]]

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

    def get(self) -> T:
        _require_locked(self._locked)
        return self._payload

    def set(self, value: T) -> None:
        _require_locked(self._locked)
        unsafe_store(self._payload, UInt32(0), value)
