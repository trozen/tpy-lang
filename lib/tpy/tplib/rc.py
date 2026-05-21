# Rc[T] / Weak[T] -- non-atomic single-threaded shared-ownership smart
# pointer with a non-owning companion.
#
# Refcount invariant: live strong handles collectively own one weak
# reference, so initial state is strong=1, weak=1 and the last strong
# drop decrements weak once after destructing the payload. This decouples
# payload lifetime from cell lifetime so a Weak outliving all Rcs can
# still check `strong > 0` against still-valid memory.
#
# `Weak` is intentionally NOT re-exported from `tplib`: import as
# `from tplib.rc import Weak`. Reserves the bare name for a future
# `tplib.arc.Weak` (atomic refcount), mirroring `std::rc::Weak` vs
# `std::sync::Weak` in Rust.
#
# Open API gaps (each tracked in BUGS.md):
# - No `Rc(other)` sharing constructor -- @nocopy + sibling-borrow into
#   __init__ isn't supported; `clone()` covers the share path internally.
# - `Rc[readonly[T]]` cannot share -- clone()/downgrade()/upgrade() write
#   the refcount and can't be @readonly.
from __future__ import annotations
from tpy import Own, Ptr, UInt32, UInt64, Deref, Covariant, Equatable, Comparable, Hashable, nocopy, auto_readonly
from tpy.unsafe import unsafe_take, unsafe_release


class _RcCell:
    strong: UInt32
    weak: UInt32

    def __init__(self) -> None:
        self.strong = 1
        self.weak = 1


@nocopy
class Rc[T](Deref[T], Covariant[T]):
    _cell: Ptr[_RcCell]
    _payload: Ptr[T]

    # TODO: package-private once TPy gains a private-method mechanism;
    # only `Rc.new`, `clone`, `downgrade`, and `Weak.upgrade` should call.
    def __init__(self, cell: Ptr[_RcCell], payload: Ptr[T]) -> None:
        self._cell = cell
        self._payload = payload

    def __del__(self) -> None:
        new_strong = self._cell.strong - 1
        self._cell.strong = new_strong
        if new_strong == 0:
            # heap_release(_payload) must happen BEFORE we decrement the
            # collective weak. Any nested Weak.__del__ triggered by the
            # payload destructor (e.g. a self-Weak field inside T) sees
            # weak >= 1 and can't free the cell out from under us.
            unsafe_release(self._payload)
            new_weak = self._cell.weak - 1
            self._cell.weak = new_weak
            if new_weak == 0:
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
    def new(value: Own[T]) -> Own[Rc[T]]:
        return Rc[T](unsafe_take(_RcCell()), unsafe_take(value))

    def clone(self) -> Own[Rc[T]]:
        new_strong = self._cell.strong + 1
        self._cell.strong = new_strong
        return Rc[T](self._cell, self._payload)

    def downgrade(self) -> Own[Weak[T]]:
        new_weak = self._cell.weak + 1
        self._cell.weak = new_weak
        return Weak[T](self._cell, self._payload)

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
    _cell: Ptr[_RcCell]
    # _payload dangles between strong=0 and weak=0, but is only dereferenced
    # via upgrade() after the strong-count check confirms the payload is live.
    _payload: Ptr[T]

    # TODO: package-private (same as Rc.__init__).
    def __init__(self, cell: Ptr[_RcCell], payload: Ptr[T]) -> None:
        self._cell = cell
        self._payload = payload

    def __del__(self) -> None:
        new_weak = self._cell.weak - 1
        self._cell.weak = new_weak
        if new_weak == 0:
            unsafe_release(self._cell)

    def upgrade(self) -> Own[Rc[T]] | None:
        strong = self._cell.strong
        if strong == 0:
            return None
        self._cell.strong = strong + 1
        return Rc[T](self._cell, self._payload)

    def clone(self) -> Own[Weak[T]]:
        new_weak = self._cell.weak + 1
        self._cell.weak = new_weak
        return Weak[T](self._cell, self._payload)
