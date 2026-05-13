# Rc[T] / Weak[T] -- non-atomic, single-threaded shared-ownership smart
# pointer with a non-owning companion.
#
# _RcCell layout: `{strong, weak, storage}` where storage is inline
# UninitArrayStorage[T, 1]. Payload destruction (storage.drop0()) happens when
# `strong` reaches zero; cell deallocation (unsafe_free) happens when `weak`
# reaches zero. The classic std::shared_ptr trick: all live strong handles
# collectively own one weak reference, so initial state is strong=1, weak=1,
# and dropping the last strong decrements weak once after destructing the
# payload. This decouples "the value is gone" from "the cell memory is gone"
# -- the latter must outlive the former whenever Weak handles exist, so
# Weak.upgrade() can safely check `strong > 0` against still-valid memory.
#
# @nocopy at the TPy level: deliberate sharing is always explicit via
# Rc.clone(), Rc.downgrade(), Weak.clone(), Weak.upgrade(). Free mutation
# through any Rc clone -- aliased clones share the underlying T (matches
# shared_ptr / Python semantics). For shared-immutable use Rc[readonly[T]].
# Weak does not implement Deref -- access must go through upgrade(), which
# returns Optional so callers handle the "payload already dropped" case.
#
# No atomic refcount (single-threaded only); atomic Arc[T] is a v3+ item.
# `Weak` is intentionally NOT re-exported from `tplib` -- import as
# `from tplib.rc import Weak`. Reserves the bare `Weak` name for an
# eventual `tplib.arc.Weak` (different type; atomic refcount). Mirrors
# `std::rc::Weak` vs `std::sync::Weak` in Rust.
#
# Rc-specific API notes (see BUGS.md for the underlying compiler gaps):
# - TODO: no `Rc(other)` sharing constructor -- clone() handles the share
#   internally because @nocopy + sibling-borrow into __init__ isn't supported.
# - TODO: `Rc[readonly[T]]` cannot be cloned -- clone() mutates refcount and
#   can't be @readonly, so a readonly Rc handle has no way to share. Same
#   issue applies to `Weak[readonly[T]].upgrade()`.
# - TODO: `Rc` is not `Covariant[T]` -- shared-mutable Rc isn't safely
#   covariant, and the codegen would break on the _RcCell wrapping layer.
from __future__ import annotations
from tpy import Own, Ptr, UInt32, UInt64, Deref, Equatable, Comparable, Hashable, nocopy, auto_readonly
from tpy.mem import UninitArrayStorage
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop


class _RcCell[T]:
    strong: UInt32
    weak: UInt32
    storage: UninitArrayStorage[T, 1]

    def __init__(self) -> None:
        # strong=1 for the constructing Rc; weak=1 for the collective weak
        # owned by all strong handles. Storage stays empty here; payload
        # init0 is deferred to `Rc.new` (see the rationale at the call site).
        self.strong = 1
        self.weak = 1
        self.storage = UninitArrayStorage[T, 1]()


@nocopy
class Rc[T](Deref[T]):
    _cell: Ptr[_RcCell[T]]

    # TODO: package-private once TPy gains a private-method mechanism. Only
    # `Rc.new`, `clone`, and `Weak.upgrade` should call this; user code
    # reaching for `Rc(cell)` directly is bypassing the refcount discipline.
    def __init__(self, cell: Ptr[_RcCell[T]]) -> None:
        self._cell = cell

    def __del__(self) -> None:
        new_strong = self._cell.strong - 1
        self._cell.strong = new_strong
        if new_strong == 0:
            # Last strong handle: destruct the payload. Then drop the
            # collective weak reference -- if no Weak handles exist either,
            # free the cell.
            #
            # Invariant: during drop0() the collective weak (this Rc's
            # contribution) is still in `weak`, so any nested Weak.__del__
            # triggered by the payload's destructor (e.g. a self-Weak field
            # inside T) decrements `weak` to >= 1 but never to 0. The cell
            # cannot be freed until we return here and decrement the
            # collective ourselves. This rules out a UAF where the payload
            # destructor's transitive Weak drops would free the cell out
            # from under us.
            self._cell.storage.drop0()
            new_weak = self._cell.weak - 1
            self._cell.weak = new_weak
            if new_weak == 0:
                unsafe_drop(self._cell)
                unsafe_free(self._cell)

    def __deref__(self) -> T:
        return self.get()

    @auto_readonly
    def get(self) -> auto_readonly[T]:
        return self._cell.storage.load0()

    @staticmethod
    def new(value: Own[T]) -> Own[Rc[T]]:
        cell = unsafe_alloc[_RcCell[T]]()
        unsafe_init(cell, _RcCell[T]())
        # Init the payload after unsafe_init so the move from the temporary
        # _RcCell() doesn't memcpy a live T through UninitArrayStorage's
        # memcpy-based move ctor (would corrupt non-trivially-copyable T like
        # std::string with its small-string self-pointer).
        cell.storage.init0(value)
        return Rc[T](cell)

    def clone(self) -> Own[Rc[T]]:
        # Hoist read/write into one each so codegen emits a single
        # `deref_check(self._cell)` per refcount op (matches __del__ idiom;
        # the `+= 1` shorthand expands to read+write with two deref_checks).
        new_strong = self._cell.strong + 1
        self._cell.strong = new_strong
        return Rc[T](self._cell)

    def downgrade(self) -> Own[Weak[T]]:
        new_weak = self._cell.weak + 1
        self._cell.weak = new_weak
        return Weak[T](self._cell)

    # Content equality and ordering: delegate to T. Matches Box and Rust's
    # `Rc<T>::eq` (content, not identity). TPy's `is` is currently restricted
    # to None/enum/bool comparisons, so cell-pointer identity ("do these two
    # handles share the same allocation?") is not yet expressible at the TPy
    # surface; see BUGS.md for the gap.
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
    _cell: Ptr[_RcCell[T]]

    # TODO: package-private (same caveat as Rc.__init__). Constructed only
    # by Rc.downgrade() and Weak.clone().
    def __init__(self, cell: Ptr[_RcCell[T]]) -> None:
        self._cell = cell

    def __del__(self) -> None:
        new_weak = self._cell.weak - 1
        self._cell.weak = new_weak
        if new_weak == 0:
            # Payload was destructed when strong reached zero; cell is now
            # safe to free.
            unsafe_drop(self._cell)
            unsafe_free(self._cell)

    def upgrade(self) -> Own[Rc[T]] | None:
        # Single read into a local: the check and the increment share it
        # (single-threaded, so no compare-exchange needed; for Arc[T] this
        # shape will need to become a CAS loop).
        strong = self._cell.strong
        if strong == 0:
            return None
        self._cell.strong = strong + 1
        return Rc[T](self._cell)

    def clone(self) -> Own[Weak[T]]:
        new_weak = self._cell.weak + 1
        self._cell.weak = new_weak
        return Weak[T](self._cell)
