# A spawned task's fields are dropped (their __del__ runs) when run()
# completes -- not deferred to join() -- matching Rust's drop-at-thread-
# completion. The task holds a Notifier whose __del__ flips a shared flag and
# notifies a Condvar; main blocks on that Condvar BEFORE it joins, so it can
# only wake if the field dropped at thread completion. A regression that
# defers the drop to join() deadlocks here (main reaches join() only after the
# wait). no_cpython: this pins a TPy ownership guarantee CPython's GC-based
# finalization does not make.
from tpy import int32, Own, nocopy
from tplib.arc import Arc
from tpy.thread import spawn
from tpy.sync import Mutex, Condvar


class State:
    done: int32

    def __init__(self) -> None:
        self.done = 0


@nocopy
class Shared:
    m: Mutex[State]
    cv: Condvar

    def __init__(self, m: Own[Mutex[State]], cv: Own[Condvar]) -> None:
        self.m = m
        self.cv = cv


@nocopy
class Notifier:
    shared: Arc[Shared]

    def __init__(self, shared: Own[Arc[Shared]]) -> None:
        self.shared = shared

    def __del__(self) -> None:
        s = self.shared.get()
        with s.m.lock() as g:
            g.get().done = 1
        s.cv.notify_all()


@nocopy
class Task:
    notifier: Notifier

    def __init__(self, notifier: Own[Notifier]) -> None:
        self.notifier = notifier

    def run(self) -> None:
        pass


def main() -> None:
    cv = Condvar()
    shared = Arc.new(Shared(Mutex.new(State()), cv))
    h = spawn(Task(Notifier(shared.clone())))
    s = shared.get()
    with s.m.lock() as g:
        while g.get().done == 0:
            s.cv.wait(g)
        print(g.get().done)
    h.join()


main()
