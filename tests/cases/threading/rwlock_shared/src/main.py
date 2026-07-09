# Arc[RwLock[list]] across threads: writer threads append under the write guard
# (exclusive), then the main thread reads the total under the read guard (shared).
# Exercises both guard kinds crossing the thread boundary; the appends are
# observed after join, proving the writes hit the shared list.
#
# `.get()` is spelled explicitly (not `data.read()` / `data.write()`) to keep the
# case CPython-compatible: Arc method auto-deref is TPy-only and unmodeled by the
# CPython Arc stub (the deref form is exercised separately in mutex_arc_autoderef,
# which is no_cpython). `r.get()` is likewise required -- a guard is not directly
# iterable (`for v in r:` is rejected in TPy too), so read the payload first.
from tpy import Int32, Own, nocopy
from tplib.arc import Arc
from tpy.thread import spawn
from tpy.sync import RwLock


@nocopy
class Writer:
    shared: Arc[RwLock[list[Int32]]]
    id: Int32
    iters: Int32

    def __init__(self, shared: Own[Arc[RwLock[list[Int32]]]], id: Int32, iters: Int32) -> None:
        self.shared = shared
        self.id = id
        self.iters = iters

    def run(self) -> None:
        i = 0
        while i < self.iters:
            with self.shared.get().write() as w:
                w.append(self.id)
            i += 1


def main() -> None:
    data = Arc.new(RwLock.new([0]))  # tpyc: is_send(yes) is_sync(yes)
    h1 = spawn(Writer(data.clone(), 1, 50))
    h2 = spawn(Writer(data.clone(), 2, 50))
    h1.join()
    h2.join()
    with data.get().read() as r:
        total = 0
        n = 0
        for v in r.get():
            total += v
            n += 1
        print(n, total)              # 101 elements; 0 + 50*(1+2) = 150


main()
