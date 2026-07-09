# The guard releases the lock on every with-body exit, not just the normal one:
# __exit__ runs on early return and on a caught exception. If either path leaked
# the lock, the subsequent same-thread re-lock would deadlock (std::mutex is
# non-recursive) -- so a clean run that reaches the final print is the guard.
from tpy import Int32
from tpy.sync import Mutex


def append_then_return(m: Mutex[list[Int32]]) -> None:
    with m.lock() as g:
        g.append(1)
        return  # early return -- __exit__ must still release


def append_then_raise(m: Mutex[list[Int32]]) -> None:
    try:
        with m.lock() as g:
            g.append(2)
            raise ValueError("boom")  # exception path -- __exit__ must release
    except ValueError:
        pass


def main() -> None:
    m = Mutex.new([0])
    append_then_return(m)   # would deadlock the next lock if the lock leaked
    append_then_raise(m)
    with m.lock() as g:     # re-acquires only if both prior guards released
        n = 0
        total = 0
        for v in g.get():
            n += 1
            total += v
        print(n, total)     # 3 elements; 0 + 1 + 2 = 3


main()
