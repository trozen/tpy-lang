# Rc[T] / Weak[T] -- non-atomic single-threaded shared-ownership smart
# pointer with a non-owning companion.
#
# Refcount invariant: live strong handles collectively own one weak
# reference, so initial state is strong=1, weak=1 and the last strong
# drop decrements weak once after destructing the payload. This decouples
# payload lifetime from cell lifetime so a Weak outliving all Rcs can
# still check `strong > 0` against still-valid memory.
#
# Layout: one heap block per Rc.new -- `_RcCell[U]` holds the refcount
# AND the payload inline (`UninitArrayStorage[U, 1]`). The cell's
# bookkeeping virtuals (incr/decr/get strong/weak, drop_payload) are
# dispatched through `_RcCellBase`, a @dynamic protocol -- so the
# Rc/Weak handles can hold a `Ptr[_RcCellBase]` and stay agnostic to U.
# The strong-zero path calls `cell.drop_payload()` BEFORE the final
# weak decrement; UninitArrayStorage's debug dtor asserts alive_==0.
#
# `Weak` is intentionally NOT re-exported from `tplib`: import as
# `from tplib.rc import Weak`. Reserves the bare name for a future
# `tplib.arc.Weak` (atomic refcount), mirroring `std::rc::Weak` vs
# `std::sync::Weak` in Rust.
#
# Open API gaps (each tracked in BUGS.md):
# - No `Rc(other)` sharing constructor -- @nocopy + sibling-borrow into
#   __init__ isn't supported; `clone()` covers the share path internally.
from __future__ import annotations
from typing import Protocol
from tpy import Own, Ptr, UInt32, UInt64, Deref, Covariant, Equatable, Comparable, Hashable, dynamic, nocopy, auto_readonly, interior
from tpy.mem import UninitArrayStorage
from tpy.unsafe import unsafe_take, unsafe_release


# TODO: Once @dynamic protocols support fields (see
# docs/DYNAMIC_PROTOCOL_DESIGN.md:559-561 -- "protocol-field gap"),
# move `strong` and `weak` to _RcCellBase as direct fields and keep
# only `release_payload` as a virtual method. That removes the vcall
# from every counter op, leaving just one indirect call per cell
# (the drop) -- matching std::shared_ptr's control-block shape. The
# fused-release API below ALREADY collapses to 1 vcall per Rc op
# (see method docs), so the @dynamic-fields work would only buy
# cache-line + icache savings on the existing path, not a per-op
# count reduction.
@dynamic
class _RcCellBase(Protocol):
    # Fused release ops keep each Rc/Weak operation to at most one
    # vcall, regardless of how many internal counter writes happen.
    # release_strong does the strong=0 -> drop_payload -> decr_weak
    # chain in one go (preserving the "strong holds collective weak"
    # invariant); try_incr_strong is the upgrade primitive.
    def incr_strong(self) -> None: ...
    def release_strong(self) -> bool: ...
    def try_incr_strong(self) -> bool: ...
    def incr_weak(self) -> None: ...
    def release_weak(self) -> bool: ...


@nocopy
class _RcCell[U](_RcCellBase):
    strong: UInt32
    weak: UInt32
    storage: UninitArrayStorage[U, 1]

    def __init__(self) -> None:
        self.strong = 1
        self.weak = 1
        # Explicit init mirrors the C++ default-construction so the field
        # is set before any use. Without it, TPy emits a warning about
        # the unset field (CPython doesn't reach this stub -- it uses
        # lib/cpy/tplib/rc.py which has a different internal model).
        self.storage = UninitArrayStorage[U, 1]()

    def incr_strong(self) -> None:
        self.strong = self.strong + 1

    def release_strong(self) -> bool:
        # Decrement strong; if it hit zero, destruct the inline payload
        # FIRST (UninitArrayStorage's debug dtor asserts alive==0) and
        # THEN decrement the collective weak. Returns True iff the cell
        # itself is now unreferenced and needs free. A nested Weak.__del__
        # triggered by the payload destructor (self-Weak inside U) sees
        # weak >= 2 (collective + its own) and can't free us mid-method.
        new_strong = self.strong - 1
        self.strong = new_strong
        if new_strong == 0:
            self.storage.drop0()
            new_weak = self.weak - 1
            self.weak = new_weak
            return new_weak == 0
        return False

    def try_incr_strong(self) -> bool:
        if self.strong == 0:
            return False
        self.strong = self.strong + 1
        return True

    def incr_weak(self) -> None:
        self.weak = self.weak + 1

    def release_weak(self) -> bool:
        new_weak = self.weak - 1
        self.weak = new_weak
        return new_weak == 0


@nocopy
class Rc[T](Deref[T], Covariant[T]):
    # `_cell` is bookkeeping outside the readonly boundary (the refcount lives
    # behind it): clone/downgrade bump it through a readonly handle, the
    # std::shared_ptr const-copy pattern. `_payload` stays inside the boundary
    # so a readonly handle still yields readonly T.
    _cell: interior[Ptr[_RcCellBase]]
    _payload: Ptr[T]

    # TODO: package-private once TPy gains a private-method mechanism;
    # only `Rc.new`, `clone`, `downgrade`, and `Weak.upgrade` should call.
    def __init__(self, cell: Ptr[_RcCellBase], payload: Ptr[T]) -> None:
        self._cell = cell
        self._payload = payload

    def __del__(self) -> None:
        if self._cell.release_strong():
            unsafe_release(self._cell)

    def __deref__(self) -> T:
        return self.get()

    # DO NOT add an owning-store API (e.g. set(value: Own[T])) here -- Rc
    # is Covariant[T], so a writer through a Pet view could install a
    # non-Parrot into the slot a Rc[Parrot] clone is observing.
    @auto_readonly
    def get(self) -> auto_readonly[T]:
        return self._payload

    @staticmethod
    def new[U: T](value: Own[U]) -> Own[Rc[T]]:
        cell = unsafe_take(_RcCell[U]())
        cell.storage.init0(value)
        return Rc[T](cell, cell.storage.ptr())

    # clone/downgrade are auto_readonly: a mutable handle yields a mutable
    # handle; a readonly handle (`readonly[Rc[T]]`) yields a readonly-payload
    # handle, so readonly can't be laundered into mutable access. The
    # `auto_readonly[T]` construction marker resolves per overload -- Rc[T] in
    # the mutable half, Rc[readonly[T]] in the const half.
    # TODO: once inference can bind a type param to readonly[T] (BUGS.md), the
    # explicit `Rc[auto_readonly[T]](...)` here (and in downgrade/upgrade/
    # Weak.clone) should reduce to plain `Rc(...)` and infer per overload.
    @auto_readonly
    def clone(self) -> Own[Rc[auto_readonly[T]]]:
        self._cell.incr_strong()
        return Rc[auto_readonly[T]](self._cell, self._payload)

    @auto_readonly
    def downgrade(self) -> Own[Weak[auto_readonly[T]]]:
        self._cell.incr_weak()
        return Weak[auto_readonly[T]](self._cell, self._payload)

    # Equality and ordering delegate to T (content, matching Rust's
    # `Rc<T>::eq`). Cell-pointer identity is not yet expressible at the
    # TPy surface; tracked in BUGS.md.
    def __str__(self) -> str:
        return f"Rc({self.get()})"

    def __repr__(self) -> str:
        return f"Rc({self.get()!r})"

    def __eq__[T: Equatable](self, other: Rc[T]) -> bool:
        return self.get() == other.get()

    def __lt__[T: Comparable](self, other: Rc[T]) -> bool:
        return self.get() < other.get()

    def __le__[T: Comparable](self, other: Rc[T]) -> bool:
        return not other.get() < self.get()

    def __gt__[T: Comparable](self, other: Rc[T]) -> bool:
        return other.get() < self.get()

    def __ge__[T: Comparable](self, other: Rc[T]) -> bool:
        return not self.get() < other.get()

    def __hash__[T: Hashable](self) -> UInt64:
        return hash(self.get())


@nocopy
class Weak[T]:
    _cell: interior[Ptr[_RcCellBase]]
    # _payload dangles between strong=0 and weak=0, but is only dereferenced
    # via upgrade() after the strong-count check confirms the payload is live.
    _payload: Ptr[T]

    # TODO: package-private (same as Rc.__init__).
    def __init__(self, cell: Ptr[_RcCellBase], payload: Ptr[T]) -> None:
        self._cell = cell
        self._payload = payload

    def __del__(self) -> None:
        if self._cell.release_weak():
            unsafe_release(self._cell)

    @auto_readonly
    def upgrade(self) -> Own[Rc[auto_readonly[T]]] | None:
        # Single vcall instead of get_strong + incr_strong: cell checks
        # strong > 0 and increments atomically (in the local sense; Rc is
        # non-atomic, so "atomically" here just means within one method).
        if not self._cell.try_incr_strong():
            return None
        return Rc[auto_readonly[T]](self._cell, self._payload)

    @auto_readonly
    def clone(self) -> Own[Weak[auto_readonly[T]]]:
        self._cell.incr_weak()
        return Weak[auto_readonly[T]](self._cell, self._payload)

