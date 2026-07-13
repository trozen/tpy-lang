# Condvar over a shared Mutex: two real threads strictly alternate via a turn
# flag, each blocking on cv.wait() until it is their turn. Proves the Condvar
# <-> MutexGuard interop (wait releases the guard's lock, blocks, reacquires)
# and that the handoff crosses the thread boundary through the shared state --
# the log is mutated by both threads under one lock and observed after join.
from tpy import Int32, Own, nocopy
from tplib.arc import Arc
from tpy.thread import spawn
from tpy.sync import Mutex, Condvar


class PingState:
    turn: Int32          # 0 = main's turn, 1 = worker's turn
    log: list[Int32]

    def __init__(self) -> None:
        self.turn = 0
        self.log = []


@nocopy
class Shared:
    m: Mutex[PingState]
    cv: Condvar

    def __init__(self, m: Own[Mutex[PingState]], cv: Own[Condvar]) -> None:
        self.m = m
        self.cv = cv


@nocopy
class Worker:
    shared: Arc[Shared]

    def __init__(self, shared: Own[Arc[Shared]]) -> None:
        self.shared = shared

    def run(self) -> None:
        i = 0
        while i < 5:
            s = self.shared.get()
            with s.m.lock() as g:
                while g.get().turn != 1:
                    s.cv.wait(g)
                g.get().log.append(2)
                g.get().turn = 0
                s.cv.notify_one()      # single waiter here; covers notify_one
            i += 1


def main() -> None:
    cv = Condvar()                     # tpyc: is_send(yes) is_sync(yes)
    shared = Arc.new(Shared(Mutex.new(PingState()), cv))
    h = spawn(Worker(shared.clone()))
    i = 0
    while i < 5:
        s = shared.get()
        with s.m.lock() as g:
            while g.get().turn != 0:
                s.cv.wait(g)
            g.get().log.append(1)
            g.get().turn = 1
            s.cv.notify_all()
        i += 1
    h.join()
    s2 = shared.get()
    with s2.m.lock() as gp:
        print(gp.get().log)


main()
