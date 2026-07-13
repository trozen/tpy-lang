# set() takes Own[T], so a still-live reference lvalue copies into the lock's
# storage (where CPython aliases) -- an acknowledged divergence the compiler
# warns about; `.copy()` silences it. Covers both MutexGuard and WriteGuard.
# no_cpython: the un-acknowledged copy IS the divergence -- the read-back below
# prints the lock's independent copy (2), where CPython would alias and print 3.
from tpy.sync import Mutex, RwLock


def main() -> None:
    m = Mutex.new([1, 2])

    live = [7, 8]
    with m.lock() as g:
        g.set(live)                 # tpyc: warning(/copies .* into owned storage/)
    live.append(9)                  # keeps `live` alive past set() -> forces the copy
    with m.lock() as g2:
        print(len(g2.get()))        # 2 -- the lock holds its own copy, not `live`

    acked = [5, 6]
    with m.lock() as g3:
        g3.set(acked.copy())        # tpyc: ok
    acked.append(7)
    with m.lock() as g4:
        print(len(g4.get()))        # 2 -- acknowledged copy is independent too

    rw = RwLock.new([1, 2])
    shared = [7, 8]
    with rw.write() as w:
        w.set(shared)               # tpyc: warning(/copies .* into owned storage/)
    shared.append(9)
    with rw.read() as r:
        print(len(r.get()))         # 2 -- WriteGuard copy is independent


main()
