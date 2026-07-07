# tpy: include("<tpy/atomic.hpp>")
"""Lock-free atomic scalars -- `Atomic[T]` over the fixed-width integer types.

`Atomic[T: AnyFixedInt]` is a `@nocopy` (copying a shared counter would silently
fork it) but movable, `Send + Sync` wrapper over `std::atomic<T>`.

Every operation takes a `MemoryOrder`, but it **defaults to `SEQ_CST`** -- the
intuitive "as if sequential" order -- so casual use needs no ordering
boilerplate; pass an explicit weaker order only on hot paths:

    counter.fetch_add(1)                    # seq_cst
    counter.fetch_add(1, MemoryOrder.RELAXED)
    counter += 1                            # atomic RMW, seq_cst
    print(counter.load())
"""
from enum import Enum
from tpy import nocopy, unsafe_send, unsafe_sync, AnyFixedInt, readonly
from tpy.extern import native, native_member


@native("std::memory_order")
class MemoryOrder(Enum):
    RELAXED = native_member("relaxed")
    ACQUIRE = native_member("acquire")
    RELEASE = native_member("release")
    ACQ_REL = native_member("acq_rel")
    SEQ_CST = native_member("seq_cst")


# Raw hardware layer: `tpy::MovableAtomic<T>` == `std::atomic<T>` plus a move
# ctor (std::atomic deletes its own), so `Atomic` stays member-wise movable with
# no drop flag. load/store/exchange/fetch_* are std::atomic's inherited members;
# CAS goes through the tuple-returning helpers (std::atomic's CAS uses a
# reference out-param). All access is atomic, so @unsafe_send/@unsafe_sync is honest.
@native("tpy::MovableAtomic")
@nocopy
@unsafe_send
@unsafe_sync
class _RawAtomic[T: AnyFixedInt]:
    def __init__(self, value: T) -> None: ...
    @readonly
    def load(self, order: MemoryOrder) -> T: ...
    def store(self, value: T, order: MemoryOrder) -> None: ...
    def exchange(self, value: T, order: MemoryOrder) -> T: ...
    def fetch_add(self, value: T, order: MemoryOrder) -> T: ...
    def fetch_sub(self, value: T, order: MemoryOrder) -> T: ...
    def fetch_and(self, value: T, order: MemoryOrder) -> T: ...
    def fetch_or(self, value: T, order: MemoryOrder) -> T: ...
    def fetch_xor(self, value: T, order: MemoryOrder) -> T: ...
    @native("tpy::atomic_cas", function=True)
    def compare_exchange(self, expected: T, desired: T,
                         success: MemoryOrder, failure: MemoryOrder) -> tuple[bool, T]: ...
    @native("tpy::atomic_cas_weak", function=True)
    def compare_exchange_weak(self, expected: T, desired: T,
                              success: MemoryOrder, failure: MemoryOrder) -> tuple[bool, T]: ...


@nocopy
class Atomic[T: AnyFixedInt]:
    _raw: _RawAtomic[T]

    def __init__(self, value: T) -> None:
        self._raw = _RawAtomic[T](value)

    @readonly
    def load(self, order: MemoryOrder = MemoryOrder.SEQ_CST) -> T:
        return self._raw.load(order)

    def store(self, value: T, order: MemoryOrder = MemoryOrder.SEQ_CST) -> None:
        self._raw.store(value, order)

    def exchange(self, value: T, order: MemoryOrder = MemoryOrder.SEQ_CST) -> T:
        return self._raw.exchange(value, order)

    def fetch_add(self, value: T, order: MemoryOrder = MemoryOrder.SEQ_CST) -> T:
        return self._raw.fetch_add(value, order)

    def fetch_sub(self, value: T, order: MemoryOrder = MemoryOrder.SEQ_CST) -> T:
        return self._raw.fetch_sub(value, order)

    def fetch_and(self, value: T, order: MemoryOrder = MemoryOrder.SEQ_CST) -> T:
        return self._raw.fetch_and(value, order)

    def fetch_or(self, value: T, order: MemoryOrder = MemoryOrder.SEQ_CST) -> T:
        return self._raw.fetch_or(value, order)

    def fetch_xor(self, value: T, order: MemoryOrder = MemoryOrder.SEQ_CST) -> T:
        return self._raw.fetch_xor(value, order)

    # Returns (succeeded, observed): on success `observed` is the swapped-from
    # value; on failure the current value, so a CAS loop retries without a
    # separate re-load. The plain form is deterministic; the `weak` form may fail
    # spuriously (use it in a loop) for cheaper codegen on LL/SC architectures.
    def compare_exchange(self, expected: T, desired: T,
                         success: MemoryOrder = MemoryOrder.SEQ_CST,
                         failure: MemoryOrder = MemoryOrder.SEQ_CST) -> tuple[bool, T]:
        return self._raw.compare_exchange(expected, desired, success, failure)

    def compare_exchange_weak(self, expected: T, desired: T,
                              success: MemoryOrder = MemoryOrder.SEQ_CST,
                              failure: MemoryOrder = MemoryOrder.SEQ_CST) -> tuple[bool, T]:
        return self._raw.compare_exchange_weak(expected, desired, success, failure)

    # Ergonomic in-place operators: atomic RMW at seq_cst, so `counter += 1` is a
    # single atomic op. Binary operators (`+`, `-`, ...) and implicit `int()` are
    # deliberately NOT provided -- they would make the non-atomic `a = a + 1` (a
    # load/store race) look valid. Read explicitly via `load()`.
    def __iadd__(self, other: T) -> Atomic[T]:
        self._raw.fetch_add(other, MemoryOrder.SEQ_CST)
        return self

    def __isub__(self, other: T) -> Atomic[T]:
        self._raw.fetch_sub(other, MemoryOrder.SEQ_CST)
        return self

    def __iand__(self, other: T) -> Atomic[T]:
        self._raw.fetch_and(other, MemoryOrder.SEQ_CST)
        return self

    def __ior__(self, other: T) -> Atomic[T]:
        self._raw.fetch_or(other, MemoryOrder.SEQ_CST)
        return self

    def __ixor__(self, other: T) -> Atomic[T]:
        self._raw.fetch_xor(other, MemoryOrder.SEQ_CST)
        return self

    # Snapshot print (relaxed load), mirroring Rust's Debug for atomics.
    def __str__(self) -> str:
        return f"Atomic({self._raw.load(MemoryOrder.RELAXED)})"

    def __repr__(self) -> str:
        return f"Atomic({self._raw.load(MemoryOrder.RELAXED)})"


def fence(order: MemoryOrder = MemoryOrder.SEQ_CST) -> None:
    _atomic_fence(order)


@native("tpy::atomic_fence")
def _atomic_fence(order: MemoryOrder) -> None: ...
