# Runtime backstop for the guard-escape gap: a lock guard enforces its lock at
# access time, so touching the payload outside the guard's `with` block aborts
# (a catchable RuntimeError) instead of racing the shared payload unsynchronized.
# Two misuse shapes per guard kind: bound-but-never-entered (never acquired), and
# used-after-the-block (the `with` as-var outlives the block, lock already
# released). The happy `with` path is unaffected -- the flag doesn't over-trigger.
# (A guard outliving a dropped bare Mutex/RwLock is a separate, region-model-gated
# hole, still tracked in BUGS.md.)
from tpy.sync import Mutex, RwLock


def main() -> None:
    m = Mutex.new([1, 2])

    g = m.lock()                 # never entered -> lock never acquired
    try:
        g.get()
    except RuntimeError:
        print("mutex unlocked-get rejected")
    try:
        g.append(3)              # via the Deref chain
    except RuntimeError:
        print("mutex unlocked-append rejected")

    with m.lock() as h:
        h.append(9)
    try:
        h.get()                  # after __exit__ -> released
    except RuntimeError:
        print("mutex post-exit rejected")

    rw = RwLock.new([0])
    r = rw.read()
    try:
        r.get()
    except RuntimeError:
        print("rwlock unlocked-read rejected")
    w = rw.write()
    try:
        w.append(1)
    except RuntimeError:
        print("rwlock unlocked-write rejected")

    with m.lock() as ok:         # happy path still works
        print(sorted(ok.get()))  # [1, 2, 9]


main()
