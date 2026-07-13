# MutexGuard.set() / WriteGuard.set() take Own[T]: a fresh reference-type value
# is moved into the lock's storage, then mutated through the guard and observed
# through a later lock -- proving set() writes into the lock's live storage (an
# alias), not a throwaway copy. Byte-parity with CPython (which rebinds).
from tpy.sync import Mutex, RwLock


def main() -> None:
    m = Mutex.new([1, 2])
    with m.lock() as g:
        g.set([7, 8, 9])            # rvalue moved into the lock's storage
        g.append(10)                # mutate the moved-in payload through the guard
    with m.lock() as g2:
        print(g2.get())             # [7, 8, 9, 10] -- the append hit the stored list

    rw = RwLock.new([1, 2])
    with rw.write() as w:
        w.set([20, 30])
        w.append(40)
    with rw.read() as r:
        print(r.get())              # [20, 30, 40]


main()
