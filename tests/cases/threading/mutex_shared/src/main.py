# Arc[Mutex[list]] shared across real threads: each worker locks the shared list
# and appends its id N times. After join the list holds every append -- the
# mutation crossed the thread boundary through the lock, not a per-thread copy.
# Mutex[list[int32]] is Send + Sync because list is Send, so the @nocopy task
# carries its Arc clone across the spawn boundary.
# (`.get().lock()` is spelled explicitly rather than `data.lock()` to stay
# CPython-compatible -- Arc method auto-deref is TPy-only, exercised separately
# in mutex_arc_autoderef, which is no_cpython.)
from tpy import int32, Own, nocopy
from tplib.arc import Arc
from tpy.thread import spawn
from tpy.sync import Mutex


@nocopy
class Appender:
    shared: Arc[Mutex[list[int32]]]
    id: int32
    iters: int32

    def __init__(self, shared: Own[Arc[Mutex[list[int32]]]], id: int32, iters: int32) -> None:
        self.shared = shared
        self.id = id
        self.iters = iters

    def run(self) -> None:
        i = 0
        while i < self.iters:
            with self.shared.get().lock() as g:
                g.append(self.id)
            i += 1


def main() -> None:
    data = Arc.new(Mutex.new([0]))   # tpyc: is_send(yes) is_sync(yes)
    h1 = spawn(Appender(data.clone(), 1, 100))
    h2 = spawn(Appender(data.clone(), 2, 100))
    h3 = spawn(Appender(data.clone(), 3, 100))
    h1.join()
    h2.join()
    h3.join()
    with data.get().lock() as g:
        total = 0
        n = 0
        for v in g.get():
            total += v
            n += 1
        print(n, total)              # 301 elements; 0 + 100*(1+2+3) = 600


main()
