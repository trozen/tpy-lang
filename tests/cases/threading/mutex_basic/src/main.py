# Mutex[T]: the lock hands out a mutable borrow of the payload through a guard.
# The list is moved in, then mutated and observed through a later lock (mutation
# crosses the lock, not a silent copy). Also covers value-type Mutex[int] via
# get/set and RwLock read/write guards. (Distinct guard names per block: the
# with as-variable persists after the block, so reusing one name across guards
# of different payload type would collide.)
from tpy import int32
from tpy.sync import Mutex, RwLock


def main() -> None:
    m = Mutex.new([1, 2, 3])
    with m.lock() as ml:
        ml.append(4)                # mutate through the guard's deref chain
    with m.lock() as ml2:
        print(sorted(ml2.get()))    # [1, 2, 3, 4] -- the append survived

    counter = Mutex.new(int32(5))
    with counter.lock() as c:
        c.set(c.get() + 1)          # value-type payload: explicit get/set
    with counter.lock() as c2:
        print(c2.get())             # 6

    rw = RwLock.new([10, 20])
    with rw.write() as w:
        w.append(30)                # write guard: mutable
    with rw.read() as r:
        print(len(r.get()))         # 3 -- read guard sees the write


main()
