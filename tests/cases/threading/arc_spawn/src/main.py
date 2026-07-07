# Arc's atomic refcount under real thread contention -- a determinism/smoke
# check for the payoff of atomics (not a hard race-detector: a subtly non-atomic
# refcount could still print the right total on a lucky interleaving). Three
# worker threads each clone and drop a shared Arc in a tight loop while reading
# its payload; a racing refcount would tend to lose increments -> premature free
# -> crash / wrong total, so a clean deterministic run is strong evidence the
# cell is correct under contention. Arc[Counter] is Send + Sync (Counter's fields
# are), so the @nocopy task moves across the spawn boundary carrying its clone.
from tpy import Int32, Own, nocopy
from tplib.arc import Arc
from tpy.thread import spawn


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


@nocopy
class Hammer:
    shared: Arc[Counter]
    iters: Int32

    def __init__(self, shared: Own[Arc[Counter]], iters: Int32) -> None:
        self.shared = shared
        self.iters = iters

    def run(self) -> Int32:
        total = 0
        i = 0
        while i < self.iters:
            c = self.shared.clone()   # concurrent atomic strong increment
            total += c.get().n        # read the shared payload
            i += 1
            # `c` drops here -> concurrent atomic strong decrement
        return total


def main() -> None:
    a = Arc.new(Counter(7))  # tpyc: is_send(yes) is_sync(yes)
    h1 = spawn(Hammer(a.clone(), 1000))
    h2 = spawn(Hammer(a.clone(), 1000))
    h3 = spawn(Hammer(a.clone(), 1000))
    print(h1.join() + h2.join() + h3.join())  # 3 * 1000 * 7 = 21000


main()
