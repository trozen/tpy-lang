# Rc[T] -- non-atomic, single-threaded shared-ownership smart pointer.
#
# Heap-allocates an RcCell (refcount + value) on construction; clone() bumps
# the refcount and returns a new Rc sharing the same cell; __del__
# decrements and frees on zero. @nocopy at the TPy level: deliberate sharing
# always goes through explicit clone(). Free mutation through any clone --
# aliased clones share the underlying T (matches shared_ptr / Python
# semantics). For shared-immutable use Rc[readonly[T]]. No atomic refcount
# (single-threaded only); atomic Arc[T] is a v3+ item.
#
# Missing companion:
# - TODO: `Weak[T]` to break reference cycles. Today an Rc[Node] cycle leaks
#   (every cell in the cycle holds a strong reference to the next, so refcount
#   never reaches zero; tests/cases/tplib/rc_cycle_leak demonstrates). Weak
#   would store a non-owning handle to the RcCell that doesn't keep the value
#   alive but can be upgraded to Option[Rc[T]] when the cell is still live.
#   Requires extending RcCell with a weak_count alongside refcount, deferring
#   cell free until both reach zero.
#
# Rc-specific API notes (see BUGS.md for the underlying compiler gaps):
# - TODO: construction goes through `make_rc(value)`, not `Rc.new(value)` --
#   cross-module imported-class staticmethod access is broken in sema.
# - TODO: no `Rc(other)` sharing constructor -- clone() handles the share
#   internally because @nocopy + sibling-borrow into __init__ isn't supported.
# - TODO: `Rc[readonly[T]]` cannot be cloned -- clone() mutates refcount and
#   can't be @readonly, so a readonly Rc handle has no way to share.
# - TODO: `Rc` is not `Covariant[T]` -- shared-mutable Rc isn't safely
#   covariant, and the codegen would break on the RcCell wrapping layer.
from __future__ import annotations
from tpy import Own, Ptr, UInt32, UInt64, Deref, Equatable, Comparable, Hashable, nocopy, auto_readonly
from tpy.unsafe import unsafe_alloc, unsafe_free, unsafe_init, unsafe_drop


class RcCell[T]:
    refcount: UInt32
    value: T

    def __init__(self, value: Own[T]) -> None:
        self.refcount = 1
        self.value = value


@nocopy
class Rc[T](Deref[T]):
    _cell: Ptr[RcCell[T]]

    # TODO: package-private once TPy gains a private-method mechanism. Only
    # `make_rc` and `clone` should call this; user code reaching for `Rc(cell)`
    # directly is bypassing the refcount discipline.
    def __init__(self, cell: Ptr[RcCell[T]]) -> None:
        self._cell = cell

    def __del__(self) -> None:
        # Hoist the decremented refcount into a local to avoid re-reading
        # cell->refcount for the zero-check.
        new_count = self._cell.refcount - 1
        self._cell.refcount = new_count
        if new_count == 0:
            unsafe_drop(self._cell)
            unsafe_free(self._cell)

    # __deref__ implicitly gets dual mutable/const overloads via
    # IMPLICIT_AUTO_READONLY_METHODS in typesys; no explicit @auto_readonly
    # needed. get() is the regular-method form of the same access (Box-parity)
    # and keeps an explicit @auto_readonly because regular method names aren't
    # in the implicit set.
    def __deref__(self) -> T:
        return self.get()

    @auto_readonly
    def get(self) -> auto_readonly[T]:
        return self._cell.value

    def clone(self) -> Own[Rc[T]]:
        self._cell.refcount += 1
        return Rc[T](self._cell)

    # Content equality and ordering: delegate to T. Matches Box and Rust's
    # `Rc<T>::eq` (content, not identity). Identity comparison stays
    # available via `is`.
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


# TODO: rename to `Rc.new(value)` once cross-module imported-class staticmethod
# resolution is fixed in sema (see BUGS.md). User-facing API will be a single
# `from tplib import Rc` then `Rc.new(value)` -- no separate factory import.
def make_rc[T](value: Own[T]) -> Own[Rc[T]]:
    cell = unsafe_alloc[RcCell[T]]()
    unsafe_init(cell, RcCell(value))
    return Rc[T](cell)
