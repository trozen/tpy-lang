# Single-threaded correctness of the Atomic[T] primitive: the full
# std::atomic<integral> op surface (load/store/exchange, fetch_add/sub/and/or/xor,
# compare_exchange[/_weak]) plus the Send/Sync classification the Arc cell relies
# on. Contention across real threads is exercised by the Arc test, not here.
from tpy.atomic import Atomic, MemoryOrder, fence
from tpy import UInt32


def main() -> None:
    a = Atomic[UInt32](0)  # tpyc: is_send(yes) is_sync(yes)
    print(a.load(MemoryOrder.RELAXED))            # 0

    a.store(10, MemoryOrder.RELAXED)
    print(a.load(MemoryOrder.RELAXED))            # 10

    print(a.exchange(20, MemoryOrder.ACQ_REL))    # 10 (old)
    print(a.load(MemoryOrder.RELAXED))            # 20

    print(a.fetch_add(5, MemoryOrder.RELAXED))    # 20 -> 25
    print(a.fetch_sub(3, MemoryOrder.RELAXED))    # 25 -> 22
    print(a.fetch_or(1, MemoryOrder.RELAXED))     # 22 -> 23
    print(a.fetch_and(0xF, MemoryOrder.RELAXED))  # 23 -> 7
    print(a.fetch_xor(0x2, MemoryOrder.RELAXED))  # 7 -> 5
    print(a.load(MemoryOrder.RELAXED))            # 5

    # Strong CAS is deterministic: matches, so it swaps and reports the old value.
    ok, observed = a.compare_exchange(5, 99, MemoryOrder.ACQ_REL, MemoryOrder.RELAXED)
    print(ok)                                     # True
    print(observed)                               # 5
    print(a.load(MemoryOrder.RELAXED))            # 99

    # Mismatch: no swap, reports the current value.
    ok2, cur = a.compare_exchange(5, 1, MemoryOrder.ACQ_REL, MemoryOrder.RELAXED)
    print(ok2)                                     # False
    print(cur)                                     # 99

    # Weak CAS may fail spuriously, so drive it in the canonical retry loop:
    # increment-if-still-99. On success the value is 100.
    while True:
        cur2 = a.load(MemoryOrder.RELAXED)
        done, _ = a.compare_exchange_weak(cur2, cur2 + 1, MemoryOrder.ACQ_REL, MemoryOrder.RELAXED)
        if done:
            break
    print(a.load(MemoryOrder.RELAXED))            # 100

    fence(MemoryOrder.SEQ_CST)

    # Default ordering (seq_cst) -- no MemoryOrder argument needed.
    b = Atomic[UInt32](0)
    b.store(50)
    print(b.load())                               # 50
    print(b.fetch_add(5))                         # 50 -> 55
    print(b.fetch_sub(2))                         # 55 -> 53
    ok3, obs = b.compare_exchange(53, 60)         # default orders
    print(ok3)                                    # True
    print(b.load())                               # 60
    fence()                                        # default order

    # In-place operators: atomic RMW at seq_cst.
    b += 10                                       # 70
    b -= 5                                        # 65
    b |= 2                                        # 67
    b &= 0xF                                      # 3
    b ^= 0x1                                      # 2
    print(b.load())                               # 2

    # Snapshot repr.
    print(b)                                      # Atomic(2)
    print(repr(b))                                # Atomic(2)

    # Fixed-width wrapping matches std::atomic<T> (and the CPython stub's _coerce).
    w = Atomic[UInt32](0xFFFFFFFF)
    print(w.fetch_add(1, MemoryOrder.RELAXED))    # 4294967295 (old), value wraps to 0
    print(w.load(MemoryOrder.RELAXED))            # 0
    w -= 1                                        # wraps back to 4294967295
    print(w.load(MemoryOrder.RELAXED))            # 4294967295
    print("done")


main()
