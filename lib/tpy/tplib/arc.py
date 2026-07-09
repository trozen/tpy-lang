# Arc[T] / Weak[T] -- atomic-refcount shared-ownership smart pointer with a
# non-owning companion. The thread-safe sibling of Rc (tplib/rc.py): identical
# shape, but the strong/weak counters are `Atomic[UInt32]` so handles can cross
# threads.
#
# KEEP IN SYNC WITH tplib/rc.py: the Arc/Weak handle classes and their dunders
# (clone/downgrade/upgrade, __eq__/__lt__/.../__hash__/__str__/__repr__) are a
# near-verbatim copy of rc.py's -- code-sharing is blocked because the cell's
# method surface differs (atomic ops vs plain arithmetic). Only `_ArcCell` and
# the ordering recipe genuinely diverge; a fix to the shared handle/dunder logic
# in one file belongs in the other.
#
# `Arc[T]` requires and provides `Send + Sync` exactly when `T` is both
# (matches Rust's `Arc<T>`), via the conditional @unsafe_send/@unsafe_sync if_params_*
# override -- the handle's raw-pointer fields are otherwise structurally non-Send.
#
# Memory ordering follows std::sync::Arc: a clone increments `relaxed` (a strong
# ref is already held, so nothing can free the cell under us); a drop decrements
# `release` and, on the final decrement, an `acquire` fence synchronizes with
# every other thread's release before the payload destructor runs; `upgrade`
# CAS-increments with `acquire`.
#
# `Weak` is intentionally NOT re-exported from `tplib`: import as
# `from tplib.arc import Weak`. It coexists with `tplib.rc.Weak`, mirroring
# std::sync::Weak vs std::rc::Weak in Rust.
from __future__ import annotations
from typing import Protocol
from tpy import Own, Ptr, UInt32, UInt64, Deref, Covariant, Equatable, Comparable, Hashable, dynamic, nocopy, auto_readonly, unsafe_interior_mutable, unsafe_send, unsafe_sync
from tpy.mem import UninitStorage
from tpy.atomic import Atomic, MemoryOrder, fence
from tpy.unsafe import unsafe_take, unsafe_release


@dynamic
class _ArcCellBase(Protocol):
    # Fused release ops keep each Arc/Weak operation to at most one vcall,
    # regardless of how many internal atomic writes happen (mirrors _RcCellBase).
    def incr_strong(self) -> None: ...
    def release_strong(self) -> bool: ...
    def try_incr_strong(self) -> bool: ...
    def incr_weak(self) -> None: ...
    def release_weak(self) -> bool: ...


@nocopy
class _ArcCell[U](_ArcCellBase):
    strong: Atomic[UInt32]
    weak: Atomic[UInt32]
    # A single owning slot: tracks its own liveness and moves correctly, so a
    # cell over a payload with SSO-`str`/non-relocatable fields survives the
    # one move into heap storage at `new_` (the payload is constructed in
    # place after that move, while the slot is still empty).
    storage: UninitStorage[U]

    def __init__(self) -> None:
        self.strong = Atomic[UInt32](1)
        self.weak = Atomic[UInt32](1)
        self.storage = UninitStorage[U]()

    def incr_strong(self) -> None:
        # Relaxed: the caller already holds a strong ref, so the cell can't be
        # freed under us and no cross-thread ordering is needed for the bump.
        self.strong.fetch_add(1, MemoryOrder.RELAXED)

    def release_strong(self) -> bool:
        # Release publishes all prior uses of the payload; on the last strong
        # drop an acquire fence synchronizes with every other thread's release
        # before we destruct. Then decrement the collective weak (strong handles
        # jointly own one weak ref). Returns True iff the cell itself is now
        # unreferenced and needs free. A nested Weak.__del__ from the payload
        # destructor sees weak >= 2 (collective + its own) and can't free us.
        prev_strong = self.strong.fetch_sub(1, MemoryOrder.RELEASE)
        if prev_strong == 1:
            fence(MemoryOrder.ACQUIRE)
            self.storage.reset()
            prev_weak = self.weak.fetch_sub(1, MemoryOrder.RELEASE)
            if prev_weak == 1:
                fence(MemoryOrder.ACQUIRE)
                return True
        return False

    def try_incr_strong(self) -> bool:
        # Weak.upgrade: increment only while strong > 0, via a CAS loop that
        # acquires on success so the recovered handle sees a live payload.
        cur = self.strong.load(MemoryOrder.RELAXED)
        while cur != 0:
            ok, observed = self.strong.compare_exchange_weak(
                cur, cur + 1, MemoryOrder.ACQUIRE, MemoryOrder.RELAXED)
            if ok:
                return True
            cur = observed
        return False

    def incr_weak(self) -> None:
        self.weak.fetch_add(1, MemoryOrder.RELAXED)

    def release_weak(self) -> bool:
        prev_weak = self.weak.fetch_sub(1, MemoryOrder.RELEASE)
        if prev_weak == 1:
            fence(MemoryOrder.ACQUIRE)
            return True
        return False


@nocopy
@unsafe_send(if_params_send=True, if_params_sync=True)
@unsafe_sync(if_params_send=True, if_params_sync=True)
class Arc[T](Deref[T], Covariant[T]):
    # `_cell` is bookkeeping outside the readonly boundary (the refcount lives
    # behind it): clone/downgrade bump it through a readonly handle, the
    # std::shared_ptr const-copy pattern. `_payload` stays inside the boundary
    # so a readonly handle still yields readonly T.
    _cell: unsafe_interior_mutable[Ptr[_ArcCellBase]]
    _payload: Ptr[T]

    def __init__(self, cell: Ptr[_ArcCellBase], payload: Ptr[T]) -> None:
        self._cell = cell
        self._payload = payload

    def __del__(self) -> None:
        if self._cell.release_strong():
            unsafe_release(self._cell)

    def __deref__(self) -> T:
        return self.get()

    # DO NOT add an owning-store API here -- Arc is Covariant[T], so a writer
    # through a supertype view could install a non-conforming value into the
    # slot a subtype clone is observing.
    @auto_readonly
    def get(self) -> auto_readonly[T]:
        return self._payload

    @staticmethod
    def new[U: T](value: Own[U]) -> Own[Arc[T]]:
        cell = unsafe_take(_ArcCell[U]())
        cell.storage.construct(value)
        return Arc[T](cell, cell.storage.ptr())

    @auto_readonly
    def clone(self) -> Own[Arc[auto_readonly[T]]]:
        self._cell.incr_strong()
        return Arc[auto_readonly[T]](self._cell, self._payload)

    @auto_readonly
    def downgrade(self) -> Own[Weak[auto_readonly[T]]]:
        self._cell.incr_weak()
        return Weak[auto_readonly[T]](self._cell, self._payload)

    # Equality and ordering delegate to T (content, matching Rust's
    # `Arc<T>::eq`). Cell-pointer identity is not yet expressible at the
    # TPy surface; tracked in BUGS.md (shared with Rc).
    def __str__(self) -> str:
        return f"Arc({self.get()})"

    def __repr__(self) -> str:
        return f"Arc({self.get()!r})"

    def __eq__[T: Equatable](self, other: Arc[T]) -> bool:
        return self.get() == other.get()

    def __lt__[T: Comparable](self, other: Arc[T]) -> bool:
        return self.get() < other.get()

    def __le__[T: Comparable](self, other: Arc[T]) -> bool:
        return not other.get() < self.get()

    def __gt__[T: Comparable](self, other: Arc[T]) -> bool:
        return other.get() < self.get()

    def __ge__[T: Comparable](self, other: Arc[T]) -> bool:
        return not self.get() < other.get()

    def __hash__[T: Hashable](self) -> UInt64:
        return hash(self.get())


@nocopy
@unsafe_send(if_params_send=True, if_params_sync=True)
@unsafe_sync(if_params_send=True, if_params_sync=True)
class Weak[T]:
    _cell: unsafe_interior_mutable[Ptr[_ArcCellBase]]
    # _payload dangles between strong=0 and weak=0, but is only dereferenced
    # via upgrade() after the strong-count check confirms the payload is live.
    _payload: Ptr[T]

    def __init__(self, cell: Ptr[_ArcCellBase], payload: Ptr[T]) -> None:
        self._cell = cell
        self._payload = payload

    def __del__(self) -> None:
        if self._cell.release_weak():
            unsafe_release(self._cell)

    @auto_readonly
    def upgrade(self) -> Own[Arc[auto_readonly[T]]] | None:
        if not self._cell.try_incr_strong():
            return None
        return Arc[auto_readonly[T]](self._cell, self._payload)

    @auto_readonly
    def clone(self) -> Own[Weak[auto_readonly[T]]]:
        self._cell.incr_weak()
        return Weak[auto_readonly[T]](self._cell, self._payload)
